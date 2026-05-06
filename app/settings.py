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
        description="Поддомен amoCRM (например: zabotael)"
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
    SALEBOT_PROJECT_ID: int = Field(description="ID проекта в Salebot")

    # ID кастомных полей AmoCRM
    FIELD_TG_ID: int = Field(
        default=1362361, description="ID поля TG ID в AmoCRM"
    )
    FIELD_TG_USERNAME: int = Field(
        default=1362363, description="ID поля tg_username в AmoCRM"
    )
    FIELD_BOT_NAME: int = Field(
        default=1362369, description="ID поля 'Название бота' в AmoCRM"
    )
    FIELD_TARIFF: int = Field(
        default=1183221, description="ID поля 'Тариф' в AmoCRM"
    )
    
    # Поля для сделки (leads)
    FIELD_LEAD_COURSE: int = Field(
        default=1183223, description="ID поля 'Курс' в сделке"
    )
    FIELD_LEAD_WHERE_STUDIED: int = Field(
        default=947607, description="ID поля 'Где учился (Предмет)' в сделке"
    )
    FIELD_LEAD_CLASS: int = Field(
        default=1188071, description="ID поля 'Класс' в сделке"
    )
    FIELD_LEAD_LEAVE_REASON: int = Field(
        default=1183225, description="ID поля 'Причина ухода' в сделке"
    )

    # Воронка и этап
    AMOCRM_PIPELINE_ID: int = Field(
        default=10379514, description="ID воронки (по умолчанию: Тест)"
    )
    AMOCRM_STATUS_ID: int = Field(
        default=82053406,
        description="ID этапа (по умолчанию: Тест/Неразобранное)",
    )
    
    # Воронка и этап для таблицы "айди игноры неоплат пг"
    AMOCRM_PIPELINE_ID_PG_IGNORE_UNPAID: int = Field(
        default=10657634, description="ID воронки для таблицы 'айди игноры неоплат пг'"
    )
    AMOCRM_STATUS_ID_PG_IGNORE_UNPAID: int = Field(
        default=84014982, description="ID этапа 'бот' для таблицы 'айди игноры неоплат пг'"
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

    # Rate Limiting
    AMOCRM_MAX_REQUESTS_PER_SECOND: int = Field(
        default=7,
        description="Максимальное количество запросов к AmoCRM API в секунду",
        ge=1,
        le=10,
    )
    
    # Параллельная обработка таблиц
    SHEETS_MAX_CONCURRENT_TASKS: int = Field(
        default=7,
        description="Максимальное количество параллельных задач при обработке таблиц",
        ge=1,
        le=20,
    )
    SHEETS_BATCH_SIZE: int = Field(
        default=50,
        description="Размер батча для обновления статусов в Google Sheets",
        ge=10,
        le=200,
    )

    # Google Sheets
    GOOGLE_SERVICE_ACCOUNT_JSON: str = Field(
        description="Путь к JSON файлу Service Account для Google Sheets"
    )
    
    # Таблица: ПГ 2к26 зеро игнор
    GOOGLE_PG_2K26_ZERO_IGNORE_SPREADSHEET_ID: str = Field(
        description="ID таблицы ПГ 2к26 зеро игнор"
    )
    GOOGLE_PG_2K26_ZERO_IGNORE_WORKSHEET_NAME: str = Field(
        description="Название листа в таблице ПГ 2к26 зеро игнор"
    )
    
    # Таблица: Retention 25-26
    GOOGLE_RETENTION_25_26_SPREADSHEET_ID: str = Field(
        description="ID таблицы Retention 25-26"
    )
    GOOGLE_RETENTION_25_26_WORKSHEET_NAME: str = Field(
        description="Название листа в таблице Retention 25-26"
    )
    GOOGLE_RETENTION_START_ROW: int = Field(
        default=8285,
        description="Номер строки начала обработки в таблице Retention (например: 8285)"
    )
    
    # Таблицы Неоплаты (9 таблиц, обрабатываются раз в месяц)
    GOOGLE_SPREADSHEET_ID_NEOPLATY_JAN_PHYSICS_2K26: str = Field(
        description="ID таблицы Неоплаты Физика"
    )
    GOOGLE_WORKSHEET_NAME_NEOPLATY_JAN_PHYSICS_2K26: str = Field(
        description="Название листа в таблице Неоплаты Физика"
    )
    
    GOOGLE_SPREADSHEET_ID_NEOPLATY_5_MONTH_OBSH: str = Field(
        description="ID таблицы Неоплаты Обществознание"
    )
    GOOGLE_WORKSHEET_NAME_NEOPLATY_5_MONTH_OBSH: str = Field(
        description="Название листа в таблице Неоплаты Обществознание"
    )
    
    GOOGLE_SPREADSHEET_ID_NEOPLATY_CHEM_JAN_4_TO_5: str = Field(
        description="ID таблицы Неоплаты Химия"
    )
    GOOGLE_WORKSHEET_NAME_NEOPLATY_CHEM_JAN_4_TO_5: str = Field(
        description="Название листа в таблице Неоплаты Химия"
    )
    
    GOOGLE_SPREADSHEET_ID_NEOPLATY_LIT_JAN_2K26: str = Field(
        description="ID таблицы Неоплаты Литература"
    )
    GOOGLE_WORKSHEET_NAME_NEOPLATY_LIT_JAN_2K26: str = Field(
        description="Название листа в таблице Неоплаты Литература"
    )
    
    GOOGLE_SPREADSHEET_ID_NEOPLATY_JAN_GENERAL: str = Field(
        description="ID таблицы Неоплаты Проф. мат (Маша)"
    )
    GOOGLE_WORKSHEET_NAME_NEOPLATY_JAN_GENERAL: str = Field(
        description="Название листа в таблице Неоплаты Проф. мат (Маша)"
    )
    
    GOOGLE_SPREADSHEET_ID_NEOPLATY_JAN_BIO_ZHENYA_2K26: str = Field(
        description="ID таблицы Неоплаты Биология (Женя)"
    )
    GOOGLE_WORKSHEET_NAME_NEOPLATY_JAN_BIO_ZHENYA_2K26: str = Field(
        description="Название листа в таблице Неоплаты Биология (Женя)"
    )
    
    GOOGLE_SPREADSHEET_ID_NEOPLATY_JAN_MATH_SASHA: str = Field(
        description="ID таблицы Неоплаты Проф. мат (Саша)"
    )
    GOOGLE_WORKSHEET_NAME_NEOPLATY_JAN_MATH_SASHA: str = Field(
        description="Название листа в таблице Неоплаты Проф. мат (Саша)"
    )
    
    GOOGLE_SPREADSHEET_ID_NEOPLATY_JAN_BIO_GELYA_2K26: str = Field(
        description="ID таблицы Неоплаты Биология (Геля)"
    )
    GOOGLE_WORKSHEET_NAME_NEOPLATY_JAN_BIO_GELYA_2K26: str = Field(
        description="Название листа в таблице Неоплаты Биология (Геля)"
    )
    
    GOOGLE_SPREADSHEET_ID_NEOPLATY_5_MONTH_INFO: str = Field(
        description="ID таблицы Неоплаты Информатика"
    )
    GOOGLE_WORKSHEET_NAME_NEOPLATY_5_MONTH_INFO: str = Field(
        description="Название листа в таблице Неоплаты Информатика"
    )
    
    # Таблица: айди игноры неоплат пг
    GOOGLE_SPREADSHEET_ID_PG_IGNORE_UNPAID: str = Field(
        description="ID таблицы 'айди игноры неоплат пг'"
    )
    GOOGLE_WORKSHEET_NAME_PG_IGNORE_UNPAID: str = Field(
        description="Название листа в таблице 'айди игноры неоплат пг'"
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
