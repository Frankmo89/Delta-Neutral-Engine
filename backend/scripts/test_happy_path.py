# scripts/test_happy_path.py
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.exchange import BybitExchange
from core.order_manager import OrderManager

SYMBOL = "BTCUSDT"          # prueba también ETHUSDT
FORCED_FUNDING_PCT = 1.0    # alto a propósito: solo para pasar el gate de viabilidad
CAPITAL = 500.0


async def main():
    ex = BybitExchange()
    om = OrderManager(ex)

    ticker = ex.get_tickers(category="linear", symbol=SYMBOL)
    price = float(ticker["result"]["list"][0]["lastPrice"])
    print(f"Precio actual {SYMBOL}: {price}")

    ok = await om.open_delta_neutral(
        symbol=SYMBOL,
        current_price=price,
        funding_rate_pct=FORCED_FUNDING_PCT,
        max_capital_usdt=CAPITAL,
    )
    print(f"\n=== RESULTADO open_delta_neutral: {ok} ===")


if __name__ == "__main__":
    asyncio.run(main())