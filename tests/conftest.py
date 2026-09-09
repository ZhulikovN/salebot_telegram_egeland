import pytest

from app.utils.redis_connection import RedisConnection


@pytest.fixture(autouse=True)
async def reset_redis_connection():
    RedisConnection._instance = None
    yield
    if RedisConnection._instance is not None:
        try:
            await RedisConnection._instance.aclose()
        except Exception:
            pass
        RedisConnection._instance = None
