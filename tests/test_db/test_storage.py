"""Тесты для ConversationStorage с PostgreSQL."""
#  poetry run pytest tests/test_db/test_storage.py -v -s --log-cli-level=INFO

from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from app.db.storage import Conversation, ConversationStorage


@pytest.fixture
async def storage() -> ConversationStorage:
    """Создать хранилище для тестов."""
    storage = ConversationStorage()
    await storage.init_database()
    yield storage
    # Cleanup: закрываем соединения после каждого теста
    await storage.close()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_init_database(storage: ConversationStorage) -> None:
    """Тест инициализации БД."""
    # Таблицы должны быть созданы
    await storage.init_database()
    print("\n✓ Database tables initialized")


@pytest.mark.asyncio
@pytest.mark.integration
async def test_create_and_get_conversation(storage: ConversationStorage) -> None:
    """Тест создания и получения диалога."""
    conversation_id = str(uuid4())
    platform_id = f"test_{uuid4().hex[:8]}"
    bot_name = f"TestBot_{uuid4().hex[:6]}"

    # Создаем
    conversation = await storage.create_conversation(
        conversation_id=conversation_id,
        salebot_client_id=12345,
        platform_id=platform_id,
        contact_id=46370921,
        lead_id=12345678,
        client_name="Test User",
        tg_username="testuser",
        bot_name=bot_name,
    )

    assert conversation.id > 0
    assert conversation.conversation_id == conversation_id
    assert conversation.platform_id == platform_id
    print(f"\n✓ Conversation created: id={conversation.id}")

    # Получаем по platform_id
    found = await storage.get_by_platform_id(platform_id, bot_name)
    assert found is not None
    assert found.conversation_id == conversation_id
    assert found.platform_id == platform_id
    assert found.contact_id == 46370921
    assert found.lead_id == 12345678
    assert found.client_name == "Test User"
    assert found.tg_username == "testuser"
    print(f"✓ Found by platform_id: {found.conversation_id}")

    # Получаем по conversation_id
    found = await storage.get_by_conversation_id(conversation_id)
    assert found is not None
    assert found.platform_id == platform_id
    assert found.salebot_client_id == 12345
    print(f"✓ Found by conversation_id: {found.conversation_id}")


@pytest.mark.asyncio
@pytest.mark.integration
async def test_get_nonexistent_conversation(storage: ConversationStorage) -> None:
    """Тест получения несуществующего диалога."""
    conversation = await storage.get_by_platform_id(
        f"nonexist_{uuid4().hex}", f"NonExistentBot_{uuid4().hex[:6]}"
    )
    assert conversation is None
    print("\n✓ Nonexistent conversation returns None")


@pytest.mark.asyncio
@pytest.mark.integration
async def test_increment_message_count(storage: ConversationStorage) -> None:
    """Тест увеличения счетчика сообщений."""
    conversation_id = str(uuid4())
    platform_id = f"test_{uuid4().hex[:8]}"
    bot_name = f"TestBot2_{uuid4().hex[:6]}"

    # Создаем
    conversation = await storage.create_conversation(
        conversation_id=conversation_id,
        salebot_client_id=67890,
        platform_id=platform_id,
        contact_id=47353447,
        lead_id=None,
        client_name="Another User",
        tg_username=None,
        bot_name=bot_name,
    )

    # Проверяем начальный счетчик
    assert conversation.messages_count == 0
    print(f"\n✓ Initial message count: {conversation.messages_count}")

    # Увеличиваем
    await storage.increment_message_count(conversation_id)
    await storage.increment_message_count(conversation_id)

    # Проверяем
    updated = await storage.get_by_conversation_id(conversation_id)
    assert updated is not None
    assert updated.messages_count == 2
    print(f"✓ Message count incremented: {updated.messages_count}")


@pytest.mark.asyncio
@pytest.mark.integration
async def test_unique_platform_bot_constraint(storage: ConversationStorage) -> None:
    """Тест уникальности platform_id + bot_name."""
    platform_id = f"test_{uuid4().hex[:8]}"
    bot_name = f"UniqueBot_{uuid4().hex[:6]}"

    # Создаем первый раз
    await storage.create_conversation(
        conversation_id=str(uuid4()),
        salebot_client_id=11111,
        platform_id=platform_id,
        contact_id=12345,
        lead_id=None,
        client_name="User 1",
        tg_username=None,
        bot_name=bot_name,
    )
    print(f"\n✓ First conversation created: {platform_id} + {bot_name}")

    # Пытаемся создать дубликат
    try:
        await storage.create_conversation(
            conversation_id=str(uuid4()),
            salebot_client_id=22222,
            platform_id=platform_id,  # ТОТ ЖЕ
            contact_id=67890,
            lead_id=None,
            client_name="User 2",
            tg_username=None,
            bot_name=bot_name,  # ТОТ ЖЕ
        )
        assert False, "Should raise unique constraint violation"
    except IntegrityError as e:
        assert "duplicate key value" in str(e) or "unique constraint" in str(e).lower()
        print(f"✓ Unique constraint works: duplicate prevented")


@pytest.mark.asyncio
@pytest.mark.integration
async def test_conversation_model_properties(storage: ConversationStorage) -> None:
    """Тест свойств модели Conversation."""
    conversation_id = str(uuid4())
    platform_id = f"test_{uuid4().hex[:8]}"
    bot_name = f"PropertiesBot_{uuid4().hex[:6]}"

    # Создаем
    conversation = await storage.create_conversation(
        conversation_id=conversation_id,
        salebot_client_id=99999,
        platform_id=platform_id,
        contact_id=11111,
        lead_id=22222,
        client_name="Props User",
        tg_username="propsuser",
        bot_name=bot_name,
    )

    # Проверяем все поля
    assert conversation.conversation_id == conversation_id
    assert conversation.salebot_client_id == 99999
    assert conversation.platform_id == platform_id
    assert conversation.contact_id == 11111
    assert conversation.lead_id == 22222
    assert conversation.client_name == "Props User"
    assert conversation.tg_username == "propsuser"
    assert conversation.bot_name == bot_name
    assert conversation.messages_count == 0
    assert conversation.first_message_at is not None
    assert conversation.last_message_at is not None
    assert conversation.created_at is not None
    assert conversation.updated_at is not None

    print(f"\n✓ All Conversation properties validated")
    print(f"  - conversation_id: {conversation.conversation_id}")
    print(f"  - platform_id: {conversation.platform_id}")
    print(f"  - contact_id: {conversation.contact_id}")
    print(f"  - lead_id: {conversation.lead_id}")
    print(f"  - messages_count: {conversation.messages_count}")
