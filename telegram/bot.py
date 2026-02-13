"""
PredictorX — Async Telegram Bot
Consolidated bot with polling-based command handling.
Based on polymarket-copy-bot's async httpx pattern (most robust implementation).
"""

import asyncio
import logging
from typing import Callable, Optional

import httpx

from config.settings import get_settings

logger = logging.getLogger(__name__)


class PredictorXBot:
    """Async Telegram bot for PredictorX."""

    BASE_URL = "https://api.telegram.org/bot{token}"

    def __init__(self):
        settings = get_settings()
        self.token = settings.telegram_bot_token
        self.chat_id = settings.telegram_chat_id
        self.base_url = self.BASE_URL.format(token=self.token)
        self._handlers: dict[str, Callable] = {}
        self._callback_handlers: dict[str, Callable] = {}
        self._offset: int = 0
        self._running = False
        self._client: Optional[httpx.AsyncClient] = None

    @property
    def configured(self) -> bool:
        return bool(self.token and self.chat_id)

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=30)
        return self._client

    # ── Message Sending ───────────────────────────────────

    async def send_message(
        self, text: str, chat_id: str = None, parse_mode: str = "HTML",
        reply_markup: dict = None,
    ) -> dict | bool:
        """Send a message to Telegram. Returns message dict if reply_markup used."""
        if not self.configured:
            logger.warning("Telegram not configured, skipping message")
            return False

        target = chat_id or self.chat_id
        client = await self._get_client()

        try:
            payload = {
                "chat_id": target,
                "text": text,
                "parse_mode": parse_mode,
                "disable_web_page_preview": True,
            }
            if reply_markup:
                payload["reply_markup"] = reply_markup

            resp = await client.post(
                f"{self.base_url}/sendMessage",
                json=payload,
            )
            data = resp.json()
            if not data.get("ok"):
                # Fallback to plain text if HTML parsing fails
                if "can't parse" in str(data.get("description", "")):
                    payload.pop("parse_mode", None)
                    payload.pop("reply_markup", None)
                    resp = await client.post(
                        f"{self.base_url}/sendMessage",
                        json=payload,
                    )
                    return resp.json().get("ok", False)
                logger.error(f"Telegram send failed: {data.get('description')}")
                return False
            if reply_markup:
                return data.get("result", True)
            return True
        except Exception as e:
            logger.error(f"Telegram send error: {e}")
            return False

    async def answer_callback_query(self, callback_query_id: str, text: str = "") -> bool:
        """Answer a callback query (acknowledge button press)."""
        client = await self._get_client()
        try:
            resp = await client.post(
                f"{self.base_url}/answerCallbackQuery",
                json={"callback_query_id": callback_query_id, "text": text},
            )
            return resp.json().get("ok", False)
        except Exception:
            return False

    async def edit_message_text(
        self, chat_id: str, message_id: int, text: str, parse_mode: str = "HTML",
    ) -> bool:
        """Edit an existing message's text (removes inline keyboard too)."""
        client = await self._get_client()
        try:
            resp = await client.post(
                f"{self.base_url}/editMessageText",
                json={
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "text": text,
                    "parse_mode": parse_mode,
                    "disable_web_page_preview": True,
                },
            )
            return resp.json().get("ok", False)
        except Exception:
            return False

    # ── Photo Sending ──────────────────────────────────────

    async def send_photo(self, photo_url: str, caption: str = "", chat_id: str = None) -> bool:
        """Send a photo (by URL) to Telegram."""
        if not self.configured:
            return False

        target = chat_id or self.chat_id
        client = await self._get_client()

        try:
            payload = {"chat_id": target, "photo": photo_url}
            if caption:
                payload["caption"] = caption
                payload["parse_mode"] = "HTML"
            resp = await client.post(
                f"{self.base_url}/sendPhoto",
                json=payload,
            )
            data = resp.json()
            if not data.get("ok"):
                logger.error(f"Telegram photo failed: {data.get('description')}")
                return False
            return True
        except Exception as e:
            logger.error(f"Telegram photo error: {e}")
            return False

    # ── Command Registration ──────────────────────────────

    def command(self, name: str):
        """Decorator to register a command handler."""
        def decorator(func: Callable):
            self._handlers[name] = func
            return func
        return decorator

    def register_command(self, name: str, handler: Callable):
        """Register a command handler programmatically."""
        self._handlers[name] = handler

    def register_callback(self, prefix: str, handler: Callable):
        """Register a callback query handler for a button prefix."""
        self._callback_handlers[prefix] = handler

    # ── Polling ───────────────────────────────────────────

    async def start_polling(self):
        """Start polling for updates and handling commands."""
        if not self.configured:
            logger.warning("Telegram not configured, polling disabled")
            return

        self._running = True
        logger.info("PredictorX Telegram bot started polling")

        # Send startup message
        await self.send_message(
            "<b>\U0001f680 PredictorX Online</b>\n\n"
            "Prediction intelligence platform active.\n"
            "Type /help to see available commands."
        )

        while self._running:
            try:
                await self._poll_updates()
            except Exception as e:
                logger.error(f"Polling error: {e}")
                await asyncio.sleep(5)

            await asyncio.sleep(1)

    async def stop_polling(self):
        """Stop the polling loop."""
        self._running = False
        if self._client:
            await self._client.aclose()

    async def _poll_updates(self):
        """Fetch and process new updates."""
        client = await self._get_client()
        try:
            resp = await client.get(
                f"{self.base_url}/getUpdates",
                params={"offset": self._offset, "timeout": 10},
                timeout=15,
            )
            data = resp.json()

            if not data.get("ok"):
                return

            for update in data.get("result", []):
                self._offset = update["update_id"] + 1
                await self._handle_update(update)

        except httpx.ReadTimeout:
            pass  # Normal for long polling

    async def _handle_update(self, update: dict):
        """Process a single update (message or callback query)."""

        # ── Handle callback queries (inline button presses) ──
        callback = update.get("callback_query")
        if callback:
            cb_id = callback.get("id", "")
            cb_data = callback.get("data", "")
            cb_message = callback.get("message", {})
            cb_chat_id = str(cb_message.get("chat", {}).get("id", ""))
            cb_message_id = cb_message.get("message_id", 0)

            # Match callback data to a registered handler by prefix
            handled = False
            for prefix, handler in self._callback_handlers.items():
                if cb_data.startswith(prefix):
                    try:
                        await handler(cb_chat_id, cb_message_id, cb_data, cb_id)
                        handled = True
                    except Exception as e:
                        logger.error(f"Callback handler error ({prefix}): {e}")
                        await self.answer_callback_query(cb_id, f"Error: {e}")
                    break

            if not handled:
                await self.answer_callback_query(cb_id, "Unknown action")
            return

        # ── Handle text messages / commands ──
        message = update.get("message", {})
        text = message.get("text", "").strip()
        chat_id = str(message.get("chat", {}).get("id", ""))

        if not text.startswith("/"):
            return

        # Parse command and args
        parts = text.split(maxsplit=1)
        command = parts[0].lower().lstrip("/").split("@")[0]  # Handle @botname
        args = parts[1] if len(parts) > 1 else ""

        handler = self._handlers.get(command)
        if handler:
            try:
                await handler(chat_id, args)
            except Exception as e:
                logger.error(f"Error handling /{command}: {e}")
                await self.send_message(f"Error: {e}", chat_id=chat_id)
        else:
            await self.send_message(
                f"Unknown command: /{command}\nType /help for available commands.",
                chat_id=chat_id,
            )


# Global bot instance
_bot: Optional[PredictorXBot] = None


def get_bot() -> PredictorXBot:
    global _bot
    if _bot is None:
        _bot = PredictorXBot()
    return _bot
