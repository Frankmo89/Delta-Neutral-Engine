"""
core/store.py
=============
Persistencia local SQLite para estado operativo del bot.

Tablas:
  - bot_positions: estado lógico de posiciones gestionadas por el bot.
  - trades: historial de órdenes ejecutadas por el bot.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path


class SQLiteStore:
    """Capa de persistencia SQLite sin dependencias externas."""

    def __init__(self, db_path: str) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS bot_positions (
                    symbol TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    spot_order_link_id TEXT,
                    perp_order_link_id TEXT,
                    open_timestamp TEXT NOT NULL,
                    close_timestamp TEXT,
                    realized_pnl REAL NOT NULL DEFAULT 0,
                    requires_manual_intervention INTEGER NOT NULL DEFAULT 0,
                    intervention_reason TEXT,
                    updated_at TEXT NOT NULL
                )
                """
            )
            columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(bot_positions)").fetchall()
            }
            if "requires_manual_intervention" not in columns:
                conn.execute(
                    """
                    ALTER TABLE bot_positions
                    ADD COLUMN requires_manual_intervention INTEGER NOT NULL DEFAULT 0
                    """
                )
            if "intervention_reason" not in columns:
                conn.execute(
                    """
                    ALTER TABLE bot_positions
                    ADD COLUMN intervention_reason TEXT
                    """
                )
            if "realized_pnl" not in columns:
                conn.execute(
                    """
                    ALTER TABLE bot_positions
                    ADD COLUMN realized_pnl REAL NOT NULL DEFAULT 0
                    """
                )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    order_link_id TEXT,
                    order_id TEXT,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    category TEXT NOT NULL,
                    qty REAL NOT NULL,
                    price REAL,
                    timestamp TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_trades_symbol_ts
                ON trades(symbol, timestamp)
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_trades_order_link
                ON trades(order_link_id)
                """
            )
            conn.commit()

    @staticmethod
    def _utc_now() -> str:
        return datetime.now(tz=timezone.utc).isoformat()

    def upsert_open_position(
        self,
        symbol: str,
        spot_order_link_id: str | None,
        perp_order_link_id: str | None,
    ) -> None:
        now = self._utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO bot_positions (
                    symbol,
                    status,
                    spot_order_link_id,
                    perp_order_link_id,
                    open_timestamp,
                    close_timestamp,
                    updated_at
                )
                VALUES (?, 'open', ?, ?, ?, NULL, ?)
                ON CONFLICT(symbol) DO UPDATE SET
                    status='open',
                    spot_order_link_id=excluded.spot_order_link_id,
                    perp_order_link_id=excluded.perp_order_link_id,
                    close_timestamp=NULL,
                    realized_pnl=0,
                    requires_manual_intervention=0,
                    intervention_reason=NULL,
                    updated_at=excluded.updated_at
                """,
                (symbol, spot_order_link_id, perp_order_link_id, now, now),
            )
            conn.commit()

    def mark_position_closed(self, symbol: str, realized_pnl: float = 0.0) -> None:
        now = self._utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE bot_positions
                SET status='closed',
                    close_timestamp=?,
                    realized_pnl=?,
                    requires_manual_intervention=0,
                    intervention_reason=NULL,
                    updated_at=?
                WHERE symbol=?
                """,
                (now, float(realized_pnl), now, symbol),
            )
            conn.commit()

    def mark_intervention_required(self, symbol: str, reason: str) -> None:
        now = self._utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE bot_positions
                SET requires_manual_intervention=1,
                    intervention_reason=?,
                    updated_at=?
                WHERE symbol=?
                """,
                (reason, now, symbol),
            )
            conn.commit()

    def clear_intervention(self, symbol: str) -> None:
        now = self._utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE bot_positions
                SET requires_manual_intervention=0,
                    intervention_reason=NULL,
                    updated_at=?
                WHERE symbol=?
                """,
                (now, symbol),
            )
            conn.commit()

    def delete_position(self, symbol: str) -> bool:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                DELETE FROM bot_positions
                WHERE symbol=?
                """,
                (symbol,),
            )
            conn.commit()
        return cursor.rowcount > 0

    def get_open_symbols(self) -> set[str]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT symbol
                FROM bot_positions
                WHERE status='open'
                """
            ).fetchall()
        return {str(row["symbol"]) for row in rows}

    def get_open_positions(self) -> list[dict[str, str | None]]:
        """Devuelve registros abiertos de bot_positions para consumo read-only."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT symbol,
                       status,
                       spot_order_link_id,
                       perp_order_link_id,
                       open_timestamp,
                       close_timestamp,
                     realized_pnl,
                      requires_manual_intervention,
                      intervention_reason,
                       updated_at
                FROM bot_positions
                WHERE status='open'
                ORDER BY open_timestamp DESC
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def get_lifetime_realized_pnl(self) -> float:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT COALESCE(SUM(realized_pnl), 0) AS total_pnl
                FROM bot_positions
                WHERE status='closed'
                """
            ).fetchone()
        return float(row["total_pnl"] if row else 0.0)

    def insert_trade(
        self,
        *,
        order_link_id: str | None,
        order_id: str | None,
        symbol: str,
        side: str,
        category: str,
        qty: float,
        price: float | None,
        timestamp: str | None = None,
    ) -> None:
        ts = timestamp or self._utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO trades (
                    order_link_id,
                    order_id,
                    symbol,
                    side,
                    category,
                    qty,
                    price,
                    timestamp
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    order_link_id,
                    order_id,
                    symbol,
                    side,
                    category,
                    qty,
                    price,
                    ts,
                ),
            )
            conn.commit()
