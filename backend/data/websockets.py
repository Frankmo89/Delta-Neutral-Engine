"""
data/websockets.py
==================
Servicio de mercado en tiempo real para Bybit V5 usando pybit WebSocket.

Responsabilidades:
    - Suscribirse a tickers linear por lotes (respetando límite por conexión).
    - Mantener un caché en memoria con el último funding/price por símbolo.
    - Exponer lecturas frescas para scanner/monitor y fallback a REST si stale.
    - Cerrar conexiones de forma limpia en shutdown.
"""

from __future__ import annotations

import asyncio
import math
import threading
import time
from collections.abc import Callable
from typing import Any

from loguru import logger
from pybit.unified_trading import WebSocket


TickerCallback = Callable[[dict[str, Any]], None]


class FundingTickerCache:
    """
    Caché de tickers linear alimentado por WebSocket de Bybit.

    Usa una o más conexiones pybit en paralelo para cubrir lotes de símbolos.
    """

    def __init__(
        self,
        testnet: bool = True,
        max_symbols_per_connection: int = 50,
        stale_after_seconds: float = 20.0,
    ) -> None:
        self._testnet = testnet
        self._max_symbols_per_connection = max(1, int(max_symbols_per_connection))
        self._stale_after_seconds = max(1.0, float(stale_after_seconds))

        self._lock = threading.RLock()
        self._connections: list[WebSocket] = []
        self._subscribed_symbols: set[str] = set()
        self._ticker_cache: dict[str, dict[str, Any]] = {}
        self._started = False

    async def start(self, symbols: set[str]) -> None:
        """Inicia conexiones WS y suscribe los símbolos faltantes."""
        await asyncio.to_thread(self._start_sync, symbols)

    async def stop(self) -> None:
        """Cierra todas las conexiones WS activas."""
        await asyncio.to_thread(self._stop_sync)

    def get_ticker(self, symbol: str, max_age_seconds: float | None = None) -> dict[str, Any] | None:
        """Devuelve el último ticker de un símbolo si está fresco."""
        with self._lock:
            item = self._ticker_cache.get(symbol)
            if not item:
                return None
            max_age = self._stale_after_seconds if max_age_seconds is None else max_age_seconds
            age = time.time() - float(item.get("updated_at", 0))
            if age > max_age:
                return None
            return dict(item)

    def get_all_tickers(self, max_age_seconds: float | None = None) -> dict[str, dict[str, Any]]:
        """Devuelve copia del caché de tickers frescos."""
        with self._lock:
            max_age = self._stale_after_seconds if max_age_seconds is None else max_age_seconds
            now = time.time()
            fresh: dict[str, dict[str, Any]] = {}
            for symbol, item in self._ticker_cache.items():
                age = now - float(item.get("updated_at", 0))
                if age <= max_age:
                    fresh[symbol] = dict(item)
            return fresh

    def is_running(self) -> bool:
        with self._lock:
            return self._started

    def subscribed_count(self) -> int:
        with self._lock:
            return len(self._subscribed_symbols)

    def _start_sync(self, symbols: set[str]) -> None:
        symbols_to_add = sorted({s for s in symbols if s and s not in self._subscribed_symbols})
        if not symbols_to_add:
            with self._lock:
                self._started = True
            return

        batches = self._chunk(symbols_to_add, self._max_symbols_per_connection)
        logger.info(
            "Iniciando FundingTickerCache WS | "
            f"nuevos_simbolos={len(symbols_to_add)} conexiones_nuevas={len(batches)}"
        )

        for index, batch in enumerate(batches, start=1):
            ws = self._create_linear_ws()
            subscribed_in_conn = 0

            for symbol in batch:
                ok = self._safe_subscribe_ticker(ws=ws, symbol=symbol)
                if ok:
                    subscribed_in_conn += 1
                    with self._lock:
                        self._subscribed_symbols.add(symbol)

            if subscribed_in_conn > 0:
                with self._lock:
                    self._connections.append(ws)
                logger.debug(
                    f"WS linear {index}/{len(batches)} activa | "
                    f"simbolos={subscribed_in_conn}"
                )
            else:
                self._safe_close_ws(ws)

        with self._lock:
            self._started = True
        logger.info(
            "FundingTickerCache activo | "
            f"simbolos_suscritos={len(self._subscribed_symbols)} "
            f"conexiones={len(self._connections)}"
        )

    def _stop_sync(self) -> None:
        with self._lock:
            connections = list(self._connections)
            self._connections.clear()
            self._subscribed_symbols.clear()
            self._started = False

        for ws in connections:
            self._safe_close_ws(ws)
        logger.info("FundingTickerCache detenido.")

    def _create_linear_ws(self) -> WebSocket:
        return WebSocket(
            testnet=self._testnet,
            channel_type="linear",
        )

    def _safe_subscribe_ticker(self, ws: WebSocket, symbol: str) -> bool:
        dispatcher = self._make_symbol_dispatcher(symbol)
        attempts = 3
        backoff_seconds = 0.5

        for attempt in range(1, attempts + 1):
            try:
                ws.ticker_stream(symbol=symbol, callback=dispatcher)
                return True
            except Exception as exc:
                if attempt >= attempts:
                    logger.error(
                        f"WS subscribe ticker falló | symbol={symbol} | error={exc}"
                    )
                    return False
                wait = backoff_seconds * math.pow(2, attempt - 1)
                logger.warning(
                    f"WS subscribe retry | symbol={symbol} intento={attempt}/{attempts - 1} "
                    f"espera={wait:.2f}s error={exc}"
                )
                time.sleep(wait)

        return False

    @staticmethod
    def _chunk(items: list[str], size: int) -> list[list[str]]:
        return [items[i:i + size] for i in range(0, len(items), size)]

    def _make_symbol_dispatcher(self, symbol: str) -> TickerCallback:
        def dispatcher(data: dict[str, Any]) -> None:
            try:
                ticker = self._normalize_ticker_payload(symbol=symbol, payload=data)
                if ticker is None:
                    return
                with self._lock:
                    self._ticker_cache[symbol] = ticker
            except Exception as exc:
                logger.error(f"Error procesando ticker WS [{symbol}]: {exc}")

        return dispatcher

    @staticmethod
    def _normalize_ticker_payload(symbol: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        data = payload.get("data")
        ticker_data: dict[str, Any] | None

        if isinstance(data, list):
            ticker_data = data[0] if data else None
        elif isinstance(data, dict):
            ticker_data = data
        else:
            ticker_data = None

        if not ticker_data:
            return None

        symbol_from_payload = str(ticker_data.get("symbol") or symbol)
        return {
            "symbol": symbol_from_payload,
            "fundingRate": ticker_data.get("fundingRate", 0),
            "lastPrice": ticker_data.get("lastPrice", 0),
            "nextFundingTime": ticker_data.get("nextFundingTime", ""),
            "volume24h": ticker_data.get("volume24h", 0),
            "updated_at": time.time(),
            "ws_ts": payload.get("ts"),
        }

    @staticmethod
    def _safe_close_ws(ws: WebSocket) -> None:
        try:
            ws.exit()
        except Exception:
            pass


class MarketDataStream:
    """
    Gestiona múltiples suscripciones WebSocket a Bybit de forma asíncrona.

    Ejemplo de uso:
        stream = MarketDataStream(testnet=True)
        stream.subscribe_ticker("BTCUSDT", category="linear", callback=mi_funcion)
        await stream.run_forever()
    """

    def __init__(self, testnet: bool = True) -> None:
        self._testnet = testnet
        self._ws_linear: WebSocket | None = None
        self._ws_spot: WebSocket | None = None
        self._callbacks: dict[str, list[TickerCallback]] = {}

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------

    def subscribe_ticker(
        self,
        symbol: str,
        category: str,
        callback: TickerCallback,
    ) -> None:
        """
        Registra una suscripción al ticker de un símbolo.

        Args:
            symbol:   Par a suscribir, e.g. "BTCUSDT".
            category: "linear" (perp) o "spot".
            callback: Función que recibe el dict de datos del ticker.
        """
        key = f"{category}:{symbol}"
        self._callbacks.setdefault(key, []).append(callback)
        ws = self._get_or_create_ws(category)
        ws.ticker_stream(symbol=symbol, callback=self._make_dispatcher(key))
        logger.info(f"Suscrito a ticker | {key}")

    def subscribe_orderbook(
        self,
        symbol: str,
        category: str,
        depth: int,
        callback: TickerCallback,
    ) -> None:
        """
        Registra una suscripción al order book de un símbolo.

        Args:
            symbol:   Par a suscribir.
            category: "linear" o "spot".
            depth:    Profundidad del order book (1, 50, 200, 500).
            callback: Función que recibe el dict de datos del order book.
        """
        key = f"{category}:orderbook:{symbol}"
        self._callbacks.setdefault(key, []).append(callback)
        ws = self._get_or_create_ws(category)
        ws.orderbook_stream(depth=depth, symbol=symbol, callback=self._make_dispatcher(key))
        logger.info(f"Suscrito a orderbook | {key} depth={depth}")

    async def run_forever(self) -> None:
        """
        Mantiene el loop activo indefinidamente.
        Los callbacks de pybit se ejecutan en hilos internos de la librería;
        este método evita que el proceso principal termine.
        """
        logger.info("MarketDataStream corriendo. Presiona Ctrl+C para detener.")
        try:
            while True:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            logger.info("MarketDataStream detenido.")

    # ------------------------------------------------------------------
    # Métodos internos
    # ------------------------------------------------------------------

    def _get_or_create_ws(self, category: str) -> WebSocket:
        """Devuelve el WebSocket correspondiente a la categoría, creándolo si no existe."""
        if category == "linear":
            if self._ws_linear is None:
                self._ws_linear = WebSocket(
                    testnet=self._testnet,
                    channel_type="linear",
                )
            return self._ws_linear
        elif category == "spot":
            if self._ws_spot is None:
                self._ws_spot = WebSocket(
                    testnet=self._testnet,
                    channel_type="spot",
                )
            return self._ws_spot
        else:
            raise ValueError(f"Categoría de WebSocket no soportada: {category}")

    def _make_dispatcher(self, key: str) -> TickerCallback:
        """Crea un callback que despacha los datos a todos los suscriptores de una key."""

        def dispatcher(data: dict[str, Any]) -> None:
            for cb in self._callbacks.get(key, []):
                try:
                    cb(data)
                except Exception as exc:
                    logger.error(f"Error en callback [{key}]: {exc}")

        return dispatcher
