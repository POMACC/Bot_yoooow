import os
import html
import hmac
import logging

from flask import Flask, request, jsonify
import telebot
import redis

# ===================================
# НАСТРОЙКИ
# ===================================

TOKEN = os.environ["BOT_TOKEN"]
WEBHOOK_SECRET = os.environ["WEBHOOK_SECRET"]
REDIS_URL = os.environ.get("REDIS_URL")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
bot = telebot.TeleBot(TOKEN, threaded=False)

# Подключение к Redis
r = redis.from_url(
    REDIS_URL,
    decode_responses=True,
    socket_connect_timeout=5,
    socket_timeout=5
) if REDIS_URL else None


# ===================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ===================================

def is_group(message):
    return message.chat.type in ("group", "supergroup")


def get_chat_key(chat_id):
    return f"chat:{chat_id}:users"


def get_user_name(user):
    return user.first_name or user.username or "друг"


def check_redis(message):
    if r is None:
        bot.reply_to(
            message,
            "🐸 Хранилище пока не подключено."
        )
        return False

    try:
        r.ping()
        return True
    except redis.RedisError:
        logger.exception("Redis unavailable")
        bot.reply_to(
            message,
            "🐸 Не могу добраться до списка участников. "
            "Попробуй позже!"
        )
        return False


# ===================================
# ПРИВЕТСТВИЕ ПРИ ДОБАВЛЕНИИ В ГРУППУ
# ===================================

@bot.my_chat_member_handler()
def welcome_to_group(update):
    if update.chat.type not in ("group", "supergroup"):
        return

    old_status = update.old_chat_member.status
    new_status = update.new_chat_member.status

    if (
        old_status in ("left", "kicked")
        and new_status in ("member", "administrator")
    ):
        text = (
            "Привет! До вас не дописаться, "
            "поэтому «жаба собрала» 🐸\n\n"
            "Напишите /in, чтобы я вас запомнила "
            "и могла позвать на общий сбор!\n\n"
            "А когда нужно собрать всех, "
            "просто напишите /all."
        )

        bot.send_message(update.chat.id, text)


# ===================================
# /START — ПРИВЕТСТВИЕ В ЛИЧКЕ
# ===================================


@bot.message_handler(commands=["start"])
def send_welcome(message):
    if message.chat.type != "private":
        return

    text = (
        "Привет, это наша жаба-зазывала! 🐸\n\n"
        "Чтобы жаба начала работать, добавь её "
        "в групповой чат. Здесь она не отвечает "
        "на команды сбора."
    )

    bot.send_message(message.chat.id, text)


# ===================================
# /IN — ЗАПОМНИТЬ УЧАСТНИКА
# ===================================

@bot.message_handler(commands=["in"])
def handle_in(message):
    if not is_group(message):
        return

    if not check_redis(message):
        return

    user = message.from_user
    if not user:
        return

    chat_id = message.chat.id
    user_id = str(user.id)
    first_name = get_user_name(user)

    user_data = f"{user.username or ''}|{first_name}"

    try:
        key = get_chat_key(chat_id)
        already_registered = r.hexists(key, user_id)
        r.hset(key, user_id, user_data)

    except redis.RedisError:
        logger.exception("Registration failed")
        bot.reply_to(
            message,
            "🐸 Не получилось записать тебя."
        )
        return

    safe_name = html.escape(first_name)

    if already_registered:
        text = f"🐸 {safe_name}, я тебя уже помню!"
    else:
        text = f"🐸 Запомнила тебя, {safe_name}!"

    bot.reply_to(message, text, parse_mode="HTML")


# ===================================
# /OUT — УДАЛИТЬ УЧАСТНИКА
# ===================================

@bot.message_handler(commands=["out"])
def handle_out(message):
    if not is_group(message):
        return

    if not check_redis(message):
        return

    user = message.from_user
    if not user:
        return

    try:
        removed = r.hdel(
            get_chat_key(message.chat.id),
            str(user.id)
        )

    except redis.RedisError:
        logger.exception("Removal failed")
        bot.reply_to(
            message,
            "🐸 Не получилось обновить список."
        )
        return

    if removed:
        bot.reply_to(
            message,
            "🐸 Вычеркнула тебя из списка!"
        )
    else:
        bot.reply_to(
            message,
            "🐸 Тебя и так нет в моём списке."
        )


# ===================================
# /ALL — ОБЩИЙ СБОР
# ===================================

@bot.message_handler(commands=["all", "everyone"])
def handle_all(message):

    # Если команду отправили в личные сообщения
    if message.chat.type == "private":
        bot.reply_to(
            message,
            "Квааа!! Стоп, а кого тут звать? 🐸\n"
            "Добавь меня сначала в группу!"
        )
        return

    # В обычной группе команда работает
    if not is_group(message):
        return

    if not check_redis(message):
        return

    chat_id = message.chat.id

    try:
        users = r.hgetall(get_chat_key(chat_id))

    except redis.RedisError:
        logger.exception("Unable to fetch users")
        bot.reply_to(
            message,
            "🐸 Не удалось собрать участников."
        )
        return

    if not users:
        bot.reply_to(
            message,
            "🐸 Пока никого не запомнила! "
            "Пусть участники напишут /in."
        )
        return

    tags = []

    for user_id, user_data in sorted(users.items()):
        parts = user_data.split("|", 1)

        username = parts[0]
        first_name = (
            parts[1] if len(parts) > 1 else "Участник"
        )

        if username:
            tag = f"@{html.escape(username)}"
        else:
            tag = (
                f'<a href="tg://user?id={user_id}">'
                f'{html.escape(first_name)}</a>'
            )

        tags.append(tag)

    # Разделение на сообщения при длинном списке
    prefix = "🐸 Общий сбор!\n\n"
    batch = prefix

    for tag in tags:
        addition = tag + " "

        if len(batch) + len(addition) > 3500:
            bot.send_message(
                chat_id,
                batch.strip(),
                parse_mode="HTML"
            )
            batch = prefix

        batch += addition

    if batch.strip() != prefix.strip():
        bot.send_message(
            chat_id,
            batch.strip(),
            parse_mode="HTML"
        )


# ===================================
# VERCEL / FLASK
# ===================================

@app.route("/", methods=["GET"])
def index():
    return jsonify({
        "status": "ok",
        "message": "Жаба на связи 🐸"
    }), 200


@app.route("/webhook", methods=["POST"])
def webhook():
    supplied_secret = request.headers.get(
        "X-Telegram-Bot-Api-Secret-Token",
        ""
    )

    if not hmac.compare_digest(
        supplied_secret,
        WEBHOOK_SECRET
    ):
        return jsonify({"error": "Forbidden"}), 403

    if not request.is_json:
        return jsonify({"error": "Invalid content type"}), 415

    try:
        update_data = request.get_json()
        update = telebot.types.Update.de_json(update_data)
        bot.process_new_updates([update])

        return jsonify({"status": "ok"}), 200

    except Exception:
        logger.exception("Webhook processing failed")
        return jsonify({"error": "Internal error"}), 500


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5000)),
        debug=False
    )
