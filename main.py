import os, asyncio, json, logging, httpx
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove, InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
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

class FilterStates(StatesGroup):
    add_address = State()
    add_room = State()
    add_name = State()
    add_interval = State()
    add_date = State()

# --- РАБОТА С GITHUB GIST ---
async def load_db():
    url = f"https://api.github.com/gists/{GIST_ID}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}"}
    async with httpx.AsyncClient() as client:
        try:
            r = await client.get(url, headers=headers)
            content = r.json()['files']['filters_data.json']['content']
            return json.loads(content)
        except Exception as e:
            logging.error(f"Ошибка Gist: {e}")
            return {}

async def save_db(data):
    url = f"https://api.github.com/gists/{GIST_ID}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}"}
    payload = {"files": {"filters_data.json": {"content": json.dumps(data, indent=2)}}}
    async with httpx.AsyncClient() as client:
        await client.patch(url, headers=headers, json=payload)

# --- УВЕДОМЛЕНИЯ (Проверка раз в сутки) ---
async def check_notifications():
    db = await load_db()
    today = datetime.now()
    for uid, filters in db.items():
        for f in filters:
            last = datetime.strptime(f['last_date'], "%Y-%m-%d")
            next_d = last + timedelta(days=f['interval']*30)
            diff = (next_d - today).days
            
            # Уведомляем за 3 дня и в день замены
            if diff == 3 or diff == 0:
                msg = f"🔔 **Напоминание!**\nЧерез {diff} дн. пора менять фильтр:\n🏠 {f['address']} -> {f['room']}\n🛠 {f['name']}"
                try: await bot.send_message(uid, msg, parse_mode="Markdown")
                except: pass

# --- КЛАВИАТУРЫ ---
def main_kb():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="📊 Статус")], 
        [KeyboardButton(text="➕ Добавить фильтр"), KeyboardButton(text="⚙️ Управление")]
    ], resize_keyboard=True)

# --- ЛОГИКА ---
@dp.message(Command("start"))
async def start(message: types.Message):
    await message.answer("🏠 Бот-контролер фильтров готов!", reply_markup=main_kb())

@dp.message(F.text == "➕ Добавить фильтр")
async def add_start(message: types.Message, state: FSMContext):
    await state.set_state(FilterStates.add_address)
    await message.answer("Назовите объект (Дом/Квартира):", reply_markup=ReplyKeyboardRemove())

# (Тут стандартные шаги добавления из прошлого кода...)
@dp.message(FilterStates.add_address)
async def add_addr(message: types.Message, state: FSMContext):
    await state.update_data(address=message.text)
    await state.set_state(FilterStates.add_room)
    await message.answer("Комната:", reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Кухня"), KeyboardButton(text="Ванна")]], resize_keyboard=True))

@dp.message(FilterStates.add_room)
async def add_r(message: types.Message, state: FSMContext):
    await state.update_data(room=message.text)
    await state.set_state(FilterStates.add_name)
    await message.answer("Тип фильтра:")

@dp.message(FilterStates.add_name)
async def add_n(message: types.Message, state: FSMContext):
    await state.update_data(name=message.text)
    await state.set_state(FilterStates.add_interval)
    await message.answer("Срок службы (мес):")

@dp.message(FilterStates.add_interval)
async def add_i(message: types.Message, state: FSMContext):
    await state.update_data(interval=int("".join(filter(str.isdigit, message.text))))
    await state.set_state(FilterStates.add_date)
    await message.answer("Дата замены (ДД.ММ.ГГГГ или сегодня):")

@dp.message(FilterStates.add_date)
async def add_d(message: types.Message, state: FSMContext):
    txt = message.text.lower()
    c_date = datetime.now().strftime("%Y-%m-%d") if txt == "сегодня" else None
    if not c_date:
        try: c_date = datetime.strptime(txt, "%d.%m.%Y").strftime("%Y-%m-%d")
        except: return await message.answer("Формат ДД.ММ.ГГГГ!")
    
    data = await state.get_data()
    uid = str(message.from_user.id)
    db = await load_db()
    if uid not in db: db[uid] = []
    db[uid].append({**data, "last_date": c_date})
    await save_db(db)
    await message.answer("✅ Сохранено!", reply_markup=main_kb())
    await state.clear()

# --- СТАТУС С КНОПКОЙ «ОБНОВИТЬ» ---
@dp.message(F.text == "📊 Статус")
async def show_status(message: types.Message):
    db = await load_db()
    filters = db.get(str(message.from_user.id), [])
    if not filters: return await message.answer("Пусто.")
    
    for i, f in enumerate(filters):
        last = datetime.strptime(f['last_date'], "%Y-%m-%d")
        next_d = last + timedelta(days=f['interval']*30)
        days = (next_d - datetime.now()).days
        icon = "🟢" if days > 15 else "🟡" if days > 0 else "🔴"
        
        kb = InlineKeyboardBuilder()
        kb.row(InlineKeyboardButton(text="🔄 Я заменил сегодня", callback_data=f"renew:{i}"))
        
        text = f"{icon} **{f['name']}** ({f['address']} / {f['room']})\n└ До замене: {days} дн. ({next_d.strftime('%d.%m.%Y')})"
        await message.answer(text, reply_markup=kb.as_markup(), parse_mode="Markdown")

@dp.callback_query(F.data.startswith("renew:"))
async def renew_filter(callback: CallbackQuery):
    idx = int(callback.data.split(":")[1])
    uid = str(callback.from_user.id)
    db = await load_db()
    
    db[uid][idx]['last_date'] = datetime.now().strftime("%Y-%m-%d")
    await save_db(db)
    await callback.answer("✅ Дата обновлена!")
    await callback.message.edit_text(f"✅ Фильтр {db[uid][idx]['name']} обновлен!\nСледующая замена через {db[uid][idx]['interval']} мес.")

# (Тут можно оставить логику удаления из прошлого сообщения...)
@dp.message(F.text == "⚙️ Управление")
async def manage(message: types.Message):
    await message.answer("Используйте меню Статус для обновления или удалите через Gist (пока в разработке).")

async def handle_hc(request): return web.Response(text="OK")

async def main():
    scheduler.add_job(check_notifications, "interval", hours=24)
    scheduler.start()
    
    app = web.Application()
    app.router.add_get("/", handle_hc)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
