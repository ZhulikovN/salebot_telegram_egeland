"""Процессор для таблицы 'айди игноры неоплат пг'."""

import logging
from typing import Dict, Any
from uuid import uuid4
from app.services.sheets_processors.base_processor import BaseSheetProcessor
from app.services.salebot_client import SalebotClient
from app.services.amocrm_client import AmoCRMClient
from app.services.amojo_client import AmojoClient
from app.db.storage import get_conversation_storage

logger = logging.getLogger(__name__)


class PgIgnoreUnpaidProcessor(BaseSheetProcessor):
    """Процессор для таблицы 'айди игноры неоплат пг'."""
    
    def __init__(self, bot_name: str, *args, **kwargs):
        """
        Инициализация процессора с клиентами.
        
        Args:
            bot_name: Название бота для этой таблицы
        """
        super().__init__(*args, **kwargs)
        self.bot_name = bot_name
        self.salebot = SalebotClient()
        self.amocrm = AmoCRMClient()
        self.amojo = AmojoClient()
        self.storage = get_conversation_storage()
    
    def parse_row(self, row: Dict[str, Any]) -> Dict[str, Any]:
        """
        Парсинг строки таблицы 'айди игноры неоплат пг'.
        
        Колонки:
        - ID курса
        - Предмет
        - TG ID ученика
        - Телефон ученика
        - ТГ тег (username)
        
        Args:
            row: Сырые данные из таблицы
            
        Returns:
            Нормализованные данные
        """
        # Обязательные поля
        course_id = str(row.get("ID курса", "")).strip()
        subject = str(row.get("Предмет", "")).strip()
        tg_id = str(row.get("TG ID ученика", "")).strip()
        phone = str(row.get("Телефон ученика", "")).strip()
        
        # ТГ тег (username) - может быть в разных форматах
        tg_tag = str(row.get("ТГ тег", "")).strip()
        tg_username = None
        
        if tg_tag:
            if "t.me/" in tg_tag:
                tg_username = tg_tag.split("t.me/")[-1].strip("/")
            elif tg_tag.startswith("@"):
                tg_username = tg_tag[1:]
            else:
                tg_username = tg_tag
        
        return {
            "course_id": course_id,
            "subject": subject,
            "tg_id": tg_id,
            "phone": phone,
            "tg_username": tg_username,
        }
    
    async def process_deal(self, data: Dict[str, Any]) -> None:
        """
        Создать сделку в amoCRM без поиска дублей.
        
        Логика:
        1. Проверить наличие tg_id ИЛИ phone (хотя бы одно должно быть)
        2. Если есть tg_id - поиск клиента в Salebot (если нет - пропустить)
        3. Поиск контакта в amoCRM по tg_id или phone
        4. Создать НОВУЮ сделку в этапе "Неразобранное" (без проверки на дубли)
        5. Если есть tg_id и salebot_client_id - создать чат и перенести историю
        
        Args:
            data: Нормализованные данные из parse_row()
        """
        tg_id = data["tg_id"]
        phone = data["phone"]
        subject = data["subject"]
        
        # Проверка: должен быть хотя бы один идентификатор
        has_tg_id = tg_id and tg_id.isdigit()
        has_phone = phone and len(phone) > 0
        
        if not has_tg_id and not has_phone:
            raise ValueError("Нет ТГ айди и телефона - строка пропущена")
        
        self.logger.info(
            "Processing deal: tg_id=%s, phone=%s, subject=%s",
            tg_id if has_tg_id else "N/A",
            phone if has_phone else "N/A",
            subject,
        )
        
        # 1. Поиск клиента в Salebot (только если есть tg_id)
        salebot_client_id = None
        
        if has_tg_id:
            self.logger.info("Searching client in Salebot: tg_id=%s", tg_id)
            
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
            
            # Если есть tg_id но нет в Salebot - ОШИБКА
            if not salebot_client_id:
                self.logger.warning("Клиент не найден в Salebot, пропускаем строку")
                raise ValueError("Диалога нет")
        
        # 2. Поиск контакта в amoCRM (только по tg_id если есть)
        contact = None
        
        if has_tg_id:
            self.logger.info("Searching contact in amoCRM by tg_id: %s", tg_id)
            contact = await self.amocrm.find_contact_by_tg_id(tg_id)
        
        if contact:
            # Контакт найден
            contact_id = contact["id"]
            self.logger.info("Contact found in amoCRM: id=%s", contact_id)
            
            # Обновляем поля контакта
            fields_to_update = {}
            if has_phone and data.get("phone"):
                fields_to_update["PHONE"] = data["phone"]
            if data.get("tg_username"):
                fields_to_update["TG_USERNAME"] = data["tg_username"]
            
            if fields_to_update:
                await self.amocrm.update_contact_fields(contact_id, fields_to_update)
                self.logger.info("Contact updated with phone/tg_username")
        else:
            # Контакт не найден - создаем новый
            self.logger.info("Contact not found, creating new...")
            contact_id = await self.amocrm.create_contact(
                name=subject or "Ученик",
                tg_id=tg_id if has_tg_id else None,
                tg_username=data.get("tg_username"),
                phone=phone if has_phone else None,
            )
            self.logger.info("New contact created: id=%s", contact_id)
        
        # 3. ВСЕГДА создаем новую сделку (без проверки дублей!)
        self.logger.info("Creating new lead (no duplicate check)...")
        lead_id = await self.amocrm.create_lead(
            contact_id=contact_id,
            bot_name=self.bot_name,
            where_studied=subject,
            pipeline_id=self.pipeline_id,
            status_id=self.status_id,
        )
        self.logger.info("New lead created: id=%s", lead_id)
        
        # 4. Создание чата и перенос истории (только если есть tg_id и salebot_client_id)
        if has_tg_id and salebot_client_id:
            # Создаем чат
            conversation_id = str(uuid4())
            self.logger.info("Creating chat in amojo: conversation_id=%s", conversation_id)
            
            chat_id = await self.amocrm.create_chat_in_amojo(
                conversation_id=conversation_id,
                user_id=f"tg:{tg_id}",
                user_name=subject or "Ученик",
            )
            self.logger.info("Chat created: chat_id=%s", chat_id)
            
            # Привязываем чат к контакту
            await self.amocrm.link_chat_to_contact(contact_id=contact_id, chat_id=chat_id)
            self.logger.info("Chat linked to contact")
            
            # Сохраняем маппинг в БД
            await self.storage.create_conversation(
                conversation_id=conversation_id,
                salebot_client_id=salebot_client_id,
                platform_id=tg_id,
                contact_id=contact_id,
                lead_id=lead_id,
                client_name=subject or "Ученик",
                tg_username=data.get("tg_username"),
                bot_name=self.bot_name,
            )
            self.logger.info("Conversation saved to DB")
            
            # Переносим историю из Salebot
            try:
                self.logger.info("Fetching history from Salebot...")
                history = await self.salebot.get_history(client_id=salebot_client_id)
                
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
                        
                        await self.amojo.send_incoming_message(
                            conversation_id=conversation_id,
                            msgid=f"history:{msg_id}",
                            sender_id=f"tg:{tg_id}",
                            sender_name=subject or "Ученик",
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
                        sender_name=subject or "Ученик",
                        text="Здравствуйте!",
                        silent=True,
                        profile_link=None,
                    )
                    self.logger.info("Welcome message sent (from client)")
            except Exception as e:
                self.logger.warning("Error fetching/transferring history: %s", e)
        else:
            self.logger.info("No chat created (no tg_id or salebot_client_id)")
        
        self.logger.info("Deal processing completed successfully")
    
    def get_tg_id_column_name(self) -> str:
        """Название колонки с ТГ айди."""
        return "TG ID ученика"
    
    def get_status_column_name(self) -> str:
        """Название колонки со статусом обработки."""
        return "Статус обработки"
    
    async def close(self) -> None:
        """Закрыть все соединения (БД, HTTP клиенты)."""
        # Закрываем aiohttp сессию AmoCRM
        if hasattr(self, 'amocrm'):
            await self.amocrm.close()
        
        # Закрываем БД через родительский метод
        await super().close()
