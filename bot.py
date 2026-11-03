import telebot
import sqlite3
import os

TOKEN = os.environ.get('BOT_TOKEN')
bot = telebot.TeleBot(TOKEN)

# Инициализируем локальную базу данных (как в оригинальном боте)
conn = sqlite3.connect('database.db', check_same_thread=False)
cursor = conn.cursor()
cursor.execute('''
    CREATE TABLE IF NOT EXISTS users (
        chat_id TEXT,
        user_id TEXT,
        first_name TEXT,
        UNIQUE(chat_id, user_id)
    )
''')
conn.commit()

# Команда /in - добавиться в список
@bot.message_handler(commands=['in'])
def add_user(message):
    chat_id = str(message.chat.id)
    user_id = str(message.from_user.id)
    first_name = message.from_user.first_name
    
    cursor.execute('INSERT OR REPLACE INTO users (chat_id, user_id, first_name) VALUES (?, ?, ?)', (chat_id, user_id, first_name))
    conn.commit()
    # Максимально короткий ответ без спама
    bot.reply_to(message, "✅ В списке.")

# Команда /out - удалиться из списка
@bot.message_handler(commands=['out'])
def remove_user(message):
    chat_id = str(message.chat.id)
    user_id = str(message.from_user.id)
    
    cursor.execute('DELETE FROM users WHERE chat_id = ? AND user_id = ?', (chat_id, user_id))
    conn.commit()
    bot.reply_to(message, "❌ Удален из списка.")

# Обработка тегов и авто-сохранение участников
@bot.message_handler(func=lambda message: True, content_types=['text', 'new_chat_members'])
def handle_all(message):
    chat_id = str(message.chat.id)
    
    # Автоматически сохраняем всех, кто пишет сообщения
    if message.from_user and not message.from_user.is_bot:
        cursor.execute('INSERT OR IGNORE INTO users (chat_id, user_id, first_name) VALUES (?, ?, ?)', 
                       (chat_id, str(message.from_user.id), message.from_user.first_name))
        conn.commit()

    # Автоматически сохраняем новых вступивших
    if message.content_type == 'new_chat_members':
        for new_user in message.new_chat_members:
            if not new_user.is_bot:
                cursor.execute('INSERT OR IGNORE INTO users (chat_id, user_id, first_name) VALUES (?, ?, ?)', 
                               (chat_id, str(new_user.id), new_user.first_name))
        conn.commit()

    # Реакция на теги
    if message.text:
        text = message.text.lower()
        if any(cmd in text for cmd in ['/everyone', '/all', '@everyone', '@all', 'общий сбор']):
            cursor.execute('SELECT user_id, first_name FROM users WHERE chat_id = ?', (chat_id,))
            users = cursor.fetchall()
            
            if len(users) > 0:
                tags = [f'<a href="tg://user?id={u[0]}">{u[1]}</a>' for u in users]
                mention_text = "Общий сбор!\n\n" + ", ".join(tags)
                bot.reply_to(message, mention_text, parse_mode='HTML')

bot.infinity_polling()
