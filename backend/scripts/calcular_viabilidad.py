"""
scripts/calcular_viabilidad.py
==============================
Calculadora offline de fricción y break-even para funding rate arbitrage.
No requiere conexión a la API — útil para análisis previo a operar.

Uso:
    python scripts/calcular_viabilidad.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# Permite importar módulos del proyecto desde cualquier directorio
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from risk.position_sizer import PositionSizer  # noqa: E402


def main() -> None:
    print("=" * 60)
    print("  CALCULADORA DE VIABILIDAD — Funding Rate Arbitrage")
    print("=" * 60)

    # --- Parámetros del análisis (editar aquí) ---
    scenarios = [
        {"symbol": "BTCUSDT",   "funding_rate": 0.0003,   "balance": 2000},
        {"symbol": "ETHUSDT",   "funding_rate": 0.00015,  "balance": 2000},
        {"symbol": "SOLUSDT",   "funding_rate": -0.0005,  "balance": 2000},
        {"symbol": "XRPUSDT",   "funding_rate": 0.00008,  "balance": 2000},
        {"symbol": "DOGEUSDT",  "funding_rate": 0.0002,   "balance": 500},
    ]

    sizer = PositionSizer(
        max_position_usdt=1000,
        taker_fee=0.00055,
        slippage=0.0002,
    )

    print(
        f"\n{'Símbolo':<12} {'F.Rate':>8} {'APR Bruto':>10} "
        f"{'Fricción':>10} {'B/E Pct.':>10} {'Tamaño $':>10} {'Estado'}"
    )
    print("-" * 75)

    for sc in scenarios:
        report = sizer.evaluate(
            symbol=sc["symbol"],
            funding_rate=sc["funding_rate"],
            available_balance_usdt=sc["balance"],
        )
        estado = "✓ VIABLE" if report.is_viable else f"✗ {report.rejection_reason}"
        print(
            f"{report.symbol:<12} "
            f"{report.funding_rate*100:>7.4f}% "
            f"{report.gross_apr_pct:>9.2f}% "
            f"{report.total_friction_pct:>9.4f}% "
            f"{report.breakeven_periods:>9.2f} "
            f"${report.position_size_usdt:>9,.2f}  "
            f"{estado}"
        )

    print("\n" + "=" * 60)
    print("Notas:")
    print("  - Fricción incluye: taker fee (x2 patas) + slippage + cierre")
    print("  - Viabilidad = break-even de periodos <= MAX_BREAKEVEN_PERIODS")
    print("  - Bybit liquida funding cada 8h (3 veces/día)")
    print("=" * 60)


if __name__ == "__main__":
    main()
