import os, asyncio, json, logging, httpx, urllib.parse
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import (
    KeyboardButton, InlineKeyboardButton, CallbackQuery
)
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
dp = Dispatcher() # По умолчанию использует MemoryStorage (состояния для каждого пользователя свои)
scheduler = AsyncIOScheduler()

# Блокировка для предотвращения конфликтов при записи в Gist
db_lock = asyncio.Lock()

# --- КАТАЛОГ ---
FILTER_DB = {
    "🛡️ Механика (ХВС)": {
        "мп-5в (5 мкм)": 3, "мп-1в (1 мкм)": 3, "ПП-5 (стандарт 10SL)": 6
    },
    "🔥 Горячая вода (ГВС)": {
        "мп-5вг (термостойкий)": 3, "Гейзер БА (горячая)": 6, "Аквафор Гросс": 6
    },
    "🖤 Уголь / Сорбция": {
        "gac-10 (гранулы)": 6, "cbc-10 (карбон-блок)": 6, "Аквафор В510-02": 6
    },
    "🧬 Мембраны (Осмос)": {
        "tw40-1812-75 (75 GPD)": 24, "Vontron ULP1812-75": 24, "Filmtec BW60-1812-75": 30
    },
    "💎 Постфильтры / Минерал": {
        "gs-10cal (минерализатор)": 12, "Постфильтр T33": 12
    }
}

class FilterStates(StatesGroup):
    add_address = State()
    add_room = State()
    add_category = State()
    add_name = State()
    add_interval = State()
    add_date = State()

# --- РАБОТА С GIST (С защитой от перезаписи) ---
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
    # Используем Lock, чтобы только один процесс писал в файл в конкретный момент времени
    async with db_lock:
        url = f"https://api.github.com/gists/{GIST_ID}"
        headers = {"Authorization": f"token {GITHUB_TOKEN}"}
        payload = {"files": {"filters_data.json": {"content": json.dumps(data, indent=2)}}}
        async with httpx.AsyncClient() as client:
            await client.patch(url, headers=headers, json=payload)

# --- УТИЛИТЫ ---
def get_buy_links(filter_name):
    query = urllib.parse.quote(f"картридж {filter_name}")
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="🔹 Ozon", url=f"https://www.ozon.ru/search/?text={query}"))
    kb.row(InlineKeyboardButton(text="💜 WB", url=f"https://www.wildberries.ru/catalog/0/search.aspx?search={query}"))
    return kb

def main_kb():
    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text="📊 Статус"))
    builder.row(KeyboardButton(text="➕ Добавить фильтр"), KeyboardButton(text="🗑 Управление"))
    return builder.as_markup(resize_keyboard=True)

def make_reply_kb(items: list, placeholder: str):
    builder = ReplyKeyboardBuilder()
    for item in items: builder.add(KeyboardButton(text=item))
    builder.adjust(2)
    builder.row(KeyboardButton(text="❌ Отмена"))
    return builder.as_markup(resize_keyboard=True, input_field_placeholder=placeholder)

# --- ОБРАБОТЧИКИ ---
@dp.message(Command("start"))
async def start(message: types.Message):
    await message.answer("🚰 **WaterFilterTracker**\nЯ помогу не забыть о замене картриджей.", reply_markup=main_kb())

@dp.message(F.text == "❌ Отмена")
@dp.message(Command("cancel"))
async def cmd_cancel(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("Действие отменено.", reply_markup=main_kb())

@dp.message(StateFilter(FilterStates), F.text.in_(["📊 Статус", "➕ Добавить фильтр", "🗑 Управление"]))
async def smart_switch(message: types.Message, state: FSMContext):
    await state.clear()
    if message.text == "📊 Статус": await show_status(message)
    elif message.text == "➕ Добавить фильтр": await add_1(message, state)
    elif message.text == "🗑 Управление": await manage_menu(message)

# (Здесь идут функции добавления add_1...add_7 из прошлого кода, 
# они используют message.from_user.id и работают корректно для разных людей)
# ... [Логика добавления остается прежней] ...

@dp.message(F.text == "➕ Добавить фильтр")
async def add_1(message: types.Message, state: FSMContext):
    await state.set_state(FilterStates.add_address)
    await message.answer("🏠 **Где меняем?**", reply_markup=make_reply_kb(["Дом", "Квартира", "Дача"], "Объект..."))

@dp.message(FilterStates.add_address)
async def add_2(message: types.Message, state: FSMContext):
    await state.update_data(address=message.text)
    await state.set_state(FilterStates.add_room)
    await message.answer("📍 **Комната:**", reply_markup=make_reply_kb(["Кухня", "Санузел"], "Комната..."))

@dp.message(FilterStates.add_room)
async def add_3(message: types.Message, state: FSMContext):
    await state.update_data(room=message.text)
    await state.set_state(FilterStates.add_category)
    await message.answer("🔍 **Тип картриджа:**", reply_markup=make_reply_kb(list(FILTER_DB.keys()), "Тип..."))

@dp.message(FilterStates.add_category)
async def add_4(message: types.Message, state: FSMContext):
    cat = message.text
    if cat not in FILTER_DB: return await message.answer("Выберите из списка!")
    await state.update_data(category=cat)
    await state.set_state(FilterStates.add_name)
    models = list(FILTER_DB[cat].keys())
    await message.answer(f"⚙️ **Модель ({cat}):**", reply_markup=make_reply_kb(models, "Модель..."))

@dp.message(FilterStates.add_name)
async def add_5(message: types.Message, state: FSMContext):
    data = await state.get_data()
    name = message.text
    await state.update_data(name=name)
    rec = FILTER_DB.get(data['category'], {}).get(name, 6)
    await state.set_state(FilterStates.add_interval)
    await message.answer(f"📅 **Рекомендую: {rec} мес.**", reply_markup=make_reply_kb([f"{rec} мес.", "6 мес."], "Срок..."))

@dp.message(FilterStates.add_interval)
async def add_6(message: types.Message, state: FSMContext):
    val = "".join(filter(str.isdigit, message.text))
    await state.update_data(interval=int(val))
    await state.set_state(FilterStates.add_date)
    await message.answer("⏳ **Когда была замена?**", reply_markup=make_reply_kb(["Сегодня"], "ДД.ММ.ГГГГ"))

@dp.message(FilterStates.add_date)
async def add_7(message: types.Message, state: FSMContext):
    txt = message.text.lower()
    c_date = datetime.now().strftime("%Y-%m-%d") if txt == "сегодня" else None
    if not c_date:
        try: c_date = datetime.strptime(txt, "%d.%m.%Y").strftime("%Y-%m-%d")
        except: return await message.answer("Ошибка формата!")
    
    # ЧИТАЕМ, ОБНОВЛЯЕМ, СОХРАНЯЕМ
    db = await load_db(); uid = str(message.from_user.id)
    if uid not in db: db[uid] = []
    data = await state.get_data()
    db[uid].append({
        "address": data['address'], "room": data['room'], "name": data['name'],
        "interval": data['interval'], "last_date": c_date
    })
    await save_db(db)
    await state.clear()
    await message.answer("✅ Сохранено!", reply_markup=main_kb())

# --- УВЕДОМЛЕНИЯ ДЛЯ ВСЕХ ---
async def check_reminders():
    db = await load_db(); today = datetime.now()
    for uid, filters in db.items(): # Перебираем каждого пользователя в базе
        for i, f in enumerate(filters):
            last = datetime.strptime(f['last_date'], "%Y-%m-%d")
            next_d = last + timedelta(days=f['interval'] * 30)
            if today >= next_d:
                kb = get_buy_links(f['name'])
                kb.row(InlineKeyboardButton(text="✅ Заменил", callback_data=f"done:{i}"))
                text = f"🔔 **ПОРА ЗАМЕНЫ!**\n📍 {f['address']} -> {f['room']}\n🛠 {f['name']}"
                try: await bot.send_message(uid, text, reply_markup=kb.as_markup())
                except: pass # Если пользователь заблокировал бота

@dp.message(F.text == "📊 Статус")
async def show_status(message: types.Message):
    db = await load_db(); filters = db.get(str(message.from_user.id), [])
    if not filters: return await message.answer("У вас пока нет фильтров.")
    for f in filters:
        last = datetime.strptime(f['last_date'], "%Y-%m-%d")
        next_d = last + timedelta(days=f['interval']*30)
        days = (next_d - datetime.now()).days
        icon = "🟢" if days > 15 else "🟡" if days > 0 else "🔴"
        kb = get_buy_links(f['name'])
        await message.answer(f"{icon} **{f['name']}**\n⏳ Осталось: {days} дн.", reply_markup=kb.as_markup())

@dp.callback_query(F.data.startswith("done:"))
async def done_cb(cb: CallbackQuery):
    idx = int(cb.data.split(":")[1]); uid = str(cb.from_user.id); db = await load_db()
    db[uid][idx]['last_date'] = datetime.now().strftime("%Y-%m-%d")
    await save_db(db); await cb.message.edit_text("✅ Обновлено!")

@dp.message(F.text == "🗑 Управление")
async def manage_menu(message: types.Message):
    db = await load_db(); filters = db.get(str(message.from_user.id), [])
    if not filters: return await message.answer("Пусто.")
    kb = InlineKeyboardBuilder()
    addrs = sorted(list(set(f.get('address', 'Объект') for f in filters)))
    for a in addrs: kb.row(InlineKeyboardButton(text=f"🏠 {a}", callback_data=f"del:{a}"))
    await message.answer("Удалить объект:", reply_markup=kb.as_markup())

@dp.callback_query(F.data.startswith("del:"))
async def del_obj(cb: CallbackQuery):
    addr = cb.data.split(":")[1]; uid = str(cb.from_user.id); db = await load_db()
    db[uid] = [f for f in db[uid] if f.get('address') != addr]
    await save_db(db); await cb.message.edit_text(f"✅ {addr} удален.")

# --- СЕРВЕР ---
async def handle_hc(request): return web.Response(text="OK")
async def main():
    scheduler.add_job(check_reminders, "interval", hours=12); scheduler.start()
    app = web.Application(); app.router.add_get("/", handle_hc)
    runner = web.AppRunner(app); await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__": asyncio.run(main())
