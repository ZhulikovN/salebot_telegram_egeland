"""
Тест для проверки создания задачи "новый должник" в amoCRM.
"""
import asyncio
import logging

from app.services.amocrm_client import AmoCRMClient
from app.settings import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


async def test_task_creation():
    """
    Тест создания задачи для существующей сделки.
    
    Требования:
    1. В amoCRM должна существовать хотя бы одна открытая сделка
    2. У сделки должен быть ответственный менеджер
    """
    amocrm = AmoCRMClient()
    
    try:
        logger.info("=" * 80)
        logger.info("ТЕСТ: Создание задачи 'новый должник'")
        logger.info("=" * 80)
        
        # 1. Найдем первую открытую сделку для теста
        logger.info("\n1. Поиск открытой сделки для теста...")
        
        # Получаем список сделок (первые 10)
        leads_response = await amocrm._make_request(
            "GET",
            "/leads",
            params={
                "limit": 10,
                "filter[statuses][0][pipeline_id]": settings.AMOCRM_PIPELINE_ID,
                "filter[statuses][0][status_id]": settings.AMOCRM_STATUS_ID,
            }
        )
        
        leads = leads_response.get("_embedded", {}).get("leads", [])
        
        if not leads:
            logger.error("❌ Нет открытых сделок для теста!")
            logger.info("\nСоздайте хотя бы одну сделку вручную или запустите process_daily_sheets.py")
            return
        
        test_lead = leads[0]
        lead_id = test_lead["id"]
        lead_name = test_lead.get("name", "Без названия")
        responsible_user_id = test_lead.get("responsible_user_id")
        
        logger.info(f"✓ Найдена сделка для теста:")
        logger.info(f"  - ID: {lead_id}")
        logger.info(f"  - Название: {lead_name}")
        logger.info(f"  - Ответственный: user_id={responsible_user_id}")
        
        # 2. Создаем задачу
        logger.info(f"\n2. Создание задачи 'новый должник' для сделки {lead_id}...")
        
        task_id = await amocrm.create_task(
            lead_id=lead_id,
            text="новый должник",
            task_type_id=1,  # Звонок
            complete_till_days=1
        )
        
        logger.info(f"✓ Задача успешно создана: id={task_id}")
        
        # 3. Проверяем созданную задачу
        logger.info(f"\n3. Проверка созданной задачи...")
        
        task_response = await amocrm._make_request(
            "GET",
            f"/tasks/{task_id}"
        )
        
        logger.info(f"✓ Задача найдена в amoCRM:")
        logger.info(f"  - ID: {task_response['id']}")
        logger.info(f"  - Текст: {task_response.get('text')}")
        logger.info(f"  - Тип: {task_response.get('task_type_id')}")
        logger.info(f"  - Срок: {task_response.get('complete_till')}")
        logger.info(f"  - Ответственный: {task_response.get('responsible_user_id')}")
        logger.info(f"  - Привязана к сделке: {task_response.get('entity_id')}")
        logger.info(f"  - Тип сущности: {task_response.get('entity_type')}")
        
        # 4. Проверяем корректность данных
        logger.info(f"\n4. Валидация данных задачи...")
        
        errors = []
        
        if task_response.get("text") != "новый должник":
            errors.append(f"Неверный текст задачи: '{task_response.get('text')}' вместо 'новый должник'")
        
        if task_response.get("task_type_id") != 1:
            errors.append(f"Неверный тип задачи: {task_response.get('task_type_id')} вместо 1")
        
        if task_response.get("entity_id") != lead_id:
            errors.append(f"Задача привязана к неверной сделке: {task_response.get('entity_id')} вместо {lead_id}")
        
        if task_response.get("entity_type") != "leads":
            errors.append(f"Неверный тип сущности: {task_response.get('entity_type')} вместо 'leads'")
        
        if task_response.get("responsible_user_id") != responsible_user_id:
            errors.append(f"Неверный ответственный: {task_response.get('responsible_user_id')} вместо {responsible_user_id}")
        
        if errors:
            logger.error("❌ ОШИБКИ ВАЛИДАЦИИ:")
            for error in errors:
                logger.error(f"  - {error}")
        else:
            logger.info("✓ Все данные задачи корректны!")
        
        # 5. Итоговый результат
        logger.info("\n" + "=" * 80)
        if errors:
            logger.error("❌ ТЕСТ НЕ ПРОЙДЕН: Есть ошибки валидации")
        else:
            logger.info("✅ ТЕСТ ПРОЙДЕН: Задача 'новый должник' создана корректно!")
        logger.info("=" * 80)
        
    except Exception as e:
        logger.error(f"\n❌ ОШИБКА: {e}", exc_info=True)
        raise
    
    finally:
        await amocrm.close()


if __name__ == "__main__":
    asyncio.run(test_task_creation())
