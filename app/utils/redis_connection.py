"""Синглтон Redis соединение для всего приложения."""

import logging

import redis.asyncio as aioredis

from app.settings import settings

logger = logging.getLogger(__name__)


class RedisConnection:
    """
    Singleton Redis клиент.

    Обеспечивает единое соединение для всего приложения:
    - Rate limiting для AmoCRM API
    - Блокировки
    - Кэширование
    """

    _instance: aioredis.Redis | None = None

    @classmethod
    def get_instance(cls) -> aioredis.Redis:
        """
        Получить единственный экземпляр Redis клиента.

        Returns:
            Redis клиент
        """
        if cls._instance is None:
            cls._instance = aioredis.from_url(
                settings.redis_url,
                decode_responses=True,
                max_connections=50,
                socket_connect_timeout=5,
                socket_timeout=30,
            )
            logger.info("Redis connection pool created")

        return cls._instance

    @classmethod
    async def close(cls) -> None:
        """Закрыть Redis соединение."""
        if cls._instance:
            await cls._instance.aclose()
            cls._instance = None
            logger.info("Redis connection closed")


def get_redis() -> aioredis.Redis:
    """
    Хелпер для получения Redis клиента.

    Returns:
        Redis клиент
    """
    return RedisConnection.get_instance()
