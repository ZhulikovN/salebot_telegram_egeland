#!/usr/bin/env python3
"""
CLI для обработки ежедневных таблиц.

Обрабатывает таблицы:
1. ПГ 2к26 зеро игнор (Английский язык)
2. Retention 25-26 (Рабочий лист) - только февраль 2026

Запускается раз в сутки через systemd timer.
"""
import asyncio
import logging
import sys
from datetime import datetime

from app.services.sheets_client import SheetsClient
from app.services.sheets_processors.pg_2k26_processor import PG2K26ZeroIgnoreProcessor
from app.services.sheets_processors.retention_processor import Retention2526Processor
from app.settings import settings

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

logger = logging.getLogger(__name__)


async def process_pg_2k26_table() -> dict:
    """
    Обработать таблицу ПГ 2к26 зеро игнор.
    
    Returns:
        Статистика обработки
    """
    logger.info("=" * 80)
    logger.info("Обработка таблицы: ПГ 2к26 зеро игнор")
    logger.info("=" * 80)
    
    try:
        sheets_client = SheetsClient()
        processor = PG2K26ZeroIgnoreProcessor(
            sheets_client=sheets_client,
            spreadsheet_id=settings.GOOGLE_PG_2K26_ZERO_IGNORE_SPREADSHEET_ID,
            worksheet_name=settings.GOOGLE_PG_2K26_ZERO_IGNORE_WORKSHEET_NAME,
            pipeline_id=settings.AMOCRM_PIPELINE_ID,
            status_id=settings.AMOCRM_STATUS_ID,
        )
        
        await processor.process_sheet()
        await processor.close()
        
        logger.info("Таблица ПГ 2к26 обработана успешно")
        return {"status": "success", "table": "ПГ 2к26 зеро игнор"}
        
    except Exception as e:
        logger.error("Ошибка обработки таблицы ПГ 2к26: %s", e, exc_info=True)
        return {"status": "error", "table": "ПГ 2к26 зеро игнор", "error": str(e)}


async def process_retention_table() -> dict:
    """
    Обработать таблицу Retention 25-26.
    
    Returns:
        Статистика обработки
    """
    logger.info("=" * 80)
    logger.info("Обработка таблицы: Retention 25-26")
    logger.info("=" * 80)
    
    try:
        sheets_client = SheetsClient()
        processor = Retention2526Processor(
            sheets_client=sheets_client,
            spreadsheet_id=settings.GOOGLE_RETENTION_25_26_SPREADSHEET_ID,
            worksheet_name=settings.GOOGLE_RETENTION_25_26_WORKSHEET_NAME,
            pipeline_id=settings.AMOCRM_PIPELINE_ID,
            status_id=settings.AMOCRM_STATUS_ID,
        )
        
        await processor.process_sheet()
        await processor.close()
        
        logger.info("Таблица Retention 25-26 обработана успешно")
        return {"status": "success", "table": "Retention 25-26"}
        
    except Exception as e:
        logger.error("Ошибка обработки таблицы Retention: %s", e, exc_info=True)
        return {"status": "error", "table": "Retention 25-26", "error": str(e)}


async def main():
    """
    Главная функция: обработать все ежедневные таблицы.
    """
    start_time = datetime.now()
    logger.info("=" * 80)
    logger.info("ЗАПУСК ОБРАБОТКИ ЕЖЕДНЕВНЫХ ТАБЛИЦ")
    logger.info("Время запуска: %s", start_time.strftime("%Y-%m-%d %H:%M:%S"))
    logger.info("=" * 80)
    
    results = []
    
    # Обработка таблицы ПГ 2к26
    result_pg = await process_pg_2k26_table()
    results.append(result_pg)
    
    # Небольшая пауза между таблицами
    await asyncio.sleep(2)
    
    # Обработка таблицы Retention
    result_retention = await process_retention_table()
    results.append(result_retention)
    
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
