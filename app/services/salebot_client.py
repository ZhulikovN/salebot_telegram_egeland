"""Клиент для работы с Salebot API."""
import asyncio
import json
import logging
from typing import Any

import aiohttp

from app.settings import settings

logger = logging.getLogger(__name__)


class RetryableSalebotError(aiohttp.ClientError):
    """Временная ошибка Salebot (502/503/504) — запрос можно повторить."""
    pass


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
        retry: int = 3,
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

        Salebot периодически отдаёт 502/503/504 — это временные сбои на их
        стороне. Без ретрая такое сообщение терялось бы безвозвратно, поэтому
        эти ошибки повторяем с экспоненциальной задержкой. Ошибки вида 4xx
        (например 404 client_not_found) не ретраим — они не временные, повтор
        их не исправит.

        Args:
            client_id: salebot_client_id из БД (это client.id из webhook, НЕ platform_id!)
            message: текст сообщения (необязателен если передан attachment_url)
            attachment_url: URL медиафайла (опционально)
            attachment_type: тип вложения — image, audio, video, file (обязателен если передан attachment_url)
            retry: количество попыток при временных ошибках (502/503/504)

        Returns:
            Ответ от Salebot API

        Raises:
            aiohttp.ClientError: При ошибке запроса после всех попыток
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

        last_error: Exception | None = None

        for attempt in range(retry):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        url, json=payload, timeout=aiohttp.ClientTimeout(total=30)
                    ) as response:
                        text = await response.text()

                        if response.status in (502, 503, 504):
                            raise RetryableSalebotError(
                                f"Salebot API error {response.status}: {text[:200]}"
                            )

                        if response.status >= 400:
                            logger.error("Salebot API error %s: %s", response.status, text)
                            raise aiohttp.ClientError(
                                f"Salebot API error {response.status}: {text}"
                            )

                        logger.info("Message sent to Salebot: %s", response.status)
                        logger.info("Salebot response body: %s", text)

                        try:
                            return json.loads(text)
                        except json.JSONDecodeError:
                            return {"status": "ok", "response": text}

            except RetryableSalebotError as e:
                last_error = e
                logger.error(
                    "Salebot temporary error sending message (attempt %d/%d): client_id=%s, %s",
                    attempt + 1,
                    retry,
                    client_id,
                    e,
                )
                if attempt < retry - 1:
                    delay = 2**attempt
                    logger.info("Retrying Salebot send in %d seconds...", delay)
                    await asyncio.sleep(delay)
                    continue
                logger.error(
                    "All %d retry attempts to Salebot failed: client_id=%s", retry, client_id
                )
                raise
            except Exception as e:
                logger.error("Error sending message to Salebot (non-retryable): %s", e)
                raise

        if last_error:
            raise last_error
        return {}

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

    async def get_variables(self, client_id: int) -> dict[str, Any]:
        """
        Получить текущие переменные клиента из Salebot.

        GET https://chatter.salebot.pro/api/{API_KEY}/get_variables?client_id={CLIENT_ID}

        Возвращает актуальный снимок переменных на момент запроса (в отличие от
        данных вебхука, которые фиксируют состояние на момент входящего сообщения).
        Используется для дозаполнения UTM-меток, которые Salebot мог выставить
        позже первого сообщения.

        Требует у API-ключа право "Разрешение на чтение информации о клиентах".

        Args:
            client_id: salebot_client_id (client.id из вебхука, НЕ platform_id!)

        Returns:
            Словарь переменных клиента (плоский JSON, ключи — имена переменных)

        Raises:
            aiohttp.ClientError: При ошибке запроса
        """
        logger.debug("Getting Salebot variables: client_id=%s", client_id)

        url = f"{self.base_url}/get_variables"
        params = {"client_id": client_id}

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, params=params, timeout=aiohttp.ClientTimeout(total=15)
                ) as response:
                    text = await response.text()

                    if response.status >= 400:
                        logger.warning(
                            "Salebot get_variables error %s: %s", response.status, text[:200]
                        )
                        raise aiohttp.ClientError(
                            f"Salebot API error {response.status}: {text}"
                        )

                    try:
                        return json.loads(text)
                    except json.JSONDecodeError:
                        logger.warning("Salebot get_variables returned non-JSON: %s", text[:200])
                        return {}

        except Exception as e:
            logger.warning("Error getting Salebot variables (client_id=%s): %s", client_id, e)
            raise

    async def save_variables(
        self,
        client_id: int,
        variables: dict[str, str],
    ) -> None:
        """
        Сохранить переменные в профиль клиента Salebot.

        POST https://chatter.salebot.pro/api/{API_KEY}/save_variables
        Body: {"client_id": 12345, "variables": {"amo_lead_id": "40547727"}}

        Best-effort: ошибки логируются как warning, не прерывают основной поток.

        Args:
            client_id: salebot_client_id (client.id из webhook, НЕ platform_id!)
            variables: словарь переменных для сохранения
        """
        logger.info("Saving Salebot variables: client_id=%s, keys=%s", client_id, list(variables))

        url = f"{self.base_url}/save_variables"
        payload = {
            "client_id": client_id,
            "variables": variables,
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url, json=payload, timeout=aiohttp.ClientTimeout(total=15)
                ) as response:
                    text = await response.text()

                    if response.status >= 400:
                        logger.warning(
                            "Salebot save_variables error %s: %s", response.status, text
                        )
                        return

                    logger.info("Salebot variables saved: %s", response.status)

        except Exception as e:
            logger.warning("Error saving Salebot variables (non-critical): %s", e)

    async def close(self) -> None:
        """Закрыть соединения (для совместимости, SalebotClient не держит постоянных соединений)."""
        pass
