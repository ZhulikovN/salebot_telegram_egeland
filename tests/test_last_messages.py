"""
Тест для проверки переноса последних 5 сообщений клиента из Salebot в amoCRM.
"""
import asyncio
import logging

from app.services.salebot_client import SalebotClient
from app.settings import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


async def test_last_messages():
    """
    Тест проверки получения последних сообщений клиента.
    
    Проверяем для TG ID: 487796379
    """
    tg_id = 487796379
    
    salebot = SalebotClient()
    
    try:
        logger.info("=" * 80)
        logger.info("ТЕСТ: Проверка переноса последних 5 сообщений клиента")
        logger.info("TG ID: %s", tg_id)
        logger.info("=" * 80)
        
        # 1. Поиск клиента в Salebot
        logger.info("\n1. Поиск клиента в Salebot...")
        salebot_response = await salebot.load_client(
            platform_id=tg_id,
            group_id="ElAuthBot"
        )
        
        if not salebot_response or salebot_response.get("status") != "success":
            logger.error("❌ Клиент не найден в Salebot!")
            return
        
        items = salebot_response.get("items", [])
        if not items:
            logger.error("❌ Нет данных клиента!")
            return
        
        salebot_client_id = items[0].get("id")
        logger.info("✓ Клиент найден: client_id=%s", salebot_client_id)
        
        # 2. Получение истории
        logger.info("\n2. Получение истории сообщений...")
        history = await salebot.get_history(client_id=salebot_client_id)
        
        messages = history.get("result", [])
        logger.info("✓ Всего сообщений в истории: %d", len(messages))
        
        # 3. Анализ сообщений
        logger.info("\n3. Анализ сообщений:")
        logger.info("-" * 80)
        
        client_messages = []
        manager_messages = []
        
        for i, msg in enumerate(messages, 1):
            msg_text = msg.get("text", "")
            is_client = msg.get("client_replica", True)
            msg_id = msg.get("id", "N/A")
            
            if not msg_text:
                continue
            
            if is_client:
                client_messages.append((i, msg_id, msg_text))
            else:
                manager_messages.append((i, msg_id, msg_text))
        
        logger.info("Сообщений клиента: %d", len(client_messages))
        logger.info("Сообщений менеджера: %d", len(manager_messages))
        
        # 4. Показываем ВСЕ сообщения клиента
        logger.info("\n4. ВСЕ сообщения клиента (в порядке получения):")
        logger.info("-" * 80)
        
        for idx, (pos, msg_id, text) in enumerate(client_messages, 1):
            preview = text[:100] + "..." if len(text) > 100 else text
            logger.info("%d. [Позиция %d, ID: %s] %s", idx, pos, msg_id, preview)
        
        # 5. СТАРАЯ ЛОГИКА (что было)
        logger.info("\n5. СТАРАЯ ЛОГИКА (берем последние 5 из всех, потом фильтруем):")
        logger.info("-" * 80)
        
        last_5_all = messages[-5:] if len(messages) >= 5 else messages
        old_logic_messages = [
            msg for msg in last_5_all 
            if msg.get("client_replica", True) and msg.get("text")
        ]
        
        logger.info("Взято последних 5 из всех сообщений:")
        for i, msg in enumerate(last_5_all, 1):
            is_client = msg.get("client_replica", True)
            msg_type = "КЛИЕНТ" if is_client else "МЕНЕДЖЕР"
            text = msg.get("text", "")[:50]
            logger.info("  %d. [%s] %s", i, msg_type, text)
        
        logger.info("\nПосле фильтрации (только клиент): %d сообщений", len(old_logic_messages))
        for i, msg in enumerate(old_logic_messages, 1):
            text = msg.get("text", "")[:100]
            logger.info("  %d. %s", i, text)
        
        # 6. НОВАЯ ЛОГИКА (что стало)
        logger.info("\n6. НОВАЯ ЛОГИКА (сначала фильтруем клиента, потом берем первые 5):")
        logger.info("-" * 80)
        
        # Фильтруем только сообщения клиента
        filtered_client = [
            msg for msg in messages 
            if msg.get("client_replica", True) and msg.get("text")
        ]
        
        # Берем первые 5 (т.к. Salebot возвращает от новых к старым!)
        last_5_client = filtered_client[:5] if len(filtered_client) >= 5 else filtered_client
        
        logger.info("Всего сообщений клиента: %d", len(filtered_client))
        logger.info("Берем последние 5 сообщений клиента:")
        
        for i, msg in enumerate(last_5_client, 1):
            text = msg.get("text", "")
            preview = text[:100] + "..." if len(text) > 100 else text
            msg_id = msg.get("id", "N/A")
            logger.info("  %d. [ID: %s] %s", i, msg_id, preview)
        
        # 7. Сравнение
        logger.info("\n7. СРАВНЕНИЕ:")
        logger.info("-" * 80)
        logger.info("Старая логика: %d сообщений", len(old_logic_messages))
        logger.info("Новая логика: %d сообщений", len(last_5_client))
        
        if len(old_logic_messages) != len(last_5_client):
            logger.warning("⚠️  КОЛИЧЕСТВО ОТЛИЧАЕТСЯ!")
        
        # Проверяем, одинаковые ли это сообщения
        old_ids = [msg.get("id") for msg in old_logic_messages]
        new_ids = [msg.get("id") for msg in last_5_client]
        
        if old_ids != new_ids:
            logger.warning("⚠️  СООБЩЕНИЯ ОТЛИЧАЮТСЯ!")
            logger.info("\nСтарая логика ID: %s", old_ids)
            logger.info("Новая логика ID: %s", new_ids)
        else:
            logger.info("✓ Сообщения одинаковые")
        
        # 8. Итог
        logger.info("\n" + "=" * 80)
        if len(last_5_client) == 5:
            logger.info("✅ ТЕСТ ПРОЙДЕН: Переносятся последние 5 сообщений клиента")
        elif len(last_5_client) < 5:
            logger.info("✅ ТЕСТ ПРОЙДЕН: Переносятся все %d сообщений клиента (меньше 5)", len(last_5_client))
        else:
            logger.error("❌ ОШИБКА: Переносится больше 5 сообщений!")
        logger.info("=" * 80)
        
    except Exception as e:
        logger.error("\n❌ ОШИБКА: %s", e, exc_info=True)
        raise
    
    finally:
        await salebot.close()


if __name__ == "__main__":
    asyncio.run(test_last_messages())
