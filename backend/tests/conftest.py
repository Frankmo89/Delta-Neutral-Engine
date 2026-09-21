"""Pytest bootstrap.

`config.settings` instantiates Settings() at import time and requires
Bybit credentials. Tests never hit the exchange, so we inject harmless
placeholders before any application module is imported.
"""

from __future__ import annotations

import os
import sys
from types import ModuleType

os.environ.setdefault("BYBIT_API_KEY", "test-key")
os.environ.setdefault("BYBIT_API_SECRET", "test-secret")
os.environ.setdefault("BYBIT_TESTNET", "true")
os.environ.setdefault("MAX_POSITION_USDT", "1000")
os.environ.setdefault("MAX_BREAKEVEN_PERIODS", "3")
os.environ.setdefault("MIN_NOTIONAL_USDT", "10")

# Scanner / exchange imports pull ccxt + pybit. Unit tests never construct
# those clients, so stub the modules at collection time.
if "ccxt" not in sys.modules:
    ccxt = ModuleType("ccxt")
    ccxt.bybit = type("bybit", (), {})  # type: ignore[attr-defined]
    sys.modules["ccxt"] = ccxt

if "pybit" not in sys.modules:
    pybit = ModuleType("pybit")
    unified = ModuleType("pybit.unified_trading")
    unified.HTTP = object
    unified.WebSocket = object
    sys.modules["pybit"] = pybit
    sys.modules["pybit.unified_trading"] = unified
