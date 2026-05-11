import os, asyncio, json, logging, httpx
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove, InlineKeyboardButton, CallbackQuery
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

# --- ВАРИАНТЫ ДЛЯ КНОПОК ---
ROOM_SUGGESTIONS = ["Кухня", "Ванна", "Туалет", "Бойлерная"]
FILTER_SUGGESTIONS = ["Механическая очистка", "Угольный картридж", "Осмос (мембрана)", "Постфильтр", "Минерализатор"]
INTERVAL_SUGGESTIONS = ["3 месяца", "6 месяцев", "12 месяцев"]

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
            logging.error(f"Ошибка загрузки базы: {e}")
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

# --- ЛОГИКА ДОБАВЛЕНИЯ ---
@dp.message(Command("start"))
async def start(message: types.Message):
    await message.answer(
        "👋 **Привет! Я твой помощник по уходу за фильтрами.**\n\n"
        "Я помогу тебе не забыть, когда пора менять картриджи в доме, квартире или на даче.\n"
        "Воспользуйся кнопками ниже, чтобы начать!", 
        reply_markup=main_kb(), parse_mode="Markdown"
    )

@dp.message(F.text == "➕ Добавить фильтр")
async def add_start(message: types.Message, state: FSMContext):
    await state.set_state(FilterStates.add_address)
    await message.answer("🏠 **Шаг 1: Адрес.**\nВведите название объекта (например: Квартира, Дача или Дом):", reply_markup=ReplyKeyboardRemove())

@dp.message(FilterStates.add_address)
async def add_addr(message: types.Message, state: FSMContext):
    await state.update_data(address=message.text)
    await state.set_state(FilterStates.add_room)
    await message.answer("📍 **Шаг 2: Комната.**\nВыберите из списка или напишите свою:", reply_markup=make_row_kb(ROOM_SUGGESTIONS))

@dp.message(FilterStates.add_room)
async def add_r(message: types.Message, state: FSMContext):
    await state.update_data(room=message.text)
    await state.set_state(FilterStates.add_name)
    await message.answer("⚙️ **Шаг 3: Название фильтра.**\nВыберите тип или введите название вручную:", reply_markup=make_row_kb(FILTER_SUGGESTIONS))

@dp.message(FilterStates.add_name)
async def add_n(message: types.Message, state: FSMContext):
    await state.update_data(name=message.text)
    await state.set_state(FilterStates.add_interval)
    await message.answer("📅 **Шаг 4: Срок службы.**\nЧерез сколько месяцев его нужно менять? Выберите или введите число:", reply_markup=make_row_kb(INTERVAL_SUGGESTIONS))

@dp.message(FilterStates.add_interval)
async def add_i(message: types.Message, state: FSMContext):
    val = "".join(filter(str.isdigit, message.text))
    if not val: return await message.answer("Пожалуйста, введите число месяцев (например: 6)")
    await state.update_data(interval=int(val))
    await state.set_state(FilterStates.add_date)
    await message.answer("⏳ **Шаг 5: Последняя замена.**\nКогда вы меняли его последний раз?\nВведите дату в формате `ДД.ММ.ГГГГ` или нажмите кнопку 'Сегодня':", reply_markup=make_row_kb(["Сегодня"]))

@dp.message(FilterStates.add_date)
async def add_d(message: types.Message, state: FSMContext):
    txt = message.text.lower()
    c_date = datetime.now().strftime("%Y-%m-%d") if txt == "сегодня" else None
    if not c_date:
        try: c_date = datetime.strptime(txt, "%d.%m.%Y").strftime("%Y-%m-%d")
        except: return await message.answer("❌ Неверный формат. Нужно ДД.ММ.ГГГГ (например: 15.05.2024)")
    
    data = await state.get_data()
    uid = str(message.from_user.id)
    db = await load_db()
    if uid not in db: db[uid] = []
    db[uid].append({**data, "last_date": c_date})
    await save_db(db)
    await message.answer(f"✅ **Готово!**\nФильтр '{data['name']}' успешно добавлен.", reply_markup=main_kb(), parse_mode="Markdown")
    await state.clear()

# --- СТАТУС ---
@dp.message(F.text == "📊 Статус")
async def show_status(message: types.Message):
    db = await load_db()
    filters = db.get(str(message.from_user.id), [])
    if not filters: return await message.answer("📭 Ваш список фильтров пока пуст. Нажмите '➕ Добавить фильтр'.")
    
    filters.sort(key=lambda x: (x.get('address', ''), x.get('room', '')))
    res = "📋 **Ваши фильтры:**\n"
    c_addr, c_room = "", ""
    
    for i, f in enumerate(filters):
        if f['address'] != c_addr:
            res += f"\n🏰 **{f['address']}**"
            c_addr = f['address']
        if f['room'] != c_room:
            res += f"\n  📍 __{f['room']}__"
            c_room = f['room']
        
        last = datetime.strptime(f['last_date'], "%Y-%m-%d")
        next_d = last + timedelta(days=f['interval']*30)
        days = (next_d - datetime.now()).days
        icon = "🟢" if days > 15 else "🟡" if days > 0 else "🔴"
        res += f"\n    {icon} {f['name']}: {days} дн. (до {next_d.strftime('%d.%m.%Y')})"
    
    await message.answer(res, parse_mode="Markdown")

# --- УДАЛЕНИЕ ---
@dp.message(F.text == "🗑 Управление")
async def manage_menu(message: types.Message):
    db = await load_db()
    user_filters = db.get(str(message.from_user.id), [])
    if not user_filters: return await message.answer("Нечего удалять.")
    
    addresses = list(set(f['address'] for f in user_filters))
    kb = InlineKeyboardBuilder()
    for addr in addresses:
        kb.row(InlineKeyboardButton(text=f"🏠 {addr}", callback_data=f"del_addr:{addr}"))
    await message.answer("Выберите объект для удаления:", reply_markup=kb.as_markup())

@dp.callback_query(F.data.startswith("del_addr:"))
async def manage_addr(callback: CallbackQuery):
    addr = callback.data.split(":")[1]
    db = await load_db()
    user_filters = db.get(str(callback.from_user.id), [])
    rooms = list(set(f['room'] for f in user_filters if f['address'] == addr))
    
    kb = InlineKeyboardBuilder()
    for rm in rooms:
        kb.row(InlineKeyboardButton(text=f"📍 {rm}", callback_data=f"del_room:{addr}:{rm}"))
    kb.row(InlineKeyboardButton(text="❌ УДАЛИТЬ ВЕСЬ ОБЪЕКТ", callback_data=f"exec_del_addr:{addr}"))
    await callback.message.edit_text(f"Объект: {addr}\nВыберите комнату для удаления или весь объект целиком:", reply_markup=kb.as_markup())

@dp.callback_query(F.data.startswith("del_room:"))
async def manage_room(callback: CallbackQuery):
    _, addr, rm = callback.data.split(":")
    db = await load_db()
    user_filters = db.get(str(callback.from_user.id), [])
    
    kb = InlineKeyboardBuilder()
    for i, f in enumerate(user_filters):
        if f['address'] == addr and f['room'] == rm:
            kb.row(InlineKeyboardButton(text=f"🗑 {f['name']}", callback_data=f"exec_del_filt:{i}"))
    kb.row(InlineKeyboardButton(text="❌ УДАЛИТЬ ВСЮ КОМНАТУ", callback_data=f"exec_del_room:{addr}:{rm}"))
    await callback.message.edit_text(f"Комната: {rm} ({addr})\nНажмите на фильтр, чтобы удалить его:", reply_markup=kb.as_markup())

@dp.callback_query(F.data.startswith("exec_del_"))
async def finalize_delete(callback: CallbackQuery):
    parts = callback.data.split(":")
    mode = parts[0]
    uid = str(callback.from_user.id)
    db = await load_db()
    
    if mode == "exec_del_addr":
        db[uid] = [f for f in db[uid] if f['address'] != parts[1]]
    elif mode == "exec_del_room":
        db[uid] = [f for f in db[uid] if not (f['address'] == parts[1] and f['room'] == parts[2])]
    elif mode == "exec_del_filt":
        db[uid].pop(int(parts[1]))
        
    await save_db(db)
    await callback.answer("Удалено!")
    await callback.message.edit_text("✅ Изменения сохранены в базе.")

# --- СЕРВЕР ---
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
