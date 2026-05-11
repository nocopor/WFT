import os
import asyncio
import sqlite3
import logging
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiohttp import web

# --- НАСТРОЙКИ ---
TOKEN = os.getenv("BOT_TOKEN")
PORT = int(os.getenv("PORT", 10000))

logging.basicConfig(level=logging.INFO)
bot = Bot(token=TOKEN)
dp = Dispatcher()

# --- СОСТОЯНИЯ (для ввода даты вручную) ---
class SetupFilter(StatesGroup):
    waiting_for_date = State()

# --- БАЗА ДАННЫХ ---
DB_PATH = "filters.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    # Таблица фильтров
    cur.execute('''CREATE TABLE IF NOT EXISTS filters (
        id INTEGER PRIMARY KEY, 
        name TEXT, 
        model TEXT, 
        interval_m INTEGER,
        snooze_until TEXT
    )''')
    # Таблица истории замен
    cur.execute('CREATE TABLE IF NOT EXISTS history (id INTEGER PRIMARY KEY, filter_id INTEGER, date TEXT)')
    
    # Твои конкретные фильтры
    my_filters = [
        (1, "WC: Холодная", "МП-5В", 3, None),
        (2, "WC: Горячая", "МП-5ВГ", 3, None),
        (3, "Осмос: Префильтр 1", "МП-1В", 6, None),
        (4, "Осмос: Угольный", "Gac-10", 6, None),
        (5, "Осмос: Префильтр 2", "МП-5В", 6, None),
        (6, "Осмос: Мембрана", "TW-40-1812-75", 24, None),
        (7, "Осмос: Минерализатор", "GS-10cal", 12, None)
    ]
    cur.executemany("INSERT OR IGNORE INTO filters VALUES (?,?,?,?,?)", my_filters)
    conn.commit()
    conn.close()

# --- КЛАВИАТУРЫ ---
def main_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📊 Статус фильтров")],
            [KeyboardButton(text="⚙️ Настроить старые даты")]
        ], resize_keyboard=True
    )

def filter_action_kb(filter_id):
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="✅ Заменил сегодня", callback_data=f"replaced_{filter_id}"))
    builder.row(InlineKeyboardButton(text="📅 Ввести дату вручную", callback_data=f"setdate_{filter_id}"))
    builder.row(InlineKeyboardButton(text="⏳ Отложить", callback_data=f"snooze_menu_{filter_id}"))
    return builder.as_markup()

# --- ОБРАБОТЧИКИ ---
@dp.message(Command("start"))
async def start(message: types.Message):
    init_db()
    await message.answer(
        "🚰 **Система контроля фильтров готова!**\n\nЯ буду следить за ресурсом твоих картриджей в туалете и на кухне.",
        reply_markup=main_kb(),
        parse_mode="Markdown"
    )

@dp.message(F.text == "📊 Статус фильтров")
async def show_status(message: types.Message):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT f.id, f.name, f.model, f.interval_m, MAX(h.date) FROM filters f LEFT JOIN history h ON f.id = h.filter_id GROUP BY f.id")
    rows = cur.fetchall()
    
    if not rows:
        await message.answer("Фильтры не настроены.")
        return

    res = "📋 **Текущее состояние:**\n\n"
    for fid, name, model, interval, last_date in rows:
        if last_date:
            last = datetime.strptime(last_date, "%Y-%m-%d")
            next_date = last + timedelta(days=interval*30)
            days_left = (next_date - datetime.now()).days
            status = "🟢" if days_left > 15 else "🟡" if days_left > 0 else "🔴"
            res += f"{status} **{name}** ({model})\n└ Осталось: {days_left} дн. (до {next_date.strftime('%d.%m.%Y')})\n\n"
        else:
            res += f"⚪️ **{name}** ({model})\n└ ⚠️ Нет данных о замене\n\n"
    
    conn.close()
    await message.answer(res, parse_mode="Markdown", reply_markup=main_kb())

# Настройка дат вручную
@dp.message(F.text == "⚙️ Настроить старые даты")
async def settings_menu(message: types.Message):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT id, name FROM filters")
    filters = cur.fetchall()
    conn.close()
    
    builder = InlineKeyboardBuilder()
    for fid, name in filters:
        builder.row(InlineKeyboardButton(text=name, callback_data=f"setdate_{fid}"))
    
    await message.answer("Выберите фильтр, для которого хотите указать дату последней замены:", reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("setdate_"))
async def process_setdate(callback: types.CallbackQuery, state: FSMContext):
    filter_id = callback.data.split("_")[1]
    await state.update_data(fid=filter_id)
    await state.set_state(SetupFilter.waiting_for_date)
    await callback.message.answer("Введите дату в формате ДД.ММ.ГГГГ (например, 15.03.2024):")
    await callback.answer()

@dp.message(SetupFilter.waiting_for_date)
async def date_input(message: types.Message, state: FSMContext):
    try:
        data = await state.get_data()
        clean_date = datetime.strptime(message.text, "%d.%m.%Y").strftime("%Y-%m-%d")
        
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("INSERT INTO history (filter_id, date) VALUES (?, ?)", (data['fid'], clean_date))
        conn.commit()
        conn.close()
        
        await message.answer(f"✅ Дата сохранена!", reply_markup=main_kb())
        await state.clear()
    except ValueError:
        await message.answer("❌ Неверный формат. Нужно: ДД.ММ.ГГГГ")

@dp.callback_query(F.data.startswith("replaced_"))
async def quick_replace(callback: types.CallbackQuery):
    fid = callback.data.split("_")[1]
    today = datetime.now().strftime("%Y-%m-%d")
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("INSERT INTO history (filter_id, date) VALUES (?, ?)", (fid, today))
    conn.commit()
    conn.close()
    await callback.message.edit_text("✅ Замена зафиксирована!")
    await callback.answer()

# --- ФОНОВЫЕ ЗАДАЧИ И СЕРВЕР ---
async def handle_health_check(request):
    return web.Response(text="Bot is alive")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

async def main():
    init_db()
    asyncio.create_task(start_web_server())
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
