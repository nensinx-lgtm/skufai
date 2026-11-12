# language: Python, file: skuf_bot.py
import telebot
import sqlite3
import time
import datetime
import os
import threading
import requests
from flask import Flask
from gigachat import GigaChat
from gigachat.models import Chat, Messages, MessagesRole
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton

# --- НАСТРОЙКИ ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "9246046750:AAEHxks0Fgu4BKO27R_Rdi5wF-LFvxdygUE")
GIGA_AUTH_KEY = os.getenv("GIGA_AUTH_KEY", "MDFhMWQxYWYtOTg1NC03Y2E5LTg2YjQtOTc1ZTM0YzRmNTQxOjg3M2ZiN2E4LTY4MjUtNDNiZC1iZmVmLWMwMzI0ZDBiN2E2Ng==")
CHANNEL_ID = "@skufv2"
ADMIN_ID = 7126354372

# --- ВЕБ-СЕРВЕР ДЛЯ RENDER ---
app = Flask(__name__)
@app.route('/')
def health_check():
    return "SKUF AI is running!", 200

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

# --- ПИНГ ДЛЯ ХОСТИНГА ---
def keep_alive():
    while True:
        time.sleep(600) # Ждем 10 минут (600 секунд)
        try:
            # Отправляем запрос к API Telegram (имитация активности)
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getMe"
            requests.get(url, timeout=10)
            print(f"[PING] {datetime.datetime.now().strftime('%H:%M:%S')} - Бот активен, Render не спит.")
        except Exception as e:
            print(f"[PING] Ошибка пинга: {e}")

# --- НАСТРОЙКА GIGACHAT ---
print("[START] Настройка GigaChat...")
giga = GigaChat(
    credentials=GIGA_AUTH_KEY,
    scope="GIGACHAT_API_PERS",
    model="GigaChat-2",
    verify_ssl_certs=False
)

bot = telebot.TeleBot(TELEGRAM_TOKEN)

# Память
user_sessions = {}
user_message_timestamps = {}
user_timed_bans = {}
is_generating = {}

# --- ЛОГИРОВАНИЕ ---
def log_ban(user_id, username, reason, action="BAN"):
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open("skuf_bans.log", "a", encoding="utf-8") as f:
            f.write(f"[{timestamp}] [SKUF AI] {action} | ID: {user_id} | @{username} | Причина: {reason}\n")
    except Exception as e:
        print(f"Ошибка записи лога: {e}")

# --- БАЗА ДАННЫХ ---
conn = sqlite3.connect('skuf_bot.db', check_same_thread=False)
cursor = conn.cursor()
cursor.execute('''CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY, 
    status TEXT DEFAULT 'user'
)''')
conn.commit()

# --- ФУНКЦИИ ---
def check_sub(user_id):
    try:
        member = bot.get_chat_member(CHANNEL_ID, user_id)
        return member.status in ['member', 'administrator', 'creator']
    except:
        return False

def get_user_status(user_id):
    cursor.execute("SELECT status FROM users WHERE id=?", (user_id,))
    res = cursor.fetchone()
    return res[0] if res else None

def add_user(user_id):
    cursor.execute("INSERT OR IGNORE INTO users (id) VALUES (?)", (user_id,))
    conn.commit()

def main_menu():
    markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(KeyboardButton("🚀 Создать проект"), KeyboardButton("⚙️ Настройки"))
    markup.add(KeyboardButton("👑 Админка"))
    return markup

# --- СТАРТ ---
@bot.message_handler(commands=['start'])
def start(message):
    user_id = message.from_user.id
    add_user(user_id)

    if get_user_status(user_id) == 'banned':
        bot.send_message(message.chat.id, "❌ Вы заблокированы.")
        return

    if not check_sub(user_id):
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("📢 Подписаться на канал", url=f"https://t.me/{CHANNEL_ID[1:]}"))
        markup.add(InlineKeyboardButton("✅ Проверить подписку", callback_data="check_sub"))
        bot.send_message(message.chat.id, "⚠️ **Для доступа к SKUF AI требуется подписка!**", reply_markup=markup, parse_mode='Markdown')
        return

    bot.send_message(message.chat.id, "👋 Привет! Я **SKUF AI**. Жми «Создать проект».", reply_markup=main_menu(), parse_mode='Markdown')

@bot.callback_query_handler(func=lambda call: call.data == "check_sub")
def check_sub_callback(call):
    if check_sub(call.from_user.id):
        bot.delete_message(call.message.chat.id, call.message.message_id)
        bot.send_message(call.message.chat.id, "✅ Подписка подтверждена!\n👋 Привет!", reply_markup=main_menu(), parse_mode='Markdown')
    else:
        bot.answer_callback_query(call.id, "Вы не подписаны!", show_alert=True)

# --- МЕНЮ ---
@bot.message_handler(func=lambda message: message.text == "🚀 Создать проект")
def create_project(message):
    if get_user_status(message.from_user.id) == 'banned': return
    bot.send_message(message.chat.id, "💬 Режим чата активирован. Напиши 'exit' чтобы выйти.")
    user_sessions[message.from_user.id] = [Messages(role=MessagesRole.SYSTEM, content="Ты — SKUF AI, умный и дерзкий ИИ.")]

@bot.message_handler(func=lambda message: message.text == "⚙️ Настройки")
def settings(message):
    bot.send_message(message.chat.id, "⚙️ Настройки.", parse_mode='Markdown')

@bot.message_handler(func=lambda message: message.text == "👑 Админка")
def admin_panel(message):
    if message.from_user.id != ADMIN_ID: return
    markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(KeyboardButton("📢 Рассылка"), KeyboardButton("📊 Онлайн"))
    markup.add(KeyboardButton("🚫 Бан"), KeyboardButton("✅ Разбан"), KeyboardButton("⬅️ Назад"))
    bot.send_message(message.chat.id, "👑 Админ-панель:", reply_markup=markup)

@bot.message_handler(func=lambda message: message.text == "⬅️ Назад")
def back(message):
    bot.send_message(message.chat.id, "Главное меню.", reply_markup=main_menu())

# --- ИИ ЧАТ ---
@bot.message_handler(func=lambda message: message.from_user.id in user_sessions)
def chat_with_ai(message):
    user_id = message.from_user.id
    username = message.from_user.username or "no_username"

    if user_id in user_timed_bans and time.time() < user_timed_bans[user_id]: return
    elif user_id in user_timed_bans: del user_timed_bans[user_id]

    if get_user_status(user_id) == 'banned':
        bot.send_message(message.chat.id, "❌ Вы заблокированы.")
        return

    if user_id in is_generating and is_generating[user_id]:
        bot.send_message(message.chat.id, "⚠️ Подожди, я еще пишу ответ.")
        return

    if not message.text: return
    if len(message.text) > 2000: return

    if message.text.lower() == 'exit':
        del user_sessions[user_id]
        return bot.send_message(message.chat.id, "Вышел.", reply_markup=main_menu())

    current_time = time.time()
    if user_id not in user_message_timestamps: user_message_timestamps[user_id] = []
    user_message_timestamps[user_id] = [t for t in user_message_timestamps[user_id] if current_time - t < 60]
    
    if len(user_message_timestamps[user_id]) >= 5:
        user_timed_bans[user_id] = current_time + (15 * 60)
        if user_id in user_sessions: del user_sessions[user_id]
        user_message_timestamps[user_id] = []
        log_ban(user_id, username, "Флуд", "AUTO-BAN")
        bot.send_message(message.chat.id, "🚫 **Вы заблокированы на 15 минут за флуд.**", parse_mode='Markdown')
        return
    user_message_timestamps[user_id].append(current_time)

    is_generating[user_id] = True
    bot.send_chat_action(message.chat.id, 'typing')
    
    try:
        history = user_sessions[user_id]
        history.append(Messages(role=MessagesRole.USER, content=message.text))
        response = giga.chat(Chat(messages=history))
        bot_reply = response.choices[0].message.content
        history.append(Messages(role=MessagesRole.ASSISTANT, content=bot_reply))
        if len(bot_reply) > 4000: bot_reply = bot_reply[:4000] + "..."
        bot.reply_to(message, bot_reply)
    except Exception as e:
        bot.reply_to(message, f"❌ Ошибка: {e}")
    finally:
        is_generating[user_id] = False

# --- АДМИН: РАССЫЛКА ---
@bot.message_handler(func=lambda message: message.from_user.id == ADMIN_ID and message.text == "📢 Рассылка")
def broadcast_start(message):
    msg = bot.send_message(message.chat.id, "Введите текст для рассылки (или 'отмена'):")
    bot.register_next_step_handler(msg, broadcast_send)

def broadcast_send(message):
    if message.text.lower() == 'отмена':
        return bot.send_message(message.chat.id, "Отменено.", reply_markup=main_menu())
    text = message.text
    cursor.execute("SELECT id FROM users")
    users = cursor.fetchall()
    success, fail = 0, 0
    for (uid,) in users:
        try:
            bot.send_message(uid, f"📢 **Анонс:**\n\n{text}", parse_mode='Markdown')
            success += 1
        except:
            fail += 1
    bot.send_message(message.chat.id, f"✅ Отправлено: {success}\nОшибок: {fail}", reply_markup=main_menu())

# --- АДМИН: СТАТИСТИКА ---
@bot.message_handler(func=lambda message: message.from_user.id == ADMIN_ID and message.text == "📊 Онлайн")
def stats(message):
    cursor.execute("SELECT COUNT(*) FROM users")
    total = cursor.fetchone()[0]
    bot.send_message(message.chat.id, f"📊 **Статистика:**\nЮзеров: {total}", parse_mode='Markdown')

# --- АДМИН: БАН/РАЗБАН ---
@bot.message_handler(func=lambda message: message.from_user.id == ADMIN_ID and message.text == "🚫 Бан")
def ban_start(message):
    msg = bot.send_message(message.chat.id, "Введите ID пользователя:")
    bot.register_next_step_handler(msg, ban_user)

def ban_user(message):
    target_id = message.forward_from.id if message.forward_from else message.text
    try:
        cursor.execute("UPDATE users SET status='banned' WHERE id=?", (int(target_id),))
        conn.commit()
        log_ban(target_id, "admin", "Ручной бан", "BAN")
        bot.send_message(message.chat.id, f"Забанен.", reply_markup=main_menu())
    except:
        bot.send_message(message.chat.id, "Ошибка ID.", reply_markup=main_menu())

@bot.message_handler(func=lambda message: message.from_user.id == ADMIN_ID and message.text == "✅ Разбан")
def unban_start(message):
    msg = bot.send_message(message.chat.id, "Введите ID пользователя:")
    bot.register_next_step_handler(msg, unban_user)

def unban_user(message):
    try:
        cursor.execute("UPDATE users SET status='user' WHERE id=?", (int(message.text),))
        conn.commit()
        log_ban(message.text, "admin", "Ручной разбан", "UNBAN")
        bot.send_message(message.chat.id, "Разбанен.", reply_markup=main_menu())
    except:
        bot.send_message(message.chat.id, "Ошибка ID.", reply_markup=main_menu())

# --- ЗАПУСК ---
if __name__ == "__main__":
    # Запускаем Flask в отдельном потоке
    threading.Thread(target=run_flask, daemon=True).start()
    # Запускаем пинг в отдельном потоке
    threading.Thread(target=keep_alive, daemon=True).start()
    
    print("=" * 50)
    print("SKUF AI Бот запущен (Render Mode + Ping).")
    print("=" * 50)
    bot.infinity_polling()