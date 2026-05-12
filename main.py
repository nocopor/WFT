import os, asyncio, json, logging, httpx, urllib.parse
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import KeyboardButton, InlineKeyboardButton, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from aiohttp import web
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# --- НАСТРОЙКИ ---
TOKEN = os.getenv("BOT_TOKEN")
GIST_ID = os.getenv("GIST_ID")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
PORT = int(os.getenv("PORT", 10000))

logging.basicConfig(level=logging.INFO)
bot = Bot(token=TOKEN)
dp = Dispatcher()
scheduler = AsyncIOScheduler()
db_lock = asyncio.Lock()

# --- КАТАЛОГ СИСТЕМ ---
SYSTEM_PRESETS = {
    "💧 Аквафор Осмо / DWM": [
        {"name": "К5 (Грязь)", "interval": 6}, {"name": "К2 (Хлор)", "interval": 6},
        {"name": "КО-50 (Мембрана)", "interval": 24}, {"name": "К7 (Финиш)", "interval": 12}
    ],
    "💧 Гейзер Престиж / Аллегро": [
        {"name": "ПП 5мкм (Грязь)", "interval": 6}, {"name": "СВС 10 (Уголь)", "interval": 6},
        {"name": "СВС 10 (Уголь 2)", "interval": 6}, {"name": "Vontron (Мембрана)", "interval": 24}, {"name": "Постфильтр", "interval": 12}
    ],
    "➕ Одиночный фильтр (ручной ввод)": []
}

class SystemStates(StatesGroup):
    choose_system = State()
    custom_name = State()
    custom_interval = State()
    add_address = State()
    add_room = State()
    confirm_date = State()

# --- УТИЛИТЫ ---
async def load_db():
    url = f"https://api.github.com/gists/{GIST_ID}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}"}
    async with httpx.AsyncClient() as client:
        try:
            r = await client.get(url, headers=headers)
            if r.status_code != 200: return {}
            return json.loads(r.json()['files']['filters_data.json']['content'])
        except: return {}

async def save_db(data):
    async with db_lock:
        url = f"https://api.github.com/gists/{GIST_ID}"
        headers = {"Authorization": f"token {GITHUB_TOKEN}"}
        payload = {"files": {"filters_data.json": {"content": json.dumps(data, indent=2)}}}
        async with httpx.AsyncClient() as client:
            await client.patch(url, headers=headers, json=payload)

def get_ozon_link(name):
    query = urllib.parse.quote(f"картридж {name}")
    return f"https://www.ozon.ru/search/?text={query}"

# --- КЛАВИАТУРЫ ---
def main_kb():
    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text="📊 Статус"), KeyboardButton(text="🛒 Купить"))
    builder.row(KeyboardButton(text="➕ Настроить"), KeyboardButton(text="🗑 Удалить"))
    return builder.as_markup(resize_keyboard=True)

def make_reply_kb(items: list, placeholder: str, show_back=True):
    builder = ReplyKeyboardBuilder()
    for item in items: builder.add(KeyboardButton(text=item))
    builder.adjust(1)
    row = [KeyboardButton(text="❌ Отмена")]
    if show_back: row.insert(0, KeyboardButton(text="⬅️ Назад"))
    builder.row(*row)
    return builder.as_markup(resize_keyboard=True, input_field_placeholder=placeholder)

# --- ГЛАВНЫЕ КОМАНДЫ ---
@dp.message(Command("start"))
async def start(message: types.Message):
    await message.answer("🛠 **WaterFilterTracker**\nВсе ваши фильтры под контролем.", reply_markup=main_kb())

# --- КОМПАКТНЫЙ СТАТУС ---
@dp.message(F.text == "📊 Статус")
async def show_status(message: types.Message):
    db = await load_db()
    filters = db.get(str(message.from_user.id), [])
    if not filters: return await message.answer("Список пуст.")
    
    res = "📋 **СОСТОЯНИЕ ФИЛЬТРОВ:**\n"
    current_addr = ""
    for f in sorted(filters, key=lambda x: x['address']):
        if f['address'] != current_addr:
            current_addr = f['address']
            res += f"\n🏠 **{current_addr}**\n"
        
        next_d = datetime.strptime(f['last_date'], "%Y-%m-%d") + timedelta(days=f['interval']*30)
        days = (next_d - datetime.now()).days
        icon = "🟢" if days > 15 else "🟡" if days > 0 else "🔴"
        res += f"{icon} {f['name']}: {days} дн.\n"
    
    await message.answer(res, parse_mode="Markdown")

# --- ЛОГИКА ПОКУПКИ (НОВОЕ) ---
@dp.message(F.text == "🛒 Купить")
async def buy_menu(message: types.Message):
    db = await load_db()
    filters = db.get(str(message.from_user.id), [])
    if not filters: return await message.answer("Сначала добавьте фильтр.")
    
    kb = InlineKeyboardBuilder()
    addrs = sorted(list(set(f['address'] for f in filters)))
    for a in addrs:
        kb.row(InlineKeyboardButton(text=f"🏠 {a}", callback_data=f"buy_addr:{a}"))
    
    await message.answer("Выберите объект для покупки картриджей:", reply_markup=kb.as_markup())

@dp.callback_query(F.data.startswith("buy_addr:"))
async def buy_select_filter(cb: CallbackQuery):
    addr = cb.data.split(":")[1]
    db = await load_db()
    filters = [f for f in db.get(str(cb.from_user.id), []) if f['address'] == addr]
    
    kb = InlineKeyboardBuilder()
    for f in filters:
        kb.row(InlineKeyboardButton(text=f"🛒 {f['name']}", url=get_ozon_link(f['name'])))
    
    await cb.message.edit_text(f"Ссылки на Ozon для объекта **{addr}**:", reply_markup=kb.as_markup(), parse_mode="Markdown")

# --- ДОБАВЛЕНИЕ И УДАЛЕНИЕ (БЕЗ ИЗМЕНЕНИЙ В ЛОГИКЕ, ТОЛЬКО КНОПКИ) ---
@dp.message(F.text == "➕ Настроить")
async def add_start(message: types.Message, state: FSMContext):
    await state.set_state(SystemStates.choose_system)
    await message.answer("Выберите систему:", reply_markup=make_reply_kb(list(SYSTEM_PRESETS.keys()), "Система...", show_back=False))

@dp.message(F.text == "⬅️ Назад")
async def go_back(message: types.Message, state: FSMContext):
    curr = await state.get_state()
    if curr == SystemStates.add_address: await add_start(message, state)
    elif curr == SystemStates.add_room:
        await state.set_state(SystemStates.add_address)
        await message.answer("🏠 Где стоит?", reply_markup=make_reply_kb(["Дом", "Квартира"], "Объект..."))
    # ... и так далее для остальных шагов

@dp.message(F.text == "🗑 Удалить")
async def del_menu(message: types.Message):
    db = await load_db(); filters = db.get(str(message.from_user.id), [])
    if not filters: return await message.answer("Пусто.")
    kb = InlineKeyboardBuilder()
    for a in sorted(list(set(f['address'] for f in filters))):
        kb.row(InlineKeyboardButton(text=f"❌ {a}", callback_data=f"del_obj:{a}"))
    await message.answer("Удалить объект целиком:", reply_markup=kb.as_markup())

@dp.callback_query(F.data.startswith("del_obj:"))
async def del_obj_cb(cb: CallbackQuery):
    addr = cb.data.split(":")[1]; uid = str(cb.from_user.id); db = await load_db()
    db[uid] = [f for f in db[uid] if f['address'] != addr]
    await save_db(db); await cb.message.edit_text(f"✅ {addr} удален.")

# (Остальные обработчики add_2..add_5 остаются как в прошлом коде)
# ...

async def handle_hc(request): return web.Response(text="OK")
async def main():
    scheduler.start()
    app = web.Application(); app.router.add_get("/", handle_hc)
    runner = web.AppRunner(app); await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__": asyncio.run(main())
