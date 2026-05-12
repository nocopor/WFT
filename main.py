import os, asyncio, json, logging, httpx
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove, 
    InlineKeyboardButton, CallbackQuery
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
dp = Dispatcher()
scheduler = AsyncIOScheduler()

# --- СПРАВОЧНИКИ ---
ADDRESS_SUGGESTIONS = ["Дом", "Квартира", "Дача", "Офис"]
ROOM_SUGGESTIONS = ["Кухня", "Ванна", "Санузел", "Туалет", "Бойлерная"]

# Каталог: Название -> Рекомендуемый срок (мес)
FILTER_CATALOG = {
    "Механика (ПП 5мкм)": 3,
    "Уголь (GAC/CBC)": 6,
    "Аквафор В510-02": 6,
    "Гейзер ПФ": 6,
    "Барьер Профи": 6,
    "Мембрана 50GPD": 24,
    "Мембрана 75GPD": 24,
    "Постфильтр T33": 12,
    "Минерализатор": 12,
    "Арагон 2": 12,
    "БС (Умягчение)": 6,
    "Внешний фильтр Fridge": 12
}

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

def make_reply_kb(items: list, placeholder: str, include_cancel=True):
    builder = ReplyKeyboardBuilder()
    for item in items: builder.add(KeyboardButton(text=item))
    builder.adjust(2)
    if include_cancel:
        builder.row(KeyboardButton(text="❌ Отмена"))
    return builder.as_markup(resize_keyboard=True, input_field_placeholder=placeholder)

# --- УМНЫЙ СБРОС (Smart Switch) ---
@dp.message(F.text == "❌ Отмена")
@dp.message(Command("cancel"))
async def global_cancel(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("Действие отменено.", reply_markup=main_kb())

@dp.message(StateFilter(FilterStates), F.text.in_(["📊 Статус", "➕ Добавить фильтр", "🗑 Управление"]))
async def smart_switch(message: types.Message, state: FSMContext):
    await state.clear()
    if message.text == "📊 Статус": await show_status(message)
    elif message.text == "➕ Добавить фильтр": await add_start(message, state)
    elif message.text == "🗑 Управление": await manage_menu(message)

# --- УВЕДОМЛЕНИЯ И SNOOZE ---
async def check_reminders():
    db = await load_db()
    today = datetime.now()
    for uid, user_filters in db.items():
        for i, f in enumerate(user_filters):
            # Проверка отложенных
            s_date = f.get('snooze_until')
            if s_date and datetime.strptime(s_date, "%Y-%m-%d") > today: continue
            
            last = datetime.strptime(f['last_date'], "%Y-%m-%d")
            next_d = last + timedelta(days=f['interval'] * 30)
            
            if today >= next_d:
                kb = InlineKeyboardBuilder()
                kb.row(InlineKeyboardButton(text="✅ Заменил сегодня", callback_data=f"done:{i}"))
                kb.row(
                    InlineKeyboardButton(text="⏳ 1 дн.", callback_data=f"snz:{i}:1"),
                    InlineKeyboardButton(text="⏳ 2 дн.", callback_data=f"snz:{i}:2"),
                    InlineKeyboardButton(text="⏳ 7 дн.", callback_data=f"snz:{i}:7")
                )
                text = f"🔔 **Пора заменить фильтр!**\n\n📍 {f.get('address')} -> {f.get('room')}\n🛠 {f.get('name')}\n📅 Срок вышел: {next_d.strftime('%d.%m.%Y')}"
                try: await bot.send_message(uid, text, reply_markup=kb.as_markup(), parse_mode="Markdown")
                except: pass

@dp.callback_query(F.data.startswith("snz:"))
async def snz_cb(callback: CallbackQuery):
    _, idx, days = callback.data.split(":")
    uid = str(callback.from_user.id)
    db = await load_db()
    db[uid][int(idx)]['snooze_until'] = (datetime.now() + timedelta(days=int(days))).strftime("%Y-%m-%d")
    await save_db(db)
    await callback.answer(f"Напомним через {days} дн.")
    await callback.message.delete()

@dp.callback_query(F.data.startswith("done:"))
async def done_cb(callback: CallbackQuery):
    idx = int(callback.data.split(":")[1]); uid = str(callback.from_user.id)
    db = await load_db()
    db[uid][idx]['last_date'] = datetime.now().strftime("%Y-%m-%d")
    db[uid][idx]['snooze_until'] = None
    await save_db(db)
    await callback.message.edit_text(f"✅ Дата замены для {db[uid][idx]['name']} обновлена!")

# --- ЛОГИКА ДОБАВЛЕНИЯ ---
@dp.message(Command("start"))
async def start(message: types.Message):
    await message.answer("🛠 **WaterFilterTracker**\nЯ напомню, когда пора менять картриджи.", reply_markup=main_kb())

@dp.message(F.text == "➕ Добавить фильтр")
async def add_start(message: types.Message, state: FSMContext):
    await state.set_state(FilterStates.add_address)
    await message.answer("🏠 **Где находится фильтр?**", reply_markup=make_reply_kb(ADDRESS_SUGGESTIONS, "Название..."))

@dp.message(FilterStates.add_address)
async def add_2(message: types.Message, state: FSMContext):
    await state.update_data(address=message.text)
    await state.set_state(FilterStates.add_room)
    await message.answer("📍 **Выберите помещение:**", reply_markup=make_reply_kb(ROOM_SUGGESTIONS, "Комната..."))

@dp.message(FilterStates.add_room)
async def add_3(message: types.Message, state: FSMContext):
    await state.update_data(room=message.text)
    await state.set_state(FilterStates.add_name)
    items = list(FILTER_CATALOG.keys())[:6]
    await message.answer("⚙️ **Модель картриджа:**", reply_markup=make_reply_kb(items + ["➡️ Еще варианты"], "Модель..."))

@dp.message(FilterStates.add_name, F.text == "➡️ Еще варианты")
async def add_3_p2(message: types.Message):
    items = list(FILTER_CATALOG.keys())[6:]
    await message.answer("⚙️ **Другие модели:**", reply_markup=make_reply_kb(items + ["⬅️ Назад"], "Другие модели..."))

@dp.message(FilterStates.add_name)
async def add_4(message: types.Message, state: FSMContext):
    if message.text == "⬅️ Назад": return await add_start(message, state)
    name = message.text
    await state.update_data(name=name)
    rec = FILTER_CATALOG.get(name, 6)
    kb = make_reply_kb([f"{rec} мес.", "3 мес.", "6 мес.", "12 мес."], "Срок...")
    await state.set_state(FilterStates.add_interval)
    await message.answer(f"📅 **Рекомендуемый ресурс: {rec} мес.**\nИли введите свое число месяцев:", reply_markup=kb)

@dp.message(FilterStates.add_interval)
async def add_5(message: types.Message, state: FSMContext):
    val = "".join(filter(str.isdigit, message.text))
    if not val: return await message.answer("Введите число.")
    await state.update_data(interval=int(val))
    await state.set_state(FilterStates.add_date)
    await message.answer("⏳ **Дата последней замены:**", reply_markup=make_reply_kb(["Сегодня"], "ДД.ММ.ГГГГ"))

@dp.message(FilterStates.add_date)
async def add_6(message: types.Message, state: FSMContext):
    txt = message.text.lower()
    c_date = datetime.now().strftime("%Y-%m-%d") if txt == "сегодня" else None
    if not c_date:
        try: c_date = datetime.strptime(txt, "%d.%m.%Y").strftime("%Y-%m-%d")
        except: return await message.answer("❌ Формат: ДД.ММ.ГГГГ")
    
    db = await load_db(); uid = str(message.from_user.id)
    if uid not in db: db[uid] = []
    data = await state.get_data()
    db[uid].append({**data, "last_date": c_date, "snooze_until": None})
    await save_db(db)
    await message.answer("✅ Успешно сохранено!", reply_markup=main_kb())
    await state.clear()

# --- СТАТУС И УПРАВЛЕНИЕ ---
@dp.message(Command("test_remind"))
async def test_cmd(message: types.Message):
    await message.answer("Проверка уведомлений...")
    await check_reminders()

@dp.message(F.text == "📊 Статус")
async def show_status(message: types.Message):
    db = await load_db(); filters = db.get(str(message.from_user.id), [])
    if not filters: return await message.answer("Список пуст.")
    res = "📋 **Ваши фильтры:**\n"
    for f in filters:
        last = datetime.strptime(f['last_date'], "%Y-%m-%d")
        next_d = last + timedelta(days=f['interval']*30)
        days = (next_d - datetime.now()).days
        icon = "🟢" if days > 15 else "🟡" if days > 0 else "🔴"
        res += f"\n{icon} **{f.get('address')}** ({f.get('room')}): {f.get('name')} — {days} дн."
    await message.answer(res, parse_mode="Markdown")

@dp.message(F.text == "🗑 Управление")
async def manage_menu(message: types.Message):
    db = await load_db(); filters = db.get(str(message.from_user.id), [])
    if not filters: return await message.answer("Нечего удалять.")
    kb = InlineKeyboardBuilder()
    addrs = sorted(list(set(f.get('address', '[Без адреса]') for f in filters)))
    for a in addrs: kb.row(InlineKeyboardButton(text=f"🏠 {a}", callback_data=f"v_a:{a}"))
    await message.answer("Управление объектами:", reply_markup=kb.as_markup())

@dp.callback_query(F.data.startswith("v_a:"))
async def v_a(cb: CallbackQuery):
    addr = cb.data.split(":")[1]; db = await load_db(); fltr = db.get(str(cb.from_user.id), [])
    rooms = sorted(list(set(f.get('room', '[Общее]') for f in fltr if f.get('address', '[Без адреса]') == addr)))
    kb = InlineKeyboardBuilder()
    for r in rooms: kb.row(InlineKeyboardButton(text=f"📍 {r}", callback_data=f"v_r:{addr}:{r}"))
    kb.row(InlineKeyboardButton(text="❌ УДАЛИТЬ ОБЪЕКТ", callback_data=f"k_a:{addr}"))
    await cb.message.edit_text(f"Объект: {addr}", reply_markup=kb.as_markup())

@dp.callback_query(F.data.startswith("k_a:"))
async def k_a(cb: CallbackQuery):
    addr = cb.data.split(":")[1]; uid = str(cb.from_user.id); db = await load_db()
    db[uid] = [f for f in db[uid] if f.get('address', '[Без адреса]') != addr]
    await save_db(db); await cb.answer("Удалено"); await cb.message.edit_text("✅ Удалено.")

# --- СЕРВЕР ---
async def handle_hc(request): return web.Response(text="OK")
async def main():
    scheduler.add_job(check_reminders, "interval", hours=12)
    scheduler.start()
    app = web.Application()
    app.router.add_get("/", handle_hc)
    runner = web.AppRunner(app); await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__": asyncio.run(main())
