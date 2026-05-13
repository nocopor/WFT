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
                        logging.info("База данных загружена из Gist")
            elif action == "save":
                payload = {"files": {"filters_db.json": {"content": json.dumps(users_db, ensure_ascii=False, indent=2)}}}
                await session.patch(url, headers=headers, json=payload)
                logging.info("Данные синхронизированы с Gist")
        except Exception as e:
            logging.error(f"Gist Error: {e}")

# --- КАТАЛОГ СИСТЕМ ---
CATALOGS = {
    "osmos": {
        "Атолл": ["A-550", "A-575", "A-550m", "A-575m", "A-450"],
        "Барьер": ["Профи Осмо 100", "Профи Осмо 100 М", "Compact Osmo", "Compact Osmo M"],
        "Гейзер": ["Престиж", "Аллегро", "Престиж М", "Аллегро М", "Маэстро"],
        "Аквафор": ["DWM-101S Морион", "DWM-102S", "Осмо Про 50", "Осмо Про 50 М"],
        "Prio": ["Expert Osmos MO530", "Econic OD320", "Start Osmos"]
    },
    "stage3": {
        "Атолл": ["Патриот", "D-31"],
        "Барьер": ["Профи Стандарт", "Профи Смягчение"],
        "Гейзер": ["Макс", "Стандарт", "БИО"],
        "Аквафор": ["Кристалл Эко", "Трио", "Кристалл Н"]
    },
    "flow": {
        "Аквафор": ["Фаворит", "Модерн", "Викинг 10SL", "Викинг 10BB"],
        "Гейзер": ["Тайфун 10SL", "Тайфун 10BB"],
        "Барьер": ["In-Line Механика", "In-Line Уголь"],
        "Джилекс": ["Колба 10SL", "Колба 10BB"]
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

class FilterStates(StatesGroup):
    waiting_for_manual_name = State()
    waiting_for_custom_interval = State()
    waiting_for_date = State()
    waiting_for_interval = State()

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
    kb.add(types.InlineKeyboardButton("📝 Свой вариант", callback_data=f"manbr_{cat_key}"))
    kb.add(types.InlineKeyboardButton("⬅️ Назад", callback_data="back_cats"))
    return kb

def get_models_kb(cat_key, brand_key):
    kb = types.InlineKeyboardMarkup(row_width=1)
    for idx, model in enumerate(CATALOGS[cat_key][brand_key]):
        kb.add(types.InlineKeyboardButton(model, callback_data=f"mod_{cat_key}_{brand_key}_{idx}"))
    kb.add(types.InlineKeyboardButton("📝 Свой вариант", callback_data=f"manmod_{cat_key}_{brand_key}"))
    kb.add(types.InlineKeyboardButton("⬅️ Назад", callback_data=f"cat_{cat_key}"))
    return kb

def get_replacement_kb(uid, f_idx):
    kb = types.InlineKeyboardMarkup(row_width=1)
    f = users_db[uid][f_idx]
    if f['category'] == "osmos" and f['intervals'].get('pre', 0) > 0:
        kb.add(types.InlineKeyboardButton("🔄 ЗАМЕНИТЬ ВЕСЬ КОМПЛЕКТ (1-3)", callback_data=f"repall_{f_idx}"))
    for code, interval in f['intervals'].items():
        if interval > 0:
            name = FILTER_CONFIGS[f['category']][code]['name']
            kb.add(types.InlineKeyboardButton(f"⚙️ {name}", callback_data=f"rep_{code}_{f_idx}"))
    kb.add(types.InlineKeyboardButton("❌ Отмена", callback_data="cancel"))
    return kb

def get_user_filters_kb(uid, prefix):
    kb = types.InlineKeyboardMarkup(row_width=1)
    for i, f in enumerate(users_db.get(uid, [])):
        kb.add(types.InlineKeyboardButton(f['model'], callback_data=f"{prefix}_{i}"))
    kb.add(types.InlineKeyboardButton("❌ Отмена", callback_data="cancel"))
    return kb

# --- ОБРАБОТЧИКИ НАВИГАЦИИ ---

@dp.message_handler(commands=['start'])
async def cmd_start(message: types.Message):
    await message.answer("💧 Сервис контроля фильтров запущен!", reply_markup=get_main_menu())
    await message.answer("Какую систему добавим?", reply_markup=get_categories_kb())

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

# --- ОБРАБОТЧИКИ СВОЕГО ВАРИАНТА ---
@dp.callback_query_handler(lambda c: c.data.startswith("manbr_") or c.data.startswith("manmod_"))
async def manual_input_start(callback_query: types.CallbackQuery, state: FSMContext):
    parts = callback_query.data.split('_')
    cat = parts[1]
    await state.update_data(manual_cat=cat)
    await FilterStates.waiting_for_manual_name.set()
    await bot.send_message(callback_query.from_user.id, "📝 Введите название вашей системы (бренд и модель):")

@dp.message_handler(state=FilterStates.waiting_for_manual_name)
async def manual_name_done(message: types.Message, state: FSMContext):
    data = await state.get_data()
    cat = data['manual_cat']
    await state.update_data(manual_name=message.text, manual_comps=list(FILTER_CONFIGS[cat].keys()), manual_idx=0, manual_ints={})
    await ask_next_manual_int(message, state)

async def ask_next_manual_int(message, state):
    data = await state.get_data()
    cat, comps, idx = data['manual_cat'], data['manual_comps'], data['manual_idx']
    if idx >= len(comps):
        uid = str(message.from_user.id)
        if uid not in users_db: users_db[uid] = []
        users_db[uid].append({
            "model": data['manual_name'], "category": cat, 
            "intervals": data['manual_ints'], "history": [], 
            "created_at": datetime.now().strftime("%d.%m.%Y")
        })
        await sync_gist("save")
        await state.finish()
        return await message.answer(f"✅ Система {data['manual_name']} добавлена!", reply_markup=get_main_menu())
    
    comp_name = FILTER_CONFIGS[cat][comps[idx]]['name']
    await FilterStates.waiting_for_custom_interval.set()
    await message.answer(f"⏱ Ресурс для: <b>{comp_name}</b> (в месяцах).\nВведите цифру (0 - если ступени нет):")

@dp.message_handler(state=FilterStates.waiting_for_custom_interval)
async def process_manual_int(message: types.Message, state: FSMContext):
    if not message.text.isdigit(): return await message.answer("⚠️ Введите только цифру.")
    data = await state.get_data()
    data['manual_ints'][data['manual_comps'][data['manual_idx']]] = int(message.text)
    await state.update_data(manual_ints=data['manual_ints'], manual_idx=data['manual_idx'] + 1)
    await ask_next_manual_int(message, state)

# --- ДОБАВЛЕНИЕ ИЗ КАТАЛОГА ---
@dp.callback_query_handler(lambda c: c.data.startswith("mod_"))
async def process_model(callback_query: types.CallbackQuery):
    _, cat, brand, idx = callback_query.data.split('_')
    model_name = CATALOGS[cat][brand][int(idx)]
    uid = str(callback_query.from_user.id)
    
    intervals = {code: data['interval'] for code, data in FILTER_CONFIGS[cat].items()}
    # Логика минерализатора
    if cat == "osmos" and not any(x in model_name.lower() for x in ["m", "мин", "морион"]):
        intervals["min"] = 0

    if uid not in users_db: users_db[uid] = []
    users_db[uid].append({
        "model": f"{brand} {model_name}", "category": cat, "intervals": intervals,
        "history": [], "created_at": datetime.now().strftime("%d.%m.%Y")
    })
    await sync_gist("save")
    await bot.send_message(uid, f"✅ <b>{brand} {model_name}</b> добавлена!", reply_markup=get_main_menu())

# --- СТАТУС ---
@dp.message_handler(lambda m: m.text == "📊 Статус")
async def cmd_status(message: types.Message):
    uid = str(message.from_user.id)
    if uid not in users_db or not users_db[uid]:
        return await message.answer("У вас нет фильтров. Добавьте систему через «➕ Добавить фильтр»")
    
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

# --- ЗАМЕНА КАРТРИДЖЕЙ ---
@dp.message_handler(lambda m: m.text == "📅 Заменил картридж")
async def cmd_replace_menu(message: types.Message):
    uid = str(message.from_user.id)
    if uid not in users_db: return await message.answer("Сначала добавьте фильтр.")
    await message.answer("Выберите систему:", reply_markup=get_user_filters_kb(uid, "selrep"))

@dp.callback_query_handler(lambda c: c.data.startswith("selrep_"))
async def process_selrep(callback_query: types.CallbackQuery):
    f_idx = int(callback_query.data.split('_')[1])
    uid = str(callback_query.from_user.id)
    await bot.edit_message_text("Что именно вы заменили?", callback_query.message.chat.id, callback_query.message.message_id, reply_markup=get_replacement_kb(uid, f_idx))

@dp.callback_query_handler(lambda c: c.data.startswith("repall_"))
async def handle_rep_all(callback_query: types.CallbackQuery):
    f_idx = int(callback_query.data.split('_')[1])
    uid = str(callback_query.from_user.id)
    date_now = datetime.now().strftime("%d.%m.%Y")
    users_db[uid][f_idx]["history"].append({"date": date_now, "item": "Предфильтры (1-3)"})
    await sync_gist("save")
    await bot.edit_message_text(f"✅ Весь комплект предфильтров заменен!\nДата: {date_now}", callback_query.message.chat.id, callback_query.message.message_id)

@dp.callback_query_handler(lambda c: c.data.startswith("rep_"))
async def handle_single_rep(callback_query: types.CallbackQuery):
    _, code, f_idx = callback_query.data.split('_')
    uid = str(callback_query.from_user.id)
    f = users_db[uid][int(f_idx)]
    item_name = FILTER_CONFIGS[f["category"]][code]["name"]
    date_now = datetime.now().strftime("%d.%m.%Y")
    f["history"].append({"date": date_now, "item": item_name})
    await sync_gist("save")
    await bot.edit_message_text(f"✅ Замена {item_name} отмечена сегодня!", callback_query.message.chat.id, callback_query.message.message_id)

@dp.callback_query_handler(lambda c: c.data == "cancel")
async def process_cancel(callback_query: types.CallbackQuery):
    await bot.delete_message(callback_query.message.chat.id, callback_query.message.message_id)

# --- ЗАПУСК ---
async def start_webserver():
    app = web.Application()
    app.router.add_get("/", lambda r: web.Response(text="Bot is Live"))
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', int(os.environ.get("PORT", 10000))).start()

async def on_startup(dp):
    await sync_gist("load")
    await bot.delete_webhook(drop_pending_updates=True)
    asyncio.create_task(start_webserver())

if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True, on_startup=on_startup)
