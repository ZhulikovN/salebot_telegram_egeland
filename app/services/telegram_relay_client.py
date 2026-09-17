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

    async def resolve_file_url(self, file_id: str) -> str:
        """
        Получить прямую HTTPS-ссылку на файл Telegram по его file_id через relay.

        Используется для медиа от клиента, когда raw Telegram update приходит
        от стороннего сервера (без Salebot как посредника) — там есть только
        file_id, а не готовая ссылка на файл.

        Args:
            file_id: file_id из Telegram update (photo/voice/video/document)

        Returns:
            Прямая HTTPS-ссылка на файл (её можно передать в amojo media_url)

        Raises:
            TelegramSendError: Если relay недоступен или Telegram вернул ошибку
        """
        if not settings.TELEGRAM_RELAY_URL:
            raise TelegramSendError("TELEGRAM_RELAY_URL is not configured")

        url = f"{settings.TELEGRAM_RELAY_URL.rstrip('/')}/resolve-file"

        form = aiohttp.FormData()
        form.add_field("token", self.token)
        form.add_field("file_id", file_id)

        headers = {"X-Relay-Secret": settings.TELEGRAM_RELAY_SECRET}

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    data=form,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as response:
                    payload = await response.json(content_type=None)
                    if response.status >= 400 or payload.get("ok") is not True:
                        raise TelegramSendError(
                            f"{response.status}: {payload.get('detail', payload)}"
                        )
                    file_url = payload["url"]
                    logger.info("File resolved via relay: file_id=%s", file_id)
                    return file_url
        except TelegramSendError:
            raise
        except Exception as e:
            raise TelegramSendError(f"{type(e).__name__}: {e}") from e

    async def fetch_file_bytes(self, file_id: str) -> tuple[bytes, str]:
        """
        Скачать файл из Telegram по file_id через relay и вернуть (байты, имя файла).

        Основной бэкенд не может напрямую обратиться к api.telegram.org, поэтому
        relay скачивает файл и возвращает его байты. Имя файла читается из
        заголовка X-Filename ответа.

        Используется чтобы сохранить файл на нашем сервере и отдать AMO URL
        нашего хостинга (api.telegram.org заблокирован в РФ, AMO не может его скачать).

        Args:
            file_id: file_id из Telegram update

        Returns:
            Кортеж (байты файла, имя файла с расширением)

        Raises:
            TelegramSendError: Если relay недоступен или не смог скачать файл
        """
        if not settings.TELEGRAM_RELAY_URL:
            raise TelegramSendError("TELEGRAM_RELAY_URL is not configured")

        url = f"{settings.TELEGRAM_RELAY_URL.rstrip('/')}/download-file"

        form = aiohttp.FormData()
        form.add_field("token", self.token)
        form.add_field("file_id", file_id)

        headers = {"X-Relay-Secret": settings.TELEGRAM_RELAY_SECRET}

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    data=form,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=60),
                ) as response:
                    if response.status >= 400:
                        detail = await response.text()
                        raise TelegramSendError(f"{response.status}: {detail}")
                    content = await response.read()
                    filename = response.headers.get("X-Filename", f"{file_id}.bin")
                    logger.info(
                        "File fetched via relay: file_id=%s, filename=%s, %d bytes",
                        file_id,
                        filename,
                        len(content),
                    )
                    return content, filename
        except TelegramSendError:
            raise
        except Exception as e:
            raise TelegramSendError(f"{type(e).__name__}: {e}") from e

    async def send_text(self, chat_id: str, text: str) -> None:
        """
        Отправить текстовое сообщение клиенту через relay.

        Используется для ботов без Salebot-клиента (salebot_client_id=0),
        например el_oge_diagnostika_bot — когда Salebot не является посредником.

        Args:
            chat_id: Telegram ID клиента (platform_id из диалога)
            text: Текст сообщения

        Raises:
            TelegramSendError: Если relay недоступен или Telegram вернул ошибку
        """
        if not settings.TELEGRAM_RELAY_URL:
            raise TelegramSendError("TELEGRAM_RELAY_URL is not configured")

        url = f"{settings.TELEGRAM_RELAY_URL.rstrip('/')}/send-text"

        form = aiohttp.FormData()
        form.add_field("token", self.token)
        form.add_field("chat_id", str(chat_id))
        form.add_field("text", text)

        headers = {"X-Relay-Secret": settings.TELEGRAM_RELAY_SECRET}

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    data=form,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as response:
                    payload = await response.json(content_type=None)
                    if response.status >= 400 or payload.get("ok") is not True:
                        raise TelegramSendError(
                            f"{response.status}: {payload.get('detail', payload)}"
                        )
                    logger.info("Text sent via relay: chat_id=%s, length=%d", chat_id, len(text))
        except TelegramSendError:
            raise
        except Exception as e:
            raise TelegramSendError(f"{type(e).__name__}: {e}") from e
