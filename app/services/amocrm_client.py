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


class LeadNotFoundError(aiohttp.ClientError):
    """
    Сделка не найдена в AmoCRM (удалена/слита) — ПОСТОЯННАЯ ошибка.

    В отличие от RetryableAmoCRMError, повтор запроса никогда не поможет.
    Используется только fill_missing_utm_fields(raise_on_not_found=True)
    для utm_backfill_worker — чтобы он мог сразу прекратить бесконечные
    попытки раз в 5 минут вместо тихого проглатывания ошибки.
    """
    pass


class RateLimitAmoCRMError(aiohttp.ClientError):
    """
    429 от AmoCRM — превышен лимит запросов.

    Это не транзиентная сетевая ошибка вроде 502/503: причиной может быть не
    только наш собственный лимит (7 req/sec на интеграцию), но и общий лимит
    аккаунта (50 req/sec на все интеграции сразу — например, при пиковой
    нагрузке в вебинар). Такому лимиту нужно заметно больше времени на
    восстановление, поэтому у него отдельный, более долгий backoff и больше
    попыток (см. RATE_LIMIT_BACKOFF_BASE_SEC / RATE_LIMIT_MAX_RETRIES ниже).
    """
    pass


# Backoff при 429: фиксированный длинный интервал с небольшим приростом
# (5с, 7с, 9с, 11с, 13с), а не экспоненциальный как для 502/503 — чтобы
# реально пережить пиковую нагрузку на аккаунтный лимit 50 req/sec.
RATE_LIMIT_BACKOFF_BASE_SEC = 5
# Минимальное количество попыток специально для 429, даже если вызывающий
# код передал retry поменьше (по умолчанию retry=3 — этого мало для 429).
RATE_LIMIT_MAX_RETRIES = 5


class AmoCRMClient:
    """
    Асинхронный клиент для AmoCRM API v4.

    Включает:
    - Rate limiting (7 запросов/сек через Redis)
    - Long-lived token authentication
    - Retry logic
    - Error handling
    """

    def __init__(
        self,
        access_token: str | None = None,
        rate_limit_key: str = "rate_limit:amocrm",
        max_requests_per_second: int | None = None,
    ) -> None:
        """
        Инициализация клиента AmoCRM.

        Args:
            access_token: Bearer-токен. Если не передан — берётся settings.AMO_ACCESS_TOKEN
                (токен основной интеграции).
            rate_limit_key: Redis-ключ для rate limiter. Разным интеграциям (свой client_id
                в AmoCRM) нужен свой ключ, иначе они будут делить один общий счётчик 7 req/sec,
                даже если физически у них разные токены.
            max_requests_per_second: лимит для этого клиента. Если не передан —
                settings.AMOCRM_MAX_REQUESTS_PER_SECOND.
        """
        self.base_url = settings.amocrm_api_url
        self.access_token = access_token or settings.AMO_ACCESS_TOKEN
        self.rate_limit_key = rate_limit_key
        self.max_requests_per_second = (
            max_requests_per_second
            if max_requests_per_second is not None
            else settings.AMOCRM_MAX_REQUESTS_PER_SECOND
        )
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
        key = self.rate_limit_key

        for _ in range(50):
            count = await redis.incr(key)

            if count == 1:
                await redis.expire(key, 1)

            if count <= self.max_requests_per_second:
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
        attempt = 0
        # У 429 свой минимум попыток — не обрезаем его значением retry,
        # переданным вызывающим кодом, если оно меньше RATE_LIMIT_MAX_RETRIES.
        max_attempts = retry

        while attempt < max_attempts:
            try:
                logger.debug(
                    "AmoCRM API request: %s %s (attempt %d/%d)",
                    method,
                    url,
                    attempt + 1,
                    max_attempts,
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

            except LeadNotFoundError:
                # Сделка/контакт не найдены (слиты/удалены) — ПОСТОЯННАЯ
                # ошибка, повтор никогда не поможет. Не ретраим (экономим
                # ~3-7 секунд и три лишних HTTP-запроса), сразу поднимаем
                # вызывающему коду — WARNING уже залогирован в _handle_response.
                raise
            except aiohttp.ClientError as e:
                last_error = e
                logger.error(
                    "AmoCRM API error (attempt %d/%d): %s", attempt + 1, max_attempts, e
                )
                # 403 — IP заблокирован, retry бессмысленен
                if "IP blocked 403" in str(e):
                    logger.error("IP blocked by AmoCRM, aborting retries for %s %s", method, endpoint)
                    raise

                is_rate_limited = isinstance(e, RateLimitAmoCRMError)
                if is_rate_limited:
                    max_attempts = max(max_attempts, RATE_LIMIT_MAX_RETRIES)

                if attempt == max_attempts - 1:
                    logger.error("All retry attempts failed for %s %s", method, endpoint)
                    raise

                if is_rate_limited:
                    # Фиксированный длинный backoff вместо экспоненциального:
                    # 5с, 7с, 9с, 11с, 13с — даём аккаунтному лимиту 50 req/sec
                    # реально освободиться, а не долбим его каждую секунду.
                    delay = RATE_LIMIT_BACKOFF_BASE_SEC + attempt * 2
                else:
                    # Exponential backoff для транзиентных 502/503/сети: 1с, 2с, 4с
                    delay = 2**attempt

                logger.info("Retrying in %d seconds...", delay)
                await asyncio.sleep(delay)
                attempt += 1
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
            # Пауза здесь не нужна — её теперь считает _make_request (более
            # долгий backoff специально для 429, см. RATE_LIMIT_BACKOFF_BASE_SEC).
            raise RateLimitAmoCRMError("Rate limit exceeded (429)")

        if response.status in (502, 503, 504):
            text = await response.text()
            logger.error("AmoCRM temporary error %s (retryable): %s", response.status, text[:200])
            raise RetryableAmoCRMError(f"API error {response.status}: {text[:200]}")

        if response.status == 400:
            text = await response.text()
            if "not found" in text.lower():
                # Сделка/контакт удалены или слиты NOVA — ПОСТОЯННАЯ ошибка
                # (повтор никогда не поможет), и почти всегда ожидаемая —
                # у вызывающего кода почти везде есть recovery через
                # check_duplicate_lead (fill_missing_utm_fields,
                # conversation_manager._resolve_lead_after_merge). WARNING,
                # а не ERROR — чтобы не засорять лог там, где всё в итоге
                # штатно восстанавливается.
                logger.warning("AmoCRM entity not found (400): %s", text)
                raise LeadNotFoundError(f"API error {response.status}: {text}")
            logger.error("AmoCRM API error %s: %s", response.status, text)
            raise aiohttp.ClientError(f"API error {response.status}: {text}")

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
        only_open: bool = True,
    ) -> dict[str, Any] | None:
        """
        Найти сделку контакта.

        Логика по ТЗ (п. 6.2–6.3) для only_open=True (по умолчанию, анти-дубликат
        перед созданием новой сделки — см. _create_new_conversation/_reopen_conversation):
        - Дубль только если сделка принадлежит контакту И открытая
        - Если pipeline_id передан — ищем только в этой воронке
        - Если pipeline_id не передан — ищем по всем воронкам (сценарий после слияния контактов NOVA)

        only_open=False — режим "поиск сделки-наследника после слияния NOVA"
        (см. conversation_manager._resolve_lead_after_merge, fill_missing_utm_fields,
        worker.py LEAD_EVENT): здесь задача не "избежать дубля", а "куда делась
        сделка" — статус (открыта/закрыта) к этой задаче не имеет отношения,
        наследник вполне может быть уже закрыт к моменту, когда мы спохватились
        его искать. В этом режиме статус не фильтруется, но если среди
        подходящих по контакту/воронке сделок есть хоть одна открытая —
        предпочитаем её; если открытых нет совсем — берём "самую свежую" по
        updated_at (а если он не возвращался — по id, т.к. id в amoCRM растут
        монотонно по времени создания).

        Args:
            contact_id: ID контакта
            pipeline_id: ID воронки для проверки. Если None — поиск по всем воронкам.
            only_open: True (по умолчанию) — отбрасывать закрытые сделки (анти-дубликат,
                поведение не менялось). False — не фильтровать по статусу, искать
                сделку-наследника после слияния (см. выше).

        Returns:
            Данные сделки или None
        """
        logger.info(
            "Searching for duplicate lead: contact=%s, pipeline=%s, only_open=%s",
            contact_id,
            pipeline_id if pipeline_id is not None else "all",
            only_open,
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

            candidates: list[dict[str, Any]] = []

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

                # Проверяем воронку — только если pipeline_id задан явно
                lead_pipeline_id = lead.get("pipeline_id")
                if pipeline_id is not None and lead_pipeline_id != pipeline_id:
                    logger.debug(
                        "Lead %s is in different pipeline %s (need %s), skipping",
                        lead["id"],
                        lead_pipeline_id,
                        pipeline_id,
                    )
                    continue

                status_id = lead.get("status_id")
                is_closed = status_id in closed_statuses

                if only_open and is_closed:
                    logger.debug(
                        "Lead %s is closed (status=%s), skipping", lead["id"], status_id
                    )
                    continue

                candidates.append(lead)

            if not candidates:
                logger.info(
                    "No duplicate lead found for contact %s in pipeline %s",
                    contact_id,
                    pipeline_id if pipeline_id is not None else "all",
                )
                return None

            if only_open:
                # Как и раньше: первая подходящая открытая сделка в порядке ответа API.
                chosen = candidates[0]
            else:
                open_candidates = [
                    lead for lead in candidates
                    if lead.get("status_id") not in closed_statuses
                ]
                if open_candidates:
                    chosen = open_candidates[0]
                else:
                    # Все кандидаты закрыты — берём самую "свежую" (вероятнее
                    # всего это и есть сделка-наследник в цепочке слияний).
                    chosen = max(
                        candidates,
                        key=lambda lead: (lead.get("updated_at") or 0, lead.get("id") or 0),
                    )
                    logger.info(
                        "check_duplicate_lead: only_open=False, all %d candidate(s) closed, "
                        "picked most recent: id=%s, updated_at=%s",
                        len(candidates),
                        chosen.get("id"),
                        chosen.get("updated_at"),
                    )

            logger.info(
                "Duplicate lead found: id=%s, pipeline=%s, status=%s",
                chosen["id"],
                chosen.get("pipeline_id"),
                chosen.get("status_id"),
            )
            return chosen

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
        tag_ids: list[int] | None = None,
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
            tag_ids: ID тегов, которые нужно поставить сразу при создании (через _embedded.tags,
                без отдельных запросов add_lead_tag после)

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

        embedded: dict[str, Any] = {
            "contacts": [{"id": contact_id}],
        }
        if tag_ids:
            embedded["tags"] = [{"id": tag_id} for tag_id in tag_ids]

        lead_data: dict[str, Any] = {
            "name": name,
            "pipeline_id": pipeline_id,
            "status_id": status_id,
            "_embedded": embedded,
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

    async def move_lead(
        self,
        lead_id: int,
        pipeline_id: int,
        status_id: int,
        raise_on_not_found: bool = False,
    ) -> None:
        """
        Переместить сделку в другую воронку / этап.

        Args:
            lead_id: ID сделки
            pipeline_id: ID целевой воронки
            status_id: ID целевого этапа
            raise_on_not_found: Если True — при ошибке "Lead not found" (сделка
                удалена/слита через NOVA, например в гонке с get_lead чуть
                раньше) поднять LeadNotFoundError вместо тихого логирования.
                Нужно вызывающему коду, чтобы отличить "сделки больше нет,
                нужно искать актуальную" от обычной временной ошибки сети.
                По умолчанию False — поведение для остальных вызывающих
                (diag_start-перенос) не меняется.

        Raises:
            LeadNotFoundError: если raise_on_not_found=True и сделка не найдена.
        """
        logger.info("Moving lead %s to pipeline=%s, status=%s", lead_id, pipeline_id, status_id)
        try:
            await self._make_request(
                "PATCH",
                "/leads",
                data=[{"id": lead_id, "pipeline_id": pipeline_id, "status_id": status_id}],
            )
            logger.info("Lead %s moved to pipeline=%s, status=%s", lead_id, pipeline_id, status_id)
        except LeadNotFoundError:
            if raise_on_not_found:
                raise
            logger.warning("Lead %s not found on move (likely merged)", lead_id)
        except Exception as e:
            logger.error("Failed to move lead %s: %s", lead_id, e)

    async def update_lead_enum(
        self, lead_id: int, field_id: int, enum_id: int, raise_on_not_found: bool = False
    ) -> None:
        """
        Обновить select-поле сделки по enum_id.

        Args:
            lead_id: ID сделки
            field_id: ID поля типа select
            enum_id: ID варианта выбора
            raise_on_not_found: Если True — при ошибке "Lead not found" поднять
                LeadNotFoundError вместо тихого логирования (см. move_lead).
                По умолчанию False — поведение для остальных вызывающих не меняется.

        Raises:
            LeadNotFoundError: если raise_on_not_found=True и сделка не найдена.
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
        except LeadNotFoundError:
            if raise_on_not_found:
                raise
            logger.warning("Lead %s not found on enum update (likely merged)", lead_id)
        except Exception as e:
            logger.error("Failed to update lead %s field=%s: %s", lead_id, field_id, e)

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

        except LeadNotFoundError as e:
            # Постоянная, часто ожидаемая ошибка (сделка слита NOVA) —
            # у вызывающего кода почти везде есть recovery. WARNING, не ERROR.
            logger.warning("Lead %s not found on update: %s", lead_id, e)
            raise
        except Exception as e:
            logger.error("Error updating lead %s: %s", lead_id, e)
            raise

    async def fill_missing_utm_fields(
        self,
        lead_id: int,
        utm_data: dict,
        raise_on_not_found: bool = False,
        contact_id: int | None = None,
    ) -> bool:
        """
        Дозаполнить пустые UTM-поля сделки (first-touch: не перезаписывает уже заполненные).

        Если сделка оказалась слита NOVA между GET и PATCH (GET по старому
        lead_id ещё успевает пройти, а PATCH уже отвечает "not found") —
        автоматически ищем выжившую сделку того же контакта через
        check_duplicate_lead и дозаполняем метки прямо на ней. Никакой записи
        в основную БД при этом не происходит (у этого клиента нет доступа к
        storage) — lead_id в БД сам поправится при следующем сообщении
        клиента через conversation_manager._reopen_conversation.

        Args:
            lead_id: ID сделки
            utm_data: UTM-метки {"utm_source": ..., "utm_medium": ..., ...}
            raise_on_not_found: Если True — при ошибке "Lead not found" (и если
                не удалось найти сделку-дубль контакта) поднять
                LeadNotFoundError вместо тихого return False. Нужно только
                utm_backfill_worker, чтобы отличить "сделки больше нет, перестать
                пытаться" от обычной временной ошибки. По умолчанию False —
                поведение для остальных вызывающих (conversation_manager) не меняется.
            contact_id: Опционально — заведомо верный ID контакта, если он уже
                известен вызывающему (utm_backfill_worker передаёт его из
                своего Redis-флага). Используется для поиска дубля ВМЕСТО
                _embedded.contacts из GET-ответа — на практике для уже
                сливающейся/слитой NOVA сделки GET успешно возвращает старые
                custom_fields_values, но _embedded.contacts к этому моменту
                уже пустой (контакт отвязывается от старой сделки раньше, чем
                сама сделка пропадает из выдачи), поэтому полагаться на embed
                в этом сценарии нельзя (подтверждено на проде 16.10.2026). Если
                не передан — пробуем достать contact_id из embed как раньше
                (работает для вызовов из conversation_manager, где lead_id
                всегда свежий/только что найденный, а не потенциально стальной).

        Returns:
            True если после этого вызова ВСЕ 5 UTM-полей заполнены (можно больше
            не проверять эту сделку), False если что-то ещё осталось пустым.

        Raises:
            LeadNotFoundError: если raise_on_not_found=True и сделка не найдена.
        """
        utm_field_map = {
            settings.FIELD_UTM_SOURCE:   utm_data.get("utm_source"),
            settings.FIELD_UTM_MEDIUM:   utm_data.get("utm_medium"),
            settings.FIELD_UTM_CAMPAIGN: utm_data.get("utm_campaign"),
            settings.FIELD_UTM_TERM:     utm_data.get("utm_term"),
            settings.FIELD_UTM_CONTENT:  utm_data.get("utm_content"),
        }

        try:
            lead_response = await self._make_request(
                "GET", f"/leads/{lead_id}", params={"with": "contacts"}
            )
            existing = self._parse_custom_fields(lead_response.get("custom_fields_values"))

            _EMPTY = {"none", "null", "undefined", ""}
            fields_to_update = {
                field_id: value
                for field_id, value in utm_field_map.items()
                if value and str(value).strip().lower() not in _EMPTY and not existing.get(field_id)
            }

            if fields_to_update:
                try:
                    await self.update_lead(lead_id, fields_to_update)
                    logger.info("UTM fields filled for lead %s: %s", lead_id, fields_to_update)
                except LeadNotFoundError:
                    resolved_contact_id = contact_id
                    if not resolved_contact_id:
                        contacts = lead_response.get("_embedded", {}).get("contacts", [])
                        resolved_contact_id = contacts[0]["id"] if contacts else None
                    if not resolved_contact_id:
                        raise
                    # Сделка слита NOVA между нашим GET и PATCH (GET успел
                    # пройти по старому id, PATCH уже не нашёл сделку). Ищем
                    # выжившую сделку этого же контакта и дозаполняем метки
                    # прямо на ней через amoCRM API. В основную БД ничего не
                    # пишем — это отдельный сервис без доступа к storage,
                    # lead_id в БД сам поправится при следующем сообщении
                    # клиента через conversation_manager._reopen_conversation.
                    logger.info(
                        "Lead %s not found on update (likely merged), searching "
                        "duplicate for contact %s",
                        lead_id, resolved_contact_id,
                    )
                    duplicate = await self.check_duplicate_lead(
                        contact_id=resolved_contact_id, pipeline_id=None, only_open=False
                    )
                    if not duplicate:
                        raise
                    fresh_lead_id = duplicate["id"]
                    existing = self._parse_custom_fields(duplicate.get("custom_fields_values"))
                    fields_to_update = {
                        field_id: value
                        for field_id, value in fields_to_update.items()
                        if not existing.get(field_id)
                    }
                    if fields_to_update:
                        await self.update_lead(fresh_lead_id, fields_to_update)
                        logger.info(
                            "UTM fields filled for lead %s (duplicate of merged lead %s): %s",
                            fresh_lead_id, lead_id, fields_to_update,
                        )

            all_filled = all(
                existing.get(field_id) or fields_to_update.get(field_id)
                for field_id in utm_field_map
            )
            return all_filled
        except LeadNotFoundError as e:
            # Дошли сюда — значит восстановление НЕ помогло: дубль не
            # нашёлся совсем (или не было contact_id, чтобы его искать).
            # Это настоящая неудача, а не штатный race с NOVA.
            if raise_on_not_found:
                # utm_backfill_worker сам логирует и снимает флаг — не дублируем лог.
                raise
            logger.error(
                "Lead %s not found and no duplicate could be resolved: %s", lead_id, e
            )
            return False
        except Exception as e:
            logger.error("Failed to fill UTM fields for lead %s: %s", lead_id, e)
            return False

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
        self,
        contact_id: int,
        chat_id: str,
        scope_id: str | None = None,
        retry: int = 3,
    ) -> None:
        """
        Привязать чат к контакту через POST /api/v4/contacts/chats.

        ВАЖНО: Требует OAuth2 токен (access_token), а не long-lived токен!

        Ретраится на транзиентных 5xx (502/503/504) и сетевых ошибках
        экспоненциальным backoff (1с/2с/4с) — как и остальные вызовы через
        _make_request. Раньше этот метод был единственным в клиенте без
        внутреннего retry на 5xx (ретраилась только протухшая авторизация,
        401): один-единственный 504 сразу убивал всю попытку привязки чата,
        и внешний requeue (worker.py) пересоздавал чат в amojo с нуля на
        каждом раунде, забивая amojo осиротевшими непривязанными чатами, а
        при затяжном шторме 504 у AmoCRM сообщение терялось полностью, хотя
        сама сделка была жива и привязка — идемпотентная операция, повтор с
        тем же chat_id/contact_id ничего не портит.

        Args:
            contact_id: ID контакта в AmoCRM
            chat_id: ID чата из amojo (UUID)
            scope_id: Scope ID канала (по умолчанию из settings)
            retry: Количество попыток на транзиентные ошибки
        """
        scope_id = scope_id or settings.AMOJO_SCOPE_ID

        url = f"{settings.amocrm_api_url}/contacts/chats"

        payload = [
            {
                "contact_id": contact_id,
                "chat_id": chat_id,
                "scope_id": scope_id,
            }
        ]

        # Получаем OAuth2 токен через TokenManager
        token_manager = get_token_manager()
        oauth_token = await token_manager.get_access_token()

        await self._ensure_session()

        last_error: Exception | None = None
        token_refreshed = False

        for attempt in range(retry):
            logger.info(
                "Linking chat to contact (attempt %d/%d): contact_id=%s, chat_id=%s, scope_id=%s",
                attempt + 1,
                retry,
                contact_id,
                chat_id,
                scope_id,
            )

            headers = {
                "Authorization": f"Bearer {oauth_token}",
                "Content-Type": "application/json",
            }

            try:
                await self._rate_limit()

                async with self.session.post(
                    url,
                    headers=headers,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as response:
                    if response.status >= 400:
                        text = await response.text()

                        # Если токен истек (401) - обновляем и повторяем
                        # немедленно, без backoff (это не транзиентный сбой
                        # AmoCRM, а наша просроченная авторизация).
                        if response.status == 401 and not token_refreshed:
                            logger.info("OAuth token expired, refreshing...")
                            token_refreshed = True
                            oauth_token = await token_manager.refresh_access_token()
                            continue

                        logger.error(
                            "AmoCRM API error linking chat %s: %s", response.status, text
                        )
                        last_error = aiohttp.ClientError(
                            f"API error {response.status}: {text}"
                        )

                        if attempt == retry - 1:
                            logger.error(
                                "All %d retry attempts to link chat failed: "
                                "contact_id=%s, chat_id=%s",
                                retry,
                                contact_id,
                                chat_id,
                            )
                            raise last_error

                        delay = 2**attempt
                        logger.info("Retrying chat link in %d seconds...", delay)
                        await asyncio.sleep(delay)
                        continue

                    logger.info("Chat %s linked to contact %s", chat_id, contact_id)
                    return

            except aiohttp.ClientError:
                raise
            except Exception as e:
                last_error = e
                logger.error(
                    "Error linking chat to contact (attempt %d/%d): %s",
                    attempt + 1,
                    retry,
                    e,
                )
                if attempt == retry - 1:
                    raise
                delay = 2**attempt
                await asyncio.sleep(delay)

        # Сюда попадаем, если последняя попытка ушла в "continue" из-за
        # обновления токена (401 на самой последней итерации) — без этого
        # fallback функция тихо вернула бы None (т.е. "успех"), хотя
        # привязка на самом деле ни разу не подтвердилась.
        raise last_error or aiohttp.ClientError(
            f"Failed to link chat after {retry} attempts (token refresh consumed "
            f"last retry): contact_id={contact_id}, chat_id={chat_id}"
        )

    async def add_lead_tag(
        self, lead_id: int, tag_id: int, raise_on_not_found: bool = False
    ) -> None:
        """
        Добавить тег к сделке без удаления существующих тегов.

        Args:
            lead_id: ID сделки
            tag_id: ID тега в AmoCRM (постоянный, создаётся один раз вручную)
            raise_on_not_found: Если True — при ошибке "Lead not found" поднять
                LeadNotFoundError вместо тихого логирования (см. move_lead).
                По умолчанию False — поведение для остальных вызывающих не меняется.

        Raises:
            LeadNotFoundError: если raise_on_not_found=True и сделка не найдена.
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
        except LeadNotFoundError:
            if raise_on_not_found:
                raise
            logger.warning("Lead %s not found on add_lead_tag (likely merged)", lead_id)
        except Exception as e:
            logger.error("Failed to add tag id=%s to lead %s: %s", tag_id, lead_id, e)

    async def add_lead_note(self, lead_id: int, text: str) -> None:
        """
        Добавить примечание (заметку) к сделке.

        Примечания отображаются в ленте активности сделки (вкладка «Примечания»)
        и видны всем менеджерам, работающим с этой сделкой.

        Args:
            lead_id: ID сделки
            text: Текст примечания
        """
        logger.info("Adding note to lead %s: %r", lead_id, text[:80])
        try:
            await self._make_request(
                "POST",
                "/leads/notes",
                data=[{
                    "entity_id": lead_id,
                    "note_type": "common",
                    "params": {"text": text},
                }],
            )
            logger.info("Note added to lead %s", lead_id)
        except Exception as e:
            logger.warning("Failed to add note to lead %s: %s", lead_id, e)

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

