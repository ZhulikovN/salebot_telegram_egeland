"""Webhook endpoint для amoCRM (amojo) — регистрация канала."""
import logging

from fastapi import APIRouter, Request

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/amojo/webhook/{scope_id}")
async def amojo_webhook(scope_id: str, request: Request) -> dict[str, str]:
    """
    Webhook для регистрации канала amojo и входящих событий от amoCRM.

    На этапе регистрации канала amoCRM отправляет запрос на этот URL
    и ожидает ответ 200 OK. Endpoint логирует входящий запрос и сразу
    возвращает 200.

    Args:
        scope_id: Scope ID канала из URL
        request: FastAPI Request

    Returns:
        Статус OK
    """
    raw_body = await request.body()
    logger.info(
        "Amojo webhook: scope_id=%s, body_len=%d",
        scope_id,
        len(raw_body),
    )
    if raw_body:
        logger.info("Amojo webhook body: %s", raw_body.decode("utf-8", errors="replace")[:500])

    return {"status": "ok"}
