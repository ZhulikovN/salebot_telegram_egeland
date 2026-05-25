"""Маршрутизация ботов Salebot: воронка, этап и название сделки."""
from dataclasses import dataclass, field

from app.settings import settings


@dataclass(frozen=True)
class BotConfig:
    """Конфигурация бота для создания сделки в AmoCRM."""

    pipeline_id: int
    status_id: int
    lead_name: str
    # Маппинг: lowercase-ключевое слово → название тега.
    # Если сообщение содержит ключевое слово — тег добавляется к сделке.
    keywords: tuple[tuple[str, str], ...] = field(default=())


# Конфигурация для каждого бота.
# Ключ — значение поля client.group из вебхука Salebot (название бота).
_BOT_CONFIGS: dict[str, BotConfig] = {
    # Max messenger
    "278172561": BotConfig(
        pipeline_id=settings.AMOCRM_PIPELINE_ID_LEADS,
        status_id=settings.AMOCRM_STATUS_ID_LEADS,
        lead_name="Заявка: MAX - Перегон - @egeland_connection_bot",
    ),
    # Instagram
    "mikhail_matematik": BotConfig(
        pipeline_id=settings.AMOCRM_PIPELINE_ID_IG_MIKHAIL,
        status_id=settings.AMOCRM_STATUS_ID_IG_MIKHAIL,
        lead_name="Заявка: IG - mikhail_matematik",
        keywords=(
            ("диагностика", "ДИАГНОСТИКА"),
            ("курс", "КУРС"),
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
