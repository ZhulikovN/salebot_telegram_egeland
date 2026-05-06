# Shared Database Services для Salebot ↔ amoCRM Integration

Этот файл содержит конфигурацию PostgreSQL и Redis, которые работают постоянно и не меняются при деплое новых версий приложения.

## 🚀 Быстрый старт

```bash
# 1. Создай .env файл
cp env.example .env
nano .env  # Установи пароли

# 2. Запусти БД
docker compose -f docker-compose-db.yml up -d

# 3. Проверь статус
docker compose -f docker-compose-db.yml ps

# 4. Смотри логи
docker logs -f salebot-telegram-redis
docker logs -f salebot-telegram-postgres
```

## 📦 Сервисы

### PostgreSQL (salebot-telegram-postgres)
- **Образ:** `postgres:16-alpine`
- **Внутренний адрес:** `salebot-telegram-postgres:5432`
- **Volume:** `postgres_data` (персистентное хранилище)
- **Сеть:** `salebot-telegram-network`

### Redis (salebot-telegram-redis)
- **Образ:** `redis:7-alpine`
- **Внутренний адрес:** `salebot-telegram-redis:6379`
- **Volume:** `redis_data` (персистентное хранилище)
- **Сеть:** `salebot-telegram-network`

## 🔒 Безопасность

### Защита от DDoS
- **Порты НЕ expose наружу** — доступ только внутри Docker сети
- **Redis protected mode** — включен
- **Опасные команды отключены:** FLUSHDB, FLUSHALL, CONFIG, SHUTDOWN, SLAVEOF, REPLICAOF

### Настройки Redis
- **Аутентификация:** обязательный пароль (`REDIS_PASSWORD`)
- **Persistence:** AOF (Append Only File) с синхронизацией каждую секунду
- **Memory limit:** 256MB с политикой вытеснения `allkeys-lru`

## 🔧 Переменные окружения (.env)

```bash
# PostgreSQL
POSTGRES_DB=salebot_telegram
POSTGRES_USER=salebot
POSTGRES_PASSWORD=<YOUR_SECURE_PASSWORD>

# Redis
REDIS_PASSWORD=<YOUR_SECURE_PASSWORD>
```

## 📊 Мониторинг

```bash
# Статус сервисов
docker compose -f docker-compose-db.yml ps

# Логи
docker logs -f salebot-telegram-postgres
docker logs -f salebot-telegram-redis

# Использование ресурсов
docker stats salebot-telegram-postgres salebot-telegram-redis

# Проверка здоровья
docker inspect salebot-telegram-postgres | grep -A 10 Health
docker inspect salebot-telegram-redis | grep -A 10 Health
```

## 🔄 Управление

### Запуск
```bash
docker compose -f docker-compose-db.yml up -d
```

### Остановка
```bash
docker compose -f docker-compose-db.yml down
```

### Перезапуск
```bash
docker compose -f docker-compose-db.yml restart
```

### Удаление с данными (⚠️ ОСТОРОЖНО!)
```bash
docker compose -f docker-compose-db.yml down -v
```

## 🔗 Подключение из приложения

### PostgreSQL
```python
# .env приложения
POSTGRES_HOST=salebot-telegram-postgres
POSTGRES_PORT=5432
POSTGRES_DB=salebot_telegram
POSTGRES_USER=salebot
POSTGRES_PASSWORD=<PASSWORD>
```

### Redis
```python
# .env приложения
REDIS_HOST=salebot-telegram-redis
REDIS_PORT=6379
REDIS_PASSWORD=<PASSWORD>
```

## 🐛 Troubleshooting

### Redis не запускается
```bash
# Проверь логи
docker logs salebot-telegram-redis

# Частые причины:
# 1. REDIS_PASSWORD не установлен в .env
# 2. Недостаточно памяти на сервере
# 3. Volume повреждён
```

### PostgreSQL не запускается
```bash
# Проверь логи
docker logs salebot-telegram-postgres

# Частые причины:
# 1. POSTGRES_* переменные не установлены в .env
# 2. Недостаточно места на диске
# 3. Volume повреждён
```

### Конфликт с другими проектами
Если на сервере запущены другие проекты (например, `amocrm-duplicate-merger`):
- Убедись что имена контейнеров уникальны ✅
- Убедись что имена сетей уникальны ✅
- Убедись что имена сервисов в docker-compose.yml уникальны ✅

**Пример:**
```yaml
# amocrm-duplicate-merger
services:
  redis: ...  # ← Имя сервиса: "redis"

# salebot_telegram_zabotael
services:
  salebot-redis: ...  # ← УНИКАЛЬНОЕ имя: "salebot-redis"
```

## 📝 Бэкап и восстановление

### PostgreSQL
```bash
# Бэкап
docker exec salebot-telegram-postgres pg_dump -U salebot salebot_telegram > backup.sql

# Восстановление
cat backup.sql | docker exec -i salebot-telegram-postgres psql -U salebot salebot_telegram
```

### Redis
```bash
# Бэкап (AOF файл)
docker exec salebot-telegram-redis redis-cli -a $REDIS_PASSWORD BGSAVE

# Копирование backup
docker cp salebot-telegram-redis:/data/dump.rdb ./redis_backup.rdb
```

## ⚙️ Тюнинг производительности

### PostgreSQL
```yaml
# Добавь в docker-compose-db.yml:
postgres:
  command:
    - "postgres"
    - "-c"
    - "max_connections=200"
    - "-c"
    - "shared_buffers=256MB"
    - "-c"
    - "effective_cache_size=1GB"
```

### Redis
```yaml
# Уже настроено:
# - maxmemory 256mb
# - maxmemory-policy allkeys-lru
# - appendonly yes (persistence)
```

## 🔗 Связанные файлы
- `docker-compose-db.yml` — конфигурация сервисов
- `.env` — переменные окружения (НЕ коммитится в Git!)
- `env.example` — пример переменных окружения
