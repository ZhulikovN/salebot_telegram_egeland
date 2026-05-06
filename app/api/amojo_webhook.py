"""Webhook endpoint для amoCRM (amojo)."""
import json
import logging

from fastapi import APIRouter, HTTPException, Request

from app.models.amojo import AmojoWebhook
from app.services.conversation_manager import ConversationManager
from app.settings import settings

router = APIRouter()
logger = logging.getLogger(__name__)

ALLOWED_SCOPES = {settings.AMOJO_SCOPE_ID}


@router.post("/amojo/webhook/{scope_id}")
async def amojo_webhook(
    scope_id: str,
    request: Request,
) -> dict[str, str | bool]:
    """
    Webhook для ответов менеджеров из amoCRM.

    Алгоритм:
    1. Проверить scope_id
    2. Парсить JSON payload
    3. Проверить что сообщение от менеджера (sender без client_id)
    4. Найти диалог по conversation_id
    5. Отправить сообщение в Salebot

    Args:
        scope_id: Scope ID из URL (должен совпадать с AMOJO_SCOPE_ID)
        request: FastAPI Request

    Returns:
        Статус обработки
    """
    try:
        # 1. Проверяем scope_id
        if scope_id not in ALLOWED_SCOPES:
            logger.warning("Unknown scope_id: %s", scope_id)
            raise HTTPException(status_code=403, detail="Unknown scope")

        # 2. Получаем тело запроса
        raw_body = await request.body()
        
        logger.info(
            "AMOJO webhook: scope=%s, body_len=%d",
            scope_id,
            len(raw_body),
        )
        # logger.info("AMOJO webhook RAW BODY: %s", raw_body.decode("utf-8")[:1000])

        # 3. Парсим JSON
        try:
            payload = json.loads(raw_body.decode("utf-8"))
            webhook = AmojoWebhook.model_validate(payload)
        except Exception as e:
            logger.error("Invalid webhook payload: %s", e)
            # Всегда возвращаем 200 для amoCRM
            return {"status": "error", "detail": "invalid_payload"}

        # 4. Проверяем структуру
        if not webhook.message:
            logger.warning("No message in payload")
            return {"status": "ok"}

        # 5. Проверяем что сообщение от менеджера
        if not webhook.message.is_from_manager:
            logger.info(
                "Message from client, ignoring: conversation=%s",
                webhook.message.conversation_id,
            )
            return {"status": "ignored", "reason": "message_from_client"}

        text_preview = webhook.message.message.text or ""
        if len(text_preview) > 50:
            text_preview = text_preview[:50] + "..."

        logger.info(
            "Amojo webhook from manager: conversation=%s, sender=%s, text=%s",
            webhook.message.conversation_id,
            webhook.message.sender.name or "unknown",
            text_preview,
        )

        # 6. Добавляем задачу в очередь (быстро!)
        from app.workers.queue import push_task

        logger.info("Pushing amojo task to queue: %s", webhook.message.conversation_id)
        
        await push_task(
            "amojo_message",
            {
                "conversation_id": webhook.message.conversation_id,
                "message_text": webhook.message.message.text,
            },
        )

        logger.info(
            "Amojo message queued: conversation_id=%s",
            webhook.message.conversation_id,
        )
        return {"status": "ok", "queued": True}

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error processing amojo webhook: %s", e, exc_info=True)
        # Всегда возвращаем 200 для amoCRM
        return {"status": "error", "detail": str(e)}
