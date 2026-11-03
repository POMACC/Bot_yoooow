from flask import Flask, request
import telebot
import os
import redis

TOKEN = os.environ.get('BOT_TOKEN')
REDIS_URL = os.environ.get('REDIS_URL')

bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)

# Подключение к базе Upstash
db = redis.from_url(REDIS_URL, decode_responses=True) if REDIS_URL else None

@bot.message_handler(commands=['in'])
def add_user(message):
    if db:
        db.hset(str(message.chat.id), str(message.from_user.id), message.from_user.first_name)
    bot.reply_to(message, "✅ В списке.")

@bot.message_handler(commands=['out'])
def remove_user(message):
    if db:
        db.hdel(str(message.chat.id), str(message.from_user.id))
    bot.reply_to(message, "❌ Удален из списка.")

@bot.message_handler(func=lambda message: True, content_types=['text', 'new_chat_members'])
def handle_all(message):
    chat_id = str(message.chat.id)
    
    # Автоматически сохраняем тех, кто пишет или вступает
    if db:
        if message.from_user and not message.from_user.is_bot:
            db.hset(chat_id, str(message.from_user.id), message.from_user.first_name)
        if message.content_type == 'new_chat_members':
            for new_user in message.new_chat_members:
                if not new_user.is_bot:
                    db.hset(chat_id, str(new_user.id), new_user.first_name)

    # Реакция на теги
    if message.text:
        text = message.text.lower()
        if any(cmd in text for cmd in ['/everyone', '/all', '@everyone', '@all', 'общий сбор']):
            chat_users = db.hgetall(chat_id) if db else {}
            if len(chat_users) > 0:
                tags = [f'<a href="tg://user?id={uid}">{name}</a>' for uid, name in chat_users.items()]
                mention_text = "Общий сбор!\n\n" + ", ".join(tags)
                bot.reply_to(message, mention_text, parse_mode='HTML')

# Системные функции для Vercel
@app.route('/', methods=['POST'])
def webhook():
    if request.headers.get('content-type') == 'application/json':
        json_string = request.get_data().decode('utf-8')
        update = telebot.types.Update.de_json(json_string)
        bot.process_new_updates([update])
        return ''
    return "OK"

@app.route('/', methods=['GET'])
def index():
    webhook_url = f"https://{request.host}/"
    bot.remove_webhook()
    bot.set_webhook(url=webhook_url)
    return "Вебхук установлен. Бот готов!"
