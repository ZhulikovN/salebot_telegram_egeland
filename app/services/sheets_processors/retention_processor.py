"""Процессор для таблицы Retention 25-26."""

import logging
from typing import Dict, Any
from uuid import uuid4
from app.services.sheets_processors.base_processor import BaseSheetProcessor
from app.services.salebot_client import SalebotClient
from app.services.amocrm_client import AmoCRMClient
from app.services.amojo_client import AmojoClient
from app.db.storage import get_conversation_storage
from app.settings import settings

logger = logging.getLogger(__name__)


class Retention2526Processor(BaseSheetProcessor):
    """Процессор для таблицы Retention 25-26 (Рабочий лист)."""
    
    def __init__(self, *args, **kwargs):
        """Инициализация процессора с клиентами."""
        super().__init__(*args, **kwargs)
        self.salebot = SalebotClient()
        self.amocrm = AmoCRMClient()
        self.amojo = AmojoClient()
        self.storage = get_conversation_storage()
    
    def parse_row(self, row: Dict[str, Any]) -> Dict[str, Any]:
        """
        Парсинг строки таблицы Retention 25-26.
        
        Колонки:
        - С какого направления ученик? (колонка E)
        - Предмет (колонка F)
        - ССЫЛКА на тг ученика (колонка G)
        - Имя ученика (колонка H)
        - В каком классе ученик? (колонка I)
        - Номертелефонаученика (колонка J, слитно!)
        - ТГ айди (колонка W)
        - Причина ухода (новое поле)
        
        Args:
            row: Сырые данные из таблицы
            
        Returns:
            Нормализованные данные
        """
        tg_link = str(row.get("ССЫЛКА на тг ученика", "")).strip()
        tg_username = None
        
        # Извлекаем username из ссылки или просто текста
        if tg_link:
            if "t.me/" in tg_link:
                # Полная ссылка: https://t.me/username
                tg_username = tg_link.split("t.me/")[-1].strip("/")
            elif tg_link.startswith("@"):
                # С префиксом @: @username
                tg_username = tg_link[1:]
            else:
                # Просто username без префикса
                tg_username = tg_link
        
        return {
            "direction": str(row.get("С какого направления ученик?", "")).strip(),
            "subject": str(row.get("Предмет", "")).strip(),
            "tg_link": tg_link,
            "tg_username": tg_username,
            "student_name": str(row.get("Имя ученика", "")).strip(),
            "class": str(row.get("В каком классе ученик?", "")).strip(),
            "phone": str(row.get("Номертелефонаученика", "")).strip(),
            "tg_id": str(row.get("ТГ айди", "")).strip(),
            "leave_reason": str(row.get("Причина ухода", "")).strip(),
        }
    
    async def process_deal(self, data: Dict[str, Any]) -> None:
        """
        Создать или обновить сделку в amoCRM.
        
        Логика:
        1. Проверить наличие tg_id (если пустой - пропустить)
        2. Поиск клиента в Salebot по tg_id (если нет - ошибка "Диалога нет")
        3. Поиск контакта в amoCRM по tg_id
        4. Создать/найти сделку
        5. Создать чат в amojo
        6. Перенести последние 5 сообщений из Salebot
        
        Args:
            data: Нормализованные данные из parse_row()
        """
        tg_id = data["tg_id"]
        student_name = data["student_name"]
        
        # Проверка: ТГ айди должен быть заполнен и быть числом
        if not tg_id or not tg_id.isdigit():
            raise ValueError(f"ТГ айди пустой или некорректный: '{tg_id}' - строка пропущена")
        
        self.logger.info("Processing deal for tg_id=%s, student=%s", tg_id, student_name)
        
        # 1. Поиск клиента в Salebot (обязательно!)
        self.logger.info("Searching client in Salebot: tg_id=%s", tg_id)
        salebot_client_id = None
        
        try:
            salebot_response = await self.salebot.load_client(
                platform_id=int(tg_id),
                group_id="ElAuthBot"
            )
            
            if salebot_response and salebot_response.get("status") == "success":
                items = salebot_response.get("items", [])
                if items and len(items) > 0:
                    salebot_client_id = items[0].get("id")
                    if salebot_client_id:
                        self.logger.info("Client found in Salebot: client_id=%s", salebot_client_id)
                    else:
                        self.logger.warning("Client found but no id in response")
                else:
                    self.logger.warning("Client not found in Salebot: tg_id=%s", tg_id)
            else:
                self.logger.warning("Salebot returned error or empty response")
        except Exception as e:
            self.logger.warning("Error searching in Salebot: %s", e)
        
        # Если нет в Salebot - ПРОПУСКАЕМ строку (не создаем контакт и сделку)
        if not salebot_client_id:
            self.logger.warning("Клиент не найден в Salebot, пропускаем строку")
            raise ValueError("Диалога нет")
        
        # 2. Поиск контакта в amoCRM
        self.logger.info("Searching contact in amoCRM: tg_id=%s", tg_id)
        contact = await self.amocrm.find_contact_by_tg_id(tg_id)
        
        if contact:
            # Контакт найден
            contact_id = contact["id"]
            self.logger.info("Contact found in amoCRM: id=%s", contact_id)
            
            # Обновляем email, phone и tg_username, если они есть в таблице
            fields_to_update = {}
            if data.get("phone"):
                fields_to_update["PHONE"] = data["phone"]
            if data.get("tg_username"):
                fields_to_update["TG_USERNAME"] = data["tg_username"]
            
            if fields_to_update:
                await self.amocrm.update_contact_fields(contact_id, fields_to_update)
                self.logger.info("Contact updated with phone/tg_username")
            
            # Проверяем открытые сделки
            open_lead = await self.amocrm.check_duplicate_lead(contact_id=contact_id)
            
            if open_lead:
                # Есть открытая сделка
                lead_id = open_lead["id"]
                self.logger.info("Open lead found: id=%s", lead_id)
                
                # Создаем задачу "новый должник" (срок 1 сутки)
                try:
                    task_id = await self.amocrm.create_task(
                        lead_id=lead_id,
                        text="новый должник",
                        task_type_id=1,  # Звонок
                        complete_till_days=1
                    )
                    self.logger.info("✓ Task 'новый должник' created: id=%s", task_id)
                except Exception as task_error:
                    self.logger.warning("Failed to create task for lead %s: %s", lead_id, task_error)
            else:
                # Нет открытых сделок - создаем новую
                lead_id = await self.amocrm.create_lead(
                    contact_id=contact_id,
                    bot_name="Retention 25-26",
                    course=data.get("direction"),
                    where_studied=data.get("subject"),
                    student_class=data.get("class"),
                    leave_reason=data.get("leave_reason"),
                )
                self.logger.info("New lead created: id=%s", lead_id)
        else:
            # Контакт не найден - создаем новый
            self.logger.info("Contact not found, creating new...")
            contact_id = await self.amocrm.create_contact(
                name=student_name or "Ученик",
                tg_id=tg_id,
                tg_username=data.get("tg_username"),
                phone=data.get("phone"),
            )
            self.logger.info("New contact created: id=%s", contact_id)
            
            # Создаем сделку для нового контакта
            lead_id = await self.amocrm.create_lead(
                contact_id=contact_id,
                bot_name="Retention 25-26",
                course=data.get("direction"),
                where_studied=data.get("subject"),
                student_class=data.get("class"),
                leave_reason=data.get("leave_reason"),
            )
            self.logger.info("New lead created: id=%s", lead_id)
        
        # 3. ВСЕГДА создаем новый чат для каждой строки из таблицы
        conversation_id = str(uuid4())
        self.logger.info("Creating new chat in amojo: conversation_id=%s", conversation_id)
        
        chat_id = await self.amocrm.create_chat_in_amojo(
            conversation_id=conversation_id,
            user_id=f"tg:{tg_id}",
            user_name=student_name or "Ученик",
        )
        self.logger.info("Chat created: chat_id=%s", chat_id)
        
        # 4. Привязать чат к контакту
        await self.amocrm.link_chat_to_contact(contact_id=contact_id, chat_id=chat_id)
        self.logger.info("Chat linked to contact")
        
        # 5. Сохранить маппинг в БД
        await self.storage.create_conversation(
            conversation_id=conversation_id,
            salebot_client_id=salebot_client_id,
            platform_id=tg_id,
            contact_id=contact_id,
            lead_id=lead_id,
            client_name=student_name or "Ученик",
            tg_username=data.get("tg_username"),
            bot_name="Retention 25-26",
        )
        self.logger.info("Conversation saved to DB")
        
        # 6. Получить историю из Salebot и перенести последние 5 сообщений
        try:
            self.logger.info("Fetching history from Salebot...")
            history = await self.salebot.get_history(client_id=salebot_client_id)
            
            # Salebot возвращает историю в поле "result" (от НОВЫХ к СТАРЫМ!)
            messages = history.get("result", [])
            if messages:
                # Сначала фильтруем только сообщения клиента
                client_messages = [
                    msg for msg in messages 
                    if msg.get("client_replica", True) and msg.get("text")
                ]
                
                # Берем первые 5 (т.к. Salebot возвращает от новых к старым)
                last_messages = client_messages[:5] if len(client_messages) >= 5 else client_messages
                self.logger.info("Transferring %d latest client messages to amojo", len(last_messages))
                
                for msg in last_messages:
                    msg_text = msg.get("text", "")
                    msg_id = msg.get("id", uuid4().hex)
                    
                    # Отправляем в amojo
                    await self.amojo.send_incoming_message(
                        conversation_id=conversation_id,
                        msgid=f"history:{msg_id}",
                        sender_id=f"tg:{tg_id}",
                        sender_name=student_name or "Ученик",
                        text=msg_text,
                        silent=True,
                        profile_link=None,
                    )
                
                self.logger.info("History transferred: %d messages", len(last_messages))
            else:
                # Если истории нет, отправляем приветственное сообщение от клиента
                self.logger.info("No history in Salebot, sending welcome message from client...")
                await self.amojo.send_incoming_message(
                    conversation_id=conversation_id,
                    msgid=f"welcome:{uuid4().hex}",
                    sender_id=f"tg:{tg_id}",
                    sender_name=student_name or "Ученик",
                    text="Здравствуйте!",
                    silent=True,
                    profile_link=None,
                )
                self.logger.info("Welcome message sent (from client)")
        except Exception as e:
            self.logger.warning("Error fetching/transferring history: %s", e)
        
        self.logger.info("Deal processing completed successfully")
    
    def get_tg_id_column_name(self) -> str:
        """
        Получить название колонки с ТГ айди.
        
        Returns:
            "ТГ айди" - для таблицы Retention
        """
        return "ТГ айди"
    
    def get_status_column_name(self) -> str:
        """
        Получить название колонки со статусом обработки.
        
        Returns:
            "Статус обработки" - стандартное название
        """
        return "Статус обработки"
    
    def should_process_row(self, row_num: int, row_data: Dict[str, Any]) -> bool:
        """
        Фильтр для обработки только строк начиная с указанного номера.
        
        Логика:
        Обрабатываем только строки с номером >= GOOGLE_RETENTION_START_ROW
        
        Args:
            row_num: Номер строки
            row_data: Данные строки
            
        Returns:
            True если строку нужно обработать (>= start_row), False если пропустить
        """
        return row_num >= settings.GOOGLE_RETENTION_START_ROW
    
    async def close(self) -> None:
        """Закрыть все соединения (БД, HTTP клиенты)."""
        # Закрываем aiohttp сессию AmoCRM
        if hasattr(self, 'amocrm'):
            await self.amocrm.close()
        
        # Закрываем БД через родительский метод
        await super().close()
