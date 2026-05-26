"""
config/settings.py
==================
Carga y valida todas las variables de entorno del proyecto.
Expone un objeto `Settings` singleton con los parámetros globales.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# Busca el .env en la raíz del proyecto (un nivel arriba de /config)
_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=_ENV_PATH)


def _require(key: str) -> str:
    """Lee una variable de entorno obligatoria; lanza ValueError si falta."""
    value = os.getenv(key)
    if not value:
        raise ValueError(
            f"Variable de entorno requerida '{key}' no encontrada. "
            f"Revisa tu archivo .env (plantilla: .env.example)."
        )
    return value


def _csv_env_list(key: str, default: str) -> list[str]:
    raw = os.getenv(key, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


@dataclass(frozen=True)
class Settings:
    """Configuración global del bot, cargada desde variables de entorno."""

    # --- Credenciales Bybit ---
    api_key: str = field(default_factory=lambda: _require("BYBIT_API_KEY"))
    api_secret: str = field(default_factory=lambda: _require("BYBIT_API_SECRET"))
    testnet: bool = field(
        default_factory=lambda: os.getenv("BYBIT_TESTNET", "true").lower() == "true"
    )

    # --- Parámetros de trading ---
    base_currency: str = field(
        default_factory=lambda: os.getenv("BASE_CURRENCY", "USDT")
    )
    max_position_usdt: float = field(
        default_factory=lambda: float(os.getenv("MAX_POSITION_USDT", "1000"))
    )
    max_breakeven_periods: float = field(
        default_factory=lambda: float(os.getenv("MAX_BREAKEVEN_PERIODS", "3"))
    )
    min_notional_usdt: float = field(
        default_factory=lambda: float(os.getenv("MIN_NOTIONAL_USDT", "10"))
    )
    bot_order_prefix: str = field(
        default_factory=lambda: os.getenv("BOT_ORDER_PREFIX", "FBOT")
    )
    telegram_bot_token: str = field(
        default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN", "")
    )
    telegram_chat_id: str = field(
        default_factory=lambda: os.getenv("TELEGRAM_CHAT_ID", "")
    )
    cors_origins: list[str] = field(
        default_factory=lambda: _csv_env_list(
            "CORS_ORIGINS",
            "http://localhost:5173",
        )
    )
    api_access_key: str = field(
        default_factory=lambda: os.getenv("API_ACCESS_KEY", "")
    )
    max_retries: int = field(
        default_factory=lambda: int(os.getenv("MAX_RETRIES", "3"))
    )
    max_open_positions: int = field(
        default_factory=lambda: int(os.getenv("MAX_OPEN_POSITIONS", "3"))
    )
    db_path: str = field(
        default_factory=lambda: os.getenv(
            "DB_PATH",
            str(Path(__file__).resolve().parent.parent / "data" / "bot_database.db"),
        )
    )
    max_network_retries: int = field(
        default_factory=lambda: int(os.getenv("MAX_NETWORK_RETRIES", "3"))
    )
    backoff_factor: float = field(
        default_factory=lambda: float(os.getenv("BACKOFF_FACTOR", "2.0"))
    )
    ws_ticker_max_symbols_per_connection: int = field(
        default_factory=lambda: int(os.getenv("WS_TICKER_MAX_SYMBOLS_PER_CONN", "50"))
    )
    ws_ticker_stale_seconds: float = field(
        default_factory=lambda: float(os.getenv("WS_TICKER_STALE_SECONDS", "20"))
    )
    leverage: int = field(
        default_factory=lambda: int(os.getenv("LEVERAGE", "1"))
    )
    max_fill_check_attempts: int = field(
        default_factory=lambda: int(os.getenv("MAX_FILL_CHECK_ATTEMPTS", "5"))
    )
    fill_check_delay_seconds: float = field(
        default_factory=lambda: float(os.getenv("FILL_CHECK_DELAY_SECONDS", "0.5"))
    )
    spot_fill_tolerance: float = field(
        default_factory=lambda: float(os.getenv("SPOT_FILL_TOLERANCE", "0.95"))
    )
    hedge_integrity_tolerance: float = field(
        default_factory=lambda: float(os.getenv("HEDGE_INTEGRITY_TOLERANCE", "0.90"))
    )
    ws_scanner_push_seconds: int = field(
        default_factory=lambda: int(os.getenv("WS_SCANNER_PUSH_SECONDS", "5"))
    )
    scanner_top_n: int = field(
        default_factory=lambda: int(os.getenv("SCANNER_TOP_N", "20"))
    )
    spot_symbols_cache_ttl_seconds: int = field(
        default_factory=lambda: int(os.getenv("SPOT_SYMBOLS_CACHE_TTL_SECONDS", "300"))
    )

    def __post_init__(self) -> None:
        if self.leverage < 1 or self.leverage > 10:
            raise ValueError(f"LEVERAGE debe estar entre 1 y 10. Recibido: {self.leverage}")
        if self.max_position_usdt <= 0:
            raise ValueError("MAX_POSITION_USDT debe ser mayor que 0.")
        if self.max_breakeven_periods <= 0:
            raise ValueError("MAX_BREAKEVEN_PERIODS debe ser mayor que 0.")
        if self.min_notional_usdt <= 0:
            raise ValueError("MIN_NOTIONAL_USDT debe ser mayor que 0.")
        if not self.bot_order_prefix.strip():
            raise ValueError("BOT_ORDER_PREFIX no puede estar vacío.")
        if self.max_retries < 0:
            raise ValueError("MAX_RETRIES debe ser mayor o igual que 0.")
        if self.max_open_positions <= 0:
            raise ValueError("MAX_OPEN_POSITIONS debe ser mayor que 0.")
        if not self.db_path.strip():
            raise ValueError("DB_PATH no puede estar vacío.")
        if not self.cors_origins:
            raise ValueError("CORS_ORIGINS debe contener al menos un origen.")
        if self.max_network_retries < 0:
            raise ValueError("MAX_NETWORK_RETRIES debe ser mayor o igual que 0.")
        if self.backoff_factor < 1.0:
            raise ValueError("BACKOFF_FACTOR debe ser >= 1.0.")
        if self.ws_ticker_max_symbols_per_connection <= 0:
            raise ValueError("WS_TICKER_MAX_SYMBOLS_PER_CONN debe ser mayor que 0.")
        if self.ws_ticker_stale_seconds <= 0:
            raise ValueError("WS_TICKER_STALE_SECONDS debe ser mayor que 0.")
        if self.max_fill_check_attempts < 1:
            raise ValueError("MAX_FILL_CHECK_ATTEMPTS debe ser >= 1.")
        if self.fill_check_delay_seconds <= 0:
            raise ValueError("FILL_CHECK_DELAY_SECONDS debe ser > 0.")
        if not (0 < self.spot_fill_tolerance <= 1.0):
            raise ValueError("SPOT_FILL_TOLERANCE debe estar en (0, 1.0].")
        if not (0 < self.hedge_integrity_tolerance <= 1.0):
            raise ValueError("HEDGE_INTEGRITY_TOLERANCE debe estar en (0, 1.0].")
        if self.ws_scanner_push_seconds < 1:
            raise ValueError("WS_SCANNER_PUSH_SECONDS debe ser >= 1.")
        if self.scanner_top_n < 1:
            raise ValueError("SCANNER_TOP_N debe ser >= 1.")
        if self.spot_symbols_cache_ttl_seconds < 0:
            raise ValueError("SPOT_SYMBOLS_CACHE_TTL_SECONDS debe ser >= 0.")


# Instancia global — importar desde aquí en el resto del proyecto:
#   from config.settings import settings
settings = Settings()
