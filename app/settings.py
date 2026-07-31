"""Настройки приложения."""
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from dotenv import load_dotenv

load_dotenv()

class Settings(BaseSettings):
    """Настройки приложения из переменных окружения."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # amoCRM (long-lived token для большинства запросов)
    AMOCRM_SUBDOMAIN: str = Field(
        description="Поддомен amoCRM (например: egeland)"
    )
    AMO_ACCESS_TOKEN: str = Field(
        description="Долгоживущий Bearer токен для API amoCRM"
    )

    # amoCRM OAuth2 (для специальных запросов)
    AMO_CLIENT_ID: str = Field(description="Client ID для OAuth2 интеграции")
    AMO_CLIENT_SECRET: str = Field(description="Client Secret для OAuth2 интеграции")
    AMO_REDIRECT_URI: str = Field(description="Redirect URI для OAuth2")
    AMO_AUTH_CODE: str = Field(
        description="Authorization Code для получения токенов"
    )
    BASE_DOMAIN: str = Field(description="Базовый домен amoCRM")

    # amojo (канал для чатов)
    AMOJO_CHANNEL_ID: str = Field(description="ID канала amojo")
    AMOJO_CHANNEL_SECRET: str = Field(
        description="Secret для подписи amojo webhook"
    )
    AMOJO_SCOPE_ID: str = Field(
        description="Scope ID канала (channel_id_account_id)"
    )
    AMOJO_ACCOUNT_ID: str = Field(description="Account ID в amoCRM")

    # Salebot
    SALEBOT_API_KEY: str = Field(description="API ключ для Salebot")
    SALEBOT_PROJECT_ID: int = Field(description="ID проекта в Salebot (799515)")

    # Токены Telegram-ботов для прямой отправки медиа в обход Salebot.
    # Salebot отдаёт Telegram только ссылку на файл, но серверы Telegram не могут
    # скачать её с нашего хостинга — картинка приходит клиенту текстовой ссылкой.
    # Для ботов из этого маппинга медиа грузится в Bot API байтами.
    # Формат env: TELEGRAM_BOT_TOKENS={"el_connetbot": "123:AAA..."}
    TELEGRAM_BOT_TOKENS: dict[str, str] = Field(
        default_factory=dict,
        description="Маппинг bot_name → токен Telegram Bot API",
    )

    # ID кастомных полей контакта в AmoCRM
    FIELD_TG_ID: int = Field(
        default=811310, description="ID поля Telegram user id (Radist.online)"
    )
    FIELD_TG_USERNAME: int = Field(
        default=811308, description="ID поля Telegram username (Radist.online)"
    )
    FIELD_MAX_USER_ID: int = Field(
        default=813975, description="ID поля Max user id (Radist.online)"
    )
    FIELD_IG_USERNAME: int = Field(
        default=814675, description="ID поля Ник Instagram"
    )
    FIELD_VK_ID: int = Field(
        default=814023, description="ID поля ВКонтакте ID клиента"
    )

    # UTM-метки контакта (первое касание)
    FIELD_UTM_SOURCE: int = Field(default=688736, description="ID поля utm_source контакта")
    FIELD_UTM_MEDIUM: int = Field(default=688744, description="ID поля utm_medium контакта")
    FIELD_UTM_CAMPAIGN: int = Field(default=688742, description="ID поля utm_campaign контакта")
    FIELD_UTM_TERM: int = Field(default=688740, description="ID поля utm_term контакта")
    FIELD_UTM_CONTENT: int = Field(default=712229, description="ID поля utm_content контакта")

    # ID кастомных полей сделки в AmoCRM
    FIELD_BOT_NAME: int = Field(
        default=809165, description="ID поля 'Источник перехода' в сделке"
    )

    # Воронка и этап — основные (test_el_salebot и все боты без явного конфига)
    AMOCRM_PIPELINE_ID: int = Field(
        default=10195498, description="ID воронки (Анкета удержания)"
    )
    AMOCRM_STATUS_ID: int = Field(
        default=80731234,
        description="ID этапа создания сделки (из бота)",
    )

    # Воронка и этап — для новых лид-ботов (el_connetbot, el_eduwith_bot, el_edu_with_bot, el_edu_withbot)
    AMOCRM_PIPELINE_ID_LEADS: int = Field(
        default=8598230, description="ID воронки для лид-ботов"
    )
    AMOCRM_STATUS_ID_LEADS: int = Field(
        default=83375282, description="ID этапа создания сделки для лид-ботов"
    )

    # Воронка и этап — Instagram mikhail_matematik
    AMOCRM_PIPELINE_ID_IG_MIKHAIL: int = Field(
        default=10195498, description="ID воронки для Instagram mikhail_matematik"
    )
    AMOCRM_STATUS_ID_IG_MIKHAIL: int = Field(
        default=80731234, description="ID этапа для Instagram mikhail_matematik"
    )

    # Статусы закрытых этапов (для проверки дублей)
    STATUS_SUCCESS: int = Field(default=142, description="ID статуса 'Успешно'")
    STATUS_CLOSED: int = Field(default=143, description="ID статуса 'Закрыто'")

    # PostgreSQL
    POSTGRES_HOST: str = Field(description="Хост PostgreSQL сервера")
    POSTGRES_PORT: int = Field(default=5432, description="Порт PostgreSQL сервера")
    POSTGRES_DB: str = Field(description="Имя базы данных")
    POSTGRES_USER: str = Field(description="Пользователь PostgreSQL")
    POSTGRES_PASSWORD: str = Field(description="Пароль PostgreSQL")

    # Redis
    REDIS_HOST: str = Field(description="Хост Redis сервера")
    REDIS_PORT: int = Field(default=6379, description="Порт Redis сервера")
    REDIS_PASSWORD: str = Field(description="Пароль для Redis")

    # Публичный URL нашего сервера (для проксирования медиафайлов AMO → Salebot)
    PUBLIC_URL: str = Field(
        description="Публичный URL сервера (например: https://example.com) без слеша в конце"
    )

    # Rate Limiting
    AMOCRM_MAX_REQUESTS_PER_SECOND: int = Field(
        default=5,
        description="Максимальное количество запросов к AmoCRM API в секунду",
        ge=1,
        le=10,
    )

    @property
    def amocrm_api_url(self) -> str:
        """Базовый URL для amoCRM API."""
        return f"https://{self.AMOCRM_SUBDOMAIN}.amocrm.ru/api/v4"

    @property
    def amojo_api_url(self) -> str:
        """Базовый URL для amojo API."""
        return "https://amojo.amocrm.ru"

    @property
    def salebot_api_url(self) -> str:
        """URL для API Salebot."""
        return f"https://chatter.salebot.pro/api/{self.SALEBOT_API_KEY}"

    @property
    def redis_url(self) -> str:
        """Сформировать URL подключения к Redis с паролем."""
        return f"redis://:{self.REDIS_PASSWORD}@{self.REDIS_HOST}:{self.REDIS_PORT}"

    @property
    def postgres_url(self) -> str:
        """Сформировать URL подключения к PostgreSQL."""
        return f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"


settings = Settings()
