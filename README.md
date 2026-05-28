# salebot_telegram_egeland

Двусторонняя интеграция между Salebot.pro и amoCRM. При первом сообщении клиента в бота автоматически создаётся контакт
и сделка в amoCRM, диалог отображается в карточке сделки, менеджер отвечает из amoCRM — ответ уходит клиенту обратно
через Salebot.

---

## Архитектура

```
Клиент (Telegram / Instagram / Max)
        ↓
   Salebot.pro
        ↓ webhook
   FastAPI (API)
        ↓
   Redis (очередь задач)
        ↓
   Worker (3 инстанса)
        ↓
   amoCRM API + amojo API
        ↓ webhook
   FastAPI (API)
        ↓
   Redis (очередь задач)
        ↓
   Worker
        ↓
   Salebot API → клиент
```

**Компоненты:**

- `app/api/` — FastAPI-эндпоинты для приёма вебхуков от Salebot и amoCRM
- `app/workers/worker.py` — фоновые воркеры (3 инстанса через systemd), обрабатывают задачи из Redis
- `app/workers/queue.py` — Redis-очереди с FIFO-гарантией порядка сообщений на диалог
- `app/services/conversation_manager.py` — основная логика: создание контакта, сделки, чата, отправка сообщений
- `app/services/amocrm_client.py` — клиент AmoCRM API v4 с rate limiting (5 req/s) и retry (3 попытки)
- `app/services/amojo_client.py` — клиент amojo (чаты в amoCRM)
- `app/services/salebot_client.py` — клиент Salebot API
- `app/config/bot_routing.py` — маршрутизация: воронка, этап, название сделки, теги, триггеры по кнопкам
- `app/db/storage.py` — PostgreSQL: маппинг `(platform_id, bot_name) → lead_id, conversation_id`

---

## Логика работы

### Входящее сообщение (Salebot → amoCRM)

1. Salebot присылает вебхук на `/webhook/salebot`
2. API кладёт задачу в Redis (`tasks:global`)
3. Воркер берёт задачу, добавляет сообщение в очередь диалога (`queue:conversation:{key}`)
4. Захватывает блокировку диалога (Redis lock)
5. Обрабатывает все накопившиеся сообщения по порядку (FIFO)

**При первом сообщении клиента:**

- Ищет контакт в amoCRM по `platform_id` → если нет, по `tg_username` → если нет, создаёт
- Ищет открытую сделку контакта в нужной воронке → если нет, создаёт
- Создаёт чат в amojo, привязывает к контакту
- Сохраняет маппинг в PostgreSQL

**При повторных сообщениях:**

- Находит диалог по `(platform_id, bot_name)` в PostgreSQL
- Проверяет, что привязанная сделка не закрыта (статус 142/143)
- Если сделка закрыта — удаляет запись из БД и создаёт всё заново
- Отправляет сообщение в amojo

### Исходящее сообщение (amoCRM → Salebot)

1. amoCRM присылает вебхук на `/amojo/webhook/{scope_id}`
2. API фильтрует: только сообщения от менеджера (не от клиента)
3. Кладёт задачу в Redis
4. Воркер находит диалог по `conversation_id`, отправляет сообщение через Salebot API

### Сообщения от бота (is_input=0)

Salebot присылает вебхуки как на входящие (клиент), так и на исходящие (бот) сообщения. Исходящие (
`is_bot_message=True`) пересылаются в amojo от имени клиента с пометкой `silent=True` — не создают уведомления. Если
диалог ещё не создан — бот-сообщения игнорируются.

**Защита от дублей:** когда менеджер отвечает из amoCRM, мы отправляем сообщение в Salebot. Salebot возвращает его эхом
как `is_input=0`. Такие эхо-сообщения подавляются через Redis-ключ с TTL 15 секунд.

---

## Маршрутизация ботов

Конфигурируется в `app/config/bot_routing.py`. Ключ — `client.group` из вебхука Salebot (название бота).

| Бот                             | Воронка  | Этап     | Название сделки                                 |
|---------------------------------|----------|----------|-------------------------------------------------|
| `el_connetbot`                  | 8598230  | 83375282 | Заявка: TG - Перегон - @el_connetbot            |
| `el_eduwith_bot`                | 8598230  | 83375282 | Заявка: TG - Flocktory - @el_eduwith_bot        |
| `el_edu_with_bot`               | 8598230  | 83375282 | Заявка: TG - RIS.Promo - @el_edu_with_bot       |
| `el_edu_withbot`                | 8598230  | 83375282 | Заявка: TG - ТелеМаркетинг - @el_edu_withbot    |
| `278172561` (Max)               | 8598230  | 83375282 | Заявка: MAX - Перегон - @egeland_connection_bot |
| `mikhail_matematik` (Instagram) | 10243538 | 81078194 | Заявка: IG - mikhail_matematik                  |
| остальные                       | 10195498 | 80731234 | `{bot_name} - Новая заявка`                     |

Для неизвестных ботов применяется дефолтный конфиг.

### Кастомное поле для platform_id

Для Max-бота (`278172561`) `platform_id` сохраняется в поле `813975` (Max user id), а не в стандартное поле Telegram ID
`811310`.

### Теги по ключевым словам

Для бота `mikhail_matematik` при каждом сообщении проверяется наличие ключевых слов (без учёта регистра, в любом месте
текста):

| Ключевое слово | Тег (tag_id)         |
|----------------|----------------------|
| диагностика    | 916729 (ДИАГНОСТИКА) |
| курс           | 737540 (КУРС)        |

Тег добавляется к сделке без удаления существующих тегов.

### Триггеры по кнопкам

**Обновление select-полей сделки** (для всех ботов, точное совпадение текста кнопки):

| Кнопка   | Поле               | Значение |
|----------|--------------------|----------|
| Родитель | 809891 (Инициатор) | 1374865  |
| Ученик   | 809891 (Инициатор) | 1374867  |
| 7 класс  | 809893 (Класс)     | 1378765  |
| 8 класс  | 809893 (Класс)     | 1378767  |
| 9 класс  | 809893 (Класс)     | 1374871  |
| 10 класс | 809893 (Класс)     | 1374873  |
| 11 класс | 809893 (Класс)     | 1374875  |

**Перемещение сделки по воронке** (для бота `test_el_salebot`, только один раз):

| Кнопка                 | Воронка  | Этап     |
|------------------------|----------|----------|
| Есть вопрос            | 10195498 | 86072578 |
| Присоединиться к курсу | 10195498 | 86072582 |

Перемещение происходит только если сделка ещё не в целевой воронке — без лишних запросов.

---

## Поля amoCRM

### Контакт

| Поле              | ID     | Описание                           |
|-------------------|--------|------------------------------------|
| Telegram user id  | 811310 | platform_id клиента (для TG-ботов) |
| Telegram username | 811308 | tg_username без @                  |
| Max user id       | 813975 | platform_id клиента (для Max-бота) |

### Сделка

| Поле              | ID     | Описание                 |
|-------------------|--------|--------------------------|
| Источник перехода | 809165 | Название бота (bot_name) |
| Инициатор сделки  | 809891 | Родитель / Ученик        |
| Класс             | 809893 | 7-11 класс               |
| utm_source        | 688736 | UTM первого касания      |
| utm_medium        | 688744 | UTM первого касания      |
| utm_campaign      | 688742 | UTM первого касания      |
| utm_term          | 688740 | UTM первого касания      |
| utm_content       | 712229 | UTM первого касания      |

**First-touch логика для UTM:** UTM-метки записываются только при первом сообщении клиента. Если поля уже заполнены — не
перезаписываются.

### Закрытые статусы сделок

- `142` — Успешно реализовано
- `143` — Закрыто и не реализовано

Если привязанная сделка имеет один из этих статусов — запись в БД удаляется и при следующем сообщении создаётся новый
диалог.

---

## Добавление нового бота

1. Определить `client.group` из логов: найти строку `SALEBOT_RAW: ... bot=<значение>`
2. Добавить запись в `_BOT_CONFIGS` в `app/config/bot_routing.py`:

```python
"имя_бота": BotConfig(
    pipeline_id=settings.AMOCRM_PIPELINE_ID_LEADS,
    status_id=settings.AMOCRM_STATUS_ID_LEADS,
    lead_name="Заявка: TG - Источник - @bot_name",
),
```

3. Если нужна новая воронка — добавить `AMOCRM_PIPELINE_ID_*` и `AMOCRM_STATUS_ID_*` в `app/settings.py`
4. Задеплоить новый релиз, перезапустить воркеры и API

---

## Переменные окружения (.env)

```env
# amoCRM
AMOCRM_SUBDOMAIN=egeland
AMO_ACCESS_TOKEN=...

# amoCRM OAuth2 (для привязки чатов к контактам)
AMO_CLIENT_ID=...
AMO_CLIENT_SECRET=...
AMO_REDIRECT_URI=...
AMO_AUTH_CODE=...
BASE_DOMAIN=amocrm.ru

# amojo (канал чатов)
AMOJO_CHANNEL_ID=...
AMOJO_CHANNEL_SECRET=...
AMOJO_SCOPE_ID=...
AMOJO_ACCOUNT_ID=...

# Salebot
SALEBOT_API_KEY=...
SALEBOT_PROJECT_ID=799515

# PostgreSQL
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_DB=egeland_telegram
POSTGRES_USER=egeland_salebot
POSTGRES_PASSWORD=...

# Redis
REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_PASSWORD=...

# Публичный URL сервера (для проксирования медиа от amoCRM в Salebot)
PUBLIC_URL=https://your-server.com
```

---

## Деплой

Релизы хранятся в `~/salebot_telegram_egeland/releases/release_NNN/`.

```bash
# Перезапуск API
sudo systemctl restart salebot-telegram-egeland-api

# Перезапуск воркеров (3 инстанса)
sudo systemctl restart salebot-telegram-egeland-worker@1
sudo systemctl restart salebot-telegram-egeland-worker@2
sudo systemctl restart salebot-telegram-egeland-worker@3

# Логи API
sudo journalctl -u salebot-telegram-egeland-api -f

# Логи воркера (только ошибки)
sudo journalctl -u salebot-telegram-egeland-worker@1 --since "2026-05-27 00:00:00" | grep "ERROR"
```

После деплоя рекомендуется очистить `__pycache__`:

```bash
find ~/salebot_telegram_egeland -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null
```

---

## Известные особенности

**"Чужие" amojo-диалоги.** AmoCRM присылает вебхуки по всем событиям в аккаунте, в том числе по чатам, созданным не
нашей интеграцией. Такие события логируются как `Conversation not found` и корректно пропускаются — воркер не зависает.

**Таймауты AmoCRM.** При кратковременной недоступности AmoCRM (3 retry с exponential backoff) сообщение клиента
теряется — сделка не создаётся. Если клиент напишет повторно — всё создастся нормально.

**Старые сделки в БД.** Связка диалога со сделкой хранится в PostgreSQL. Если сделка была создана до смены конфига
бота — она будет найдена в старой воронке. Решение: закрыть такие сделки в amoCRM (статус 142/143) — при следующем
сообщении создастся новая сделка в правильной воронке.

---
