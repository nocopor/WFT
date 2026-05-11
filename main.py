import os, asyncio, json, logging, httpx
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove, InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiohttp import web

# --- НАСТРОЙКИ ---
TOKEN = os.getenv("BOT_TOKEN")
GIST_ID = os.getenv("GIST_ID")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
PORT = int(os.getenv("PORT", 10000))

logging.basicConfig(level=logging.INFO)
bot = Bot(token=TOKEN)
dp = Dispatcher()

# --- ПОДСКАЗКИ ---
ROOMS = ["Кухня", "Ванна", "Туалет", "Котельная"]
FILTER_TYPES = ["Полипропилен", "Угольный", "Осмос", "Постфильтр", "Минерализатор"]
INTERVALS = ["3 месяца", "6 месяцев", "12 месяцев"]

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

# --- КЛАВИАТУРЫ ---
def main_kb():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="📊 Статус")], 
        [KeyboardButton(text="➕ Добавить фильтр"), KeyboardButton(text="🗑 Управление")]
    ], resize_keyboard=True)

def make_row_kb(items: list):
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=i) for i in items]], resize_keyboard=True)

# --- ДОБАВЛЕНИЕ ---
@dp.message(Command("start"))
async def start(message: types.Message):
    await message.answer("🏠 Бот-трекер запущен!", reply_markup=main_kb())

@dp.message(F.text == "➕ Добавить фильтр")
async def add_f(message: types.Message, state: FSMContext):
    await state.set_state(FilterStates.add_address)
    await message.answer("Назовите объект (Дом/Квартира):", reply_markup=ReplyKeyboardRemove())

@dp.message(FilterStates.add_address)
async def add_addr(message: types.Message, state: FSMContext):
    await state.update_data(address=message.text)
    await state.set_state(FilterStates.add_room)
    await message.answer("Выберите комнату:", reply_markup=make_row_kb(ROOMS))

@dp.message(FilterStates.add_room)
async def add_r(message: types.Message, state: FSMContext):
    await state.update_data(room=message.text)
    await state.set_state(FilterStates.add_name)
    await message.answer("Тип фильтра:", reply_markup=make_row_kb(FILTER_TYPES))

@dp.message(FilterStates.add_name)
async def add_n(message: types.Message, state: FSMContext):
    await state.update_data(name=message.text)
    await state.set_state(FilterStates.add_interval)
    await message.answer("Срок службы:", reply_markup=make_row_kb(INTERVALS))

@dp.message(FilterStates.add_interval)
async def add_i(message: types.Message, state: FSMContext):
    val = "".join(filter(str.isdigit, message.text))
    await state.update_data(interval=int(val))
    await state.set_state(FilterStates.add_date)
    await message.answer("Дата замены (ДД.ММ.ГГГГ или сегодня):", reply_markup=make_row_kb(["Сегодня"]))

@dp.message(FilterStates.add_date)
async def add_d(message: types.Message, state: FSMContext):
    txt = message.text.lower()
    c_date = datetime.now().strftime("%Y-%m-%d") if txt == "сегодня" else None
    if not c_date:
        try: c_date = datetime.strptime(txt, "%d.%m.%Y").strftime("%Y-%m-%d")
        except: return await message.answer("Ошибка формата!")
    
    data = await state.get_data()
    uid = str(message.from_user.id)
    db = await load_db()
    if uid not in db: db[uid] = []
    db[uid].append({**data, "last_date": c_date})
    await save_db(db)
    await message.answer("✅ Сохранено!", reply_markup=main_kb())
    await state.clear()

# --- УПРАВЛЕНИЕ / УДАЛЕНИЕ ---
@dp.message(F.text == "🗑 Управление")
async def manage_menu(message: types.Message):
    db = await load_db()
    user_filters = db.get(str(message.from_user.id), [])
    if not user_filters: return await message.answer("Нечего удалять.")
    
    addresses = list(set(f['address'] for f in user_filters))
    kb = InlineKeyboardBuilder()
    for addr in addresses:
        kb.row(InlineKeyboardButton(text=f"🏠 {addr}", callback_data=f"del_addr:{addr}"))
    
    await message.answer("Выберите объект для управления:", reply_markup=kb.as_markup())

@dp.callback_query(F.data.startswith("del_addr:"))
async def manage_addr(callback: CallbackQuery):
    addr = callback.data.split(":")[1]
    db = await load_db()
    user_filters = db.get(str(callback.from_user.id), [])
    rooms = list(set(f['room'] for f in user_filters if f['address'] == addr))
    
    kb = InlineKeyboardBuilder()
    for rm in rooms:
        kb.row(InlineKeyboardButton(text=f"📍 {rm}", callback_data=f"del_room:{addr}:{rm}"))
    kb.row(InlineKeyboardButton(text="❌ УДАЛИТЬ ВЕСЬ ДОМ", callback_data=f"confirm_del_addr:{addr}"))
    kb.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_manage"))
    
    await callback.message.edit_text(f"Объект: {addr}\nВыберите комнату или удалите всё:", reply_markup=kb.as_markup())

@dp.callback_query(F.data.startswith("del_room:"))
async def manage_room(callback: CallbackQuery):
    _, addr, rm = callback.data.split(":")
    db = await load_db()
    user_filters = db.get(str(callback.from_user.id), [])
    filters = [f for f in user_filters if f['address'] == addr and f['room'] == rm]
    
    kb = InlineKeyboardBuilder()
    for i, f in enumerate(user_filters):
        if f['address'] == addr and f['room'] == rm:
            kb.row(InlineKeyboardButton(text=f"🗑 {f['name']}", callback_data=f"confirm_del_filt:{i}"))
    
    kb.row(InlineKeyboardButton(text="❌ УДАЛИТЬ ВСЮ КОМНАТУ", callback_data=f"confirm_del_room:{addr}:{rm}"))
    kb.row(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"del_addr:{addr}"))
    
    await callback.message.edit_text(f"Комната: {rm} ({addr})\nВыберите фильтр для удаления:", reply_markup=kb.as_markup())

@dp.callback_query(F.data.startswith("confirm_del_"))
async def execute_delete(callback: CallbackQuery):
    parts = callback.data.split(":")
    mode = parts[0]
    uid = str(callback.from_user.id)
    db = await load_db()
    
    if mode == "confirm_del_addr":
        db[uid] = [f for f in db[uid] if f['address'] != parts[1]]
    elif mode == "confirm_del_room":
        db[uid] = [f for f in db[uid] if not (f['address'] == parts[1] and f['room'] == parts[2])]
    elif mode == "confirm_del_filt":
        db[uid].pop(int(parts[1]))
        
    await save_db(db)
    await callback.answer("Удалено!")
    await callback.message.edit_text("✅ Изменения сохранены в облаке.")

@dp.callback_query(F.data == "back_to_manage")
async def back_to_manage(callback: CallbackQuery):
    await manage_menu(callback.message)

# --- СТАТУС ---
@dp.message(F.text == "📊 Статус")
async def show_status(message: types.Message):
    db = await load_db()
    filters = db.get(str(message.from_user.id), [])
    if not filters: return await message.answer("Список пуст.")
    
    filters.sort(key=lambda x: (x.get('address', ''), x.get('room', '')))
    res = "📋 **Статус фильтров:**\n"
    c_addr, c_room = "", ""
    for f in filters:
        if f['address'] != c_addr:
            res += f"\n🏰 **{f['address']}**"
            c_addr = f['address']
        if f['room'] != c_room:
            res += f"\n 📍 __{f['room']}__"
            c_room = f['room']
        
        last = datetime.strptime(f['last_date'], "%Y-%m-%d")
        next_d = last + timedelta(days=f['interval']*30)
        days = (next_d - datetime.now()).days
        icon = "🟢" if days > 15 else "🟡" if days > 0 else "🔴"
        res += f"\n  {icon} {f['name']}: {days} дн."
    await message.answer(res, parse_mode="Markdown")

async def handle_hc(request): return web.Response(text="OK")

async def main():
    app = web.Application()
    app.router.add_get("/", handle_hc)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
