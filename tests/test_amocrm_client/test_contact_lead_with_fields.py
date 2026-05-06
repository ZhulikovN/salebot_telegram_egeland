#!/usr/bin/env python3
"""
Тест создания контакта и сделки с дополнительными полями
"""
import asyncio
import logging

from app.services.amocrm_client import AmoCRMClient
from app.settings import settings

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)

async def main():
    print("=" * 80)
    print("Тест создания контакта и сделки с дополнительными полями")
    print("=" * 80)
    
    # Инициализация клиента
    amocrm = AmoCRMClient()
    
    # Тестовые данные
    test_tg_id = "777777777"
    test_name = "Тестовый Ученик С Полями"
    test_email = "test_fields@example.com"
    test_phone = "+79001234567"
    test_tg_username = "test_username"
    
    # Данные для сделки
    test_course = "ПГ 2к26"
    test_where_studied = "Английский язык"
    test_class = "11 класс"
    test_leave_reason = "Тестовая причина"
    
    try:
        # ШАГ 1: Создать контакт с полями
        print(f"\n[ШАГ 1] Создание контакта...")
        print(f"  Имя: {test_name}")
        print(f"  ТГ ID: {test_tg_id}")
        print(f"  TG Username: {test_tg_username}")
        print(f"  Email: {test_email}")
        print(f"  Phone: {test_phone}")
        
        contact_id = await amocrm.create_contact(
            name=test_name,
            tg_id=test_tg_id,
            tg_username=test_tg_username,
            email=test_email,
            phone=test_phone,
        )
        print(f"  ✓ Contact created: {contact_id}")
        
        # ШАГ 2: Создать сделку с дополнительными полями
        print(f"\n[ШАГ 2] Создание сделки...")
        print(f"  Курс: {test_course}")
        print(f"  Где учился (Предмет): {test_where_studied}")
        print(f"  Класс: {test_class}")
        print(f"  Причина ухода: {test_leave_reason}")
        
        lead_id = await amocrm.create_lead(
            contact_id=contact_id,
            bot_name="ПГ 2к26 зеро игнор",
            course=test_course,
            where_studied=test_where_studied,
            student_class=test_class,
            leave_reason=test_leave_reason,
        )
        print(f"  ✓ Lead created: {lead_id}")
        
        # ШАГ 3: Вывод результатов
        print("\n" + "=" * 80)
        print("✅ ГОТОВО!")
        print("=" * 80)
        print(f"\nПроверьте в amoCRM:")
        print(f"  - Контакт: https://zabotael.amocrm.ru/contacts/detail/{contact_id}")
        print(f"  - Сделка: https://zabotael.amocrm.ru/leads/detail/{lead_id}")
        print(f"\nКонтакт должен содержать:")
        print(f"  - TG ID: {test_tg_id}")
        print(f"  - TG Username: {test_tg_username}")
        print(f"  - Email: {test_email}")
        print(f"  - Телефон: {test_phone}")
        print(f"\nСделка должна содержать:")
        print(f"  - Курс: {test_course}")
        print(f"  - Где учился (Предмет): {test_where_studied}")
        print(f"  - Класс: {test_class}")
        print(f"  - Причина ухода: {test_leave_reason}")
        print("=" * 80)
        
    except Exception as e:
        print(f"\n❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())
