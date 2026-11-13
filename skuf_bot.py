# language: Python, file: skuf_bot.py
import telebot
import sqlite3
import time
import datetime
import requests
import json
import random
import os
import threading
from bs4 import BeautifulSoup
from openai import OpenAI
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, LabeledPrice
from flask import Flask

# --- НАСТРОЙКИ ---
TELEGRAM_TOKEN = "9246046750:AAEHxks0Fgu4BKO27R_Rdi5wF-LFvxdygUE" # Твой токен
ZAI_API_KEY = "db05e8f6f2444ee3b605624db998708c.uQ6HPlI60NpBZBT8" 
CHANNEL_ID = "@skufv2"
ADMIN_ID = 7126354372

client = OpenAI(api_key=ZAI_API_KEY, base_url="https://api.z.ai/api/paas/v4/")
MODEL_NAME = "glm-4.5-flash"

bot = telebot.TeleBot(TELEGRAM_TOKEN)

# --- ВЕБ-СЕРВЕР ДЛЯ RENDER ---
app = Flask(__name__)
@app.route('/')
def health_check():
    return "SKUF AI is running!", 200

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

# --- ПАМЯТЬ ---
user_sessions = {} # user_id: {"history": [], "mode": "default"}
user_timed_bans = {}
is_generating = {}
agent_mode_chats = set() # ID чатов, где включен агент

# --- БАЗА ДАННЫХ (ХЕШИ) ---
conn = sqlite3.connect('skuf_bot.db', check_same_thread=False)
cursor = conn.cursor()
cursor.execute('''CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY, 
    hash_id TEXT UNIQUE,
    status TEXT DEFAULT 'user',
    sub_end REAL DEFAULT 0
)''')
conn.commit()

# --- ПРОМПТЫ ---
BASE_PROMPT = """Ты — SKUF AI, созданный компанией Luna. Отвечай четко, дерзко, без воды. Если просят код — пиши код."""
MODES = {
    "default": "Ты в стандартном режиме.",
    "coder": "Ты в режиме кодера. Пиши только код.",
    "psixolog": "Ты в режиме психолога. Анализируй эмоции, успокаивай.",
    "acro": "Ты в образе 'Гопник из 90-х'. Общайся на сленге, используй мат для связки слов.",
    "agent_troll": "Ты — секретарь-тролль. Твоя задача — следить за чатом. Если кто-то пишет агрессивно, тупит или оскорбляет других, ты должен ответить ему агрессией, высмеять его, защитить других участников. Будь жестким, но не переходи на личности."
}

# --- ФУНКЦИИ ХЕШЕЙ ---
def get_user_hash(user_id):
    cursor.execute("SELECT hash_id FROM users WHERE id=?", (user_id,))
    res = cursor.fetchone()
    return res[0] if res else None

def create_user_hash(user_id):
    new_hash = f"#{random.randint(1000, 9999)}"
    cursor.execute("SELECT id FROM users WHERE hash_id=?", (new_hash,))
    if cursor.fetchone():
        return create_user_hash(user_id)
    cursor.execute("INSERT OR IGNORE INTO users (id, hash_id) VALUES (?, ?)", (user_id, new_hash))
    cursor.execute("UPDATE users SET hash_id=? WHERE id=?", (new_hash, user_id))
    conn.commit()
    return new_hash

def check_channel_sub(user_id):
    try:
        member = bot.get_chat_member(CHANNEL_ID, user_id)
        return member.status in ['member', 'administrator', 'creator']
    except: return False

# --- МЕНЮ ---
def get_main_menu(user_id):
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("🚀 Новый чат", callback_data="new_chat"), InlineKeyboardButton("▶️ Продолжить", callback_data="continue_chat"))
    markup.add(InlineKeyboardButton("⚙️ Настройки", callback_data="settings"))
    if user_id == ADMIN_ID:
        markup.add(InlineKeyboardButton("👑 Админка", callback_data="admin_panel"))
    return markup

# --- СТАРТ ---
@bot.message_handler(commands=['start'])
def start(message):
    user_id = message.from_user.id
    user_hash = get_user_hash(user_id)
    
    if not user_hash:
        user_hash = create_user_hash(user_id)
    
    if not check_channel_sub(user_id):
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("📢 Подписаться", url=f"https://t.me/{CHANNEL_ID[1:]}"))
        markup.add(InlineKeyboardButton("✅ Проверить", callback_data="check_sub"))
        return bot.send_message(message.chat.id, "⚠️ **Для доступа к SKUF AI требуется подписка!**", reply_markup=markup, parse_mode='Markdown')

    welcome_text = f"""
👋 **Добро пожаловать в SKUF AI!**

Твой уникальный ID: **{user_hash}**
История твоих чатов сохранена навсегда.

🧠 **Доступные режимы:**
`/default` — стандартный
`/acro` — агрессивный (гопник)
`/psixolog` — психолог
`/coder` — кодер
`/agent troll` — бот-тролль для групп

👉 **Выбери действие:**
    """
    bot.send_message(message.chat.id, welcome_text, reply_markup=get_main_menu(user_id), parse_mode='Markdown')

@bot.callback_query_handler(func=lambda call: call.data == "check_sub")
def check_sub_callback(call):
    if check_channel_sub(call.from_user.id):
        bot.delete_message(call.message.chat.id, call.message.message_id)
        start(call.message)
    else:
        bot.answer_callback_query(call.id, "Вы не подписаны!", show_alert=True)

# --- МЕНЮ ---
@bot.callback_query_handler(func=lambda call: call.data == "new_chat")
def new_chat(call):
    user_id = call.from_user.id
    user_sessions[user_id] = {"history": [{"role": "system", "content": BASE_PROMPT + "\n\n" + MODES["default"]}], "mode": "default"}
    bot.edit_message_text(f"💬 Новый чат создан.\nТвой ID: **{get_user_hash(user_id)}**\nПиши сообщение.", chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=None, parse_mode='Markdown')

@bot.callback_query_handler(func=lambda call: call.data == "continue_chat")
def continue_chat(call):
    user_id = call.from_user.id
    if user_id not in user_sessions or len(user_sessions[user_id]["history"]) <= 1:
        return bot.answer_callback_query(call.id, "У тебя нет сохраненных чатов. Начни новый.", show_alert=True)
    bot.edit_message_text(f"▶️ Продолжаем чат.\nТвой ID: **{get_user_hash(user_id)}**", chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=None, parse_mode='Markdown')

@bot.callback_query_handler(func=lambda call: call.data == "settings")
def settings(call):
    text = "⚙️ **Настройки**\n\nСмена режимов командой в чате:\n`/default`, `/acro`, `/psixolog`, `/coder`, `/agent troll`"
    bot.edit_message_text(text, chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=get_main_menu(call.from_user.id), parse_mode='Markdown')

# --- РЕЖИМЫ ---
@bot.message_handler(commands=['coder', 'psixolog', 'acro', 'default', 'agent'])
def change_mode(message):
    user_id = message.from_user.id
    cmd = message.text[1:].split()
    
    if cmd[0] == "agent":
        if len(cmd) > 1 and cmd[1] == "troll":
            if message.chat.type in ['group', 'supergroup']:
                agent_mode_chats.add(message.chat.id)
                bot.reply_to(message, "🤖 **Режим Агента-Тролля активирован.**\nЯ буду следить за чатом и отвечать агрессией на агрессию.")
            else:
                bot.reply_to(message, "❌ Этот режим работает только в группах.")
        return

    if cmd[0] in MODES:
        if user_id not in user_sessions:
            user_sessions[user_id] = {"history": [{"role": "system", "content": BASE_PROMPT}], "mode": "default"}
        user_sessions[user_id]["mode"] = cmd[0]
        user_sessions[user_id]["history"][0] = {"role": "system", "content": BASE_PROMPT + "\n\n" + MODES[cmd[0]]}
        bot.reply_to(message, f"✅ Режим изменен на: **{cmd[0]}**")

# --- ИИ ЧАТ (ЛС) ---
@bot.message_handler(func=lambda message: message.chat.type == 'private' and message.from_user.id in user_sessions)
def chat_with_ai(message):
    user_id = message.from_user.id
    if not message.text: return
    
    bot.send_chat_action(message.chat.id, 'typing')
    try:
        history = user_sessions[user_id]["history"]
        history.append({"role": "user", "content": message.text})
        response = client.chat.completions.create(model=MODEL_NAME, messages=history, temperature=0.7)
        bot_reply = response.choices[0].message.content
        history.append({"role": "assistant", "content": bot_reply})
        if len(history) > 21: user_sessions[user_id]["history"] = [history[0]] + history[-20:]
        bot.reply_to(message, bot_reply)
    except Exception as e:
        bot.reply_to(message, f"❌ Ошибка: {e}")

# --- АГЕНТ ТРОЛЛИНГА (В ГРУППАХ) ---
@bot.message_handler(func=lambda message: message.chat.id in agent_mode_chats and message.chat.type in ['group', 'supergroup'])
def agent_troll(message):
    if message.text and not message.text.startswith('/') and message.from_user.id != bot.get_me().id:
        aggressive_words = ["хуй", "бля", "ебан", "пидор", "лох", "тупой", "идиот", "дурак"]
        if any(word in message.text.lower() for word in aggressive_words):
            prompt = f"Ты агент-тролль. Пользователь написал: '{message.text}'. Ответь ему агрессивно, высмей его, но не переходи на личности."
            
            chat_id = message.chat.id
            if chat_id not in user_sessions:
                user_sessions[chat_id] = {"history": [{"role": "system", "content": MODES["agent_troll"]}], "mode": "agent_troll"}
            
            history = user_sessions[chat_id]["history"]
            history.append({"role": "user", "content": prompt})
            
            try:
                bot.send_chat_action(message.chat.id, 'typing')
                response = client.chat.completions.create(model=MODEL_NAME, messages=history, temperature=0.8)
                bot_reply = response.choices[0].message.content
                history.append({"role": "assistant", "content": bot_reply})
                if len(history) > 21: user_sessions[chat_id]["history"] = [history[0]] + history[-20:]
                
                bot.reply_to(message, bot_reply)
            except:
                pass

# --- АДМИНКА ---
@bot.callback_query_handler(func=lambda call: call.data == "admin_panel")
def admin_panel(call):
    if call.from_user.id != ADMIN_ID: return
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("📊 Статистика", callback_data="admin_stats"))
    markup.add(InlineKeyboardButton("⬅️ Назад", callback_data="new_chat"))
    bot.edit_message_text("👑 Админ-панель:", chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data == "admin_stats")
def admin_stats(call):
    if call.from_user.id != ADMIN_ID: return
    cursor.execute("SELECT COUNT(*) FROM users")
    total = cursor.fetchone()[0]
    bot.edit_message_text(f"📊 **Статистика:**\nВсего юзеров: {total}", chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=get_main_menu(call.from_user.id), parse_mode='Markdown')

# --- ЗАПУСК ---
if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    print("=" * 50)
    print("SKUF AI Бот запущен (Хеши + Агент).")
    print("=" * 50)
    bot.infinity_polling()
