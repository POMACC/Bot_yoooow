"""Жаба-зазывала. Flask webhook для Vercel, Redis и pyTelegramBotAPI.

Переменные окружения: BOT_TOKEN, WEBHOOK_SECRET, REDIS_URL.
Установка: python -m pip install Flask pyTelegramBotAPI redis

После деплоя назначьте бота администратором группы (без лишних прав).
Один раз задайте WEBHOOK_URL в окружении: полный HTTPS URL /webhook.
Затем выполните: python zhababot.py --set-webhook
Существующие регистрации chat:<id>:users сохраняются.
"""

import html
import hmac
import json
import logging
import os
import re
import sys
import uuid
from contextvars import ContextVar

import redis
import telebot
from flask import Flask, jsonify, request
from telebot import types
from werkzeug.exceptions import BadRequest

TOKEN = os.environ["BOT_TOKEN"]
WEBHOOK_SECRET = os.environ["WEBHOOK_SECRET"]
REDIS_URL = os.environ.get("REDIS_URL")
TTL = 7 * 24 * 60 * 60
LOCK_TTL = 300
GATHER_COMMAND = re.compile(
    r"^\s*(?:/(?:all|everyone)(?:@(?P<bot>[A-Za-z0-9_]+))?|@all)(?=\s|$)",
    re.IGNORECASE,
)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
app = Flask(__name__)
bot = telebot.TeleBot(TOKEN, threaded=False)
r = redis.from_url(
    REDIS_URL, decode_responses=True,
    socket_connect_timeout=5, socket_timeout=5,
) if REDIS_URL else None
# Ключи защиты разделены по ботам, даже при общем Redis.
BOT_ID = TOKEN.split(":", 1)[0]
current_update = ContextVar("current_update", default=None)


def get_chat_key(chat_id):
    return f"chat:{chat_id}:users"


def is_group(message):
    return message.chat.type in ("group", "supergroup")


def get_user_name(user):
    return user.first_name or user.username or "друг"


def send_once(chat_id, text, *, slot="reply", **kwargs):
    """Не отправлять повторно уже успешно записанную часть ответа.

    Между Telegram sendMessage и записью в Redis нет общей транзакции:
    при обрыве соединения в этот момент редкий дубль всё ещё возможен.
    """
    update_id = current_update.get()
    key = f"bot:{BOT_ID}:update:{update_id}:sent:{slot}"
    if update_id is not None and r.exists(key):
        return
    bot.send_message(chat_id, text, **kwargs)
    if update_id is not None:
        r.set(key, "1", ex=TTL)


def reply(message, text, **kwargs):
    send_once(
        message.chat.id, text,
        reply_parameters=types.ReplyParameters(
            message_id=message.message_id, allow_sending_without_reply=True,
        ), **kwargs,
    )


@bot.my_chat_member_handler()
def welcome_to_group(update):
    if update.chat.type not in ("group", "supergroup"):
        return
    if (update.old_chat_member.status in ("left", "kicked")
            and update.new_chat_member.status in ("member", "administrator")):
        send_once(update.chat.id, (
            "Привет! До вас не дописаться, поэтому «жаба собрала» 🐸\n\n"
            "Напишите /in, чтобы я вас запомнила "
            "и могла позвать на общий сбор!\n\n"
            "А когда нужно собрать всех, просто напишите /all."
        ))


def member_is_present(member):
    return member.status in ("creator", "administrator", "member") or (
        member.status == "restricted" and member.is_member
    )


@bot.chat_member_handler()
def member_changed(update):
    if update.chat.type not in ("group", "supergroup"):
        return
    member = update.new_chat_member
    key = get_chat_key(update.chat.id)
    uid = str(member.user.id)
    if not member_is_present(member):
        r.hdel(key, uid)
    elif r.hexists(key, uid):
        # Обновляем имя, но не подписываем вошедших без /in.
        r.hset(key, uid, f"{member.user.username or ''}|{get_user_name(member.user)}")


@bot.message_handler(content_types=["left_chat_member"])
def member_left(message):
    if is_group(message) and message.left_chat_member:
        r.hdel(get_chat_key(message.chat.id), str(message.left_chat_member.id))


@bot.message_handler(commands=["start"])
def send_welcome(message):
    if message.chat.type == "private":
        send_once(message.chat.id, (
            "Привет, это наша жаба-зазывала! 🐸\n\n"
            "Чтобы жаба начала работать, добавь её в групповой чат. "
            "Здесь она не отвечает на команды сбора."
        ))


@bot.message_handler(commands=["in"])
def handle_in(message):
    if not is_group(message) or not message.from_user or message.sender_chat:
        return
    user = message.from_user
    if user.is_bot:
        return
    name = get_user_name(user)
    # HSET возвращает 1 для новой записи и 0 для существующей.
    added = r.hset(get_chat_key(message.chat.id), str(user.id),
                   f"{user.username or ''}|{name}")
    safe_name = html.escape(name)
    text = (f"🐸 Запомнила тебя, {safe_name}!" if added
            else f"🐸 {safe_name}, я тебя уже помню!")
    reply(message, text, parse_mode="HTML")


@bot.message_handler(commands=["out"])
def handle_out(message):
    if not is_group(message) or not message.from_user or message.sender_chat:
        return
    removed = r.hdel(get_chat_key(message.chat.id), str(message.from_user.id))
    reply(message, "🐸 Вычеркнула тебя из списка!" if removed
          else "🐸 Тебя и так нет в моём списке.")


def build_batches(users, note=""):
    prefix = "🐸 Общий сбор!\n\n"
    batch = prefix
    batches = []
    for uid, data in sorted(users.items()):
        if not uid.isdecimal():
            logger.warning("Skipping invalid stored user ID")
            continue
        parts = data.split("|", 1)
        name = parts[1] if len(parts) > 1 else "Участник"
        # Ограничение защищает от некорректных/слишком длинных старых данных.
        name = html.escape(name[:128] or "Участник")
        tag = f'<a href="tg://user?id={uid}">{name}</a> '
        if len((batch + tag).encode("utf-16-le")) // 2 > 3500:
            batches.append(batch.strip())
            batch = prefix
        batch += tag
    if batch != prefix:
        batches.append(batch.strip())
    if batches and note:
        # Длинный текст делим до экранирования HTML, чтобы не разрывать сущности.
        note_parts = [html.escape(note[i:i + 1700], quote=False)
                      for i in range(0, len(note), 1700)]
        combined = prefix + note_parts[-1] + "\n\n" + batches[0][len(prefix):]
        if len(combined.encode("utf-16-le")) // 2 <= 3500:
            batches[0] = combined
            note_parts.pop()
        batches = [prefix + part for part in note_parts] + batches
    return batches


@bot.message_handler(func=lambda message: bool(GATHER_COMMAND.match(message.text or "")))
def handle_all(message):
    command = GATHER_COMMAND.match(message.text or "")
    if not command:
        return
    addressed_bot = command.group("bot")
    if addressed_bot and addressed_bot.lower() != bot.user.username.lower():
        return

    if message.chat.type == "private":
        reply(message, "Квааа!! Стоп, а кого тут звать? 🐸\n"
              "Добавь меня сначала в группу!")
        return
    if not is_group(message):
        return

    # Сохраняем план сбора: при повторной доставке события состав частей
    # не меняется и уже отправленные части пропускаются.
    plan_key = f"bot:{BOT_ID}:update:{current_update.get()}:plan"
    stored = r.get(plan_key)
    if stored:
        batches = json.loads(stored)
    else:
        note = message.text[command.end():].strip()
        batches = build_batches(r.hgetall(get_chat_key(message.chat.id)), note)
        r.set(plan_key, json.dumps(batches, ensure_ascii=False), ex=TTL)
    if not batches:
        reply(message, "🐸 Пока никого не запомнила! "
              "Пусть участники напишут /in.")
        return

    target = message.reply_to_message
    for index, batch in enumerate(batches):
        kwargs = {"parse_mode": "HTML"}
        if target:
            kwargs["reply_parameters"] = types.ReplyParameters(
                message_id=target.message_id, allow_sending_without_reply=True,
            )
        if message.message_thread_id is not None:
            kwargs["message_thread_id"] = message.message_thread_id
        send_once(message.chat.id, batch, slot=f"all:{index}", **kwargs)


@app.route("/", methods=["GET"])
def index():
    return jsonify(status="ok", message="Жаба на связи 🐸"), 200


@app.route("/webhook", methods=["POST"])
def webhook():
    supplied = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not hmac.compare_digest(supplied.encode(), WEBHOOK_SECRET.encode()):
        return jsonify(error="Forbidden"), 403
    if not request.is_json:
        return jsonify(error="Invalid content type"), 415
    try:
        data = request.get_json()
    except BadRequest:
        return jsonify(error="Invalid JSON"), 400
    if not isinstance(data, dict) or type(data.get("update_id")) is not int:
        return jsonify(error="Invalid update"), 400
    if r is None:
        # Не подтверждаем событие: Telegram сможет повторить доставку.
        return jsonify(error="Storage unavailable"), 503

    update_id = data["update_id"]
    base = f"bot:{BOT_ID}:update:{update_id}"
    done_key, lock_key = base + ":done", base + ":lock"
    owner = uuid.uuid4().hex
    acquired = False
    context_token = None
    try:
        if r.exists(done_key):
            return jsonify(status="duplicate"), 200
        acquired = bool(r.set(lock_key, owner, nx=True, ex=LOCK_TTL))
        if not acquired:
            # 200 здесь мог бы потерять событие, если первый обработчик упадёт.
            return jsonify(error="Update in progress"), 503
        if r.exists(done_key):
            return jsonify(status="duplicate"), 200
        context_token = current_update.set(update_id)
        update = types.Update.de_json(data)
        bot.process_new_updates([update])
        r.set(done_key, "1", ex=TTL)
        return jsonify(status="ok"), 200
    except redis.RedisError:
        logger.error("Redis operation failed for update %s", update_id)
        return jsonify(error="Storage unavailable"), 503
    except Exception as exc:
        # Не пишем URL запросов с токеном бота в журнал.
        logger.error("Update %s failed (%s)", update_id, type(exc).__name__)
        return jsonify(error="Processing failed"), 500
    finally:
        if context_token is not None:
            current_update.reset(context_token)
        if acquired:
            try:
                r.eval(
                    "if redis.call('get', KEYS[1]) == ARGV[1] then "
                    "return redis.call('del', KEYS[1]) else return 0 end",
                    1, lock_key, owner,
                )
            except redis.RedisError:
                logger.error("Could not release update lock")


if __name__ == "__main__":
    if "--set-webhook" in sys.argv:
        bot.set_webhook(
            url=os.environ["WEBHOOK_URL"], secret_token=WEBHOOK_SECRET,
            allowed_updates=["message", "my_chat_member", "chat_member"],
            max_connections=1,
        )
        print("Webhook настроен. Существующие события не удалены.")
    else:
        app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
