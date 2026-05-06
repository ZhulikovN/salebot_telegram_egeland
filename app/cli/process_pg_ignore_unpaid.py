#!/usr/bin/env python3
"""
CLI для обработки таблицы 'айди игноры неоплат пг'.

Запуск:
    python -m app.cli.process_pg_ignore_unpaid
"""
import asyncio
import logging
import sys
from app.services.sheets_client import SheetsClient
from app.services.sheets_processors.pg_ignore_unpaid_processor import PgIgnoreUnpaidProcessor
from app.settings import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("process_pg_ignore_unpaid.log"),
        logging.StreamHandler(sys.stdout),
    ],
)

logger = logging.getLogger(__name__)


async def main():
    """Обработка таблицы 'айди игноры неоплат пг'."""
    logger.info("=" * 80)
    logger.info("Starting processing: айди игноры неоплат пг")
    logger.info("=" * 80)
    
    sheets_client = SheetsClient()
    
    # Используем настройки из settings для воронки и этапа
    processor = PgIgnoreUnpaidProcessor(
        bot_name="айди игноры неоплат пг",
        sheets_client=sheets_client,
        spreadsheet_id=settings.GOOGLE_SPREADSHEET_ID_PG_IGNORE_UNPAID,
        worksheet_name=settings.GOOGLE_WORKSHEET_NAME_PG_IGNORE_UNPAID,
        pipeline_id=settings.AMOCRM_PIPELINE_ID_PG_IGNORE_UNPAID,
        status_id=settings.AMOCRM_STATUS_ID_PG_IGNORE_UNPAID,
    )
    
    try:
        result = await processor.process_sheet()
        logger.info("=" * 80)
        logger.info("Processing completed successfully!")
        logger.info(f"Total: {result['total']}")
        logger.info(f"Success: {result['success']}")
        logger.info(f"Errors: {result['errors']}")
        logger.info(f"Skipped: {result['skipped']}")
        logger.info("=" * 80)
        
        if result["errors"] > 0:
            logger.warning("Some rows had errors. Check the log for details.")
            sys.exit(1)
        else:
            logger.info("All rows processed successfully!")
            sys.exit(0)
    except Exception as e:
        logger.exception("Fatal error during processing: %s", e)
        sys.exit(1)
    finally:
        await processor.close()


if __name__ == "__main__":
    asyncio.run(main())
