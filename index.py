from flask import Flask, request
import telebot
import os

# Получаем токен из переменных окружения Vercel или вставь свой сюда
TOKEN = os.environ.get('BOT_TOKEN', '9216230949:AAGc0N_hX9NyTZArI_KhhWGXrGJV7y2w3DM')
bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)

@bot.message_handler(commands=['start'])
def send_welcome(message):
    text = (
        "Привет, это наша жаба-зазывала.\n"
        "Добавьте в свою группу эту жабу.\n"
        "Все должны отметиться через команду /in, чтобы жаба их запомнила."
    )
    bot.reply_to(message, text)

@bot.message_handler(commands=['in'])
def handle_in(message):
    bot.reply_to(message, "🐸 Жаба записала тебя в список!")

@bot.message_handler(commands=['out'])
def handle_out(message):
    bot.reply_to(message, "🐸 Жаба вычеркнула тебя из списка.")

@bot.message_handler(commands=['everyone', 'all'])
def handle_all(message):
    bot.reply_to(message, "🐸 Общий сбор! (тут будут теги участников)")

# Прием запросов от Telegram через вебхук Vercel
@app.route(f'/{TOKEN}', methods=['POST'])
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
