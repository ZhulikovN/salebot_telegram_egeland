"""Клиент Telegram Bot API для прямой отправки медиа (в обход Salebot)."""
import logging

import aiohttp

logger = logging.getLogger(__name__)

_API_BASE = "https://api.telegram.org"
_UPLOAD_TIMEOUT = 120

# Ограничение Telegram на длину подписи к вложению.
CAPTION_LIMIT = 1024

# Тип медиа из amojo → цепочка методов Bot API (метод, имя поля с файлом).
# Пробуем по порядку: если Telegram отверг файл (неподходящий формат/размер),
# отправляем следующим способом, чтобы клиент всё равно получил вложение.
_MEDIA_METHODS: dict[str, tuple[tuple[str, str], ...]] = {
    "picture": (("sendPhoto", "photo"), ("sendDocument", "document")),
    "voice": (("sendVoice", "voice"), ("sendAudio", "audio")),
    "video": (("sendVideo", "video"), ("sendDocument", "document")),
    "file": (("sendDocument", "document"),),
}


class TelegramSendError(Exception):
    """Не удалось отправить медиа через Telegram Bot API."""


def supports_media_type(media_type: str) -> bool:
    """Проверить, что для типа медиа есть метод отправки."""
    return media_type in _MEDIA_METHODS


class TelegramClient:
    """Отправка файлов напрямую в Telegram Bot API multipart-загрузкой."""

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
        Отправить файл клиенту, загрузив его байтами.

        Args:
            chat_id: Telegram ID клиента (platform_id из диалога)
            media_type: Тип медиа из amojo (picture/voice/video/file)
            content: Содержимое файла
            filename: Имя файла с расширением
            caption: Подпись к вложению (опционально)

        Returns:
            Название использованного метода Bot API

        Raises:
            TelegramSendError: Если ни один способ отправки не сработал
        """
        methods = _MEDIA_METHODS.get(media_type)
        if not methods:
            raise TelegramSendError(f"Unsupported media type: {media_type}")

        last_error = ""

        for method, field_name in methods:
            try:
                await self._call(
                    method=method,
                    field_name=field_name,
                    chat_id=chat_id,
                    content=content,
                    filename=filename,
                    caption=caption,
                )
                logger.info(
                    "Media sent to Telegram: chat_id=%s, method=%s, %d bytes",
                    chat_id,
                    method,
                    len(content),
                )
                return method
            except TelegramSendError as e:
                last_error = str(e)
                logger.warning(
                    "Telegram %s failed for chat_id=%s: %s", method, chat_id, e
                )

        raise TelegramSendError(last_error or f"All methods failed for {media_type}")

    async def _call(
        self,
        method: str,
        field_name: str,
        chat_id: str,
        content: bytes,
        filename: str,
        caption: str | None,
    ) -> None:
        """Выполнить запрос к Bot API с multipart-загрузкой файла."""
        url = f"{_API_BASE}/bot{self.token}/{method}"

        form = aiohttp.FormData()
        form.add_field("chat_id", str(chat_id))
        if caption:
            form.add_field("caption", caption[:CAPTION_LIMIT])
        form.add_field(field_name, content, filename=filename)

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    data=form,
                    timeout=aiohttp.ClientTimeout(total=_UPLOAD_TIMEOUT),
                ) as response:
                    payload = await response.json(content_type=None)

                    if response.status >= 400 or not payload.get("ok"):
                        raise TelegramSendError(
                            f"{response.status}: {payload.get('description', payload)}"
                        )
        except TelegramSendError:
            raise
        except Exception as e:
            raise TelegramSendError(f"{type(e).__name__}: {e}") from e
