"""FastAPI application."""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import amojo_webhook, health, media, salebot_webhook
from app.db.storage import get_conversation_storage
from app.utils.token_manager import TokenManager
from app.workers.queue import close_queue

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Управление жизненным циклом приложения.

    Выполняется при старте и остановке приложения.
    """
    logger.info("=" * 60)
    logger.info("Starting Salebot ↔ amoCRM Integration Service")
    logger.info("=" * 60)

    # Создаём storage для инициализации БД (будет закрыт после)
    storage = get_conversation_storage()
    
    try:
        await storage.init_database()
        logger.info("✓ Database initialized successfully")
    except Exception as e:
        logger.error("✗ Failed to initialize database: %s", e)
        logger.warning("  Application will continue, but may fail on first request")
    finally:
        # Закрываем временный storage
        await storage.close()

    # Инициализируем OAuth2 токены (если нужны)
    try:
        token_manager = TokenManager()
        await token_manager.get_access_token()
        logger.info("✓ Token manager initialized")
    except Exception as e:
        logger.error("✗ Failed to initialize token manager: %s", e, exc_info=True)
        logger.warning("  Chat linking may fail")

    logger.info("✓ Service started successfully")
    logger.info("=" * 60)

    yield

    # Shutdown
    logger.info("=" * 60)
    logger.info("Shutting down service...")
    logger.info("=" * 60)

    # Закрываем Redis
    try:
        await close_queue()
        logger.info("✓ Redis connection closed")
    except Exception as e:
        logger.error("✗ Failed to close Redis: %s", e)

    logger.info("✓ Service stopped")
    logger.info("=" * 60)


# Создаем приложение с lifespan
app = FastAPI(
    title="Salebot ↔ amoCRM Integration",
    description="Интеграция Salebot.pro с amoCRM через amojo (с Redis очередями)",
    version="2.0.0",
    lifespan=lifespan,
)

# Подключаем роутеры
app.include_router(health.router, tags=["health"])
app.include_router(salebot_webhook.router, tags=["webhooks"])
app.include_router(amojo_webhook.router, tags=["webhooks"])
app.include_router(media.router, tags=["media"])
