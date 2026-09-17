"""Webhook для события "заявка на консультацию" от сервера el_oge_diagnostika_bot.

Коллега (Григорий Конев) держит бота el_oge_diagnostika_bot и шлёт нам это
событие, когда ученик запрашивает консультацию (кнопка "получить консультацию"
после диагностики, из меню или из дожима). Сделка у клиента уже существует —
создана с первого касания в тестовой воронке (см. bot_routing.py). Наша задача
здесь — найти эту сделку по tg_id (через свою БД, без лишнего запроса к AmoCRM),
перенести её в целевую воронку/этап, заполнить поля сделки (класс, промокод,
UTM) и записать оставшийся контекст (тип обращения, предмет, результат теста,
источник, экран) примечанием.

Формат события и договорённости — переписка с Григорием Коневым 17.09.2026:
- Ключ поиска — tg_id.
- Повторные события с тем же lead_id (их внутренний ID заявки, НЕ AmoCRM) —
  это обновление той же сделки, не дубль.
- В ответе возвращаем amo_lead_id — коллега сохраняет его у себя.
- Отправка событий с их стороны идёт в фоне с retry — их ответ ученику не
  зависит от скорости нашего ответа, поэтому у нас нет необходимости отвечать
  мгновенно, но сам move_lead/add_lead_note всё равно уводим в очередь, чтобы
  не задерживать HTTP-ответ на время работы rate-лимитера AmoCRM.

Аутентификация: тот же заголовок X-Webhook-Secret, что и у telegram_webhook.
"""
import logging
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request

from app.db.storage import get_conversation_storage
from app.settings import settings
from app.workers.queue import push_task

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/webhook/lead/{bot_name}")
async def lead_webhook(
    bot_name: str,
    request: Request,
    x_webhook_secret: str | None = Header(default=None),
) -> dict[str, Any]:
    """
    Принять событие о заявке на консультацию от сервера коллеги.

    Ищет существующую сделку по (tg_id, bot_name) в своей БД (сделка уже
    создана с первого касания), ставит в очередь перенос в целевую воронку
    и примечание с контекстом, отвечает сразу с amo_lead_id — не дожидаясь
    фактического выполнения запросов к AmoCRM.
    """
    if not settings.TELEGRAM_WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="Endpoint disabled")
    if x_webhook_secret != settings.TELEGRAM_WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="Invalid secret")

    try:
        payload: dict[str, Any] = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    tg_id = payload.get("tg_id")
    if not tg_id:
        return {"status": "error", "detail": "missing tg_id"}

    platform_id = str(tg_id)
    external_lead_id = payload.get("lead_id")  # их внутренний ID заявки, не AmoCRM

    logger.info(
        "LEAD_WEBHOOK: bot=%s, tg_id=%s, external_lead_id=%s, kind=%s, created=%s",
        bot_name,
        platform_id,
        external_lead_id,
        payload.get("kind"),
        payload.get("created"),
    )

    try:
        storage = get_conversation_storage()
        try:
            conversation = await storage.get_by_platform_id(platform_id, bot_name)
        finally:
            await storage.close()

        if not conversation:
            logger.warning(
                "LEAD_WEBHOOK: conversation not found, bot=%s, tg_id=%s — "
                "сделка должна была быть создана с первого касания",
                bot_name,
                platform_id,
            )
            return {"status": "not_found"}

        if not conversation.lead_id:
            logger.warning(
                "LEAD_WEBHOOK: conversation found but lead_id is empty, bot=%s, tg_id=%s",
                bot_name,
                platform_id,
            )
            return {"status": "no_lead"}

        lead_id = conversation.lead_id

        # Сам перенос сделки и примечание уходят в очередь — не блокируем
        # HTTP-ответ на время работы rate-лимитера AmoCRM.
        await push_task(
            "lead_event",
            {
                "bot_name": bot_name,
                "platform_id": platform_id,
                "lead_id": lead_id,
                "kind": payload.get("kind"),
                "grade": payload.get("grade"),
                "subject": payload.get("subject"),
                "subjects": payload.get("subjects"),
                "result": payload.get("result"),
                "promo": payload.get("promo"),
                "source": payload.get("source"),
                "utm": payload.get("utm"),
                "place": payload.get("place"),
                "external_lead_id": external_lead_id,
                "created": payload.get("created"),
            },
        )

        logger.info(
            "LEAD_WEBHOOK queued: bot=%s, tg_id=%s, lead_id=%s", bot_name, platform_id, lead_id
        )
        return {"status": "ok", "amo_lead_id": lead_id}

    except Exception as e:
        logger.error(
            "LEAD_WEBHOOK error: bot=%s, tg_id=%s, error=%s",
            bot_name,
            platform_id,
            e,
            exc_info=True,
        )
        return {"status": "error", "detail": str(e)}
