"""Клиент для работы с Salebot API."""
import logging
from typing import Any

import aiohttp

from app.settings import settings

logger = logging.getLogger(__name__)


class SalebotClient:
    """Клиент для отправки сообщений через Salebot API."""

    def __init__(self) -> None:
        """Инициализация клиента Salebot."""
        self.base_url = settings.salebot_api_url
        self.project_id = settings.SALEBOT_PROJECT_ID

    async def send_message(
        self,
        client_id: int,
        message: str,
        attachment_url: str | None = None,
        attachment_type: str | None = None,
    ) -> dict[str, Any]:
        """
        Отправить сообщение клиенту через Salebot API.

        POST https://chatter.salebot.pro/api/{API_KEY}/message

        Body:
        {
            "client_id": "836058546",
            "project_id": 424757,
            "message": "Текст сообщения",
            "attachment_url": "https://...",   # опционально
            "attachment_type": "image/audio/video/file"  # обязателен если передан attachment_url
        }

        Args:
            client_id: salebot_client_id из БД (это client.id из webhook, НЕ platform_id!)
            message: текст сообщения (необязателен если передан attachment_url)
            attachment_url: URL медиафайла (опционально)
            attachment_type: тип вложения — image, audio, video, file (обязателен если передан attachment_url)

        Returns:
            Ответ от Salebot API

        Raises:
            aiohttp.ClientError: При ошибке запроса
        """
        if attachment_url:
            logger.info(
                "Sending media to Salebot: client_id=%s, type=%s, url=%s",
                client_id,
                attachment_type,
                attachment_url,
            )
        else:
            logger.info("Sending message to Salebot: client_id=%s", client_id)

        url = f"{self.base_url}/message"

        payload: dict[str, Any] = {
            "client_id": str(client_id),
            "project_id": self.project_id,
            "message": message,
        }

        if attachment_url and attachment_type:
            payload["attachment_url"] = attachment_url
            payload["attachment_type"] = attachment_type

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url, json=payload, timeout=aiohttp.ClientTimeout(total=30)
                ) as response:
                    text = await response.text()

                    if response.status >= 400:
                        logger.error("Salebot API error %s: %s", response.status, text)
                        raise aiohttp.ClientError(
                            f"Salebot API error {response.status}: {text}"
                        )

                    logger.info("Message sent to Salebot: %s", response.status)
                    logger.debug("Salebot response: %s", text)

                    try:
                        import json
                        result = json.loads(text)
                        return result
                    except json.JSONDecodeError:
                        return {"status": "ok", "response": text}

        except Exception as e:
            logger.error("Error sending message to Salebot: %s", e)
            raise

    async def get_history(self, client_id: int) -> dict[str, Any]:
        """
        Получить историю сообщений клиента из Salebot.

        GET https://chatter.salebot.pro/api/{API_KEY}/get_history?client_id={CLIENT_ID}

        Args:
            client_id: salebot_client_id (это client.id из webhook)

        Returns:
            Словарь с историей сообщений:
            {
                "messages": [
                    {
                        "text": "...",
                        "sender": "client" | "manager",
                        "timestamp": 1234567890,
                        "client_name": "...",
                        "username": "..."
                    },
                    ...
                ]
            }

        Raises:
            aiohttp.ClientError: При ошибке запроса
        """
        logger.info("Getting Salebot history: client_id=%s", client_id)

        url = f"{self.base_url}/get_history"
        params = {"client_id": client_id}

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, params=params, timeout=aiohttp.ClientTimeout(total=30)
                ) as response:
                    text = await response.text()

                    if response.status >= 400:
                        logger.error("Salebot API error %s: %s", response.status, text)
                        raise aiohttp.ClientError(
                            f"Salebot API error {response.status}: {text}"
                        )

                    logger.info("Salebot history received: %s", response.status)
                    logger.debug("Salebot response: %s", text[:200])

                    try:
                        import json
                        result = json.loads(text)
                        return result
                    except json.JSONDecodeError:
                        return {"error": "Invalid JSON response", "raw": text}

        except Exception as e:
            logger.error("Error getting Salebot history: %s", e)
            raise

    async def load_client(self, platform_id: int, group_id: str) -> dict[str, Any]:
        """
        Загрузить клиента из Salebot по platform_id (Telegram ID).

        POST https://chatter.salebot.pro/api/{API_KEY}/load_clients

        Body:
        [
            {
                "platform_id": 6253651200,
                "group_id": "ElAuthBot",
                "client_type": 1
            }
        ]

        Args:
            platform_id: Telegram ID клиента
            group_id: Название бота (bot_name)

        Returns:
            Словарь с данными клиента:
            {
                "status": "success",
                "items": [
                    {
                        "platform_id": 6253651200,
                        "group_id": "ElAuthBot",
                        "client_type": 1,
                        "status": "success",
                        "id": 836058546  # salebot_client_id
                    }
                ]
            }

        Raises:
            aiohttp.ClientError: При ошибке запроса
        """
        logger.info("Loading Salebot client: platform_id=%s, group_id=%s", platform_id, group_id)

        url = f"{self.base_url}/load_clients"
        payload = [
            {
                "platform_id": platform_id,
                "group_id": group_id,
                "client_type": 1,  # 1 = Telegram
            }
        ]

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url, json=payload, timeout=aiohttp.ClientTimeout(total=30)
                ) as response:
                    text = await response.text()

                    if response.status >= 400:
                        logger.error("Salebot API error %s: %s", response.status, text)
                        raise aiohttp.ClientError(
                            f"Salebot API error {response.status}: {text}"
                        )

                    logger.info("Salebot client loaded: %s", response.status)
                    logger.debug("Salebot response: %s", text)

                    try:
                        import json
                        result = json.loads(text)
                        return result
                    except json.JSONDecodeError:
                        return {"error": "Invalid JSON response", "raw": text}

        except Exception as e:
            logger.error("Error loading Salebot client: %s", e)
            raise

    async def close(self) -> None:
        """Закрыть соединения (для совместимости, SalebotClient не держит постоянных соединений)."""
        pass
