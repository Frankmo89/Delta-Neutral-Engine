from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal

from loguru import logger

from config.settings import settings
from core.notifier import TelegramNotifier
from core.store import SQLiteStore
from risk.position_sizer import PositionSizer


def _apply_qty_step(qty: float, step: float) -> float:
    """
    Redondea qty hacia abajo al múltiplo de step más cercano.
    Usa Decimal para evitar errores de coma flotante en el truncado.
    """
    d_qty = Decimal(str(qty))
    d_step = Decimal(str(step))
    return float((d_qty // d_step) * d_step)


class OrderManager:
    def __init__(self, exchange_client):
        # Recibe una instancia de BybitExchange ya autenticada
        self.exchange = exchange_client
        self.store = SQLiteStore(settings.db_path)
        self.notifier = TelegramNotifier()

    def get_managed_symbols(self) -> set[str]:
        """Devuelve una copia de los símbolos gestionados por este bot."""
        return self.store.get_open_symbols()

    def _record_trade(
        self,
        *,
        symbol: str,
        side: str,
        qty: float,
        category: str,
        result: dict,
    ) -> None:
        """Persiste una orden ejecutada en el historial de trades."""
        self.store.insert_trade(
            order_link_id=result.get("orderLinkId"),
            order_id=result.get("orderId"),
            symbol=symbol,
            side=side,
            category=category,
            qty=qty,
            price=None,
        )

    async def _send_alert(self, text: str) -> None:
        """Envía alerta push sin romper el flujo si falla el canal."""
        try:
            await self.notifier.send_message(text)
        except Exception as exc:
            logger.warning(f"Falló envío de alerta push: {exc}")

    @staticmethod
    def _is_transient_ret_code(ret_code: int) -> bool:
        return ret_code in {10000, 10006, 10016}

    @staticmethod
    def _is_transient_exception(exc: Exception) -> bool:
        message = str(exc).lower()
        transient_markers = (
            "timeout",
            "timed out",
            "temporarily unavailable",
            "connection",
            "network",
            "429",
            "502",
            "503",
            "504",
        )
        return any(marker in message for marker in transient_markers)

    async def _colocar_orden(
        self,
        symbol: str,
        side: str,
        qty: float,
        category: str,
        reduce_only: bool = False,
    ) -> dict:
        """
        Coloca una orden de mercado en Bybit V5 vía pybit.

        Args:
            symbol:   Par en formato Bybit, e.g. "BTCUSDT".
            side:     "Buy" o "Sell".
            qty:      Cantidad de tokens (base coin).
            category: "spot" o "linear" (Futuros Perpetuos USDT).

        Returns:
            Dict con 'status', 'category', y 'orderId' (éxito) o
            'retCode' + 'msg' (error).
        """
        qty_str = str(qty)
        order_link_id = (
            f"{settings.bot_order_prefix}-{symbol}-{uuid.uuid4().hex[:8]}"
        )

        params: dict = {
            "category": category,
            "symbol": symbol,
            "side": side,
            "orderType": "Market",
            "qty": qty_str,
            "orderLinkId": order_link_id,
        }

        # Bybit V5 — Spot Market Buy interpreta qty en USDT por defecto.
        # marketUnit="baseCoin" fuerza qty en tokens, garantizando la
        # simetría exacta de notional con la pata de futuros (Legging Risk).
        if category == "spot" and side == "Buy":
            params["marketUnit"] = "baseCoin"
        if category == "linear" and reduce_only:
            params["reduceOnly"] = True

        logger.debug(
            f"[{category.upper()}] Enviando orden | side={side} qty={qty_str} "
            f"symbol={symbol} reduceOnly={reduce_only} params={params}"
        )

        # place_order muta estado: no reintentar a ciegas.
        # Antes de cada reenvío verificamos idempotencia con orderLinkId.
        max_attempts = settings.max_network_retries + 1
        loop = asyncio.get_running_loop()

        for attempt in range(1, max_attempts + 1):
            try:
                response: dict = await loop.run_in_executor(
                    None,
                    lambda: self.exchange.session.place_order(**params),
                )

                ret_code: int = int(response.get("retCode", -1))

                if ret_code == 0:
                    order_id: str = response["result"]["orderId"]
                    logger.info(
                        f"[{category.upper()}] Orden {side} ejecutada | "
                        f"symbol={symbol} | qty={qty_str} | orderId={order_id} | "
                        f"orderLinkId={order_link_id}"
                    )
                    return {
                        "status": "success",
                        "category": category,
                        "orderId": order_id,
                        "orderLinkId": order_link_id,
                    }

                ret_msg: str = response.get("retMsg", "Error desconocido")

                if not self._is_transient_ret_code(ret_code) or attempt >= max_attempts:
                    logger.error(
                        f"[{category.upper()}] Orden {side} rechazada | "
                        f"retCode={ret_code} | msg={ret_msg} | orderLinkId={order_link_id}"
                    )
                    return {
                        "status": "error",
                        "category": category,
                        "retCode": ret_code,
                        "msg": ret_msg,
                        "orderLinkId": order_link_id,
                    }

                logger.warning(
                    f"[{category.upper()}] RetCode transitorio={ret_code} en place_order | "
                    f"attempt={attempt}/{max_attempts - 1} | verificando orderLinkId={order_link_id}"
                )
                existing_order = await loop.run_in_executor(
                    None,
                    self.exchange.find_order_by_link_id,
                    category,
                    symbol,
                    order_link_id,
                )
                if existing_order:
                    recovered_order_id = str(existing_order.get("orderId", "UNKNOWN"))
                    logger.warning(
                        f"[{category.upper()}] Orden recuperada por idempotencia | "
                        f"symbol={symbol} | orderId={recovered_order_id} | "
                        f"orderLinkId={order_link_id}"
                    )
                    return {
                        "status": "success",
                        "category": category,
                        "orderId": recovered_order_id,
                        "orderLinkId": order_link_id,
                    }

                await asyncio.sleep(settings.backoff_factor ** (attempt - 1))

            except Exception as exc:
                if not self._is_transient_exception(exc) or attempt >= max_attempts:
                    logger.error(
                        f"[{category.upper()}] Excepción colocando orden {side} {symbol}: {exc} "
                        f"| orderLinkId={order_link_id}"
                    )
                    return {
                        "status": "error",
                        "category": category,
                        "retCode": -1,
                        "msg": str(exc),
                        "orderLinkId": order_link_id,
                    }

                logger.warning(
                    f"[{category.upper()}] Error transitorio en place_order: {exc} | "
                    f"attempt={attempt}/{max_attempts - 1} | verificando orderLinkId={order_link_id}"
                )
                existing_order = await loop.run_in_executor(
                    None,
                    self.exchange.find_order_by_link_id,
                    category,
                    symbol,
                    order_link_id,
                )
                if existing_order:
                    recovered_order_id = str(existing_order.get("orderId", "UNKNOWN"))
                    logger.warning(
                        f"[{category.upper()}] Orden recuperada por idempotencia tras excepción | "
                        f"symbol={symbol} | orderId={recovered_order_id} | "
                        f"orderLinkId={order_link_id}"
                    )
                    return {
                        "status": "success",
                        "category": category,
                        "orderId": recovered_order_id,
                        "orderLinkId": order_link_id,
                    }

                await asyncio.sleep(settings.backoff_factor ** (attempt - 1))

        return {
            "status": "error",
            "category": category,
            "retCode": -1,
            "msg": "place_order agotó reintentos de red con verificación idempotente",
            "orderLinkId": order_link_id,
        }

    def _get_spot_balance(self, base_coin: str) -> float:
        """Lee el walletBalance de la moneda base en la cuenta UNIFIED."""
        resp = self.exchange.get_wallet_balance(account_type="UNIFIED")
        accounts: list = resp["list"]
        if not accounts:
            return 0.0
        for coin_data in accounts[0].get("coin", []):
            if coin_data.get("coin") == base_coin:
                return float(coin_data.get("walletBalance", "0"))
        return 0.0

    def _get_linear_short_size(self, symbol: str) -> float:
        """Devuelve el tamaño short activo para un símbolo en linear."""
        resp = self.exchange.get_positions(category="linear", settle_coin="USDT")
        for position in resp["result"].get("list", []):
            if position.get("symbol") != symbol:
                continue
            if position.get("side") != "Sell":
                continue
            size = float(position.get("size", "0") or 0)
            if size > 0:
                return size
        return 0.0

    def _get_linear_short_unrealized_pnl(self, symbol: str) -> float:
        """Obtiene el PnL no realizado actual del short linear, usado como snapshot de cierre."""
        resp = self.exchange.get_positions(category="linear", settle_coin="USDT")
        for position in resp["result"].get("list", []):
            if position.get("symbol") != symbol:
                continue
            if position.get("side") != "Sell":
                continue
            size = float(position.get("size", "0") or 0)
            if size <= 0:
                continue
            return float(position.get("unrealisedPnl", "0") or 0)
        return 0.0

    async def _rollback(self, symbol: str, qty: float, res_spot: dict, res_perp: dict) -> None:
        """
        Protocolo de emergencia ante descalce: cierra la pata que tuvo éxito
        para devolver el portafolio a Delta ≈ 0.

        Lógica:
          - Spot OK  + Perp FAIL → SELL en Spot  (deshace la compra del activo real).
          - Spot FAIL + Perp OK  → BUY  en Linear (deshace el short de futuros).
        """
        logger.critical(
            f"[ROLLBACK] Iniciando cierre de emergencia | symbol={symbol} qty={qty}"
        )
        await self._send_alert(
            "[CRITICO] Se activó _rollback por descalce en "
            f"*{symbol}*. qty={qty}. Protección asimétrica en ejecución."
        )
        loop = asyncio.get_running_loop()

        if res_spot["status"] == "success" and res_perp["status"] != "success":
            logger.critical(
                f"[ROLLBACK] Spot ejecutado, Perp falló "
                f"(retCode={res_perp.get('retCode')} msg={res_perp.get('msg')}). "
                f"Cerrando pata Spot con SELL Market."
            )
            base_coin: str = symbol.removesuffix("USDT")
            spot_balance = await loop.run_in_executor(
                None, self._get_spot_balance, base_coin
            )
            spot_step = await loop.run_in_executor(
                None, self.exchange.get_qty_step, symbol, "spot"
            )
            qty_spot = _apply_qty_step(spot_balance, spot_step)
            if not qty_spot:
                logger.critical(
                    f"[ROLLBACK] Balance real Spot insuficiente para cerrar {symbol}. "
                    f"base_coin={base_coin} spot_balance={spot_balance} step={spot_step}"
                )
                return
            resultado = await self._colocar_orden(symbol, "Sell", qty_spot, "spot")

        elif res_perp["status"] == "success" and res_spot["status"] != "success":
            logger.critical(
                f"[ROLLBACK] Perp ejecutado, Spot falló "
                f"(retCode={res_spot.get('retCode')} msg={res_spot.get('msg')}). "
                f"Cerrando pata Perp con BUY Market."
            )
            resultado = await self._colocar_orden(
                symbol, "Buy", qty, "linear", reduce_only=True
            )

        else:
            # Ambas fallaron: no hay posición abierta, nada que cerrar.
            logger.critical("[ROLLBACK] Ambas patas fallaron. No hay posición que cerrar.")
            return

        if resultado["status"] == "success":
            logger.critical(
                f"[ROLLBACK] Completado con éxito | orderId={resultado['orderId']}. "
                f"Portafolio restaurado a Delta ≈ 0."
            )
        else:
            logger.critical(
                f"[ROLLBACK] FALLO CRÍTICO: no se pudo cerrar la posición abierta. "
                f"Intervención manual requerida inmediatamente. "
                f"retCode={resultado.get('retCode')} | msg={resultado.get('msg')}"
            )

    async def open_delta_neutral(
        self,
        symbol: str,
        current_price: float,
        funding_rate_pct: float,
        max_capital_usdt: float,
    ) -> bool:
        """
        Valida la viabilidad del trade con PositionSizer y, si es viable,
        abre la posición Delta-Neutral de forma simultánea.

        Args:
            symbol:           Par en formato Bybit, e.g. "BTCUSDT".
            current_price:    Precio de mercado actual del activo (USDT).
            funding_rate_pct: Funding rate en porcentaje (e.g. 0.03 → 0.03%).
            max_capital_usdt: Capital máximo disponible a desplegar (USDT).

        Returns:
            True si la posición quedó abierta con cobertura perfecta, False en cualquier
            otro caso (rechazo de riesgo, fallo de orden, rollback ejecutado).
        """
        logger.info(f"=== INICIANDO APERTURA DELTA-NEUTRAL: {symbol} ===")

        # 1. Validar viabilidad con PositionSizer
        # evaluate() espera funding_rate como decimal: dividimos el pct entre 100.
        sizer = PositionSizer(max_position_usdt=max_capital_usdt)
        reporte = sizer.evaluate(
            symbol=symbol,
            funding_rate=funding_rate_pct / 100,
            available_balance_usdt=max_capital_usdt,
        )

        logger.info(str(reporte))

        if not reporte.is_viable:
            logger.warning(
                f"Operación rechazada por gestión de riesgo | "
                f"symbol={symbol} | motivo='{reporte.rejection_reason}'"
            )
            return False

        # 2. Calcular qty en tokens a partir del tamaño aprobado en USDT
        raw_qty = sizer.calculate_qty(reporte.position_size_usdt, current_price)
        if not raw_qty:
            logger.error(
                f"qty calculado es 0 para {symbol} "
                f"(position_size_usdt={reporte.position_size_usdt}, price={current_price}). "
                "Operación abortada."
            )
            return False

        # 3. Obtener el qtyStep de cada mercado y aplicar el más restrictivo.
        # pybit es síncrono: ejecutamos las dos consultas en paralelo para no
        # bloquear el event loop y minimizar la latencia antes del gather.
        loop = asyncio.get_running_loop()
        try:
            spot_step, linear_step = await asyncio.gather(
                loop.run_in_executor(None, self.exchange.get_qty_step, symbol, "spot"),
                loop.run_in_executor(None, self.exchange.get_qty_step, symbol, "linear"),
            )
        except ValueError as exc:
            logger.error(f"No se pudo obtener qty_step para {symbol}: {exc}. Operación abortada.")
            return False

        # Usamos el step más grande (más restrictivo) para que el mismo qty
        # sea válido en ambos mercados simultáneamente.
        restrictive_step = max(spot_step, linear_step)
        qty = _apply_qty_step(raw_qty, restrictive_step)
        logger.debug(
            f"qty_step | spot={spot_step} linear={linear_step} "
            f"restrictive={restrictive_step} | raw={raw_qty} → final={qty}"
        )

        if not qty:
            logger.error(
                f"qty=0 tras aplicar step={restrictive_step} a raw_qty={raw_qty} "
                f"para {symbol}. Capital insuficiente para el tamaño mínimo de lote."
            )
            return False

        spot_max_qty = self.exchange.get_max_order_qty(symbol, "spot")
        linear_max_qty = self.exchange.get_max_order_qty(symbol, "linear")
        restrictive_max_qty = min(spot_max_qty, linear_max_qty)
        if qty > restrictive_max_qty:
            adjusted_qty = _apply_qty_step(restrictive_max_qty, restrictive_step)
            if not adjusted_qty:
                logger.error(
                    f"qty=0 tras recortar por maxOrderQty={restrictive_max_qty} "
                    f"para {symbol}. Operación abortada."
                )
                return False

            logger.info(
                f"Tamaño recortado de {qty} a {adjusted_qty} por límite de exchange | "
                f"symbol={symbol} | spot_maxOrderQty={spot_max_qty} | "
                f"linear_maxOrderQty={linear_max_qty}"
            )
            qty = adjusted_qty

        capital_invested_usdt = qty * current_price

        logger.info(
            f"Riesgo aprobado | capital={reporte.position_size_usdt:.2f} USDT | "
            f"qty={qty} tokens | capital_ajustado={capital_invested_usdt:.2f} USDT | "
            f"breakeven={reporte.breakeven_periods:.2f} periodos"
        )

        # 4. Configurar leverage en el contrato linear ANTES de enviar órdenes.
        #    retCode 110043 = "leverage not modified" → ya estaba correcto, tratar como éxito.
        #    Cualquier otro error no aborta: el exchange ya tiene algún leverage y operar
        #    con el leverage actual es mejor que no operar y dejar el scanner sin acción.
        leverage_str = str(settings.leverage)
        try:
            lev_response: dict = await loop.run_in_executor(
                None,
                lambda: self.exchange.session.set_leverage(
                    category="linear",
                    symbol=symbol,
                    buyLeverage=leverage_str,
                    sellLeverage=leverage_str,
                ),
            )
            lev_ret_code = int(lev_response.get("retCode", -1))
            if lev_ret_code == 0:
                logger.info(
                    f"Leverage configurado | symbol={symbol} leverage={leverage_str}x"
                )
            elif lev_ret_code == 110043:
                # "leverage not modified" — ya estaba en el valor correcto.
                logger.debug(
                    f"Leverage ya en {leverage_str}x | symbol={symbol} (retCode 110043 — sin cambio)"
                )
            else:
                logger.warning(
                    f"set_leverage retCode inesperado={lev_ret_code} "
                    f"msg={lev_response.get('retMsg', 'N/A')} | symbol={symbol}. "
                    f"Continuando con leverage actual del exchange."
                )
        except Exception as exc:
            logger.warning(
                f"Error al llamar set_leverage para {symbol}: {exc}. "
                "Continuando con leverage actual del exchange."
            )

        # 5. Snapshot del balance spot ANTES del gather para verificar fill real.
        #    retCode=0 en Bybit significa "orden ACEPTADA", no "orden EJECUTADA".
        #    En Spot Market con liquidez nula la orden puede aceptarse sin llenarse nunca.
        base_coin: str = symbol.removesuffix("USDT")
        try:
            spot_balance_before = await loop.run_in_executor(
                None, self._get_spot_balance, base_coin
            )
        except Exception as exc:
            logger.warning(
                f"[{symbol}] No se pudo leer spot_balance_before: {exc}. "
                "Se usará 0.0 como referencia de fill."
            )
            spot_balance_before = 0.0

        # 6. Preparar las tareas asíncronas
        tarea_spot = self._colocar_orden(symbol, "Buy",  qty, "spot")
        tarea_perp = self._colocar_orden(symbol, "Sell", qty, "linear")

        # 7. Disparar ambas órdenes AL MISMO TIEMPO
        logger.info("Disparando órdenes concurrentes...")
        res_spot, res_perp = await asyncio.gather(tarea_spot, tarea_perp)

        # 8. Verificar fill real de cada pata.
        #    retCode=0 sólo garantiza que Bybit ACEPTÓ la orden, no que se llenó.
        #    Hacemos polling de balance/posición para confirmar ejecución real antes
        #    de declarar cobertura delta-neutral y persistir en SQLite.
        spot_filled = False
        perp_filled = False

        if res_spot["status"] == "success":
            spot_balance_after = spot_balance_before
            for attempt in range(1, settings.max_fill_check_attempts + 1):
                await asyncio.sleep(settings.fill_check_delay_seconds)
                try:
                    spot_balance_after = await loop.run_in_executor(
                        None, self._get_spot_balance, base_coin
                    )
                except Exception as exc:
                    logger.warning(
                        f"[{symbol}] Poll fill spot #{attempt}: error leyendo balance: {exc}"
                    )
                    continue
                delta = spot_balance_after - spot_balance_before
                min_delta = qty * settings.spot_fill_tolerance
                logger.debug(
                    f"[{symbol}] Poll fill spot "
                    f"#{attempt}/{settings.max_fill_check_attempts}: "
                    f"delta={delta:.6f} / mínimo={min_delta:.6f}"
                )
                if delta >= min_delta:
                    logger.debug(
                        f"[{symbol}] Fill spot confirmado en poll #{attempt} | "
                        f"delta={delta:.6f} ≥ mínimo={min_delta:.6f}"
                    )
                    spot_filled = True
                    break

            if not spot_filled:
                delta_final = spot_balance_after - spot_balance_before
                min_delta = qty * settings.spot_fill_tolerance
                logger.critical(
                    f"[{symbol}] Spot ACEPTADA pero NO llenó tras "
                    f"{settings.max_fill_check_attempts} polls | "
                    f"delta={delta_final:.6f} < mínimo={min_delta:.6f}. "
                    "Se trata como fallo de ejecución real."
                )
                res_spot = {
                    "status": "error",
                    "category": "spot",
                    "retCode": -2,
                    "msg": (
                        f"spot aceptada pero no llenó: "
                        f"delta={delta_final:.6f} < mínimo={min_delta:.6f}"
                    ),
                    "orderLinkId": res_spot.get("orderLinkId", ""),
                }

        if res_perp["status"] == "success":
            try:
                short_size = await loop.run_in_executor(
                    None, self._get_linear_short_size, symbol
                )
                if short_size > 0:
                    perp_filled = True
                    logger.debug(
                        f"[{symbol}] Fill linear confirmado | short_size={short_size}"
                    )
                else:
                    logger.critical(
                        f"[{symbol}] Perp ACEPTADA pero short_size=0 — "
                        "orden no se ejecutó."
                    )
                    res_perp = {
                        "status": "error",
                        "category": "linear",
                        "retCode": -2,
                        "msg": "perp aceptada pero short_size=0 tras verificación",
                        "orderLinkId": res_perp.get("orderLinkId", ""),
                    }
            except Exception as exc:
                logger.warning(
                    f"[{symbol}] No se pudo verificar fill perp: {exc}. "
                    "Se asume fill OK para no abortar innecesariamente."
                )
                perp_filled = True

        # 9. Solo si AMBAS patas verificadas con fill real → posición delta-neutral confirmada.
        #    No persistir en SQLite hasta tener certeza de cobertura efectiva.
        if spot_filled and perp_filled:
            self._record_trade(
                symbol=symbol,
                side="Buy",
                qty=qty,
                category="spot",
                result=res_spot,
            )
            self._record_trade(
                symbol=symbol,
                side="Sell",
                qty=qty,
                category="linear",
                result=res_perp,
            )
            self.store.upsert_open_position(
                symbol=symbol,
                spot_order_link_id=res_spot.get("orderLinkId"),
                perp_order_link_id=res_perp.get("orderLinkId"),
            )
            logger.success(
                f"Cobertura perfecta! Delta-Neutral en {symbol} | "
                f"spot_orderId={res_spot['orderId']} | perp_orderId={res_perp['orderId']}"
            )
            return True

        logger.critical(
            f"ALERTA DE DESCALCE! Una pata no llenó o falló en {symbol}. "
            f"spot_filled={spot_filled} ({res_spot['status']}) | "
            f"perp_filled={perp_filled} ({res_perp['status']}). "
            "Iniciando protocolo de emergencia (Rollback)..."
        )
        await self._rollback(symbol, qty, res_spot, res_perp)
        return False

    async def close_delta_neutral(self, symbol: str, qty: float) -> bool:
        """
        Cierra una posición Delta-Neutral existente con operaciones inversas:
          - SELL en Spot   (deshace la compra del activo real)
          - BUY  en Linear (cierra el short de futuros)

        Aplica el mismo redondeo de qty_step que la apertura para garantizar
        que las cantidades sean válidas en ambos mercados.

        Args:
            symbol: Par en formato Bybit, e.g. "ZKUSDT".
            qty:    Tamaño de la posición a cerrar en tokens (base coin).

        Returns:
            True si ambas patas se cerraron correctamente, False en caso contrario.
        """
        logger.info(f"=== INICIANDO CIERRE DELTA-NEUTRAL: {symbol} ===")

        loop = asyncio.get_running_loop()
        max_retries = settings.max_retries
        try:
            realized_pnl_snapshot = await loop.run_in_executor(
                None, self._get_linear_short_unrealized_pnl, symbol
            )
        except Exception as exc:
            realized_pnl_snapshot = 0.0
            logger.warning(
                f"[{symbol}] No se pudo leer unrealizedPnl para snapshot de cierre: {exc}. "
                "Se persistirá realized_pnl=0."
            )

        # ----------------------------------------------------------------
        # 1. Obtener qty_step de ambos mercados y balance real en Spot
        #    en paralelo para minimizar latencia antes del gather de cierre.
        #
        #    Bybit cobra la comisión de la compra original restando una
        #    pequeña fracción del token base, por lo que el balance real
        #    disponible es ligeramente menor que qty nominal del contrato.
        #    Usar qty nominal en Spot → ErrCode 170131 (Insufficient balance).
        # ----------------------------------------------------------------
        base_coin: str = symbol.removesuffix("USDT")

        async def _fresh_spot_close_qty() -> tuple[float, float, float]:
            """Lee balance real y step de spot para recalcular qty de cierre."""
            spot_balance, spot_step = await asyncio.gather(
                loop.run_in_executor(None, self._get_spot_balance, base_coin),
                loop.run_in_executor(None, self.exchange.get_qty_step, symbol, "spot"),
            )
            return _apply_qty_step(spot_balance, spot_step), spot_balance, spot_step

        async def _fresh_linear_close_qty(nominal_qty: float) -> tuple[float, float, float]:
            """Lee el short remanente y step de linear para recalcular qty de cierre."""
            short_size, linear_step = await asyncio.gather(
                loop.run_in_executor(None, self._get_linear_short_size, symbol),
                loop.run_in_executor(None, self.exchange.get_qty_step, symbol, "linear"),
            )
            reference_qty = short_size if short_size > 0 else nominal_qty
            return _apply_qty_step(reference_qty, linear_step), short_size, linear_step

        try:
            (qty_spot, spot_balance, spot_step), (qty_linear, short_size, linear_step) = await asyncio.gather(
                _fresh_spot_close_qty(),
                _fresh_linear_close_qty(qty),
            )
        except ValueError as exc:
            logger.error(f"No se pudo obtener qty_step para {symbol}: {exc}. Cierre abortado.")
            return False

        # ----------------------------------------------------------------
        # 2. Asimetría de cierre:
        #    - Spot SELL  → usamos el balance real (lo que realmente tenemos)
        #    - Linear BUY → usamos qty nominal del contrato (lo que reporta Bybit)
        #    Ambos se redondean con su propio step para garantizar validez.
        # ----------------------------------------------------------------
        logger.info(
            f"Cierre asimétrico | symbol={symbol} | "
            f"spot_balance={spot_balance} → qty_spot={qty_spot} (step={spot_step}) | "
            f"qty_nominal={qty} | short_size={short_size} → qty_linear={qty_linear} "
            f"(step={linear_step})"
        )

        if not qty_spot and not qty_linear:
            logger.critical(
                f"[{symbol}] Cierre abortado: ambas patas en qty=0 | "
                f"spot_balance={spot_balance} step_spot={spot_step} | "
                f"short_size={short_size} step_linear={linear_step}. "
                "No hay nada que cerrar."
            )
            return False

        if not qty_spot and qty_linear > 0:
            logger.critical(
                f"[{symbol}] SPOT expirado/externo detectado (qty_spot=0) con short activo. "
                f"Se cerrará SOLO pata linear con reduceOnly=True | qty_linear={qty_linear}."
            )
            res_linear_only = await self._colocar_orden(
                symbol, "Buy", qty_linear, "linear", reduce_only=True
            )
            if res_linear_only["status"] != "success":
                logger.critical(
                    f"[{symbol}] FALLO REAL cerrando pata linear huérfana | "
                    f"retCode={res_linear_only.get('retCode')} | "
                    f"msg={res_linear_only.get('msg')}"
                )
                await self._send_alert(
                    "[CRITICO] Fallo cerrando pata linear huérfana en "
                    f"*{symbol}*. retCode={res_linear_only.get('retCode')} "
                    f"msg={res_linear_only.get('msg')}"
                )
                return False

            self._record_trade(
                symbol=symbol,
                side="Buy",
                qty=qty_linear,
                category="linear",
                result=res_linear_only,
            )
            self.store.mark_position_closed(symbol, realized_pnl=realized_pnl_snapshot)
            logger.critical(
                f"[{symbol}] Cierre de emergencia exitoso: solo linear cerrada "
                "tras detectar spot expirado/externo."
            )
            return True

        if not qty_linear and qty_spot > 0:
            logger.critical(
                f"[{symbol}] LINEAR ya cerrada/externa detectada (qty_linear=0) con spot remanente. "
                f"Se cerrará SOLO pata spot | qty_spot={qty_spot}."
            )
            res_spot_only = await self._colocar_orden(symbol, "Sell", qty_spot, "spot")
            if res_spot_only["status"] != "success":
                logger.critical(
                    f"[{symbol}] FALLO REAL cerrando pata spot remanente | "
                    f"retCode={res_spot_only.get('retCode')} | "
                    f"msg={res_spot_only.get('msg')}"
                )
                await self._send_alert(
                    "[CRITICO] Fallo cerrando pata spot remanente en "
                    f"*{symbol}*. retCode={res_spot_only.get('retCode')} "
                    f"msg={res_spot_only.get('msg')}"
                )
                return False

            self._record_trade(
                symbol=symbol,
                side="Sell",
                qty=qty_spot,
                category="spot",
                result=res_spot_only,
            )
            self.store.mark_position_closed(symbol, realized_pnl=realized_pnl_snapshot)
            logger.critical(
                f"[{symbol}] Cierre de emergencia exitoso: solo spot cerrada "
                "tras detectar linear cerrada/externa."
            )
            return True

        # Disparar cierre de ambas patas simultáneamente (misma garantía anti-Legging)
        tarea_spot = self._colocar_orden(symbol, "Sell", qty_spot,   "spot")
        tarea_perp = self._colocar_orden(
            symbol, "Buy", qty_linear, "linear", reduce_only=True
        )

        logger.info("Disparando órdenes de cierre concurrentes...")
        res_spot, res_perp = await asyncio.gather(tarea_spot, tarea_perp)

        if res_spot["status"] == "success" and res_perp["status"] == "success":
            self._record_trade(
                symbol=symbol,
                side="Sell",
                qty=qty_spot,
                category="spot",
                result=res_spot,
            )
            self._record_trade(
                symbol=symbol,
                side="Buy",
                qty=qty_linear,
                category="linear",
                result=res_perp,
            )
            self.store.mark_position_closed(symbol, realized_pnl=realized_pnl_snapshot)
            logger.success(
                f"Posición cerrada correctamente | {symbol} | "
                f"spot_orderId={res_spot['orderId']} | perp_orderId={res_perp['orderId']}"
            )
            return True

        # Si solo una pata falla, reintentamos exclusivamente la fallida.
        if res_spot["status"] != "success" and res_perp["status"] == "success":
            self._record_trade(
                symbol=symbol,
                side="Buy",
                qty=qty_linear,
                category="linear",
                result=res_perp,
            )
            logger.warning(
                f"[{symbol}] Cierre parcial: falló Spot y Linear cerró OK. "
                f"Reintentando pata Spot hasta {max_retries} veces."
            )
            last_error = res_spot
            for attempt in range(1, max_retries + 1):
                qty_spot_retry, spot_balance_retry, spot_step_retry = await _fresh_spot_close_qty()
                if not qty_spot_retry:
                    logger.critical(
                        f"[{symbol}] Retry Spot #{attempt}: qty=0 | "
                        f"spot_balance={spot_balance_retry} step={spot_step_retry}."
                    )
                    continue
                logger.warning(
                    f"[{symbol}] Retry Spot #{attempt}/{max_retries} | qty={qty_spot_retry}"
                )
                last_error = await self._colocar_orden(symbol, "Sell", qty_spot_retry, "spot")
                if last_error["status"] == "success":
                    self._record_trade(
                        symbol=symbol,
                        side="Sell",
                        qty=qty_spot_retry,
                        category="spot",
                        result=last_error,
                    )
                    self.store.mark_position_closed(symbol, realized_pnl=realized_pnl_snapshot)
                    logger.success(
                        f"[{symbol}] Recuperación exitosa: Spot cerrada en retry #{attempt}."
                    )
                    return True

            logger.critical(
                f"[{symbol}] FALLO CRÍTICO DE CIERRE | pata_fallida=spot | "
                f"retCode={last_error.get('retCode')} | msg={last_error.get('msg')}"
            )
            await self._send_alert(
                "[CRITICO] Fallo definitivo de cierre Delta-Neutral en "
                f"*{symbol}*. Pata huérfana: *spot*. "
                f"retCode={last_error.get('retCode')} msg={last_error.get('msg')}"
            )
            return False

        if res_perp["status"] != "success" and res_spot["status"] == "success":
            self._record_trade(
                symbol=symbol,
                side="Sell",
                qty=qty_spot,
                category="spot",
                result=res_spot,
            )
            logger.warning(
                f"[{symbol}] Cierre parcial: falló Linear y Spot cerró OK. "
                f"Reintentando pata Linear hasta {max_retries} veces."
            )
            last_error = res_perp
            for attempt in range(1, max_retries + 1):
                qty_linear_retry, short_size_retry, linear_step_retry = await _fresh_linear_close_qty(qty)
                if not qty_linear_retry:
                    if short_size_retry == 0:
                        self.store.mark_position_closed(symbol, realized_pnl=realized_pnl_snapshot)
                        logger.warning(
                            f"[{symbol}] Short linear ya está en 0 durante retry #{attempt}. "
                            "Se considera cierre efectivo."
                        )
                        return True
                    logger.critical(
                        f"[{symbol}] Retry Linear #{attempt}: qty=0 | "
                        f"short_size={short_size_retry} step={linear_step_retry}."
                    )
                    continue
                logger.warning(
                    f"[{symbol}] Retry Linear #{attempt}/{max_retries} | qty={qty_linear_retry}"
                )
                last_error = await self._colocar_orden(
                    symbol, "Buy", qty_linear_retry, "linear", reduce_only=True
                )
                if last_error["status"] == "success":
                    self._record_trade(
                        symbol=symbol,
                        side="Buy",
                        qty=qty_linear_retry,
                        category="linear",
                        result=last_error,
                    )
                    self.store.mark_position_closed(symbol, realized_pnl=realized_pnl_snapshot)
                    logger.success(
                        f"[{symbol}] Recuperación exitosa: Linear cerrada en retry #{attempt}."
                    )
                    return True

            logger.critical(
                f"[{symbol}] FALLO CRÍTICO DE CIERRE | pata_fallida=linear | "
                f"retCode={last_error.get('retCode')} | msg={last_error.get('msg')}"
            )
            await self._send_alert(
                "[CRITICO] Fallo definitivo de cierre Delta-Neutral en "
                f"*{symbol}*. Pata huérfana: *linear*. "
                f"retCode={last_error.get('retCode')} msg={last_error.get('msg')}"
            )
            return False

        logger.critical(
            f"ERROR AL CERRAR posición en {symbol}. "
            f"spot={res_spot['status']} | perp={res_perp['status']}. "
            "Ambas patas fallaron en el intento inicial. Verificar manualmente en Bybit."
        )
        await self._send_alert(
            "[CRITICO] Fallo definitivo de cierre Delta-Neutral en "
            f"*{symbol}*. Ambas patas fallaron en el intento inicial."
        )
        return False