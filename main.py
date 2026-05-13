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

# --- БАЗА ДАННЫХ GIST ---
async def sync_gist(action="load"):
    global users_db
    if not GIST_ID or not GITHUB_TOKEN: return
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
            elif action == "save":
                payload = {"files": {"filters_db.json": {"content": json.dumps(users_db, ensure_ascii=False, indent=2)}}}
                await session.patch(url, headers=headers, json=payload)
        except Exception as e: logging.error(f"Gist Error: {e}")

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
        "Гейзер": ["Макс", "Стандарт", "БИО"],
        "Аквафор": ["Кристалл Эко", "Трио", "Кристалл Н"]
    },
    "flow": {
        "Аквафор": ["Фаворит", "Модерн", "Викинг 10SL"],
        "Гейзер": ["Тайфун 10SL", "Тайфун 10BB"],
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
    kb.add(types.InlineKeyboardButton("📝 Свой вариант", callback_data=f"man_{cat}"))
    kb.add(types.InlineKeyboardButton("⬅️ Назад", callback_data="back_cats"))
    return kb

def get_models_kb(cat, brand):
    kb = types.InlineKeyboardMarkup(row_width=1)
    for idx, m in enumerate(CATALOGS[cat][brand]):
        kb.add(types.InlineKeyboardButton(m, callback_data=f"mod_{cat}_{brand}_{idx}"))
    kb.add(types.InlineKeyboardButton("📝 Свой вариант", callback_data=f"man_{cat}"))
    kb.add(types.InlineKeyboardButton("⬅️ Назад", callback_data=f"cat_{cat}"))
    return kb

def get_date_kb(code, f_idx):
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("📅 Сегодня", callback_data=f"dt_now_{code}_{f_idx}"),
           types.InlineKeyboardButton("✍️ Ввести дату вручную", callback_data=f"dt_man_{code}_{f_idx}"),
           types.InlineKeyboardButton("❌ Отмена", callback_data="cancel"))
    return kb

# --- ОБРАБОТЧИКИ ---

@dp.message_handler(commands=['start'])
async def cmd_start(message: types.Message):
    await message.answer("💧 Сервис контроля фильтров готов!", reply_markup=get_main_menu())
    await message.answer("Какую систему добавим?", reply_markup=get_categories_kb())

@dp.message_handler(lambda m: m.text == "➕ Добавить фильтр")
async def add_filter(message: types.Message):
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
    if cat == "osmos" and not any(x in model_name.lower() for x in ["m", "мин", "морион"]):
        intervals["min"] = 0

    if uid not in users_db: users_db[uid] = []
    users_db[uid].append({"model": f"{brand} {model_name}", "category": cat, "intervals": intervals, "history": [], "created_at": datetime.now().strftime("%d.%m.%Y")})
    await sync_gist("save")
    
    hint = f"✅ <b>{brand} {model_name}</b> добавлена!\n\n"
    hint += "ℹ️ <i>Рекомендуемый срок службы картриджей установлен автоматически. Если вы хотите его изменить под свой расход воды, перейдите в меню «⚙️ Настройки» -> «Изменить интервалы».</i>"
    await bot.send_message(uid, hint, reply_markup=get_main_menu())

# --- СВОЙ ВАРИАНТ ---
@dp.callback_query_handler(lambda c: c.data.startswith("man_"))
async def manual_input_start(callback_query: types.CallbackQuery, state: FSMContext):
    cat = callback_query.data.split('_')[1]
    await state.update_data(manual_cat=cat)
    await FilterStates.waiting_for_manual_name.set()
    # Скрываем клавиатуру меню
    await bot.send_message(callback_query.from_user.id, "📝 Введите название фильтра (например: Мой Осмос 500):", reply_markup=types.ReplyKeyboardRemove())

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
    
    comp_code = data['manual_comps'][idx]
    comp_info = FILTER_CONFIGS[cat][comp_code]
    await FilterStates.waiting_for_custom_interval.set()
    text = (f"⏱ <b>Укажите ресурс для: {comp_info['name']}</b>\n\n"
            f"💡 Рекомендуемый период замены: {comp_info['interval']} мес.\n"
            f"Напишите через сколько месяцев вы планируете замену. Если этого фильтра у вас нет, напишите 0.")
    await message.answer(text)

@dp.message_handler(state=FilterStates.waiting_for_custom_interval)
async def process_manual_int(message: types.Message, state: FSMContext):
    if not message.text.isdigit(): return await message.answer("⚠️ Пожалуйста, введите только число.")
    data = await state.get_data()
    data['manual_ints'][data['manual_comps'][data['manual_idx']]] = int(message.text)
    await state.update_data(manual_ints=data['manual_ints'], manual_idx=data['manual_idx'] + 1)
    await ask_manual_int(message, state)

# --- СТАТУС И КНОПКА КУПИТЬ ---
@dp.message_handler(lambda m: m.text == "📊 Статус")
async def cmd_status(message: types.Message):
    uid = str(message.from_user.id)
    if uid not in users_db or not users_db[uid]: return await message.answer("У вас нет фильтров.")
    
    res = "📊 <b>ТЕКУЩИЙ СТАТУС:</b>\n\n"
    now = datetime.now()
    needing_buy = []
    
    for i, f in enumerate(users_db[uid]):
        res += f"🚰 <b>{f['model']}</b>\n"
        for code, interval in f['intervals'].items():
            if interval == 0: continue
            name = FILTER_CONFIGS[f['category']][code]['name']
            last_date_str = next((h['date'] for h in reversed(f['history']) if h['item'] == name), f['created_at'])
            last_date = datetime.strptime(last_date_str, "%d.%m.%Y")
            days_left = (last_date + timedelta(days=interval * 30.4) - now).days
            
            icon = "🟢"
            if days_left <= 0: icon = "🔴"; needing_buy.append(i)
            elif days_left <= 30: icon = "🟡"; needing_buy.append(i)
            
            res += f"  {icon} {name}: {days_left} дн.\n"
        res += "\n"
    
    kb = None
    if needing_buy:
        kb = types.InlineKeyboardMarkup(row_width=1)
        unique_idxs = list(set(needing_buy))
        for idx in unique_idxs:
            kb.add(types.InlineKeyboardButton(f"🛒 Купить для {users_db[uid][idx]['model'][:15]}...", callback_data=f"buy_{idx}"))
    
    await message.answer(res, reply_markup=kb)

# --- ЗАМЕНА С ВЫБОРОМ ДАТЫ ---
@dp.message_handler(lambda m: m.text == "📅 Заменил картридж")
async def cmd_replace(message: types.Message):
    uid = str(message.from_user.id)
    if uid not in users_db or not users_db[uid]: return
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
    for code, interval in f['intervals'].items():
        if interval > 0:
            kb.add(types.InlineKeyboardButton(FILTER_CONFIGS[f['category']][code]['name'], callback_data=f"choice_{code}_{f_idx}"))
    await bot.edit_message_text("Что именно заменили?", callback_query.message.chat.id, callback_query.message.message_id, reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith("choice_"))
async def date_choice(callback_query: types.CallbackQuery):
    _, code, f_idx = callback_query.data.split('_')
    await bot.edit_message_text("Когда была произведена замена?", callback_query.message.chat.id, callback_query.message.message_id, reply_markup=get_date_kb(code, f_idx))

@dp.callback_query_handler(lambda c: c.data.startswith("dt_now_"))
async def rep_now(callback_query: types.CallbackQuery):
    _, _, code, f_idx = callback_query.data.split('_')
    uid = str(callback_query.from_user.id)
    name = FILTER_CONFIGS[users_db[uid][int(f_idx)]['category']][code]['name']
    date = datetime.now().strftime("%d.%m.%Y")
    users_db[uid][int(f_idx)]['history'].append({"date": date, "item": name})
    await sync_gist("save")
    await bot.edit_message_text(f"✅ Замена {name} сохранена сегодня!", callback_query.message.chat.id, callback_query.message.message_id)

@dp.callback_query_handler(lambda c: c.data.startswith("dt_man_"))
async def rep_manual(callback_query: types.CallbackQuery, state: FSMContext):
    _, _, code, f_idx = callback_query.data.split('_')
    await state.update_data(rep_code=code, rep_fidx=int(f_idx))
    await FilterStates.waiting_for_date.set()
    await bot.send_message(callback_query.from_user.id, "✍️ Введите дату замены в формате ДД.ММ.ГГГГ (например, 15.05.2023):")

@dp.message_handler(state=FilterStates.waiting_for_date)
async def process_manual_date(message: types.Message, state: FSMContext):
    try:
        valid_date = datetime.strptime(message.text, "%d.%m.%Y").strftime("%d.%m.%Y")
        data = await state.get_data()
        uid = str(message.from_user.id)
        name = FILTER_CONFIGS[users_db[uid][data['rep_fidx']]['category']][data['rep_code']]['name']
        users_db[uid][data['rep_fidx']]['history'].append({"date": valid_date, "item": name})
        await sync_gist("save")
        await state.finish()
        await message.answer(f"✅ Замена {name} сохранена на дату {valid_date}!", reply_markup=get_main_menu())
    except: await message.answer("⚠️ Неверный формат. Введите дату как ДД.ММ.ГГГГ")

# --- ОСТАЛЬНОЕ ---
@dp.callback_query_handler(lambda c: c.data.startswith("buy_"))
async def buy_links(callback_query: types.CallbackQuery):
    idx = int(callback_query.data.split('_')[1])
    model = users_db[str(callback_query.from_user.id)][idx]['model']
    query = quote(f"картриджи {model}")
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("🛒 На Ozon", url=f"https://www.ozon.ru/search/?text={query}"),
           types.InlineKeyboardButton("🟣 На Wildberries", url=f"https://www.wildberries.ru/catalog/0/search.aspx?search={query}"))
    await bot.send_message(callback_query.from_user.id, f"🛒 Ссылки для <b>{model}</b>:", reply_markup=kb)

@dp.message_handler(lambda m: m.text == "📜 История")
async def cmd_history(message: types.Message):
    uid = str(message.from_user.id)
    if uid not in users_db or not users_db[uid]: return await message.answer("История пуста.")
    res = "📜 <b>ИСТОРИЯ ЗАМЕН:</b>\n\n"
    for f in users_db[uid]:
        res += f"🚰 <b>{f['model']}</b>:\n"
        for h in f['history'][-5:]: res += f"  ▫️ {h['date']} — {h['item']}\n"
    await message.answer(res)

@dp.callback_query_handler(lambda c: c.data == "cancel")
async def cancel_cb(callback_query: types.CallbackQuery):
    await bot.delete_message(callback_query.message.chat.id, callback_query.message.message_id)

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
