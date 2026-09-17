"""Клиент для работы с amojo API (чаты AmoCRM)."""
import hashlib
import hmac
import logging
from datetime import datetime, timezone
from email.utils import format_datetime
from typing import Any
import json

import aiohttp

from app.services.amocrm_client import RetryableAmoCRMError
from app.settings import settings

logger = logging.getLogger(__name__)


class AmojoNotFoundError(aiohttp.ClientError):
    """Чат не найден в amojo (4xx) — чат был удалён вручную в AmoCRM."""
    pass


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
VOICE_EXTENSIONS = {".oga", ".ogg", ".mp3", ".m4a", ".wav", ".aac"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}


def _get_amojo_media_type(url: str) -> str:
    """Определить тип медиа для amojo по расширению URL."""
    lower = url.lower().split("?")[0]
    for ext in IMAGE_EXTENSIONS:
        if lower.endswith(ext):
            return "picture"
    for ext in VOICE_EXTENSIONS:
        if lower.endswith(ext):
            return "voice"
    for ext in VIDEO_EXTENSIONS:
        if lower.endswith(ext):
            return "video"
    return "file"


def _get_file_name(url: str) -> str:
    """Получить имя файла из URL (последний сегмент пути, без query-параметров)."""
    clean_url = url.split("?")[0].rstrip("/")
    name = clean_url.rsplit("/", 1)[-1]
    return name or "file"


async def _get_file_size(media_url: str) -> int:
    """
    Получить размер файла по media_url через HEAD-запрос (Content-Length).

    amoCRM требует поле file_size в payload для типов picture/video/file —
    без него в интерфейсе AmoCRM вложение показывается как "0 Байт / Ошибка",
    даже если сам файл по ссылке скачивается нормально (см. переписку по
    диагностике el_oge_diagnostika_bot, 16.09.2026).

    Args:
        media_url: Публичная ссылка на файл (наш /media/... или сторонний хостинг)

    Returns:
        Размер файла в байтах, либо 0 если не удалось определить (best-effort)
    """
    try:
        async with aiohttp.ClientSession() as session:
            async with session.head(
                media_url,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as response:
                content_length = response.headers.get("Content-Length")
                if content_length is not None:
                    return int(content_length)
    except Exception as e:
        logger.warning(
            "Failed to HEAD media_url for file_size: url=%s, error=%s", media_url, e
        )
    return 0


class AmojoClient:
    """Клиент для отправки сообщений в amojo (чаты AmoCRM)."""

    def __init__(self) -> None:
        """Инициализация клиента Amojo."""
        self.base_url = settings.amojo_api_url
        self.scope_id = settings.AMOJO_SCOPE_ID
        self.channel_secret = settings.AMOJO_CHANNEL_SECRET

    def _get_rfc2822_date(self) -> str:
        """
        Получить текущую дату в формате RFC 2822.

        Returns:
            Дата в формате RFC 2822 (например: Mon, 19 Jan 2026 12:00:00 GMT)
        """
        return format_datetime(datetime.now(timezone.utc))

    def _md5_hex_lower(self, data: str) -> str:
        """
        Получить MD5 хеш строки в lowercase hex.

        Args:
            data: Строка для хеширования

        Returns:
            MD5 хеш в lowercase hex
        """
        return hashlib.md5(data.encode("utf-8")).hexdigest().lower()

    def _make_signature(
        self, method: str, body_json: str, content_type: str, date_rfc2822: str, path: str
    ) -> str:
        """
        Создать HMAC-SHA1 подпись для запроса к amojo.

        Args:
            method: HTTP метод (POST)
            body_json: JSON тело запроса
            content_type: Content-Type заголовок
            date_rfc2822: Date заголовок в RFC 2822
            path: Путь API (например: /v2/origin/custom/{scope_id})

        Returns:
            HMAC-SHA1 подпись в lowercase hex
        """
        checksum = self._md5_hex_lower(body_json)

        string_to_sign = "\n".join([method.upper(), checksum, content_type, date_rfc2822, path])

        signature_hex = hmac.new(
            key=self.channel_secret.encode("utf-8"),
            msg=string_to_sign.encode("utf-8"),
            digestmod=hashlib.sha1,
        ).hexdigest().lower()

        return signature_hex

    async def send_incoming_message(
        self,
        conversation_id: str,
        msgid: str,
        sender_id: str,
        sender_name: str,
        text: str,
        silent: bool = True,
        profile_link: str | None = None,
        media_url: str | None = None,
    ) -> dict[str, Any]:
        """
        Отправить входящее сообщение от клиента в amojo.

        Args:
            conversation_id: ID диалога
            msgid: Уникальный ID сообщения
            sender_id: ID отправителя (tg:{platform_id})
            sender_name: Имя отправителя
            text: Текст сообщения
            silent: Не создавать Неразобранное (True = не создавать)
            profile_link: Ссылка на профиль (https://t.me/username)
            media_url: URL медиафайла (картинка/голосовое/видео)

        Returns:
            Ответ от amojo API
        """
        if media_url:
            media_type = _get_amojo_media_type(media_url)
            file_name = _get_file_name(media_url)
            file_size = await _get_file_size(media_url)
            logger.info(
                "Sending media to amojo: conversation=%s, type=%s, url=%s, "
                "file_name=%s, file_size=%d",
                conversation_id,
                media_type,
                media_url,
                file_name,
                file_size,
            )
        else:
            media_type = None
            logger.info(
                "Sending message to amojo: conversation=%s, sender=%s, silent=%s",
                conversation_id,
                sender_id,
                silent,
            )

        now = datetime.now(timezone.utc)
        timestamp = int(now.timestamp())
        msec_timestamp = int(now.timestamp() * 1000)

        if media_url and media_type:
            # file_name и file_size обязательны для picture/video/file (см. докс
            # amoCRM chat-api-reference) — без них AmoCRM показывает вложение
            # как "0 Байт / Ошибка", даже если файл по ссылке скачивается нормально.
            message_block: dict[str, Any] = {
                "type": media_type,
                "text": text,
                "media": media_url,
                "file_name": file_name,
                "file_size": file_size,
            }
        else:
            message_block = {
                "type": "text",
                "text": text,
            }

        # Формируем payload
        payload: dict[str, Any] = {
            "event_type": "new_message",
            "payload": {
                "timestamp": timestamp,
                "msec_timestamp": msec_timestamp,
                "conversation_id": conversation_id,
                "msgid": msgid,
                "silent": silent,
                "sender": {
                    "id": sender_id,
                    "name": sender_name,
                },
                "message": message_block,
            },
        }

        if profile_link:
            payload["payload"]["sender"]["profile_link"] = profile_link

        body_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

        # Формируем заголовки
        content_type = "application/json"
        date_header = self._get_rfc2822_date()
        path = f"/v2/origin/custom/{self.scope_id}"

        signature = self._make_signature("POST", body_json, content_type, date_header, path)

        headers = {
            "Content-Type": content_type,
            "Date": date_header,
            "Content-MD5": self._md5_hex_lower(body_json),
            "X-Signature": signature,
        }

        url = f"{self.base_url}{path}"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url, data=body_json.encode("utf-8"), headers=headers, timeout=aiohttp.ClientTimeout(total=30)
                ) as response:
                    if 400 <= response.status < 500:
                        text = await response.text()
                        logger.error("Amojo API error %s: %s", response.status, text)
                        raise AmojoNotFoundError(f"Amojo API error {response.status}: {text}")

                    if response.status >= 500:
                        text = await response.text()
                        logger.error("Amojo API error %s: %s", response.status, text)
                        raise RetryableAmoCRMError(f"Amojo API error {response.status}: {text}")

                    logger.info("Message sent to amojo: %s", response.status)

                    if response.status == 204:
                        return {}

                    return await response.json()

        except Exception as e:
            logger.error("Error sending message to amojo: %s", e)
            raise
