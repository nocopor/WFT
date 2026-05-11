import os, asyncio, json, logging, httpx
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from aiohttp import web

# --- НАСТРОЙКИ ---
TOKEN = os.getenv("BOT_TOKEN")
GIST_ID = os.getenv("GIST_ID")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
PORT = int(os.getenv("PORT", 10000))

logging.basicConfig(level=logging.INFO)
bot = Bot(token=TOKEN)
dp = Dispatcher()

# --- СПИСКИ ПОДСКАЗОК ---
ROOMS = ["Кухня", "Ванна", "Туалет", "Котельная"]
FILTER_TYPES = [
    "Полипропилен (механика)", 
    "Угольный картридж (GAC/CBC)", 
    "Осмос (Мембрана)", 
    "Постфильтр", 
    "Минерализатор",
    "Магистральный фильтр"
]
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
def make_row_keyboard(items: list):
    row = [KeyboardButton(text=item) for item in items]
    return ReplyKeyboardMarkup(keyboard=[row], resize_keyboard=True)

def main_kb():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="📊 Статус")], [KeyboardButton(text="➕ Добавить фильтр")]
    ], resize_keyboard=True)

# --- ЛОГИКА ДОБАВЛЕНИЯ ---
@dp.message(Command("start"))
async def start(message: types.Message):
    await message.answer("🏠 Бот-трекер фильтров готов к работе!", reply_markup=main_kb())

@dp.message(F.text == "➕ Добавить фильтр")
async def add_f(message: types.Message, state: FSMContext):
    await state.set_state(FilterStates.add_address)
    await message.answer("Введите название дома/адрес (например: Квартира или Дача):", reply_markup=ReplyKeyboardRemove())

@dp.message(FilterStates.add_address)
async def add_addr(message: types.Message, state: FSMContext):
    await state.update_data(address=message.text)
    await state.set_state(FilterStates.add_room)
    await message.answer("Выберите или введите комнату:", reply_markup=make_row_keyboard(ROOMS))

@dp.message(FilterStates.add_room)
async def add_room(message: types.Message, state: FSMContext):
    await state.update_data(room=message.text)
    await state.set_state(FilterStates.add_name)
    await message.answer("Выберите или введите тип фильтра:", reply_markup=make_row_keyboard(FILTER_TYPES))

@dp.message(FilterStates.add_name)
async def add_name(message: types.Message, state: FSMContext):
    await state.update_data(name=message.text)
    await state.set_state(FilterStates.add_interval)
    await message.answer("Срок службы:", reply_markup=make_row_keyboard(INTERVALS))

@dp.message(FilterStates.add_interval)
async def add_interval(message: types.Message, state: FSMContext):
    val = message.text.split()[0] # Берем только число из "6 месяцев"
    if not val.isdigit(): return await message.answer("Введите число месяцев.")
    await state.update_data(interval=int(val))
    await state.set_state(FilterStates.add_date)
    await message.answer("Дата последней замены? (ДД.ММ.ГГГГ или 'сегодня'):", reply_markup=make_row_keyboard(["Сегодня"]))

@dp.message(FilterStates.add_date)
async def add_date(message: types.Message, state: FSMContext):
    text = message.text.lower()
    clean_date = datetime.now().strftime("%Y-%m-%d") if text == "сегодня" else None
    if not clean_date:
        try: clean_date = datetime.strptime(text, "%d.%m.%Y").strftime("%Y-%m-%d")
        except: return await message.answer("Формат: ДД.ММ.ГГГГ")

    data = await state.get_data()
    uid = str(message.from_user.id)
    db = await load_db()
    
    if uid not in db: db[uid] = []
    db[uid].append({
        "address": data['address'],
        "room": data['room'],
        "name": data['name'],
        "interval": data['interval'],
        "last_date": clean_date
    })
    
    await save_db(db)
    await message.answer(f"✅ Сохранено: {data['address']} -> {data['room']} -> {data['name']}", reply_markup=main_kb())
    await state.clear()

# --- СТАТУС ---
@dp.message(F.text == "📊 Статус")
async def show(message: types.Message):
    db = await load_db()
    filters = db.get(str(message.from_user.id), [])
    if not filters: return await message.answer("Список пуст.")
    
    # Сортируем для красоты
    filters.sort(key=lambda x: (x.get('address', ''), x.get('room', '')))
    
    res = "📋 **Ваши фильтры:**\n"
    current_addr = ""
    current_room = ""
    
    for f in filters:
        addr = f.get('address', 'Без адреса')
        room = f.get('room', 'Общее')
        
        if addr != current_addr:
            res += f"\n🏰 **{addr}**"
            current_addr = addr
        if room != current_room:
            res += f"\n 📍 __{room}__"
            current_room = room
            
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
