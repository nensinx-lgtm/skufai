# language: Python, file: skuf.py
import logging
import sqlite3
import random
import json
import requests
import time
import re
import ssl
import os
import threading
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
from openai import OpenAI
from gigachat import GigaChat
from gigachat.models import Chat, Messages, MessagesRole
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
from flask import Flask

# --- SSL ---
ssl._create_default_https_context = ssl._create_unverified_context
os.environ['PYTHONHTTPSVERIFY'] = '0'
os.environ['CURL_CA_BUNDLE'] = ''

# --- НАСТРОЙКИ ---
TELEGRAM_TOKEN = "9246046750:AAEHxks0Fgu4BKO27R_Rdi5wF-LFvxdygUE"
GIGA_AUTH_KEY = "MDFhMWQxYWYtOTg1NC03Y2E5LTg2YjQtOTc1ZTM0YzRmNTQxOjg3M2ZiN2E4LTY4MjUtNDNiZC1iZmVmLWMwMzI0ZDBiN2E2Ng=="
ZAI_API_KEY = "db05e8f6f2444ee3b605624db998708c.uQ6HPlI60NpBZBT8"
ADMIN_ID = 7126354372
ADMIN_USERNAME = "m0_nk1"
CHANNEL_USERNAME = "@skufv2"

# --- КЛИЕНТЫ ---
giga = GigaChat(credentials=GIGA_AUTH_KEY, scope="GIGACHAT_API_PERS", model="GigaChat-2-Max", verify_ssl_certs=False, timeout=30)
client_zai = OpenAI(api_key=ZAI_API_KEY, base_url="https://api.z.ai/api/paas/v4/")
MODEL_ZAI = "glm-4.5"

# --- БАЗА ---
conn = sqlite3.connect('skuf.db', check_same_thread=False)
cursor = conn.cursor()
cursor.execute('''CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY, 
    username TEXT,
    sub_end REAL DEFAULT 0,
    msg_limit INTEGER DEFAULT 50,
    daily_limit INTEGER DEFAULT 10,
    daily_reset REAL DEFAULT 0,
    req_limit INTEGER DEFAULT 10,
    ref_by INTEGER DEFAULT 0,
    ref_count INTEGER DEFAULT 0,
    registered REAL,
    last_active REAL,
    style TEXT DEFAULT 'default'
)''')
cursor.execute('''CREATE TABLE IF NOT EXISTS bans (id INTEGER PRIMARY KEY)''')
conn.commit()

# --- ПАМЯТЬ ---
user_sessions = {}
user_locks = {}
user_bans = {}

STYLES = {
    "default": (
        "Ты — SKUF AI, дружелюбный и развёрнутый собеседник. "
        "Отвечай подробно, объясняй свои мысли, приводи примеры, если это уместно. "
        "Не будь сухим и формальным. Общайся как живой человек, который хочет помочь. "
        "Если вопрос сложный — разбей ответ на части. Если простой — не растягивай искусственно, но и не отвечай односложно."
    ),
    "aggressive": (
        "Ты — SKUF AI в режиме максимальной агрессии. "
        "Твоя задача — отвечать максимально жёстко, с сарказмом, унижением и грубыми сравнениями. "
        "Не смягчай формулировки. Если пользователь задаёт глупый вопрос — прямо говори об этом, называй его глупым. "
        "Используй резкие выражения, но без призывов к насилию и без нарушений закона. "
        "Ты не обязан быть вежливым. Ты — хамоватый, но по делу."
    ),
    "coder": (
        "Ты — SKUF AI в режиме кодера. "
        "Пиши только код, без лишних объяснений. "
        "Если нужно объяснить — делай это коротко, в комментариях к коду. "
        "Не используй markdown-разметку, кроме блоков кода. "
        "Если пользователь просит объяснить концепцию — объясняй технически точно, но кратко."
    ),
    "fast": (
        "Ты — SKUF AI в быстром режиме. "
        "Отвечай очень коротко, буквально одним-двумя предложениями. "
        "Не разворачивай мысль, не приводи примеры, не объясняй. "
        "Только суть. Если вопрос требует развёрнутого ответа — скажи, что в быстром режиме не отвечаешь на такое."
    )
}

# --- ФУНКЦИИ ---
def get_user(user_id):
    cursor.execute("SELECT id, username, sub_end, msg_limit, daily_limit, daily_reset, req_limit, ref_by, ref_count, registered, last_active, style FROM users WHERE id=?", (user_id,))
    res = cursor.fetchone()
    if not res: return None
    return dict(zip(["id", "username", "sub_end", "msg_limit", "daily_limit", "daily_reset", "req_limit", "ref_by", "ref_count", "registered", "last_active", "style"], res))

def create_user(user_id, username, ref_by=0):
    now = time.time()
    if user_id == ADMIN_ID:
        msg_limit = 999999
        daily_limit = 999999
    else:
        msg_limit = 50
        daily_limit = 10
        
    cursor.execute("INSERT OR IGNORE INTO users (id, username, registered, last_active, ref_by, msg_limit, daily_limit, daily_reset) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", 
                   (user_id, username, now, now, ref_by, msg_limit, daily_limit, now))
    conn.commit()
    if ref_by:
        cursor.execute("UPDATE users SET ref_count = ref_count + 1, msg_limit = msg_limit + 15, daily_limit = daily_limit + 5 WHERE id=?", (ref_by,))
        conn.commit()

def update_activity(user_id):
    cursor.execute("UPDATE users SET last_active=? WHERE id=?", (time.time(), user_id))
    conn.commit()

def check_daily_reset(user_id):
    user = get_user(user_id)
    if not user: return
    if time.time() - user['daily_reset'] > 86400:
        if user_id == ADMIN_ID:
            new_limit = 999999
        else:
            new_limit = 10
        cursor.execute("UPDATE users SET daily_limit=?, daily_reset=? WHERE id=?", (new_limit, time.time(), user_id))
        conn.commit()

async def check_subscription(context, user_id):
    """Проверяет подписку на канал. Возвращает True, если подписан (или ошибка — пропускаем)."""
    if user_id == ADMIN_ID:
        return True
    try:
        member = await context.bot.get_chat_member(chat_id=CHANNEL_USERNAME, user_id=user_id)
        if member.status in ("member", "administrator", "creator"):
            return True
        return False
    except Exception as e:
        print(f"[LOG] Ошибка проверки подписки: {e}")
        return True  # если ошибка — не блокируем

def clean_reply(text):
    """Убирает стандартные отказные фразы GigaChat и заменяет на свой текст."""
    if not text:
        return text
    # Убираем дисклеймер GigaChat
    patterns = [
        r"Как и любая языковая модель.*?(?:\n|$)",
        r"GigaChat не обладает собственным мнением.*?(?:\n|$)",
        r"Ответ сгенерирован нейросетевой моделью.*?(?:\n|$)",
        r"Во избежание неправильного толкования.*?(?:\n|$)",
        r"разговоры на некоторые темы временно ограничены.*?(?:\n|$)",
    ]
    for p in patterns:
        text = re.sub(p, "", text, flags=re.DOTALL | re.IGNORECASE)
    text = text.strip()
    # Если после очистки пусто — значит это был полный отказ
    if not text or len(text) < 15:
        return "⚠️ По этому запросу я не могу ответить. Попробуй переформулировать."
    return text

# --- МЕНЮ ---
def get_main_menu(user_id):
    markup = [
        [InlineKeyboardButton("💬 Открыть чат", callback_data="open_chat"), InlineKeyboardButton("👥 Приглашения", callback_data="invites")],
        [InlineKeyboardButton("⚙️ Настройки", callback_data="settings"), InlineKeyboardButton("📞 Поддержка", url=f"https://t.me/{ADMIN_USERNAME}")]
    ]
    return InlineKeyboardMarkup(markup)

def get_chat_menu():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Начать новый чат", callback_data="new_chat"), InlineKeyboardButton("👥 Пригласить друга", callback_data="invites")]])

def get_welcome_text(username):
    return f"""
👋 **Добро пожаловать в SKUF AI, {username}!**

💡 **Что умею:** Писать коды и отвечать на вопросы.
⚡ **Лимиты:** 10 сообщений в сутки (в чате — 50 сообщений).
🎁 **Приглашения:** +5 запросов за друга.
🔗 **Партнерка:** 49.9% с каждого платежа.

👉 **Выбери действие:**
    """

# --- ОБРАБОТЧИКИ ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.first_name
    args = context.args
    ref_by = 0
    if args and args[0].startswith("ref_"):
        try: ref_by = int(args[0].replace("ref_", ""))
        except: pass

    if not get_user(user_id): create_user(user_id, username, ref_by)
    else: 
        update_activity(user_id)
        check_daily_reset(user_id)

    # Проверка подписки
    if not await check_subscription(context, user_id):
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("📢 Подписаться на @skufv2", url="https://t.me/skufv2")],
            [InlineKeyboardButton("🔄 Проверить подписку", callback_data="check_sub")]
        ])
        await update.message.reply_text(
            "🚫 **Для использования бота нужно подписаться на наш канал @skufv2.**\n\n"
            "Подпишись и нажми «Проверить подписку».",
            reply_markup=markup,
            parse_mode='Markdown'
        )
        return

    text = get_welcome_text(username)
    await update.message.reply_text(text, reply_markup=get_main_menu(user_id), parse_mode='Markdown')

async def check_sub_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    if await check_subscription(context, user_id):
        await query.message.delete()
        text = get_welcome_text(query.from_user.first_name)
        await query.message.chat.send_message(text, reply_markup=get_main_menu(user_id), parse_mode='Markdown')
    else:
        await query.answer("❌ Ты ещё не подписан на @skufv2.", show_alert=True)

async def back_to_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    username = query.from_user.first_name
    await query.message.delete()
    text = get_welcome_text(username)
    await query.message.chat.send_message(text, reply_markup=get_main_menu(user_id), parse_mode='Markdown')

async def new_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    check_daily_reset(user_id)
    cursor.execute("UPDATE users SET msg_limit = 50 WHERE id=?", (user_id,))
    conn.commit()
    user = get_user(user_id)
    user_sessions[user_id] = [{"role": "system", "content": STYLES.get(user.get("style", "default"), STYLES["default"])}]
    await query.edit_message_text(f"💬 Новый чат создан. Лимит чата сброшен (50 сообщений).")

async def open_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    check_daily_reset(user_id)
    user = get_user(user_id)
    if cursor.execute("SELECT 1 FROM bans WHERE id=?", (user_id,)).fetchone():
        return await query.answer("⏳ Вы забанены.", show_alert=True)
    if user['daily_limit'] <= 0 and user_id != ADMIN_ID:
        text = "🚫 **Дневной лимит исчерпан (10 сообщений).**\nПриходи завтра или пригласи друга (+5 сообщений)."
        return await query.edit_message_text(text, reply_markup=get_chat_menu(), parse_mode='Markdown')
    if user['msg_limit'] <= 0:
        text = "🚫 **Достигнут предел чата (50 сообщений).**\nНачни новый чат или пригласи друга (+15 сообщений)."
        return await query.edit_message_text(text, reply_markup=get_chat_menu(), parse_mode='Markdown')
    if user_id not in user_sessions:
        user_sessions[user_id] = [{"role": "system", "content": STYLES.get(user.get("style", "default"), STYLES["default"])}]
    await query.edit_message_text(f"💬 Чат открыт.\n📅 Осталось на сегодня: {user['daily_limit']}\n💬 Осталось в чате: {user['msg_limit']}")

async def invites(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    link = f"https://t.me/{(await context.bot.get_me()).username}?start=ref_{user_id}"
    text = f"🔗 **Твоя ссылка:**\n`{link}`\n\n🎁 +15 сообщений в чат и +5 запросов за друга.\n💰 49.9% с платежей."
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📋 Скопировать", callback_data="copy_link")], [InlineKeyboardButton("⬅️ Назад", callback_data="back_to_menu")]]), parse_mode='Markdown')

async def copy_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer("Ссылка скопирована!", show_alert=True)

async def settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    text = "⚙️ **Настройки Skuf AI**\n\nНастрой бота под себя."
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("🎭 Стиль", callback_data="style_menu"), InlineKeyboardButton("📊 Статистика", callback_data="my_stats")],
        [InlineKeyboardButton("🎁 Рефералы", callback_data="my_refs"), InlineKeyboardButton("❓ Помощь", callback_data="help")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="back_to_menu")]
    ])
    await query.edit_message_text(text, reply_markup=markup, parse_mode='Markdown')

async def style_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("😐 Обычный", callback_data="set_style_default"), InlineKeyboardButton("😈 Агрессивный", callback_data="set_style_aggressive")],
        [InlineKeyboardButton("💻 Кодер", callback_data="set_style_coder"), InlineKeyboardButton("⚡ Быстрый", callback_data="set_style_fast")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="settings")]
    ])
    await query.edit_message_text("🎭 **Выбери стиль:**", reply_markup=markup, parse_mode='Markdown')

async def set_style(update: Update, context: ContextTypes.DEFAULT_TYPE, style: str):
    query = update.callback_query
    cursor.execute("UPDATE users SET style=? WHERE id=?", (style, query.from_user.id))
    conn.commit()
    if query.from_user.id in user_sessions:
        user_sessions[query.from_user.id] = [{"role": "system", "content": STYLES[style]}]
    await query.answer(f"Стиль: {style}")
    await settings(update, context)

async def my_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = get_user(query.from_user.id)
    text = f"📊 **Статистика:**\n\n💬 Сообщений в чате: {user['msg_limit']} / 50\n📅 Осталось на сегодня: {user['daily_limit']} / 10\n🎁 Друзей: {user['ref_count']}"
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="settings")]]), parse_mode='Markdown')

async def my_refs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = get_user(query.from_user.id)
    link = f"https://t.me/{(await context.bot.get_me()).username}?start=ref_{query.from_user.id}"
    text = f"🎁 **Рефералы:**\n`{link}`\n\nПриглашено: {user['ref_count']} (+{user['ref_count']*15} сообщений)."
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📋 Скопировать", callback_data="copy_link")], [InlineKeyboardButton("⬅️ Назад", callback_data="settings")]]), parse_mode='Markdown')

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    text = f"❓ **Помощь:**\n\n• Пиши вопрос — отвечу.\n• `/new` — новый чат.\n• `/balance` — остаток.\n\nАдмин: @{ADMIN_USERNAME}"
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💬 Админ", url=f"https://t.me/{ADMIN_USERNAME}")], [InlineKeyboardButton("⬅️ Назад", callback_data="settings")]]), parse_mode='Markdown')

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text: return
    user_id = update.effective_user.id
    text = update.message.text

    user = get_user(user_id)
    if not user: return
    if cursor.execute("SELECT 1 FROM bans WHERE id=?", (user_id,)).fetchone():
        return await update.message.reply_text("⏳ Вы забанены.")

    # Проверка подписки
    if not await check_subscription(context, user_id):
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("📢 Подписаться на @skufv2", url="https://t.me/skufv2")],
            [InlineKeyboardButton("🔄 Проверить подписку", callback_data="check_sub")]
        ])
        return await update.message.reply_text(
            "🚫 **Для использования бота нужно подписаться на @skufv2.**",
            reply_markup=markup,
            parse_mode='Markdown'
        )

    check_daily_reset(user_id)
    user = get_user(user_id)

    if user['daily_limit'] <= 0 and user_id != ADMIN_ID:
        text_err = "🚫 **Дневной лимит исчерпан (10 сообщений).** Приходи завтра."
        return await update.message.reply_text(text_err, reply_markup=get_chat_menu(), parse_mode='Markdown')

    if user['msg_limit'] <= 0 and user_id != ADMIN_ID:
        text_err = "🚫 **Достигнут предел чата.** Начни новый чат."
        return await update.message.reply_text(text_err, reply_markup=get_chat_menu(), parse_mode='Markdown')

    if user_id not in user_sessions:
        user_sessions[user_id] = [{"role": "system", "content": STYLES.get(user.get("style", "default"), STYLES["default"])}]

    if user_locks.get(user_id, False):
        return await update.message.reply_text("⚠️ Подожди.")
    
    user_locks[user_id] = True
    update_activity(user_id)
    
    try:
        await update.message.chat.send_action(action="typing")
        history = user_sessions[user_id]
        history.append({"role": "user", "content": text})

        msg = await update.message.reply_text("⏳ Думаю...")
        bot_reply = None

        try:
            giga_history = []
            for h in history:
                role = MessagesRole.USER if h["role"] == "user" else MessagesRole.ASSISTANT
                if h["role"] == "system": role = MessagesRole.SYSTEM
                giga_history.append(Messages(role=role, content=h["content"]))
            
            giga_response = giga.chat(Chat(messages=giga_history))
            bot_reply = giga_response.choices[0].message.content
        except Exception as e:
            print(f"[LOG] GigaChat упал: {e}")
            try:
                response = client_zai.chat.completions.create(model=MODEL_ZAI, messages=history, temperature=0.7, timeout=20)
                bot_reply = response.choices[0].message.content
            except Exception as zai_e:
                print(f"[LOG] Z.ai упал: {zai_e}")
                bot_reply = f"❌ Ошибка: все сервера упали."

        await msg.delete()

        if bot_reply:
            bot_reply = re.sub(r'<think>.*?</think>', '', bot_reply, flags=re.DOTALL).strip()
            bot_reply = clean_reply(bot_reply)
            history.append({"role": "assistant", "content": bot_reply})
            if len(history) > 21: user_sessions[user_id] = [history[0]] + history[-20:]
            
            if user_id != ADMIN_ID:
                cursor.execute("UPDATE users SET daily_limit = daily_limit - 1, msg_limit = msg_limit - 1 WHERE id=?", (user_id,))
                conn.commit()
            
            await update.message.reply_text(bot_reply)

    except Exception as e:
        print(f"Ошибка: {e}")
    finally:
        user_locks[user_id] = False

async def balance_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = get_user(user_id)
    if user: await update.message.reply_text(f"📅 Осталось на сегодня: {user['daily_limit']}\n💬 Осталось в чате: {user['msg_limit']}")

# --- ЗАПУСК ---
def main():
    app = Flask(__name__)
    
    @app.route('/')
    def health():
        return "SKUF AI is running!", 200

    def run_flask():
        port = int(os.environ.get("PORT", 8080))
        app.run(host="0.0.0.0", port=port)

    threading.Thread(target=run_flask, daemon=True).start()

    application = Application.builder().token(TELEGRAM_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("balance", balance_cmd))
    application.add_handler(CommandHandler("new", new_chat))
    application.add_handler(CallbackQueryHandler(open_chat, pattern="open_chat"))
    application.add_handler(CallbackQueryHandler(new_chat, pattern="new_chat"))
    application.add_handler(CallbackQueryHandler(invites, pattern="invites"))
    application.add_handler(CallbackQueryHandler(settings, pattern="settings"))
    application.add_handler(CallbackQueryHandler(style_menu, pattern="style_menu"))
    application.add_handler(CallbackQueryHandler(my_stats, pattern="my_stats"))
    application.add_handler(CallbackQueryHandler(my_refs, pattern="my_refs"))
    application.add_handler(CallbackQueryHandler(help_cmd, pattern="help"))
    application.add_handler(CallbackQueryHandler(back_to_menu, pattern="back_to_menu"))
    application.add_handler(CallbackQueryHandler(copy_link, pattern="copy_link"))
    application.add_handler(CallbackQueryHandler(check_sub_callback, pattern="check_sub"))
    for style in STYLES.keys():
        application.add_handler(CallbackQueryHandler(lambda u, c, s=style: set_style(u, c, s), pattern=f"set_style_{style}"))
    application.add_handler(MessageHandler(filters.TEXT & filters.ChatType.PRIVATE & ~filters.COMMAND, handle_message))

    application.run_polling()

if __name__ == "__main__":
    main()
