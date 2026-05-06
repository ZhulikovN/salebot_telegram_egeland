"""Менеджер диалогов Salebot ↔ amoCRM."""
import logging
from uuid import uuid4

from app.db.storage import Conversation, get_conversation_storage
from app.services.amocrm_client import AmoCRMClient
from app.services.amojo_client import AmojoClient
from app.services.salebot_client import SalebotClient

logger = logging.getLogger(__name__)


class ConversationManager:
    """
    Менеджер для управления диалогами между Salebot и amoCRM.

    Объединяет логику:
    - Создания/поиска контактов и сделок
    - Создания чатов в amojo
    - Отправки сообщений в обе стороны
    - Сохранения маппинга в БД
    """

    def __init__(self) -> None:
        """Инициализация менеджера."""
        self.storage = get_conversation_storage()
        self.amocrm = AmoCRMClient()
        self.amojo = AmojoClient()
        self.salebot = SalebotClient()

    async def handle_salebot_message(
        self,
        platform_id: str,
        bot_name: str,
        salebot_client_id: int,
        client_name: str,
        message_text: str,
        tg_username: str | None = None,
    ) -> str | None:
        """
        Обработать входящее сообщение от Salebot.

        Алгоритм:
        1. Найти существующий диалог по platform_id (только TG ID, без bot_name)
        2. Если диалога нет - игнорировать (контакты и сделки создаются только из таблиц)
        3. Если диалог есть - отправить сообщение в amojo
        4. Обновить счетчик сообщений

        Args:
            platform_id: Telegram ID клиента
            bot_name: Название бота (не используется для поиска)
            salebot_client_id: client.id из Salebot для ответов
            client_name: Имя клиента
            message_text: Текст сообщения
            tg_username: Telegram username

        Returns:
            conversation_id чата или None если диалог не найден
        """
        # 1. Ищем существующий диалог в БД (только по platform_id)
        conversation = await self.storage.get_by_platform_id(platform_id)

        if not conversation:
            logger.info(
                "Conversation not found for platform_id=%s - message ignored (contacts/leads created only from sheets)",
                platform_id,
            )
            return None

        logger.info(
            "Found existing conversation: %s (bot_name=%s)",
            conversation.conversation_id,
            conversation.bot_name,
        )

        # 2. Отправляем сообщение в amojo (всегда silent=True)
        await self.amojo.send_incoming_message(
            conversation_id=conversation.conversation_id,
            msgid=f"salebot:{uuid4().hex}",
            sender_id=f"tg:{platform_id}",
            sender_name=client_name,
            text=message_text,
            silent=True,
        )
        logger.info(
            "Message sent to amojo: %s (silent=True)",
            conversation.conversation_id,
        )

        # 3. Обновляем счетчик
        await self.storage.increment_message_count(conversation.conversation_id)

        return conversation.conversation_id

    async def handle_amojo_message(
        self,
        conversation_id: str,
        message_text: str,
    ) -> None:
        """
        Обработать ответ менеджера из amoCRM.

        Args:
            conversation_id: UUID чата
            message_text: Текст сообщения от менеджера

        Raises:
            ValueError: Если диалог не найден
        """
        # 1. Находим диалог
        conversation = await self.storage.get_by_conversation_id(conversation_id)
        if not conversation:
            raise ValueError(f"Conversation not found: {conversation_id}")

        # 2. Отправляем сообщение в Salebot
        await self.salebot.send_message(
            client_id=conversation.salebot_client_id,
            message=message_text,
        )
        logger.info("Message sent to Salebot: client_id=%s", conversation.salebot_client_id)

        # 3. Обновляем счетчик
        logger.debug("Starting increment_message_count for %s", conversation_id)
        await self.storage.increment_message_count(conversation_id)
        logger.debug("increment_message_count completed for %s", conversation_id)


    async def close(self) -> None:
        """Закрыть все соединения."""
        await self.amocrm.close()
        await self.salebot.close()
        await self.storage.close()
        logger.info("ConversationManager connections closed")
