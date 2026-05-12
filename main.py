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

# --- КАТАЛОГ КОНКРЕТНЫХ СИСТЕМ ---
SYSTEM_PRESETS = {
    "💧 Аквафор Осмо / DWM": [
        {"name": "К5 (Грязь и песок)", "interval": 6},
        {"name": "К2 (Хлор и запах)", "interval": 6},
        {"name": "КО-50 (Мембрана накипь)", "interval": 24},
        {"name": "К7 (Финишная очистка)", "interval": 12}
    ],
    "💧 Гейзер Престиж / Аллегро": [
        {"name": "ПП 5мкм (Грязь)", "interval": 6},
        {"name": "СВС 10 (Уголь)", "interval": 6},
        {"name": "СВС 10 (Уголь 2)", "interval": 6},
        {"name": "Vontron 50 GPD (Мембрана)", "interval": 24},
        {"name": "Постфильтр (Вкус)", "interval": 12}
    ],
    "💧 Atoll A-550 / A-560": [
        {"name": "Pentek P5 (Грязь)", "interval": 6},
        {"name": "Pentek GAC-10 (Уголь)", "interval": 6},
        {"name": "Pentek P1 (Тонкая грязь)", "interval": 6},
        {"name": "Filmtec 50 GPD (Мембрана)", "interval": 24},
        {"name": "CK-2581 (Постфильтр)", "interval": 12}
    ],
    "💧 Барьер Профи Осмо": [
        {"name": "Механика (Песок)", "interval": 6},
        {"name": "Сорбция (Хлор)", "interval": 6},
        {"name": "Механика 1мкм", "interval": 6},
        {"name": "Мембрана Осмо", "interval": 24},
        {"name": "Постфильтр", "interval": 12}
    ],
    "🛠 Моя система (мп-5в, gac-10...)": [
        {"name": "мп-5в (Ржавчина)", "interval": 3},
        {"name": "gac-10 (Уголь)", "interval": 6},
        {"name": "мп-1в (Мелкая грязь)", "interval": 3},
        {"name": "tw40-1812-75 (Мембрана)", "interval": 24},
        {"name": "gs-10cal (Минерал)", "interval": 12}
    ]
}

class SystemStates(StatesGroup):
    choose_system = State()
    add_address = State()
    add_room = State()
    confirm_date = State()

# --- ФУНКЦИИ ---
def get_buy_links(name):
    query = urllib.parse.quote(f"картридж {name}")
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="🔹 Ozon", url=f"https://www.ozon.ru/search/?text={query}"))
    kb.row(InlineKeyboardButton(text="💜 WB", url=f"https://www.wildberries.ru/catalog/0/search.aspx?search={query}"))
    kb.row(InlineKeyboardButton(text="💛 Маркет", url=f"https://market.yandex.ru/search?text={query}"))
    return kb

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

# --- КЛАВИАТУРЫ ---
def main_kb():
    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text="📊 Мои фильтры"))
    builder.row(KeyboardButton(text="➕ Настроить новый фильтр"))
    builder.row(KeyboardButton(text="🗑 Удалить объект"))
    return builder.as_markup(resize_keyboard=True)

def make_reply_kb(items: list, placeholder: str, show_back=True):
    builder = ReplyKeyboardBuilder()
    for item in items: builder.add(KeyboardButton(text=item))
    builder.adjust(1)
    row = [KeyboardButton(text="❌ Отмена")]
    if show_back: row.insert(0, KeyboardButton(text="⬅️ Назад"))
    builder.row(*row)
    return builder.as_markup(resize_keyboard=True, input_field_placeholder=placeholder)

# --- ОБРАБОТКА "НАЗАД" ---
@dp.message(F.text == "⬅️ Назад")
async def go_back(message: types.Message, state: FSMContext):
    curr = await state.get_state()
    if curr == SystemStates.add_address: await add_start(message, state)
    elif curr == SystemStates.add_room:
        await state.set_state(SystemStates.add_address)
        await message.answer("🏠 Где стоит фильтр?", reply_markup=make_reply_kb(["Дом", "Квартира", "Дача"], "Объект..."))
    elif curr == SystemStates.confirm_date:
        await state.set_state(SystemStates.add_room)
        await message.answer("📍 В каком помещении?", reply_markup=make_reply_kb(["Кухня", "Санузел"], "Комната..."))

# --- ДОБАВЛЕНИЕ ---
@dp.message(F.text == "➕ Настроить новый фильтр")
async def add_start(message: types.Message, state: FSMContext):
    await state.set_state(SystemStates.choose_system)
    kb = make_reply_kb(list(SYSTEM_PRESETS.keys()), "Выберите модель...", show_back=False)
    await message.answer("❓ **Какая система у вас установлена?**", reply_markup=kb)

@dp.message(SystemStates.choose_system)
async def add_2(message: types.Message, state: FSMContext):
    if message.text not in SYSTEM_PRESETS: return await message.answer("Выберите из списка.")
    await state.update_data(system_model=message.text)
    await state.set_state(SystemStates.add_address)
    await message.answer("🏠 **Где стоит фильтр?**", reply_markup=make_reply_kb(["Дом", "Квартира", "Дача"], "Место..."))

@dp.message(SystemStates.add_address)
async def add_3(message: types.Message, state: FSMContext):
    await state.update_data(address=message.text)
    await state.set_state(SystemStates.add_room)
    await message.answer("📍 **В каком помещении?**", reply_markup=make_reply_kb(["Кухня", "Санузел"], "Комната..."))

@dp.message(SystemStates.add_room)
async def add_4(message: types.Message, state: FSMContext):
    await state.update_data(room=message.text)
    await state.set_state(SystemStates.confirm_date)
    await message.answer("⏳ **Когда была замена?**", reply_markup=make_reply_kb(["Сегодня", "Неделю назад", "Месяц назад"], "Дата..."))

@dp.message(SystemStates.confirm_date)
async def add_5(message: types.Message, state: FSMContext):
    data = await state.get_data(); uid = str(message.from_user.id)
    install_date = datetime.now()
    if "неделю" in message.text.lower(): install_date -= timedelta(days=7)
    elif "месяц" in message.text.lower(): install_date -= timedelta(days=30)
    
    db = await load_db(); 
    if uid not in db: db[uid] = []
    
    for cart in SYSTEM_PRESETS[data['system_model']]:
        db[uid].append({
            "address": data['address'], "room": data['room'],
            "system": data['system_model'], "name": cart['name'],
            "interval": cart['interval'], "last_date": install_date.strftime("%Y-%m-%d")
        })
    
    await save_db(db); await state.clear()
    await message.answer("✅ Система настроена!", reply_markup=main_kb())

# --- УДАЛЕНИЕ (ИСПРАВЛЕНО) ---
@dp.message(F.text == "🗑 Удалить объект")
async def manage_menu(message: types.Message):
    db = await load_db(); filters = db.get(str(message.from_user.id), [])
    if not filters: return await message.answer("Пусто.")
    kb = InlineKeyboardBuilder()
    addrs = sorted(list(set(f.get('address', 'Объект') for f in filters)))
    for a in addrs: kb.row(InlineKeyboardButton(text=f"🏠 Удалить {a}", callback_data=f"del_obj:{a}"))
    await message.answer("Что именно удалить?", reply_markup=kb.as_markup())

@dp.callback_query(F.data.startswith("del_obj:"))
async def del_obj_cb(cb: CallbackQuery):
    addr = cb.data.split(":")[1]; uid = str(cb.from_user.id); db = await load_db()
    db[uid] = [f for f in db[uid] if f.get('address') != addr]
    await save_db(db); await cb.message.edit_text(f"✅ Объект '{addr}' полностью удален.")

# --- СТАТУС (ССЫЛКИ ВЕРНУЛИ) ---
@dp.message(F.text == "📊 Мои фильтры")
async def show_status(message: types.Message):
    db = await load_db(); filters = db.get(str(message.from_user.id), [])
    if not filters: return await message.answer("Список пуст.")
    
    grouped = {}
    for f in filters:
        key = (f.get('address', 'Дом'), f.get('room', 'Кухня'), f.get('system', 'Фильтр'))
        if key not in grouped: grouped[key] = []
        grouped[key].append(f)

    for (addr, room, sys_name), carts in grouped.items():
        res = f"🏰 **{addr}** | 📍 {room}\n💧 *{sys_name}*\n"
        await message.answer(res, parse_mode="Markdown")
        for c in carts:
            next_d = datetime.strptime(c['last_date'], "%Y-%m-%d") + timedelta(days=c['interval']*30)
            days = (next_d - datetime.now()).days
            icon = "🟢" if days > 15 else "🟡" if days > 0 else "🔴"
            kb = get_buy_links(c['name'])
            await message.answer(f"{icon} {c['name']}\n⏳ Осталось: {days} дн.", reply_markup=kb.as_markup())

# --- УВЕДОМЛЕНИЯ ---
async def check_reminders():
    db = await load_db(); today = datetime.now()
    for uid, filters in db.items():
        for i, f in enumerate(filters):
            next_d = datetime.strptime(f['last_date'], "%Y-%m-%d") + timedelta(days=f['interval']*30)
            if today >= next_d:
                kb = get_buy_links(f['name'])
                kb.row(InlineKeyboardButton(text="✅ Заменил", callback_data=f"done:{i}"))
                text = f"🔔 **ПОРА ЗАМЕНЫ!**\n📍 {f['address']} -> {f['name']}"
                try: await bot.send_message(uid, text, reply_markup=kb.as_markup())
                except: pass

@dp.callback_query(F.data.startswith("done:"))
async def done_cb(cb: CallbackQuery):
    idx = int(cb.data.split(":")[1]); uid = str(cb.from_user.id); db = await load_db()
    db[uid][idx]['last_date'] = datetime.now().strftime("%Y-%m-%d")
    await save_db(db); await cb.message.edit_text("✅ Дата обновлена!")

@dp.message(F.text == "❌ Отмена")
@dp.message(Command("cancel"))
async def cancel(message: types.Message, state: FSMContext):
    await state.clear(); await message.answer("Отменено.", reply_markup=main_kb())

async def handle_hc(request): return web.Response(text="OK")
async def main():
    scheduler.add_job(check_reminders, "interval", hours=12); scheduler.start()
    app = web.Application(); app.router.add_get("/", handle_hc)
    runner = web.AppRunner(app); await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__": asyncio.run(main())
