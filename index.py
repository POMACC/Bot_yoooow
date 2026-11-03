from flask import Flask, request
import telebot
import os
import redis

# Вставь свой реальный токен прямо сюда в кавычки:
TOKEN = "9216230949:AAGc0N_hX9NyTZArI_KhhWGXrGJV7y2w3DM"
bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)

redis_url = os.environ.get('REDIS_URL')
r = redis.from_url(redis_url) if redis_url else None

@bot.message_handler(commands=['start'])
def send_welcome(message):
    text = (
        "Привет, это наша жаба-зазывала 🐸\n"
        "Добавьте меня в группу, и пишите /in, чтобы я запомнила участников для общего сбора!"
    )
    bot.reply_to(message, text)

@bot.message_handler(commands=['in'])
def handle_in(message):
    chat_id = message.chat.id
    user = message.from_user
    user_id = user.id
    username = user.username
    first_name = user.first_name or "User"

    # Сохраняем пользователя в Redis (сет для конкретного чата)
    if r:
        # Храним данные в формате JSON или просто строку с именем и id/username
        user_data = f"{username}|{first_name}|{user_id}"
        r.sadd(f"chat:{chat_id}:users", user_data)
    
    bot.reply_to(message, f"🐸 Жаба записала тебя, {first_name}!")

@bot.message_handler(commands=['out'])
def handle_out(message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    
    if r:
        # Удаляем пользователя из базы чата
        users = r.smembers(f"chat:{chat_id}:users")
        for u in users:
            u_str = u.decode('utf-8')
            if str(user_id) in u_str:
                r.srem(f"chat:{chat_id}:users", u)
                
    bot.reply_to(message, "🐸 Жаба вычеркнула тебя из списка.")

@bot.message_handler(commands=['everyone', 'all'])
def handle_all(message):
    chat_id = message.chat.id
    tags = []

    if r:
        users = r.smembers(f"chat:{chat_id}:users")
        for u in users:
            u_str = u.decode('utf-8')
            parts = u_str.split('|')
            if len(parts) >= 3:
                username, first_name, user_id = parts[0], parts[1], parts[2]
                if username and username != 'None':
                    tags.append(f"@{username}")
                else:
                    # Если нет юзернейма, тегаем по имени через Markdown/HTML (скрытая ссылка на профиль)
                    tags.append(f"[{first_name}](tg://user?id={user_id})")

    if tags:
        text = "🐸 Общий сбор!\n\n" + " ".join(tags)
        bot.send_message(chat_id, text, parse_mode="Markdown")
    else:
        bot.reply_to(message, "🐸 Никто еще не записался! Пусть участники напишут /in")

@app.route('/', methods=['POST'])
def webhook():
    if request.headers.get('content-type') == 'application/json':
        json_string = request.get_data().decode('utf-8')
        update = telebot.types.Update.de_json(json_string)
        bot.process_new_updates([update])
        return '', 200
    else:
        return 'Forbidden', 403

@app.route('/')
def index():
    return 'Бот работает и принимает запросы!'

if __name__ == '__main__':
    app.run(debug=True)
