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

# --- КАТАЛОГ СИСТЕМ (Готовые наборы) ---
# Описания максимально простые для обычного пользователя
SYSTEM_PRESETS = {
    "🔹 Осмос (от накипи)": [
        {"name": "1 ступень (от песка и грязи)", "interval": 6},
        {"name": "2 ступень (от хлора и запаха)", "interval": 6},
        {"name": "3 ступень (финишная очистка)", "interval": 6},
        {"name": "Мембрана (защита от накипи)", "interval": 24},
        {"name": "Минерализатор (вкус воды)", "interval": 12}
    ],
    "🔹 Проточный (3 ступени)": [
        {"name": "1 ступень (от ржавчины)", "interval": 6},
        {"name": "2 ступень (смягчение)", "interval": 6},
        {"name": "3 ступень (вкус и запах)", "interval": 6}
    ],
    "🔹 Магистральный (горячая вода)": [
        {"name": "Картридж для горячей воды", "interval": 3}
    ],
    "🛠 Моя система (мп-5в, gac-10...)": [
        {"name": "мп-5в (грубая очистка)", "interval": 3},
        {"name": "gac-10 (уголь)", "interval": 6},
        {"name": "мп-1в (тонкая очистка)", "interval": 3},
        {"name": "tw40-1812-75 (мембрана)", "interval": 24},
        {"name": "gs-10cal (минерал)", "interval": 12}
    ]
}

class SystemStates(StatesGroup):
    choose_system = State()
    add_address = State()
    add_room = State()
    confirm_date = State()

# --- РАБОТА С GIST ---
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

def make_reply_kb(items: list, placeholder: str):
    builder = ReplyKeyboardBuilder()
    for item in items: builder.add(KeyboardButton(text=item))
    builder.adjust(1)
    builder.row(KeyboardButton(text="❌ Отмена"))
    return builder.as_markup(resize_keyboard=True, input_field_placeholder=placeholder)

# --- ЛОГИКА ДОБАВЛЕНИЯ ---
@dp.message(Command("start"))
async def start(message: types.Message):
    await message.answer("👋 Привет! Я помогу следить за чистотой вашей воды.\n\nБольше не нужно помнить названия картриджей — я всё сделаю за вас.", reply_markup=main_kb())

@dp.message(F.text == "➕ Настроить новый фильтр")
async def add_start(message: types.Message, state: FSMContext):
    await state.set_state(SystemStates.choose_system)
    kb = make_reply_kb(list(SYSTEM_PRESETS.keys()), "Выберите тип...")
    await message.answer("❓ **Какая система у вас установлена?**\n\nЕсли не знаете — выберите 'Осмос' (обычно это система с баком). Также можете прислать фото (функция в разработке).", reply_markup=kb)

@dp.message(SystemStates.choose_system)
async def add_2(message: types.Message, state: FSMContext):
    if message.text not in SYSTEM_PRESETS:
        return await message.answer("Пожалуйста, выберите вариант из списка.")
    await state.update_data(system_model=message.text)
    await state.set_state(SystemStates.add_address)
    await message.answer("🏠 **Где стоит фильтр?**\n(например: Дом, Квартира или Дача)", reply_markup=make_reply_kb(["Дом", "Квартира", "Дача"], "Место..."))

@dp.message(SystemStates.add_address)
async def add_3(message: types.Message, state: FSMContext):
    await state.update_data(address=message.text)
    await state.set_state(SystemStates.add_room)
    await message.answer("📍 **В каком помещении?**", reply_markup=make_reply_kb(["Кухня", "Санузел", "Бойлерная"], "Комната..."))

@dp.message(SystemStates.add_room)
async def add_4(message: types.Message, state: FSMContext):
    await state.update_data(room=message.text)
    await state.set_state(SystemStates.confirm_date)
    await message.answer("⏳ **Когда установили новые картриджи?**", reply_markup=make_reply_kb(["Сегодня", "Неделю назад", "Месяц назад"], "Дата..."))

@dp.message(SystemStates.confirm_date)
async def add_5(message: types.Message, state: FSMContext):
    data = await state.get_data()
    uid = str(message.from_user.id)
    
    # Определяем дату
    install_date = datetime.now()
    if "неделю" in message.text.lower(): install_date -= timedelta(days=7)
    elif "месяц" in message.text.lower(): install_date -= timedelta(days=30)
    
    db = await load_db()
    if uid not in db: db[uid] = []
    
    # Добавляем сразу все картриджи из пресета
    for cartridge in SYSTEM_PRESETS[data['system_model']]:
        db[uid].append({
            "address": data['address'],
            "room": data['room'],
            "system": data['system_model'],
            "name": cartridge['name'],
            "interval": cartridge['interval'],
            "last_date": install_date.strftime("%Y-%m-%d")
        })
    
    await save_db(db)
    await state.clear()
    await message.answer(f"✅ **Готово!**\nВсе картриджи для системы '{data['system_model']}' добавлены. Я напомню, когда пора будет их менять.", reply_markup=main_kb())

# --- СТАТУС (Группировка) ---
@dp.message(F.text == "📊 Мои фильтры")
async def show_status(message: types.Message):
    db = await load_db()
    filters = db.get(str(message.from_user.id), [])
    if not filters: return await message.answer("У вас пока нет настроенных фильтров.")
    
    # Группировка по Адресу -> Комнате -> Системе
    grouped = {}
    for f in filters:
        addr = f.get('address', 'Прочее')
        room = f.get('room', 'Общее')
        sys = f.get('system', 'Фильтр')
        if addr not in grouped: grouped[addr] = {}
        if room not in grouped[addr]: grouped[addr][room] = {}
        if sys not in grouped[addr][room]: grouped[addr][room][sys] = []
        grouped[addr][room][sys].append(f)
    
    res = "📋 **ВАШИ ОБЪЕКТЫ:**\n"
    for addr, rooms in grouped.items():
        res += f"\n🏰 **{addr.upper()}**\n"
        for room, systems in rooms.items():
            res += f"  📍 {room}\n"
            for sys_name, cartridges in systems.items():
                res += f"    💧 *Система: {sys_name}*\n"
                for c in cartridges:
                    last = datetime.strptime(c['last_date'], "%Y-%m-%d")
                    next_d = last + timedelta(days=c['interval'] * 30)
                    days = (next_d - datetime.now()).days
                    icon = "🟢" if days > 15 else "🟡" if days > 0 else "🔴"
                    res += f"      {icon} {c['name']}: {days} дн.\n"
    
    await message.answer(res, parse_mode="Markdown", reply_markup=main_kb())

# --- ОСТАЛЬНАЯ ЛОГИКА (Удаление, Уведомления) ОСТАЕТСЯ ПРЕЖНЕЙ ---
# ... (Код для check_reminders и удаления объектов) ...

async def handle_hc(request): return web.Response(text="OK")
async def main():
    scheduler.add_job(lambda: asyncio.create_task(check_reminders()), "interval", hours=12)
    scheduler.start()
    app = web.Application(); app.router.add_get("/", handle_hc)
    runner = web.AppRunner(app); await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__": asyncio.run(main())
