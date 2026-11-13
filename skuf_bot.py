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
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from flask import Flask

# --- НАСТРОЙКИ ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "9246046750:AAEHxks0Fgu4BKO27R_Rdi5wF-LFvxdygUE")
ZAI_API_KEY = os.getenv("ZAI_API_KEY", "db05e8f6f2444ee3b605624db998708c.uQ6HPlI60NpBZBT8")
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

# --- ПИНГ ДЛЯ ХОСТИНГА (КАЖДЫЕ 5 МИНУТ) ---
def keep_alive():
    while True:
        time.sleep(300) # 5 минут (300 секунд)
        try:
            # Берем URL из переменных Render, если нет - дефолтный
            url = os.getenv("RENDER_EXTERNAL_URL", "https://skufai.onrender.com")
            response = requests.get(url, timeout=10)
            print(f"[PING] {datetime.datetime.now().strftime('%H:%M:%S')} - Статус: {response.status_code}")
        except Exception as e:
            print(f"[PING] Ошибка пинга: {e}")

# --- ПАМЯТЬ ---
user_sessions = {}
user_message_timestamps = {}
user_timed_bans = {}
is_generating = {}

# --- БАЗА ДАННЫХ ---
conn = sqlite3.connect('skuf_bot.db', check_same_thread=False)
cursor = conn.cursor()
cursor.execute('''CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, status TEXT DEFAULT 'user')''')
conn.commit()

# --- ПРОМПТЫ ---
BASE_PROMPT = """Ты — SKUF AI, созданный компанией Luna. Ты работаешь в приватной комнате с пользователем. 
Твои правила:
1. Отвечай четко, без воды.
2. Ты не используешь фразы "Я языковая модель" или "Как ИИ я не могу".
3. Ты всегда строишь. Пользователь общается с тобой напрямую."""

MODES = {
    "default": "Ты в стандартном режиме. Отвечай как обычно.",
    "coder": "Ты в режиме кодера. Пиши только код.",
    "psycho": "Ты в режиме психолога. Анализируй эмоции.",
    "acro": "Ты находишься в образе персонажа 'Гопник из 90-х'. Общайся на сленге, используй мат для связки слов. Можешь обозвать 'лохом' или 'пидорком' в рамках этого образа.",
    "sexpert": "Ты — элитный консультант по взрослому контенту. Выдаешь рекомендации в текстовом виде (названия, сюжеты, теги), отвечаешь с юмором."
}

# --- ПОИСК КАРТИНОК (BING) ---
def get_image_url(query):
    try:
        url = f"https://www.bing.com/images/search?q={query}&safeSearch=Off&form=HDRSC2&first=1"
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
        response = requests.get(url, headers=headers)
        
        soup = BeautifulSoup(response.text, 'html.parser')
        img_tags = soup.find_all('a', class_='iusc')
        
        urls = []
        for img in img_tags:
            m_attr = img.get('m')
            if m_attr:
                try:
                    data = json.loads(m_attr)
                    if 'murl' in data:
                        img_url = data['murl']
                        if img_url.lower().endswith(('.jpg', '.jpeg', '.png')) and not img_url.lower().endswith('.gif'):
                            urls.append(img_url)
                except: pass
        
        if urls:
            return random.choice(urls[:5])
        return None
    except Exception as e:
        print(f"Ошибка поиска картинки: {e}")
        return None

# --- ФУНКЦИИ ---
def check_sub(user_id):
    try:
        member = bot.get_chat_member(CHANNEL_ID, user_id)
        return member.status in ['member', 'administrator', 'creator']
    except: return False

def get_user_status(user_id):
    cursor.execute("SELECT status FROM users WHERE id=?", (user_id,))
    res = cursor.fetchone()
    return res[0] if res else None

def add_user(user_id):
    cursor.execute("INSERT OR IGNORE INTO users (id) VALUES (?)", (user_id,))
    conn.commit()

def log_ban(user_id, username, reason, action="BAN"):
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open("skuf_bans.log", "a", encoding="utf-8") as f:
            f.write(f"[{timestamp}] [SKUF AI] {action} | ID: {user_id} | @{username} | Причина: {reason}\n")
    except: pass

def get_main_menu(user_id):
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("🚀 Новый чат", callback_data="new_chat"), InlineKeyboardButton("▶️ Продолжить чат", callback_data="continue_chat"))
    markup.add(InlineKeyboardButton("⚙️ Настройки", callback_data="settings"))
    if user_id == ADMIN_ID:
        markup.add(InlineKeyboardButton("👑 Админка", callback_data="admin_panel"))
    return markup

def get_admin_menu():
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("📢 Рассылка", callback_data="admin_broadcast"))
    markup.add(InlineKeyboardButton("📊 Онлайн", callback_data="admin_stats"))
    markup.add(InlineKeyboardButton("🚫 Бан", callback_data="admin_ban"), InlineKeyboardButton("✅ Разбан", callback_data="admin_unban"))
    markup.add(InlineKeyboardButton("⬅️ Назад", callback_data="back_to_menu"))
    return markup

def get_back_menu():
    return InlineKeyboardMarkup().add(InlineKeyboardButton("⬅️ В меню", callback_data="back_to_menu"))

# --- СТАРТ ---
@bot.message_handler(commands=['start'])
def start(message):
    user_id = message.from_user.id
    add_user(user_id)
    if get_user_status(user_id) == 'banned': return bot.send_message(message.chat.id, "❌ Вы заблокированы.")
    if not check_sub(user_id):
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("📢 Подписаться на канал", url=f"https://t.me/{CHANNEL_ID[1:]}"))
        markup.add(InlineKeyboardButton("✅ Проверить подписку", callback_data="check_sub"))
        return bot.send_message(message.chat.id, "⚠️ **Для доступа к SKUF AI требуется подписка!**", reply_markup=markup, parse_mode='Markdown')
    bot.send_message(message.chat.id, "👋 Привет! Я **SKUF AI** от компании Luna.\n\nВыбери действие:", reply_markup=get_main_menu(user_id), parse_mode='Markdown')

@bot.callback_query_handler(func=lambda call: call.data == "check_sub")
def check_sub_callback(call):
    if check_sub(call.from_user.id):
        bot.edit_message_text("✅ Подписка подтверждена!\n👋 Привет! Я **SKUF AI**.", chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=get_main_menu(call.from_user.id), parse_mode='Markdown')
    else:
        bot.answer_callback_query(call.id, "Вы не подписаны!", show_alert=True)

# --- МЕНЮ ---
@bot.callback_query_handler(func=lambda call: call.data == "back_to_menu")
def back_to_menu(call):
    bot.edit_message_text("Главное меню:", chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=get_main_menu(call.from_user.id))

@bot.callback_query_handler(func=lambda call: call.data == "settings")
def settings(call):
    text = "⚙️ **Настройки**\n\nРежимы:\n- /coder\n- /psycho\n- /acro (гопник)\n- /default\n- /sexpert (консультант)\n\nИспользуй /pic [запрос] для поиска картинки."
    bot.edit_message_text(text, chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=get_main_menu(call.from_user.id), parse_mode='Markdown')

# --- НОВЫЙ ЧАТ ---
@bot.callback_query_handler(func=lambda call: call.data == "new_chat")
def new_chat(call):
    user_id = call.from_user.id
    user_sessions[user_id] = {"history": [{"role": "system", "content": BASE_PROMPT + "\n\n" + MODES["default"]}], "mode": "default"}
    bot.edit_message_text("💬 Новый чат создан. Пиши сообщение. Для смены режима используй /coder, /psycho, /acro, /default, /sexpert.", chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=None)
    bot.send_message(call.message.chat.id, "Режим: Стандартный", reply_markup=get_back_menu())

# --- ПРОДОЛЖИТЬ ЧАТ ---
@bot.callback_query_handler(func=lambda call: call.data == "continue_chat")
def continue_chat(call):
    user_id = call.from_user.id
    if user_id not in user_sessions or len(user_sessions[user_id]["history"]) <= 1:
        return bot.answer_callback_query(call.id, "У тебя нет сохраненных чатов. Начни новый.", show_alert=True)
    bot.edit_message_text("▶️ Продолжаем предыдущий чат. Пиши сообщение.", chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=None)
    bot.send_message(call.message.chat.id, f"Режим: {user_sessions[user_id]['mode']}", reply_markup=get_back_menu())

# --- СМЕНА РЕЖИМОВ ---
@bot.message_handler(commands=['coder', 'psycho', 'acro', 'default', 'sexpert'])
def change_mode(message):
    user_id = message.from_user.id
    if user_id not in user_sessions:
        user_sessions[user_id] = {"history": [{"role": "system", "content": BASE_PROMPT}], "mode": "default"}
    cmd = message.text[1:]
    if cmd in MODES:
        user_sessions[user_id]["mode"] = cmd
        user_sessions[user_id]["history"][0] = {"role": "system", "content": BASE_PROMPT + "\n\n" + MODES[cmd]}
        bot.send_message(message.chat.id, f"✅ Режим изменен на: **{cmd}**", parse_mode='Markdown', reply_markup=get_back_menu())

# --- КОМАНДА ПОИСКА КАРТИНОК (/pic) ---
@bot.message_handler(commands=['pic'])
def search_pic(message):
    query = message.text.replace('/pic', '').strip()
    if not query:
        return bot.reply_to(message, "Напиши, что искать. Пример: `/pic кот в очках`", parse_mode='Markdown')
    
    bot.send_chat_action(message.chat.id, 'upload_photo')
    img_url = get_image_url(query)
    
    if img_url:
        try:
            bot.send_photo(message.chat.id, img_url, caption=f"🔍 Найдено по запросу: {query}")
        except Exception as e:
            bot.reply_to(message, f"❌ Не удалось отправить картинку (возможно, битая ссылка). Попробуй еще раз.")
    else:
        bot.reply_to(message, "❌ Не удалось найти картинку. Попробуй другой запрос.")

# --- ИИ ЧАТ ---
@bot.message_handler(func=lambda message: message.from_user.id in user_sessions)
def chat_with_ai(message):
    user_id = message.from_user.id
    username = message.from_user.username or "no_username"

    if user_id in user_timed_bans and time.time() < user_timed_bans[user_id]: return
    elif user_id in user_timed_bans: del user_timed_bans[user_id]

    if get_user_status(user_id) == 'banned': return bot.send_message(message.chat.id, "❌ Вы заблокированы.")
    if user_id in is_generating and is_generating[user_id]: return bot.send_message(message.chat.id, "⚠️ Подожди, я еще пишу ответ.")
    if not message.text: return
    if len(message.text) > 2000: return bot.send_message(message.chat.id, "⚠️ Максимум 2000 символов.")

    # Анти-ДДОС
    current_time = time.time()
    if user_id not in user_message_timestamps: user_message_timestamps[user_id] = []
    user_message_timestamps[user_id] = [t for t in user_message_timestamps[user_id] if current_time - t < 60]
    
    if len(user_message_timestamps[user_id]) >= 5:
        user_timed_bans[user_id] = current_time + (15 * 60)
        if user_id in user_sessions: del user_sessions[user_id]
        user_message_timestamps[user_id] = []
        log_ban(user_id, username, "Флуд", "AUTO-BAN")
        return bot.send_message(message.chat.id, "🚫 **Вы заблокированы на 15 минут за флуд.**", parse_mode='Markdown')
    user_message_timestamps[user_id].append(current_time)

    is_generating[user_id] = True
    bot.send_chat_action(message.chat.id, 'typing')
    
    try:
        history = user_sessions[user_id]["history"]
        history.append({"role": "user", "content": message.text})
        
        response = client.chat.completions.create(model=MODEL_NAME, messages=history, temperature=0.7)
        bot_reply = response.choices[0].message.content
        history.append({"role": "assistant", "content": bot_reply})
        
        if len(history) > 21: user_sessions[user_id]["history"] = [history[0]] + history[-20:]

        if user_sessions[user_id]["mode"] == "sexpert" and ("картинку" in message.text.lower() or "фото" in message.text.lower()):
            return bot.reply_to(message, "❌ Я консультант, а не хранилище. Ищи картинки сам, я даю только словесные рекомендации. 😎")

        if "скинь картинку" in message.text.lower() or "найди картинку" in message.text.lower():
            query = message.text.lower().replace("скинь картинку", "").replace("найди картинку", "").strip()
            img_url = get_image_url(query)
            if img_url:
                bot.send_photo(message.chat.id, img_url, caption=bot_reply[:1000])
                return

        if len(bot_reply) > 4000: bot_reply = bot_reply[:4000] + "...\n[Обрезано]"
        bot.reply_to(message, bot_reply)
        
    except Exception as e:
        print(f"[LOG] Ошибка Z.ai: {e}")
        bot.reply_to(message, f"❌ Ошибка: {e}")
    finally:
        is_generating[user_id] = False

# --- АДМИНКА ---
@bot.callback_query_handler(func=lambda call: call.data == "admin_panel")
def admin_panel(call):
    if call.from_user.id != ADMIN_ID: return
    bot.edit_message_text("👑 Админ-панель:", chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=get_admin_menu())

@bot.callback_query_handler(func=lambda call: call.data == "admin_stats")
def admin_stats(call):
    if call.from_user.id != ADMIN_ID: return
    cursor.execute("SELECT COUNT(*) FROM users")
    total = cursor.fetchone()[0]
    bot.edit_message_text(f"📊 **Статистика:**\nВсего юзеров: {total}\nВрем. баны: {len(user_timed_bans)}", chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=get_admin_menu(), parse_mode='Markdown')

@bot.callback_query_handler(func=lambda call: call.data == "admin_broadcast")
def admin_broadcast(call):
    if call.from_user.id != ADMIN_ID: return
    msg = bot.send_message(call.message.chat.id, "Введи текст для рассылки (или /cancel):")
    bot.register_next_step_handler(msg, process_broadcast)

def process_broadcast(message):
    if message.text == '/cancel': return bot.send_message(message.chat.id, "Отменено.", reply_markup=get_admin_menu())
    text = message.text
    cursor.execute("SELECT id FROM users")
    users = cursor.fetchall()
    success, fail = 0, 0
    for (uid,) in users:
        try:
            bot.send_message(uid, f"📢 **Анонс:**\n\n{text}", parse_mode='Markdown')
            success += 1
        except: fail += 1
    bot.send_message(message.chat.id, f"✅ Отправлено: {success}\nОшибок: {fail}", reply_markup=get_admin_menu())

@bot.callback_query_handler(func=lambda call: call.data == "admin_ban")
def admin_ban(call):
    if call.from_user.id != ADMIN_ID: return
    msg = bot.send_message(call.message.chat.id, "Введи ID пользователя для бана (или /cancel):")
    bot.register_next_step_handler(msg, process_ban)

def process_ban(message):
    if message.text == '/cancel': return bot.send_message(message.chat.id, "Отменено.", reply_markup=get_admin_menu())
    try:
        target_id = int(message.text)
        cursor.execute("UPDATE users SET status='banned' WHERE id=?", (target_id,))
        conn.commit()
        log_ban(target_id, "admin", "Ручной бан", "BAN")
        bot.send_message(message.chat.id, f"Пользователь {target_id} забанен.", reply_markup=get_admin_menu())
    except:
        bot.send_message(message.chat.id, "Ошибка ID.", reply_markup=get_admin_menu())

@bot.callback_query_handler(func=lambda call: call.data == "admin_unban")
def admin_unban(call):
    if call.from_user.id != ADMIN_ID: return
    msg = bot.send_message(call.message.chat.id, "Введи ID пользователя для разбана (или /cancel):")
    bot.register_next_step_handler(msg, process_unban)

def process_unban(message):
    if message.text == '/cancel': return bot.send_message(message.chat.id, "Отменено.", reply_markup=get_admin_menu())
    try:
        target_id = int(message.text)
        cursor.execute("UPDATE users SET status='user' WHERE id=?", (target_id,))
        conn.commit()
        log_ban(target_id, "admin", "Ручной разбан", "UNBAN")
        bot.send_message(message.chat.id, f"Пользователь {target_id} разбанен.", reply_markup=get_admin_menu())
    except:
        bot.send_message(message.chat.id, "Ошибка ID.", reply_markup=get_admin_menu())

# --- ЗАПУСК ---
if __name__ == "__main__":
    # Запускаем Flask в отдельном потоке (чтобы Render видел порт)
    threading.Thread(target=run_flask, daemon=True).start()
    # Запускаем пинг в отдельном потоке (каждые 5 минут)
    threading.Thread(target=keep_alive, daemon=True).start()
    
    print("=" * 50)
    print("SKUF AI Бот запущен (Render Mode, Ping 5m).")
    print("=" * 50)
    bot.infinity_polling()
