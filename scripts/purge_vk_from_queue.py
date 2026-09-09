"""
Одноразовый скрипт: УДАЛИТЬ сообщения ВК-бота "203482421" из очередей
tasks:priority и tasks:bot (текущий backlog не нужен), не трогая сообщения
других мессенджеров.

Новые ВК-сообщения (после деплоя фикса в salebot_webhook.py/queue.py) уже
не попадают в tasks:priority/tasks:bot — они сразу уходят в отдельную
низкоприоритетную очередь tasks:vk_low и обрабатываются там. Этот скрипт
только чистит то, что накопилось ДО деплоя и что явно сказали не нужно.

Безопасность:
- Использует RENAME (атомарная операция) перед обработкой, чтобы не читать
  и не удалять из "живой" очереди напрямую.
- Воркеры (BLPOP с timeout=5) на время переименования просто не находят
  ключ и ждут следующую попытку — без ошибок.
- Новые сообщения (не ВК), пришедшие во время работы скрипта, создадут
  новый список автоматически (RPUSH создаёт список, если его нет) —
  не потеряются, просто могут оказаться в очереди чуть раньше некоторых
  "старых" отфильтрованных сообщений (порядок между разными диалогами
  не критичен).
- Если скрипт упадёт между RENAME и восстановлением — все исходные данные
  остаются целыми во временном ключе (tasks:priority:tmp / tasks:bot:tmp),
  их можно восстановить руками через RENAME обратно.
- Защита от повторного запуска: если tmp-ключ уже существует (прошлый
  запуск не завершился) — скрипт останавливается, а не затирает его молча.

Запуск (с хоста, где приложение имеет доступ к Redis):
    /home/ubuntu/.cache/pypoetry/virtualenvs/salebot-telegram-egeland-efy7SSHF-py3.12/bin/python \\
        scripts/purge_vk_from_queue.py --host localhost --port 6379 --password <pass>
"""
import argparse
import json

import redis

VK_BOT_NAME = "203482421"
SOURCE_QUEUES = ["tasks:priority", "tasks:bot"]


def purge(r: redis.Redis, queue_name: str) -> None:
    tmp_key = f"{queue_name}:tmp"

    # Защита от повторного запуска: если tmp-ключ уже существует (например,
    # прошлый запуск скрипта прервался посередине) — НЕ перезаписываем его
    # молча, иначе потеряем данные прошлой попытки.
    if r.exists(tmp_key):
        print(
            f"ОСТАНОВКА: {tmp_key} уже существует (похоже, прошлый запуск "
            f"не был завершён). Проверь его содержимое руками перед повтором:\n"
            f"  redis-cli llen {tmp_key}\n"
            f"Либо восстанови вручную: RENAME {tmp_key} {queue_name}"
        )
        raise SystemExit(1)

    # 1. Атомарно "забираем" очередь себе, оригинальный ключ временно пуст.
    if not r.exists(queue_name):
        print(f"{queue_name}: пусто/не существует, пропускаем")
        return

    r.rename(queue_name, tmp_key)

    # 2. Читаем всё, что было (порядок head->tail сохраняется).
    raw_items = r.lrange(tmp_key, 0, -1)
    print(f"{queue_name}: всего элементов = {len(raw_items)}")

    kept = []
    dropped = 0

    for raw in raw_items:
        try:
            task = json.loads(raw)
            bot_name = task.get("data", {}).get("bot_name")
        except Exception as e:
            # Не смогли распарсить — на всякий случай оставляем, не удаляем.
            print(f"  WARN: не удалось распарсить элемент, оставляю как есть: {e}")
            kept.append(raw)
            continue

        if bot_name == VK_BOT_NAME:
            dropped += 1
        else:
            kept.append(raw)

    print(f"{queue_name}: удалено ВК-сообщений = {dropped}, оставлено = {len(kept)}")

    # 3. Кладём обратно в исходном порядке (RPUSH сохраняет порядок head->tail).
    if kept:
        pipe = r.pipeline()
        for raw in kept:
            pipe.rpush(queue_name, raw)
        pipe.execute()

    # 4. Убираем временный ключ. ВК-сообщения (dropped) нигде не сохраняются —
    #    они безвозвратно удалены, как и требовалось.
    r.delete(tmp_key)
    print(f"{queue_name}: готово")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=6379)
    parser.add_argument("--password", default=None)
    parser.add_argument("--db", type=int, default=0)
    args = parser.parse_args()

    r = redis.Redis(
        host=args.host, port=args.port, password=args.password, db=args.db,
        decode_responses=True,
    )
    r.ping()

    for queue_name in SOURCE_QUEUES:
        purge(r, queue_name)


if __name__ == "__main__":
    main()
