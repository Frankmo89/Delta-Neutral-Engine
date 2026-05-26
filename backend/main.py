"""
main.py
=======
Punto de entrada principal del Funding Rate Arbitrage Bot.

Orquesta el ciclo completo:
  1. Escanear funding rates (FundingRateScanner).
  2. Tomar la mejor oportunidad (Top 1 por APR estimado).
  3. Validar viabilidad y ejecutar la posición Delta-Neutral (OrderManager).
    4. Repetir el ciclo con cadencia corta para que PositionMonitor decida
         el cierre cuando cambie la viabilidad.

Gestión de cierre: captura SIGINT / KeyboardInterrupt para apagar el bot
de forma limpia sin dejar órdenes colgadas.
"""

from __future__ import annotations

import asyncio
import signal
import sys

from loguru import logger

from config.settings import settings
from core.exchange import BybitExchange
from core.notifier import TelegramNotifier
from core.order_manager import OrderManager
from core.position_monitor import PositionMonitor
from data.scanner import FundingRateScanner
from data.websockets import FundingTickerCache

# ---------------------------------------------------------------------------
# Constantes operativas
# ---------------------------------------------------------------------------

SCAN_INTERVAL_SECONDS: int = 60         # Pausa entre escaneos sin posición abierta

# Capital máximo por operación en Testnet: prevalece sobre .env para evitar
# exponer capital real por error de configuración.
TESTNET_CAPITAL_CAP_USDT: float = 50.0


def get_capital_per_trade_usdt() -> float:
    """Capital efectivo por operación según entorno actual."""
    if settings.testnet:
        return min(settings.max_position_usdt, TESTNET_CAPITAL_CAP_USDT)
    return settings.max_position_usdt


def create_bot_components() -> tuple[
    BybitExchange,
    FundingRateScanner,
    OrderManager,
    PositionMonitor,
    FundingTickerCache,
]:
    """Crea e inyecta todos los componentes del motor de trading."""
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
    order_manager = OrderManager(exchange)
    monitor = PositionMonitor(
        exchange,
        order_manager,
        min_funding_threshold=0.001,
        ticker_cache=ticker_cache,
        ws_stale_after_seconds=settings.ws_ticker_stale_seconds,
    )
    return exchange, scanner, order_manager, monitor, ticker_cache


def _get_linear_symbols_for_ws(exchange: BybitExchange) -> set[str]:
    """Obtiene símbolos linear relevantes para suscribir ticker WS."""
    instruments = exchange.get_instruments_info(category="linear")
    return {
        str(inst.get("symbol", ""))
        for inst in instruments
        if str(inst.get("symbol", "")).endswith("USDT")
    }


# ---------------------------------------------------------------------------
# Bucle principal
# ---------------------------------------------------------------------------

async def trading_loop(
    scanner: FundingRateScanner,
    order_manager: OrderManager,
    monitor: PositionMonitor,
    stop_event: asyncio.Event,
) -> None:
    """
    Ciclo de trading:
      monitor → (si sin posición) scan → Top 1 → open_delta_neutral → pausar.

    Al inicio de cada ciclo revisa posiciones abiertas con PositionMonitor:
      - Si hay posición activa: evalúa su funding rate y la cierra si bajó del umbral.
      - Si no hay posición: escanea nuevas oportunidades y abre la mejor.

    Se detiene cuando `stop_event` es activado (Ctrl+C / SIGTERM).
    """
    logger.info("Bucle de trading iniciado.")

    while not stop_event.is_set():
        # ------------------------------------------------------------------
        # 1. Monitorear posiciones abiertas y cerrar si el funding cayó
        # ------------------------------------------------------------------
        try:
            await monitor.check_active_positions()
        except Exception as exc:
            logger.error(f"Error en check_active_positions: {exc}.")

        # ------------------------------------------------------------------
        # 2. Si ya hay una posición activa, no abrir otra
        # ------------------------------------------------------------------
        try:
            open_symbols = await monitor.get_open_symbols()
        except Exception as exc:
            logger.error(f"Error consultando posiciones abiertas: {exc}.")
            await _sleep_or_stop(SCAN_INTERVAL_SECONDS, stop_event)
            continue

        if open_symbols:
            logger.info(
                f"Posición(es) activa(s): {open_symbols}. "
                f"Capacidad restante={settings.max_open_positions - len(open_symbols)}. "
                f"Continuando monitoreo y esperando próximo ciclo en {SCAN_INTERVAL_SECONDS}s."
            )

        # ------------------------------------------------------------------
        # 3. Sin posición abierta: escanear nuevas oportunidades
        # ------------------------------------------------------------------
        try:
            logger.info("--- Iniciando escaneo de funding rates ---")
            df = scanner.scan(top_n=5)
        except Exception as exc:
            logger.error(f"Error durante el escaneo: {exc}. Reintentando en {SCAN_INTERVAL_SECONDS}s.")
            await _sleep_or_stop(SCAN_INTERVAL_SECONDS, stop_event)
            continue

        if df.empty:
            logger.warning(
                "Sin oportunidades que superen los filtros de volumen y rate. "
                f"Reintentando en {SCAN_INTERVAL_SECONDS}s."
            )
            await _sleep_or_stop(SCAN_INTERVAL_SECONDS, stop_event)
            continue

        # ------------------------------------------------------------------
        # 4. Tomar el Top 1
        # ------------------------------------------------------------------
        top = df.iloc[0]
        symbol: str         = str(top["symbol"])
        current_price: float = float(top["last_price"])
        funding_rate_pct: float = float(top["funding_rate_pct"])
        apr_est: float      = float(top["apr_est"])

        logger.info(
            f"Top oportunidad | symbol={symbol} | "
            f"funding_rate={funding_rate_pct:.4f}% | "
            f"APR est.={apr_est:.2f}% | "
            f"last_price={current_price:.4f} USDT"
        )

        if symbol in open_symbols:
            logger.info(
                f"Oportunidad omitida porque {symbol} ya está abierta. "
                f"Símbolos gestionados actuales: {open_symbols}"
            )
            await _sleep_or_stop(SCAN_INTERVAL_SECONDS, stop_event)
            continue

        if len(open_symbols) >= settings.max_open_positions:
            logger.info(
                "Oportunidad rechazada por límite de posiciones concurrentes | "
                f"symbol={symbol} | abiertas={len(open_symbols)} | "
                f"max_open_positions={settings.max_open_positions} | symbols={open_symbols}"
            )
            await _sleep_or_stop(SCAN_INTERVAL_SECONDS, stop_event)
            continue

        # ------------------------------------------------------------------
        # 5. Determinar capital a desplegar
        # ------------------------------------------------------------------
        if settings.testnet:
            capital = get_capital_per_trade_usdt()
            logger.debug(
                f"Testnet activo: capital limitado a {capital} USDT "
                f"(MAX_POSITION_USDT={settings.max_position_usdt})"
            )
        else:
            capital = settings.max_position_usdt

        # ------------------------------------------------------------------
        # 6. Intentar abrir posición Delta-Neutral
        # ------------------------------------------------------------------
        try:
            abierta: bool = await order_manager.open_delta_neutral(
                symbol=symbol,
                current_price=current_price,
                funding_rate_pct=funding_rate_pct,
                max_capital_usdt=capital,
            )
        except Exception as exc:
            logger.error(
                f"Excepción inesperada en open_delta_neutral para {symbol}: {exc}. "
                f"Saltando al próximo ciclo."
            )
            await _sleep_or_stop(SCAN_INTERVAL_SECONDS, stop_event)
            continue

        # ------------------------------------------------------------------
        # 7. Posición abierta: pausar hasta el próximo ciclo de funding
        # ------------------------------------------------------------------
        if abierta:
            logger.info(
                f"Posición Delta-Neutral abierta en {symbol}. "
                f"Monitoreando cada {SCAN_INTERVAL_SECONDS}s. "
                "PositionMonitor cerrará automáticamente cuando el funding caiga."
            )
            # Pausa breve para dejar avanzar el ciclo principal y re-evaluar
            # posiciones en la siguiente iteración del monitor.
            await asyncio.sleep(5)
            continue

        # ------------------------------------------------------------------
        # 8. Sin posición: esperar antes del próximo escaneo
        # ------------------------------------------------------------------
        logger.info(f"Sin posición abierta. Próximo escaneo en {SCAN_INTERVAL_SECONDS}s.")
        await _sleep_or_stop(SCAN_INTERVAL_SECONDS, stop_event)

    logger.info("Bucle de trading detenido.")


async def _sleep_or_stop(seconds: int, stop_event: asyncio.Event) -> None:
    """Duerme `seconds` segundos o retorna antes si `stop_event` se activa."""
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=seconds)
    except asyncio.TimeoutError:
        pass  # Timeout normal: continuar el bucle


async def run_trading_service(
    stop_event: asyncio.Event,
    scanner: FundingRateScanner,
    order_manager: OrderManager,
    monitor: PositionMonitor,
    ticker_cache: FundingTickerCache,
    exchange: BybitExchange,
) -> None:
    """Punto de entrada reusable para ejecutar el motor de trading."""
    notifier = TelegramNotifier()

    logger.info(
        f"=== Funding Rate Arbitrage Bot arrancando ==="
        f"\n  testnet          : {settings.testnet}"
        f"\n  max_position_usdt: {settings.max_position_usdt}"
        f"\n  leverage         : {settings.leverage}x"
    )

    if settings.testnet:
        logger.warning(
            "TESTNET activo — no se opera con fondos reales. "
            "Cambia BYBIT_TESTNET=false en .env para Mainnet."
        )

    if notifier.enabled:
        await notifier.send_message(
            "[INFO] Funding Bot iniciado. "
            f"testnet={settings.testnet} capital_max={settings.max_position_usdt}"
        )

    try:
        loop = asyncio.get_running_loop()
        symbols = await loop.run_in_executor(None, _get_linear_symbols_for_ws, exchange)
        await ticker_cache.start(symbols)
        logger.info(
            "Ticker WS iniciado en motor trading | "
            f"simbolos={ticker_cache.subscribed_count()}"
        )
    except Exception as exc:
        logger.warning(
            "No se pudo iniciar ticker WS en motor trading. "
            f"Se usará fallback REST. error={exc}"
        )

    try:
        await trading_loop(scanner, order_manager, monitor, stop_event)
    finally:
        await ticker_cache.stop()
        logger.info("Bot detenido. Sesión de Bybit cerrada correctamente.")


# ---------------------------------------------------------------------------
# Inicialización y gestión de señales
# ---------------------------------------------------------------------------

async def main() -> None:
    """Inicializa componentes, registra señales y arranca el bucle de trading."""

    stop_event = asyncio.Event()

    # Registrar manejadores de señales
    # loop.add_signal_handler no está disponible en Windows; usamos signal.signal
    # con call_soon_threadsafe para activar el evento desde cualquier hilo.
    loop = asyncio.get_running_loop()

    def _request_stop(sig_name: str) -> None:
        logger.warning(f"Señal {sig_name} recibida. Iniciando cierre graceful...")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _request_stop, sig.name)
        except (NotImplementedError, OSError):
            # Fallback para Windows (SIGTERM no está disponible en win32)
            try:
                signal.signal(
                    sig,
                    lambda s, f, sn=sig.name: loop.call_soon_threadsafe(
                        _request_stop, sn
                    ),
                )
            except (OSError, ValueError):
                pass  # SIGTERM no existe en Windows: ignorar silenciosamente

    # Inicializar componentes
    exchange, scanner, order_manager, monitor, ticker_cache = create_bot_components()

    try:
        await run_trading_service(
            stop_event,
            scanner,
            order_manager,
            monitor,
            ticker_cache,
            exchange,
        )
    except KeyboardInterrupt:
        logger.warning("KeyboardInterrupt capturado en main.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        # asyncio.run() puede propagar KeyboardInterrupt al salir: suprimir aquí
        sys.exit(0)
