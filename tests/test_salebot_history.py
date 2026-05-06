"""Тест миграции истории сообщений из Salebot в amoCRM."""
#  poetry run pytest tests/test_salebot_history.py -v -s --log-cli-level=INFO

from uuid import uuid4

import pytest

from app.services.amocrm_client import AmoCRMClient
from app.services.amojo_client import AmojoClient
from app.services.salebot_client import SalebotClient
from app.db.storage import get_conversation_storage


@pytest.mark.asyncio
@pytest.mark.integration
async def test_migrate_history_from_salebot() -> None:
    SALEBOT_CLIENT_ID = 836058546
    PLATFORM_ID = "6253651200"
    BOT_NAME = "ElAuthBot"
    
    amocrm = AmoCRMClient()
    amojo = AmojoClient()
    salebot = SalebotClient()
    storage = get_conversation_storage()
    
    print("\n=== 0. Инициализация БД ===")
    await storage.init_database()
    print("Таблицы созданы")
    
    try:
        print(f"\n=== 1. Получаем историю из Salebot для client_id={SALEBOT_CLIENT_ID} ===")
        history = await salebot.get_history(client_id=SALEBOT_CLIENT_ID)
        
        print(f"STATUS: {history.get('status')}")
        print(f"Ключи в ответе: {list(history.keys())}")
        
        if "result" not in history:
            print("ERROR: Нет ключа 'result' в ответе")
            print(f"Ответ: {history}")
            return
        
        messages = history["result"]
        print(f"Получено сообщений: {len(messages)}")
        
        if not messages:
            print("История пуста")
            return
        
        last_messages = messages[:10] if len(messages) > 10 else messages
        last_messages = list(reversed(last_messages))
        print(f"Переносим последние {len(last_messages)} сообщений")
        print(f"Период: с {last_messages[0].get('created_at')} по {last_messages[-1].get('created_at')}")
        
        client_name = "Nikita Zhulikov"
        tg_username = "ZhulikovNikita"
        
        print(f"Клиент: {client_name} (@{tg_username})")
        
        print("\n=== 2. Проверяем существующий диалог в БД ===")
        existing = await storage.get_by_platform_id(PLATFORM_ID, BOT_NAME)
        
        if existing:
            print("Диалог уже существует, используем существующие данные:")
            print(f"  - conversation_id: {existing.conversation_id}")
            print(f"  - contact_id: {existing.contact_id}")
            print(f"  - lead_id: {existing.lead_id}")
            conversation_id = existing.conversation_id
            contact_id = existing.contact_id
            lead_id = existing.lead_id
        else:
            print("Создаем новый диалог")
            
            print("\n=== 3. Создаем контакт ===")
            contact_id = await amocrm.create_contact(
                name=client_name,
                tg_id=PLATFORM_ID,
                tg_username=tg_username,
            )
            print(f"Контакт создан: {contact_id}")
            
            print("\n=== 4. Создаем сделку ===")
            lead_id = await amocrm.create_lead(
                contact_id=contact_id,
                bot_name=BOT_NAME,
            )
            print(f"Сделка создана: {lead_id}")
            
            print("\n=== 5. Создаем чат в amojo ===")
            conversation_id = str(uuid4())
            
            chat_id = await amocrm.create_chat_in_amojo(
                conversation_id=conversation_id,
                user_id=f"tg:{PLATFORM_ID}",
                user_name=client_name,
                profile_link=f"https://t.me/{tg_username}",
            )
            print(f"Чат создан: {chat_id}")
            
            print("\n=== 6. Привязываем чат к контакту ===")
            await amocrm.link_chat_to_contact(
                contact_id=contact_id,
                chat_id=chat_id,
            )
            print(f"Чат {chat_id} привязан к контакту {contact_id}")
            
            print("\n=== 7. Сохраняем в БД ===")
            await storage.create_conversation(
                conversation_id=conversation_id,
                salebot_client_id=SALEBOT_CLIENT_ID,
                platform_id=PLATFORM_ID,
                contact_id=contact_id,
                lead_id=lead_id,
                client_name=client_name,
                tg_username=tg_username,
                bot_name=BOT_NAME,
            )
            print(f"Диалог сохранен в БД: {conversation_id}")
        
        print(f"\n=== 8. Переносим {len(last_messages)} сообщений ===")
        
        for i, msg in enumerate(last_messages, start=1):
            text = msg.get("text", "")
            is_client = msg.get("client_replica", True)
            msg_id = msg.get("id", i)
            
            if not text:
                print(f"{i}. Пропуск: пустое сообщение")
                continue
            
            if is_client:
                sender_id = f"tg:{PLATFORM_ID}"
                sender_name = client_name
                sender_type = "client"
            else:
                sender_id = "manager"
                sender_name = "Менеджер"
                sender_type = "manager"
            
            print(f"{i}. [{sender_type}] {text[:50]}...")
            
            await amojo.send_incoming_message(
                conversation_id=conversation_id,
                msgid=f"history:{msg_id}",
                sender_id=sender_id,
                sender_name=sender_name,
                text=text,
                silent=True,
                profile_link=f"https://t.me/{tg_username}",
            )
        
        print("\n=== 9. Проверка результата ===")
        conversation = await storage.get_by_platform_id(PLATFORM_ID, BOT_NAME)
        
        assert conversation is not None
        assert conversation.contact_id == contact_id
        assert conversation.lead_id == lead_id
        assert conversation.salebot_client_id == SALEBOT_CLIENT_ID
        
        print("✅ История успешно перенесена!")
        print(f"   conversation_id: {conversation.conversation_id}")
        print(f"   contact_id: {conversation.contact_id}")
        print(f"   lead_id: {conversation.lead_id}")
        
    finally:
        await amocrm.close()


# @pytest.mark.asyncio
# @pytest.mark.integration
# async def test_get_salebot_history() -> None:
#     """Простой тест получения истории из Salebot API."""
#     SALEBOT_CLIENT_ID = 836058546
#
#     salebot = SalebotClient()
#
#     print(f"\n=== Получаем историю для client_id={SALEBOT_CLIENT_ID} ===")
#     history = await salebot.get_history(client_id=SALEBOT_CLIENT_ID)
#
#     print(f"\nКлючи в ответе: {list(history.keys())}")
#
#     if "messages" in history:
#         messages = history["messages"]
#         print(f"Всего сообщений: {len(messages)}")
#
#         if messages:
#             print("\nПример первого сообщения:")
#             first = messages[0]
#             print(f"  - text: {first.get('text', '')[:100]}")
#             print(f"  - sender: {first.get('sender')}")
#             print(f"  - timestamp: {first.get('timestamp')}")
#             print(f"  - client_name: {first.get('client_name')}")
#
#             print("\nПример последнего сообщения:")
#             last = messages[-1]
#             print(f"  - text: {last.get('text', '')[:100]}")
#             print(f"  - sender: {last.get('sender')}")
#             print(f"  - timestamp: {last.get('timestamp')}")
#
#     assert "messages" in history or "error" in history
