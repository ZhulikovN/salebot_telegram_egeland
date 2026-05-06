"""Хранилище для маппинга диалогов Salebot ↔ amoCRM."""
import logging
from datetime import datetime

from sqlalchemy import Index, Integer, String, Text, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.settings import settings

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    """Базовый класс для всех моделей."""

    pass


class Conversation(Base):
    """
    Модель диалога Salebot ↔ amoCRM.

    Хранит связь между:
    - (platform_id, bot_name) и conversation_id (UUID чата в amojo)
    - Контактом и сделкой в amoCRM
    - salebot_client_id для отправки ответов

    Уникальность: одна запись на пару (клиент + бот).
    """

    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # Идентификаторы
    conversation_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    salebot_client_id: Mapped[int] = mapped_column(Integer, nullable=False)
    platform_id: Mapped[str] = mapped_column(String(255), nullable=False)
    contact_id: Mapped[int] = mapped_column(Integer, nullable=False)
    lead_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Данные клиента
    client_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    tg_username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    bot_name: Mapped[str] = mapped_column(String(255), nullable=False)

    # Метаданные
    first_message_at: Mapped[datetime] = mapped_column(nullable=False)
    last_message_at: Mapped[datetime] = mapped_column(nullable=False)
    messages_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Временные метки
    created_at: Mapped[datetime] = mapped_column(nullable=False)
    updated_at: Mapped[datetime] = mapped_column(nullable=False)

    __table_args__ = (
        Index("idx_conversation_id", "conversation_id"),
        Index("idx_platform_id", "platform_id"),
        Index("idx_salebot_client_id", "salebot_client_id"),
        Index("idx_contact_id", "contact_id"),
        # Уникальность: один диалог на пару (клиент + бот)
        Index("idx_platform_bot", "platform_id", "bot_name", unique=True),
    )


class ConversationStorage:
    """Хранилище для связки диалогов Salebot и чатов amoCRM."""

    def __init__(self) -> None:
        """Инициализация хранилища с собственным engine."""
        self.engine = create_async_engine(
            settings.postgres_url,
            echo=False,
            pool_size=10,
            max_overflow=20,
            pool_timeout=60,
            pool_pre_ping=True,
            pool_recycle=3600,
        )
        self.AsyncSessionLocal = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )

    async def init_database(self) -> None:
        """Создать таблицы если их нет."""
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("Database tables created")

    async def get_by_platform_id(
        self, platform_id: str, bot_name: str
    ) -> "Conversation | None":
        """
        Найти диалог по паре (platform_id, bot_name).

        Args:
            platform_id: Telegram ID клиента
            bot_name: Название бота

        Returns:
            Модель диалога или None
        """
        async with self.AsyncSessionLocal() as session:
            result = await session.execute(
                select(Conversation).where(
                    Conversation.platform_id == platform_id,
                    Conversation.bot_name == bot_name,
                )
            )
            return result.scalars().first()

    async def get_by_conversation_id(self, conversation_id: str) -> "Conversation | None":
        """
        Найти диалог по conversation_id (UUID чата).

        Args:
            conversation_id: UUID чата в amojo

        Returns:
            Модель диалога или None
        """
        async with self.AsyncSessionLocal() as session:
            result = await session.execute(
                select(Conversation).where(Conversation.conversation_id == conversation_id)
            )
            return result.scalars().first()

    async def create_conversation(
        self,
        conversation_id: str,
        salebot_client_id: int,
        platform_id: str,
        contact_id: int,
        lead_id: int | None,
        client_name: str,
        tg_username: str | None,
        bot_name: str,
    ) -> "Conversation":
        """
        Создать новую запись о диалоге.

        Если запись с парой (platform_id, bot_name) уже существует — вернуть её.

        Args:
            conversation_id: UUID чата в amojo
            salebot_client_id: client.id из Salebot webhook
            platform_id: TG ID клиента
            contact_id: ID контакта в amoCRM
            lead_id: ID сделки в amoCRM
            client_name: Имя клиента
            tg_username: Telegram username (без @)
            bot_name: Название бота

        Returns:
            Созданная или существующая модель диалога
        """
        existing = await self.get_by_platform_id(platform_id, bot_name)
        if existing:
            logger.info(
                "Conversation already exists: platform_id=%s, bot_name=%s",
                platform_id,
                bot_name,
            )
            return existing

        now = datetime.now()

        conversation = Conversation(
            conversation_id=conversation_id,
            salebot_client_id=salebot_client_id,
            platform_id=platform_id,
            contact_id=contact_id,
            lead_id=lead_id,
            client_name=client_name,
            tg_username=tg_username,
            bot_name=bot_name,
            first_message_at=now,
            last_message_at=now,
            messages_count=0,
            created_at=now,
            updated_at=now,
        )

        async with self.AsyncSessionLocal() as session:
            session.add(conversation)
            await session.commit()
            await session.refresh(conversation)

        logger.info(
            "Conversation created: id=%s, conversation_id=%s, platform_id=%s, bot_name=%s",
            conversation.id,
            conversation_id,
            platform_id,
            bot_name,
        )

        return conversation

    async def increment_message_count(self, conversation_id: str) -> None:
        """
        Увеличить счетчик сообщений и обновить last_message_at.

        Args:
            conversation_id: UUID чата
        """
        now = datetime.now()

        async with self.AsyncSessionLocal() as session:
            result = await session.execute(
                select(Conversation).where(Conversation.conversation_id == conversation_id)
            )
            conversation = result.scalars().first()

            if conversation:
                conversation.messages_count += 1
                conversation.last_message_at = now
                conversation.updated_at = now
                await session.commit()

                logger.debug(
                    "Message count incremented: %s (count=%d)",
                    conversation_id,
                    conversation.messages_count,
                )

    async def close(self) -> None:
        """Закрыть все соединения с БД."""
        await self.engine.dispose()
        logger.info("Database connections closed")


def get_conversation_storage() -> ConversationStorage:
    """
    Создать новый экземпляр хранилища.

    Каждый воркер/процесс должен иметь свой экземпляр ConversationStorage
    с собственным connection pool.

    Returns:
        Новый экземпляр ConversationStorage с отдельным connection pool
    """
    return ConversationStorage()
