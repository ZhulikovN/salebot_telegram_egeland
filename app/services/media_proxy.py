"""Прокси для скачивания медиафайлов из AmoCRM и раздачи Salebot."""
import logging
import os
import time
import uuid
from pathlib import Path

import aiohttp

from app.settings import settings

logger = logging.getLogger(__name__)

MEDIA_DIR = Path("/tmp/salebot_media")
MEDIA_TTL_SECONDS = 86400  # 24 часа — файл должен жить, пока Telegram его не заберёт


def _ensure_media_dir() -> None:
    """Создать директорию для медиафайлов если её нет."""
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)


def _cleanup_old_files() -> None:
    """Удалить файлы старше MEDIA_TTL_SECONDS."""
    if not MEDIA_DIR.exists():
        return
    now = time.time()
    for f in MEDIA_DIR.iterdir():
        try:
            if f.is_file() and (now - f.stat().st_mtime) > MEDIA_TTL_SECONDS:
                f.unlink()
                logger.debug("Deleted expired media file: %s", f.name)
        except Exception as e:
            logger.warning("Failed to delete media file %s: %s", f.name, e)


def _get_extension(url: str) -> str:
    """Получить расширение файла из URL."""
    clean_url = url.split("?")[0]
    parts = clean_url.split("/")
    filename = parts[-1] if parts else ""
    if "." in filename:
        return "." + filename.rsplit(".", 1)[-1].lower()
    return ".bin"


async def download_and_proxy(media_url: str) -> str | None:
    """
    Скачать медиафайл с AmoCRM и вернуть публичный URL нашего сервера.

    AmoCRM drive URLs закрыты — требуют авторизацию.
    Мы скачиваем файл с нашим токеном, сохраняем в /tmp/,
    и отдаём Salebot наш публичный URL.

    Args:
        media_url: URL файла на drive-b.amocrm.ru

    Returns:
        Публичный URL файла на нашем сервере или None при ошибке
    """
    _ensure_media_dir()
    _cleanup_old_files()

    ext = _get_extension(media_url)
    filename = f"{uuid.uuid4().hex}{ext}"
    filepath = MEDIA_DIR / filename

    headers = {
        "Authorization": f"Bearer {settings.AMO_ACCESS_TOKEN}",
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                media_url,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=60),
            ) as response:
                if response.status != 200:
                    logger.error(
                        "Failed to download media from AMO: status=%s, url=%s",
                        response.status,
                        media_url,
                    )
                    return None

                content = await response.read()

        filepath.write_bytes(content)

        public_url = f"{settings.PUBLIC_URL}/media/{filename}"
        logger.info(
            "Media downloaded and proxied: %s bytes, url=%s",
            len(content),
            public_url,
        )
        return public_url

    except Exception as e:
        logger.error("Error downloading media from AMO: %s", e, exc_info=True)
        return None


def get_media_path(filename: str) -> Path | None:
    """
    Получить путь к медиафайлу по имени.

    Args:
        filename: Имя файла

    Returns:
        Path к файлу или None если не найден
    """
    filepath = MEDIA_DIR / filename
    if filepath.exists() and filepath.is_file():
        return filepath
    return None
