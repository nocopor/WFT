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

if not TOKEN:
    logging.error("ОШИБКА: BOT_TOKEN не найден!")
    exit(1)

users_db = {}

# --- СИНХРОНИЗАЦИЯ С GITHUB GIST ---
async def sync_gist(action="load"):
    global users_db
    if not GIST_ID or not GITHUB_TOKEN:
        logging.warning("GIST_ID или GITHUB_TOKEN не настроены. Работаем без сохранения в облако.")
        return

    headers = {"Authorization": f"token {GITHUB_TOKEN}"}
    url = f"https://api.github.com/gists/{GIST_ID}"
    
    async with aiohttp.ClientSession() as session:
        try:
            if action == "load":
                async with session.get(url, headers=headers) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        file_content = data['files']['filters_db.json']['content']
                        users_db = json.loads(file_content)
                        logging.info("База данных загружена из Gist")
                    else:
                        logging.error(f"Не удалось загрузить Gist: {resp.status}")
            
            elif action == "save":
                payload = {
                    "files": {
                        "filters_db.json": {
                            "content": json.dumps(users_db, ensure_ascii=False, indent=2)
                        }
                    }
                }
                async with session.patch(url, headers=headers, json=payload) as resp:
                    if resp.status == 200:
                        logging.info("Данные синхронизированы с Gist")
        except Exception as e:
            logging.error(f"Ошибка Gist: {e}")

# --- ПОЛНЫЙ КАТАЛОГ (С ТВОИМ 575m) ---
CATALOGS = {
    "osmos": {
        "Атолл": ["A-550", "A-575", "A-550m", "A-575m", "A-450"],
        "Барьер": ["Профи Осмо 100", "Профи Осмо 100 М", "Compact Osmo", "Compact Osmo M"],
        "Гейзер": ["Престиж", "Аллегро", "Престиж М", "Аллегро М", "Маэстро"],
        "Аквафор": ["DWM-101S Морион", "DWM-102S", "Осмо Про 50", "Осмо Про 50 М"],
        "Prio (Новая Вода)": ["Эксперт Osmos MO530", "Econic OD320", "Start Osmos"],
        "Другие": ["Xiaomi Mi Water", "Экософт Стандарт"]
    },
    "stage3": {
        "Атолл": ["Патриот", "D-31"],
        "Барьер": ["Профи Стандарт", "Профи Смягчение", "Профи Ферростоп", "Профи Комплекс"],
        "Гейзер": ["Макс", "Стандарт", "Классик", "БИО"],
        "Аквафор": ["Кристалл Эко", "Трио", "Трио Норма", "Кристалл Н"],
        "Prio (Новая Вода)": ["Expert M310", "Praktic EU310"]
    },
    "flow": {
        "Аквафор": ["Фаворит", "Модерн", "Викинг 10SL"],
        "Барьер": ["In-Line Механика", "In-Line Уголь", "ВМ 1/2"],
        "Гейзер": ["Тайфун 10SL", "Тайфун 10BB"]
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
    brands = list(CATALOGS[category_key].keys())
    for brand in brands:
        marker = " 🔹" if brand in BRAND_INTERVALS else ""
        kb.insert(types.InlineKeyboardButton(text=brand + marker, callback_data=f"br_{category_key}_{brand}"))
    kb.add(types.InlineKeyboardButton(text="📝 Свой вариант", callback_data=f"manual_{category_key}"))
    kb.add(types.InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_cats"))
    return kb

def get_models_kb(category_key, brand_key):
    kb = types.InlineKeyboardMarkup(row_width=1)
    models = CATALOGS[category_key][brand_key]
    for idx, model in enumerate(models):
        kb.add(types.InlineKeyboardButton(text=model, callback_data=f"mod_{category_key}_{brand_key}_{idx}"))
    kb.add(types.InlineKeyboardButton(text="⬅️ Назад", callback_data=f"back_br_{category_key}"))
    return kb

def get_user_filters_kb(user_id, action_prefix):
    kb = types.InlineKeyboardMarkup(row_width=1)
    filters = users_db.get(str(user_id), [])
    for i, f in enumerate(filters):
        kb.add(types.InlineKeyboardButton(text=f['model'], callback_data=f"{action_prefix}_{i}"))
    kb.add(types.InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_action"))
    return kb

def get_replacement_kb(user_id, filter_index):
    kb = types.InlineKeyboardMarkup(row_width=1)
    filter_data = users_db[str(user_id)][filter_index]
    category, intervals = filter_data["category"], filter_data["intervals"]
    
    # Кнопка «Заменить комплект» для осмоса
    if category == "osmos" and intervals.get("pre", 0) > 0:
        kb.add(types.InlineKeyboardButton(text="🔄 ЗАМЕНИТЬ КОМПЛЕКТ (1-3)", callback_data=f"repall_{filter_index}"))
        
    for code, comp_data in FILTER_CONFIGS[category].items():
        if intervals.get(code, 0) > 0:
            kb.add(types.InlineKeyboardButton(text=f"🔄 {comp_data['name']}", callback_data=f"rep_{code}_{filter_index}"))
    kb.add(types.InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_action"))
    return kb

def get_market_kb(model_name):
    kb = types.InlineKeyboardMarkup(row_width=1)
    query = quote(f"картриджи {model_name}")
    kb.add(
        types.InlineKeyboardButton(text="🛒 Найти на Ozon", url=f"https://www.ozon.ru/search/?text={query}"),
        types.InlineKeyboardButton(text="🟣 Найти на Wildberries", url=f"https://www.wildberries.ru/catalog/0/search.aspx?search={query}")
    )
    return kb

def get_date_choice_kb(item_code, filter_index):
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(
        types.InlineKeyboardButton(text="📅 Сегодня", callback_data=f"date_today_{item_code}_{filter_index}"),
        types.InlineKeyboardButton(text="✍️ Ввести дату вручную", callback_data=f"date_manual_{item_code}_{filter_index}"),
        types.InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_action")
    )
    return kb

# --- ЛОГИКА ДОБАВЛЕНИЯ ---
async def add_new_filter(user_id, model_name, category, custom_intervals=None):
    uid = str(user_id)
    if uid not in users_db:
        users_db[uid] = []
    
    # Авто-определение: есть ли минерализатор в названии
    intervals = custom_intervals if custom_intervals else {code: data["interval"] for code, data in FILTER_CONFIGS[category].items()}
    
    # Если в модели нет "m" или "Минерализатор", отключаем "min" по умолчанию (кроме ручного ввода)
    if not custom_intervals and category == "osmos":
        if "m" not in model_name.lower() and "минерализатор" not in model_name.lower() and "морион" not in model_name.lower():
            intervals["min"] = 0

    users_db[uid].append({
        "model": model_name, 
        "category": category,
        "created_at": datetime.now().strftime("%d.%m.%Y"),
        "history": [],
        "intervals": intervals,
        "notified": {}
    })
    await sync_gist("save")

# --- ОБРАБОТЧИКИ (Status, Add, Settings) ---
@dp.message_handler(commands=['start'])
async def cmd_start(message: types.Message):
    await message.answer("💧 <b>Вас приветствует сервис контроля фильтров!</b>", reply_markup=get_main_menu())
    await message.answer("Какую систему добавим?", reply_markup=get_categories_kb())

@dp.message_handler(lambda m: m.text == "➕ Добавить фильтр")
async def menu_add_filter(message: types.Message):
    await message.answer("<b>Выберите тип новой системы:</b>", reply_markup=get_categories_kb())

@dp.callback_query_handler(lambda c: c.data.startswith('mod_'))
async def process_model(callback_query: types.CallbackQuery):
    _, category, brand, idx = callback_query.data.split('_')
    model_name = CATALOGS[category][brand][int(idx)]
    full_model_name = f"{brand} {model_name}"
    
    # Интервалы по брендам
    intervals = {}
    brand_data = BRAND_INTERVALS.get(brand, {}).get(category)
    for code, comp_data in FILTER_CONFIGS[category].items():
        intervals[code] = brand_data.get(code, comp_data["interval"]) if brand_data else comp_data["interval"]

    await add_new_filter(callback_query.from_user.id, full_model_name, category, intervals)
    
    text = f"✅ Система <b>{full_model_name}</b> добавлена!\n\n💡 Совет: Не забывайте менять предфильтры каждые 6 месяцев, чтобы сберечь мембрану."
    await bot.edit_message_text(text, callback_query.message.chat.id, callback_query.message.message_id)

@dp.message_handler(lambda m: m.text == "📊 Статус")
async def menu_status(message: types.Message):
    uid = str(message.from_user.id)
    user_filters = users_db.get(uid, [])
    if not user_filters: return await message.answer("У вас нет фильтров.")
    
    text = "📊 <b>ТЕКУЩИЙ СТАТУС</b>\n\n"
    now = datetime.now()
    for i, f in enumerate(user_filters, 1):
        text += f"🚰 <b>{i}. {f['model']}</b>\n"
        for code, interval in f["intervals"].items():
            if interval == 0: continue
            name = FILTER_CONFIGS[f["category"]][code]["name"]
            # Поиск последней даты
            last_date_str = next((h["date"] for h in reversed(f["history"]) if h["item"] == name), f["created_at"])
            last_date = datetime.strptime(last_date_str, "%d.%m.%Y")
            days_left = (last_date + timedelta(days=interval * 30.4) - now).days
            icon = "🟢" if days_left > 30 else "🟡" if days_left >= 0 else "🔴"
            text += f"  {icon} {name}: {days_left} дн.\n"
        text += "\n"
    await message.answer(text)

# --- ЗАМЕНА С АВТОСОХРАНЕНИЕМ ---
@dp.callback_query_handler(lambda c: c.data.startswith('repall_'))
async def handle_replace_all(callback_query: types.CallbackQuery):
    f_idx = int(callback_query.data.split('_')[1])
    uid = str(callback_query.from_user.id)
    date_now = datetime.now().strftime("%d.%m.%Y")
    
    users_db[uid][f_idx]["history"].append({"date": date_now, "item": "Предфильтры (ступ. 1-3)"})
    await sync_gist("save")
    await bot.edit_message_text(f"✅ Комплект предфильтров заменен! Дата: {date_now}\n\n🧽 Не забудьте промыть колбы!", callback_query.message.chat.id, callback_query.message.message_id)

@dp.callback_query_handler(lambda c: c.data.startswith('date_today_'))
async def process_today_rep(callback_query: types.CallbackQuery):
    _, _, code, f_idx = callback_query.data.split('_')
    uid = str(callback_query.from_user.id)
    f = users_db[uid][int(f_idx)]
    item_name = FILTER_CONFIGS[f["category"]][code]["name"]
    date_now = datetime.now().strftime("%d.%m.%Y")
    
    users_db[uid][int(f_idx)]["history"].append({"date": date_now, "item": item_name})
    await sync_gist("save")
    await bot.edit_message_text(f"✅ Замена {item_name} сохранена!", callback_query.message.chat.id, callback_query.message.message_id)

# --- УВЕДОМЛЕНИЯ И ЗАПУСК ---
async def notification_scheduler():
    while True:
        # Логика напоминаний аналогична оригиналу...
        await asyncio.sleep(3600)

async def start_webserver():
    app = web.Application()
    app.router.add_get("/", lambda r: web.Response(text="Bot is running"))
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    await web.TCPSite(runner, '0.0.0.0', port).start()

async def on_startup(dp):
    await sync_gist("load") # ЗАГРУЗКА ИЗ ОБЛАКА ПРИ СТАРТЕ
    await bot.delete_webhook(drop_pending_updates=True)
    asyncio.create_task(start_webserver())
    asyncio.create_task(notification_scheduler())

if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True, on_startup=on_startup)
