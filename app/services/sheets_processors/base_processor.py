"""Базовый класс для обработки таблиц."""

import logging
import asyncio
from abc import ABC, abstractmethod
from typing import Dict, Any, List, Tuple
from app.services.sheets_client import SheetsClient
from app.settings import settings

logger = logging.getLogger(__name__)


class BaseSheetProcessor(ABC):
    """Базовый класс для обработки таблиц."""
    
    def __init__(
        self,
        sheets_client: SheetsClient,
        spreadsheet_id: str,
        worksheet_name: str,
        pipeline_id: int,
        status_id: int
    ):
        """
        Инициализация процессора.
        
        Args:
            sheets_client: Клиент Google Sheets
            spreadsheet_id: ID таблицы
            worksheet_name: Название листа
            pipeline_id: ID воронки в amoCRM
            status_id: ID этапа в amoCRM
        """
        self.sheets_client = sheets_client
        self.spreadsheet_id = spreadsheet_id
        self.worksheet_name = worksheet_name
        self.pipeline_id = pipeline_id
        self.status_id = status_id
        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")
        self._status_col_cache = None
        self._error_col_cache = None
        
        # Параметры параллельной обработки (из настроек)
        self.max_concurrent_tasks = settings.SHEETS_MAX_CONCURRENT_TASKS
        self.batch_size = settings.SHEETS_BATCH_SIZE
    
    @abstractmethod
    def parse_row(self, row: Dict[str, Any]) -> Dict[str, Any]:
        """
        Парсинг строки таблицы в единый формат.
        
        Args:
            row: Сырые данные строки из таблицы
            
        Returns:
            Словарь с нормализованными данными
        """
        pass
    
    @abstractmethod
    async def process_deal(self, data: Dict[str, Any]) -> None:
        """
        Создать или обновить сделку в amoCRM.
        
        Args:
            data: Нормализованные данные из parse_row()
        """
        pass
    
    def get_status_column(self, worksheet=None) -> str:
        """
        Получить букву колонки со статусом обработки.
        
        Ищет колонку по названию get_status_column_name().
        Можно переопределить в наследниках для хардкода буквы.
        
        Args:
            worksheet: gspread.Worksheet (опционально, для автопоиска)
        
        Returns:
            Буква колонки (например, "J")
        """
        if self._status_col_cache:
            return self._status_col_cache
        
        if worksheet:
            col_letter = self.sheets_client.find_column_letter(
                worksheet,
                self.get_status_column_name()
            )
            if col_letter:
                self._status_col_cache = col_letter
                return col_letter
        
        # Fallback - должен быть переопределен в наследниках
        raise NotImplementedError("get_status_column must be overridden or worksheet must be provided")
    
    def get_error_column(self, worksheet=None) -> str:
        """
        Получить букву колонки с текстом ошибки.
        
        Ищет колонку "Текст ошибки" автоматически.
        Можно переопределить в наследниках для хардкода буквы.
        
        Args:
            worksheet: gspread.Worksheet (опционально, для автопоиска)
        
        Returns:
            Буква колонки (например, "K")
        """
        if self._error_col_cache:
            return self._error_col_cache
        
        if worksheet:
            col_letter = self.sheets_client.find_column_letter(
                worksheet,
                "Текст ошибки"
            )
            if col_letter:
                self._error_col_cache = col_letter
                return col_letter
        
        # Fallback - должен быть переопределен в наследниках
        raise NotImplementedError("get_error_column must be overridden or worksheet must be provided")
    
    def get_tg_id_column_name(self) -> str:
        """
        Получить название колонки с ТГ айди.
        
        Returns:
            Название колонки (по умолчанию: "Тг айди ученика")
        """
        return "Тг айди ученика"
    
    def get_status_column_name(self) -> str:
        """
        Получить название колонки со статусом обработки.
        
        Returns:
            Название колонки (по умолчанию: "Статус обработки")
        """
        return "Статус обработки"
    
    def should_process_row(self, row_num: int, row_data: Dict[str, Any]) -> bool:
        """
        Проверить, нужно ли обрабатывать строку.
        
        Может быть переопределен в наследниках для дополнительной фильтрации.
        
        Args:
            row_num: Номер строки
            row_data: Данные строки
            
        Returns:
            True если строку нужно обработать, False если пропустить
        """
        return True
    
    async def _process_single_row(
        self,
        row_num: int,
        row_data: Dict[str, Any]
    ) -> Tuple[int, str, str]:
        """
        Обработать одну строку.
        
        Args:
            row_num: Номер строки
            row_data: Данные строки
            
        Returns:
            Tuple[row_num, status, error_message]
            status: 'success', 'error', 'skipped'
        """
        try:
            # Парсинг строки
            parsed_data = self.parse_row(row_data)
            
            # Создание/обновление сделки
            await self.process_deal(parsed_data)
            
            return (row_num, 'success', '')
            
        except ValueError as e:
            # ValueError = пропускаем строку
            error_msg = str(e)
            if "пустой" in error_msg.lower() or "пропущена" in error_msg.lower():
                return (row_num, 'skipped', error_msg)
            else:
                return (row_num, 'error', error_msg)
                
        except Exception as e:
            # Другие ошибки
            error_msg = str(e)
            return (row_num, 'error', error_msg)
    
    def _batch_update_statuses(
        self,
        worksheet,
        results: List[Tuple[int, str, str]]
    ) -> None:
        """
        Обновить статусы батчем (пачкой).
        
        Args:
            worksheet: gspread.Worksheet
            results: Список результатов обработки [(row_num, status, error), ...]
        """
        if not results:
            return
        
        status_col = self.get_status_column(worksheet)
        error_col = self.get_error_column(worksheet)
        
        updates = []
        
        for row_num, status, error_msg in results:
            if status == 'success':
                updates.append({
                    'range': f'{status_col}{row_num}',
                    'values': [['успешно']]
                })
            elif status == 'error':
                updates.append({
                    'range': f'{status_col}{row_num}',
                    'values': [['ошибка']]
                })
                if error_msg:
                    updates.append({
                        'range': f'{error_col}{row_num}',
                        'values': [[error_msg[:500]]]
                    })
        
        if updates:
            try:
                worksheet.batch_update(updates)
                self.logger.info("Batch updated %d statuses", len(results))
            except Exception as e:
                self.logger.error("Failed to batch update statuses: %s", e)
    
    async def process_sheet(self) -> None:
        """
        Обработать всю таблицу с параллельной обработкой и батчингом.
        
        Алгоритм:
        1. Получить worksheet
        2. Найти строки со статусом "не загружено"
        3. Обработать строки параллельно (с семафором)
        4. Обновить статусы батчами
        """
        self.logger.info("=" * 60)
        self.logger.info("Processing sheet: %s", self.__class__.__name__)
        self.logger.info("Spreadsheet ID: %s", self.spreadsheet_id)
        self.logger.info("Worksheet: %s", self.worksheet_name)
        self.logger.info("Parallel tasks: %d, Batch size: %d", self.max_concurrent_tasks, self.batch_size)
        self.logger.info("=" * 60)
        
        try:
            # Получить worksheet
            worksheet = self.sheets_client.get_worksheet(
                self.spreadsheet_id,
                self.worksheet_name
            )
            
            # Получить необработанные строки
            unprocessed = self.sheets_client.get_unprocessed_rows(
                worksheet,
                status_column_name=self.get_status_column_name(),
                tg_id_column_name=self.get_tg_id_column_name()
            )
            
            if not unprocessed:
                self.logger.info("No unprocessed rows found")
                return
            
            # Фильтрация строк + проверка на дубли по tg_id
            rows_to_process = []
            seen_tg_ids = set()
            duplicate_rows = []
            
            for row_num, row_data in unprocessed:
                if not self.should_process_row(row_num, row_data):
                    continue
                
                # Проверка на дубль по tg_id
                try:
                    parsed = self.parse_row(row_data)
                    tg_id = parsed.get("tg_id", "").strip()
                    
                    if tg_id and tg_id in seen_tg_ids:
                        # Дубль найден!
                        self.logger.warning("Duplicate tg_id=%s found at row %d, marking as success", tg_id, row_num)
                        duplicate_rows.append((row_num, 'success', 'ДУБЛЬ'))
                        continue
                    
                    if tg_id:
                        seen_tg_ids.add(tg_id)
                    
                    rows_to_process.append((row_num, row_data))
                    
                except Exception as e:
                    # Если не удалось распарсить - все равно обрабатываем
                    self.logger.debug("Could not parse row %d for duplicate check: %s", row_num, e)
                    rows_to_process.append((row_num, row_data))
            
            # Сразу записываем дубли как успешно
            if duplicate_rows:
                self.logger.info("Found %d duplicate rows, marking as success", len(duplicate_rows))
                self._batch_update_statuses(worksheet, duplicate_rows)
            
            if not rows_to_process:
                self.logger.info("No rows to process after filtering")
                return
            
            self.logger.info("Total rows to process: %d (duplicates skipped: %d)", 
                           len(rows_to_process), len(duplicate_rows))
            
            # Счетчики
            success_count = 0
            error_count = 0
            skipped_count = 0
            
            # Семафор для ограничения параллельных задач
            semaphore = asyncio.Semaphore(self.max_concurrent_tasks)
            
            async def process_with_semaphore(row_num, row_data):
                async with semaphore:
                    return await self._process_single_row(row_num, row_data)
            
            # Обрабатываем батчами
            for i in range(0, len(rows_to_process), self.batch_size):
                batch = rows_to_process[i:i + self.batch_size]
                batch_num = i // self.batch_size + 1
                total_batches = (len(rows_to_process) + self.batch_size - 1) // self.batch_size
                
                self.logger.info("Processing batch %d/%d (%d rows)", batch_num, total_batches, len(batch))
                
                # Запускаем параллельную обработку батча
                tasks = [process_with_semaphore(row_num, row_data) for row_num, row_data in batch]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                
                # Обрабатываем результаты
                batch_results = []
                for idx, result in enumerate(results):
                    if isinstance(result, Exception):
                        row_num = batch[idx][0]
                        error_msg = str(result)
                        self.logger.error("Row %d failed with exception: %s", row_num, error_msg)
                        batch_results.append((row_num, 'error', error_msg))
                        error_count += 1
                    else:
                        row_num, status, error_msg = result
                        if status == 'success':
                            success_count += 1
                            self.logger.info("Row %d processed successfully", row_num)
                        elif status == 'error':
                            error_count += 1
                            self.logger.error("Row %d failed: %s", row_num, error_msg)
                        elif status == 'skipped':
                            skipped_count += 1
                            self.logger.info("Row %d skipped: %s", row_num, error_msg)
                        
                        batch_results.append((row_num, status, error_msg))
                
                # Батчинг обновления статусов
                self._batch_update_statuses(worksheet, batch_results)
                
                self.logger.info("Batch %d/%d completed: success=%d, errors=%d, skipped=%d", 
                               batch_num, total_batches, success_count, error_count, skipped_count)
            
            # Итоговая статистика
            self.logger.info("=" * 60)
            self.logger.info("Sheet processing completed")
            self.logger.info("Success: %d, Errors: %d, Skipped: %d", success_count, error_count, skipped_count)
            self.logger.info("=" * 60)
            
        except Exception as e:
            self.logger.error(
                "Fatal error processing sheet: %s",
                e,
                exc_info=True
            )
            raise
    
    async def close(self) -> None:
        """
        Закрыть все соединения (БД, клиенты).
        
        Вызывается после завершения обработки.
        """
        # Закрываем БД если есть storage
        if hasattr(self, 'storage'):
            try:
                await self.storage.close()
                self.logger.info("Database connections closed")
            except Exception as e:
                self.logger.error("Error closing database: %s", e)
