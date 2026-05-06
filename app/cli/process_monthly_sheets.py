#!/usr/bin/env python3
"""
CLI для обработки месячных таблиц Неоплаты.

Обрабатывает 12 таблиц:
1. Неоплаты_Январь_Годовой_Физика 2к26
2. Неоплаты 5 Месяц годовой Обществознание
3. Неоплаты_Химия_Январь_из 4 в 5
4. Литра_Неоплаты_Январь_2к26
5. Неоплаты январь (Проф. мат Маша)
6. Неоплаты_ЯНВАРЬ_Годовой_Био Женя 2к26
7. Неоплаты мат Саша январь
8. Неоплаты_ЯНВАРЬ_Годовой_Био Геля 2к26
9. Неоплаты 5го месяца ИНФА
10-12. (добавить остальные)

Запускается раз в месяц через systemd timer.
"""
import asyncio
import logging
import sys
from datetime import datetime

from app.services.sheets_client import SheetsClient
from app.services.sheets_processors.neoplaty_processor import NeoplatyProcessor
from app.settings import settings

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

logger = logging.getLogger(__name__)


# Конфигурация всех таблиц Неоплаты
NEOPLATY_TABLES = [
    {
        "name": "Неоплаты Физика",
        "bot_name": "Неоплаты Физика 2к26",
        "spreadsheet_id": settings.GOOGLE_SPREADSHEET_ID_NEOPLATY_JAN_PHYSICS_2K26,
        "worksheet_name": settings.GOOGLE_WORKSHEET_NAME_NEOPLATY_JAN_PHYSICS_2K26,
    },
    {
        "name": "Неоплаты Обществознание",
        "bot_name": "Неоплаты Обществознание 5 месяц",
        "spreadsheet_id": settings.GOOGLE_SPREADSHEET_ID_NEOPLATY_5_MONTH_OBSH,
        "worksheet_name": settings.GOOGLE_WORKSHEET_NAME_NEOPLATY_5_MONTH_OBSH,
    },
    {
        "name": "Неоплаты Химия",
        "bot_name": "Неоплаты Химия",
        "spreadsheet_id": settings.GOOGLE_SPREADSHEET_ID_NEOPLATY_CHEM_JAN_4_TO_5,
        "worksheet_name": settings.GOOGLE_WORKSHEET_NAME_NEOPLATY_CHEM_JAN_4_TO_5,
    },
    {
        "name": "Неоплаты Литература",
        "bot_name": "Неоплаты Литература",
        "spreadsheet_id": settings.GOOGLE_SPREADSHEET_ID_NEOPLATY_LIT_JAN_2K26,
        "worksheet_name": settings.GOOGLE_WORKSHEET_NAME_NEOPLATY_LIT_JAN_2K26,
    },
    {
        "name": "Неоплаты Проф. мат (Маша)",
        "bot_name": "Неоплаты Проф. мат (Маша)",
        "spreadsheet_id": settings.GOOGLE_SPREADSHEET_ID_NEOPLATY_JAN_GENERAL,
        "worksheet_name": settings.GOOGLE_WORKSHEET_NAME_NEOPLATY_JAN_GENERAL,
    },
    {
        "name": "Неоплаты Биология (Женя)",
        "bot_name": "Неоплаты Биология (Женя)",
        "spreadsheet_id": settings.GOOGLE_SPREADSHEET_ID_NEOPLATY_JAN_BIO_ZHENYA_2K26,
        "worksheet_name": settings.GOOGLE_WORKSHEET_NAME_NEOPLATY_JAN_BIO_ZHENYA_2K26,
    },
    {
        "name": "Неоплаты Проф. мат (Саша)",
        "bot_name": "Неоплаты Проф. мат (Саша)",
        "spreadsheet_id": settings.GOOGLE_SPREADSHEET_ID_NEOPLATY_JAN_MATH_SASHA,
        "worksheet_name": settings.GOOGLE_WORKSHEET_NAME_NEOPLATY_JAN_MATH_SASHA,
    },
    {
        "name": "Неоплаты Биология (Геля)",
        "bot_name": "Неоплаты Биология (Геля)",
        "spreadsheet_id": settings.GOOGLE_SPREADSHEET_ID_NEOPLATY_JAN_BIO_GELYA_2K26,
        "worksheet_name": settings.GOOGLE_WORKSHEET_NAME_NEOPLATY_JAN_BIO_GELYA_2K26,
    },
    {
        "name": "Неоплаты Информатика",
        "bot_name": "Неоплаты Информатика 5 месяц",
        "spreadsheet_id": settings.GOOGLE_SPREADSHEET_ID_NEOPLATY_5_MONTH_INFO,
        "worksheet_name": settings.GOOGLE_WORKSHEET_NAME_NEOPLATY_5_MONTH_INFO,
    },
]


async def process_neoplaty_table(table_config: dict) -> dict:
    """
    Обработать одну таблицу Неоплаты.
    
    Args:
        table_config: Конфигурация таблицы
    
    Returns:
        Статистика обработки
    """
    logger.info("=" * 80)
    logger.info("Обработка таблицы: %s", table_config["name"])
    logger.info("=" * 80)
    
    try:
        sheets_client = SheetsClient()
        processor = NeoplatyProcessor(
            bot_name=table_config["bot_name"],
            sheets_client=sheets_client,
            spreadsheet_id=table_config["spreadsheet_id"],
            worksheet_name=table_config["worksheet_name"],
            pipeline_id=settings.AMOCRM_PIPELINE_ID,
            status_id=settings.AMOCRM_STATUS_ID,
        )
        
        await processor.process_sheet()
        await processor.close()
        
        logger.info("Таблица %s обработана успешно", table_config["name"])
        return {"status": "success", "table": table_config["name"]}
        
    except Exception as e:
        logger.error("Ошибка обработки таблицы %s: %s", table_config["name"], e, exc_info=True)
        return {"status": "error", "table": table_config["name"], "error": str(e)}


async def main():
    """
    Главная функция: обработать все месячные таблицы Неоплаты.
    """
    start_time = datetime.now()
    logger.info("=" * 80)
    logger.info("ЗАПУСК ОБРАБОТКИ МЕСЯЧНЫХ ТАБЛИЦ (НЕОПЛАТЫ)")
    logger.info("Время запуска: %s", start_time.strftime("%Y-%m-%d %H:%M:%S"))
    logger.info("Всего таблиц: %d", len(NEOPLATY_TABLES))
    logger.info("=" * 80)
    
    results = []
    
    # Обработка всех таблиц
    for idx, table_config in enumerate(NEOPLATY_TABLES, start=1):
        logger.info("\n[%d/%d] Обработка: %s", idx, len(NEOPLATY_TABLES), table_config["name"])
        
        result = await process_neoplaty_table(table_config)
        results.append(result)
        
        # Пауза между таблицами
        if idx < len(NEOPLATY_TABLES):
            await asyncio.sleep(2)
    
    # Итоговая статистика
    end_time = datetime.now()
    duration = (end_time - start_time).total_seconds()
    
    logger.info("=" * 80)
    logger.info("ОБРАБОТКА ЗАВЕРШЕНА")
    logger.info("=" * 80)
    logger.info("Время завершения: %s", end_time.strftime("%Y-%m-%d %H:%M:%S"))
    logger.info("Длительность: %.2f секунд", duration)
    logger.info("")
    logger.info("Результаты:")
    
    success_count = 0
    error_count = 0
    
    for result in results:
        status_text = "[OK]" if result["status"] == "success" else "[ERROR]"
        logger.info("  %s %s: %s", status_text, result["table"], result["status"])
        
        if result["status"] == "success":
            success_count += 1
        else:
            error_count += 1
            if "error" in result:
                logger.error("    Ошибка: %s", result["error"])
    
    logger.info("")
    logger.info("Итого: %d успешно, %d ошибок", success_count, error_count)
    logger.info("=" * 80)
    
    # Возвращаем код выхода
    if error_count > 0:
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Прервано пользователем")
        sys.exit(130)
    except Exception as e:
        logger.error("Критическая ошибка: %s", e, exc_info=True)
        sys.exit(1)
