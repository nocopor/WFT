import os, asyncio, json, logging, httpx
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove, InlineKeyboardButton, CallbackQuery
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
ROOM_SUGGESTIONS = ["Кухня", "Ванна", "Туалет", "Санузел", "Бойлерная"]

# Каталог: Название -> Рекомендуемый срок в месяцах
FILTER_CATALOG = {
    "Механика (ПП)": 3,
    "Угольный (GAC/CBC)": 6,
    "Аквафор В510-02": 6,
    "Барьер Профи": 6,
    "Гейзер ПФ": 6,
    "Мембрана 50GPD": 18,
    "Мембрана 75GPD": 24,
    "Постфильтр T33": 12,
    "Минерализатор": 12,
    "Внешний фильтр холодильника": 12
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
    return builder.as_markup(resize_keyboard=True)

def make_reply_kb(items: list, placeholder: str):
    builder = ReplyKeyboardBuilder()
    for item in items: builder.add(KeyboardButton(text=item))
    builder.adjust(2)
    return builder.as_markup(resize_keyboard=True, input_field_placeholder=placeholder)

# --- УВЕДОМЛЕНИЯ И SNOOZE ---
async def send_check_reminders():
    db = await load_db()
    today = datetime.now()
    for uid, user_filters in db.items():
        for i, f in enumerate(user_filters):
            # Проверка отложенных напоминаний
            snooze_until = f.get('snooze_until')
            if snooze_until and datetime.strptime(snooze_until, "%Y-%m-%d") > today:
                continue

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
                text = f"🔔 **Пора менять фильтр!**\n\n📍 {f['address']} -> {f['room']}\n🛠 {f['name']}\n📅 Срок вышел: {next_d.strftime('%d.%m.%Y')}"
                try: await bot.send_message(uid, text, reply_markup=kb.as_markup(), parse_mode="Markdown")
                except: pass

@dp.callback_query(F.data.startswith("snz:"))
async def snooze_callback(callback: CallbackQuery):
    _, idx, days = callback.data.split(":")
    uid = str(callback.from_user.id)
    db = await load_db()
    
    snooze_date = (datetime.now() + timedelta(days=int(days))).strftime("%Y-%m-%d")
    db[uid][int(idx)]['snooze_until'] = snooze_date
    await save_db(db)
    
    await callback.answer(f"Отложено на {days} дн.")
    await callback.message.delete()

@dp.callback_query(F.data.startswith("done:"))
async def done_callback(callback: CallbackQuery):
    idx = int(callback.data.split(":")[1])
    uid = str(callback.from_user.id)
    db = await load_db()
    
    db[uid][idx]['last_date'] = datetime.now().strftime("%Y-%m-%d")
    db[uid][idx]['snooze_until'] = None
    await save_db(db)
    
    await callback.answer("✅ Отлично! Дата обновлена.")
    await callback.message.edit_text(f"✅ Дата замены для {db[uid][idx]['name']} обновлена на сегодня!")

# --- ДОБАВЛЕНИЕ ФИЛЬТРА ---
@dp.message(F.text == "➕ Добавить фильтр")
async def add_1(message: types.Message, state: FSMContext):
    await state.set_state(FilterStates.add_address)
    await message.answer("🏠 **Где находится фильтр?**", reply_markup=make_reply_kb(ADDRESS_SUGGESTIONS, "Название дома..."))

@dp.message(FilterStates.add_address)
async def add_2(message: types.Message, state: FSMContext):
    await state.update_data(address=message.text)
    await state.set_state(FilterStates.add_room)
    await message.answer("📍 **В какой комнате?**", reply_markup=make_reply_kb(ROOM_SUGGESTIONS, "Выберите комнату..."))

@dp.message(FilterStates.add_room)
async def add_3(message: types.Message, state: FSMContext):
    await state.update_data(room=message.text)
    await state.set_state(FilterStates.add_name)
    # Показываем первую страницу каталога
    items = list(FILTER_CATALOG.keys())[:6]
    kb = make_reply_kb(items + ["➡️ Ещё варианты"], "Модель картриджа...")
    await message.answer("⚙️ **Модель картриджа:**", reply_markup=kb)

@dp.message(FilterStates.add_name, F.text == "➡️ Ещё варианты")
async def add_3_page2(message: types.Message):
    items = list(FILTER_CATALOG.keys())[6:]
    kb = make_reply_kb(items + ["⬅️ Назад"], "Другие модели...")
    await message.answer("⚙️ **Другие популярные модели:**", reply_markup=kb)

@dp.message(FilterStates.add_name)
async def add_4(message: types.Message, state: FSMContext):
    if message.text == "⬅️ Назад": return await add_3(message, state)
    
    name = message.text
    await state.update_data(name=name)
    
    # Рекомендуемый срок
    rec_months = FILTER_CATALOG.get(name, 6)
    kb = make_reply_kb([f"{rec_months} месяцев", "3 месяца", "12 месяцев"], "Срок службы...")
    
    await state.set_state(FilterStates.add_interval)
    await message.answer(f"📅 **Рекомендуемый срок для этого фильтра: {rec_months} мес.**\nИли введите своё число месяцев:", reply_markup=kb)

@dp.message(FilterStates.add_interval)
async def add_5(message: types.Message, state: FSMContext):
    val = "".join(filter(str.isdigit, message.text))
    await state.update_data(interval=int(val))
    await state.set_state(FilterStates.add_date)
    await message.answer("⏳ **Дата последней замены:**", reply_markup=make_reply_kb(["Сегодня"], "Когда меняли?"))

@dp.message(FilterStates.add_date)
async def add_6(message: types.Message, state: FSMContext):
    txt = message.text.lower()
    c_date = datetime.now().strftime("%Y-%m-%d") if txt == "сегодня" else None
    if not c_date:
        try: c_date = datetime.strptime(txt, "%d.%m.%Y").strftime("%Y-%m-%d")
        except: return await message.answer("❌ Формат: ДД.ММ.ГГГГ")
    
    data = await state.get_data()
    uid = str(message.from_user.id)
    db = await load_db()
    if uid not in db: db[uid] = []
    db[uid].append({**data, "last_date": c_date, "snooze_until": None})
    await save_db(db)
    await message.answer("✅ Сохранено в базу!", reply_markup=main_kb())
    await state.clear()

# --- ТЕСТ И СТАТУС ---
@dp.message(Command("test_remind"))
async def test_cmd(message: types.Message):
    await message.answer("⏳ Запускаю проверку уведомлений вручную...")
    await send_check_reminders()

@dp.message(F.text == "📊 Статус")
async def show_status(message: types.Message):
    db = await load_db()
    filters = db.get(str(message.from_user.id), [])
    if not filters: return await message.answer("Список пуст.")
    
    res = "📋 **Статус:**\n"
    for f in filters:
        last = datetime.strptime(f['last_date'], "%Y-%m-%d")
        next_d = last + timedelta(days=f['interval']*30)
        days = (next_d - datetime.now()).days
        icon = "🟢" if days > 15 else "🟡" if days > 0 else "🔴"
        res += f"\n{icon} **{f.get('address')}** - {f.get('name')}: {days} дн."
    await message.answer(res, parse_mode="Markdown")

@dp.message(F.text == "🗑 Управление")
async def manage_start(message: types.Message):
    # (Здесь остается твоя логика удаления из прошлого сообщения)
    await message.answer("Меню управления открыто (см. Inline кнопки)...")

# --- СЕРВЕР ---
async def handle_hc(request): return web.Response(text="OK")
async def main():
    scheduler.add_job(send_check_reminders, "interval", hours=12) # Проверка каждые 12 часов
    scheduler.start()
    
    app = web.Application()
    app.router.add_get("/", handle_hc)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__": asyncio.run(main())
