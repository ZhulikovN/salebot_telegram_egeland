# Telegram Relay

Отдельный маленький сервис для отправки медиа (фото/голосовые/видео/файлы) в
Telegram Bot API байтами. Разворачивается **не на основном сервере**
(salebot_telegram_egeland), а на отдельном VPS вне РФ-облаков — потому что
основной сервер (Yandex Cloud) не может напрямую достучаться до
`api.telegram.org` (сеть заблокирована), а этот сервер может.

## Как это работает

```
Основной бэкенд (Yandex Cloud) ──HTTPS──► Relay (AWS/др. VPS) ──HTTPS──► Telegram Bot API
        POST /send-media                        sendPhoto/sendVoice/...
```

Основной бэкенд скачивает файл из AmoCRM, шлёт его байтами на relay вместе с
токеном бота, `chat_id` и типом медиа. Relay сам вызывает нужный метод
Telegram Bot API. Если relay недоступен или Telegram отклонил файл — основной
бэкенд откатывается на старую отправку через Salebot (ссылкой).

## Развёртывание (на новом сервере, Ubuntu 22.04/24.04)

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y nginx certbot python3-certbot-nginx python3-venv python3-pip

mkdir -p ~/relay && cd ~/relay
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt   # скопировать requirements.txt на сервер заранее
```

Скопировать на сервер `app.py` и `.env.example` → `.env`, заполнить
`RELAY_SHARED_SECRET` случайной строкой:

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

Проверить, что сервис поднимается вручную:

```bash
source venv/bin/activate
uvicorn app:app --host 127.0.0.1 --port 8000
# в другом терминале:
curl http://127.0.0.1:8000/health
```

Настроить автозапуск через systemd (файл `telegram-relay.service` в этой
папке, поправить пути под реального пользователя, если отличается):

```bash
sudo cp telegram-relay.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now telegram-relay
sudo systemctl status telegram-relay
```

Настроить nginx как TLS-терминатор перед сервисом (`nginx.conf.example` в
этой папке) и выпустить сертификат:

```bash
sudo cp nginx.conf.example /etc/nginx/sites-available/relay
sudo ln -s /etc/nginx/sites-available/relay /etc/nginx/sites-enabled/relay
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d relay.egeinformatika.ru
```

## Проверка снаружи

```bash
curl https://relay.egeinformatika.ru/health
```

## Безопасность

- Доступ к порту 443 должен быть ограничён Security Group на уровне сервера
  до IP основного бэкенда (Yandex Cloud VM) — сам relay не должен быть
  публично открыт всем.
- `RELAY_SHARED_SECRET` передаётся в заголовке `X-Relay-Secret` при каждом
  запросе — без него `/send-media` отвечает `401`.
- Токены ботов на relay не хранятся — передаются в каждом запросе от
  основного бэкенда и нигде не логируются целиком.

