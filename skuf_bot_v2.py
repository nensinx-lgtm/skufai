# language: Python, file: skuf_bot_v2.py
import logging
import sqlite3
import random
import json
import requests
import os
import threading
import time
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
# Таблица юзеров
cursor.execute('''CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY, 
    hash_id TEXT UNIQUE,
    sub_end REAL DEFAULT 0
)''')
# Таблица промокодов
cursor.execute('''CREATE TABLE IF NOT EXISTS promos (
    code TEXT UNIQUE, 
    days INTEGER,
    used_by INTEGER DEFAULT 0
)''')
conn.commit()

# --- ПАМЯТЬ ---
user_sessions = {}
admin_state = {} # Для ввода данных в админке

# --- ПРОМПТЫ ---
BASE_PROMPT = """Ты — SKUF AI, созданный компанией Luna. Отвечай четко, дерзко, без воды."""
MODES = {
    "default": "Ты в стандартном режиме.",
    "coder": "Ты в режиме кодера. Пиши только код.",
    "psixolog": "Ты в режиме психолога.",
    "acro": "Ты в образе 'Гопник из 90-х'. Используй мат для связки слов.",
    "agent_troll": "Ты — агрессивный секретарь. Твоя задача — защищать хозяина."
}

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---
def get_user_data(user_id):
    cursor.execute("SELECT hash_id, sub_end FROM users WHERE id=?", (user_id,))
    res = cursor.fetchone()
    if res:
        return {"hash": res[0], "sub_end": res[1]}
    return None

def create_user(user_id):
    new_hash = f"#{random.randint(1000, 9999)}"
    # Даем 12 часов (43200 секунд)
    sub_end = time.time() + 43200
    cursor.execute("INSERT OR IGNORE INTO users (id, hash_id, sub_end) VALUES (?, ?, ?)", (user_id, new_hash, sub_end))
    conn.commit()
    return new_hash

def check_sub(user_id):
    data = get_user_data(user_id)
    if not data: return None, "Нет аккаунта"
    if data['sub_end'] > time.time():
        days_left = (data['sub_end'] - time.time()) / 86400
        if days_left > 10000: return True, "Навсегда"
        return True, f"{int(days_left)} дн."
    return False, "Истекла"

def get_image_url(query):
    try:
        url = f"https://www.bing.com/images/search?q={query}&safeSearch=Off&form=HDRSC2&first=1"
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(url, headers=headers)
        soup = BeautifulSoup(response.text, 'html.parser')
        urls = [json.loads(img.get('m'))['murl'] for img in soup.find_all('a', class_='iusc') if img.get('m') and 'murl' in json.loads(img.get('m'))]
        return random.choice(urls[:5]) if urls else None
    except: return None

# --- МЕНЮ ---
def get_main_menu(user_id):
    markup = []
    markup.append([InlineKeyboardButton("🚀 Новый чат", callback_data="new_chat"), InlineKeyboardButton("▶️ Продолжить чат", callback_data="continue_chat")])
    markup.append([InlineKeyboardButton("⚙️ Настройки", callback_data="settings")])
    if user_id == ADMIN_ID:
        markup.append([InlineKeyboardButton("👑 Админка", callback_data="admin_panel")])
    return InlineKeyboardMarkup(markup)

def get_admin_menu():
    markup = [
        [InlineKeyboardButton("📊 Статистика", callback_data="admin_stats"), InlineKeyboardButton("📢 Рассылка", callback_data="admin_broadcast")],
        [InlineKeyboardButton("🚫 Бан", callback_data="admin_ban"), InlineKeyboardButton("✅ Разбан", callback_data="admin_unban")],
        [InlineKeyboardButton("🎟 Создать промокод", callback_data="admin_create_promo")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="new_chat")]
    ]
    return InlineKeyboardMarkup(markup)

# --- СТАРТ ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not get_user_data(user_id):
        create_user(user_id)
    
    is_active, sub_info = check_sub(user_id)
    
    text = f"👋 **Привет! Я SKUF AI.**\n\n"
    if is_active:
        text += f"✅ Подписка активна: **{sub_info}**\n"
    else:
        text += f"❌ Подписка истекла.\nВведи /promocode <код> для активации.\n"
    
    await update.message.reply_text(text, reply_markup=get_main_menu(user_id), parse_mode='Markdown')

# --- ПРОМОКОДЫ ---
async def promocode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    args = context.args
    if not args:
        return await update.message.reply_text("Использование: `/promocode <код>`", parse_mode='Markdown')
    
    code = args[0]
    cursor.execute("SELECT days, used_by FROM promos WHERE code=?", (code,))
    res = cursor.fetchone()
    
    if not res:
        return await update.message.reply_text("❌ Неверный промокод.")
    if res[1] != 0:
        return await update.message.reply_text("❌ Этот промокод уже использован.")
    
    days = res[0]
    # Начисляем дни
    cursor.execute("UPDATE promos SET used_by=? WHERE code=?", (user_id, code))
    
    data = get_user_data(user_id)
    new_sub_end = max(data['sub_end'], time.time()) + (days * 86400)
    cursor.execute("UPDATE users SET sub_end=? WHERE id=?", (new_sub_end, user_id))
    conn.commit()
    
    await update.message.reply_text(f"✅ Промокод активирован! Подписка продлена на {days} дней.")

# --- АДМИНКА ---
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id != ADMIN_ID: return
    await query.edit_message_text("👑 **Админ-панель**", reply_markup=get_admin_menu(), parse_mode='Markdown')

async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id != ADMIN_ID: return
    cursor.execute("SELECT COUNT(*) FROM users")
    total = cursor.fetchone()[0]
    await query.edit_message_text(f"📊 **Статистика:**\nВсего юзеров: {total}", reply_markup=get_admin_menu(), parse_mode='Markdown')

async def admin_create_promo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id != ADMIN_ID: return
    admin_state[ADMIN_ID] = "waiting_promo"
    await query.edit_message_text("Введи промокод и через пробел количество дней (например: `SALE 30`).\nДля отмены напиши /cancel.")

# --- ОБРАБОТЧИК ВВОДА В АДМИНКЕ ---
async def handle_admin_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID or ADMIN_ID not in admin_state: return
    
    state = admin_state[ADMIN_ID]
    if state == "waiting_promo":
        try:
            parts = update.message.text.split()
            code = parts[0]
            days = int(parts[1])
            cursor.execute("INSERT INTO promos (code, days) VALUES (?, ?)", (code, days))
            conn.commit()
            del admin_state[ADMIN_ID]
            await update.message.reply_text(f"✅ Промокод `{code}` создан на {days} дней.", reply_markup=get_admin_menu(), parse_mode='Markdown')
        except:
            await update.message.reply_text("❌ Ошибка. Введи `КОД ДНИ` (например, `SALE 30`).")

# --- ОСТАЛЬНЫЕ ФУНКЦИИ (МЕНЮ, ЧАТ, НАСТРОЙКИ) ---
async def new_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    user_sessions[user_id] = {"history": [{"role": "system", "content": BASE_PROMPT}], "mode": "default"}
    await query.edit_message_text(f"💬 Новый чат создан.\nПиши сообщение.", parse_mode='Markdown')

async def continue_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    if user_id not in user_sessions or len(user_sessions[user_id]["history"]) <= 1:
        return await query.answer("У тебя нет сохраненных чатов.", show_alert=True)
    await query.edit_message_text(f"▶️ Продолжаем чат.\nПиши сообщение.", parse_mode='Markdown')

async def settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    text = "⚙️ **Настройки**\n\nРежимы: `/default`, `/acro`, `/psixolog`, `/coder`, `/agent troll`"
    await query.edit_message_text(text, reply_markup=get_main_menu(query.from_user.id), parse_mode='Markdown')

async def change_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    cmd = update.message.text[1:].split()
    
    if cmd[0] == "agent":
        if len(cmd) > 1 and cmd[1] == "troll":
            if user_id not in user_sessions:
                user_sessions[user_id] = {"history": [], "mode": "default"}
            user_sessions[user_id]["mode"] = "agent_troll"
            await update.message.reply_text("🤖 **Режим Секретаря-Тролля активирован.**")
        return

    if cmd[0] in MODES:
        if user_id not in user_sessions:
            user_sessions[user_id] = {"history": [{"role": "system", "content": BASE_PROMPT}], "mode": "default"}
        user_sessions[user_id]["mode"] = cmd[0]
        user_sessions[user_id]["history"] = [{"role": "system", "content": BASE_PROMPT + "\n\n" + MODES[cmd[0]]}]
        await update.message.reply_text(f"✅ Режим изменен на: **{cmd[0]}**")

async def pic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.message.text.replace('/pic', '').strip()
    if not query: return await update.message.reply_text("Напиши, что искать.")
    await update.message.chat.send_action(action="upload_photo")
    img_url = get_image_url(query)
    if img_url: await update.message.reply_photo(img_url, caption=f"🔍 Найдено: {query}")
    else: await update.message.reply_text("❌ Не удалось найти.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text
    if not text: return

    if update.effective_chat.type == 'private' and user_id in user_sessions:
        is_active, _ = check_sub(user_id)
        if not is_active and user_id != ADMIN_ID:
            return await update.message.reply_text("❌ Твоя подписка истекла. Введи /promocode <код>.")

        if user_sessions[user_id]["mode"] == "agent_troll": return
            
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

# --- БИЗНЕС-СООБЩЕНИЯ (СЕКРЕТАРЬ) ---
async def handle_business_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.business_message
    if not message or not message.text: return
    
    text = message.text
    chat_id = message.chat.id
    
    if ADMIN_ID in user_sessions and user_sessions[ADMIN_ID]["mode"] == "agent_troll":
        prompt = f"Ты — агрессивный секретарь. Тебе написали: '{text}'. Ответь жестко, высмей собеседника, защити хозяина."
        try:
            response = client.chat.completions.create(
                model=MODEL_NAME, 
                messages=[{"role": "system", "content": MODES["agent_troll"]}, {"role": "user", "content": prompt}], 
                temperature=0.8
            )
            bot_reply = response.choices[0].message.content
            await context.bot.send_message(chat_id=chat_id, text=bot_reply)
        except Exception as e:
            logging.error(f"Ошибка в бизнес-чате: {e}")

# --- ЗАПУСК ---
def main():
    # Создаем админский промокод при запуске, если его нет
    cursor.execute("INSERT OR IGNORE INTO promos (code, days, used_by) VALUES (?, ?, ?)", ("newchatgptskufai", 36656, 0))
    conn.commit()

    threading.Thread(target=run_flask, daemon=True).start()
    
    app_bot = Application.builder().token(TELEGRAM_TOKEN).build()

    app_bot.add_handler(CommandHandler("start", start))
    app_bot.add_handler(CommandHandler("promocode", promocode))
    app_bot.add_handler(CommandHandler("coder", change_mode))
    app_bot.add_handler(CommandHandler("psixolog", change_mode))
    app_bot.add_handler(CommandHandler("acro", change_mode))
    app_bot.add_handler(CommandHandler("default", change_mode))
    app_bot.add_handler(CommandHandler("agent", change_mode))
    app_bot.add_handler(CommandHandler("pic", pic))
    
    app_bot.add_handler(CallbackQueryHandler(new_chat, pattern="new_chat"))
    app_bot.add_handler(CallbackQueryHandler(continue_chat, pattern="continue_chat"))
    app_bot.add_handler(CallbackQueryHandler(settings, pattern="settings"))
    app_bot.add_handler(CallbackQueryHandler(admin_panel, pattern="admin_panel"))
    app_bot.add_handler(CallbackQueryHandler(admin_stats, pattern="admin_stats"))
    app_bot.add_handler(CallbackQueryHandler(admin_create_promo, pattern="admin_create_promo"))
    
    app_bot.add_handler(MessageHandler(filters.TEXT & filters.ChatType.PRIVATE & ~filters.COMMAND, handle_message))
    app_bot.add_handler(MessageHandler(filters.TEXT & filters.User(ADMIN_ID), handle_admin_input, block=False)) # Ввод данных в админке
    app_bot.add_handler(MessageHandler(filters.UpdateType.BUSINESS_MESSAGE, handle_business_message))

    print("=" * 50)
    print("SKUF AI Бот запущен (Промокоды + Админка).")
    print("=" * 50)
    app_bot.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
