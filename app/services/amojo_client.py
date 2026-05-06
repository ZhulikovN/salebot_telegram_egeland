"""Клиент для работы с amojo API (чаты AmoCRM)."""
import hashlib
import hmac
import logging
from datetime import datetime, timezone
from email.utils import format_datetime
from typing import Any
import json

import aiohttp

from app.settings import settings

logger = logging.getLogger(__name__)


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
    ) -> dict[str, Any]:
        """
        Отправить входящее сообщение от клиента в amojo.

        Args:
            conversation_id: ID диалога (salebot:{project_id}:{platform_id})
            msgid: Уникальный ID сообщения (salebot:{project_id}:{message_id})
            sender_id: ID отправителя (tg:{platform_id})
            sender_name: Имя отправителя
            text: Текст сообщения
            silent: Не создавать Неразобранное (True = не создавать)
            profile_link: Ссылка на профиль (https://t.me/username)

        Returns:
            Ответ от amojo API
        """
        logger.info(
            "Sending message to amojo: conversation=%s, sender=%s, silent=%s",
            conversation_id,
            sender_id,
            silent,
        )

        now = datetime.now(timezone.utc)
        timestamp = int(now.timestamp())
        msec_timestamp = int(now.timestamp() * 1000)

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
                "message": {
                    "type": "text",
                    "text": text,
                },
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
                    if response.status >= 400:
                        text = await response.text()
                        logger.error("Amojo API error %s: %s", response.status, text)
                        raise aiohttp.ClientError(f"Amojo API error {response.status}: {text}")

                    logger.info("Message sent to amojo: %s", response.status)

                    if response.status == 204:
                        return {}

                    return await response.json()

        except Exception as e:
            logger.error("Error sending message to amojo: %s", e)
            raise
