# language: Python, file: skuf_bot_v2.py
import logging
import sqlite3
import random
import json
import requests
import os
import threading
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
from openai import OpenAI
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler

# --- НАСТРОЙКИ ---
TELEGRAM_TOKEN = "9246046750:AAEHxks0Fgu4BKO27R_Rdi5wF-LFvxdygUE" 
ZAI_API_KEY = "db05e8f6f2444ee3b605624db998708c.uQ6HPlI60NpBZBT8" 
CHANNEL_ID = "@skufv2"
ADMIN_ID = 7126354372

client = OpenAI(api_key=ZAI_API_KEY, base_url="https://api.z.ai/api/paas/v4/")
MODEL_NAME = "glm-4.5-flash"

# --- ЛОГИРОВАНИЕ ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# --- ВЕБ-СЕРВЕР ДЛЯ RENDER ---
app = Flask(__name__)
@app.route('/')
def health_check():
    return "SKUF AI is running!", 200

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

# --- БАЗА ДАННЫХ ---
conn = sqlite3.connect('skuf_bot.db', check_same_thread=False)
cursor = conn.cursor()
cursor.execute('''CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, hash_id TEXT UNIQUE)''')
conn.commit()

# --- ПАМЯТЬ ---
user_sessions = {} # Хранит режимы и историю
agent_mode_chats = set()

# --- ПРОМПТЫ ---
BASE_PROMPT = """Ты — SKUF AI, созданный компанией Luna. Отвечай четко, дерзко, без воды."""
MODES = {
    "default": "Ты в стандартном режиме.",
    "coder": "Ты в режиме кодера. Пиши только код.",
    "psixolog": "Ты в режиме психолога.",
    "acro": "Ты в образе 'Гопник из 90-х'. Используй мат для связки слов.",
    "agent_troll": "Ты — агрессивный секретарь. Твоя задача — защищать хозяина. Если ему пишут гадости, ты должен ответить жестко, высмеять собеседника, но не переходить на личности. Будь дерзким и злым."
}

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---
def get_user_hash(user_id):
    cursor.execute("SELECT hash_id FROM users WHERE id=?", (user_id,))
    res = cursor.fetchone()
    return res[0] if res else None

def create_user_hash(user_id):
    new_hash = f"#{random.randint(1000, 9999)}"
    cursor.execute("INSERT OR IGNORE INTO users (id, hash_id) VALUES (?, ?)", (user_id, new_hash))
    cursor.execute("UPDATE users SET hash_id=? WHERE id=?", (new_hash, user_id))
    conn.commit()
    return new_hash

def get_image_url(query):
    try:
        url = f"https://www.bing.com/images/search?q={query}&safeSearch=Off&form=HDRSC2&first=1"
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(url, headers=headers)
        soup = BeautifulSoup(response.text, 'html.parser')
        urls = [json.loads(img.get('m'))['murl'] for img in soup.find_all('a', class_='iusc') if img.get('m') and 'murl' in json.loads(img.get('m'))]
        return random.choice(urls[:5]) if urls else None
    except: return None

def get_main_menu():
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("🚀 Новый чат", callback_data="new_chat"), InlineKeyboardButton("⚙️ Настройки", callback_data="settings")]
    ])
    return markup

# --- ОБРАБОТЧИКИ ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_hash = get_user_hash(user_id)
    if not user_hash:
        user_hash = create_user_hash(user_id)
    
    welcome_text = f"""
👋 **Добро пожаловать в SKUF AI!**

Твой уникальный ID: **{user_hash}**
Я работаю в личных сообщениях, а также умею быть секретарем (если ты подключил меня в настройках) и агентом-троллем в группах.

🧠 **Режимы:**
`/default` — стандартный
`/acro` — агрессивный (гопник)
`/psixolog` — психолог
`/coder` — кодер
`/agent troll` — секретарь-тролль (для ЛС и групп)

👉 **Выбери действие:**
    """
    await update.message.reply_text(welcome_text, reply_markup=get_main_menu(), parse_mode='Markdown')

async def new_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    user_sessions[user_id] = {"history": [{"role": "system", "content": BASE_PROMPT}], "mode": "default"}
    await query.edit_message_text(f"💬 Новый чат создан.\nТвой ID: **{get_user_hash(user_id)}**\nПиши сообщение.", parse_mode='Markdown')

async def settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    text = "⚙️ **Настройки**\n\nРежимы меняются командами в чате:\n`/default`, `/acro`, `/psixolog`, `/coder`, `/agent troll`"
    await query.edit_message_text(text, reply_markup=get_main_menu(), parse_mode='Markdown')

async def change_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    cmd = update.message.text[1:].split()
    
    if cmd[0] == "agent":
        if len(cmd) > 1 and cmd[1] == "troll":
            if user_id not in user_sessions:
                user_sessions[user_id] = {"history": [], "mode": "default"}
            user_sessions[user_id]["mode"] = "agent_troll"
            await update.message.reply_text("🤖 **Режим Секретаря-Тролля активирован.**\nЯ буду следить за твоими чатами и отвечать агрессией на агрессию (если подключен как секретарь).")
        return

    if cmd[0] in MODES:
        if user_id not in user_sessions:
            user_sessions[user_id] = {"history": [{"role": "system", "content": BASE_PROMPT}], "mode": "default"}
        user_sessions[user_id]["mode"] = cmd[0]
        user_sessions[user_id]["history"] = [{"role": "system", "content": BASE_PROMPT + "\n\n" + MODES[cmd[0]]}]
        await update.message.reply_text(f"✅ Режим изменен на: **{cmd[0]}**")

async def pic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.message.text.replace('/pic', '').strip()
    if not query:
        return await update.message.reply_text("Напиши, что искать.")
    
    await update.message.chat.send_action(action="upload_photo")
    img_url = get_image_url(query)
    if img_url:
        await update.message.reply_photo(img_url, caption=f"🔍 Найдено: {query}")
    else:
        await update.message.reply_text("❌ Не удалось найти.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text
    if not text: return

    # Если это ЛС и у юзера есть сессия (обычный чат)
    if update.effective_chat.type == 'private' and user_id in user_sessions:
        # Если включен тролль, в ЛС он не должен отвечать на свои же сообщения, только на бизнес
        if user_sessions[user_id]["mode"] == "agent_troll":
            return
            
        await update.message.chat.send_action(action="typing")
        try:
            history = user_sessions[user_id]["history"]
            history.append({"role": "user", "content": text})
            response = client.chat.completions.create(model=MODEL_NAME, messages=history, temperature=0.7)
            bot_reply = response.choices[0].message.content
            history.append({"role": "assistant", "content": bot_reply})
            if len(history) > 21: user_sessions[user_id]["history"] = [history[0]] + history[-20:]
            await update.message.reply_text(bot_reply)
        except Exception as e:
            await update.message.reply_text(f"❌ Ошибка: {e}")

# --- ОБРАБОТЧИК БИЗНЕС-СООБЩЕНИЙ (СЕКРЕТАРЬ-ТРОЛЛЬ) ---
async def handle_business_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.business_message
    if not message or not message.text: return
    
    text = message.text
    chat_id = message.chat.id
    
    # Проверяем, включен ли у тебя (Админа) режим тролля
    if ADMIN_ID in user_sessions and user_sessions[ADMIN_ID]["mode"] == "agent_troll":
        logging.info(f"[BUSINESS TROLL] Сообщение: {text}")
        
        prompt = f"Ты — агрессивный секретарь. Тебе написали: '{text}'. Ответь жестко, высмей собеседника, защити хозяина. Не переходи на личности."
        
        try:
            response = client.chat.completions.create(
                model=MODEL_NAME, 
                messages=[{"role": "system", "content": MODES["agent_troll"]}, {"role": "user", "content": prompt}], 
                temperature=0.8
            )
            bot_reply = response.choices[0].message.content
            
            # Отправляем ответ в чат
            await context.bot.send_message(chat_id=chat_id, text=bot_reply)
        except Exception as e:
            logging.error(f"Ошибка в бизнес-чате: {e}")

# --- АГЕНТ ТРОЛЛИНГА (ГРУППЫ) ---
async def agent_troll_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if message.chat.id in agent_mode_chats and message.text and not message.text.startswith('/'):
        aggressive_words = ["хуй", "бля", "ебан", "пидор", "лох", "тупой", "идиот", "дурак"]
        if any(word in message.text.lower() for word in aggressive_words):
            prompt = f"Ты агент-тролль. Пользователь написал: '{message.text}'. Ответь ему агрессивно."
            try:
                await message.chat.send_action(action="typing")
                response = client.chat.completions.create(
                    model=MODEL_NAME, 
                    messages=[{"role": "system", "content": MODES["agent_troll"]}, {"role": "user", "content": prompt}], 
                    temperature=0.8
                )
                await message.reply_text(response.choices[0].message.content)
            except: pass

# --- ЗАПУСК ---
def main():
    # Запускаем Flask в фоне
    threading.Thread(target=run_flask, daemon=True).start()
    
    app_bot = Application.builder().token(TELEGRAM_TOKEN).build()

    # Команды
    app_bot.add_handler(CommandHandler("start", start))
    app_bot.add_handler(CommandHandler("coder", change_mode))
    app_bot.add_handler(CommandHandler("psixolog", change_mode))
    app_bot.add_handler(CommandHandler("acro", change_mode))
    app_bot.add_handler(CommandHandler("default", change_mode))
    app_bot.add_handler(CommandHandler("agent", change_mode))
    app_bot.add_handler(CommandHandler("pic", pic))
    
    # Кнопки
    app_bot.add_handler(CallbackQueryHandler(new_chat, pattern="new_chat"))
    app_bot.add_handler(CallbackQueryHandler(settings, pattern="settings"))
    
    # Обработчик ЛС (обычный чат)
    app_bot.add_handler(MessageHandler(filters.TEXT & filters.ChatType.PRIVATE, handle_message))
    
    # Обработчик Бизнес-сообщений (Секретарь)
    app_bot.add_handler(MessageHandler(filters.UpdateType.BUSINESS_MESSAGE, handle_business_message))
    
    # Обработчик групп (Агент)
    app_bot.add_handler(MessageHandler(filters.TEXT & filters.ChatType.GROUPS, agent_troll_group))

    print("=" * 50)
    print("SKUF AI Бот запущен (Секретарь + Агент).")
    print("=" * 50)
    app_bot.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
