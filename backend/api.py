"""
api.py
======
Servidor FastAPI read-only para exponer el estado del bot Delta-Neutral y
streaming de oportunidades para la UI React.

Arquitectura operativa desde P10:
    - `main.py` ejecuta el motor de trading en un proceso independiente.
    - `api.py` NO arranca el bot; solo lee estado desde SQLite y expone REST/WS.

Para operar el sistema completo:
    1. Ejecutar `python main.py` desde `backend/` en una terminal.
    2. Ejecutar `uvicorn api:app --reload` desde `backend/` en otra terminal.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect, status
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from config.settings import settings
from core.exchange import BybitExchange
from core.order_manager import OrderManager
from core.store import SQLiteStore
from data.scanner import FundingRateScanner
from data.websockets import FundingTickerCache
from main import SCAN_INTERVAL_SECONDS, get_capital_per_trade_usdt
from risk.position_sizer import PositionSizer


def _get_linear_symbols_for_ws(exchange: BybitExchange) -> set[str]:
    """Obtiene símbolos linear relevantes para suscribir ticker WS."""
    instruments = exchange.get_instruments_info(category="linear")
    return {
        str(inst.get("symbol", ""))
        for inst in instruments
        if str(inst.get("symbol", "")).endswith("USDT")
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Inicializa solo componentes read-only.
    La API no arranca el motor de trading; lee estado desde SQLite.
    """
    exchange = BybitExchange()
    ticker_cache = FundingTickerCache(
        testnet=settings.testnet,
        max_symbols_per_connection=settings.ws_ticker_max_symbols_per_connection,
        stale_after_seconds=settings.ws_ticker_stale_seconds,
    )
    scanner = FundingRateScanner(
        exchange,
        ticker_cache=ticker_cache,
        ws_stale_after_seconds=settings.ws_ticker_stale_seconds,
    )
    store = SQLiteStore(settings.db_path)
    order_manager = OrderManager(exchange)

    app.state.exchange = exchange
    app.state.scanner = scanner
    app.state.store = store
    app.state.ticker_cache = ticker_cache
    app.state.order_manager = order_manager
    app.state.scanner_snapshot = None  # Se llena en la tarea de fondo

    try:
        loop = asyncio.get_running_loop()
        symbols = await loop.run_in_executor(None, _get_linear_symbols_for_ws, exchange)
        await ticker_cache.start(symbols)
        logger.info(
            "Ticker WS iniciado en API read-only | "
            f"simbolos={ticker_cache.subscribed_count()}"
        )
    except Exception as exc:
        logger.warning(
            "No se pudo iniciar ticker WS en API. "
            f"Se usará fallback REST. error={exc}"
        )

    # Iniciar productor de snapshots del scanner en background.
    # FIX A: un único productor alimenta a todos los clientes WS.
    async def _scanner_snapshot_producer_task():
        """Productor de background que ejecuta scanner.scan() periódicamente."""
        while True:
            try:
                df = await loop.run_in_executor(
                    None, scanner.scan, settings.scanner_top_n
                )
                records = [] if df.empty else df.to_dict(orient="records")
                app.state.scanner_snapshot = {
                    "type": "scanner_snapshot",
                    "results": records,
                    "count": len(records),
                    "timestamp": datetime.now(tz=timezone.utc).isoformat(),
                }
                logger.debug(f"Scanner snapshot actualizado: {len(records)} resultados")
            except Exception as exc:
                logger.warning(f"Error en scanner snapshot producer: {exc}")
                app.state.scanner_snapshot = {
                    "type": "scanner_error",
                    "error": str(exc),
                    "timestamp": datetime.now(tz=timezone.utc).isoformat(),
                }
            
            await asyncio.sleep(settings.ws_scanner_push_seconds)

    producer_task = asyncio.create_task(_scanner_snapshot_producer_task())
    app.state.scanner_producer_task = producer_task

    logger.info(
        "API iniciada en modo solo lectura. Ejecuta main.py en un proceso separado "
        "para el motor de trading. Scanner snapshot producer iniciado."
    )

    try:
        yield
    finally:
        # Cancelar productor de scanner
        producer_task.cancel()
        try:
            await producer_task
        except asyncio.CancelledError:
            pass
        
        await app.state.ticker_cache.stop()
        logger.info("Shutdown FastAPI detectado. API read-only detenida.")


app = FastAPI(
    title="Funding Bot API",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS abierto para permitir la futura UI React sin bloqueos en desarrollo.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


async def _require_api_access_key(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> None:
    """Protección ligera opcional para REST. Si no hay clave configurada, no exige nada."""
    if not settings.api_access_key:
        return
    if x_api_key != settings.api_access_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
        )


@app.get("/api/status")
async def get_status(_: None = Depends(_require_api_access_key)) -> dict:
    """Métricas globales del servicio y configuración activa."""
    store: SQLiteStore = app.state.store
    open_symbols = sorted(store.get_open_symbols())

    return {
        "service": "funding-bot-api",
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "testnet": settings.testnet,
        "base_currency": settings.base_currency,
        "max_position_usdt": settings.max_position_usdt,
        "max_open_positions": settings.max_open_positions,
        "capital_per_trade_usdt": get_capital_per_trade_usdt(),
        "scan_interval_seconds": SCAN_INTERVAL_SECONDS,
        "process_mode": "read-only-api",
        "trading_task_running": False,
        "open_positions_count": len(open_symbols),
        "open_symbols": open_symbols,
    }


@app.get("/api/positions")
async def get_positions(_: None = Depends(_require_api_access_key)) -> list[dict]:
    """Lista de posiciones del bot leyendo símbolos abiertos desde SQLite."""
    store: SQLiteStore = app.state.store
    exchange: BybitExchange = app.state.exchange
    sizer = PositionSizer(max_position_usdt=settings.max_position_usdt)
    open_position_rows = store.get_open_positions()
    if not open_position_rows:
        return []

    open_symbols = {str(row["symbol"]) for row in open_position_rows}
    meta_by_symbol = {str(row["symbol"]): row for row in open_position_rows}
    loop = asyncio.get_running_loop()

    response = await loop.run_in_executor(
        None,
        exchange.get_positions,
        "linear",
        "USDT",
        None,
    )
    all_positions = response["result"]["list"]
    active = [
        p for p in all_positions
        if p.get("side") == "Sell"
        and float(p.get("size", "0") or 0) > 0
        and p.get("symbol") in open_symbols
    ]

    if not active:
        return jsonable_encoder([
            {
                "symbol": str(row["symbol"]),
                "side": "Sell",
                "qty": 0.0,
                "avg_entry_price": 0.0,
                "mark_price": 0.0,
                "unrealized_pnl": 0.0,
                "funding_rate": 0.0,
                "funding_rate_pct": 0.0,
                "breakeven_periods": None,
                "requires_manual_intervention": bool(row.get("requires_manual_intervention")),
                "intervention_reason": row.get("intervention_reason"),
                "open_timestamp": row.get("open_timestamp"),
                "status": row.get("status"),
            }
            for row in open_position_rows
        ])

    funding_tasks = [
        loop.run_in_executor(None, exchange.get_tickers, "linear", str(p["symbol"]))
        for p in active
    ]
    funding_results = await asyncio.gather(*funding_tasks, return_exceptions=True)

    payload: list[dict] = []
    for position, funding_response in zip(active, funding_results):
        symbol = str(position.get("symbol", ""))
        qty = float(position.get("size", "0") or 0)
        mark_price = float(position.get("markPrice", "0") or 0)
        avg_price = float(position.get("avgPrice", "0") or 0)
        reference_price = mark_price if mark_price > 0 else avg_price
        position_notional = qty * reference_price if reference_price > 0 else qty
        funding_rate = 0.0
        if not isinstance(funding_response, Exception):
            tickers = funding_response.get("result", {}).get("list", [])
            if tickers:
                funding_rate = float(tickers[0].get("fundingRate", 0) or 0)

        report = sizer.evaluate_existing_position(
            symbol=symbol,
            funding_rate=funding_rate,
            position_size_usdt=position_notional,
        )

        meta = meta_by_symbol.get(symbol, {})
        payload.append(
            {
                "symbol": symbol,
                "side": str(position.get("side", "")),
                "qty": qty,
                "avg_entry_price": avg_price,
                "mark_price": mark_price,
                "unrealized_pnl": float(position.get("unrealisedPnl", "0") or 0),
                "funding_rate": funding_rate,
                "funding_rate_pct": funding_rate * 100,
                "breakeven_periods": report.breakeven_periods,
                "requires_manual_intervention": bool(meta.get("requires_manual_intervention")),
                "intervention_reason": meta.get("intervention_reason"),
                "open_timestamp": meta.get("open_timestamp"),
                "status": meta.get("status", "open"),
            }
        )

    return jsonable_encoder(payload)


@app.get("/api/portfolio")
async def get_portfolio(_: None = Depends(_require_api_access_key)) -> dict:
    """Métricas agregadas de cartera: equity total y PnL acumulado histórico."""
    exchange: BybitExchange = app.state.exchange
    store: SQLiteStore = app.state.store
    loop = asyncio.get_running_loop()

    total_balance, lifetime_pnl = await asyncio.gather(
        loop.run_in_executor(
            None,
            lambda: exchange.get_wallet_balance("UNIFIED", total_usdt=True),
        ),
        loop.run_in_executor(None, store.get_lifetime_realized_pnl),
    )

    return {
        "total_balance": float(total_balance),
        "lifetime_pnl": float(lifetime_pnl),
        "currency": "USDT",
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
    }


@app.post("/api/positions/{symbol}/close")
async def close_position(symbol: str, _: None = Depends(_require_api_access_key)) -> dict:
    """Cierre manual de posición Delta-Neutral para un símbolo específico."""
    exchange: BybitExchange = app.state.exchange
    order_manager: OrderManager = app.state.order_manager
    loop = asyncio.get_running_loop()

    response = await loop.run_in_executor(
        None,
        exchange.get_positions,
        "linear",
        "USDT",
        symbol,
    )
    positions = response.get("result", {}).get("list", [])
    short_position = next(
        (
            p for p in positions
            if p.get("symbol") == symbol
            and p.get("side") == "Sell"
            and float(p.get("size", "0") or 0) > 0
        ),
        None,
    )
    if short_position is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No hay posición short activa para {symbol}",
        )

    qty = float(short_position.get("size", "0") or 0)
    if qty <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Qty inválida para cierre manual en {symbol}",
        )

    logger.warning(f"[{symbol}] Cierre manual solicitado por API | qty={qty}")
    success = await order_manager.close_delta_neutral(symbol, qty)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                f"Falló el cierre manual en {symbol}. "
                "Revisar logs del backend para diagnóstico."
            ),
        )

    return {
        "ok": True,
        "symbol": symbol,
        "action": "manual_close",
    }


@app.delete("/api/positions/{symbol}/force")
async def force_delete_position(symbol: str, _: None = Depends(_require_api_access_key)) -> dict:
    """
    Elimina la posición del registro SQLite sin enviar órdenes al exchange.
    El PositionMonitor liberará el candado en memoria al reconciliar con SQLite.
    """
    store: SQLiteStore = app.state.store
    deleted = store.delete_position(symbol)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No existe una posición abierta registrada para {symbol}",
        )

    logger.warning(
        f"[{symbol}] Posición eliminada por DELETE /api/positions/{{symbol}}/force. "
        "No se enviaron órdenes a Bybit."
    )
    return {
        "ok": True,
        "symbol": symbol,
        "action": "force_delete_position",
    }


@app.websocket("/ws/scanner")
async def ws_scanner(websocket: WebSocket) -> None:
    """
    Stream de oportunidades de scanner para tabla en vivo del frontend.
    Lee snapshots compartidos del productor sin ejecutar scans duplicados.
    
    FIX A: el snapshot lo genera un productor único en background.
    FIX B: desconexión limpia sin reenviar por conexión cerrada.
    """
    await websocket.accept()
    logger.info("Cliente conectado a /ws/scanner")

    try:
        while True:
            # Si el snapshot aún no existe (primer scan no terminó),
            # envía señal de warming_up.
            if app.state.scanner_snapshot is None:
                try:
                    await websocket.send_json({
                        "type": "warming_up",
                        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
                        "message": "Scanner iniciándose...",
                    })
                except Exception as exc:
                    logger.debug(f"Error enviando warming_up: {exc}. Cliente desconectado.")
                    break
            else:
                # Envía el snapshot actual
                try:
                    await websocket.send_json(
                        jsonable_encoder(app.state.scanner_snapshot)
                    )
                except Exception as exc:
                    logger.debug(f"Error enviando snapshot: {exc}. Cliente desconectado.")
                    break

            await asyncio.sleep(settings.ws_scanner_push_seconds)

    except WebSocketDisconnect:
        logger.info("Cliente desconectado de /ws/scanner (WebSocketDisconnect)")
    except Exception as exc:
        # FIX B: captura explícita de otros errores de conexión
        logger.debug(f"Error en /ws/scanner (conexión cerrada o error): {exc}")
