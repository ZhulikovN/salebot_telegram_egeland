"""Менеджер OAuth2 токенов AmoCRM с Redis хранилищем."""

import asyncio
import logging

import aiohttp

from app.settings import settings
from app.utils.redis_token_storage import get_redis_token_storage

logger = logging.getLogger(__name__)


class TokenManager:
    """
    Менеджер OAuth2 токенов AmoCRM.

    Управляет access/refresh токенами для специальных запросов,
    требующих OAuth2 авторизацию (не long-lived токен).
    """

    def __init__(self) -> None:
        """Инициализация менеджера токенов."""
        self.storage = get_redis_token_storage()

    async def get_access_token(self) -> str:
        """
        Получить актуальный access token.

        Если токена нет - инициализирует через auth_code.
        Если токен истек - обновляет через refresh_token.

        Returns:
            Access token
        """
        token = await self.storage.get_access_token()

        if token:
            return token

        # Нет токена - инициализируем
        logger.info("No access token found, initializing...")
        await self._initialize_tokens()

        token = await self.storage.get_access_token()
        if not token:
            raise RuntimeError("Failed to initialize access token")

        return token

    async def refresh_access_token(self) -> str:
        """
        Обновить access token через refresh token с распределенной блокировкой.

        Returns:
            Новый access token

        Raises:
            RuntimeError: Если не удалось обновить токен
        """
        # Пытаемся захватить блокировку
        acquired = await self.storage.try_acquire_refresh_lock(timeout=30)

        if not acquired:
            # Не удалось захватить блокировку - ждем пока другой воркер обновит
            logger.info("Waiting for another worker to refresh token...")
            await asyncio.sleep(2)

            token = await self.storage.get_access_token()
            if token:
                return token

            raise RuntimeError("Failed to get refreshed token from another worker")

        try:
            # Захватили блокировку - обновляем токен
            refresh_token = await self.storage.get_refresh_token()

            if not refresh_token:
                logger.error("No refresh token available")
                raise RuntimeError("No refresh token available")

            logger.info("Refreshing access token...")

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"https://{settings.AMOCRM_SUBDOMAIN}.amocrm.ru/oauth2/access_token",
                    json={
                        "client_id": settings.AMO_CLIENT_ID,
                        "client_secret": settings.AMO_CLIENT_SECRET,
                        "grant_type": "refresh_token",
                        "refresh_token": refresh_token,
                        "redirect_uri": settings.AMO_REDIRECT_URI,
                    },
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as response:
                    if response.status != 200:
                        text = await response.text()
                        logger.error("Token refresh failed: %s %s", response.status, text)
                        raise RuntimeError(f"Token refresh failed: {response.status}")

                    data = await response.json()
                    new_access_token = data["access_token"]
                    new_refresh_token = data["refresh_token"]

                    await self.storage.save_tokens(new_access_token, new_refresh_token)

                    logger.info("Access token refreshed successfully")
                    return new_access_token

        finally:
            await self.storage.release_refresh_lock()

    async def _initialize_tokens(self) -> None:
        """
        Первичная инициализация токенов через auth_code.

        Raises:
            RuntimeError: Если инициализация не удалась
        """
        logger.info("Initializing tokens via auth_code...")

        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"https://{settings.AMOCRM_SUBDOMAIN}.amocrm.ru/oauth2/access_token",
                json={
                    "client_id": settings.AMO_CLIENT_ID,
                    "client_secret": settings.AMO_CLIENT_SECRET,
                    "grant_type": "authorization_code",
                    "code": settings.AMO_AUTH_CODE,
                    "redirect_uri": settings.AMO_REDIRECT_URI,
                },
                timeout=aiohttp.ClientTimeout(total=10),
            ) as response:
                if response.status != 200:
                    text = await response.text()
                    logger.error(
                        "Token initialization failed: %s %s", response.status, text
                    )
                    raise RuntimeError(
                        f"Token initialization failed: {response.status}"
                    )

                data = await response.json()
                access_token = data["access_token"]
                refresh_token = data["refresh_token"]

                await self.storage.save_tokens(access_token, refresh_token)

                logger.info("Tokens initialized and saved to Redis")


_token_manager_instance: TokenManager | None = None


def get_token_manager() -> TokenManager:
    """Получить глобальный экземпляр TokenManager (singleton)."""
    global _token_manager_instance
    if _token_manager_instance is None:
        _token_manager_instance = TokenManager()
    return _token_manager_instance
