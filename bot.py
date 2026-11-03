import telebot
import os
from threading import Thread
from http.server import HTTPServer, BaseHTTPRequestHandler

# Токен теперь берется из секретных переменных сервера
TOKEN = os.environ.get('BOT_TOKEN')
bot = telebot.TeleBot(TOKEN)
chat_users = {}

@bot.message_handler(func=lambda message: True)
def handle_messages(message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    first_name = message.from_user.first_name

    if chat_id not in chat_users:
        chat_users[chat_id] = {}
        
    chat_users[chat_id][user_id] = first_name

    if message.text:
        bot_info = bot.get_me()
        bot_username = f"@{bot_info.username}"
        
        if bot_username in message.text:
            if len(chat_users[chat_id]) > 1:
                tags = [f'<a href="tg://user?id={uid}">{name}</a>' for uid, name in chat_users[chat_id].items()]
                mention_text = "Общий сбор!\n" + ", ".join(tags)
                bot.reply_to(message, mention_text, parse_mode='HTML')
            else:
                bot.reply_to(message, "Коллеги, напишите что нибудь в чат, что бы я вас запомнил")

# Заглушка для Render, чтобы он не думал, что бот завис
class SimpleHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is OK")

def run_server():
    port = int(os.environ.get('PORT', 10000))
    HTTPServer(('0.0.0.0', port), SimpleHandler).serve_forever()

if __name__ == '__main__':
    Thread(target=run_server).start()
    bot.infinity_polling()
