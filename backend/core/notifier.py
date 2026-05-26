"""
core/notifier.py
================
Notificaciones push opcionales para eventos críticos del bot.

Implementación actual:
  - Telegram Bot API vía stdlib (urllib) ejecutada en thread para no
    bloquear el event loop.
  - Degradación elegante si faltan credenciales en .env.
"""

from __future__ import annotations

import asyncio
import json
from urllib import error, parse, request

from loguru import logger

from config.settings import settings


class TelegramNotifier:
    """Notificador Telegram con degradación elegante."""

    def __init__(self) -> None:
        self._bot_token = settings.telegram_bot_token
        self._chat_id = settings.telegram_chat_id
        self._enabled = bool(self._bot_token and self._chat_id)

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def send_message(self, text: str) -> bool:
        """Envía un mensaje a Telegram; retorna False si está deshabilitado o falla."""
        if not self._enabled:
            logger.debug("TelegramNotifier deshabilitado: faltan TELEGRAM_BOT_TOKEN/CHAT_ID.")
            return False

        payload = {
            "chat_id": self._chat_id,
            "text": text,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        }
        return await asyncio.to_thread(self._post_message, payload)

    def _post_message(self, payload: dict[str, str]) -> bool:
        url = f"https://api.telegram.org/bot{self._bot_token}/sendMessage"
        body = parse.urlencode(payload).encode("utf-8")
        req = request.Request(url, data=body, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")

        try:
            with request.urlopen(req, timeout=10) as response:
                raw = response.read().decode("utf-8")
                data = json.loads(raw)
                ok = bool(data.get("ok", False))
                if not ok:
                    logger.warning(f"Telegram API respondió sin ok: {data}")
                return ok
        except (error.URLError, error.HTTPError, TimeoutError, OSError) as exc:
            logger.warning(f"No se pudo enviar alerta a Telegram: {exc}")
            return False
