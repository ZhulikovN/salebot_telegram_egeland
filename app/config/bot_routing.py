"""Маршрутизация ботов Salebot: воронка, этап и название сделки."""
from dataclasses import dataclass, field

from app.settings import settings

# Общие триггеры для обновления полей сделки по тексту кнопки.
# Формат: (точный_текст_кнопки, field_id_в_amo, enum_id_в_amo).
# Поле 809891 "Инициатор сделки", поле 809893 "Класс".
_COMMON_FIELD_TRIGGERS: tuple[tuple[str, int, int], ...] = (
    ("Родитель",  809891, 1374865),
    ("Ученик",    809891, 1374867),
    ("7 класс",   809893, 1378765),
    ("8 класс",   809893, 1378767),
    ("9 класс",   809893, 1374871),
    ("10 класс",  809893, 1374873),
    ("11 класс",  809893, 1374875),
)


@dataclass(frozen=True)
class BotConfig:
    """Конфигурация бота для создания сделки в AmoCRM."""

    pipeline_id: int
    status_id: int
    lead_name: str
    # Теги, которые ставятся на сделку при каждом создании новой беседы из этого бота.
    # Формат: tuple[tag_id, ...].
    default_tags: tuple[int, ...] = field(default=())
    # Маппинг: (lowercase-ключевое слово, tag_id в AmoCRM).
    # Если сообщение содержит ключевое слово — тег добавляется к сделке по ID.
    keywords: tuple[tuple[str, int], ...] = field(default=())
    # ID поля контакта для хранения platform_id.
    # None = дефолт (FIELD_TG_ID). Для Max нужно FIELD_MAX_USER_ID.
    platform_id_field: int | None = field(default=None)
    # Триггеры обновления select-полей сделки по точному тексту кнопки.
    # Формат: (текст_кнопки, field_id, enum_id).
    # По умолчанию применяются общие триггеры для всех ботов.
    field_triggers: tuple[tuple[str, int, int], ...] = field(default=_COMMON_FIELD_TRIGGERS)
    # Триггеры перемещения сделки в другую воронку/этап по точному тексту кнопки.
    # Формат: (текст_кнопки, pipeline_id, status_id).
    # Перемещение происходит только один раз — если сделка уже в целевой воронке, пропускается.
    pipeline_triggers: tuple[tuple[str, int, int], ...] = field(default=())


# Конфигурация для каждого бота.
# Ключ — значение поля client.group из вебхука Salebot (название бота).
_BOT_CONFIGS: dict[str, BotConfig] = {
    # Тестовый бот с переносом воронки по кнопкам
    # TODO: заменить "test_el_salebot" на реальное имя бота из логов (bot=... в SALEBOT_RAW)
    "test_el_salebot": BotConfig(
        pipeline_id=settings.AMOCRM_PIPELINE_ID,
        status_id=settings.AMOCRM_STATUS_ID,
        lead_name="",
        keywords=(
            ("есть вопрос",             905921),   # тег ЕСТЬ ВОПРОС
            ("присоединиться к курсу",  917405),   # тег ПРИСОЕДИНИТЬСЯ К КУРСУ
        ),
        pipeline_triggers=(
            ("Есть вопрос",             10195498, 86072578),
            ("Присоединиться к курсу",  10195498, 86072582),
        ),
    ),
    # Max messenger
    "278172561": BotConfig(
        pipeline_id=settings.AMOCRM_PIPELINE_ID_LEADS,
        status_id=settings.AMOCRM_STATUS_ID_LEADS,
        lead_name="Заявка: MAX - Перегон - @egeland_connection_bot",
        platform_id_field=settings.FIELD_MAX_USER_ID,
    ),
    "298311406": BotConfig(
        pipeline_id=settings.AMOCRM_PIPELINE_ID_LEADS,
        status_id=settings.AMOCRM_STATUS_ID_LEADS,
        lead_name="Заявка: MAX - Flocktory - @egeland_edu_bot",
        platform_id_field=settings.FIELD_MAX_USER_ID,
    ),
    "301899084": BotConfig(
        pipeline_id=settings.AMOCRM_PIPELINE_ID_LEADS,
        status_id=settings.AMOCRM_STATUS_ID_LEADS,
        lead_name="Заявка: MAX - RIS.Promo - @egeland_eduwith_bot",
        platform_id_field=settings.FIELD_MAX_USER_ID,
    ),
    # Instagram
    "mikhail_matematik": BotConfig(
        pipeline_id=settings.AMOCRM_PIPELINE_ID_IG_MIKHAIL,
        status_id=settings.AMOCRM_STATUS_ID_IG_MIKHAIL,
        lead_name="Заявка: IG - mikhail_matematik",
        default_tags=(681886,),  # тег "instagram"
        platform_id_field=settings.FIELD_IG_USERNAME,
        keywords=(
            ("диагностика", 916729),
            ("курс", 737540),
        ),
    ),
    # ВКонтакте — Общая группа ЕГЭ и ОГЭ (Group ID: 203482421)
    "203482421": BotConfig(
        pipeline_id=10849334,
        status_id=85382934,
        lead_name="Заявка: ВК - Общая группа - ЕГЭ и ОГЭ",
        default_tags=(656132,),  # тег "vk"
        platform_id_field=settings.FIELD_VK_ID,
    ),
    "el_efir_bot": BotConfig(
        pipeline_id=10849334,
        status_id=85382934,
        lead_name="Заявка: TG - @el_efir_bot",
        default_tags=(660360,),  # тег "telegram"
    ),
    "el_diagnostic_bot": BotConfig(
        pipeline_id=10243538,
        status_id=81078194,
        lead_name="Заявка: TG - @el_diagnostic_bot",
        pipeline_triggers=(
            ("Отправить телефон", 10243538, 81078194),
        ),
    ),
    "el_connetbot": BotConfig(
        pipeline_id=settings.AMOCRM_PIPELINE_ID_LEADS,
        status_id=settings.AMOCRM_STATUS_ID_LEADS,
        lead_name="Заявка: TG - Перегон - @el_connetbot",
    ),
    "el_eduwith_bot": BotConfig(
        pipeline_id=settings.AMOCRM_PIPELINE_ID_LEADS,
        status_id=settings.AMOCRM_STATUS_ID_LEADS,
        lead_name="Заявка: TG - Flocktory - @el_eduwith_bot",
    ),
    "el_edu_with_bot": BotConfig(
        pipeline_id=settings.AMOCRM_PIPELINE_ID_LEADS,
        status_id=settings.AMOCRM_STATUS_ID_LEADS,
        lead_name="Заявка: TG - RIS.Promo - @el_edu_with_bot",
    ),
    "el_edu_withbot": BotConfig(
        pipeline_id=settings.AMOCRM_PIPELINE_ID_LEADS,
        status_id=settings.AMOCRM_STATUS_ID_LEADS,
        lead_name="Заявка: TG - ТелеМаркетинг - @el_edu_withbot",
    ),
    "el_personal_bot": BotConfig(
        pipeline_id=9472270,
        status_id=75778598,
        lead_name="Заявка: TG - @el_personal_bot",
        pipeline_triggers=(
            # start=efir — первое сообщение клиента при переходе по ссылке
            ("efir",               10849334, 85382938),
            ("Купить курс",        10849334, 85383566),
            ("Узнать подробности", 10849334, 85383538),
            # старые триггеры внутри воронки 9472270
            ("Есть вопросики",     9472270, 75778626),
        ),
        keywords=(
            ("купить курс",        907137),   # тег КУПИТЬ КУРС
            ("узнать подробности", 914269),   # тег УЗНАТЬ ПОДРОБНОСТИ
            ("позвать человека",   905921),   # тег ЕСТЬ ВОПРОС
            ("есть вопросики",     905921),   # тег ЕСТЬ ВОПРОС
            ("есть вопрос",        905921),   # тег ЕСТЬ ВОПРОС
            # старые ключевые слова
            ("есть вопросики",     917969),
            ("узнать подробности", 917971),
            ("занять место",       917973),
        ),
    ),
}

# Конфиг по умолчанию для всех остальных ботов (test_el_salebot и прочие)
_DEFAULT_BOT_CONFIG = BotConfig(
    pipeline_id=settings.AMOCRM_PIPELINE_ID,
    status_id=settings.AMOCRM_STATUS_ID,
    lead_name="",  # будет сформировано в create_lead из bot_name
)


def get_bot_config(bot_name: str) -> BotConfig:
    """
    Получить конфигурацию бота по его названию.

    Args:
        bot_name: Название бота (client.group из Salebot webhook)

    Returns:
        BotConfig с pipeline_id, status_id и lead_name.
        Для неизвестных ботов возвращает дефолтный конфиг.
    """
    return _BOT_CONFIGS.get(bot_name, _DEFAULT_BOT_CONFIG)
