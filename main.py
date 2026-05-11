import os
import asyncio
import sqlite3
import logging
from datetime import datetime
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiohttp import web

# Настройки
TOKEN = os.getenv("BOT_TOKEN")
# Render предоставляет порт через переменную окружения PORT
PORT = int(os.getenv("PORT", 10000))

logging.basicConfig(level=logging.INFO)
bot = Bot(token=TOKEN)
dp = Dispatcher()

# --- Блок базы данных ---
def init_db():
    conn = sqlite3.connect("filters.db")
    cur = conn.cursor()
    cur.execute('CREATE TABLE IF NOT EXISTS filters (id INTEGER PRIMARY KEY, name TEXT, interval_m INTEGER)')
    cur.execute('CREATE TABLE IF NOT EXISTS history (id INTEGER PRIMARY KEY, filter_id INTEGER, date TEXT)')
    filters = [
        (1, "Туалет: ХВС", 6), (2, "Туалет: ГВС", 6), 
        (3, "Осмос: 1-ПП", 4), (4, "Осмос: 2-Уголь", 6), 
        (5, "Осмос: 3-ПП", 6), (6, "Осмос: Мембрана", 24), 
        (7, "Осмос: Постфильтр", 12)
    ]
    cur.executemany("INSERT OR IGNORE INTO filters VALUES (?,?,?)", filters)
    conn.commit()
    conn.close()

# --- Обработчики команд ---
@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    init_db()
    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📊 Состояние")]], 
        resize_keyboard=True
    )
    await message.answer("Бот запущен на Render! Нажмите кнопку, чтобы проверить фильтры.", reply_markup=kb)

@dp.message(F.text == "📊 Состояние")
async def show_status(message: types.Message):
    conn = sqlite3.connect("filters.db")
    cur = conn.cursor()
    cur.execute("""
        SELECT f.name, f.interval_m, MAX(h.date) 
        FROM filters f 
        LEFT JOIN history h ON f.id = h.filter_id 
        GROUP BY f.id
    """)
    rows = cur.fetchall()
    text = "📋 **Статус фильтров:**\n\n"
    for name, interval, last_date in rows:
        if last_date:
            last = datetime.strptime(last_date, "%Y-%m-%d")
            days_passed = (datetime.now() - last).days
            days_left = (interval * 30) - days_passed
            text += f"🔹 {name}: еще {days_left} дн.\n"
        else:
            text += f"🔸 {name}: нет данных\n"
    conn.close()
    await message.answer(text, parse_mode="Markdown")

# --- Веб-сервер для Health Check (нужен для Render) ---
async def handle_health_check(request):
    return web.Response(text="Bot is alive")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

# --- Запуск ---
async def main():
    init_db()
    # Запускаем фоновый веб-сервер, чтобы Render не перезагружал контейнер
    asyncio.create_task(start_web_server())
    # Запускаем бота в режиме Polling
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())