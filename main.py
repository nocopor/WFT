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

# --- СПИСКИ КОНКРЕТНЫХ МОДЕЛЕЙ И ПОДСКАЗОК ---
ROOM_SUGGESTIONS = ["Кухня", "Ванна", "Туалет", "Бойлерная"]

# Популярные модели картриджей
FILTER_SUGGESTIONS = [
    "МП-5В (механика)", "GAC-10 (уголь)", "CBC-10 (кокос)", 
    "Аквафор В510-02", "Гейзер ПФ", "Мембрана 50GPD",
    "Барьер Профи", "Минерализатор RO", "Постфильтр T33"
]

INTERVAL_SUGGESTIONS = ["3 месяца", "6 месяцев", "12 месяцев"]

class FilterStates(StatesGroup):
    add_address = State()
    add_room = State()
    add_name = State()
    add_interval = State()
    add_date = State()

# --- РАБОТА С GIST ---
async def load_db():
    url = f"https://api.github.com/gists/{GIST_ID}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}"}
    async with httpx.AsyncClient() as client:
        try:
            r = await client.get(url, headers=headers)
            if r.status_code != 200: return {}
            content = r.json()['files']['filters_data.json']['content']
            return json.loads(content)
        except: return {}

async def save_db(data):
    url = f"https://api.github.com/gists/{GIST_ID}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}"}
    payload = {"files": {"filters_data.json": {"content": json.dumps(data, indent=2)}}}
    async with httpx.AsyncClient() as client:
        await client.patch(url, headers=headers, json=payload)

# --- КЛАВИАТУРЫ ---
def main_kb():
    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text="📊 Статус"))
    builder.row(KeyboardButton(text="➕ Добавить фильтр"), KeyboardButton(text="🗑 Управление"))
    return builder.as_markup(resize_keyboard=True, input_field_placeholder="Выберите действие...")

def make_suggest_kb(items: list, placeholder: str):
    builder = ReplyKeyboardBuilder()
    for item in items:
        builder.add(KeyboardButton(text=item))
    builder.adjust(2)
    return builder.as_markup(
        resize_keyboard=True, 
        one_time_keyboard=True, 
        input_field_placeholder=placeholder
    )

# --- ЛОГИКА ---
@dp.message(Command("start"))
async def start(message: types.Message):
    await message.answer("🛠 **Бот-контролер фильтров**\nНастройте уведомления и следите за чистой водой.", reply_markup=main_kb(), parse_mode="Markdown")

@dp.message(F.text == "➕ Добавить фильтр")
async def add_start(message: types.Message, state: FSMContext):
    await state.set_state(FilterStates.add_address)
    await message.answer("🏠 **Где находится фильтр?**\nНапишите: Квартира, Дача или Дом:", reply_markup=ReplyKeyboardRemove())

@dp.message(FilterStates.add_address)
async def add_addr(message: types.Message, state: FSMContext):
    await state.update_data(address=message.text)
    await state.set_state(FilterStates.add_room)
    await message.answer("📍 **В какой комнате?**", reply_markup=make_suggest_kb(ROOM_SUGGESTIONS, "Выберите комнату..."))

@dp.message(FilterStates.add_room)
async def add_r(message: types.Message, state: FSMContext):
    await state.update_data(room=message.text)
    await state.set_state(FilterStates.add_name)
    await message.answer("⚙️ **Модель картриджа:**", reply_markup=make_suggest_kb(FILTER_SUGGESTIONS, "Выберите модель..."))

@dp.message(FilterStates.add_name)
async def add_n(message: types.Message, state: FSMContext):
    await state.update_data(name=message.text)
    await state.set_state(FilterStates.add_interval)
    await message.answer("📅 **Ресурс (мес):**", reply_markup=make_suggest_kb(INTERVAL_SUGGESTIONS, "Срок службы..."))

@dp.message(FilterStates.add_interval)
async def add_i(message: types.Message, state: FSMContext):
    val = "".join(filter(str.isdigit, message.text))
    if not val: return await message.answer("Введите число.")
    await state.update_data(interval=int(val))
    await state.set_state(FilterStates.add_date)
    await message.answer("⏳ **Дата замены:**", reply_markup=make_suggest_kb(["Сегодня"], "Когда меняли?"))

@dp.message(FilterStates.add_date)
async def add_d(message: types.Message, state: FSMContext):
    txt = message.text.lower()
    c_date = datetime.now().strftime("%Y-%m-%d") if txt == "сегодня" else None
    if not c_date:
        try: c_date = datetime.strptime(txt, "%d.%m.%Y").strftime("%Y-%m-%d")
        except: return await message.answer("Формат: ДД.ММ.ГГГГ")
    
    data = await state.get_data()
    uid = str(message.from_user.id)
    db = await load_db()
    if uid not in db: db[uid] = []
    db[uid].append({**data, "last_date": c_date})
    await save_db(db)
    await message.answer("✅ Сохранено!", reply_markup=main_kb())
    await state.clear()

# --- СТАТУС ---
@dp.message(F.text == "📊 Статус")
async def show_status(message: types.Message):
    db = await load_db()
    filters = db.get(str(message.from_user.id), [])
    if not filters: return await message.answer("Список пуст.")
    
    filters.sort(key=lambda x: (str(x.get('address', '[Без адреса]')), str(x.get('room', '[Общее]'))))
    res = "📋 **Статус:**\n"
    c_addr, c_room = "", ""
    for f in filters:
        addr = f.get('address', '[Без адреса]')
        room = f.get('room', '[Общее]')
        if addr != c_addr: res += f"\n🏰 **{addr}**"; c_addr = addr
        if room != c_room: res += f"\n  📍 __{room}__"; c_room = room
        
        last = datetime.strptime(f['last_date'], "%Y-%m-%d")
        next_d = last + timedelta(days=f.get('interval', 6)*30)
        days = (next_d - datetime.now()).days
        icon = "🟢" if days > 15 else "🟡" if days > 0 else "🔴"
        res += f"\n    {icon} {f.get('name')}: {days} дн."
    await message.answer(res, parse_mode="Markdown")

# --- УПРАВЛЕНИЕ ---
@dp.message(F.text == "🗑 Управление")
async def manage(message: types.Message):
    db = await load_db()
    filters = db.get(str(message.from_user.id), [])
    if not filters: return await message.answer("Пусто.")
    
    kb = InlineKeyboardBuilder()
    # Собираем все адреса, включая пустые
    addresses = sorted(list(set(f.get('address', '[Без адреса]') for f in filters)))
    for addr in addresses:
        kb.row(InlineKeyboardButton(text=f"🏠 {addr}", callback_data=f"v_addr:{addr}"))
    await message.answer("Выберите объект:", reply_markup=kb.as_markup())

@dp.callback_query(F.data.startswith("v_addr:"))
async def v_addr(callback: CallbackQuery):
    addr = callback.data.split(":")[1]
    db = await load_db()
    filters = db.get(str(callback.from_user.id), [])
    # Фильтруем комнаты в этом адресе
    rooms = sorted(list(set(f.get('room', '[Общее]') for f in filters if f.get('address', '[Без адреса]') == addr)))
    
    kb = InlineKeyboardBuilder()
    for rm in rooms:
        kb.row(InlineKeyboardButton(text=f"📍 {rm}", callback_data=f"v_room:{addr}:{rm}"))
    kb.row(InlineKeyboardButton(text="❌ УДАЛИТЬ ВСЁ ТУТ", callback_data=f"k_addr:{addr}"))
    kb.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="b_m"))
    await callback.message.edit_text(f"Объект: {addr}", reply_markup=kb.as_markup())

@dp.callback_query(F.data.startswith("v_room:"))
async def v_room(callback: CallbackQuery):
    _, addr, rm = callback.data.split(":")
    db = await load_db()
    all_f = db.get(str(callback.from_user.id), [])
    
    kb = InlineKeyboardBuilder()
    for i, f in enumerate(all_f):
        # Проверяем соответствие адреса и комнаты (с учетом пустых полей)
        f_addr = f.get('address', '[Без адреса]')
        f_room = f.get('room', '[Общее]')
        if f_addr == addr and f_room == rm:
            kb.row(InlineKeyboardButton(text=f"🗑 {f.get('name')}", callback_data=f"k_f:{i}"))
    
    kb.row(InlineKeyboardButton(text="❌ УДАЛИТЬ КОМНАТУ", callback_data=f"k_r:{addr}:{rm}"))
    kb.row(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"v_addr:{addr}"))
    await callback.message.edit_text(f"Комната: {rm}", reply_markup=kb.as_markup())

@dp.callback_query(F.data.startswith("k_"))
async def k_logic(callback: CallbackQuery):
    parts = callback.data.split(":")
    uid = str(callback.from_user.id)
    db = await load_db()
    
    if parts[0] == "k_addr":
        db[uid] = [f for f in db[uid] if f.get('address', '[Без адреса]') != parts[1]]
    elif parts[0] == "k_r":
        db[uid] = [f for f in db[uid] if not (f.get('address', '[Без адреса]') == parts[1] and f.get('room', '[Общее]') == parts[2])]
    elif parts[0] == "k_f":
        db[uid].pop(int(parts[1]))
        
    await save_db(db)
    await callback.answer("Удалено!")
    await callback.message.edit_text("✅ Обновлено.")

@dp.callback_query(F.data == "b_m")
async def b_m(callback: CallbackQuery): await manage(callback.message)

async def handle_hc(request): return web.Response(text="OK")
async def main():
    app = web.Application()
    app.router.add_get("/", handle_hc)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__": asyncio.run(main())
