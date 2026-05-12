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

# --- УМНЫЙ КАТАЛОГ С ПРЕСЕТАМИ СТУПЕНЕЙ ---
# Структура: "Название": ["Ступени", "которые", "есть"]
# pre - предфильтры, mem - мембрана, post - постфильтр, min - минерализатор
MODELS_DATA = {
    "osmos": {
        "Атолл": {
            "A-550 / A-575": ["pre", "mem", "post"],
            "A-550m / A-575m": ["pre", "mem", "post", "min"],
            "A-450 Compact": ["pre", "mem", "post"]
        },
        "Аквафор": {
            "DWM-101S Морион": ["pre", "mem", "post", "min"],
            "DWM-202S / 102S": ["pre", "mem", "post", "min"],
            "Осмо Про 50/100": ["pre", "mem", "post"]
        },
        "Гейзер": {
            "Престиж / Аллегро": ["pre", "mem", "post"],
            "Престиж М / Аллегро М": ["pre", "mem", "post", "min"],
            "Нанотек": ["pre", "mem", "post"]
        },
        "Барьер": {
            "Профи Осмо 100": ["pre", "mem", "post"],
            "Профи Осмо 100 М": ["pre", "mem", "post", "min"],
            "Compact Osmo": ["pre", "mem", "post"]
        }
    },
    "stage3": {
        "Гейзер": {"Макс": ["set"], "Стандарт": ["set"], "БИО": ["set"]},
        "Аквафор": {"Кристалл Эко": ["set"], "Трио": ["set"]},
        "Барьер": {"Профи Стандарт": ["set"], "Эксперт Стандарт": ["set"]}
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

# --- БОТ И ЛОГИКА ---
bot = Bot(token=TOKEN, parse_mode=types.ParseMode.HTML)
dp = Dispatcher(bot, storage=MemoryStorage())

class FilterStates(StatesGroup):
    waiting_for_manual_name = State()
    waiting_for_interval = State()
    waiting_for_date = State()

# --- КЛАВИАТУРЫ ---
def get_main_menu():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("📊 Статус", "➕ Добавить фильтр")
    kb.row("📅 Заменил картридж", "🛒 Купить картриджи")
    kb.row("📜 История", "⚙️ Настройки")
    return kb

def get_brands_kb(cat):
    kb = types.InlineKeyboardMarkup(row_width=2)
    for brand in MODELS_DATA[cat].keys():
        kb.insert(types.InlineKeyboardButton(text=brand, callback_data=f"br_{cat}_{brand}"))
    kb.add(types.InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_cats"))
    return kb

def get_models_kb(cat, brand):
    kb = types.InlineKeyboardMarkup(row_width=1)
    for model in MODELS_DATA[cat][brand].keys():
        kb.add(types.InlineKeyboardButton(text=model, callback_data=f"mod_{cat}_{brand}_{model}"))
    kb.add(types.InlineKeyboardButton(text="⬅️ Назад", callback_data=f"cat_{cat}"))
    return kb

def get_replacement_kb(user_id, f_idx):
    kb = types.InlineKeyboardMarkup(row_width=1)
    f = users_db[user_id][f_idx]
    
    # Кнопка для замены всего комплекта предфильтров (если это осмос)
    if f['category'] == 'osmos' and 'pre' in f['intervals']:
        kb.add(types.InlineKeyboardButton(text="🔄 ЗАМЕНИТЬ ВЕСЬ КОМПЛЕКТ (1-3)", callback_data=f"rep_allpre_{f_idx}"))
    
    for code, interval in f['intervals'].items():
        if interval > 0:
            name = FILTER_CONFIGS[f['category']][code]['name']
            kb.add(types.InlineKeyboardButton(text=f"⚙️ {name}", callback_data=f"rep_{code}_{f_idx}"))
    return kb

# --- ОБРАБОТЧИКИ ---

@dp.message_handler(commands=['start'])
async def cmd_start(message: types.Message):
    await message.answer("💧 <b>Система контроля фильтров</b>\nВыберите тип вашей системы:", 
                         reply_markup=get_categories_inline())

def get_categories_inline():
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("💧 Обратный осмос", callback_data="cat_osmos"),
           types.InlineKeyboardButton("🧪 3-ступенчатый", callback_data="cat_stage3"))
    return kb

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
    _, cat, brand, model = callback_query.data.split('_')
    
    # Автоматически берем только те ступени, которые есть в этой модели
    active_codes = MODELS_DATA[cat][brand][model]
    intervals = {code: FILTER_CONFIGS[cat][code]['interval'] for code in active_codes}
    
    user_id = callback_query.from_user.id
    if user_id not in users_db: users_db[user_id] = []
    
    users_db[user_id].append({
        "model": f"{brand} {model}",
        "category": cat,
        "intervals": intervals,
        "history": [],
        "created_at": datetime.now().strftime("%d.%m.%Y")
    })
    
    await bot.answer_callback_query(callback_query.id, "Система добавлена!")
    await bot.send_message(user_id, f"✅ <b>{brand} {model}</b> успешно добавлена!\nСтупени настроены автоматически.", 
                           reply_markup=get_main_menu())

# --- ЛОГИКА ЗАМЕНЫ КОМПЛЕКТОМ ---
@dp.callback_query_handler(lambda c: c.data.startswith('rep_allpre_'))
async def replace_all_pre(callback_query: types.CallbackQuery):
    f_idx = int(callback_query.data.split('_')[2])
    user_id = callback_query.from_user.id
    date_str = datetime.now().strftime("%d.%m.%Y")
    
    # Записываем замену для предфильтров
    users_db[user_id][f_idx]["history"].append({"date": date_str, "item": "Комплект предфильтров (1-3)"})
    
    await bot.edit_message_text(f"✅ Отмечена замена <b>полного комплекта</b> предфильтров!\nДата: {date_str}", 
                                 callback_query.message.chat.id, callback_query.message.message_id)

# --- ЗАМЕНА ПО ОДНОМУ ---
@dp.message_handler(lambda m: m.text == "📅 Заменил картридж")
async def cmd_rep(message: types.Message):
    user_id = message.from_user.id
    if not users_db.get(user_id): return await message.answer("Сначала добавьте фильтр.")
    
    # Для простоты берем первый фильтр (или делаем выбор, если их много)
    await message.answer("Что вы заменили?", reply_markup=get_replacement_kb(user_id, 0))

# --- ВЕБ-СЕРВЕР И ЗАПУСК ---
async def on_startup(dp):
    asyncio.create_task(notification_loop())

async def notification_loop():
    while True:
        # Здесь логика уведомлений (аналогично прошлой версии)
        await asyncio.sleep(3600)

if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True, on_startup=on_startup)
