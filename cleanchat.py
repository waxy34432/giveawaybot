"""Режим «одно сообщение»: новое сообщение бота в личке заменяет предыдущее.

Работает на уровне запросов к Telegram API, поэтому обработчикам почти ничего менять не нужно.
Режимы отправки (контекстный менеджер send_mode):
  dialog (по умолчанию) — удаляет прошлое сообщение диалога, новое запоминает для замены;
  final — удаляет прошлое сообщение диалога, но само не удаляется позже (билет, файл);
  keep  — ничего не удаляет и не запоминается (уведомления, заявки на оплату, итоги).
Группы и каналы не затрагиваются, только личные чаты.
"""
import asyncio
import logging
from collections import defaultdict
from contextlib import contextmanager
from contextvars import ContextVar

from aiogram import BaseMiddleware
from aiogram.client.session.middlewares.base import BaseRequestMiddleware
from aiogram.methods import SendDocument, SendMediaGroup, SendMessage, SendPhoto, SendVideo

log = logging.getLogger("cleanchat")

_MODE: ContextVar = ContextVar("send_mode", default="dialog")
_TRACKED = (SendMessage, SendPhoto, SendDocument, SendVideo, SendMediaGroup)


@contextmanager
def send_mode(mode: str):
    token = _MODE.set(mode)
    try:
        yield
    finally:
        _MODE.reset(token)


class CleanChatMiddleware(BaseRequestMiddleware):
    def __init__(self):
        self._last = {}
        self._locks = defaultdict(asyncio.Lock)

    async def __call__(self, make_request, bot, method):
        if not isinstance(method, _TRACKED):
            return await make_request(bot, method)
        chat_id = getattr(method, "chat_id", None)
        # личные чаты имеют положительный ID; каналы и группы пропускаем
        if not isinstance(chat_id, int) or chat_id <= 0:
            return await make_request(bot, method)

        mode = _MODE.get()
        async with self._locks[chat_id]:
            if mode != "keep":
                await self._purge(bot, chat_id)
            result = await make_request(bot, method)
            if mode == "dialog":
                payload = getattr(result, "result", result)
                items = payload if isinstance(payload, list) else [payload]
                self._last[chat_id] = [m.message_id for m in items if hasattr(m, "message_id")]
        return result

    async def _purge(self, bot, chat_id: int):
        for mid in self._last.pop(chat_id, []):
            try:
                await bot.delete_message(chat_id, mid)
            except Exception as e:
                log.debug("не удалось удалить сообщение %s в %s: %s", mid, chat_id, e)


class CleanUserMessages(BaseMiddleware):
    """Необязательно: удаляет сообщения самого пользователя (текст, фото, видео) после обработки."""

    async def __call__(self, handler, event, data):
        result = await handler(event, data)
        chat = getattr(event, "chat", None)
        user = getattr(event, "from_user", None)
        if chat is not None and chat.type == "private" and user is not None and not user.is_bot:
            try:
                await event.delete()
            except Exception as e:
                log.debug("не удалось удалить сообщение пользователя: %s", e)
        return result
