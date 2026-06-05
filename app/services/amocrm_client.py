"""Клиент для работы с API AmoCRM."""
import asyncio
import hashlib
import hmac
import json
import logging
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from typing import Any

import aiohttp

from app.settings import settings
from app.utils.redis_connection import get_redis
from app.utils.token_manager import get_token_manager

logger = logging.getLogger(__name__)


class RetryableAmoCRMError(aiohttp.ClientError):
    """Временная ошибка AmoCRM (502/503/504) — запрос можно повторить позже."""
    pass


class AmoCRMClient:
    """
    Асинхронный клиент для AmoCRM API v4.

    Включает:
    - Rate limiting (7 запросов/сек через Redis)
    - Long-lived token authentication
    - Retry logic
    - Error handling
    """

    def __init__(self) -> None:
        """Инициализация клиента AmoCRM."""
        self.base_url = settings.amocrm_api_url
        self.access_token = settings.AMO_ACCESS_TOKEN
        self.session: aiohttp.ClientSession | None = None

    async def _ensure_session(self) -> None:
        """Создать aiohttp сессию если её нет."""
        if self.session is None:
            self.session = aiohttp.ClientSession()

    async def _rate_limit(self) -> None:
        """
        Глобальный rate limiter через Redis (7 запросов/сек по умолчанию).

        Использует атомарные операции incr/decr для подсчета запросов.
        """
        redis = get_redis()
        key = "rate_limit:amocrm"

        for _ in range(50):
            count = await redis.incr(key)

            if count == 1:
                await redis.expire(key, 1)

            if count <= settings.AMOCRM_MAX_REQUESTS_PER_SECOND:
                return

            await redis.decr(key)
            await asyncio.sleep(0.1)

        logger.warning("Rate limit timeout exceeded")

    async def _make_request(
        self,
        method: str,
        endpoint: str,
        data: dict[str, Any] | list[dict[str, Any]] | None = None,
        params: dict[str, Any] | None = None,
        retry: int = 3,
    ) -> dict[str, Any]:
        """
        Выполнить HTTP запрос к API AmoCRM с rate limiting и retry логикой.

        Args:
            method: HTTP метод (GET, POST, PATCH)
            endpoint: Endpoint API (например, /contacts)
            data: Данные для отправки (для POST/PATCH)
            params: Query параметры (для GET)
            retry: Количество повторных попыток

        Returns:
            Ответ от API в виде dict

        Raises:
            aiohttp.ClientError: При ошибке API после всех retry
        """
        await self._ensure_session()
        await self._rate_limit()

        url = f"{self.base_url}{endpoint}"

        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }

        last_error = None
        
        for attempt in range(retry):
            try:
                logger.debug(
                    "AmoCRM API request: %s %s (attempt %d/%d)",
                    method,
                    url,
                    attempt + 1,
                    retry,
                )

                if method == "GET":
                    async with self.session.get(
                        url, headers=headers, params=params
                    ) as response:
                        return await self._handle_response(response)
                elif method == "POST":
                    async with self.session.post(
                        url, headers=headers, json=data
                    ) as response:
                        return await self._handle_response(response)
                elif method == "PATCH":
                    async with self.session.patch(
                        url, headers=headers, json=data
                    ) as response:
                        return await self._handle_response(response)
                else:
                    raise ValueError(f"Unsupported HTTP method: {method}")

            except aiohttp.ClientError as e:
                last_error = e
                logger.error(
                    "AmoCRM API error (attempt %d/%d): %s", attempt + 1, retry, e
                )
                # 403 — IP заблокирован, retry бессмысленен
                if "IP blocked 403" in str(e):
                    logger.error("IP blocked by AmoCRM, aborting retries for %s %s", method, endpoint)
                    raise
                if attempt == retry - 1:
                    logger.error("All retry attempts failed for %s %s", method, endpoint)
                    raise
                # Exponential backoff: 2^0=1s, 2^1=2s, 2^2=4s
                delay = 2**attempt
                logger.info("Retrying in %d seconds...", delay)
                await asyncio.sleep(delay)
            except Exception as e:
                logger.error("Unexpected error in AmoCRM request: %s", e, exc_info=True)
                raise

        # Не должны сюда попасть, но для безопасности
        if last_error:
            raise last_error
        return {}

    async def _handle_response(self, response: aiohttp.ClientResponse) -> dict[str, Any]:
        """
        Обработать ответ от AmoCRM API.

        Args:
            response: Ответ от aiohttp

        Returns:
            Parsed JSON response

        Raises:
            aiohttp.ClientError: При ошибке API
        """
        if response.status == 403:
            text = await response.text()
            logger.error("AmoCRM IP blocked (403) — stopping retries: %s", text[:200])
            raise aiohttp.ClientError(f"IP blocked 403: {text[:200]}")

        if response.status == 429:
            logger.warning("AmoCRM rate limit exceeded (429)")
            await asyncio.sleep(1)
            raise aiohttp.ClientError("Rate limit exceeded")

        if response.status in (502, 503, 504):
            text = await response.text()
            logger.error("AmoCRM temporary error %s (retryable): %s", response.status, text[:200])
            raise RetryableAmoCRMError(f"API error {response.status}: {text[:200]}")

        if response.status >= 400:
            text = await response.text()
            logger.error("AmoCRM API error %s: %s", response.status, text)
            raise aiohttp.ClientError(f"API error {response.status}: {text}")

        logger.debug("AmoCRM API response: %s", response.status)

        if response.status == 204:
            return {}

        return await response.json()

    async def close(self) -> None:
        """Закрыть aiohttp сессию."""
        if self.session:
            await self.session.close()
            self.session = None

    def _parse_custom_fields(self, custom_fields_values: list[dict[str, Any]] | None) -> dict[int, Any]:
        """
        Преобразовать custom_fields_values в словарь {field_id: value}.

        Args:
            custom_fields_values: Список кастомных полей из AmoCRM (может быть None)

        Returns:
            Словарь {field_id: значение}
        """
        result: dict[int, Any] = {}

        if not custom_fields_values:
            return result

        for field in custom_fields_values:
            field_id = field.get("field_id")
            values = field.get("values", [])

            if not field_id or not values:
                continue

            if len(values) == 1:
                value_data = values[0]
                if "enum_id" in value_data:
                    result[field_id] = value_data["enum_id"]
                elif "value" in value_data:
                    result[field_id] = value_data["value"]
            else:
                enum_ids = [v.get("enum_id") for v in values if "enum_id" in v]
                if enum_ids:
                    result[field_id] = enum_ids
                else:
                    result[field_id] = [v.get("value") for v in values if "value" in v]

        return result

    async def find_contact_by_tg_id(
        self, tg_id: str, platform_id_field: int | None = None
    ) -> dict[str, Any] | None:
        """
        Найти контакт по полю platform_id.

        Args:
            tg_id: ID клиента (platform_id)
            platform_id_field: ID поля в AmoCRM (по умолчанию FIELD_TG_ID)

        Returns:
            Данные контакта или None если не найден
        """
        field_id = platform_id_field or settings.FIELD_TG_ID
        logger.info("Searching contact by platform_id=%s (field=%s)", tg_id, field_id)

        try:
            response = await self._make_request(
                "GET",
                "/contacts",
                params={
                    "query": tg_id,
                },
            )

            contacts = response.get("_embedded", {}).get("contacts", [])

            for contact in contacts:
                custom_fields = self._parse_custom_fields(contact.get("custom_fields_values"))
                if custom_fields.get(field_id) == tg_id:
                    logger.info("Found contact by platform_id field=%s: %s", field_id, contact["id"])
                    return contact

            logger.info("Contact not found by platform_id=%s (field=%s)", tg_id, field_id)
            return None

        except Exception as e:
            logger.error("Error finding contact by platform_id: %s", e)
            raise

    async def find_contact_by_username(self, username: str) -> dict[str, Any] | None:
        """
        Найти контакт по полю tg_username.

        Args:
            username: Telegram username (без @)

        Returns:
            Данные контакта или None если не найден
        """
        logger.info("Searching contact by username: %s", username)

        try:
            response = await self._make_request(
                "GET",
                "/contacts",
                params={
                    "query": username,
                },
            )

            contacts = response.get("_embedded", {}).get("contacts", [])

            for contact in contacts:
                custom_fields = self._parse_custom_fields(contact.get("custom_fields_values"))
                if custom_fields.get(settings.FIELD_TG_USERNAME) == username:
                    logger.info("Found contact by username: %s", contact["id"])
                    return contact

            logger.info("Contact not found by username: %s", username)
            return None

        except Exception as e:
            logger.error("Error finding contact by username: %s", e)
            raise

    async def create_contact(
        self,
        name: str,
        tg_id: str,
        tg_username: str | None = None,
        email: str | None = None,
        phone: str | None = None,
        platform_id_field: int | None = None,
    ) -> int:
        """
        Создать новый контакт в AmoCRM.

        Args:
            name: Имя контакта
            tg_id: ID клиента (platform_id)
            tg_username: Telegram username (без @)
            email: Email контакта
            phone: Телефон контакта
            platform_id_field: ID поля для хранения platform_id (по умолчанию FIELD_TG_ID)

        Returns:
            ID созданного контакта
        """
        field_id = platform_id_field or settings.FIELD_TG_ID
        logger.info("Creating contact: name=%s, platform_id=%s, field=%s, username=%s",
                    name, tg_id, field_id, tg_username)

        contact_data: dict[str, Any] = {
            "name": name,
            "custom_fields_values": [
                {"field_id": field_id, "values": [{"value": tg_id}]},
            ],
        }

        if tg_username:
            contact_data["custom_fields_values"].append(
                {"field_id": settings.FIELD_TG_USERNAME, "values": [{"value": tg_username}]}
            )

        if email:
            contact_data["custom_fields_values"].append(
                {"field_code": "EMAIL", "values": [{"value": email, "enum_code": "WORK"}]}
            )

        if phone:
            contact_data["custom_fields_values"].append(
                {"field_code": "PHONE", "values": [{"value": phone, "enum_code": "WORK"}]}
            )

        try:
            response = await self._make_request("POST", "/contacts", data=[contact_data])

            contact_id: int = response["_embedded"]["contacts"][0]["id"]
            logger.info("Contact created: %s", contact_id)
            return contact_id

        except Exception as e:
            logger.error("Error creating contact: %s", e)
            raise

    async def update_contact_fields(self, contact_id: int, fields: dict[str, Any]) -> None:
        """
        Обновить поля контакта по field_code (EMAIL, PHONE) или field_name (TG_USERNAME).

        Args:
            contact_id: ID контакта
            fields: Словарь {field_name: value} для обновления
                   Например: {"EMAIL": "test@example.com", "PHONE": "+79991234567", "TG_USERNAME": "username"}
        """
        logger.info("Updating contact %s with fields: %s", contact_id, fields)

        custom_fields_values = []
        for field_name, value in fields.items():
            if value is not None:
                if field_name in ("EMAIL", "PHONE"):
                    custom_fields_values.append(
                        {"field_code": field_name, "values": [{"value": value, "enum_code": "WORK"}]}
                    )
                elif field_name == "TG_USERNAME":
                    custom_fields_values.append(
                        {"field_id": settings.FIELD_TG_USERNAME, "values": [{"value": value}]}
                    )
                else:
                    custom_fields_values.append({"field_code": field_name, "values": [{"value": value}]})

        if not custom_fields_values:
            logger.info("No fields to update")
            return

        contact_data = {
            "id": contact_id,
            "custom_fields_values": custom_fields_values,
        }

        try:
            await self._make_request("PATCH", "/contacts", data=[contact_data])
            logger.info("Contact %s updated successfully", contact_id)

        except Exception as e:
            logger.error("Error updating contact: %s", e)
            raise

    async def update_contact(self, contact_id: int, fields: dict[int, Any]) -> None:
        """
        Обновить поля контакта (без перезаписи старых данных).

        Args:
            contact_id: ID контакта
            fields: Словарь {field_id: value} для обновления

        Note:
            Старые данные приоритетнее новых (не перезаписываем)
        """
        logger.info("Updating contact %s with fields: %s", contact_id, fields)

        custom_fields_values = []
        for field_id, value in fields.items():
            if value is not None:
                custom_fields_values.append({"field_id": field_id, "values": [{"value": value}]})

        if not custom_fields_values:
            logger.info("No fields to update")
            return

        contact_data = {
            "id": contact_id,
            "custom_fields_values": custom_fields_values,
        }

        try:
            await self._make_request("PATCH", "/contacts", data=[contact_data])
            logger.info("Contact %s updated", contact_id)

        except Exception as e:
            logger.error("Error updating contact: %s", e)
            raise

    async def get_contact(self, contact_id: int) -> dict[str, Any] | None:
        """
        Получить контакт по ID.

        Returns:
            Данные контакта, {} если 204 (поглощён через merge), None при ошибке/404
        """
        logger.debug("Fetching contact: %s", contact_id)
        try:
            return await self._make_request("GET", f"/contacts/{contact_id}", retry=1)
        except Exception as e:
            logger.warning("Contact %s not found or error: %s", contact_id, e)
            return None

    async def get_lead(self, lead_id: int) -> dict[str, Any] | None:
        """
        Получить сделку по ID.

        Args:
            lead_id: ID сделки в amoCRM

        Returns:
            Данные сделки или None если не найдена (404, слита, удалена)
        """
        logger.debug("Fetching lead: %s", lead_id)
        try:
            return await self._make_request("GET", f"/leads/{lead_id}", retry=1)
        except Exception as e:
            logger.warning("Lead %s not found or error: %s", lead_id, e)
            return None

    async def check_duplicate_lead(
        self,
        contact_id: int,
        pipeline_id: int | None = None,
    ) -> dict[str, Any] | None:
        """
        Найти открытую сделку контакта в указанной воронке.

        Логика по ТЗ (п. 6.2–6.3):
        - Дубль только если сделка принадлежит контакту И в той же воронке И открытая
        - Сделка в другой воронке — НЕ дубль

        Args:
            contact_id: ID контакта
            pipeline_id: ID воронки для проверки (по умолчанию из settings)

        Returns:
            Данные открытой сделки-дубля или None
        """
        pipeline_id = pipeline_id or settings.AMOCRM_PIPELINE_ID
        logger.info(
            "Searching for duplicate lead: contact=%s, pipeline=%s",
            contact_id,
            pipeline_id,
        )

        try:
            response = await self._make_request(
                "GET",
                "/leads",
                params={
                    "filter[query]": str(contact_id),
                    "with": "contacts",
                    "limit": 250,
                },
            )

            leads = response.get("_embedded", {}).get("leads", [])

            if not leads:
                logger.info("No leads found for contact %s", contact_id)
                return None

            closed_statuses = {settings.STATUS_SUCCESS, settings.STATUS_CLOSED}

            for lead in leads:
                # Проверяем что сделка принадлежит нашему контакту
                contacts = lead.get("_embedded", {}).get("contacts", [])
                contact_ids = [c["id"] for c in contacts]

                if contact_id not in contact_ids:
                    logger.debug(
                        "Lead %s is not linked to contact %s, skipping",
                        lead["id"],
                        contact_id,
                    )
                    continue

                # Проверяем воронку — дубль только в той же воронке
                lead_pipeline_id = lead.get("pipeline_id")
                if lead_pipeline_id != pipeline_id:
                    logger.debug(
                        "Lead %s is in different pipeline %s (need %s), skipping",
                        lead["id"],
                        lead_pipeline_id,
                        pipeline_id,
                    )
                    continue

                # Проверяем что сделка открытая
                status_id = lead.get("status_id")
                if status_id in closed_statuses:
                    logger.debug(
                        "Lead %s is closed (status=%s), skipping", lead["id"], status_id
                    )
                    continue

                logger.info(
                    "Duplicate lead found: id=%s, pipeline=%s, status=%s",
                    lead["id"],
                    lead_pipeline_id,
                    status_id,
                )
                return lead

            logger.info(
                "No duplicate lead found for contact %s in pipeline %s",
                contact_id,
                pipeline_id,
            )
            return None

        except Exception as e:
            logger.error("Error checking duplicate lead: %s", e)
            raise

    async def create_lead(
        self,
        contact_id: int,
        bot_name: str,
        pipeline_id: int | None = None,
        status_id: int | None = None,
        lead_name: str | None = None,
        utm_data: dict | None = None,
    ) -> int:
        """
        Создать сделку в AmoCRM (без проверки дублей).

        Args:
            contact_id: ID контакта
            bot_name: Название бота (пишется в поле FIELD_BOT_NAME сделки)
            pipeline_id: ID воронки (по умолчанию из settings)
            status_id: ID этапа (по умолчанию из settings)
            lead_name: Название сделки (если не передано — формируется из bot_name)
            utm_data: UTM-метки первого касания (utm_source, utm_medium, utm_campaign, utm_term, utm_content)

        Returns:
            ID созданной сделки
        """
        pipeline_id = pipeline_id or settings.AMOCRM_PIPELINE_ID
        status_id = status_id or settings.AMOCRM_STATUS_ID
        name = lead_name or f"{bot_name} - Новая заявка"

        logger.info(
            "Creating lead: contact=%s, bot=%s, pipeline=%s, status=%s, name=%r",
            contact_id,
            bot_name,
            pipeline_id,
            status_id,
            name,
        )

        custom_fields_values = [
            {"field_id": settings.FIELD_BOT_NAME, "values": [{"value": bot_name}]},
        ]

        # UTM-метки первого касания (поля сделки)
        if utm_data:
            utm_field_map = {
                settings.FIELD_UTM_SOURCE:   utm_data.get("utm_source"),
                settings.FIELD_UTM_MEDIUM:   utm_data.get("utm_medium"),
                settings.FIELD_UTM_CAMPAIGN: utm_data.get("utm_campaign"),
                settings.FIELD_UTM_TERM:     utm_data.get("utm_term"),
                settings.FIELD_UTM_CONTENT:  utm_data.get("utm_content"),
            }
            for field_id, value in utm_field_map.items():
                if value:
                    custom_fields_values.append(
                        {"field_id": field_id, "values": [{"value": value}]}
                    )
            logger.info("Adding UTM data to lead: %s", utm_data)

        lead_data: dict[str, Any] = {
            "name": name,
            "pipeline_id": pipeline_id,
            "status_id": status_id,
            "_embedded": {
                "contacts": [{"id": contact_id}],
            },
            "custom_fields_values": custom_fields_values,
        }

        try:
            response = await self._make_request("POST", "/leads", data=[lead_data])

            lead_id: int = response["_embedded"]["leads"][0]["id"]
            logger.info("Lead created: %s", lead_id)
            return lead_id

        except Exception as e:
            logger.error("Error creating lead: %s", e)
            raise

    async def move_lead(self, lead_id: int, pipeline_id: int, status_id: int) -> None:
        """
        Переместить сделку в другую воронку / этап.

        Args:
            lead_id: ID сделки
            pipeline_id: ID целевой воронки
            status_id: ID целевого этапа
        """
        logger.info("Moving lead %s to pipeline=%s, status=%s", lead_id, pipeline_id, status_id)
        try:
            await self._make_request(
                "PATCH",
                "/leads",
                data=[{"id": lead_id, "pipeline_id": pipeline_id, "status_id": status_id}],
            )
            logger.info("Lead %s moved to pipeline=%s, status=%s", lead_id, pipeline_id, status_id)
        except Exception as e:
            logger.warning("Failed to move lead %s: %s", lead_id, e)

    async def update_lead_enum(self, lead_id: int, field_id: int, enum_id: int) -> None:
        """
        Обновить select-поле сделки по enum_id.

        Args:
            lead_id: ID сделки
            field_id: ID поля типа select
            enum_id: ID варианта выбора
        """
        logger.info("Updating lead %s: field=%s enum=%s", lead_id, field_id, enum_id)
        try:
            await self._make_request(
                "PATCH",
                "/leads",
                data=[{
                    "id": lead_id,
                    "custom_fields_values": [
                        {"field_id": field_id, "values": [{"enum_id": enum_id}]},
                    ],
                }],
            )
            logger.info("Lead %s field=%s updated to enum=%s", lead_id, field_id, enum_id)
        except Exception as e:
            logger.warning("Failed to update lead %s field=%s: %s", lead_id, field_id, e)

    async def update_lead(self, lead_id: int, fields: dict[int, Any]) -> None:
        """
        Обновить кастомные поля сделки.

        Args:
            lead_id: ID сделки
            fields: Словарь {field_id: value} для обновления
        """
        logger.info("Updating lead %s with fields: %s", lead_id, fields)

        custom_fields_values = []
        for field_id, value in fields.items():
            if value is not None:
                custom_fields_values.append({"field_id": field_id, "values": [{"value": value}]})

        if not custom_fields_values:
            logger.info("No fields to update")
            return

        lead_data = {
            "id": lead_id,
            "custom_fields_values": custom_fields_values,
        }

        try:
            await self._make_request("PATCH", "/leads", data=[lead_data])
            logger.info("Lead %s updated", lead_id)

        except Exception as e:
            logger.error("Error updating lead: %s", e)
            raise

    async def create_chat_in_amojo(
        self,
        conversation_id: str,
        user_id: str,
        user_name: str,
        profile_link: str | None = None,
    ) -> str:
        """
        Создать чат в amojo через POST /v2/origin/custom/{scope_id}/chats.

        Этот метод создает чат в amojo БЕЗ привязки к контакту.
        Для привязки используй link_chat_to_contact().

        Args:
            conversation_id: Уникальный ID диалога (например: "tg:111111:BotName")
            user_id: ID пользователя (например: "tg:111111")
            user_name: Имя пользователя
            profile_link: Ссылка на профиль (опционально)

        Returns:
            chat_id созданного чата (UUID)
        """
        logger.info("Creating chat in amojo: conversation_id=%s", conversation_id)

        path = f"/v2/origin/custom/{settings.AMOJO_SCOPE_ID}/chats"
        url = f"{settings.amojo_api_url}{path}"

        body = {
            "conversation_id": conversation_id,
            "user": {
                "id": user_id,
                "name": user_name,
            },
        }

        if profile_link:
            body["user"]["profile_link"] = profile_link

        # Сериализуем JSON стабильно (без пробелов)
        request_body = json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode(
            "utf-8"
        )

        # Формируем подпись для amojo
        content_type = "application/json"
        content_md5 = hashlib.md5(request_body).hexdigest().lower()
        date_header = format_datetime(datetime.now(timezone.utc))

        string_to_sign = "\n".join(
            [
                "POST",
                content_md5,
                content_type,
                date_header,
                path,
            ]
        )

        x_signature = hmac.new(
            settings.AMOJO_CHANNEL_SECRET.encode("utf-8"),
            string_to_sign.encode("utf-8"),
            hashlib.sha1,
        ).hexdigest().lower()

        headers = {
            "Content-Type": content_type,
            "Date": date_header,
            "Content-MD5": content_md5,
            "X-Signature": x_signature,
        }

        try:
            await self._ensure_session()

            async with self.session.post(
                url, data=request_body, headers=headers, timeout=aiohttp.ClientTimeout(total=30)
            ) as response:
                if response.status >= 400:
                    text = await response.text()
                    logger.error("Amojo API error %s: %s", response.status, text)
                    raise aiohttp.ClientError(f"Amojo API error {response.status}: {text}")

                # amojo может вернуть пустой ответ (204) или JSON
                if response.status == 204:
                    # Нет тела ответа - используем conversation_id как chat_id
                    logger.info(
                        "Chat created in amojo (204 No Content), using conversation_id as chat_id"
                    )
                    return conversation_id

                result = await response.json()
                logger.debug("Amojo response: %s", result)

                # Пытаемся получить chat_id из разных полей
                chat_id = result.get("chat_id") or result.get("id") or result.get("conversation_id")

                if not chat_id:
                    # Если chat_id нет в ответе - используем conversation_id
                    logger.warning(
                        "No chat_id in amojo response, using conversation_id: %s", result
                    )
                    chat_id = conversation_id

                logger.info("Chat created in amojo: chat_id=%s", chat_id)
                return chat_id

        except Exception as e:
            logger.error("Error creating chat in amojo: %s", e)
            raise

    async def link_chat_to_contact(
        self, contact_id: int, chat_id: str, scope_id: str | None = None
    ) -> None:
        """
        Привязать чат к контакту через POST /api/v4/contacts/chats.

        ВАЖНО: Требует OAuth2 токен (access_token), а не long-lived токен!

        Args:
            contact_id: ID контакта в AmoCRM
            chat_id: ID чата из amojo (UUID)
            scope_id: Scope ID канала (по умолчанию из settings)
        """
        scope_id = scope_id or settings.AMOJO_SCOPE_ID

        logger.info(
            "Linking chat to contact: contact_id=%s, chat_id=%s, scope_id=%s",
            contact_id,
            chat_id,
            scope_id,
        )

        # Получаем OAuth2 токен через TokenManager
        token_manager = get_token_manager()
        oauth_token = await token_manager.get_access_token()

        url = f"{settings.amocrm_api_url}/contacts/chats"

        headers = {
            "Authorization": f"Bearer {oauth_token}",
            "Content-Type": "application/json",
        }

        payload = [
            {
                "contact_id": contact_id,
                "chat_id": chat_id,
                "scope_id": scope_id,
            }
        ]

        try:
            await self._ensure_session()
            await self._rate_limit()

            async with self.session.post(
                url, headers=headers, json=payload, timeout=aiohttp.ClientTimeout(total=30)
            ) as response:
                if response.status >= 400:
                    text = await response.text()
                    logger.error(
                        "AmoCRM API error linking chat %s: %s", response.status, text
                    )

                    # Если токен истек (401) - пробуем обновить и повторить
                    if response.status == 401:
                        logger.info("OAuth token expired, refreshing...")
                        oauth_token = await token_manager.refresh_access_token()

                        headers["Authorization"] = f"Bearer {oauth_token}"

                        async with self.session.post(
                            url,
                            headers=headers,
                            json=payload,
                            timeout=aiohttp.ClientTimeout(total=30),
                        ) as retry_response:
                            if retry_response.status >= 400:
                                retry_text = await retry_response.text()
                                logger.error(
                                    "AmoCRM API error after token refresh %s: %s",
                                    retry_response.status,
                                    retry_text,
                                )
                                raise aiohttp.ClientError(
                                    f"API error {retry_response.status}: {retry_text}"
                                )

                            logger.info("Chat %s linked to contact %s", chat_id, contact_id)
                            return

                    raise aiohttp.ClientError(f"API error {response.status}: {text}")

                logger.info("Chat %s linked to contact %s", chat_id, contact_id)

        except Exception as e:
            logger.error("Error linking chat to contact: %s", e)
            raise

    async def add_lead_tag(self, lead_id: int, tag_id: int) -> None:
        """
        Добавить тег к сделке без удаления существующих тегов.

        Args:
            lead_id: ID сделки
            tag_id: ID тега в AmoCRM (постоянный, создаётся один раз вручную)
        """
        logger.info("Adding tag id=%s to lead %s", tag_id, lead_id)
        try:
            # Получить текущие теги сделки
            lead = await self._make_request(
                "GET", f"/leads/{lead_id}", params={"with": "tags"}
            )
            existing_tags: list[dict] = lead.get("_embedded", {}).get("tags", [])

            existing_ids = {t["id"] for t in existing_tags if t.get("id")}
            if tag_id in existing_ids:
                logger.debug("Tag id=%s already exists on lead %s, skipping", tag_id, lead_id)
                return

            # PATCH: существующие теги + новый, все по ID
            tags_payload = [{"id": t["id"]} for t in existing_tags if t.get("id")]
            tags_payload.append({"id": tag_id})

            await self._make_request(
                "PATCH",
                "/leads",
                data=[{"id": lead_id, "_embedded": {"tags": tags_payload}}],
            )
            logger.info("Tag id=%s added to lead %s", tag_id, lead_id)
        except Exception as e:
            logger.warning("Failed to add tag id=%s to lead %s: %s", tag_id, lead_id, e)

    async def create_task(
        self,
        lead_id: int,
        text: str,
        task_type_id: int = 1,
        complete_till_days: int = 1
    ) -> int:
        """
        Создать задачу для ответственного менеджера сделки.
        
        Args:
            lead_id: ID сделки
            text: Текст задачи
            task_type_id: Тип задачи (1 = Звонок, по умолчанию)
            complete_till_days: Срок выполнения в днях (по умолчанию 1)
            
        Returns:
            ID созданной задачи
            
        Raises:
            ValueError: Если у сделки нет ответственного
        """
        logger.info("Creating task for lead %d: '%s'", lead_id, text)
        
        try:
            # Получаем сделку, чтобы узнать responsible_user_id
            lead_response = await self._make_request(
                "GET",
                f"/leads/{lead_id}"
            )
            
            responsible_user_id = lead_response.get("responsible_user_id")
            
            if not responsible_user_id:
                logger.warning("Lead %d has no responsible_user_id, cannot create task", lead_id)
                raise ValueError(f"Lead {lead_id} has no responsible_user_id")
            
            logger.info("Lead manager: user_id=%d", responsible_user_id)
            
            # Вычисляем срок выполнения (timestamp)
            complete_till = int((datetime.now() + timedelta(days=complete_till_days)).timestamp())
            
            # Создаем задачу
            task_data = {
                "text": text,
                "complete_till": complete_till,
                "entity_id": lead_id,
                "entity_type": "leads",
                "responsible_user_id": responsible_user_id,
                "task_type_id": task_type_id,
            }
            
            response = await self._make_request(
                "POST",
                "/tasks",
                data=[task_data]
            )
            
            task_id = response["_embedded"]["tasks"][0]["id"]
            logger.info("Task created: id=%d for user %d", task_id, responsible_user_id)
            
            return task_id
            
        except Exception as e:
            logger.error("Error creating task: %s", e)
            raise

