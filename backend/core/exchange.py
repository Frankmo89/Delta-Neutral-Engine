"""
core/exchange.py
================
Wrapper unificado para la conexión a Bybit.
Combina pybit (WebSocket nativo y REST v5) con ccxt (fallback y utilidades).

Responsabilidades:
  - Inicializar clientes REST autenticados (Spot y Derivados).
  - Proveer métodos de conveniencia para consultas de mercado.
  - Abstraer las diferencias entre Testnet y Mainnet.
"""

from __future__ import annotations

import time
from typing import Any

import ccxt
from loguru import logger
from pybit.unified_trading import HTTP

from config.settings import settings


class BybitExchange:
    """
    Cliente unificado para interactuar con Bybit v5 API.

    Utiliza `pybit` como cliente primario y `ccxt` como secundario
    para operaciones que requieren normalización cross-exchange.
    """

    def __init__(self) -> None:
        self._testnet = settings.testnet
        self._session: HTTP = self._init_pybit_session()
        self._ccxt_client: ccxt.bybit = self._init_ccxt_client()
        self._instrument_lot_cache: dict[tuple[str, str], dict[str, Any]] = {}
        logger.info(
            f"BybitExchange inicializado | testnet={self._testnet}"
        )

    # ------------------------------------------------------------------
    # Resiliencia de red (solo lecturas)
    # ------------------------------------------------------------------

    @staticmethod
    def _is_transient_ret_code(ret_code: int) -> bool:
        """Codigos Bybit que suelen ser transitorios (red/rate-limit/servidor)."""
        return ret_code in {10000, 10006, 10016}

    @staticmethod
    def _is_transient_exception(exc: Exception) -> bool:
        """Heuristica simple para errores transitorios de red/timeout."""
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

    def _with_read_retries(self, operation: str, func, *args, **kwargs) -> dict[str, Any]:
        """Ejecuta una lectura REST con reintentos y backoff exponencial."""
        max_attempts = settings.max_network_retries + 1
        backoff = max(settings.backoff_factor, 1.0)
        last_error: Exception | None = None

        for attempt in range(1, max_attempts + 1):
            try:
                response: dict[str, Any] = func(*args, **kwargs)
                ret_code = int(response.get("retCode", 0))
                if ret_code == 0:
                    return response

                if self._is_transient_ret_code(ret_code) and attempt < max_attempts:
                    wait_s = backoff ** (attempt - 1)
                    logger.warning(
                        f"[{operation}] retCode transitorio={ret_code} | "
                        f"retry {attempt}/{max_attempts - 1} en {wait_s:.2f}s"
                    )
                    time.sleep(wait_s)
                    continue

                raise RuntimeError(
                    f"{operation} falló | retCode={ret_code} "
                    f"retMsg={response.get('retMsg', 'N/A')}"
                )

            except Exception as exc:
                last_error = exc
                if attempt >= max_attempts or not self._is_transient_exception(exc):
                    raise
                wait_s = backoff ** (attempt - 1)
                logger.warning(
                    f"[{operation}] error transitorio: {exc} | "
                    f"retry {attempt}/{max_attempts - 1} en {wait_s:.2f}s"
                )
                time.sleep(wait_s)

        if last_error is not None:
            raise last_error
        raise RuntimeError(f"{operation} falló sin detalle")

    # ------------------------------------------------------------------
    # Inicialización de clientes
    # ------------------------------------------------------------------

    def _init_pybit_session(self) -> HTTP:
        """Inicializa la sesión REST de pybit con las credenciales del .env."""
        return HTTP(
            testnet=self._testnet,
            api_key=settings.api_key,
            api_secret=settings.api_secret,
        )

    def _init_ccxt_client(self) -> ccxt.bybit:
        """Inicializa el cliente ccxt para Bybit con las mismas credenciales."""
        client = ccxt.bybit(
            {
                "apiKey": settings.api_key,
                "secret": settings.api_secret,
                "enableRateLimit": True,
                "options": {"defaultType": "linear"},
            }
        )
        if self._testnet:
            client.set_sandbox_mode(True)
        return client

    # ------------------------------------------------------------------
    # Métodos de mercado (REST)
    # ------------------------------------------------------------------

    def get_funding_rate(self, symbol: str) -> dict[str, Any]:
        """
        Obtiene el funding rate actual y el próximo de un símbolo perpetuo.

        Args:
            symbol: Par en formato Bybit, e.g. "BTCUSDT".

        Returns:
            Dict con 'fundingRate', 'fundingRateTimestamp' y 'nextFundingTime'.
        """
        response = self.get_tickers(category="linear", symbol=symbol)
        result = response["result"]["list"]
        if not result:
            raise ValueError(f"No se encontraron datos para el símbolo: {symbol}")
        return result[0]

    def get_tickers(self, category: str, symbol: str | None = None) -> dict[str, Any]:
        """Wrapper con retry para `session.get_tickers`."""
        params: dict[str, Any] = {"category": category}
        if symbol:
            params["symbol"] = symbol
        return self._with_read_retries("get_tickers", self._session.get_tickers, **params)

    def get_positions(
        self,
        category: str,
        settle_coin: str | None = None,
        symbol: str | None = None,
    ) -> dict[str, Any]:
        """Wrapper con retry para `session.get_positions`."""
        params: dict[str, Any] = {"category": category}
        if settle_coin:
            params["settleCoin"] = settle_coin
        if symbol:
            params["symbol"] = symbol
        return self._with_read_retries("get_positions", self._session.get_positions, **params)

    def get_orderbook(self, symbol: str, limit: int = 5) -> dict[str, Any]:
        """
        Obtiene el order book de un símbolo.

        Args:
            symbol: Par en formato Bybit, e.g. "BTCUSDT".
            limit:  Profundidad del order book (1–500).

        Returns:
            Dict con claves 'bids' y 'asks'.
        """
        response = self._with_read_retries(
            "get_orderbook",
            self._session.get_orderbook,
            category="linear",
            symbol=symbol,
            limit=limit,
        )
        return response["result"]

    def get_wallet_balance(
        self,
        account_type: str = "UNIFIED",
        *,
        total_usdt: bool = False,
    ) -> dict[str, Any] | float:
        """
        Consulta el balance de la billetera.

        Args:
            account_type: Tipo de cuenta Bybit ("UNIFIED", "CONTRACT", "SPOT").
            total_usdt: Si es True, devuelve el balance total consolidado en USDT.

        Returns:
            Dict con el balance por moneda, o float con el total consolidado en USDT.
        """
        response = self._with_read_retries(
            "get_wallet_balance",
            self._session.get_wallet_balance,
            accountType=account_type,
        )
        result = response["result"]
        if not total_usdt:
            return result

        accounts: list[dict[str, Any]] = result.get("list", [])
        if not accounts:
            return 0.0

        account = accounts[0]
        total_wallet_balance = account.get("totalWalletBalance")
        if total_wallet_balance not in (None, ""):
            try:
                return float(total_wallet_balance)
            except (TypeError, ValueError):
                pass

        # Fallback defensivo: sumar walletBalance de USDT si el campo agregado no está.
        usdt_total = 0.0
        for coin_data in account.get("coin", []):
            if str(coin_data.get("coin", "")).upper() != "USDT":
                continue
            usdt_total += float(coin_data.get("walletBalance", "0") or 0)
        return usdt_total

    def get_instruments_info(self, category: str = "linear") -> list[dict[str, Any]]:
        """
        Obtiene la información de todos los instrumentos de una categoría.

        Args:
            category: "linear" (USDT Perp), "spot", "inverse".

        Returns:
            Lista de dicts con metadata de cada instrumento.
        """
        response = self._with_read_retries(
            "get_instruments_info",
            self._session.get_instruments_info,
            category=category,
        )
        return response["result"]["list"]

    def _get_lot_size_filter(self, symbol: str, category: str) -> dict[str, Any]:
        """Obtiene y cachea el lotSizeFilter del instrumento solicitado."""
        cache_key = (symbol, category)
        if cache_key in self._instrument_lot_cache:
            return self._instrument_lot_cache[cache_key]

        response = self._with_read_retries(
            "get_instruments_info",
            self._session.get_instruments_info,
            category=category,
            symbol=symbol,
        )
        instruments: list = response["result"]["list"]
        if not instruments:
            raise ValueError(
                f"Instrumento no encontrado: {symbol} (category={category})"
            )

        lot_filter: dict[str, Any] = dict(instruments[0].get("lotSizeFilter", {}))
        self._instrument_lot_cache[cache_key] = lot_filter
        return lot_filter

    def find_order_by_link_id(
        self,
        category: str,
        symbol: str,
        order_link_id: str,
    ) -> dict[str, Any] | None:
        """
        Busca una orden por orderLinkId en open_orders y order_history.
        Se usa para idempotencia tras errores transitorios de place_order.
        """
        open_orders_resp = self._with_read_retries(
            "get_open_orders",
            self._session.get_open_orders,
            category=category,
            symbol=symbol,
            orderLinkId=order_link_id,
            limit=1,
        )
        open_orders = open_orders_resp.get("result", {}).get("list", [])
        if open_orders:
            return open_orders[0]

        history_resp = self._with_read_retries(
            "get_order_history",
            self._session.get_order_history,
            category=category,
            symbol=symbol,
            orderLinkId=order_link_id,
            limit=1,
        )
        history = history_resp.get("result", {}).get("list", [])
        if history:
            return history[0]
        return None

    # ------------------------------------------------------------------
    # Propiedades de acceso
    # ------------------------------------------------------------------

    @property
    def session(self) -> HTTP:
        """Acceso directo a la sesión pybit para operaciones avanzadas."""
        return self._session

    @property
    def ccxt(self) -> ccxt.bybit:
        """Acceso directo al cliente ccxt para operaciones cross-exchange."""
        return self._ccxt_client

    def get_qty_step(self, symbol: str, category: str) -> float:
        """
        Devuelve el incremento mínimo de cantidad para un instrumento.

        Bybit usa campos distintos según la categoría:
          - 'linear' / 'inverse' → lotSizeFilter.qtyStep
          - 'spot'               → lotSizeFilter.basePrecision

        El resultado se cachea en memoria para evitar llamadas repetidas.
        """
        lot_filter = self._get_lot_size_filter(symbol, category)
        step_key = "basePrecision" if category == "spot" else "qtyStep"
        step = float(lot_filter.get(step_key, "1"))

        lot_filter[step_key] = step
        logger.debug(f"qty_step cacheado | {symbol} [{category}] = {step}")
        return step

    def get_max_order_qty(self, symbol: str, category: str) -> float:
        """
        Devuelve el tamaño máximo permitido por orden para un instrumento.

        El valor se extrae de `lotSizeFilter.maxOrderQty` y se cachea en memoria.
        Si Bybit no expone el dato, se devuelve infinito para no recortar por error.
        """
        lot_filter = self._get_lot_size_filter(symbol, category)
        raw_max_qty = lot_filter.get("maxOrderQty")
        try:
            max_qty = float(raw_max_qty)
        except (TypeError, ValueError):
            max_qty = float("inf")

        if max_qty <= 0:
            max_qty = float("inf")

        lot_filter["maxOrderQty"] = max_qty
        logger.debug(f"max_order_qty cacheado | {symbol} [{category}] = {max_qty}")
        return max_qty
