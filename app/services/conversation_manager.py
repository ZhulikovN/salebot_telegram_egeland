"""Менеджер диалогов Salebot ↔ amoCRM."""
import hashlib
import logging
from uuid import uuid4

from app.config.bot_routing import get_bot_config
from app.db.storage import Conversation, get_conversation_storage
from app.services.amocrm_client import AmoCRMClient
from app.services.amojo_client import AmojoClient, AmojoNotFoundError
from app.services.salebot_client import SalebotClient
from app.settings import settings
from app.utils.redis_connection import get_redis

logger = logging.getLogger(__name__)

_ECHO_TTL = 15  # секунд


def _echo_key(platform_id: str, bot_name: str, text: str) -> str:
    """Redis-ключ для дедупликации эхо-сообщений менеджера."""
    digest = hashlib.md5(text.encode("utf-8")).hexdigest()
    return f"echo:{platform_id}:{bot_name}:{digest}"


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

        # current_lead — данные сделки из get_lead, переиспользуются для pipeline_triggers
        # чтобы не делать лишний GET-запрос.
        current_lead: dict | None = None

        # Если диалог найден — проверяем, что привязанная сделка ещё открыта.
        # Если сделка закрыта (142/143) или недоступна (слита/удалена через merge) —
        # удаляем запись и создаём новый диалог.
        if conversation and conversation.lead_id:
            lead = await self.amocrm.get_lead(conversation.lead_id)
            logger.info(
                "get_lead(%s) returned: status_id=%s, lead=%s",
                conversation.lead_id,
                lead.get("status_id") if lead else None,
                "None" if lead is None else "absorbed(204)" if lead == {} else "dict",
            )
            if lead is None or lead == {}:
                # None  → 404 / API-ошибка: сделка удалена или недоступна.
                # {} → 204: сделка поглощена через NOVA merge.
                # В обоих случаях переоткрываем диалог.
                reason = "absorbed (204)" if lead == {} else "not found (404/error)"
                logger.info(
                    "Lead %s %s, reopening conversation for platform_id=%s, bot=%s",
                    conversation.lead_id,
                    reason,
                    platform_id,
                    bot_name,
                )
                conversation = await self._reopen_conversation(
                    conversation=conversation,
                    platform_id=platform_id,
                    bot_name=bot_name,
                    salebot_client_id=salebot_client_id,
                    client_name=client_name,
                    tg_username=tg_username,
                    utm_data=utm_data,
                )
                # После reopen новая сделка создана в начальной воронке бота.
                # Устанавливаем current_lead чтобы pipeline_triggers могли сработать.
                if conversation:
                    bot_config_reopen = get_bot_config(bot_name)
                    current_lead = {
                        "pipeline_id": bot_config_reopen.pipeline_id,
                        "status_id": bot_config_reopen.status_id,
                    }
            else:
                closed_statuses = {settings.STATUS_SUCCESS, settings.STATUS_CLOSED}
                lead_status = lead.get("status_id")
                if lead_status is not None and lead_status in closed_statuses:
                #     # Сделка закрыта: создаём новую сделку, переиспользуем amojo-чат.
                #     logger.info(
                #         "Lead %s is closed (status=%s), reopening conversation for platform_id=%s, bot=%s",
                #         conversation.lead_id,
                #         lead_status,
                #         platform_id,
                #         bot_name,
                #     )
                #     conversation = await self._reopen_conversation(
                #         conversation=conversation,
                #         platform_id=platform_id,
                #         bot_name=bot_name,
                #         salebot_client_id=salebot_client_id,
                #         client_name=client_name,
                #         tg_username=tg_username,
                #         utm_data=utm_data,
                #     )
                # else:
                #     # status_id=None — переходное состояние AmoCRM (слияние контактов/сделок
                #     # через NOVA или другой виджет). Сделка физически существует, считаем
                #     # её открытой и доставляем сообщение без создания дубля.
                #     current_lead = lead
                #
                    # Сделка закрыта (142/143): НЕ создаём новую сделку.
                    # Доставляем сообщение в существующий amojo-чат закрытой сделки —
                    # менеджер видит новые сообщения прямо в закрытой сделке.
                    logger.info(
                        "Lead %s is closed (status=%s), delivering to existing chat: platform_id=%s, bot=%s",
                        conversation.lead_id,
                        lead_status,
                        platform_id,
                        bot_name,
                    )
                # Независимо от статуса (открытая, закрытая, status_id=None) —
                # доставляем в существующий amojo-чат без создания дубля сделки.
                current_lead = lead

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

            # Устанавливаем current_lead для нового диалога чтобы pipeline_triggers
            # могли сработать уже на первом сообщении (например, start=efir).
            bot_config_init = get_bot_config(bot_name)
            current_lead = {
                "pipeline_id": bot_config_init.pipeline_id,
                "status_id": bot_config_init.status_id,
            }

        logger.info(
            "Sending message to amojo: conversation=%s",
            conversation.conversation_id,
        )

        attachments = attachments or []

        # Отправляем текст и вложения; при AmojoNotFoundError (чат удалён вручную в AmoCRM)
        # выполняем fallback: пересоздаём чат и повторяем отправку.
        try:
            if message_text:
                await self.amojo.send_incoming_message(
                    conversation_id=conversation.conversation_id,
                    msgid=f"salebot:{uuid4().hex}",
                    sender_id=f"tg:{platform_id}",
                    sender_name=client_name,
                    text=message_text,
                    silent=False,
                )
                await self.storage.increment_message_count(conversation.conversation_id)

            for media_url in attachments:
                await self.amojo.send_incoming_message(
                    conversation_id=conversation.conversation_id,
                    msgid=f"salebot:{uuid4().hex}",
                    sender_id=f"tg:{platform_id}",
                    sender_name=client_name,
                    text="",
                    silent=False,
                    media_url=media_url,
                )
                await self.storage.increment_message_count(conversation.conversation_id)

        except AmojoNotFoundError:
            # Чат был удалён вручную в AmoCRM — пересоздаём и повторяем.
            logger.warning(
                "Amojo chat not found for conversation=%s, recreating (platform_id=%s, bot=%s)",
                conversation.conversation_id,
                platform_id,
                bot_name,
            )
            conversation = await self._recreate_amojo_chat(
                conversation=conversation,
                platform_id=platform_id,
                bot_name=bot_name,
                client_name=client_name,
                tg_username=tg_username,
            )
            if not conversation:
                raise RuntimeError(
                    f"Failed to recreate amojo chat for platform_id={platform_id}, bot={bot_name} "
                    f"— message not delivered"
                )

            if message_text:
                await self.amojo.send_incoming_message(
                    conversation_id=conversation.conversation_id,
                    msgid=f"salebot:{uuid4().hex}",
                    sender_id=f"tg:{platform_id}",
                    sender_name=client_name,
                    text=message_text,
                    silent=False,
                )
                await self.storage.increment_message_count(conversation.conversation_id)

            for media_url in attachments:
                await self.amojo.send_incoming_message(
                    conversation_id=conversation.conversation_id,
                    msgid=f"salebot:{uuid4().hex}",
                    sender_id=f"tg:{platform_id}",
                    sender_name=client_name,
                    text="",
                    silent=False,
                    media_url=media_url,
                )
                await self.storage.increment_message_count(conversation.conversation_id)

        if not message_text and not attachments:
            logger.warning(
                "Empty message (no text, no attachments): platform_id=%s, conversation=%s",
                platform_id,
                conversation.conversation_id,
            )

        # Проверяем ключевые слова и ставим теги на сделку (best-effort, не блокирует)
        if message_text and conversation.lead_id:
            bot_config = get_bot_config(bot_name)
            if bot_config.keywords:
                text_lower = message_text.lower()
                for keyword, tag_id in bot_config.keywords:
                    if keyword in text_lower:
                        await self.amocrm.add_lead_tag(conversation.lead_id, tag_id)

        # Обновляем select-поля сделки по точному совпадению текста кнопки
        if message_text and conversation.lead_id:
            bot_config = get_bot_config(bot_name)
            for trigger_text, field_id, enum_id in bot_config.field_triggers:
                if message_text == trigger_text:
                    await self.amocrm.update_lead_enum(conversation.lead_id, field_id, enum_id)

        # Перемещаем сделку в другую воронку по тексту кнопки (только один раз).
        # Используем current_lead из уже сделанного get_lead — без лишнего GET-запроса.
        if message_text and current_lead and conversation and conversation.lead_id:
            bot_config = get_bot_config(bot_name)
            for trigger_text, target_pipeline_id, target_status_id in bot_config.pipeline_triggers:
                if message_text == trigger_text:
                    current_pipeline_id = current_lead.get("pipeline_id")
                    current_status_id = current_lead.get("status_id")
                    # Запрет движения назад: только внутри той же воронки и когда
                    # у бота задан stage_order. Если ранг цели меньше текущего — пропускаем.
                    if (bot_config.stage_order is not None and
                            current_pipeline_id == target_pipeline_id):
                        current_rank = bot_config.stage_order.get(current_status_id)
                        target_rank = bot_config.stage_order.get(target_status_id)
                        if (current_rank is not None and target_rank is not None and
                                target_rank < current_rank):
                            logger.info(
                                "Lead %s backward move blocked: %s (rank %s) -> %s (rank %s)",
                                conversation.lead_id,
                                current_status_id,
                                current_rank,
                                target_status_id,
                                target_rank,
                            )
                            break
                    if (current_pipeline_id != target_pipeline_id or
                        current_status_id != target_status_id):
                        await self.amocrm.move_lead(
                            conversation.lead_id, target_pipeline_id, target_status_id
                        )
                    else:
                        logger.debug(
                            "Lead %s already in pipeline %s status %s, skipping move",
                            conversation.lead_id,
                            target_pipeline_id,
                            target_status_id,
                        )
                    break

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
                platform_id_field=bot_config.platform_id_field,
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

            # Сохраняем lead_id в переменную amo_lead_id в профиле клиента Salebot
            # чтобы коллега мог подставить #{amo_lead_id} в ссылку на оплату
            await self.salebot.save_variables(
                client_id=salebot_client_id,
                variables={"amo_lead_id": str(lead_id)},
            )

            # Ставим default_tags на сделку (теги, заданные в конфиге бота)
            if bot_config.default_tags:
                for tag_id in bot_config.default_tags:
                    await self.amocrm.add_lead_tag(lead_id, tag_id)

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

    async def _reopen_conversation(
        self,
        conversation: Conversation,
        platform_id: str,
        bot_name: str,
        salebot_client_id: int,
        client_name: str,
        tg_username: str | None,
        utm_data: dict | None,
    ) -> Conversation | None:
        """
        Переоткрыть диалог при закрытой/удалённой сделке.

        Создаёт новую сделку в amoCRM, но оставляет тот же amojo-чат —
        клиент продолжает писать в тот же чат без потери истории.

        Args:
            conversation: Существующая запись диалога из БД
            platform_id: Telegram ID клиента
            bot_name: Название бота
            salebot_client_id: client.id из Salebot
            client_name: Имя клиента
            tg_username: Telegram username (без @)
            utm_data: UTM-метки из Salebot

        Returns:
            Обновлённая запись Conversation или None при ошибке
        """
        try:
            bot_config = get_bot_config(bot_name)

            # Проверяем жив ли контакт из БД.
            # Если AmoCRM вернул {} (204) — контакт поглощён NOVA.
            # В этом случае ищем выжившего по platform_id чтобы не создавать
            # сделку с мёртвым контактом (что даёт пустую сделку без привязки).
            contact_id = conversation.contact_id
            logger.info(
                "Reopen: checking contact liveness: contact_id=%s, platform_id=%s, bot=%s",
                contact_id,
                platform_id,
                bot_name,
            )
            contact_data = await self.amocrm.get_contact(contact_id)

            if contact_data == {}:
                # Контакт поглощён (204) — ищем актуального
                fresh_contact_id = await self._find_or_create_contact(
                    platform_id=platform_id,
                    client_name=client_name,
                    tg_username=tg_username,
                    platform_id_field=bot_config.platform_id_field,
                )
                logger.info(
                    "Reopen: contact %s absorbed (204), resolved to %s (platform_id=%s, db_updated=%s)",
                    contact_id,
                    fresh_contact_id,
                    platform_id,
                    fresh_contact_id != contact_id,
                )
                if fresh_contact_id != contact_id:
                    await self.storage.update_contact_id(platform_id, bot_name, fresh_contact_id)
                contact_id = fresh_contact_id

                # Контакт сменился — старый amojo-чат привязан к поглощённому контакту.
                # Создаём новый чат и привязываем к актуальному контакту,
                # иначе сообщения продолжат уходить в сделку старого контакта.
                new_conversation_id = str(uuid4())
                profile_link = f"https://t.me/{tg_username}" if tg_username else None
                chat_id = await self.amocrm.create_chat_in_amojo(
                    conversation_id=new_conversation_id,
                    user_id=f"tg:{platform_id}",
                    user_name=client_name,
                    profile_link=profile_link,
                )
                await self.amocrm.link_chat_to_contact(
                    contact_id=contact_id,
                    chat_id=chat_id,
                )
                await self.storage.update_conversation_id(platform_id, bot_name, new_conversation_id)
                logger.info(
                    "Reopen: contact absorbed — new amojo chat created: old_conv=%s, new_conv=%s, contact=%s",
                    conversation.conversation_id,
                    new_conversation_id,
                    contact_id,
                )

            # Ищем открытую сделку во всех воронках — чтобы не создавать дубль,
            # даже если существующая сделка находится в воронке другого бота.
            duplicate_lead = await self.amocrm.check_duplicate_lead(
                contact_id=contact_id,
                pipeline_id=None,
            )

            if duplicate_lead:
                lead_id = duplicate_lead["id"]
                logger.info(
                    "Duplicate lead found on reopen (pipeline=%s): lead_id=%s",
                    bot_config.pipeline_id,
                    lead_id,
                )
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
                logger.info("New lead created on reopen: lead_id=%s", lead_id)

                # # Создаём новый amojo-чат: старый привязан к закрытому лиду в AmoCRM
                # # и сообщения продолжат уходить туда, даже если мы обновили lead_id в БД.
                # new_conversation_id = str(uuid4())
                # profile_link = f"https://t.me/{tg_username}" if tg_username else None
                # new_chat_id = await self.amocrm.create_chat_in_amojo(
                #     conversation_id=new_conversation_id,
                #     user_id=f"tg:{platform_id}",
                #     user_name=client_name,
                #     profile_link=profile_link,
                # )
                # await self.amocrm.link_chat_to_contact(
                #     contact_id=contact_id,
                #     chat_id=new_chat_id,
                # )
                # await self.storage.update_conversation_id(platform_id, bot_name, new_conversation_id)
                # logger.info(
                #     "Reopen: new lead created — new amojo chat created: old_conv=%s, new_conv=%s, contact=%s, lead=%s",
                #     conversation.conversation_id,
                #     new_conversation_id,
                #     contact_id,
                #     lead_id,
                # )

            await self.salebot.save_variables(
                client_id=salebot_client_id,
                variables={"amo_lead_id": str(lead_id)},
            )

            if bot_config.default_tags:
                for tag_id in bot_config.default_tags:
                    await self.amocrm.add_lead_tag(lead_id, tag_id)

            # Обновляем lead_id в БД и сбрасываем messages_count
            await self.storage.update_lead_id(platform_id, bot_name, lead_id)
            logger.info(
                "Conversation reopened (reused amojo chat): platform_id=%s, bot=%s, new_lead_id=%s, conversation_id=%s",
                platform_id,
                bot_name,
                lead_id,
                conversation.conversation_id,
            )

            return await self.storage.get_by_platform_id(platform_id, bot_name)

        except Exception as e:
            logger.error(
                "Error reopening conversation for platform_id=%s, bot=%s: %s",
                platform_id,
                bot_name,
                e,
                exc_info=True,
            )
            return None

    async def _recreate_amojo_chat(
        self,
        conversation: Conversation,
        platform_id: str,
        bot_name: str,
        client_name: str,
        tg_username: str | None,
    ) -> Conversation | None:
        """
        Fallback: пересоздать amojo-чат когда он был удалён вручную в AmoCRM.

        Создаёт новый чат, привязывает к контакту, обновляет conversation_id в БД.

        Args:
            conversation: Текущая запись диалога (с устаревшим conversation_id)
            platform_id: Telegram ID клиента
            bot_name: Название бота
            client_name: Имя клиента
            tg_username: Telegram username (без @)

        Returns:
            Обновлённая запись Conversation или None при ошибке
        """
        try:
            new_conversation_id = str(uuid4())
            profile_link = f"https://t.me/{tg_username}" if tg_username else None

            chat_id = await self.amocrm.create_chat_in_amojo(
                conversation_id=new_conversation_id,
                user_id=f"tg:{platform_id}",
                user_name=client_name,
                profile_link=profile_link,
            )

            await self.amocrm.link_chat_to_contact(
                contact_id=conversation.contact_id,
                chat_id=chat_id,
            )

            await self.storage.update_conversation_id(platform_id, bot_name, new_conversation_id)
            logger.info(
                "Amojo chat recreated (fallback): platform_id=%s, bot=%s, old_conversation_id=%s, new_conversation_id=%s",
                platform_id,
                bot_name,
                conversation.conversation_id,
                new_conversation_id,
            )

            return await self.storage.get_by_platform_id(platform_id, bot_name)

        except Exception as e:
            logger.error(
                "Error recreating amojo chat for platform_id=%s, bot=%s: %s",
                platform_id,
                bot_name,
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
        platform_id_field: int | None = None,
    ) -> int:
        """
        Найти существующий контакт или создать новый.

        Поиск по platform_id → поиск по username → создание.
        При нахождении: дополняет только пустые поля (старые данные приоритетнее).
        UTM хранятся в сделке, не в контакте.

        Args:
            platform_id: ID клиента в мессенджере
            client_name: Имя клиента
            tg_username: Telegram username (без @)
            utm_data: не используется здесь (UTM пишутся в сделку)
            platform_id_field: ID поля AmoCRM для хранения platform_id (по умолчанию FIELD_TG_ID)

        Returns:
            ID контакта в AMO
        """
        pid_field = platform_id_field or settings.FIELD_TG_ID

        # Поиск по platform_id в нужном поле
        contact = await self.amocrm.find_contact_by_tg_id(platform_id, platform_id_field=pid_field)

        # Поиск по username если не нашли по platform_id (только для TG-ботов)
        if not contact and tg_username and pid_field == settings.FIELD_TG_ID:
            contact = await self.amocrm.find_contact_by_username(tg_username)

        if contact:
            contact_id = contact["id"]
            logger.info("Found existing contact: %s", contact_id)

            # Дополняем только пустые поля (старые данные приоритетнее)
            existing_fields = self.amocrm._parse_custom_fields(
                contact.get("custom_fields_values")
            )

            fields_to_update: dict[int, str] = {}

            if not existing_fields.get(pid_field):
                fields_to_update[pid_field] = platform_id

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
            platform_id_field=pid_field,
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

    async def handle_bot_message(
        self,
        platform_id: str,
        bot_name: str,
        message_text: str,
    ) -> None:
        """
        Переслать сообщение бота в amojo (отображается как второй участник диалога).

        Вызывается когда Salebot присылает вебхук с is_input=0 (сообщение от бота).
        Если диалог ещё не создан в БД — пропускаем (не создаём сделку из бот-сообщения).

        Args:
            platform_id: Telegram ID клиента
            bot_name: Название бота
            message_text: Текст сообщения бота
        """
        # Проверяем: не является ли это эхом сообщения, которое мы сами отправили в Salebot
        if message_text:
            redis = get_redis()
            key = _echo_key(platform_id, bot_name, message_text)
            is_echo = await redis.getdel(key)
            if is_echo:
                logger.info(
                    "Echo suppressed for platform_id=%s, bot=%s, text=%r",
                    platform_id,
                    bot_name,
                    message_text[:60],
                )
                return

        conversation = await self.storage.get_by_platform_id(platform_id, bot_name)

        if not conversation:
            logger.debug(
                "No conversation for bot message, skipping: platform_id=%s, bot=%s",
                platform_id,
                bot_name,
            )
            return

        from uuid import uuid4
        await self.amojo.send_incoming_message(
            conversation_id=conversation.conversation_id,
            msgid=f"bot:{uuid4().hex}",
            sender_id=f"tg:{platform_id}",
            sender_name=conversation.client_name or platform_id,
            text=message_text,
            silent=True,
        )

        logger.info(
            "Bot message forwarded to amojo: conversation=%s, bot=%s",
            conversation.conversation_id,
            bot_name,
        )

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

        # Помечаем сообщение как "отправленное нами" — чтобы Salebot-эхо не дублировалось в amojo
        if message_text:
            redis = get_redis()
            key = _echo_key(conversation.platform_id, conversation.bot_name, message_text)
            await redis.setex(key, _ECHO_TTL, "1")

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
