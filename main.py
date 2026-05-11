import os, asyncio, json, logging, httpx
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove, InlineKeyboardButton, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
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
FILTER_SUGGESTIONS = ["Механика (ПП)", "Уголь (GAC/CBC)", "Осмос", "Постфильтр", "Минерализатор"]
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
            if r.status_code != 200:
                logging.error(f"Ошибка API GitHub: {r.status_code}")
                return {}
            content = r.json()['files']['filters_data.json']['content']
            return json.loads(content)
        except Exception as e:
            logging.error(f"Критическая ошибка загрузки: {e}")
            return {}

async def save_db(data):
    url = f"https://api.github.com/gists/{GIST_ID}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}"}
    payload = {"files": {"filters_data.json": {"content": json.dumps(data, indent=2)}}}
    async with httpx.AsyncClient() as client:
        await client.patch(url, headers=headers, json=payload)

# --- УТИЛИТЫ ДЛЯ КНОПОК ---
def main_kb():
    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text="📊 Статус"))
    builder.row(KeyboardButton(text="➕ Добавить фильтр"), KeyboardButton(text="🗑 Управление"))
    return builder.as_markup(resize_keyboard=True)

def make_suggest_kb(items: list):
    builder = ReplyKeyboardBuilder()
    for item in items:
        builder.add(KeyboardButton(text=item))
    builder.adjust(2) # Делаем по 2 кнопки в ряд
    return builder.as_markup(resize_keyboard=True)

# --- ЛОГИКА ДОБАВЛЕНИЯ ---
@dp.message(Command("start"))
async def start(message: types.Message):
    await message.answer(
        "👋 **Привет! Я твой домашний мастер по фильтрам.**\n\n"
        "Я буду напоминать, когда пора менять картриджи, чтобы вода всегда была чистой.\n"
        "Используй меню внизу для настройки.", 
        reply_markup=main_kb(), parse_mode="Markdown"
    )

@dp.message(F.text == "➕ Добавить фильтр")
async def add_start(message: types.Message, state: FSMContext):
    await state.set_state(FilterStates.add_address)
    await message.answer("🏠 **Шаг 1: Где находится?**\nВведите название (например: Квартира, Дача или Дом):", reply_markup=ReplyKeyboardRemove())

@dp.message(FilterStates.add_address)
async def add_addr(message: types.Message, state: FSMContext):
    await state.update_data(address=message.text)
    await state.set_state(FilterStates.add_room)
    await message.answer("📍 **Шаг 2: Комната.**\nВыберите вариант или введите свой:", reply_markup=make_suggest_kb(ROOM_SUGGESTIONS))

@dp.message(FilterStates.add_room)
async def add_r(message: types.Message, state: FSMContext):
    await state.update_data(room=message.text)
    await state.set_state(FilterStates.add_name)
    await message.answer("⚙️ **Шаг 3: Тип фильтра.**\nВыберите из списка или напишите название:", reply_markup=make_suggest_kb(FILTER_SUGGESTIONS))

@dp.message(FilterStates.add_name)
async def add_n(message: types.Message, state: FSMContext):
    await state.update_data(name=message.text)
    await state.set_state(FilterStates.add_interval)
    await message.answer("📅 **Шаг 4: Ресурс.**\nЧерез сколько месяцев замена?", reply_markup=make_suggest_kb(INTERVAL_SUGGESTIONS))

@dp.message(FilterStates.add_interval)
async def add_i(message: types.Message, state: FSMContext):
    val = "".join(filter(str.isdigit, message.text))
    if not val: return await message.answer("Введите число месяцев цифрами.")
    await state.update_data(interval=int(val))
    await state.set_state(FilterStates.add_date)
    await message.answer("⏳ **Шаг 5: Дата установки.**\nКогда меняли в прошлый раз? (ДД.ММ.ГГГГ или нажмите 'Сегодня')", reply_markup=make_suggest_kb(["Сегодня"]))

@dp.message(FilterStates.add_date)
async def add_d(message: types.Message, state: FSMContext):
    txt = message.text.lower()
    c_date = datetime.now().strftime("%Y-%m-%d") if txt == "сегодня" else None
    if not c_date:
        try: c_date = datetime.strptime(txt, "%d.%m.%Y").strftime("%Y-%m-%d")
        except: return await message.answer("❌ Формат: ДД.ММ.ГГГГ (например 01.01.2024)")
    
    data = await state.get_data()
    uid = str(message.from_user.id)
    db = await load_db()
    if uid not in db: db[uid] = []
    db[uid].append({**data, "last_date": c_date})
    await save_db(db)
    await message.answer(f"✅ **Успешно!**\nФильтр сохранен в вашу базу данных.", reply_markup=main_kb(), parse_mode="Markdown")
    await state.clear()

# --- СТАТУС ---
@dp.message(F.text == "📊 Статус")
async def show_status(message: types.Message):
    db = await load_db()
    uid = str(message.from_user.id)
    filters = db.get(uid, [])
    
    if not filters:
        return await message.answer("📭 Ваш список пока пуст. Добавьте первый фильтр!")

    # Сортировка с защитой от пустых полей
    filters.sort(key=lambda x: (str(x.get('address', '')), str(x.get('room', ''))))
    
    res = "📋 **Текущее состояние:**\n"
    c_addr, c_room = "", ""
    
    for f in filters:
        addr = f.get('address', 'Без адреса')
        room = f.get('room', 'Общее')
        
        if addr != c_addr:
            res += f"\n🏰 **{addr}**"
            c_addr = addr
        if room != c_room:
            res += f"\n  📍 __{room}__"
            c_room = room
        
        try:
            last = datetime.strptime(f['last_date'], "%Y-%m-%d")
            next_d = last + timedelta(days=f.get('interval', 6)*30)
            days = (next_d - datetime.now()).days
            icon = "🟢" if days > 15 else "🟡" if days > 0 else "🔴"
            res += f"\n    {icon} {f.get('name', 'Фильтр')}: {days} дн."
        except:
            res += f"\n    ⚪️ {f.get('name', 'Фильтр')}: Ошибка даты"
            
    await message.answer(res, parse_mode="Markdown")

# --- УДАЛЕНИЕ ---
@dp.message(F.text == "🗑 Управление")
async def manage_menu(message: types.Message):
    db = await load_db()
    user_filters = db.get(str(message.from_user.id), [])
    if not user_filters: return await message.answer("База данных пуста.")
    
    kb = InlineKeyboardBuilder()
    addresses = sorted(list(set(f.get('address', 'Без адреса') for f in user_filters)))
    for addr in addresses:
        kb.row(InlineKeyboardButton(text=f"🏠 {addr}", callback_data=f"view_addr:{addr}"))
    
    await message.answer("Выберите объект для управления:", reply_markup=kb.as_markup())

@dp.callback_query(F.data.startswith("view_addr:"))
async def view_addr(callback: CallbackQuery):
    addr = callback.data.split(":")[1]
    db = await load_db()
    filters = db.get(str(callback.from_user.id), [])
    rooms = sorted(list(set(f.get('room', 'Общее') for f in filters if f.get('address') == addr)))
    
    kb = InlineKeyboardBuilder()
    for rm in rooms:
        kb.row(InlineKeyboardButton(text=f"📍 {rm}", callback_data=f"view_room:{addr}:{rm}"))
    kb.row(InlineKeyboardButton(text="❌ УДАЛИТЬ ВЕСЬ ОБЪЕКТ", callback_data=f"kill_addr:{addr}"))
    kb.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="back_main"))
    
    await callback.message.edit_text(f"Объект: {addr}\nВыберите комнату:", reply_markup=kb.as_markup())

@dp.callback_query(F.data.startswith("view_room:"))
async def view_room(callback: CallbackQuery):
    _, addr, rm = callback.data.split(":")
    db = await load_db()
    user_filters = db.get(str(callback.from_user.id), [])
    
    kb = InlineKeyboardBuilder()
    for i, f in enumerate(user_filters):
        if f.get('address') == addr and f.get('room') == rm:
            kb.row(InlineKeyboardButton(text=f"🗑 {f.get('name')}", callback_data=f"kill_filt:{i}"))
    
    kb.row(InlineKeyboardButton(text="❌ УДАЛИТЬ ВСЮ КОМНАТУ", callback_data=f"kill_room:{addr}:{rm}"))
    kb.row(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"view_addr:{addr}"))
    await callback.message.edit_text(f"Комната: {rm}\nНажмите на фильтр для удаления:", reply_markup=kb.as_markup())

@dp.callback_query(F.data.startswith("kill_"))
async def kill_logic(callback: CallbackQuery):
    parts = callback.data.split(":")
    mode = parts[0]
    uid = str(callback.from_user.id)
    db = await load_db()
    
    if mode == "kill_addr":
        db[uid] = [f for f in db[uid] if f.get('address') != parts[1]]
    elif mode == "kill_room":
        db[uid] = [f for f in db[uid] if not (f.get('address') == parts[1] and f.get('room') == parts[2])]
    elif mode == "kill_filt":
        db[uid].pop(int(parts[1]))
        
    await save_db(db)
    await callback.answer("Удалено!")
    await callback.message.edit_text("✅ Изменения применены.")

@dp.callback_query(F.data == "back_main")
async def back_main(callback: CallbackQuery):
    await manage_menu(callback.message)

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
