"""FastAPI application."""
import logging

from fastapi import FastAPI

from app.api import amojo_webhook, health

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Salebot ↔ amoCRM Integration (egeland)",
    description="Интеграция Salebot.pro с amoCRM",
    version="1.0.0",
)

app.include_router(health.router, tags=["health"])
app.include_router(amojo_webhook.router, tags=["webhooks"])
