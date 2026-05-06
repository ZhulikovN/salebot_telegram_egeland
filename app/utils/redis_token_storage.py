"""Хранилище токенов AmoCRM в Redis с распределенной блокировкой."""
import asyncio
import logging
import time

from app.utils.redis_connection import get_redis

logger = logging.getLogger(__name__)


class RedisTokenStorage:
    """
    Асинхронное хранилище токенов AmoCRM в Redis.

    Поддерживает:
    - Распределенную блокировку при обновлении токенов
    - Работу с несколькими воркерами одновременно
    - Автоматическое снятие устаревших блокировок

    Использует общий Redis singleton вместо собственного клиента.
    """

    def __init__(self) -> None:
        """Инициализация хранилища токенов."""
        self.access_key = "amocrm:token:access"
        self.refresh_key = "amocrm:token:refresh"
        self.lock_key = "amocrm:token:refresh_lock"
        self.lock_ttl = 30

    async def get_access_token(self) -> str | None:
        """Получить access token из Redis."""
        redis = get_redis()
        token = await redis.get(self.access_key)
        if token:
            logger.debug("Access token retrieved from Redis: %s...", token[:20])
        return token

    async def get_refresh_token(self) -> str | None:
        """Получить refresh token из Redis."""
        redis = get_redis()
        token = await redis.get(self.refresh_key)
        if token:
            logger.debug("Refresh token retrieved from Redis: %s...", token[:20])
        return token

    async def save_tokens(self, access_token: str, refresh_token: str) -> None:
        """
        Сохранить оба токена в Redis атомарно.

        Args:
            access_token: Access token
            refresh_token: Refresh token
        """
        redis = get_redis()

        async with redis.pipeline(transaction=True) as pipe:
            await pipe.set(self.access_key, access_token)
            await pipe.set(self.refresh_key, refresh_token)
            await pipe.execute()

        logger.info(
            "Tokens saved to Redis: access=%s..., refresh=%s...",
            access_token[:20],
            refresh_token[:20],
        )

    async def try_acquire_refresh_lock(self, timeout: int = 30) -> bool:
        """
        Попытка захватить распределенную блокировку для refresh токена.

        Args:
            timeout: Максимальное время ожидания блокировки в секундах

        Returns:
            True если блокировка захвачена, False если не удалось
        """
        redis = get_redis()

        end_time = time.time() + timeout
        worker_id = f"worker_{id(self)}"

        while time.time() < end_time:
            acquired = await redis.set(
                self.lock_key, worker_id, nx=True, ex=self.lock_ttl
            )

            if acquired:
                logger.info("Refresh lock acquired by %s", worker_id)
                return True

            ttl = await redis.ttl(self.lock_key)
            if ttl == -1:
                logger.warning("Lock exists without TTL, resetting it")
                await redis.delete(self.lock_key)
                continue

            logger.debug("Lock held by another worker, waiting... (TTL: %ds)", ttl)
            await asyncio.sleep(0.5)

        logger.warning("Failed to acquire refresh lock after %ds timeout", timeout)
        return False

    async def release_refresh_lock(self) -> None:
        """Освободить блокировку refresh."""
        redis = get_redis()
        await redis.delete(self.lock_key)
        logger.info("Refresh lock released")


_storage_instance: RedisTokenStorage | None = None


def get_redis_token_storage() -> RedisTokenStorage:
    """Получить глобальный экземпляр Redis storage (singleton)."""
    global _storage_instance
    if _storage_instance is None:
        _storage_instance = RedisTokenStorage()
    return _storage_instance
