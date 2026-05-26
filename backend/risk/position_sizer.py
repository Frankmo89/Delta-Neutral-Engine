"""
risk/position_sizer.py
======================
Valida y calcula el tamaño óptimo de posición para la estrategia
Delta-Neutral, considerando balance disponible, fricción total y
umbrales de rentabilidad mínima.

Responsabilidades:
  - Calcular el costo total de fricción (fees + spread + slippage).
  - Determinar si un trade es viable dado un funding rate.
  - Calcular el tamaño de posición respetando el riesgo máximo configurado.
"""

from __future__ import annotations

from dataclasses import dataclass

from loguru import logger

from config.settings import settings


# Fees por defecto de Bybit (Unified Account, Maker/Taker)
DEFAULT_TAKER_FEE = 0.00055   # 0.055% por leg
DEFAULT_MAKER_FEE = 0.00020   # 0.020% por leg
DEFAULT_SLIPPAGE_EST = 0.0002  # 0.02% slippage estimado por leg


@dataclass
class ViabilityReport:
    """Resultado del análisis de viabilidad de un trade."""

    symbol: str
    funding_rate: float
    gross_apr_pct: float
    total_friction_pct: float
    breakeven_periods: float
    net_apr_pct: float
    position_size_usdt: float
    is_viable: bool
    rejection_reason: str | None = None

    def __str__(self) -> str:
        status = "VIABLE" if self.is_viable else f"NO VIABLE ({self.rejection_reason})"
        return (
            f"[{self.symbol}] {status} | "
            f"Gross APR: {self.gross_apr_pct:.2f}% | "
            f"Fricción round-trip: {self.total_friction_pct:.4f}% | "
            f"Break-even: {self.breakeven_periods:.2f} periodos | "
            f"Tamaño: ${self.position_size_usdt:,.2f}"
        )


class PositionSizer:
    """
    Valida la viabilidad de un trade y calcula el tamaño de posición.

    La viabilidad se calcula por break-even de periodos:
        breakeven_periods = roundtrip_cost / funding_per_period

    donde roundtrip_cost incluye fees de apertura + cierre en ambas patas
    (spot + perp) más slippage estimado.
    """

    def __init__(
        self,
        max_position_usdt: float,
        taker_fee: float = DEFAULT_TAKER_FEE,
        maker_fee: float = DEFAULT_MAKER_FEE,
        slippage: float = DEFAULT_SLIPPAGE_EST,
        min_net_apr_pct: float = 5.0,
        max_breakeven_periods: float = settings.max_breakeven_periods,
        min_notional_usdt: float = settings.min_notional_usdt,
    ) -> None:
        """
        Args:
            max_position_usdt: Capital máximo a destinar por operación.
            taker_fee:         Fee de taker por leg (decimal).
            maker_fee:         Fee de maker por leg (decimal, no usado por defecto).
            slippage:          Slippage estimado por leg (decimal).
            min_net_apr_pct:   APR neto mínimo requerido para aprobar el trade (%).
            max_breakeven_periods: Máximo de periodos para amortizar la fricción.
            min_notional_usdt: Notional mínimo requerido para operar.
        """
        self.max_position_usdt = max_position_usdt
        self.taker_fee = taker_fee
        self.maker_fee = maker_fee
        self.slippage = slippage
        self.min_net_apr_pct = min_net_apr_pct
        self.max_breakeven_periods = max_breakeven_periods
        self.min_notional_usdt = min_notional_usdt

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------

    def evaluate(
        self,
        symbol: str,
        funding_rate: float,
        available_balance_usdt: float,
    ) -> ViabilityReport:
        """
        Evalúa si un par es viable y calcula su tamaño de posición.

        Args:
            symbol:                  Par a evaluar, e.g. "BTCUSDT".
            funding_rate:            Funding rate actual (decimal, e.g. 0.0003).
            available_balance_usdt:  Balance disponible en USDT.

        Returns:
            ViabilityReport con todos los cálculos y la decisión final.
        """
        position = self._position_size(available_balance_usdt)

        report = self._build_report(
            symbol=symbol,
            funding_rate=funding_rate,
            position_size_usdt=position,
        )
        logger.debug(str(report))
        return report

    def evaluate_existing_position(
        self,
        symbol: str,
        funding_rate: float,
        position_size_usdt: float,
    ) -> ViabilityReport:
        """
        Reutiliza la misma lógica de break-even para una posición ya abierta.

        Args:
            symbol: Símbolo del contrato, e.g. "BTCUSDT".
            funding_rate: Funding rate actual como decimal.
            position_size_usdt: Notional actual de la posición.
        """
        report = self._build_report(
            symbol=symbol,
            funding_rate=funding_rate,
            position_size_usdt=position_size_usdt,
        )
        logger.debug(str(report))
        return report

    def calculate_qty(self, position_usdt: float, last_price: float) -> float:
        """
        Convierte un tamaño de posición en USDT a cantidad en moneda base.

        Args:
            position_usdt: Tamaño de posición en USDT.
            last_price:    Precio actual del activo.

        Returns:
            Cantidad en moneda base (truncada a 4 decimales).
        """
        if last_price <= 0:
            raise ValueError(f"last_price debe ser > 0. Recibido: {last_price}")
        return round(position_usdt / last_price, 4)

    # ------------------------------------------------------------------
    # Métodos de cálculo interno
    # ------------------------------------------------------------------

    def _gross_apr(self, funding_rate: float) -> float:
        """
        Calcula el APR bruto anualizado a partir del funding rate.

        APR bruto (%) = |funding_rate| * liquidaciones_por_día * días_año * 100
        """
        return abs(funding_rate) * 3 * 365 * 100

    def _friction_per_round_trip(self) -> float:
        """
        Calcula la fricción total por round-trip (apertura + cierre, dos patas).

        Fricción = (fee_spot + fee_perp + slippage_spot + slippage_perp) * 2
                 = (taker_fee + taker_fee + slippage + slippage) * 2
        """
        friction_one_way = (self.taker_fee + self.slippage) * 2  # spot + perp
        return friction_one_way * 2  # apertura + cierre

    def _breakeven_periods(self, funding_rate: float) -> float:
        """Cuántos periodos de funding se necesitan para amortizar la fricción."""
        funding_per_period = abs(funding_rate)
        if funding_per_period == 0:
            return float("inf")
        return self._friction_per_round_trip() / funding_per_period

    def _build_report(
        self,
        symbol: str,
        funding_rate: float,
        position_size_usdt: float,
    ) -> ViabilityReport:
        gross_apr = self._gross_apr(funding_rate)
        roundtrip_cost = self._friction_per_round_trip()
        breakeven_periods = self._breakeven_periods(funding_rate)
        net_apr = gross_apr - roundtrip_cost * 100

        is_viable = True
        rejection_reason: str | None = None

        if funding_rate <= 0:
            is_viable = False
            rejection_reason = "Funding rate no favorable"
        elif position_size_usdt < self.min_notional_usdt:
            is_viable = False
            rejection_reason = (
                f"Tamaño de posición ${position_size_usdt:.2f} < mínimo requerido "
                f"${self.min_notional_usdt:.2f}"
            )
        elif breakeven_periods > self.max_breakeven_periods:
            is_viable = False
            rejection_reason = (
                f"Break-even {breakeven_periods:.2f} periodos > máximo "
                f"{self.max_breakeven_periods:.2f}"
            )

        return ViabilityReport(
            symbol=symbol,
            funding_rate=funding_rate,
            gross_apr_pct=gross_apr,
            total_friction_pct=roundtrip_cost * 100,
            breakeven_periods=breakeven_periods,
            net_apr_pct=net_apr,
            position_size_usdt=position_size_usdt,
            is_viable=is_viable,
            rejection_reason=rejection_reason,
        )

    def _position_size(self, available_balance_usdt: float) -> float:
        """
        Determina el tamaño de posición respetando el capital disponible y el máximo.
        """
        # Para una posición Delta-Neutral 1:1 necesitamos capital en ambas patas
        max_deployable = min(available_balance_usdt / 2, self.max_position_usdt / 2)
        return round(max_deployable, 2)
