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

# Ранги этапов воронки "ТГ БОТы" (id 9472270) для запрета движения сделки назад.
# Правило: бот двигает сделку только если ранг целевого этапа >= ранга текущего.
# Ранги выведены из правил запрета движения назад (ТЗ).
_TG_BOTS_STAGE_ORDER: dict[int, int] = {
    75778598: 0,  # В работе
    75778626: 1,  # Ответ
    75778630: 2,  # Выставил оффер
    75778634: 3,  # Бесплатная польза
    75778638: 3,  # Родители
    75778642: 3,  # Ушёл в игнор
    75778646: 3,  # Вышел на связь
    75778650: 3,  # Забронировали место
    75778654: 3,  # Бронь 10 класс
    75778658: 3,  # Реквизиты выставлены
    81046786: 4,  # МАТКАП
    75778662: 4,  # Задержка оплаты
    142:      4,  # Успешно реализовано
    143:      4,  # Закрыто и не реализовано
}


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
    # Если задан — новая сделка создаётся только когда сообщение содержит
    # хотя бы одно из этих слов (lowercase). Если диалог уже существует —
    # сообщения доставляются в любом случае.
    create_keywords: tuple[str, ...] = field(default=())
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
    # Ранги этапов воронки для запрета движения сделки назад по pipeline_triggers.
    # Формат: {status_id: rank}. None = гард выключен (движение в любую сторону).
    # Гард применяется только когда текущая и целевая воронка совпадают.
    stage_order: dict[int, int] | None = field(default=None)


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
    "409020925": BotConfig(
        pipeline_id=4423755,
        status_id=87906726,
        lead_name="Заявка: Мах - Алиса РОП - Перегон",
        platform_id_field=settings.FIELD_MAX_USER_ID,
        default_tags=(914873, 926819),  # max, el_gettouch_bot
    ),
    "258057137": BotConfig(
        pipeline_id=4423755,
        status_id=79839682,
        lead_name="Заявка: Max - ПОЛИНА РОП - Перегон",
        platform_id_field=settings.FIELD_MAX_USER_ID,
        default_tags=(914873, 924885),  # max, max_egeland_connect_bot
    ),
    "278172561": BotConfig(
        pipeline_id=4423755,
        status_id=87768358,
        lead_name="Заявка: Max - СВЕТА РОП - Перегон",
        platform_id_field=settings.FIELD_MAX_USER_ID,
        default_tags=(914873, 924871),  # max, egeland_connection_bot
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
    # Max — el_personal_bot (Group ID: 366667105)
    "366667105": BotConfig(
        pipeline_id=9472270,
        status_id=75778598,
        lead_name="Заявка: MAX - @el_personal_bot",
        platform_id_field=settings.FIELD_MAX_USER_ID,
    ),
    # Instagram
    "bio_el_oge": BotConfig(
        pipeline_id=9472270,
        status_id=75778594,
        lead_name="Заявка: IG - bio_el_oge",
        default_tags=(681886,),  # тег "instagram"
        platform_id_field=settings.FIELD_IG_USERNAME,
        create_keywords=("курс",),
    ),
    "russichka_oge_ell": BotConfig(
        pipeline_id=9472270,
        status_id=75778594,
        lead_name="Заявка: IG - russichka_oge_ell",
        default_tags=(681886,),  # тег "instagram"
        platform_id_field=settings.FIELD_IG_USERNAME,
        create_keywords=("курс",),
    ),
    "matematica_oge_ell": BotConfig(
        pipeline_id=9472270,
        status_id=75778594,
        lead_name="Заявка: IG - matematica_oge_ell",
        default_tags=(681886,),  # тег "instagram"
        platform_id_field=settings.FIELD_IG_USERNAME,
        create_keywords=("курс",),
    ),
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
        pipeline_id=10195498,
        status_id=80731234,
        lead_name="Заявка: ВК - Общая группа - ЕГЭ и ОГЭ",
        default_tags=(656132,),  # тег "vk"
        platform_id_field=settings.FIELD_VK_ID,
    ),
    "demo2_el_bot": BotConfig(
        pipeline_id=settings.AMOCRM_PIPELINE_ID,
        status_id=settings.AMOCRM_STATUS_ID,
        lead_name="Заявка: TG - @demo2_el_bot",
        default_tags=(660360,),  # тег "telegram"
        pipeline_triggers=(
            ("Позвать менеджера", 9472270, 75778594),
        ),
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
    "El_School_Ege_bot": BotConfig(
        pipeline_id=10195498,
        status_id=80731234,
        lead_name="Заявка: TG - @El_School_Ege_bot",
        pipeline_triggers=(
            ("Отправить номер",      10243538, 81078194),
            ("Записываюсь на курс", 10243538, 81078194),
        ),
    ),
    "el_gettouch_bot": BotConfig(
        pipeline_id=4423755,
        status_id=87906726,
        lead_name="Заявка: TG - Алиса РОП - Перегон",
        default_tags=(660360, 926819),  # telegram, el_gettouch_bot
    ),
    "el_connect_bot": BotConfig(
        pipeline_id=4423755,
        status_id=79839682,
        lead_name="Заявка: TG - ПОЛИНА РОП - Перегон",
        default_tags=(660360, 924883),  # telegram, tg_el_connect_bot
    ),
    "el_connetbot": BotConfig(
        pipeline_id=4423755,
        status_id=87768358,
        lead_name="Заявка: TG - СВЕТА РОП - Перегон",
        default_tags=(660360, 924857),  # telegram, tg_el_connetbot
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
            ("Купить курс",        9472270, 75778658),
            ("Есть вопросики",     9472270, 75778626),
            ("Позвать человека",   9472270, 75778626),
            ("Узнать подробности", 9472270, 75778630),
            ("Занять место",       9472270, 75778658),
        ),
        stage_order=_TG_BOTS_STAGE_ORDER,
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
        field_triggers=_COMMON_FIELD_TRIGGERS + (
            # Кнопки класса — "Перешел/Перехожу в N класс" → соответствующий N класс
            ("Перешел в 11 класс",    809893, 1374875),  # → 11 класс
            ("Перехожу в 11 класс",   809893, 1374875),
            ("Перехожу в 11-й класс", 809893, 1374875),
            ("Перешел в 10 класс",    809893, 1374873),  # → 10 класс
            ("Перехожу в 10 класс",   809893, 1374873),
            ("Перехожу в 10-й класс", 809893, 1374873),
            ("Перешел в 9 класс",     809893, 1374871),  # → 9 класс
            ("Перехожу в 9 класс",    809893, 1374871),
            ("Перехожу в 9-й класс",  809893, 1374871),
            ("Перешел в 8 класс",     809893, 1378767),  # → 8 класс
            ("Перехожу в 8 класс",    809893, 1378767),
            ("Перехожу в 8-й класс",  809893, 1378767),
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
