"""
Relay-сервис для отправки медиа в Telegram Bot API.

Разворачивается на отдельном сервере вне РФ-облаков (см. README.md в этой папке).
Основной бэкенд (salebot_telegram_egeland) не может напрямую достучаться до
api.telegram.org — этот сервис выступает промежуточным звеном: принимает файл
от основного бэкенда и сам загружает его в Telegram Bot API байтами.

Запуск (локально, для отладки):
    uvicorn app:app --host 0.0.0.0 --port 8000
"""
import logging
import os
from typing import Any

import aiohttp
from fastapi import FastAPI, Form, Header, HTTPException, UploadFile

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("relay")

app = FastAPI(title="Telegram Relay")

_API_BASE = "https://api.telegram.org"
_UPLOAD_TIMEOUT = 120

# Ограничение Telegram на длину подписи к вложению.
CAPTION_LIMIT = 1024

# Тип медиа → цепочка методов Bot API (метод, имя поля с файлом).
# Пробуем по порядку: если Telegram отверг файл (неподходящий формат/размер),
# отправляем следующим способом, чтобы клиент всё равно получил вложение.
_MEDIA_METHODS: dict[str, tuple[tuple[str, str], ...]] = {
    "picture": (("sendPhoto", "photo"), ("sendDocument", "document")),
    "voice": (("sendVoice", "voice"), ("sendAudio", "audio")),
    "video": (("sendVideo", "video"), ("sendDocument", "document")),
    "file": (("sendDocument", "document"),),
}

RELAY_SHARED_SECRET = os.environ.get("RELAY_SHARED_SECRET", "")

if not RELAY_SHARED_SECRET:
    logger.warning(
        "RELAY_SHARED_SECRET не задан — любой, кто достучится до сервиса, "
        "сможет слать сообщения от лица ваших ботов. Задайте секрет в .env"
    )


class RelaySendError(Exception):
    """Не удалось отправить медиа через Telegram Bot API."""


def _check_secret(x_relay_secret: str | None) -> None:
    """Проверить общий секрет запроса. Бросает 401, если не совпадает."""
    if not RELAY_SHARED_SECRET or x_relay_secret != RELAY_SHARED_SECRET:
        raise HTTPException(status_code=401, detail="Invalid or missing X-Relay-Secret")


async def _call_telegram(
    token: str,
    method: str,
    field_name: str,
    chat_id: str,
    content: bytes,
    filename: str,
    caption: str | None,
) -> None:
    """Выполнить запрос к Bot API с multipart-загрузкой файла."""
    url = f"{_API_BASE}/bot{token}/{method}"

    form = aiohttp.FormData()
    form.add_field("chat_id", str(chat_id))
    if caption:
        form.add_field("caption", caption[:CAPTION_LIMIT])
    form.add_field(field_name, content, filename=filename)

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url, data=form, timeout=aiohttp.ClientTimeout(total=_UPLOAD_TIMEOUT)
            ) as response:
                payload = await response.json(content_type=None)
                if response.status >= 400 or not payload.get("ok"):
                    raise RelaySendError(
                        f"{response.status}: {payload.get('description', payload)}"
                    )
    except RelaySendError:
        raise
    except Exception as e:
        raise RelaySendError(f"{type(e).__name__}: {e}") from e


@app.get("/health")
async def health() -> dict[str, str]:
    """Проверка живости сервиса (без аутентификации)."""
    return {"status": "ok"}


@app.post("/send-media")
async def send_media(
    file: UploadFile,
    token: str = Form(..., description="Токен Telegram-бота"),
    chat_id: str = Form(..., description="Telegram ID клиента"),
    media_type: str = Form(..., description="picture/voice/video/file"),
    caption: str | None = Form(default=None),
    x_relay_secret: str | None = Header(default=None),
) -> dict[str, Any]:
    """
    Принять файл от основного бэкенда и отправить его в Telegram Bot API.

    Пробует методы из _MEDIA_METHODS по порядку, пока один не сработает.
    """
    _check_secret(x_relay_secret)

    methods = _MEDIA_METHODS.get(media_type)
    if not methods:
        raise HTTPException(status_code=400, detail=f"Unsupported media_type: {media_type}")

    content = await file.read()
    filename = file.filename or "file.bin"

    last_error = ""
    for method, field_name in methods:
        try:
            await _call_telegram(
                token=token,
                method=method,
                field_name=field_name,
                chat_id=chat_id,
                content=content,
                filename=filename,
                caption=caption,
            )
            logger.info(
                "Media sent: chat_id=%s, method=%s, %d bytes", chat_id, method, len(content)
            )
            return {"ok": True, "method": method}
        except RelaySendError as e:
            last_error = str(e)
            logger.warning("Method %s failed for chat_id=%s: %s", method, chat_id, e)

    raise HTTPException(
        status_code=502,
        detail=last_error or f"All methods failed for media_type={media_type}",
    )
