"""
data/scanner.py
===============
Escanea y rankea los funding rates disponibles en Bybit para identificar
las oportunidades de arbitraje más rentables.

Responsabilidades:
  - Obtener funding rates de todos los pares USDT-Perpetuo vía REST.
  - Filtrar por liquidez mínima y umbrales de rentabilidad.
  - Devolver un DataFrame ordenado por APR estimado descendente.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
from loguru import logger

from core.exchange import BybitExchange
from data.websockets import FundingTickerCache


# Constantes de filtrado por defecto
MIN_VOLUME_24H_USDT = 5_000_000   # Volumen mínimo 24h en USDT
MIN_FUNDING_RATE_ABS = 0.0001     # |funding rate| mínimo (0.01%)
FUNDING_INTERVALS_PER_DAY = 3     # Bybit liquida cada 8 horas → 3 veces/día


class FundingRateScanner:
    """
    Escanea todos los contratos USDT-Perpetuo de Bybit y construye
    un ranking de oportunidades de funding rate arbitrage.
    """

    def __init__(
        self,
        exchange: BybitExchange,
        ticker_cache: FundingTickerCache | None = None,
        ws_stale_after_seconds: float = 20.0,
    ) -> None:
        self._exchange = exchange
        self._ticker_cache = ticker_cache
        self._ws_stale_after_seconds = ws_stale_after_seconds

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------

    def scan(
        self,
        min_volume: float = MIN_VOLUME_24H_USDT,
        min_rate: float = MIN_FUNDING_RATE_ABS,
        top_n: int = 20,
    ) -> pd.DataFrame:
        """
        Ejecuta el escaneo completo y devuelve los mejores pares ordenados.

        Args:
            min_volume: Volumen mínimo 24h (USDT) para incluir un par.
            min_rate:   Valor absoluto mínimo del funding rate.
            top_n:      Número máximo de resultados a devolver.

        Returns:
            DataFrame con columnas:
              - symbol, funding_rate, funding_rate_pct, apr_est,
                volume_24h, next_funding_time
        """
        raw = self._fetch_all_tickers()
        df = self._parse_tickers(raw)
        df = self._filter(df, min_volume=min_volume, min_rate=min_rate)

        # Descartar perpetuos que no tienen contraparte en Spot.
        # Evita errores 170121 (Invalid symbol) al intentar operar ambas patas.
        spot_symbols = self._get_spot_symbols()
        n_before = len(df)
        df = df[df["symbol"].isin(spot_symbols)].copy()
        n_dropped = n_before - len(df)
        if n_dropped:
            logger.debug(f"{n_dropped} pares descartados por no tener mercado Spot.")

        df = self._rank(df)
        logger.info(f"Scan completado: {len(df)} pares elegibles (top_n={top_n})")
        return df.head(top_n).reset_index(drop=True)

    # ------------------------------------------------------------------
    # Métodos internos
    # ------------------------------------------------------------------

    def _fetch_all_tickers(self) -> list[dict[str, Any]]:
        """
        Obtiene tickers linear usando WS cache como fuente primaria.
        Si el cache está vacío o stale, cae a REST con retries (P7).
        """
        if self._ticker_cache is not None:
            ws_tickers = self._ticker_cache.get_all_tickers(
                max_age_seconds=self._ws_stale_after_seconds
            )
            if ws_tickers:
                logger.debug(f"Tickers obtenidos desde WS cache: {len(ws_tickers)}")
                return list(ws_tickers.values())

            logger.debug("WS cache vacío/stale en scanner. Usando fallback REST.")

        logger.debug("Obteniendo tickers de Bybit linear vía REST...")
        response = self._exchange.get_tickers(category="linear")
        tickers: list[dict[str, Any]] = response["result"]["list"]
        logger.debug(f"{len(tickers)} tickers recibidos")
        return tickers

    def _get_spot_symbols(self) -> set[str]:
        """
        Devuelve el conjunto de símbolos disponibles en el mercado Spot.
        Se usa para filtrar perpetuos que no tienen contraparte negociable en Spot.
        """
        logger.debug("Obteniendo instrumentos Spot para filtro de cross-listing...")
        instruments = self._exchange.get_instruments_info(category="spot")
        return {inst["symbol"] for inst in instruments}

    @staticmethod
    def _parse_tickers(tickers: list[dict[str, Any]]) -> pd.DataFrame:
        """Convierte la lista raw de tickers en un DataFrame limpio."""
        records = []
        for t in tickers:
            try:
                records.append(
                    {
                        "symbol": t["symbol"],
                        "funding_rate": float(t.get("fundingRate", 0)),
                        "next_funding_time": t.get("nextFundingTime", ""),
                        "volume_24h": float(t.get("volume24h", 0)),
                        "last_price": float(t.get("lastPrice", 0)),
                    }
                )
            except (KeyError, ValueError):
                continue  # saltar pares con datos incompletos

        df = pd.DataFrame(records)
        df["funding_rate_pct"] = df["funding_rate"] * 100
        # APR estimado: rate * liquidaciones_por_día * 365
        df["apr_est"] = df["funding_rate"].abs() * FUNDING_INTERVALS_PER_DAY * 365 * 100
        return df

    @staticmethod
    def _filter(
        df: pd.DataFrame,
        min_volume: float,
        min_rate: float,
    ) -> pd.DataFrame:
        """Aplica filtros de volumen y tamaño mínimo del funding rate."""
        mask = (df["volume_24h"] >= min_volume) & (df["funding_rate"].abs() >= min_rate)
        return df[mask].copy()

    @staticmethod
    def _rank(df: pd.DataFrame) -> pd.DataFrame:
        """Ordena por APR estimado en orden descendente."""
        return df.sort_values("apr_est", ascending=False)
