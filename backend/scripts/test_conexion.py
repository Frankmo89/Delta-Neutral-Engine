"""
scripts/test_conexion.py
========================
Valida que las API keys configuradas en .env funcionan correctamente
contra el Testnet de Bybit.

Comprueba:
  1. Conexión REST (autenticada) → balance de billetera.
  2. Datos de mercado (público) → top 5 funding rates.
  3. WebSocket (público) → recepción de un ticker en tiempo real.

Uso:
    python scripts/test_conexion.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loguru import logger  # noqa: E402

from config.settings import settings  # noqa: E402
from core.exchange import BybitExchange  # noqa: E402
from data.scanner import FundingRateScanner  # noqa: E402
from data.websockets import MarketDataStream  # noqa: E402


def test_rest_autenticado(exchange: BybitExchange) -> bool:
    """Test 1: Consulta el balance de la cuenta (requiere API key válida)."""
    print("\n[TEST 1] Conexión REST autenticada (billetera)...")
    try:
        balance = exchange.get_wallet_balance(account_type="UNIFIED")
        coins = balance.get("list", [{}])[0].get("coin", [])
        usdt = next((c for c in coins if c["coin"] == "USDT"), None)
        usdt_balance = float(usdt["walletBalance"]) if usdt else 0.0
        print(f"  ✓ Conectado | Balance USDT: ${usdt_balance:,.4f}")
        return True
    except Exception as exc:
        print(f"  ✗ FALLÓ: {exc}")
        return False


def test_mercado_publico(exchange: BybitExchange) -> bool:
    """Test 2: Obtiene y muestra los 5 funding rates más altos (no requiere auth)."""
    print("\n[TEST 2] Datos de mercado públicos (funding rates)...")
    try:
        scanner = FundingRateScanner(exchange)
        df = scanner.scan(top_n=5, min_volume=1_000_000, min_rate=0.00005)
        if df.empty:
            print("  ! Sin resultados (puede ser normal en Testnet)")
            return True
        print(f"  ✓ Top 5 funding rates:")
        for _, row in df.iterrows():
            print(
                f"     {row['symbol']:<14} rate={row['funding_rate_pct']:+.4f}%  "
                f"APR≈{row['apr_est']:.1f}%"
            )
        return True
    except Exception as exc:
        print(f"  ✗ FALLÓ: {exc}")
        return False


async def test_websocket_publico() -> bool:
    """Test 3: Escucha un ticker vía WebSocket por 5 segundos."""
    print("\n[TEST 3] WebSocket público (ticker BTCUSDT linear, 5s)...")
    received: list[dict] = []

    def on_ticker(data: dict) -> None:
        received.append(data)
        if len(received) == 1:
            topic = data.get("topic", "?")
            ts = data.get("ts", "?")
            print(f"  ✓ Mensaje recibido | topic={topic} ts={ts}")

    stream = MarketDataStream(testnet=settings.testnet)
    stream.subscribe_ticker("BTCUSDT", category="linear", callback=on_ticker)

    try:
        await asyncio.wait_for(stream.run_forever(), timeout=5.0)
    except asyncio.TimeoutError:
        pass  # esperado — se detiene tras 5s

    if received:
        print(f"  ✓ {len(received)} mensajes recibidos en 5 segundos")
        return True
    else:
        print("  ✗ No se recibieron mensajes (revisar conectividad de red)")
        return False


async def main() -> None:
    print("=" * 55)
    print("  TEST DE CONEXIÓN — Funding Rate Bot (Bybit)")
    print(f"  Modo: {'TESTNET' if settings.testnet else 'MAINNET'}")
    print("=" * 55)

    exchange = BybitExchange()

    results = {
        "REST autenticado": test_rest_autenticado(exchange),
        "Mercado público":  test_mercado_publico(exchange),
        "WebSocket público": await test_websocket_publico(),
    }

    print("\n" + "=" * 55)
    print("  RESUMEN")
    print("=" * 55)
    all_ok = True
    for name, ok in results.items():
        status = "✓ OK" if ok else "✗ FALLÓ"
        print(f"  {status}  {name}")
        if not ok:
            all_ok = False

    print("=" * 55)
    if all_ok:
        print("  Todo en orden. Puedes proceder con el bot.")
    else:
        print("  Revisa los errores anteriores antes de continuar.")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
