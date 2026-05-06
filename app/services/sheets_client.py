"""Клиент для работы с Google Sheets API."""

import logging
import gspread
from google.oauth2.service_account import Credentials
from typing import List, Tuple, Dict, Any
from app.settings import settings

logger = logging.getLogger(__name__)


class SheetsClient:
    """Клиент для работы с Google Sheets API."""
    
    def __init__(self):
        """Инициализация клиента с Service Account."""
        scopes = ['https://www.googleapis.com/auth/spreadsheets']
        
        try:
            creds = Credentials.from_service_account_file(
                settings.GOOGLE_SERVICE_ACCOUNT_JSON,
                scopes=scopes
            )
            self.client = gspread.authorize(creds)
            logger.info("✓ Google Sheets client initialized")
        except Exception as e:
            logger.error("✗ Failed to initialize Google Sheets client: %s", e)
            raise
    
    def get_worksheet(self, spreadsheet_id: str, worksheet_name: str):
        """
        Получить worksheet по ID таблицы и имени листа.
        
        Args:
            spreadsheet_id: ID таблицы из URL
            worksheet_name: Название листа
            
        Returns:
            gspread.Worksheet
        """
        try:
            sheet = self.client.open_by_key(spreadsheet_id)
            worksheet = sheet.worksheet(worksheet_name)
            logger.debug("Worksheet opened: %s / %s", spreadsheet_id, worksheet_name)
            return worksheet
        except Exception as e:
            logger.error(
                "Failed to open worksheet: %s / %s - %s",
                spreadsheet_id,
                worksheet_name,
                e
            )
            raise
    
    def find_column_letter(self, worksheet, column_name: str) -> str | None:
        """
        Найти букву колонки по её названию.
        
        Args:
            worksheet: gspread.Worksheet
            column_name: Название колонки
            
        Returns:
            Буква колонки (например "Z") или None если не найдена
        """
        try:
            # Получаем первую строку (заголовки)
            headers = worksheet.row_values(1)
            
            # Ищем колонку по названию
            for idx, header in enumerate(headers, start=1):
                if str(header).strip() == column_name:
                    # Преобразуем номер колонки в букву
                    return self._column_number_to_letter(idx)
            
            return None
        except Exception as e:
            logger.error("Error finding column '%s': %s", column_name, e)
            return None
    
    def _column_number_to_letter(self, col_num: int) -> str:
        """
        Преобразовать номер колонки в букву.
        
        Args:
            col_num: Номер колонки (1-based)
            
        Returns:
            Буква колонки (A, B, ..., Z, AA, AB, ...)
        """
        result = ""
        while col_num > 0:
            col_num -= 1
            result = chr(col_num % 26 + ord('A')) + result
            col_num //= 26
        return result
    
    def get_all_rows(self, worksheet) -> List[Dict[str, Any]]:
        """
        Получить все строки из таблицы.
        
        Обрабатывает таблицы с пустыми заголовками.
        
        Args:
            worksheet: gspread.Worksheet
            
        Returns:
            List[Dict] - список словарей с данными строк
        """
        try:
            # Сначала пробуем стандартный метод
            all_records = worksheet.get_all_records()
            logger.info("Total rows in sheet: %d", len(all_records))
            return all_records
        except Exception as e:
            # Если ошибка из-за пустых заголовков - читаем вручную
            if "duplicates" in str(e) or "header" in str(e):
                logger.warning("Table has empty headers, using manual parsing")
                
                # Читаем заголовки
                headers = worksheet.row_values(1)
                
                # Читаем все данные
                all_values = worksheet.get_all_values()
                
                # Преобразуем в словари (пропуская пустые заголовки)
                all_rows = []
                for row_values in all_values[1:]:  # Пропускаем первую строку (заголовки)
                    row_dict = {}
                    for idx, value in enumerate(row_values):
                        if idx < len(headers) and headers[idx]:  # Только если заголовок не пустой
                            row_dict[headers[idx]] = value
                    all_rows.append(row_dict)
                
                logger.info("Total rows in sheet: %d", len(all_rows))
                return all_rows
            else:
                logger.error("Failed to get all rows: %s", e)
                raise
    
    def get_unprocessed_rows(
        self,
        worksheet,
        status_column_name: str = "Статус обработки",
        tg_id_column_name: str = "Тг айди ученика"
    ) -> List[Tuple[int, Dict[str, Any]]]:
        """
        Получить строки для обработки (пустой статус или ошибка).
        
        Логика:
        - Пропускаем строки без ТГ айди (пустые строки, скрытые строки)
        - Обрабатываем строки с пустым статусом (новые)
        - Обрабатываем строки со статусом "ошибка" (повтор)
        - Пропускаем строки со статусом "успешно"
        
        Автоматически пропускает все строки до начала таблицы с данными.
        
        Args:
            worksheet: gspread.Worksheet
            status_column_name: Название колонки со статусом
            tg_id_column_name: Название колонки с ТГ айди
            
        Returns:
            List[(row_number, row_data)]
            row_number - номер строки в таблице (для обновления)
            row_data - словарь с данными строки
        """
        all_records = self.get_all_rows(worksheet)
        
        unprocessed = []
        for idx, row in enumerate(all_records):
            row_num = idx + 2  # +2 потому что idx начинается с 0, а строки с 1, и первая строка - заголовки
            
            # Проверяем наличие ТГ айди
            tg_id = str(row.get(tg_id_column_name, "")).strip()
            if not tg_id:
                continue  # Пропускаем пустые строки (включая скрытые и строки до начала таблицы)
            
            # Проверяем, что ТГ айди - это цифры
            if not tg_id.isdigit():
                continue  # Пропускаем строки с некорректным ТГ айди (например "-")
            
            # Проверяем статус обработки
            status = str(row.get(status_column_name, "")).strip().lower()
            
            # Обрабатываем если статус пустой или "ошибка"
            if status == "" or status == "ошибка":
                unprocessed.append((row_num, row))
        
        logger.info("Unprocessed rows: %d (empty status or error)", len(unprocessed))
        return unprocessed
    
    def update_status(
        self,
        worksheet,
        row_num: int,
        status_col: str,
        status: str,
        error_col: str = None,
        error: str = None
    ) -> None:
        """
        Обновить статус обработки строки.
        
        Args:
            worksheet: gspread.Worksheet
            row_num: Номер строки в таблице
            status_col: Буква колонки статуса (например, "J")
            status: Статус ("успешно" или "ошибка")
            error_col: Буква колонки ошибки (например, "K")
            error: Текст ошибки (опционально)
        """
        try:
            updates = [
                {
                    'range': f'{status_col}{row_num}',
                    'values': [[status]]
                }
            ]
            
            if error and error_col:
                updates.append({
                    'range': f'{error_col}{row_num}',
                    'values': [[error[:500]]]  # Ограничение длины
                })
            
            worksheet.batch_update(updates)
            logger.debug("Status updated: row %d = %s", row_num, status)
            
        except Exception as e:
            logger.error("Failed to update status for row %d: %s", row_num, e)
            raise
