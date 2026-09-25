"""
Отправка аналитических событий партнёру (egeinformatika.ru).

Сейчас реализовано одно событие: lead_created — уведомление о том, что
пользователь пришёл в el_personal_bot по deeplink ?start=diag_* из
диагностического бота коллеги.

Отправка происходит fire-and-forget: ошибка логируется, но не пробрасывается
наружу и не влияет на основной поток обработки сообщений.
"""
import asyncio
import logging
from datetime import datetime, timezone

import aiohttp

from app.settings import settings

logger = logging.getLogger(__name__)

_MAX_ATTEMPTS = 3
_RETRY_DELAYS = (2.0, 5.0)  # паузы между попытками (секунды)


async def notify_lead_created(
    *,
    tg_id: str,
    start: str,
    amo_lead_id: int,
) -> None:
    """
    Отправить событие lead_created на партнёрский аналитический эндпоинт.

    Если PARTNER_ANALYTICS_URL или PARTNER_ANALYTICS_SECRET не заданы в .env —
    функция немедленно возвращается без каких-либо запросов.

    Args:
        tg_id:       Telegram ID клиента (строка).
        start:       Значение параметра start из deeplink (например "diag_oge").
        amo_lead_id: ID сделки в AmoCRM.
    """
    url = settings.PARTNER_ANALYTICS_URL
    secret = settings.PARTNER_ANALYTICS_SECRET
    if not url or not secret:
        logger.debug(
            "PARTNER_NOTIFY: PARTNER_ANALYTICS_URL/SECRET not set — skipping lead_created"
        )
        return

    payload = {
        "event": "lead_created",
        "tg_id": int(tg_id),
        "start": start,
        "amo_lead_id": amo_lead_id,
        "created_at": datetime.now(tz=timezone.utc).isoformat(),
    }
    headers = {
        "Content-Type": "application/json",
        "X-Webhook-Secret": secret,
    }

    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    json=payload,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status == 200:
                        logger.info(
                            "PARTNER_NOTIFY: lead_created sent: tg_id=%s, start=%r, "
                            "amo_lead_id=%s",
                            tg_id, start, amo_lead_id,
                        )
                        return
                    body = await resp.text()
                    logger.warning(
                        "PARTNER_NOTIFY: unexpected status %s on attempt %d/%d: %r",
                        resp.status, attempt, _MAX_ATTEMPTS, body[:200],
                    )
        except Exception as exc:
            logger.warning(
                "PARTNER_NOTIFY: error on attempt %d/%d: %s",
                attempt, _MAX_ATTEMPTS, exc,
            )

        if attempt < _MAX_ATTEMPTS:
            await asyncio.sleep(_RETRY_DELAYS[attempt - 1])

    logger.error(
        "PARTNER_NOTIFY: all %d attempts failed for lead_created: "
        "tg_id=%s, start=%r, amo_lead_id=%s",
        _MAX_ATTEMPTS, tg_id, start, amo_lead_id,
    )
