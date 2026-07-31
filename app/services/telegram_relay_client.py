"""Клиент relay-сервиса для прямой отправки медиа в Telegram (в обход Salebot).

Наш сервер не может напрямую достучаться до api.telegram.org (сеть
заблокирована), поэтому запрос идёт на relay — отдельный сервис на VPS вне
РФ-облаков (см. папку relay/ в корне репозитория), который уже сам грузит
файл в Telegram Bot API.
"""
import logging

import aiohttp

from app.settings import settings

logger = logging.getLogger(__name__)

_UPLOAD_TIMEOUT = 120

# Ограничение Telegram на длину подписи к вложению.
CAPTION_LIMIT = 1024

# Типы медиа amojo, для которых relay умеет подобрать метод Bot API.
_SUPPORTED_MEDIA_TYPES = {"picture", "voice", "video", "file"}


class TelegramSendError(Exception):
    """Не удалось отправить медиа через relay."""


def supports_media_type(media_type: str) -> bool:
    """Проверить, что для типа медиа есть метод отправки на стороне relay."""
    return media_type in _SUPPORTED_MEDIA_TYPES


class TelegramRelayClient:
    """Отправка файлов клиенту через relay-сервис (HTTP + multipart)."""

    def __init__(self, token: str) -> None:
        """
        Args:
            token: Токен Telegram-бота (формат "<bot_id>:<secret>")
        """
        self.token = token

    async def send_media(
        self,
        chat_id: str,
        media_type: str,
        content: bytes,
        filename: str,
        caption: str | None = None,
    ) -> str:
        """
        Отправить файл клиенту через relay, загрузив его байтами.

        Args:
            chat_id: Telegram ID клиента (platform_id из диалога)
            media_type: Тип медиа из amojo (picture/voice/video/file)
            content: Содержимое файла
            filename: Имя файла с расширением
            caption: Подпись к вложению (опционально)

        Returns:
            Название использованного Telegram Bot API метода (из ответа relay)

        Raises:
            TelegramSendError: Если relay недоступен или не смог отправить файл
        """
        if not settings.TELEGRAM_RELAY_URL:
            raise TelegramSendError("TELEGRAM_RELAY_URL is not configured")

        url = f"{settings.TELEGRAM_RELAY_URL.rstrip('/')}/send-media"

        form = aiohttp.FormData()
        form.add_field("token", self.token)
        form.add_field("chat_id", str(chat_id))
        form.add_field("media_type", media_type)
        if caption:
            form.add_field("caption", caption[:CAPTION_LIMIT])
        form.add_field("file", content, filename=filename)

        headers = {"X-Relay-Secret": settings.TELEGRAM_RELAY_SECRET}

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    data=form,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=_UPLOAD_TIMEOUT),
                ) as response:
                    payload = await response.json(content_type=None)

                    if response.status >= 400 or payload.get("ok") is not True:
                        raise TelegramSendError(
                            f"{response.status}: {payload.get('detail', payload)}"
                        )

                    method = payload.get("method", "unknown")
                    logger.info(
                        "Media sent via relay: chat_id=%s, method=%s, %d bytes",
                        chat_id,
                        method,
                        len(content),
                    )
                    return method
        except TelegramSendError:
            raise
        except Exception as e:
            raise TelegramSendError(f"{type(e).__name__}: {e}") from e
