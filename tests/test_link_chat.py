"""
Тест link_chat_to_contact: сначала отправляем сообщение, потом привязываем.

Запуск:
    poetry run pytest tests/test_link_chat.py -v -s --log-cli-level=INFO
"""
import logging
from uuid import uuid4

import pytest

from app.services.amocrm_client import AmoCRMClient
from app.services.amojo_client import AmojoClient
from app.utils.token_manager import get_token_manager

logger = logging.getLogger(__name__)

EXISTING_CONTACT_ID = 60517991


@pytest.mark.asyncio
@pytest.mark.integration
async def test_link_chat_to_contact() -> None:
    """Сначала сообщение, потом link_chat_to_contact."""
    amocrm = AmoCRMClient()
    amojo = AmojoClient()
    token_manager = get_token_manager()

    try:
        # 1. OAuth токен
        logger.info("[1] Получаем OAuth токен...")
        token = await token_manager.get_access_token()
        assert token
        logger.info("    OK: %s...", token[:40])

        # 2. Создаём чат в amojo
        logger.info("[2] Создаём чат в amojo...")
        conversation_id = str(uuid4())
        platform_id = f"8000{uuid4().hex[:6]}"

        chat_id = await amocrm.create_chat_in_amojo(
            conversation_id=conversation_id,
            user_id=f"tg:{platform_id}",
            user_name="Тест LinkChat",
            profile_link=None,
        )
        assert chat_id
        logger.info("    OK: chat_id=%s", chat_id)
        logger.info("    conversation_id=%s", conversation_id)

        # 3. Сначала отправляем первое сообщение
        logger.info("[3] Отправляем первое сообщение (silent=False)...")
        msgid = f"test:{uuid4().hex}"
        await amojo.send_incoming_message(
            conversation_id=conversation_id,
            msgid=msgid,
            sender_id=f"tg:{platform_id}",
            sender_name="Тест LinkChat",
            text="pytest: первое сообщение перед link",
            silent=False,
        )
        logger.info("    OK: msgid=%s", msgid)

        # 4. Потом привязываем чат к контакту
        logger.info("[4] link_chat_to_contact: contact=%s, chat=%s...", EXISTING_CONTACT_ID, chat_id)
        await amocrm.link_chat_to_contact(
            contact_id=EXISTING_CONTACT_ID,
            chat_id=chat_id,
        )
        logger.info("    OK: чат привязан!")

        logger.info("=" * 60)
        logger.info("УСПЕХ!")
        logger.info("=" * 60)

    finally:
        await amocrm.close()
