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

if not TOKEN:
    logging.error("ОШИБКА: BOT_TOKEN не найден!")
    exit(1)

users_db = {}

# --- УМНЫЙ КАТАЛОГ С ПРЕСЕТАМИ СТУПЕНЕЙ ---
MODELS_DATA = {
    "osmos": {
        "Атолл": {
            "A-550": ["pre", "mem", "post"],
            "A-575": ["pre", "mem", "post"],
            "A-450": ["pre", "mem", "post"],
            "A-550m (с минерализатором)": ["pre", "mem", "post", "min"],
            "A-575m (с минерализатором)": ["pre", "mem", "post", "min"]
        },
        "Барьер": {
            "Профи Осмо 100": ["pre", "mem", "post"],
            "Профи Осмо 100 М": ["pre", "mem", "post", "min"],
            "Compact Osmo": ["pre", "mem", "post"]
        },
        "Гейзер": {
            "Престиж": ["pre", "mem", "post"],
            "Аллегро": ["pre", "mem", "post"],
            "Маэстро": ["pre", "mem", "post"],
            "Престиж М": ["pre", "mem", "post", "min"]
        },
        "Аквафор": {
            "DWM-101S Морион": ["pre", "mem", "post", "min"],
            "Осмо Про 50": ["pre", "mem", "post"],
            "Осмо 50 ПН": ["pre", "mem", "post"]
        },
        "Prio (Новая Вода)": {
            "Эксперт Osmos MO530": ["pre", "mem", "post", "min"],
            "Start Osmos": ["pre", "mem", "post"]
        }
    },
    "stage3": {
        "Атолл": {"Патриот": ["set"], "D-31": ["set"]},
        "Барьер": {"Профи Стандарт": ["set"], "Профи Смягчение": ["set"]},
        "Гейзер": {"Макс": ["set"], "Стандарт": ["set"], "БИО": ["set"]},
        "Аквафор": {"Кристалл Эко": ["set"], "Трио": ["set"]}
    },
    "flow": {
        "Аквафор": {"Фаворит": ["cart"]},
        "Барьер": {"In-Line Механика": ["cart"]},
        "Гейзер": {"Тайфун 10SL": ["cart"]}
    }
}

FILTER_CONFIGS = {
    "osmos": {
        "pre": {"name": "Предфильтры (ступ. 1-3)", "interval": 6},
        "mem": {"name": "Мембрана RO", "interval": 24}, 
        "post": {"name": "Постфильтр", "interval": 12},
        "min": {"name": "Минерализатор", "interval": 12}
    },
    "stage3": {"set": {"name": "Комплект картриджей", "interval": 12}},
    "flow": {"cart": {"name": "Сменный модуль", "interval": 6}}
}

BRAND_INTERVALS = {
    "Аквафор": {"osmos": {"pre": 6, "mem": 18, "post": 12, "min": 12}, "stage3": {"set": 12}},
    "Атолл": {"osmos": {"pre": 6, "mem": 24, "post": 12, "min": 12}, "stage3": {"set": 6}},
    "Барьер": {"osmos": {"pre": 6, "mem": 12, "post": 6, "min": 4}, "stage3": {"set": 6}},
    "Гейзер": {"osmos": {"pre": 12, "mem": 24, "post": 12, "min": 12}, "stage3": {"set": 12}}
}

class FilterStates(StatesGroup):
    waiting_for_manual_name = State()
    waiting_for_custom_interval = State()
    waiting_for_date = State()
    waiting_for_interval = State()

bot = Bot(token=TOKEN, parse_mode=types.ParseMode.HTML)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

# --- КЛАВИАТУРЫ ---
def get_main_menu():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("📊 Статус", "➕ Добавить фильтр")
    kb.row("📅 Заменил картридж", "🛒 Купить картриджи")
    kb.row("📜 История", "⚙️ Настройки")
    return kb

def get_settings_kb():
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(
        types.InlineKeyboardButton(text="⏱ Настроить интервалы", callback_data="set_intervals"),
        types.InlineKeyboardButton(text="🗑 Удалить систему", callback_data="set_del_filter"),
        types.InlineKeyboardButton(text="🧨 Очистить профиль полностью", callback_data="set_clear")
    )
    return kb

def get_categories_kb():
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(
        types.InlineKeyboardButton(text="💧 Обратный осмос", callback_data="cat_osmos"),
        types.InlineKeyboardButton(text="🧪 3-ступенчатый (под мойку)", callback_data="cat_stage3"),
        types.InlineKeyboardButton(text="🚰 Проточный / Магистральный", callback_data="cat_flow")
    )
    return kb

def get_brands_kb(category_key):
    kb = types.InlineKeyboardMarkup(row_width=2)
    brands = list(MODELS_DATA[category_key].keys())
    for brand in brands:
        marker = " 🔹" if brand in BRAND_INTERVALS else ""
        kb.insert(types.InlineKeyboardButton(text=brand + marker, callback_data=f"br_{category_key}_{brand}"))
    kb.add(types.InlineKeyboardButton(text="📝 Свой вариант", callback_data=f"manual_{category_key}"))
    kb.add(types.InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_cats"))
    return kb

def get_models_kb(category_key, brand_key):
    kb = types.InlineKeyboardMarkup(row_width=1)
    models = list(MODELS_DATA[category_key][brand_key].keys())
    for model in models:
        kb.add(types.InlineKeyboardButton(text=model, callback_data=f"mod_{category_key}_{brand_key}_{model}"))
    kb.add(types.InlineKeyboardButton(text="📝 Свой вариант", callback_data=f"manual_{category_key}"))
    kb.add(types.InlineKeyboardButton(text="⬅️ Назад", callback_data=f"back_br_{category_key}"))
    return kb

def get_user_filters_kb(user_id, action_prefix):
    kb = types.InlineKeyboardMarkup(row_width=1)
    filters = users_db.get(user_id, [])
    for i, f in enumerate(filters):
        kb.add(types.InlineKeyboardButton(text=f['model'], callback_data=f"{action_prefix}_{i}"))
    kb.add(types.InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_action"))
    return kb

def get_replacement_kb(user_id, filter_index):
    kb = types.InlineKeyboardMarkup(row_width=1)
    filter_data = users_db[user_id][filter_index]
    category, intervals = filter_data["category"], filter_data["intervals"]
    
    if category == "osmos" and intervals.get("pre", 0) > 0:
        kb.add(types.InlineKeyboardButton(text="🔄 ЗАМЕНИТЬ ВЕСЬ КОМПЛЕКТ ПРЕДФИЛЬТРОВ", callback_data=f"repall_{filter_index}"))
        
    for code, comp_data in FILTER_CONFIGS[category].items():
        if intervals.get(code, 0) > 0:
            kb.add(types.InlineKeyboardButton(text=f"🔄 {comp_data['name']}", callback_data=f"rep_{code}_{filter_index}"))
    kb.add(types.InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_action"))
    return kb

def get_intervals_kb(user_id, filter_index):
    kb = types.InlineKeyboardMarkup(row_width=1)
    filter_data = users_db[user_id][filter_index]
    category, intervals = filter_data["category"], filter_data["intervals"]
    for code, comp_data in FILTER_CONFIGS[category].items():
        current_int = intervals.get(code, 0)
        status_text = f"{comp_data['name']} ({current_int} мес)" if current_int > 0 else f"❌ {comp_data['name']} (Отключено)"
        kb.add(types.InlineKeyboardButton(text=status_text + " ✏️", callback_data=f"editint_{code}_{filter_index}"))
    kb.add(types.InlineKeyboardButton(text="⬅️ Назад", callback_data="set_intervals"))
    return kb

def get_date_choice_kb(item_code, filter_index):
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(
        types.InlineKeyboardButton(text="📅 Сегодня", callback_data=f"date_today_{item_code}_{filter_index}"),
        types.InlineKeyboardButton(text="✍️ Ввести дату вручную", callback_data=f"date_manual_{item_code}_{filter_index}"),
        types.InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_action")
    )
    return kb

def get_market_kb(model_name):
    kb = types.InlineKeyboardMarkup(row_width=1)
    query = quote(f"картриджи {model_name}")
    kb.add(
        types.InlineKeyboardButton(text="🛒 Найти на Ozon", url=f"https://www.ozon.ru/search/?text={query}"),
        types.InlineKeyboardButton(text="🟣 Найти на Wildberries", url=f"https://www.wildberries.ru/catalog/0/search.aspx?search={query}")
    )
    return kb

# --- ЛОГИКА ---
def add_new_filter(user_id, model_name, category, custom_intervals=None):
    if user_id not in users_db: users_db[user_id] = []
    users_db[user_id].append({
        "model": model_name, 
        "category": category,
        "created_at": datetime.now().strftime("%d.%m.%Y"),
        "history": [],
        "intervals": custom_intervals if custom_intervals else {},
        "notified": {}
    })

@dp.message_handler(commands=['start'])
async def cmd_start(message: types.Message):
    await message.answer("💧 <b>Система контроля фильтров</b>\nВыберите вашу систему:", reply_markup=get_categories_kb())

@dp.message_handler(lambda m: m.text == "➕ Добавить фильтр")
async def menu_add_filter(message: types.Message):
    await message.answer("<b>Выберите тип системы:</b>", reply_markup=get_categories_kb())

@dp.callback_query_handler(lambda c: c.data == 'cancel_action')
async def handle_cancel(callback_query: types.CallbackQuery):
    await bot.delete_message(callback_query.message.chat.id, callback_query.message.message_id)

@dp.callback_query_handler(lambda c: c.data.startswith('cat_'))
async def process_category(callback_query: types.CallbackQuery):
    cat_key = callback_query.data.split('_')[1]
    await bot.edit_message_text("<b>Выберите производителя:</b>", callback_query.message.chat.id, callback_query.message.message_id, reply_markup=get_brands_kb(cat_key))

@dp.callback_query_handler(lambda c: c.data.startswith('br_'))
async def process_brand(callback_query: types.CallbackQuery):
    _, cat_key, brand_key = callback_query.data.split('_')
    await bot.edit_message_text(f"Производитель: <b>{brand_key}</b>\n<i>Выберите модель:</i>", callback_query.message.chat.id, callback_query.message.message_id, reply_markup=get_models_kb(cat_key, brand_key))

@dp.callback_query_handler(lambda c: c.data.startswith('mod_'))
async def process_model(callback_query: types.CallbackQuery):
    _, category, brand, model_name = callback_query.data.split('_', 3)
    full_model_name = f"{brand} {model_name}"
    active_stages = MODELS_DATA[category][brand][model_name]
    intervals = {}
    brand_data = BRAND_INTERVALS.get(brand, {}).get(category)
    
    for code in FILTER_CONFIGS[category].keys():
        if code in active_stages:
            intervals[code] = brand_data.get(code, FILTER_CONFIGS[category][code]["interval"]) if brand_data else FILTER_CONFIGS[category][code]["interval"]
        else:
            intervals[code] = 0
    
    add_new_filter(callback_query.from_user.id, full_model_name, category, intervals)
    await bot.edit_message_text(f"✅ <b>{full_model_name}</b> добавлена!\nРесурс настроен автоматически.", callback_query.message.chat.id, callback_query.message.message_id)
    await bot.send_message(callback_query.from_user.id, "Главное меню:", reply_markup=get_main_menu())

@dp.message_handler(lambda m: m.text == "📊 Статус")
async def menu_status(message: types.Message):
    user_filters = users_db.get(message.from_user.id, [])
    if not user_filters: return await message.answer("Нет добавленных систем.")
    
    text = "📊 <b>ТЕКУЩИЙ СТАТУС</b>\n━━━━━━━━━━━━━━\n\n"
    now = datetime.now()
    for i, f in enumerate(user_filters, 1):
        text += f"🚰 <b>{i}. {f['model']}</b>\n"
        for code, comp_data in FILTER_CONFIGS[f["category"]].items():
            interval = f["intervals"].get(code, 0)
            if interval == 0: continue 
            last_rep = next((item for item in reversed(f["history"]) if item["item"] == comp_data["name"]), None)
            last_date = datetime.strptime(last_rep["date"], "%d.%m.%Y") if last_rep else datetime.strptime(f["created_at"], "%d.%m.%Y")
            days_left = (last_date + timedelta(days=interval * 30.4) - now).days
            status = "🟢" if days_left > 30 else "🟡" if days_left >= 0 else "🔴"
            text += f"  {status} {comp_data['name']}: {days_left} дн.\n"
        text += "\n"
    await message.answer(text)

@dp.message_handler(lambda m: m.text == "📅 Заменил картридж")
async def menu_replacement(message: types.Message):
    filters = users_db.get(message.from_user.id, [])
    if not filters: return await message.answer("Сначала добавьте фильтр.")
    await message.answer("Выберите систему для замены:", reply_markup=get_user_filters_kb(message.from_user.id, 'selrep'))

@dp.callback_query_handler(lambda c: c.data.startswith('selrep_'))
async def process_rep_selection(callback_query: types.CallbackQuery):
    f_idx = int(callback_query.data.split('_')[1])
    await bot.edit_message_text(f"🛠 <b>{users_db[callback_query.from_user.id][f_idx]['model']}</b>\nЧто заменили?", callback_query.message.chat.id, callback_query.message.message_id, reply_markup=get_replacement_kb(callback_query.from_user.id, f_idx))

@dp.callback_query_handler(lambda c: c.data.startswith('repall_'))
async def handle_replace_all_pre(callback_query: types.CallbackQuery):
    f_idx = int(callback_query.data.split('_')[1])
    date_now = datetime.now().strftime("%d.%m.%Y")
    users_db[callback_query.from_user.id][f_idx]["history"].append({"date": date_now, "item": FILTER_CONFIGS["osmos"]["pre"]["name"]})
    await bot.edit_message_text(f"✅ Комплект предфильтров заменен!\nДата: {date_now}", callback_query.message.chat.id, callback_query.message.message_id)

@dp.callback_query_handler(lambda c: c.data.startswith('rep_'))
async def handle_replace_action(callback_query: types.CallbackQuery):
    _, code, f_idx = callback_query.data.split('_')
    f = users_db[callback_query.from_user.id][int(f_idx)]
    item_name = FILTER_CONFIGS[f["category"]][code]["name"]
    await bot.edit_message_text(f"<b>{item_name}</b>\nКогда была замена?", callback_query.message.chat.id, callback_query.message.message_id, reply_markup=get_date_choice_kb(code, int(f_idx)))

@dp.callback_query_handler(lambda c: c.data.startswith('date_today_'))
async def process_date_today(callback_query: types.CallbackQuery):
    _, _, code, f_idx = callback_query.data.split('_')
    f_idx = int(f_idx)
    date_now = datetime.now().strftime("%d.%m.%Y")
    item_name = FILTER_CONFIGS[users_db[callback_query.from_user.id][f_idx]["category"]][code]["name"]
    users_db[callback_query.from_user.id][f_idx]["history"].append({"date": date_now, "item": item_name})
    await bot.edit_message_text(f"✅ Данные сохранены: {date_now}", callback_query.message.chat.id, callback_query.message.message_id)

@dp.message_handler(lambda m: m.text == "🛒 Купить картриджи")
async def menu_buy(message: types.Message):
    filters = users_db.get(message.from_user.id, [])
    if not filters: return await message.answer("Нет фильтров.")
    await message.answer("Для какой системы ищем?", reply_markup=get_user_filters_kb(message.from_user.id, 'selbuy'))

@dp.callback_query_handler(lambda c: c.data.startswith('selbuy_'))
async def process_buy_sel(callback_query: types.CallbackQuery):
    model = users_db[callback_query.from_user.id][int(callback_query.data.split('_')[1])]['model']
    await bot.edit_message_text(f"🛒 Ищем для <b>{model}</b>:", callback_query.message.chat.id, callback_query.message.message_id, reply_markup=get_market_kb(model))

# --- ЗАПУСК НА RENDER ---
async def start_webserver():
    app = web.Application()
    app.router.add_get("/", lambda r: web.Response(text="Alive"))
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    await web.TCPSite(runner, '0.0.0.0', port).start()

async def on_startup(dp):
    await bot.delete_webhook(drop_pending_updates=True)
    asyncio.create_task(start_webserver())

if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True, on_startup=on_startup)
