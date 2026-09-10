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

Если задан UTM_AMO_ACCESS_TOKEN (переменная окружения, токен отдельной
интеграции в AmoCRM) — сервис использует свой Bearer-токен и свой Redis-ключ
"rate_limit:amocrm:utm", то есть свои 7 req/sec, независимые от основного
воркера. Если UTM_AMO_ACCESS_TOKEN не задан — работает как раньше: общий
токен и общий Redis-ключ "rate_limit:amocrm" с веб-воркерами, отдельно
превысить лимит не может.

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
UTM_BACKFILL_INTERVAL_SEC = 300  # 5 минут — даём Salebot время записать метки
# Не проверяем диалоги старше этого возраста — окно само "сдвигается"
UTM_BACKFILL_WINDOW_HOURS = 2
# Пауза между диалогами при УСПЕШНОМ запросе к Salebot
UTM_BACKFILL_PER_ITEM_DELAY_SEC = 0.3
# Пауза после ОШИБКИ от Salebot (502/503/сеть) — даём сервису время восстановиться
# вместо того чтобы продолжать долбить его на той же скорости
UTM_SALEBOT_ERROR_BACKOFF_SEC = 5
# TTL флага "все UTM уже заполнены" — больше чем окно, чтобы не перепроверять
UTM_DONE_TTL_SEC = UTM_BACKFILL_WINDOW_HOURS * 3600 + 3600
# TTL флага "Salebot не вернул ни одного UTM-значения" — кешируем пустой ответ,
# чтобы не опрашивать Salebot повторно каждые 90 сек для клиентов без UTM-данных.
# Выбрано 1 час: UTM в Salebot может появиться позже (бот ещё не дошёл до нужного шага),
# но опрашивать каждые 90 сек при 500+ диалогах — это ~3 req/s непрерывно.
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

        # Пропускаем сделки где все UTM уже заполнены
        done_key = f"utm_sync_done:{conv.lead_id}"
        if await redis.get(done_key):
            continue

        # Пропускаем сделки где Salebot недавно вернул пустые данные —
        # нет смысла спрашивать его снова каждые 90 сек, если UTM у клиента
        # ещё не появился. Ключ живёт UTM_NO_DATA_TTL_SEC секунд (1 час).
        no_data_key = f"utm_no_data:{conv.lead_id}"
        if await redis.get(no_data_key):
            continue

        checked += 1
        error_occurred = False
        try:
            variables = await salebot.get_variables(conv.salebot_client_id)
            utm_data = _extract_utm(variables)

            if not any(utm_data.values()):
                # Salebot не знает UTM для этого клиента — кешируем чтобы
                # не долбить его снова через 90 сек
                await redis.set(no_data_key, "1", ex=UTM_NO_DATA_TTL_SEC)
                continue

            all_filled = await amocrm.fill_missing_utm_fields(conv.lead_id, utm_data)
            if all_filled:
                await redis.set(done_key, "1", ex=UTM_DONE_TTL_SEC)
                updated += 1

        except Exception as e:
            error_occurred = True
            logger.warning(
                "UTM backfill failed for lead=%s, conversation=%s: %s",
                conv.lead_id,
                conv.conversation_id,
                e,
            )
        finally:
            # При ошибке (502/503/сеть) — увеличенная пауза, чтобы дать
            # Salebot или AmoCRM время восстановиться перед следующим запросом.
            # При успехе — стандартная пауза 0.3с.
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
        "Interval=%ds, window=%dh, AmoCRM rate limit shared via Redis (%d req/s)",
        UTM_BACKFILL_INTERVAL_SEC,
        UTM_BACKFILL_WINDOW_HOURS,
        settings.AMOCRM_MAX_REQUESTS_PER_SECOND,
    )
    logger.info("=" * 60)

    # Если задан отдельный токен (своя интеграция в AmoCRM) — используем свой
    # Redis-ключ для rate limiter, чтобы не делить лимит с основным воркером.
    # Если токена нет — работаем как раньше, на общем токене и общем лимите
    # (иначе получим два независимых лимитера на один и тот же физический
    # токен, и вместе они превысят реальные 7 req/sec на стороне AmoCRM).
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
