"""Pydantic модели для Salebot webhook."""
from pydantic import BaseModel, Field


class SalebotClient(BaseModel):
    """Данные клиента из Salebot."""

    id: int = Field(description="client_id в Salebot (НЕ platform_id!)")
    recepient: str = Field(description="platform_id - TG ID клиента")
    name: str = Field(description="Имя клиента")
    group: str = Field(description="Название бота")
    variables: dict[str, str | None] = Field(
        default_factory=dict, description="Переменные клиента (tg_username, class и т.д.)"
    )


class SalebotWebhook(BaseModel):
    """
    Webhook от Salebot при новом сообщении.

    Пример:
    {
        "id": 22563045582,
        "client": {
            "id": 836058546,
            "recepient": "6253651200",
            "name": "Имя Фамилия",
            "group": "ElAuthBot",
            "variables": {
                "tg_username": "username",
                "class": "ЕГЭ"
            }
        },
        "message": "Привет",
        "attachments": null,
        "project_id": 424757,
        "is_input": 1,
        "delivered": 1,
        "internal_id": "123456789"
    }
    """

    id: int = Field(description="ID сообщения в Salebot")
    client: SalebotClient = Field(description="Данные клиента")
    message: str | None = Field(default=None, description="Текст сообщения (null при медиа без подписи)")
    attachments: list | None = Field(default=None, description="Вложения (если есть)")
    project_id: int = Field(description="ID проекта в Salebot")
    is_input: int = Field(description="1 = от клиента, 0 = от бота")
    delivered: int = Field(description="Статус доставки")
    internal_id: str | None = Field(
        default=None, description="ID сообщения в Telegram (может быть NULL)"
    )

    @property
    def is_from_client(self) -> bool:
        """Проверить, что сообщение от клиента (а не от бота)."""
        return self.is_input == 1

    @property
    def platform_id(self) -> str:
        """Получить platform_id (TG ID клиента)."""
        return self.client.recepient

    @property
    def salebot_client_id(self) -> int:
        """Получить client.id для отправки ответов."""
        return self.client.id

    @property
    def bot_name(self) -> str:
        """Получить название бота."""
        return self.client.group

    @property
    def tg_username(self) -> str | None:
        """Получить Telegram username из variables."""
        return self.client.variables.get("tg_username")

    @property
    def utm_data(self) -> dict[str, str | None]:
        """Получить UTM-метки из variables клиента."""
        v = self.client.variables
        return {
            "utm_source": v.get("utm_source"),
            "utm_medium": v.get("utm_medium"),
            "utm_campaign": v.get("utm_campaign"),
            "utm_term": v.get("utm_term"),
            "utm_content": v.get("utm_content"),
        }

