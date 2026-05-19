"""Менеджер диалогов Salebot ↔ amoCRM."""
import logging
from uuid import uuid4

from app.config.bot_routing import get_bot_config
from app.db.storage import get_conversation_storage
from app.services.amocrm_client import AmoCRMClient
from app.services.amojo_client import AmojoClient
from app.services.salebot_client import SalebotClient
from app.settings import settings
logger = logging.getLogger(__name__)


class ConversationManager:
    """
    Менеджер для управления диалогами между Salebot и amoCRM.

    При первом сообщении клиента:
    - Ищет или создаёт контакт в AMO
    - Проверяет дубль сделки в текущей воронке
    - Создаёт сделку если дубля нет
    - Создаёт чат в amojo и привязывает к контакту
    - Сохраняет маппинг в БД

    При повторных сообщениях:
    - Находит диалог в БД по (platform_id, bot_name)
    - Отправляет сообщение в уже существующий чат amojo
    """

    def __init__(self) -> None:
        """Инициализация менеджера."""
        self.storage = get_conversation_storage()
        self.amocrm = AmoCRMClient()
        self.amojo = AmojoClient()
        self.salebot = SalebotClient()

    async def handle_salebot_message(
        self,
        platform_id: str,
        bot_name: str,
        salebot_client_id: int,
        client_name: str,
        message_text: str | None,
        attachments: list | None = None,
        tg_username: str | None = None,
        utm_data: dict | None = None,
    ) -> str | None:
        """
        Обработать входящее сообщение от Salebot.

        Алгоритм:
        1. Найти диалог по (platform_id, bot_name)
        2. Если диалог есть → отправить сообщение в amojo
        3. Если диалога нет → создать контакт+сделку+чат → сохранить → отправить

        Args:
            platform_id: Telegram ID клиента
            bot_name: Название бота
            salebot_client_id: client.id из Salebot для ответов
            client_name: Имя клиента (уже с fallback на tg_username)
            message_text: Текст сообщения
            tg_username: Telegram username (без @)

        Returns:
            conversation_id чата или None при ошибке
        """
        conversation = await self.storage.get_by_platform_id(platform_id, bot_name)

        if not conversation:
            logger.info(
                "No conversation found for platform_id=%s, bot=%s — creating new",
                platform_id,
                bot_name,
            )
            try:
                conversation = await self._create_new_conversation(
                    platform_id=platform_id,
                    bot_name=bot_name,
                    salebot_client_id=salebot_client_id,
                    client_name=client_name,
                    tg_username=tg_username,
                    utm_data=utm_data,
                )
            except Exception as e:
                if "duplicate key value" in str(e) or "unique constraint" in str(e).lower():
                    logger.warning("Race condition: conversation already created, fetching from DB")
                    conversation = await self.storage.get_by_platform_id(platform_id, bot_name)
                    if not conversation:
                        raise
                else:
                    raise

            if not conversation:
                logger.error(
                    "Failed to create conversation for platform_id=%s, bot=%s",
                    platform_id,
                    bot_name,
                )
                return None

        logger.info(
            "Sending message to amojo: conversation=%s",
            conversation.conversation_id,
        )

        is_first_message = conversation.messages_count == 0
        attachments = attachments or []

        if message_text:
            await self.amojo.send_incoming_message(
                conversation_id=conversation.conversation_id,
                msgid=f"salebot:{uuid4().hex}",
                sender_id=f"tg:{platform_id}",
                sender_name=client_name,
                text=message_text,
                silent=not is_first_message,
            )
            await self.storage.increment_message_count(conversation.conversation_id)
            is_first_message = False

        for media_url in attachments:
            await self.amojo.send_incoming_message(
                conversation_id=conversation.conversation_id,
                msgid=f"salebot:{uuid4().hex}",
                sender_id=f"tg:{platform_id}",
                sender_name=client_name,
                text="",
                silent=not is_first_message,
                media_url=media_url,
            )
            await self.storage.increment_message_count(conversation.conversation_id)
            is_first_message = False

        if not message_text and not attachments:
            logger.warning(
                "Empty message (no text, no attachments): platform_id=%s, conversation=%s",
                platform_id,
                conversation.conversation_id,
            )

        return conversation.conversation_id

    async def _create_new_conversation(
        self,
        platform_id: str,
        bot_name: str,
        salebot_client_id: int,
        client_name: str,
        tg_username: str | None,
        utm_data: dict | None = None,
    ):
        """
        Создать новый диалог: контакт → сделка → чат amojo → запись в БД.

        Args:
            platform_id: Telegram ID клиента
            bot_name: Название бота
            salebot_client_id: client.id из Salebot
            client_name: Имя клиента
            tg_username: Telegram username (без @)

        Returns:
            Созданная запись Conversation или None при ошибке
        """
        try:
            # Определяем конфигурацию воронки/этапа/названия по боту
            bot_config = get_bot_config(bot_name)
            logger.info(
                "Bot config for %r: pipeline=%s, status=%s, lead_name=%r",
                bot_name,
                bot_config.pipeline_id,
                bot_config.status_id,
                bot_config.lead_name or "(default)",
            )

            # 1. Найти или создать контакт
            contact_id = await self._find_or_create_contact(
                platform_id=platform_id,
                client_name=client_name,
                tg_username=tg_username,
                utm_data=utm_data,
            )

            # 2. Проверить дубль сделки в нужной воронке (зависит от бота)
            duplicate_lead = await self.amocrm.check_duplicate_lead(
                contact_id=contact_id,
                pipeline_id=bot_config.pipeline_id,
            )

            if duplicate_lead:
                lead_id = duplicate_lead["id"]
                logger.info(
                    "Duplicate lead found in pipeline %s: lead_id=%s",
                    bot_config.pipeline_id,
                    lead_id,
                )
                # First-touch для дубля: заполняем только пустые UTM поля сделки
                if utm_data:
                    await self._update_lead_utm_first_touch(lead_id, utm_data)
            else:
                lead_id = await self.amocrm.create_lead(
                    contact_id=contact_id,
                    bot_name=bot_name,
                    pipeline_id=bot_config.pipeline_id,
                    status_id=bot_config.status_id,
                    lead_name=bot_config.lead_name or None,
                    utm_data=utm_data,
                )
                logger.info("New lead created: lead_id=%s", lead_id)

            # 3. Создать чат в amojo
            # conversation_id — наш идентификатор, с ним же отправляем сообщения
            amojo_conversation_id = str(uuid4())
            profile_link = f"https://t.me/{tg_username}" if tg_username else None

            chat_id = await self.amocrm.create_chat_in_amojo(
                conversation_id=amojo_conversation_id,
                user_id=f"tg:{platform_id}",
                user_name=client_name,
                profile_link=profile_link,
            )

            # 4. Привязать чат к контакту
            await self.amocrm.link_chat_to_contact(
                contact_id=contact_id,
                chat_id=chat_id,
            )

            # 5. Сохранить маппинг в БД (conversation_id = наш UUID, не chat_id от amojo)
            conversation = await self.storage.create_conversation(
                conversation_id=amojo_conversation_id,
                salebot_client_id=salebot_client_id,
                platform_id=platform_id,
                contact_id=contact_id,
                lead_id=lead_id,
                client_name=client_name,
                tg_username=tg_username,
                bot_name=bot_name,
            )

            logger.info(
                "New conversation created: conversation_id=%s, contact=%s, lead=%s",
                chat_id,
                contact_id,
                lead_id,
            )

            return conversation

        except Exception as e:
            logger.error(
                "Error creating new conversation for platform_id=%s: %s",
                platform_id,
                e,
                exc_info=True,
            )
            return None

    async def _find_or_create_contact(
        self,
        platform_id: str,
        client_name: str,
        tg_username: str | None,
        utm_data: dict | None = None,
    ) -> int:
        """
        Найти существующий контакт или создать новый.

        Поиск по TG ID → поиск по username → создание.
        При нахождении: дополняет только пустые tg-поля (старые данные приоритетнее).
        UTM хранятся в сделке, не в контакте.

        Args:
            platform_id: Telegram ID
            client_name: Имя клиента
            tg_username: Telegram username (без @)
            utm_data: не используется здесь (UTM пишутся в сделку)

        Returns:
            ID контакта в AMO
        """
        # Поиск по TG ID
        contact = await self.amocrm.find_contact_by_tg_id(platform_id)

        # Поиск по username если не нашли по TG ID
        if not contact and tg_username:
            contact = await self.amocrm.find_contact_by_username(tg_username)

        if contact:
            contact_id = contact["id"]
            logger.info("Found existing contact: %s", contact_id)

            # Дополняем только пустые TG-поля (старые данные приоритетнее)
            existing_fields = self.amocrm._parse_custom_fields(
                contact.get("custom_fields_values")
            )

            fields_to_update: dict[int, str] = {}

            if not existing_fields.get(settings.FIELD_TG_ID):
                fields_to_update[settings.FIELD_TG_ID] = platform_id

            if tg_username and not existing_fields.get(settings.FIELD_TG_USERNAME):
                fields_to_update[settings.FIELD_TG_USERNAME] = tg_username

            if fields_to_update:
                await self.amocrm.update_contact(contact_id, fields_to_update)

            return contact_id

        # Создаём новый контакт
        contact_id = await self.amocrm.create_contact(
            name=client_name,
            tg_id=platform_id,
            tg_username=tg_username,
        )
        logger.info("New contact created: %s", contact_id)
        return contact_id

    async def _update_lead_utm_first_touch(self, lead_id: int, utm_data: dict) -> None:
        """
        Заполнить UTM поля существующей сделки — только пустые (first-touch).

        Args:
            lead_id: ID сделки
            utm_data: UTM-метки из Salebot
        """
        try:
            lead_response = await self.amocrm._make_request("GET", f"/leads/{lead_id}")
            existing = self.amocrm._parse_custom_fields(
                lead_response.get("custom_fields_values")
            )

            utm_field_map = {
                settings.FIELD_UTM_SOURCE:   utm_data.get("utm_source"),
                settings.FIELD_UTM_MEDIUM:   utm_data.get("utm_medium"),
                settings.FIELD_UTM_CAMPAIGN: utm_data.get("utm_campaign"),
                settings.FIELD_UTM_TERM:     utm_data.get("utm_term"),
                settings.FIELD_UTM_CONTENT:  utm_data.get("utm_content"),
            }

            fields_to_update = {
                field_id: value
                for field_id, value in utm_field_map.items()
                if value and not existing.get(field_id)
            }

            if fields_to_update:
                await self.amocrm.update_lead(lead_id, fields_to_update)
                logger.info(
                    "UTM first-touch updated for duplicate lead %s: %s",
                    lead_id,
                    fields_to_update,
                )
            else:
                logger.debug("UTM fields already filled for lead %s, skipping", lead_id)
        except Exception as e:
            logger.warning("Failed to update UTM for lead %s: %s", lead_id, e)

    async def handle_amojo_message(
        self,
        conversation_id: str,
        message_text: str | None,
        message_type: str = "text",
        media_url: str | None = None,
    ) -> None:
        """
        Обработать ответ менеджера из amoCRM.

        Args:
            conversation_id: UUID чата
            message_text: Текст сообщения от менеджера
            message_type: Тип сообщения (text/picture/voice/video/file)
            media_url: URL медиафайла (для не-текстовых сообщений)

        Raises:
            ValueError: Если диалог не найден
        """
        conversation = await self.storage.get_by_conversation_id(conversation_id)
        if not conversation:
            raise ValueError(f"Conversation not found: {conversation_id}")

        # Маппинг типов amojo → Salebot attachment_type
        amojo_to_salebot_type: dict[str, str] = {
            "picture": "image",
            "voice": "audio",
            "video": "video",
            "file": "file",
        }
        salebot_attachment_type = amojo_to_salebot_type.get(message_type)

        # Проксируем медиафайл через наш сервер (drive-b.amocrm.ru требует авторизацию)
        public_media_url: str | None = None
        if media_url and salebot_attachment_type:
            from app.services.media_proxy import download_and_proxy
            public_media_url = await download_and_proxy(media_url)
            if not public_media_url:
                logger.error(
                    "Failed to proxy media, sending text only: url=%s",
                    media_url,
                )

        await self.salebot.send_message(
            client_id=conversation.salebot_client_id,
            message=message_text or "",
            attachment_url=public_media_url,
            attachment_type=salebot_attachment_type if public_media_url else None,
        )

        logger.info(
            "Message sent to Salebot: client_id=%s, type=%s",
            conversation.salebot_client_id,
            message_type,
        )
        await self.storage.increment_message_count(conversation_id)

    async def close(self) -> None:
        """Закрыть все соединения."""
        await self.amocrm.close()
        await self.salebot.close()
        await self.storage.close()
        logger.info("ConversationManager connections closed")
