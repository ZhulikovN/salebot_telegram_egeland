#!/usr/bin/env python3
"""
Тест создания контакта и сделки с проверкой кастомных полей.

Запуск:
    cd ~/salebot_telegram_egeland/current
    poetry run python tests/test_amocrm_client/test_contact_lead_with_fields.py
"""
import asyncio
import logging
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app.services.amocrm_client import AmoCRMClient
from app.settings import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


async def main() -> None:
    print("=" * 80)
    print("Тест создания контакта и сделки с полями (egeland)")
    print(f"  FIELD_TG_ID      = {settings.FIELD_TG_ID}")
    print(f"  FIELD_TG_USERNAME = {settings.FIELD_TG_USERNAME}")
    print(f"  FIELD_BOT_NAME   = {settings.FIELD_BOT_NAME}")
    print(f"  PIPELINE_ID      = {settings.AMOCRM_PIPELINE_ID}")
    print("=" * 80)

    amocrm = AmoCRMClient()

    tg_id = f"test_{uuid.uuid4().hex[:8]}"
    tg_username = f"testuser_{uuid.uuid4().hex[:6]}"
    bot_name = "ElAuthBot"
    name = f"Тест {tg_id}"

    try:
        # ШАГ 1: Создать контакт
        print(f"\n[1] Создание контакта: name={name}, tg_id={tg_id}, username={tg_username}")
        contact_id = await amocrm.create_contact(
            name=name,
            tg_id=tg_id,
            tg_username=tg_username,
        )
        print(f"    ✓ contact_id={contact_id}")

        # ШАГ 2: Проверить поля контакта
        print(f"\n[2] Проверка полей контакта...")
        response = await amocrm._make_request("GET", f"/contacts/{contact_id}")
        fields = amocrm._parse_custom_fields(response.get("custom_fields_values"))

        tg_id_val = fields.get(settings.FIELD_TG_ID)
        tg_username_val = fields.get(settings.FIELD_TG_USERNAME)

        print(f"    FIELD_TG_ID ({settings.FIELD_TG_ID}): {tg_id_val!r}  → {'OK' if tg_id_val == tg_id else 'FAIL'}")
        print(f"    FIELD_TG_USERNAME ({settings.FIELD_TG_USERNAME}): {tg_username_val!r}  → {'OK' if tg_username_val == tg_username else 'FAIL'}")

        # ШАГ 3: Создать сделку
        print(f"\n[3] Создание сделки: bot_name={bot_name}")
        lead_id = await amocrm.create_lead(
            contact_id=contact_id,
            bot_name=bot_name,
        )
        print(f"    ✓ lead_id={lead_id}")

        # ШАГ 4: Проверить поля сделки
        print(f"\n[4] Проверка полей сделки...")
        lead_response = await amocrm._make_request("GET", f"/leads/{lead_id}")
        lead_fields = amocrm._parse_custom_fields(lead_response.get("custom_fields_values"))

        bot_name_val = lead_fields.get(settings.FIELD_BOT_NAME)
        print(f"    FIELD_BOT_NAME ({settings.FIELD_BOT_NAME}): {bot_name_val!r}  → {'OK' if bot_name_val == bot_name else 'FAIL'}")

        # Итог
        all_ok = (tg_id_val == tg_id) and (tg_username_val == tg_username) and (bot_name_val == bot_name)
        print("\n" + "=" * 80)
        print("✅ ВСЕ ПОЛЯ ЗАПОЛНЕНЫ ВЕРНО" if all_ok else "❌ ЕСТЬ ПРОБЛЕМЫ С ПОЛЯМИ")
        print(f"\nПроверить в AMO:")
        print(f"  Контакт: https://egeland.amocrm.ru/contacts/detail/{contact_id}")
        print(f"  Сделка:  https://egeland.amocrm.ru/leads/detail/{lead_id}")
        print("=" * 80)

    except Exception as e:
        print(f"\n❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()
    finally:
        await amocrm.close()


if __name__ == "__main__":
    asyncio.run(main())
