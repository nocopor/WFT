import logging
import os
import asyncio
from datetime import datetime, timedelta
from urllib.parse import quote
from aiogram import Bot, Dispatcher, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.utils import executor
from aiohttp import web

# --- КОНФИГУРАЦИЯ ---
TOKEN = os.getenv("BOT_TOKEN")
logging.basicConfig(level=logging.INFO)

users_db = {}

# --- ОБНОВЛЕННЫЙ КАТАЛОГ (ДОБАВЛЕНЫ МИНЕРАЛИЗАТОРЫ) ---
MODELS_DATA = {
    "osmos": {
        "Атолл": {
            "A-550 / A-575": ["pre", "mem", "post"],
            "A-550m / A-575m (с минерализатором)": ["pre", "mem", "post", "min"],
            "A-450 Compact": ["pre", "mem", "post"]
        },
        "Аквафор": {
            "DWM-101S Морион": ["pre", "mem", "post", "min"],
            "DWM-102S / 202S Pro": ["pre", "mem", "post", "min"],
            "Осмо Про 50/100": ["pre", "mem", "post"],
            "Осмо Про 50/100 M (с минерал.)": ["pre", "mem", "post", "min"]
        },
        "Гейзер": {
            "Престиж / Аллегро": ["pre", "mem", "post"],
            "Престиж М / Аллегро М (с минерал.)": ["pre", "mem", "post", "min"],
            "Престиж Смарт": ["pre", "mem", "post", "min"],
            "Нанотек": ["pre", "mem", "post"]
        },
        "Барьер": {
            "Профи Осмо 100": ["pre", "mem", "post"],
            "Профи Осмо 100 М (с минерал.)": ["pre", "mem", "post", "min"],
            "Compact Osmo 100": ["pre", "mem", "post"],
            "Compact Osmo 100 M (с минерал.)": ["pre", "mem", "post", "min"]
        },
        "Prio (Новая Вода)": {
            "Expert Osmos MO530": ["pre", "mem", "post", "min"],
            "Econic Osmos OD320": ["pre", "mem", "post", "min"],
            "Start Osmos": ["pre", "mem", "post"]
        }
    },
    "stage3": {
        "Гейзер": {"Макс": ["set"], "Стандарт": ["set"], "БИО": ["set"]},
        "Аквафор": {"Кристалл Эко": ["set"], "Трио": ["set"]},
        "Барьер": {"Профи Стандарт": ["set"], "Эксперт Смягчение": ["set"]}
    }
}

FILTER_CONFIGS = {
    "osmos": {
        "pre": {"name": "Предфильтры (1-3)", "interval": 6},
        "mem": {"name": "Мембрана RO", "interval": 24}, 
        "post": {"name": "Постфильтр", "interval": 12},
        "min": {"name": "Минерализатор", "interval": 12}
    },
    "stage3": {"set": {"name": "Комплект картриджей", "interval": 12}},
    "flow": {"cart": {"name": "Сменный модуль", "interval": 6}}
}

# --- ИНТЕРВАЛЫ ПО БРЕНДАМ ---
BRAND_INTERVALS = {
    "Аквафор": {"osmos": {"pre": 6, "mem": 18, "post": 12, "min": 12}},
    "Атолл": {"osmos": {"pre": 6, "mem": 24, "post": 12, "min": 12}},
    "Барьер": {"osmos": {"pre": 6, "mem": 12, "post": 6, "min": 4}},
    "Гейзер": {"osmos": {"pre": 6, "mem": 24, "post": 12, "min": 12}}
}

bot = Bot(token=TOKEN, parse_mode=types.ParseMode.HTML)
dp = Dispatcher(bot, storage=MemoryStorage())

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---
def get_main_menu():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("📊 Статус", "➕ Добавить фильтр")
    kb.row("📅 Заменил картридж", "🛒 Купить картриджи")
    kb.row("📜 История", "⚙️ Настройки")
    return kb

def get_categories_kb():
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("💧 Обратный осмос", callback_data="cat_osmos"),
           types.InlineKeyboardButton("🧪 3-ступенчатый", callback_data="cat_stage3"))
    return kb

def get_brands_kb(cat):
    kb = types.InlineKeyboardMarkup(row_width=2)
    for brand in MODELS_DATA[cat].keys():
        kb.insert(types.InlineKeyboardButton(brand, callback_data=f"br_{cat}_{brand}"))
    kb.add(types.InlineKeyboardButton("⬅️ Назад", callback_data="back_to_cats"))
    return kb

def get_models_kb(cat, brand):
    kb = types.InlineKeyboardMarkup(row_width=1)
    for model in MODELS_DATA[cat][brand].keys():
        kb.add(types.InlineKeyboardButton(model, callback_data=f"mod_{cat}_{brand}_{model}"))
    return kb

def get_replacement_kb(user_id, f_idx):
    kb = types.InlineKeyboardMarkup(row_width=1)
    f = users_db[user_id][f_idx]
    if f['category'] == 'osmos' and 'pre' in f['intervals']:
        kb.add(types.InlineKeyboardButton("🔄 ЗАМЕНИТЬ ВЕСЬ КОМПЛЕКТ (1-3)", callback_data=f"rep_allpre_{f_idx}"))
    for code, interval in f['intervals'].items():
        if interval > 0:
            name = FILTER_CONFIGS[f['category']][code]['name']
            kb.add(types.InlineKeyboardButton(f"⚙️ {name}", callback_data=f"rep_{code}_{f_idx}"))
    return kb

# --- ОБРАБОТЧИКИ ---
@dp.message_handler(commands=['start'])
async def cmd_start(message: types.Message):
    await message.answer("💧 <b>Привет! Я помогу следить за фильтрами.</b>", reply_markup=get_main_menu())
    await message.answer("Выберите тип вашей системы:", reply_markup=get_categories_kb())

@dp.callback_query_handler(lambda c: c.data == 'back_to_cats')
async def back_to_cats(callback_query: types.CallbackQuery):
    await bot.edit_message_text("Выберите тип системы:", callback_query.message.chat.id, 
                                 callback_query.message.message_id, reply_markup=get_categories_kb())

@dp.callback_query_handler(lambda c: c.data.startswith('cat_'))
async def process_cat(callback_query: types.CallbackQuery):
    cat = callback_query.data.split('_')[1]
    await bot.edit_message_text("Выберите бренд:", callback_query.message.chat.id, 
                                 callback_query.message.message_id, reply_markup=get_brands_kb(cat))

@dp.callback_query_handler(lambda c: c.data.startswith('br_'))
async def process_brand(callback_query: types.CallbackQuery):
    _, cat, brand = callback_query.data.split('_')
    await bot.edit_message_text(f"Модели <b>{brand}</b>:", callback_query.message.chat.id, 
                                 callback_query.message.message_id, reply_markup=get_models_kb(cat, brand))

@dp.callback_query_handler(lambda c: c.data.startswith('mod_'))
async def process_model_select(callback_query: types.CallbackQuery):
    _, cat, brand, model = callback_query.data.split('_', 3)
    active_codes = MODELS_DATA[cat][brand][model]
    
    # Расчет интервалов с учетом бренда
    intervals = {}
    brand_data = BRAND_INTERVALS.get(brand, {}).get(cat, {})
    for code in active_codes:
        intervals[code] = brand_data.get(code, FILTER_CONFIGS[cat][code]['interval'])

    user_id = callback_query.from_user.id
    if user_id not in users_db: users_db[user_id] = []
    users_db[user_id].append({
        "model": f"{brand} {model}", "category": cat, "intervals": intervals,
        "history": [], "created_at": datetime.now().strftime("%d.%m.%Y")
    })
    
    await bot.send_message(user_id, f"✅ <b>{brand} {model}</b> добавлена! Ступеней: {len(active_codes)}.", 
                           reply_markup=get_main_menu())

@dp.message_handler(lambda m: m.text == "📊 Статус")
async def cmd_status(message: types.Message):
    user_id = message.from_user.id
    if not users_db.get(user_id): return await message.answer("Сначала добавьте фильтр.")
    
    res = "📊 <b>Статус фильтров:</b>\n\n"
    now = datetime.now()
    for f in users_db[user_id]:
        res += f"🚰 <b>{f['model']}</b>\n"
        for code, interval in f['intervals'].items():
            name = FILTER_CONFIGS[f['category']][code]['name']
            # Упрощенный расчет для примера
            res += f"  ▫️ {name}: ок\n"
    await message.answer(res)

@dp.message_handler(lambda m: m.text == "📅 Заменил картридж")
async def cmd_replace(message: types.Message):
    user_id = message.from_user.id
    if not users_db.get(user_id): return await message.answer("Нет активных систем.")
    await message.answer("Что заменили?", reply_markup=get_replacement_kb(user_id, 0))

# --- ЗАПУСК НА RENDER ---
async def start_webserver():
    app = web.Application()
    app.router.add_get("/", lambda r: web.Response(text="Bot is running"))
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    await web.TCPSite(runner, '0.0.0.0', port).start()

async def on_startup(dp):
    await bot.delete_webhook(drop_pending_updates=True)
    asyncio.create_task(start_webserver())

if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True, on_startup=on_startup)
