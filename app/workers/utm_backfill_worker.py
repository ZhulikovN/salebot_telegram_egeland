"""
Фоновый сервис дозаполнения UTM-меток в сделках AmoCRM.

Проблема: при создании сделки Salebot не всегда успевает выставить все
UTM-переменные клиента (utm_medium, utm_content и т.д. может выставить бот
уже после первого сообщения). Первое касание пишет только то, что было
доступно в тот момент — остальное остаётся пустым навсегда.

Решение: conversation_manager ставит в Redis флаг
    utm_check_needed:{lead_id} = "{salebot_client_id}"  EX 7200
при каждом входящем salebot_message (как для новых, так и для возвращающихся
клиентов). Этот воркер раз в UTM_BACKFILL_INTERVAL_SEC секунд делает SCAN
по паттерну utm_check_needed:* и дозаполняет только пустые UTM-поля сделки.

Флаг удаляется после полного заполнения или по истечении TTL (2 часа).
utm_no_data:{lead_id} (1 час) кеширует пустой ответ Salebot — не долбим
его каждые 5 мин если UTM у клиента нет.

Если задан UTM_AMO_ACCESS_TOKEN — сервис использует свой Bearer-токен и
Redis-ключ "rate_limit:amocrm:utm" (свои 7 req/sec, независимые от воркера).

Запуск как отдельный systemd-сервис (одна инстанция):
    python -m app.workers.utm_backfill_worker
"""
import asyncio
import logging
import signal
from typing import Any

from app.services.amocrm_client import AmoCRMClient
from app.services.salebot_client import SalebotClient
from app.settings import settings
from app.utils.redis_connection import get_redis

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Как часто запускать проход по флагам
UTM_BACKFILL_INTERVAL_SEC = 300  # 5 минут
# Пауза между сделками при УСПЕШНОМ запросе к Salebot
UTM_BACKFILL_PER_ITEM_DELAY_SEC = 0.3
# Пауза после ОШИБКИ от Salebot (502/503/сеть)
UTM_SALEBOT_ERROR_BACKOFF_SEC = 5
# TTL флага "все UTM уже заполнены" — 9 часов (дольше чем TTL utm_check_needed)
UTM_DONE_TTL_SEC = 9 * 3600
# TTL кеша "Salebot вернул пустые данные" — 1 час
UTM_NO_DATA_TTL_SEC = 3600

shutdown_requested = False


def handle_shutdown_signal(signum: int, frame: Any) -> None:
    """Обработчик SIGTERM/SIGINT для graceful shutdown."""
    global shutdown_requested
    logger.info("Shutdown signal received (%s), stopping after current pass...", signum)
    shutdown_requested = True


_SALEBOT_EMPTY_VALUES = {"none", "null", "undefined", ""}


def _normalize_utm_value(val: object) -> str | None:
    """Вернуть значение UTM или None, если Salebot прислал заглушку."""
    if val is None:
        return None
    s = str(val).strip()
    return None if s.lower() in _SALEBOT_EMPTY_VALUES else s


def _extract_utm(variables: dict) -> dict[str, str | None]:
    """Достать UTM-поля из плоского ответа Salebot get_variables.

    Фильтрует строки-заглушки ("None", "null", "undefined", ""),
    которые Salebot присылает для незаполненных переменных.
    """
    return {
        "utm_source":   _normalize_utm_value(variables.get("utm_source")),
        "utm_medium":   _normalize_utm_value(variables.get("utm_medium")),
        "utm_campaign": _normalize_utm_value(variables.get("utm_campaign")),
        "utm_term":     _normalize_utm_value(variables.get("utm_term")),
        "utm_content":  _normalize_utm_value(variables.get("utm_content")),
    }


async def run_once(amocrm: AmoCRMClient, salebot: SalebotClient) -> None:
    """Один проход: найти все флаги utm_check_needed:* в Redis и дозаполнить UTM."""
    redis = get_redis()

    # Собираем все ключи utm_check_needed:* через SCAN (не блокирует Redis)
    keys: list[str] = []
    cursor = 0
    while True:
        cursor, batch = await redis.scan(cursor, match="utm_check_needed:*", count=200)
        for k in batch:
            keys.append(k.decode() if isinstance(k, bytes) else k)
        if cursor == 0:
            break

    logger.info("UTM backfill pass: %d pending check(s)", len(keys))

    checked = 0
    updated = 0

    for key in keys:
        if shutdown_requested:
            break

        # Извлекаем lead_id из имени ключа
        lead_id_str = key.removeprefix("utm_check_needed:")
        try:
            lead_id = int(lead_id_str)
        except ValueError:
            logger.warning("UTM backfill: unexpected key format %r, skipping", key)
            await redis.delete(key)
            continue

        # Пропускаем если все UTM уже заполнены
        done_key = f"utm_sync_done:{lead_id}"
        if await redis.get(done_key):
            await redis.delete(key)  # флаг больше не нужен
            continue

        # Пропускаем если Salebot недавно вернул пустые данные
        no_data_key = f"utm_no_data:{lead_id}"
        if await redis.get(no_data_key):
            continue  # флаг оставляем — попробуем снова после истечения no_data

        # Получаем salebot_client_id из значения ключа (записывается в conv_manager)
        raw = await redis.get(key)
        if not raw:
            continue  # TTL истёк между SCAN и GET — не страшно
        try:
            salebot_client_id = int(raw.decode() if isinstance(raw, bytes) else raw)
        except (ValueError, AttributeError):
            logger.warning("UTM backfill: bad salebot_client_id in key %r, removing", key)
            await redis.delete(key)
            continue

        checked += 1
        error_occurred = False
        try:
            variables = await salebot.get_variables(salebot_client_id)
            utm_data = _extract_utm(variables)

            if not any(utm_data.values()):
                # Salebot не знает UTM — кешируем пустой ответ на 1 час,
                # флаг utm_check_needed оставляем (попробуем после истечения кеша)
                await redis.set(no_data_key, "1", ex=UTM_NO_DATA_TTL_SEC)
                continue

            all_filled = await amocrm.fill_missing_utm_fields(lead_id, utm_data)
            if all_filled:
                await redis.set(done_key, "1", ex=UTM_DONE_TTL_SEC)
                await redis.delete(key)  # все поля заполнены — флаг больше не нужен
                updated += 1
            # else: часть полей ещё пустая — оставляем флаг, попробуем позже

        except Exception as e:
            error_occurred = True
            logger.warning("UTM backfill failed for lead=%s: %s", lead_id, e)
        finally:
            delay = UTM_SALEBOT_ERROR_BACKOFF_SEC if error_occurred else UTM_BACKFILL_PER_ITEM_DELAY_SEC
            await asyncio.sleep(delay)

    logger.info(
        "UTM backfill pass finished: checked=%d, fully_filled=%d", checked, updated
    )


async def main() -> None:
    """Бесконечный цикл: раз в UTM_BACKFILL_INTERVAL_SEC секунд запускать проход."""
    signal.signal(signal.SIGTERM, handle_shutdown_signal)
    signal.signal(signal.SIGINT, handle_shutdown_signal)

    logger.info("=" * 60)
    logger.info("UTM backfill worker started")
    logger.info(
        "Interval=%ds, trigger=Redis utm_check_needed:*, AmoCRM rate limit via Redis (%d req/s)",
        UTM_BACKFILL_INTERVAL_SEC,
        settings.AMOCRM_MAX_REQUESTS_PER_SECOND,
    )
    logger.info("=" * 60)

    has_own_integration = bool(settings.UTM_AMO_ACCESS_TOKEN)
    amocrm = AmoCRMClient(
        access_token=settings.UTM_AMO_ACCESS_TOKEN or None,
        rate_limit_key="rate_limit:amocrm:utm" if has_own_integration else "rate_limit:amocrm",
        max_requests_per_second=(
            settings.UTM_AMOCRM_MAX_REQUESTS_PER_SECOND
            if has_own_integration
            else settings.AMOCRM_MAX_REQUESTS_PER_SECOND
        ),
    )
    logger.info(
        "AmoCRM client for utm_backfill: own_integration=%s, rate_limit_key=%s",
        has_own_integration,
        amocrm.rate_limit_key,
    )
    salebot = SalebotClient()

    try:
        while not shutdown_requested:
            try:
                await run_once(amocrm, salebot)
            except Exception as e:
                logger.error("UTM backfill pass crashed: %s", e, exc_info=True)

            for _ in range(UTM_BACKFILL_INTERVAL_SEC):
                if shutdown_requested:
                    break
                await asyncio.sleep(1)
    finally:
        await amocrm.close()
        logger.info("UTM backfill worker stopped")


if __name__ == "__main__":
    asyncio.run(main())
