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
                        logging.info("База данных загружена")
            elif action == "save":
                payload = {"files": {"filters_db.json": {"content": json.dumps(users_db, ensure_ascii=False, indent=2)}}}
                await session.patch(url, headers=headers, json=payload)
                logging.info("Данные синхронизированы")
        except Exception as e:
            logging.error(f"Gist Error: {e}")

# --- КАТАЛОГ ---
CATALOGS = {
    "osmos": {
        "Атолл": ["A-550", "A-575", "A-550m", "A-575m", "A-450"],
        "Барьер": ["Профи Осмо 100", "Профи Осмо 100 М", "Compact Osmo"],
        "Гейзер": ["Престиж", "Аллегро", "Престиж М", "Аллегро М"],
        "Аквафор": ["DWM-101S Морион", "DWM-102S", "Осмо Про 50", "Осмо Про 50 М"]
    },
    "stage3": {
        "Атолл": ["Патриот", "D-31"],
        "Барьер": ["Профи Стандарт", "Профи Смягчение"],
        "Гейзер": ["Макс", "Стандарт", "БИО"],
        "Аквафор": ["Кристалл Эко", "Трио", "Кристалл Н"]
    },
    "flow": {
        "Аквафор": ["Фаворит", "Модерн", "Викинг 10SL"],
        "Гейзер": ["Тайфун 10SL", "Тайфун 10BB"],
        "Барьер": ["In-Line Механика"],
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
    kb.add(types.InlineKeyboardButton("💧 Обратный осмос", callback_data="cat_osmos"),
           types.InlineKeyboardButton("🧪 3-ступенчатый", callback_data="cat_stage3"),
           types.InlineKeyboardButton("🚰 Магистральный / Проточный", callback_data="cat_flow"))
    return kb

def get_brands_kb(cat):
    kb = types.InlineKeyboardMarkup(row_width=2)
    for b in CATALOGS[cat].keys():
        kb.insert(types.InlineKeyboardButton(b, callback_data=f"br_{cat}_{b}"))
    kb.add(types.InlineKeyboardButton("📝 Свой вариант", callback_data=f"man_{cat}"),
           types.InlineKeyboardButton("⬅️ Назад", callback_data="back_cats"))
    return kb

def get_models_kb(cat, brand):
    kb = types.InlineKeyboardMarkup(row_width=1)
    for idx, m in enumerate(CATALOGS[cat][brand]):
        kb.add(types.InlineKeyboardButton(m, callback_data=f"mod_{cat}_{brand}_{idx}"))
    kb.add(types.InlineKeyboardButton("📝 Свой вариант", callback_data=f"man_{cat}"),
           types.InlineKeyboardButton("⬅️ Назад", callback_data=f"cat_{cat}"))
    return kb

def get_settings_kb():
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("⏱ Изменить интервалы", callback_data="set_ints"),
           types.InlineKeyboardButton("🗑 Удалить систему", callback_data="set_del"),
           types.InlineKeyboardButton("🧨 Очистить всё", callback_data="set_clear"))
    return kb

def get_market_kb(model):
    query = quote(f"картриджи {model}")
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("🛒 На Ozon", url=f"https://www.ozon.ru/search/?text={query}"),
           types.InlineKeyboardButton("🟣 На Wildberries", url=f"https://www.wildberries.ru/catalog/0/search.aspx?search={query}"))
    return kb

# --- ОБРАБОТЧИКИ ТЕКСТОВОГО МЕНЮ ---

@dp.message_handler(commands=['start'])
async def cmd_start(message: types.Message):
    await message.answer("💧 Сервис контроля фильтров запущен!", reply_markup=get_main_menu())
    await message.answer("Какую систему добавим?", reply_markup=get_categories_kb())

@dp.message_handler(lambda m: m.text == "➕ Добавить фильтр")
async def add_filter(message: types.Message):
    await message.answer("Выберите тип системы:", reply_markup=get_categories_kb())

@dp.message_handler(lambda m: m.text == "📊 Статус")
async def cmd_status(message: types.Message):
    uid = str(message.from_user.id)
    if uid not in users_db or not users_db[uid]:
        return await message.answer("У вас нет фильтров. Добавьте систему через «➕ Добавить фильтр»")
    
    res = "📊 <b>ТЕКУЩИЙ СТАТУС:</b>\n\n"
    now = datetime.now()
    for i, f in enumerate(users_db[uid]):
        res += f"🚰 <b>{i}. {f['model']}</b>\n"
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

@dp.message_handler(lambda m: m.text == "📜 История")
async def cmd_history(message: types.Message):
    uid = str(message.from_user.id)
    if uid not in users_db or not users_db[uid]: return await message.answer("История пуста.")
    res = "📜 <b>ИСТОРИЯ ЗАМЕН:</b>\n\n"
    for f in users_db[uid]:
        res += f"🚰 <b>{f['model']}</b>:\n"
        if not f['history']: 
            res += "  <i>Замен пока не было</i>\n"
        else:
            for h in f['history'][-5:]:
                res += f"  ▫️ {h['date']} — {h['item']}\n"
        res += "\n"
    await message.answer(res)

@dp.message_handler(lambda m: m.text == "🛒 Купить картриджи")
async def cmd_buy(message: types.Message):
    uid = str(message.from_user.id)
    if uid not in users_db or not users_db[uid]: return await message.answer("Сначала добавьте фильтр.")
    kb = types.InlineKeyboardMarkup(row_width=1)
    for i, f in enumerate(users_db[uid]):
        kb.add(types.InlineKeyboardButton(f['model'], callback_data=f"buy_{i}"))
    await message.answer("Для какой системы ищем?", reply_markup=kb)

@dp.message_handler(lambda m: m.text == "⚙️ Настройки")
async def cmd_settings(message: types.Message):
    await message.answer("⚙️ <b>Настройки:</b>", reply_markup=get_settings_kb())

# --- CALLBACK ОБРАБОТЧИКИ НАВИГАЦИИ ---

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
    # Логика минерализатора
    if cat == "osmos" and not any(x in model_name.lower() for x in ["m", "мин", "морион"]):
        intervals["min"] = 0
    if uid not in users_db: users_db[uid] = []
    users_db[uid].append({"model": f"{brand} {model_name}", "category": cat, "intervals": intervals, "history": [], "created_at": datetime.now().strftime("%d.%m.%Y")})
    await sync_gist("save")
    await bot.send_message(uid, f"✅ <b>{brand} {model_name}</b> добавлена!", reply_markup=get_main_menu())

# --- CALLBACK ОБРАБОТЧИКИ НАСТРОЕК ---

@dp.callback_query_handler(lambda c: c.data == "set_del")
async def set_del_start(callback_query: types.CallbackQuery):
    uid = str(callback_query.from_user.id)
    kb = types.InlineKeyboardMarkup(row_width=1)
    for i, f in enumerate(users_db.get(uid, [])):
        kb.add(types.InlineKeyboardButton(f"Удалить {f['model']}", callback_data=f"del_{i}"))
    kb.add(types.InlineKeyboardButton("❌ Отмена", callback_data="cancel"))
    await bot.edit_message_text("Какую систему удалить?", callback_query.message.chat.id, callback_query.message.message_id, reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith("del_"))
async def set_del_confirm(callback_query: types.CallbackQuery):
    idx = int(callback_query.data.split('_')[1])
    uid = str(callback_query.from_user.id)
    deleted = users_db[uid].pop(idx)
    await sync_gist("save")
    await bot.answer_callback_query(callback_query.id, f"Удалено: {deleted['model']}")
    await bot.edit_message_text(f"🗑 Система {deleted['model']} удалена.", callback_query.message.chat.id, callback_query.message.message_id)

@dp.callback_query_handler(lambda c: c.data == "set_clear")
async def set_clear(callback_query: types.CallbackQuery):
    users_db[str(callback_query.from_user.id)] = []
    await sync_gist("save")
    await bot.edit_message_text("🧨 Все данные очищены.", callback_query.message.chat.id, callback_query.message.message_id)

# --- CALLBACK ОБРАБОТЧИКИ ЗАМЕНЫ ---

@dp.message_handler(lambda m: m.text == "📅 Заменил картридж")
async def cmd_replace(message: types.Message):
    uid = str(message.from_user.id)
    if uid not in users_db or not users_db[uid]: return await message.answer("Сначала добавьте фильтр.")
    kb = types.InlineKeyboardMarkup(row_width=1)
    for i, f in enumerate(users_db[uid]):
        kb.add(types.InlineKeyboardButton(f['model'], callback_data=f"selrep_{i}"))
    await message.answer("В какой системе замена?", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith("selrep_"))
async def process_selrep(callback_query: types.CallbackQuery):
    f_idx = int(callback_query.data.split('_')[1])
    uid = str(callback_query.from_user.id)
    f = users_db[uid][f_idx]
    kb = types.InlineKeyboardMarkup(row_width=1)
    if f['category'] == "osmos":
        kb.add(types.InlineKeyboardButton("🔄 ВЕСЬ КОМПЛЕКТ (1-3)", callback_data=f"repall_{f_idx}"))
    for code, interval in f['intervals'].items():
        if interval > 0:
            name = FILTER_CONFIGS[f['category']][code]['name']
            kb.add(types.InlineKeyboardButton(name, callback_data=f"rep_{code}_{f_idx}"))
    await bot.edit_message_text("Что заменили?", callback_query.message.chat.id, callback_query.message.message_id, reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith("repall_"))
async def rep_all(callback_query: types.CallbackQuery):
    idx = int(callback_query.data.split('_')[1])
    uid = str(callback_query.from_user.id)
    date = datetime.now().strftime("%d.%m.%Y")
    users_db[uid][idx]['history'].append({"date": date, "item": "Предфильтры (1-3)"})
    await sync_gist("save")
    await bot.edit_message_text(f"✅ Замена комплекта сохранена!", callback_query.message.chat.id, callback_query.message.message_id)

@dp.callback_query_handler(lambda c: c.data.startswith("rep_"))
async def rep_single(callback_query: types.CallbackQuery):
    _, code, idx = callback_query.data.split('_')
    uid = str(callback_query.from_user.id)
    cat = users_db[uid][int(idx)]['category']
    item = FILTER_CONFIGS[cat][code]['name']
    users_db[uid][int(idx)]['history'].append({"date": datetime.now().strftime("%d.%m.%Y"), "item": item})
    await sync_gist("save")
    await bot.edit_message_text(f"✅ Замена {item} сохранена!", callback_query.message.chat.id, callback_query.message.message_id)

@dp.callback_query_handler(lambda c: c.data.startswith("buy_"))
async def buy_select(callback_query: types.CallbackQuery):
    idx = int(callback_query.data.split('_')[1])
    model = users_db[str(callback_query.from_user.id)][idx]['model']
    await bot.edit_message_text(f"🛒 Ссылки для <b>{model}</b>:", callback_query.message.chat.id, callback_query.message.message_id, reply_markup=get_market_kb(model))

@dp.callback_query_handler(lambda c: c.data == "cancel")
async def cancel(callback_query: types.CallbackQuery):
    await bot.delete_message(callback_query.message.chat.id, callback_query.message.message_id)

# --- FSM ОБРАБОТЧИКИ (СВОЙ ВАРИАНТ) ---

@dp.callback_query_handler(lambda c: c.data.startswith("man_"))
async def manual_input_start(callback_query: types.CallbackQuery, state: FSMContext):
    cat = callback_query.data.split('_')[1]
    await state.update_data(manual_cat=cat)
    await FilterStates.waiting_for_manual_name.set()
    await bot.send_message(callback_query.from_user.id, "📝 Введите название фильтра:")

@dp.message_handler(state=FilterStates.waiting_for_manual_name)
async def manual_name(message: types.Message, state: FSMContext):
    data = await state.get_data()
    await state.update_data(manual_name=message.text, manual_idx=0, manual_ints={}, manual_comps=list(FILTER_CONFIGS[data['manual_cat']].keys()))
    await ask_manual_int(message, state)

async def ask_manual_int(message, state):
    data = await state.get_data()
    cat, idx = data['manual_cat'], data['manual_idx']
    if idx >= len(data['manual_comps']):
        uid = str(message.from_user.id)
        if uid not in users_db: users_db[uid] = []
        users_db[uid].append({"model": data['manual_name'], "category": cat, "intervals": data['manual_ints'], "history": [], "created_at": datetime.now().strftime("%d.%m.%Y")})
        await sync_gist("save")
        await state.finish()
        return await message.answer(f"✅ Фильтр {data['manual_name']} добавлен!", reply_markup=get_main_menu())
    name = FILTER_CONFIGS[cat][data['manual_comps'][idx]]['name']
    await FilterStates.waiting_for_custom_interval.set()
    await message.answer(f"⏱ Ресурс для <b>{name}</b> (мес, 0 - нет):")

@dp.message_handler(state=FilterStates.waiting_for_custom_interval)
async def process_manual_int(message: types.Message, state: FSMContext):
    if not message.text.isdigit(): return await message.answer("⚠️ Введите цифру.")
    data = await state.get_data()
    data['manual_ints'][data['manual_comps'][data['manual_idx']]] = int(message.text)
    await state.update_data(manual_ints=data['manual_ints'], manual_idx=data['manual_idx'] + 1)
    await ask_manual_int(message, state)

# --- ЗАПУСК ---
async def start_webserver():
    app = web.Application()
    app.router.add_get("/", lambda r: web.Response(text="Bot Alive"))
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', int(os.environ.get("PORT", 10000))).start()

async def on_startup(dp):
    await sync_gist("load")
    await bot.delete_webhook(drop_pending_updates=True)
    asyncio.create_task(start_webserver())

if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True, on_startup=on_startup)
