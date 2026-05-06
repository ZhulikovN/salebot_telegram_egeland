"""
Полный интеграционный тест: первое сообщение клиента.

Симулирует реальный сценарий:
1. Клиент пишет боту в Salebot
2. ConversationManager находит/создаёт контакт в AMO
3. Создаётся сделка
4. Создаётся чат в amojo и привязывается к контакту
5. Сообщение уходит в amojo

Запуск:
    poetry run pytest tests/test_full_flow.py -v -s --log-cli-level=INFO

ВНИМАНИЕ: тест реально создаёт данные в AMO.
"""

import logging
from uuid import uuid4

import pytest

from app.services.conversation_manager import ConversationManager

logger = logging.getLogger(__name__)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_first_message_full_flow() -> None:
    """
    Полный поток первого сообщения: контакт → сделка → чат → сообщение.

    Использует уникальный TG ID чтобы каждый запуск создавал новые данные.
    """
    # Уникальные данные для теста (каждый запуск — новый клиент)
    unique_suffix = uuid4().hex[:8]
    platform_id = f"7000{unique_suffix[:6]}"   # уникальный TG ID (не пересечётся с реальными)
    bot_name = "test_el_salebot"
    salebot_client_id = 999000001                # тестовый client_id Salebot (не используется для отправки)
    client_name = f"Тест Пользователь {unique_suffix}"
    tg_username = f"test_user_{unique_suffix}"
    message_text = "pytest: первое тестовое сообщение"

    logger.info("=" * 60)
    logger.info("Starting full flow test")
    logger.info("platform_id:       %s", platform_id)
    logger.info("bot_name:          %s", bot_name)
    logger.info("client_name:       %s", client_name)
    logger.info("tg_username:       %s", tg_username)
    logger.info("=" * 60)

    manager = ConversationManager()

    try:
        # Инициализируем БД
        await manager.storage.init_database()

        # --- Шаг 1: первое сообщение ---
        # Диалога в БД нет → должен создать контакт + сделку + чат → отправить сообщение
        conversation_id = await manager.handle_salebot_message(
            platform_id=platform_id,
            bot_name=bot_name,
            salebot_client_id=salebot_client_id,
            client_name=client_name,
            message_text=message_text,
            tg_username=tg_username,
        )

        assert conversation_id is not None, (
            "conversation_id должен быть заполнен после первого сообщения"
        )

        logger.info("conversation_id: %s", conversation_id)

        # --- Шаг 2: проверяем что запись в БД создалась ---
        conversation = await manager.storage.get_by_platform_id(platform_id, bot_name)

        assert conversation is not None, "Запись в БД не создалась"
        assert conversation.platform_id == platform_id
        assert conversation.bot_name == bot_name
        assert conversation.contact_id > 0, "contact_id должен быть заполнен"
        assert conversation.lead_id is not None and conversation.lead_id > 0, (
            "lead_id должен быть заполнен"
        )
        assert conversation.salebot_client_id == salebot_client_id
        assert conversation.tg_username == tg_username
        assert conversation.messages_count == 1

        logger.info("contact_id:   %s", conversation.contact_id)
        logger.info("lead_id:      %s", conversation.lead_id)
        logger.info("messages_count: %s", conversation.messages_count)

        # --- Шаг 3: повторное сообщение — не должно создавать новый контакт/сделку ---
        conversation_id_2 = await manager.handle_salebot_message(
            platform_id=platform_id,
            bot_name=bot_name,
            salebot_client_id=salebot_client_id,
            client_name=client_name,
            message_text="pytest: второе сообщение (не должно дублировать данные)",
            tg_username=tg_username,
        )

        assert conversation_id_2 == conversation_id, (
            "При повторном сообщении conversation_id должен быть тем же"
        )

        conversation_after = await manager.storage.get_by_platform_id(platform_id, bot_name)
        assert conversation_after is not None
        assert conversation_after.contact_id == conversation.contact_id, (
            "contact_id не должен измениться при повторном сообщении"
        )
        assert conversation_after.lead_id == conversation.lead_id, (
            "lead_id не должен измениться при повторном сообщении"
        )
        assert conversation_after.messages_count == 2

        logger.info("=" * 60)
        logger.info("PASSED: contact=%s, lead=%s, conversation=%s",
                    conversation.contact_id, conversation.lead_id, conversation_id)
        logger.info("=" * 60)

    finally:
        await manager.close()
