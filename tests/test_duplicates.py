"""
Тест проверки обработки дублей по tg_id.
"""
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def test_duplicate_detection():
    """
    Симуляция проверки дублей.
    """
    logger.info("=" * 80)
    logger.info("ТЕСТ: Проверка обработки дублей по tg_id")
    logger.info("=" * 80)
    
    # Симулируем строки из таблицы
    rows = [
        (2, {"tg_id": "123456", "name": "Иван"}),
        (3, {"tg_id": "789012", "name": "Петр"}),
        (4, {"tg_id": "123456", "name": "Иван Дубль"}),  # ДУБЛЬ!
        (5, {"tg_id": "345678", "name": "Мария"}),
        (6, {"tg_id": "789012", "name": "Петр Дубль"}),  # ДУБЛЬ!
    ]
    
    seen_tg_ids = set()
    rows_to_process = []
    duplicate_rows = []
    
    for row_num, row_data in rows:
        tg_id = row_data.get("tg_id", "").strip()
        
        if tg_id and tg_id in seen_tg_ids:
            logger.warning("✗ Строка %d: ДУБЛЬ (tg_id=%s)", row_num, tg_id)
            duplicate_rows.append((row_num, 'error', 'ДУБЛЬ'))
            continue
        
        if tg_id:
            seen_tg_ids.add(tg_id)
        
        logger.info("✓ Строка %d: OK (tg_id=%s)", row_num, tg_id)
        rows_to_process.append((row_num, row_data))
    
    logger.info("\n" + "=" * 80)
    logger.info("РЕЗУЛЬТАТ:")
    logger.info("Обработано: %d строк", len(rows_to_process))
    logger.info("Дублей найдено: %d строк", len(duplicate_rows))
    logger.info("=" * 80)
    
    # Проверка
    assert len(rows_to_process) == 3, "Должно быть 3 уникальных строки"
    assert len(duplicate_rows) == 2, "Должно быть 2 дубля"
    
    logger.info("\n✅ ТЕСТ ПРОЙДЕН!")
    logger.info("Дубли корректно определяются и помечаются как 'ДУБЛЬ'")


if __name__ == "__main__":
    test_duplicate_detection()
