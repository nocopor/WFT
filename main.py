import logging
import os
import asyncio
import json
import aiohttp
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
GIST_ID = os.getenv("GIST_ID")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

logging.basicConfig(level=logging.INFO)

users_db = {}

# --- СИНХРОНИЗАЦИЯ С GITHUB GIST ---
async def sync_gist(action="load"):
    global users_db
    if not GIST_ID or not GITHUB_TOKEN:
        logging.warning("GIST переменные не настроены!")
        return

    headers = {"Authorization": f"token {GITHUB_TOKEN}"}
    url = f"https://api.github.com/gists/{GIST_ID}"
    
    async with aiohttp.ClientSession() as session:
        try:
            if action == "load":
                async with session.get(url, headers=headers) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        content = data['files']['filters_db.json']['content']
                        users_db = json.loads(content)
                        logging.info("База загружена из облака")
            elif action == "save":
                payload = {"files": {"filters_db.json": {"content": json.dumps(users_db, ensure_ascii=False, indent=2)}}}
                await session.patch(url, headers=headers, json=payload)
                logging.info("База синхронизирована с Gist")
        except Exception as e:
            logging.error(f"Gist Error: {e}")

# --- КАТАЛОГ (ВСЕ КАТЕГОРИИ ВОССТАНОВЛЕНЫ) ---
CATALOGS = {
    "osmos": {
        "Атолл": ["A-550", "A-575", "A-550m", "A-575m", "A-450"],
        "Барьер": ["Профи Осмо 100", "Профи Осмо 100 М", "Compact Osmo"],
        "Гейзер": ["Престиж", "Аллегро", "Престиж М", "Аллегро М"],
        "Аквафор": ["DWM-101S Морион", "DWM-102S", "Осмо Про 50", "Осмо Про 50 М"]
    },
    "stage3": {
        "Атолл": ["Патриот", "D-31"],
        "Гейзер": ["Макс", "Стандарт", "БИО"],
        "Аквафор": ["Кристалл Эко", "Трио", "Кристалл Н"]
    },
    "flow": {
        "Аквафор": ["Фаворит", "Модерн", "Викинг 10SL", "Викинг 10BB"],
        "Гейзер": ["Тайфун 10SL", "Тайфун 10BB", "Тайфун 20BB"],
        "Барьер": ["In-Line Механика", "In-Line Уголь"],
        "Джилекс": ["Колба 10SL", "Колба 10BB", "Колба 20BB"]
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

bot = Bot(token=TOKEN, parse_mode=types.ParseMode.HTML)
dp = Dispatcher(bot, storage=MemoryStorage())

# --- КЛАВИАТУРЫ ---
def get_main_menu():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("📊 Статус", "➕ Добавить фильтр")
    kb.row("📅 Заменил картридж", "🛒 Купить картриджи")
    kb.row("📜 История", "⚙️ Настройки")
    return kb

def get_categories_kb():
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(
        types.InlineKeyboardButton("💧 Обратный осмос", callback_data="cat_osmos"),
        types.InlineKeyboardButton("🧪 3-ступенчатый (под мойку)", callback_data="cat_stage3"),
        types.InlineKeyboardButton("🚰 Магистральный / Проточный", callback_data="cat_flow")
    )
    return kb

def get_brands_kb(cat_key):
    kb = types.InlineKeyboardMarkup(row_width=2)
    for brand in CATALOGS[cat_key].keys():
        kb.insert(types.InlineKeyboardButton(brand, callback_data=f"br_{cat_key}_{brand}"))
    kb.add(types.InlineKeyboardButton("⬅️ Назад", callback_data="back_cats"))
    return kb

def get_models_kb(cat_key, brand_key):
    kb = types.InlineKeyboardMarkup(row_width=1)
    for idx, model in enumerate(CATALOGS[cat_key][brand_key]):
        kb.add(types.InlineKeyboardButton(model, callback_data=f"mod_{cat_key}_{brand_key}_{idx}"))
    kb.add(types.InlineKeyboardButton("⬅️ Назад", callback_data=f"cat_{cat_key}"))
    return kb

# --- ОБРАБОТЧИКИ ---

@dp.message_handler(commands=['start'])
async def cmd_start(message: types.Message):
    await message.answer("💧 Сервис контроля фильтров готов!", reply_markup=get_main_menu())
    await message.answer("Какую систему добавим в профиль?", reply_markup=get_categories_kb())

@dp.message_handler(lambda m: m.text == "➕ Добавить фильтр")
async def add_filter_start(message: types.Message):
    await message.answer("Выберите тип системы:", reply_markup=get_categories_kb())

@dp.callback_query_handler(lambda c: c.data == "back_cats")
async def back_cats(callback_query: types.CallbackQuery):
    await bot.edit_message_text("Выберите тип системы:", callback_query.message.chat.id, callback_query.message.message_id, reply_markup=get_categories_kb())

@dp.callback_query_handler(lambda c: c.data.startswith("cat_"))
async def process_cat(callback_query: types.CallbackQuery):
    cat = callback_query.data.split('_')[1]
    await bot.edit_message_text("Выберите производителя:", callback_query.message.chat.id, callback_query.message.message_id, reply_markup=get_brands_kb(cat))

@dp.callback_query_handler(lambda c: c.data.startswith("br_"))
async def process_brand(callback_query: types.CallbackQuery):
    _, cat, brand = callback_query.data.split('_')
    await bot.edit_message_text(f"Модели {brand}:", callback_query.message.chat.id, callback_query.message.message_id, reply_markup=get_models_kb(cat, brand))

@dp.callback_query_handler(lambda c: c.data.startswith("mod_"))
async def process_model(callback_query: types.CallbackQuery):
    _, cat, brand, idx = callback_query.data.split('_')
    model_name = CATALOGS[cat][brand][int(idx)]
    uid = str(callback_query.from_user.id)
    
    intervals = {code: data['interval'] for code, data in FILTER_CONFIGS[cat].items()}
    
    # Авто-отключение минерализатора для простых осмосов
    if cat == "osmos" and not any(x in model_name.lower() for x in ["m", "мин", "морион"]):
        intervals["min"] = 0

    if uid not in users_db: users_db[uid] = []
    users_db[uid].append({
        "model": f"{brand} {model_name}", 
        "category": cat, 
        "intervals": intervals,
        "history": [], 
        "created_at": datetime.now().strftime("%d.%m.%Y")
    })
    
    await sync_gist("save")
    await bot.send_message(uid, f"✅ <b>{brand} {model_name}</b> успешно добавлена!", reply_markup=get_main_menu())

@dp.message_handler(lambda m: m.text == "📊 Статус")
async def cmd_status(message: types.Message):
    uid = str(message.from_user.id)
    if uid not in users_db or not users_db[uid]:
        return await message.answer("У вас нет фильтров. Нажмите «➕ Добавить фильтр»")
    
    res = "📊 <b>ТЕКУЩИЙ СТАТУС:</b>\n\n"
    now = datetime.now()
    for f in users_db[uid]:
        res += f"🚰 <b>{f['model']}</b>\n"
        for code, interval in f['intervals'].items():
            if interval == 0: continue
            name = FILTER_CONFIGS[f['category']][code]['name']
            last_date_str = next((h['date'] for h in reversed(f['history']) if h['item'] == name), f['created_at'])
            last_date = datetime.strptime(last_date_str, "%d.%m.%Y")
            days_left = (last_date + timedelta(days=interval * 30.4) - now).days
            icon = "🟢" if days_left > 30 else "🟡" if days_left >= 0 else "🔴"
            res += f"  {icon} {name}: {days_left} дн.\n"
        res += "\n"
    await message.answer(res)

# --- ВЕБ-СЕРВЕР И ЗАПУСК ---
async def start_webserver():
    app = web.Application()
    app.router.add_get("/", lambda r: web.Response(text="Bot is online"))
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', int(os.environ.get("PORT", 10000))).start()

async def on_startup(dp):
    await sync_gist("load")
    await bot.delete_webhook(drop_pending_updates=True)
    asyncio.create_task(start_webserver())

if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True, on_startup=on_startup)
