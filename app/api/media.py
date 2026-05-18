"""Эндпоинт для раздачи временных медиафайлов (прокси AMO → Salebot)."""
import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.services.media_proxy import get_media_path

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/media/{filename}")
async def serve_media(filename: str) -> FileResponse:
    """
    Отдать временный медиафайл Salebot-у.

    Файлы хранятся в /tmp/salebot_media/ и удаляются через 5 минут.

    Args:
        filename: Имя файла (uuid + расширение)

    Returns:
        Содержимое файла
    """
    # Защита от path traversal
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    filepath = get_media_path(filename)
    if filepath is None:
        logger.warning("Media file not found: %s", filename)
        raise HTTPException(status_code=404, detail="File not found")

    logger.info("Serving media file: %s", filename)
    return FileResponse(path=str(filepath))
