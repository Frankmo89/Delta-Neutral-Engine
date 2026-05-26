"""
core/position_monitor.py
========================
Vigila las posiciones Delta-Neutral abiertas y gestiona su ciclo de vida.

Responsabilidades:
  - Consultar posiciones lineares abiertas en Bybit V5.
  - Consultar el balance Spot de la moneda base para verificar ambas patas.
    - Evaluar el funding rate actual con la misma lógica de break-even que usa
        PositionSizer para decidir si la posición sigue siendo viable.
    - Delegar el cierre a OrderManager.close_delta_neutral() cuando el trade
        deja de ser rentable o el funding deja de compensar la fricción.
"""

from __future__ import annotations

import asyncio

from loguru import logger

from core.exchange import BybitExchange
from core.order_manager import OrderManager
from config.settings import settings
from data.websockets import FundingTickerCache
from risk.position_sizer import PositionSizer

class PositionMonitor:
    """
    Monitorea el ciclo de vida de las posiciones Delta-Neutral abiertas.

    Fuente de verdad para posiciones abiertas: el lado linear (perpetuos).
    El lado spot se verifica consultando el balance UNIFIED de la moneda base.
    """

    def __init__(
        self,
        exchange: BybitExchange,
        order_manager: OrderManager,
        min_funding_threshold: float = 0.0,
        ticker_cache: FundingTickerCache | None = None,
        ws_stale_after_seconds: float = 20.0,
    ) -> None:
        self._exchange = exchange
        self._order_manager = order_manager
        self.min_funding_threshold = min_funding_threshold
        self._sizer = PositionSizer(max_position_usdt=settings.max_position_usdt)
        self._open_symbols: set[str] = set()
        self._intervention_required: dict[str, str] = {}
        self._ticker_cache = ticker_cache
        self._ws_stale_after_seconds = ws_stale_after_seconds

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------

    async def check_active_positions(self) -> list[str]:
        """
        Escanea posiciones lineares abiertas, evalúa su funding rate
        actual y cierra las que ya no son rentables.

        Returns:
            Lista de símbolos cerrados durante esta revisión (puede estar vacía).
        """
        loop = asyncio.get_running_loop()
        all_positions = await loop.run_in_executor(None, self._get_linear_positions)
        managed_symbols = self._order_manager.store.get_open_symbols()
        self._release_stale_intervention_locks(managed_symbols)
        symbols_to_manage = managed_symbols | set(self._intervention_required.keys())
        unmanaged_positions = [
            p for p in all_positions
            if p.get("symbol") not in symbols_to_manage
        ]
        positions = [
            p for p in all_positions
            if p.get("symbol") in symbols_to_manage
        ]

        if unmanaged_positions:
            logger.info(
                "Posiciones lineares ignoradas (no gestionadas por el bot): "
                f"{[p.get('symbol') for p in unmanaged_positions]}"
            )

        if not positions:
            if symbols_to_manage:
                logger.warning(
                    "No hay posiciones activas para los símbolos gestionados por el bot. "
                    "Se mantienen bloqueos de intervención si existen."
                )
            else:
                logger.debug("No hay posiciones lineares abiertas del bot.")
            return []

        symbols = [p["symbol"] for p in positions]
        active_short_symbols = {
            p["symbol"]
            for p in positions
            if p.get("side") == "Sell" and float(p.get("size", "0")) > 0
        }
        self._open_symbols = active_short_symbols | set(self._intervention_required.keys())
        logger.info(f"Posiciones lineares abiertas detectadas: {symbols}")

        closed: list[str] = []

        for position in positions:
            symbol: str = position["symbol"]
            qty: float  = float(position["size"])
            side: str   = position["side"]
            mark_price = float(position.get("markPrice", "0") or 0)
            avg_price = float(position.get("avgPrice", "0") or 0)
            reference_price = mark_price if mark_price > 0 else avg_price
            position_notional = qty * reference_price if reference_price > 0 else qty

            # Nuestra estrategia siempre abre un short en futuros (side=Sell).
            # Posiciones Buy pueden ser de otras estrategias; las ignoramos.
            if side != "Sell" or qty == 0:
                logger.debug(f"[{symbol}] Ignorando posición side={side} qty={qty}.")
                continue
            # ── Verificaci\u00f3n de integridad del hedge ──────────────────────────────
            # Un short con la pata spot ausente o muy reducida es exposici\u00f3n
            # direccional desnuda. El funding saludable no implica hedge intacto;
            # cerramos la pata linear inmediatamente sin importar el funding.
            base_coin: str = symbol.removesuffix("USDT")
            try:
                spot_balance = await loop.run_in_executor(
                    None, self._get_spot_balance, base_coin
                )
            except Exception as exc:
                logger.error(
                    f"[{symbol}] No se pudo verificar integridad del hedge: {exc}. "
                    "Saltando verificaci\u00f3n de hedge para esta iteraci\u00f3n."
                )
                spot_balance = None

            if spot_balance is not None and spot_balance < qty * settings.hedge_integrity_tolerance:
                ratio = spot_balance / qty if qty > 0 else 0.0
                logger.critical(
                    f"[{symbol}] \u00a1HEDGE ROTO! short_qty={qty} | "
                    f"spot_balance={spot_balance:.6f} | ratio={ratio:.4f} | "
                    f"tolerancia={settings.hedge_integrity_tolerance}. "
                    "Exposici\u00f3n desnuda detectada. Cerrando pata linear inmediatamente."
                )
                await self._order_manager._send_alert(
                    f"[CR\u00cdTICO] Hedge roto detectado en *{symbol}*. "
                    f"spot_balance={spot_balance:.6f} / qty={qty} (ratio={ratio:.4f}). "
                    "Cerrando pata linear para eliminar exposici\u00f3n desnuda."
                )
                success = await self._order_manager.close_delta_neutral(symbol, qty)
                if success:
                    closed.append(symbol)
                    self._intervention_required.pop(symbol, None)
                    self._order_manager.store.clear_intervention(symbol)
                    self._open_symbols.discard(symbol)
                    logger.info(f"[{symbol}] Pata linear cerrada tras detectar hedge roto.")
                else:
                    reason = "hedge roto: close_delta_neutral fall\u00f3 tras detectar spot insuficiente"
                    self._intervention_required[symbol] = reason
                    self._order_manager.store.mark_intervention_required(symbol, reason)
                    self._open_symbols.add(symbol)
                    logger.critical(
                        f"[{symbol}] Cierre de emergencia fallido tras hedge roto. "
                        "Marcada para intervenci\u00f3n manual y s\u00edmbolo bloqueado."
                    )
                continue  # No evaluar funding para esta posici\u00f3n
            # Obtener funding rate actual para este símbolo
            try:
                funding_rate = await loop.run_in_executor(
                    None, self._get_current_funding_rate, symbol
                )
            except Exception as exc:
                logger.error(f"[{symbol}] Error obteniendo funding rate: {exc}. Saltando.")
                continue

            report = self._sizer.evaluate_existing_position(
                symbol=symbol,
                funding_rate=funding_rate,
                position_size_usdt=position_notional,
            )

            logger.info(
                f"[{symbol}] side=Sell | qty={qty} | "
                f"funding_rate={funding_rate * 100:.4f}% | "
                f"breakeven={report.breakeven_periods:.2f} periodos"
            )

            # Decisión de cierre: la posición deja de ser viable bajo la misma
            # lógica de break-even usada para la apertura.
            if not report.is_viable:
                logger.warning(
                    f"[{symbol}] Posición ya no viable | motivo='{report.rejection_reason}'. "
                    "Cerrando posición Delta-Neutral..."
                )
                success = await self._order_manager.close_delta_neutral(symbol, qty)
                if success:
                    closed.append(symbol)
                    self._intervention_required.pop(symbol, None)
                    self._order_manager.store.clear_intervention(symbol)
                    self._open_symbols.discard(symbol)
                    logger.info(f"[{symbol}] Posición cerrada y ganancias aseguradas.")
                else:
                    reason = "close_delta_neutral devolvió False tras reintentos"
                    self._intervention_required[symbol] = reason
                    self._order_manager.store.mark_intervention_required(symbol, reason)
                    self._open_symbols.add(symbol)
                    logger.critical(
                        f"[{symbol}] Cierre fallido tras reintentos. "
                        "Marcada para intervención manual y símbolo bloqueado."
                    )
            else:
                logger.info(
                    f"[{symbol}] Funding saludable. Posición mantenida."
                )

        return closed

    async def get_open_symbols(self) -> list[str]:
        """
        Devuelve la lista de símbolos con posición linear corta activa.
        Usado por main.py para evitar abrir posiciones duplicadas.

        Returns:
            Lista de símbolos con posición Sell abierta en linear.
        """
        loop = asyncio.get_running_loop()
        positions = await loop.run_in_executor(None, self._get_linear_positions)
        managed_symbols = self._order_manager.store.get_open_symbols()
        self._release_stale_intervention_locks(managed_symbols)
        active_symbols = {
            p["symbol"]
            for p in positions
            if p.get("side") == "Sell" and float(p.get("size", "0")) > 0
            and p.get("symbol") in managed_symbols
        }
        self._open_symbols = active_symbols | set(self._intervention_required.keys())
        return sorted(self._open_symbols)

    async def get_open_positions(self) -> list[dict]:
        """
        Devuelve posiciones Delta-Neutral activas con metadatos útiles para API/UI.

        Returns:
            Lista de dicts por símbolo con qty, precios y funding actual.
        """
        loop = asyncio.get_running_loop()
        positions = await loop.run_in_executor(None, self._get_linear_positions)
        managed_symbols = self._order_manager.store.get_open_symbols()
        self._release_stale_intervention_locks(managed_symbols)
        active = [
            p for p in positions
            if p.get("side") == "Sell" and float(p.get("size", "0")) > 0
            and p.get("symbol") in managed_symbols
        ]

        if not active:
            return []

        funding_tasks = [
            loop.run_in_executor(None, self._get_current_funding_rate, p["symbol"])
            for p in active
        ]
        funding_results = await asyncio.gather(*funding_tasks, return_exceptions=True)

        payload: list[dict] = []
        for position, funding in zip(active, funding_results):
            symbol = str(position.get("symbol", ""))
            qty = float(position.get("size", "0"))
            mark_price = float(position.get("markPrice", "0") or 0)
            avg_price = float(position.get("avgPrice", "0") or 0)
            reference_price = mark_price if mark_price > 0 else avg_price
            position_notional = qty * reference_price if reference_price > 0 else qty
            funding_rate = 0.0 if isinstance(funding, Exception) else float(funding)
            report = self._sizer.evaluate_existing_position(
                symbol=symbol,
                funding_rate=funding_rate,
                position_size_usdt=position_notional,
            )

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
                    "position_notional_usdt": position_notional,
                    "breakeven_periods": report.breakeven_periods,
                    "is_viable": report.is_viable,
                    "viability_reason": report.rejection_reason,
                    "requires_manual_intervention": symbol in self._intervention_required,
                    "intervention_reason": self._intervention_required.get(symbol),
                }
            )

        return payload

    def _release_stale_intervention_locks(self, managed_symbols: set[str]) -> None:
        stale_symbols = [
            symbol
            for symbol in self._intervention_required
            if symbol not in managed_symbols
        ]
        for symbol in stale_symbols:
            self._intervention_required.pop(symbol, None)
            self._open_symbols.discard(symbol)
            logger.info(
                f"[{symbol}] Candado de intervención liberado: el símbolo ya no existe en SQLite."
            )

    # ------------------------------------------------------------------
    # Métodos de consulta internos (síncronos — se ejecutan en executor)
    # ------------------------------------------------------------------

    def _get_linear_positions(self) -> list[dict]:
        """
        Devuelve todas las posiciones USDT-perpetuo con size > 0.
        Filtra entradas vacías para evitar procesamiento innecesario.
        """
        response = self._exchange.get_positions(
            category="linear", settle_coin="USDT"
        )
        return [
            p for p in response["result"]["list"]
            if float(p.get("size", "0")) > 0
        ]

    def _get_current_funding_rate(self, symbol: str) -> float:
        """
        Consulta funding rate usando WS cache como fuente primaria.
        Si el dato no existe o está stale, usa fallback REST.

        Args:
            symbol: Par en formato Bybit, e.g. "ZKUSDT".

        Returns:
            Funding rate como decimal (e.g. 0.0003 para 0.03%).

        Raises:
            ValueError: Si no se encuentra ticker para el símbolo.
        """
        if self._ticker_cache is not None:
            ticker = self._ticker_cache.get_ticker(
                symbol=symbol,
                max_age_seconds=self._ws_stale_after_seconds,
            )
            if ticker is not None:
                return float(ticker.get("fundingRate", 0) or 0)

            logger.debug(f"[{symbol}] Funding WS stale/vacío. Fallback REST.")

        response = self._exchange.get_tickers(category="linear", symbol=symbol)
        tickers: list = response["result"]["list"]
        if not tickers:
            raise ValueError(f"No se encontró ticker para {symbol}")
        return float(tickers[0].get("fundingRate", 0))

    def _get_spot_balance(self, base_coin: str) -> float:
        """
        Consulta el balance disponible de una moneda base en la wallet UNIFIED.
        Útil para verificar que la pata spot está presente antes de cerrar.

        Args:
            base_coin: Símbolo de la moneda base, e.g. "ZK" para ZKUSDT.

        Returns:
            Balance disponible en moneda base. 0.0 si no se encuentra.
        """
        response = self._exchange.get_wallet_balance(account_type="UNIFIED")
        accounts: list = response["list"]
        if not accounts:
            return 0.0

        for coin_data in accounts[0].get("coin", []):
            if coin_data.get("coin") == base_coin:
                return float(coin_data.get("walletBalance", "0"))
        return 0.0
