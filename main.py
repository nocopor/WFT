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

# --- КОНФИГУРАЦИЯ ---
TOKEN = os.getenv("BOT_TOKEN")
PORT = int(os.getenv("PORT", 10000))

logging.basicConfig(level=logging.INFO)
bot = Bot(token=TOKEN)
dp = Dispatcher()

class SetupFilter(StatesGroup):
    waiting_for_date = State()

DB_PATH = "filters.db"

# --- ИНИЦИАЛИЗАЦИЯ БАЗЫ ---
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('''CREATE TABLE IF NOT EXISTS filters (
        id INTEGER PRIMARY KEY, 
        name TEXT, 
        model TEXT, 
        interval_m INTEGER,
        snooze_until TEXT
    )''')
    cur.execute('CREATE TABLE IF NOT EXISTS history (id INTEGER PRIMARY KEY, filter_id INTEGER, date TEXT)')
    
    # Твои точные данные
    my_filters = [
        (1, "WC: Холодная", "МП-5В", 3, None),
        (2, "WC: Горячая", "МП-5ВГ", 3, None),
        (3, "Осмос: 1-Префильтр", "МП-1В", 6, None),
        (4, "Осмос: 2-Угольный", "Gac-10", 6, None),
        (5, "Осмос: 3-Префильтр", "МП-5В", 6, None),
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
            [KeyboardButton(text="⚙️ Внести старую замену")]
        ], resize_keyboard=True
    )

def filter_action_kb(fid):
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="✅ Заменил сегодня", callback_data=f"rep_{fid}"))
    builder.row(InlineKeyboardButton(text="⏳ Отложить", callback_data=f"sn_menu_{fid}"))
    return builder.as_markup()

# --- ЛОГИКА СРОКОВ ---
def get_status_text():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT f.id, f.name, f.model, f.interval_m, f.snooze_until, MAX(h.date) FROM filters f LEFT JOIN history h ON f.id = h.filter_id GROUP BY f.id")
    rows = cur.fetchall()
    conn.close()
    
    res = "🚰 **СОСТОЯНИЕ ВАШЕЙ СИСТЕМЫ**\n" + "—" * 15 + "\n\n"
    now = datetime.now()

    for fid, name, model, interval, snooze, last_date in rows:
        if last_date:
            last = datetime.strptime(last_date, "%Y-%m-%d")
            base_next = last + timedelta(days=interval * 30)
            
            # Проверка на "отложенность"
            target_date = base_next
            if snooze:
                snooze_dt = datetime.strptime(snooze, "%Y-%m-%d")
                if snooze_dt > base_next:
                    target_date = snooze_dt

            days_left = (target_date - now).days
            
            if days_left > 15: icon = "🟢"
            elif 0 <= days_left <= 15: icon = "🟡"
            else: icon = "🔴"

            res += f"{icon} **{name}**\n`[{model}]`\n└ До: {target_date.strftime('%d.%m.%Y')} ({days_left} дн.)\n\n"
        else:
            res += f"⚪️ **{name}**\n`[{model}]`\n└ ⚠️ Нет данных! Нажмите кнопку ниже.\n\n"
    return res

# --- ХЭНДЛЕРЫ ---
@dp.message(Command("start"))
async def start(message: types.Message):
    init_db()
    await message.answer("🔧 **Бот-диспетчер фильтров запущен.**\n\nИспользуйте кнопки для контроля замен.", reply_markup=main_kb(), parse_mode="Markdown")

@dp.message(F.text == "📊 Статус фильтров")
async def cmd_status(message: types.Message):
    await message.answer(get_status_text(), parse_mode="Markdown")

@dp.message(F.text == "⚙️ Внести старую замену")
async def cmd_old_date(message: types.Message):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT id, name FROM filters")
    filters = cur.fetchall()
    conn.close()
    builder = InlineKeyboardBuilder()
    for fid, name in filters:
        builder.row(InlineKeyboardButton(text=name, callback_data=f"setdate_{fid}"))
    await message.answer("Для какого фильтра указать дату?", reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("setdate_"))
async def process_sd(callback: types.CallbackQuery, state: FSMContext):
    await state.update_data(fid=callback.data.split("_")[1])
    await state.set_state(SetupFilter.waiting_for_date)
    await callback.message.answer("Напиши дату замены в формате ДД.ММ.ГГГГ (например: 10.01.2024)")
    await callback.answer()

@dp.message(SetupFilter.waiting_for_date)
async def process_date_input(message: types.Message, state: FSMContext):
    try:
        data = await state.get_data()
        dt = datetime.strptime(message.text, "%d.%m.%Y").strftime("%Y-%m-%d")
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("INSERT INTO history (filter_id, date) VALUES (?, ?)", (data['fid'], dt))
        cur.execute("UPDATE filters SET snooze_until = NULL WHERE id = ?", (data['fid'],))
        conn.commit()
        conn.close()
        await message.answer("✅ Дата успешно сохранена!", reply_markup=main_kb())
        await state.clear()
    except:
        await message.answer("❌ Ошибка в дате. Нужно ДД.ММ.ГГГГ")

@dp.callback_query(F.data.startswith("sn_menu_"))
async def snooze_menu(callback: types.CallbackQuery):
    fid = callback.data.split("_")[3]
    builder = InlineKeyboardBuilder()
    for d in [1, 3, 7]:
        builder.add(InlineKeyboardButton(text=f"+{d} дн.", callback_data=f"sn_apply_{fid}_{d}"))
    await callback.message.edit_text("На сколько отложить напоминание?", reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("sn_apply_"))
async def snooze_apply(callback: types.CallbackQuery):
    _, _, fid, days = callback.data.split("_")
    new_date = (datetime.now() + timedelta(days=int(days))).strftime("%Y-%m-%d")
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("UPDATE filters SET snooze_until = ? WHERE id = ?", (new_date, fid))
    conn.commit()
    conn.close()
    await callback.message.edit_text(f"⏳ Отложено до {new_date}")
    await callback.answer()

# --- СЕРВЕР ---
async def handle_hc(request): return web.Response(text="OK")

async def main():
    init_db()
    app = web.Application()
    app.router.add_get("/", handle_hc)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
