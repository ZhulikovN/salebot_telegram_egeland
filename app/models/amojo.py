"""Pydantic модели для amojo webhook."""
from pydantic import BaseModel, Field


class AmojoMessage(BaseModel):
    """
    Сообщение в amojo.
    
    Пример из логов:
    {
        "id": "605d655c-d5d4-4c7f-ba51-1b39a81974f5",
        "type": "text",
        "text": "ffff",
        "markup": null,
        "tag": "",
        "media": "",
        "thumbnail": "",
        "file_name": "",
        "file_size": 0
    }
    """

    id: str = Field(description="UUID сообщения")
    type: str = Field(description="Тип сообщения (text, file, etc)")
    text: str = Field(description="Текст сообщения")
    markup: str | None = Field(default=None, description="Разметка сообщения")
    tag: str | None = Field(default=None, description="Тег сообщения")
    media: str | None = Field(default=None, description="Медиа URL")
    thumbnail: str | None = Field(default=None, description="Thumbnail URL")
    file_name: str | None = Field(default=None, description="Имя файла")
    file_size: int | None = Field(default=None, description="Размер файла")


class AmojoUser(BaseModel):
    """
    Отправитель/получатель сообщения.
    
    Пример (менеджер):
    {"id": "8f01dd65-2b1b-4576-af49-40fcf331b059", "name": "Разработчик", "client_id": null}
    
    Пример (клиент):
    {"id": "c5712f61-06d6-47d2-980b-2b0188cd704c", "name": "Nikita Zhulikov", "client_id": "tg"}
    """

    id: str = Field(description="UUID пользователя")
    name: str | None = Field(default=None, description="Имя пользователя")
    client_id: str | None = Field(default=None, description="client_id (null для менеджера, 'tg' для клиента)")


class AmojoConversation(BaseModel):
    """
    Информация о диалоге.
    
    Пример:
    {"id": "45cdab1b-0d42-4e8a-bf84-5d47350ee0d9", "client_id": "2dbaf26d-de43-4dfd-b1f0-024c1e37e945"}
    """

    id: str = Field(description="UUID чата в amojo")
    client_id: str = Field(description="Client ID чата")


class AmojoMessagePayload(BaseModel):
    """
    Payload сообщения от amoCRM.

    Реальный пример из логов:
    {
        "receiver": {"id": "c5712f61-...", "name": "Nikita Zhulikov", "client_id": "tg"},
        "sender": {"id": "8f01dd65-...", "name": "Разработчик", "client_id": null},
        "conversation": {"id": "45cdab1b-...", "client_id": "2dbaf26d-..."},
        "timestamp": 1768921906,
        "msec_timestamp": 1768921906033,
        "message": {"id": "605d655c-...", "type": "text", "text": "ffff", ...}
    }
    """

    sender: AmojoUser = Field(description="Отправитель")
    receiver: AmojoUser = Field(description="Получатель")
    conversation: AmojoConversation = Field(description="Диалог")
    message: AmojoMessage = Field(description="Сообщение")
    timestamp: int = Field(description="Unix timestamp в секундах")
    msec_timestamp: int | None = Field(default=None, description="Unix timestamp в миллисекундах")

    @property
    def conversation_id(self) -> str:
        """
        Получить ID диалога для поиска в БД.
        
        conversation.id — внутренний ID amoCRM (всегда разный)
        conversation.client_id — наш UUID, который мы отправили при создании чата
        """
        return self.conversation.client_id

    @property
    def is_from_manager(self) -> bool:
        """
        Проверить, что сообщение от менеджера (а не от клиента).

        Менеджер: sender.client_id = null
        Клиент: sender.client_id = "tg"
        """
        return self.sender.client_id is None


class AmojoWebhook(BaseModel):
    """
    Webhook от amoCRM при новом сообщении в чате.

    Реальный пример из логов:
    {
        "account_id": "3fe38ca5-a7d3-4643-85c4-8200924e581f",
        "time": 1768921906,
        "message": {
            "sender": {...},
            "receiver": {...},
            "conversation": {...},
            "message": {...},
            "timestamp": 1768921906,
            "msec_timestamp": 1768921906033
        }
    }
    """

    account_id: str = Field(description="UUID аккаунта amoCRM")
    time: int = Field(description="Unix timestamp события")
    message: AmojoMessagePayload = Field(description="Данные сообщения")
