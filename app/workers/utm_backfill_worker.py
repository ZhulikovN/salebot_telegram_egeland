"""
Фоновый сервис дозаполнения UTM-меток в сделках AmoCRM.

Проблема: при создании сделки Salebot не всегда успевает выставить все
UTM-переменные клиента (utm_medium, utm_content и т.д. может выставить бот
уже после первого сообщения). Первое касание пишет только то, что было
доступно в тот момент — остальное остаётся пустым навсегда.

Решение: отдельный процесс, который раз в UTM_BACKFILL_INTERVAL_SEC секунд
берёт диалоги, созданные не позднее UTM_BACKFILL_WINDOW_HOURS часов назад,
запрашивает у Salebot актуальный снимок переменных клиента (get_variables)
и дозаполняет только пустые UTM-поля сделки.

Окно само "сдвигается": диалог старше UTM_BACKFILL_WINDOW_HOURS больше не
проверяется, независимо от результата — никаких вечных повторов.

Нагрузка на AmoCRM ограничена не отдельно, а вместе со всем остальным
проектом: rate limiter в AmoCRMClient считает запросы через общий Redis-ключ
"rate_limit:amocrm", используемый также веб-воркерами. Этот сервис — просто
ещё один клиент того же лимита, отдельно превысить его не может.

Запуск как отдельный systemd-сервис (одна инстанция, БЕЗ шаблонизации @N —
дублировать не нужно, окно и без этого покрывает все диалоги):
    python -m app.workers.utm_backfill_worker
"""
import asyncio
import logging
import signal
from typing import Any

from app.db.storage import ConversationStorage, get_conversation_storage
from app.services.amocrm_client import AmoCRMClient
from app.services.salebot_client import SalebotClient
from app.settings import settings
from app.utils.redis_connection import get_redis

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Как часто запускать проход по диалогам
UTM_BACKFILL_INTERVAL_SEC = 90
# Не проверяем диалоги старше этого возраста — окно само "сдвигается"
UTM_BACKFILL_WINDOW_HOURS = 2
# Пауза между обработкой отдельных диалогов внутри одного прохода —
# бережём Salebot API (у AmoCRM свой общий rate limiter, здесь не нужен)
UTM_BACKFILL_PER_ITEM_DELAY_SEC = 0.3
# TTL флага "все UTM уже заполнены" — больше чем окно, чтобы не перепроверять
UTM_DONE_TTL_SEC = UTM_BACKFILL_WINDOW_HOURS * 3600 + 3600

shutdown_requested = False


def handle_shutdown_signal(signum: int, frame: Any) -> None:
    """Обработчик SIGTERM/SIGINT для graceful shutdown."""
    global shutdown_requested
    logger.info("Shutdown signal received (%s), stopping after current pass...", signum)
    shutdown_requested = True


def _extract_utm(variables: dict) -> dict[str, str | None]:
    """Достать UTM-поля из плоского ответа Salebot get_variables."""
    return {
        "utm_source": variables.get("utm_source"),
        "utm_medium": variables.get("utm_medium"),
        "utm_campaign": variables.get("utm_campaign"),
        "utm_term": variables.get("utm_term"),
        "utm_content": variables.get("utm_content"),
    }


async def run_once(
    amocrm: AmoCRMClient, salebot: SalebotClient, storage: ConversationStorage
) -> None:
    """Один проход: найти диалоги в окне, дозаполнить пустые UTM-поля."""
    redis = get_redis()

    conversations = await storage.get_recent_with_lead(
        max_age_hours=UTM_BACKFILL_WINDOW_HOURS
    )

    logger.info("UTM backfill pass: %d conversation(s) in window", len(conversations))

    checked = 0
    updated = 0

    for conv in conversations:
        if shutdown_requested:
            break

        done_key = f"utm_sync_done:{conv.lead_id}"
        if await redis.get(done_key):
            continue

        checked += 1
        try:
            variables = await salebot.get_variables(conv.salebot_client_id)
            utm_data = _extract_utm(variables)

            if not any(utm_data.values()):
                continue

            all_filled = await amocrm.fill_missing_utm_fields(conv.lead_id, utm_data)
            if all_filled:
                await redis.set(done_key, "1", ex=UTM_DONE_TTL_SEC)
                updated += 1

        except Exception as e:
            logger.warning(
                "UTM backfill failed for lead=%s, conversation=%s: %s",
                conv.lead_id,
                conv.conversation_id,
                e,
            )
        finally:
            # В finally, а не в конце тела try — иначе early `continue` выше
            # (пустые переменные у Salebot) пропускал паузу, и запросы к
            # Salebot API шли подряд без выдержки.
            await asyncio.sleep(UTM_BACKFILL_PER_ITEM_DELAY_SEC)

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
        "Interval=%ds, window=%dh, AmoCRM rate limit shared via Redis (%d req/s)",
        UTM_BACKFILL_INTERVAL_SEC,
        UTM_BACKFILL_WINDOW_HOURS,
        settings.AMOCRM_MAX_REQUESTS_PER_SECOND,
    )
    logger.info("=" * 60)

    amocrm = AmoCRMClient()
    salebot = SalebotClient()
    storage = get_conversation_storage()

    try:
        while not shutdown_requested:
            try:
                await run_once(amocrm, salebot, storage)
            except Exception as e:
                logger.error("UTM backfill pass crashed: %s", e, exc_info=True)

            for _ in range(UTM_BACKFILL_INTERVAL_SEC):
                if shutdown_requested:
                    break
                await asyncio.sleep(1)
    finally:
        await amocrm.close()
        await storage.close()
        logger.info("UTM backfill worker stopped")


if __name__ == "__main__":
    asyncio.run(main())
