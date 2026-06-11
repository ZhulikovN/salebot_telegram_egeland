"""
Ручной тест отправки картинки через Salebot API (для спора с поддержкой Salebot).

Идея: повторить ровно то, что делал Salebot в своём успешном тесте, но нашими руками
и через НАШ боевой SalebotClient (тот же код, что в проде).

Отправляем тестовому клиенту 3 сообщения подряд:
  1. CONTROL  — публичная картинка ftcdn (их рабочий пример). Ожидаем: придёт картинкой.
  2. OUR      — наш URL с домена egeinformatika.ru. Сравниваем: картинка или ссылка.
  3. метка-текст между ними, чтобы было видно где что.

Как интерпретировать результат в Telegram у тестового клиента:
  - CONTROL картинкой + OUR картинкой  → проблема ушла (HEAD-фикс помог), всё работает.
  - CONTROL картинкой + OUR ссылкой    → Salebot/Telegram по-разному обрабатывает наш URL,
                                          при том что файл отдаёт 200 image/* даже их боту
                                          → проблема на стороне Salebot. Доказано.
  - CONTROL ссылкой                     → проблема в самом проекте/токене/клиенте (общая),
                                          смотреть настройки канала в Salebot.

ВАЖНО:
  - OUR_URL должен быть ЖИВЫМ на момент запуска (файлы в /tmp живут 5 минут).
    Сгенерируй свежий: отправь картинку менеджером в amoCRM, возьми url из логов
    воркера (строка "Sending media to Salebot ... url=...") и вставь ниже.
  - CLIENT_ID — это client.id из вебхука Salebot (НЕ platform_id).
    Из логов: platform_id=6253651200, bot=test_el_salebot → client_id=946936575.

Запуск:
    .venv/bin/python -m tests.manual_media_test
"""
import asyncio

from app.services.salebot_client import SalebotClient

# client.id тестового пользователя (из логов Salebot), НЕ platform_id
CLIENT_ID = 946936575

# Контроль: рабочий публичный пример самого Salebot
CONTROL_URL = (
    "https://t3.ftcdn.net/jpg/09/21/96/64/"
    "360_F_921966420_z9ihp0oKgSMicapn7568sTYgpzN3I65g.jpg"
)

# Наш URL — ВСТАВЬ СВЕЖИЙ ЖИВОЙ файл с egeinformatika.ru/media/...
OUR_URL = "https://egeinformatika.ru/media/43e50271bae8449d9f974d37dc1c4fbf.png"


async def main() -> None:
    client = SalebotClient()

    print("=== 1. CONTROL (ftcdn, рабочий пример Salebot) ===")
    r1 = await client.send_message(
        client_id=CLIENT_ID,
        message="",
        attachment_url=CONTROL_URL,
        attachment_type="image",
    )
    print("Salebot ответ:", r1)

    await client.send_message(client_id=CLIENT_ID, message="--- ниже наш файл ---")

    print("\n=== 2. OUR (egeinformatika.ru) ===")
    r2 = await client.send_message(
        client_id=CLIENT_ID,
        message="",
        attachment_url=OUR_URL,
        attachment_type="image",
    )
    print("Salebot ответ:", r2)

    await client.close()

    print(
        "\nГотово. Теперь открой Telegram тестового клиента и сравни:\n"
        "  - CONTROL пришёл картинкой?\n"
        "  - OUR пришёл картинкой или ссылкой?\n"
    )


if __name__ == "__main__":
    asyncio.run(main())
