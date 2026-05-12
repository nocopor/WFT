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

# --- КАТАЛОГ ---
CATALOGS = {
    "osmos": {
        "Аквафор": ["DWM-101S Морион", "Осмо Про 50", "Осмо 50 ПН"],
        "Гейзер": ["Престиж", "Аллегро", "Маэстро"],
        "Атолл": ["A-550", "A-575", "A-450"],
        "Барьер": ["Профи Осмо 100", "Compact Osmo"],
        "Prio (Новая Вода)": ["Эксперт Osmos MO530", "Start Osmos"],
        "Другие": ["Xiaomi Mi Water", "Экософт Стандарт"]
    },
    "stage3": {
        "Аквафор": ["Кристалл", "Трио"],
        "Гейзер": ["Макс", "Стандарт", "Классик", "БИО"],
        "Барьер": ["Эксперт", "Профи"],
        "Prio (Новая Вода)": ["Expert M310", "Praktic EU310"],
        "Атолл": ["Патриот", "D-31"]
    },
    "flow": {
        "Аквафор": ["Фаворит", "Модерн", "Викинг 10SL", "Викинг 10BB", "Викинг 20BB"],
        "Гейзер": ["Тайфун 10SL", "Тайфун 10BB", "Тайфун 20BB", "1УЖ Евро"],
        "Барьер": ["In-Line", "ВМ 1/2"],
        "Джилекс": ["Колба 10SL", "Колба 10BB", "Колба 20BB"]
    }
}

FILTER_CONFIGS = {
    "osmos": {
        "pre": {"name": "Предфильтры", "interval": 6},
        "mem": {"name": "Мембрана", "interval": 24},
        "post": {"name": "Постфильтр", "interval": 12}
    },
    "stage3": {
        "set": {"name": "Комплект картриджей", "interval": 12}
    },
    "flow": {
        "cart": {"name": "Сменный модуль", "interval": 6}
    }
}

class FilterStates(StatesGroup):
    waiting_for_manual_name = State()
    waiting_for_date = State()
    waiting_for_interval = State()

bot = Bot(token=TOKEN, parse_mode=types.ParseMode.HTML)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

# --- ВЕБ-СЕРВЕР ---
async def handle(request):
    return web.Response(text="Bot is running!")

async def start_webserver():
    app = web.Application()
    app.router.add_get("/", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 10000)
    await site.start()

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
        types.InlineKeyboardButton(text="🗑 Очистить профиль", callback_data="set_clear")
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
        kb.insert(types.InlineKeyboardButton(text=brand, callback_data=f"br_{category_key}_{brand}"))
    kb.add(types.InlineKeyboardButton(text="📝 Свой вариант", callback_data=f"manual_{category_key}"))
    kb.add(types.InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_cats"))
    return kb

def get_models_kb(category_key, brand_key):
    kb = types.InlineKeyboardMarkup(row_width=1)
    models = CATALOGS[category_key][brand_key]
    for idx, model in enumerate(models):
        kb.add(types.InlineKeyboardButton(text=model, callback_data=f"mod_{category_key}_{brand_key}_{idx}"))
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
    category = users_db[user_id][filter_index]["category"]
    components = FILTER_CONFIGS[category]
    for code, comp_data in components.items():
        kb.add(types.InlineKeyboardButton(text=f"🔄 {comp_data['name']}", callback_data=f"rep_{code}_{filter_index}"))
    kb.add(types.InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_action"))
    return kb

def get_intervals_kb(user_id, filter_index):
    kb = types.InlineKeyboardMarkup(row_width=1)
    filter_data = users_db[user_id][filter_index]
    category = filter_data["category"]
    intervals = filter_data["intervals"]
    components = FILTER_CONFIGS[category]
    for code, comp_data in components.items():
        current_int = intervals[code]
        kb.add(types.InlineKeyboardButton(text=f"{comp_data['name']} ({current_int} мес) ✏️", callback_data=f"editint_{code}_{filter_index}"))
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
    ozon_url = f"https://www.ozon.ru/search/?text={query}"
    wb_url = f"https://www.wildberries.ru/catalog/0/search.aspx?search={query}"
    
    kb.add(
        types.InlineKeyboardButton(text="🛒 Найти на Ozon", url=ozon_url),
        types.InlineKeyboardButton(text="🟣 Найти на Wildberries", url=wb_url)
    )
    return kb

# --- ДОБАВЛЕНИЕ СИСТЕМЫ ---
def add_new_filter(user_id, model_name, category):
    if user_id not in users_db:
        users_db[user_id] = []
    intervals = {code: data["interval"] for code, data in FILTER_CONFIGS[category].items()}
    users_db[user_id].append({
        "model": model_name, 
        "category": category,
        "created_at": datetime.now().strftime("%d.%m.%Y"),
        "history": [],
        "intervals": intervals,
        "notified": {} # Словарь для контроля отправленных уведомлений
    })

@dp.message_handler(commands=['start'])
async def cmd_start(message: types.Message):
    text = (
        "💧 <b>Добро пожаловать!</b>\n\n"
        "Я помогу вовремя обслуживать ваши фильтры для воды.\n"
        "<i>Какую систему добавим в ваш профиль?</i>"
    )
    await message.answer(text, reply_markup=get_categories_kb())

@dp.message_handler(lambda m: m.text == "➕ Добавить фильтр")
async def menu_add_filter(message: types.Message):
    await message.answer("<b>Выберите тип новой системы фильтрации:</b>", reply_markup=get_categories_kb())

@dp.callback_query_handler(lambda c: c.data == 'cancel_action')
async def handle_cancel(callback_query: types.CallbackQuery):
    await bot.delete_message(callback_query.message.chat.id, callback_query.message.message_id)

@dp.callback_query_handler(lambda c: c.data == 'back_to_cats')
@dp.callback_query_handler(lambda c: c.data.startswith('cat_'))
async def process_category(callback_query: types.CallbackQuery):
    if callback_query.data == 'back_to_cats':
        await bot.edit_message_text("<b>Выберите тип системы:</b>", callback_query.message.chat.id, callback_query.message.message_id, reply_markup=get_categories_kb())
    else:
        cat_key = callback_query.data.split('_')[1]
        await bot.edit_message_text("<b>Выберите производителя:</b>", callback_query.message.chat.id, callback_query.message.message_id, reply_markup=get_brands_kb(cat_key))

@dp.callback_query_handler(lambda c: c.data.startswith('back_br_'))
@dp.callback_query_handler(lambda c: c.data.startswith('br_'))
async def process_brand(callback_query: types.CallbackQuery):
    if callback_query.data.startswith('back_br_'):
        cat_key = callback_query.data.split('_')[2]
        await bot.edit_message_text("<b>Выберите производителя:</b>", callback_query.message.chat.id, callback_query.message.message_id, reply_markup=get_brands_kb(cat_key))
    else:
        parts = callback_query.data.split('_')
        cat_key = parts[1]
        brand_key = parts[2]
        await bot.edit_message_text(f"Производитель: <b>{brand_key}</b>\n<i>Выберите модель:</i>", callback_query.message.chat.id, callback_query.message.message_id, reply_markup=get_models_kb(cat_key, brand_key))

@dp.callback_query_handler(lambda c: c.data.startswith('mod_'))
async def process_model(callback_query: types.CallbackQuery):
    parts = callback_query.data.split('_')
    category = parts[1]
    brand = parts[2]
    model_idx = int(parts[3])
    
    model_name = CATALOGS[category][brand][model_idx]
    full_model_name = f"{brand} {model_name}"
    
    add_new_filter(callback_query.from_user.id, full_model_name, category)
    
    intervals_text = ""
    for comp_data in FILTER_CONFIGS[category].values():
        intervals_text += f"  ▫️ {comp_data['name']}: {comp_data['interval']} мес.\n"
    
    text = (
        f"✅ Система добавлена: <b>{full_model_name}</b>\n\n"
        f"💡 <b>Рекомендуемый ресурс:</b>\n{intervals_text}\n"
        f"<i>⚙️ Изменить сроки можно в меню «Настройки».</i>\n\n"
        f"Отсчет ресурса начат."
    )
    await bot.edit_message_text(text, callback_query.message.chat.id, callback_query.message.message_id)
    await bot.send_message(callback_query.from_user.id, "Главное меню:", reply_markup=get_main_menu())

# --- УМНЫЙ СТАТУС С КНОПКАМИ ПОКУПКИ ---
@dp.message_handler(lambda m: m.text == "📊 Статус")
async def menu_status(message: types.Message):
    user_filters = users_db.get(message.from_user.id, [])
    if not user_filters:
        return await message.answer("У вас нет добавленных систем. Нажмите «➕ Добавить фильтр».")
    
    text = "📊 <b>ТЕКУЩИЙ СТАТУС СИСТЕМ</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
    now = datetime.now()
    filters_needing_replacement = set()
    
    for i, f in enumerate(user_filters, 1):
        text += f"🚰 <b>{i}. {f['model']}</b>\n"
        category = f["category"]
        intervals = f["intervals"]
        history = f["history"]
        components = FILTER_CONFIGS[category]
        
        sorted_history = sorted(history, key=lambda x: datetime.strptime(x['date'], "%d.%m.%Y"))
        
        for code, comp_data in components.items():
            name = comp_data["name"]
            last_rep = next((item for item in reversed(sorted_history) if item["item"] == name), None)
            
            if last_rep:
                last_date = datetime.strptime(last_rep["date"], "%d.%m.%Y")
            else:
                created_at_str = f.get("created_at", now.strftime("%d.%m.%Y"))
                last_date = datetime.strptime(created_at_str, "%d.%m.%Y")
                
            next_rep_date = last_date + timedelta(days=intervals[code] * 30.4)
            days_left = (next_rep_date - now).days
            
            if days_left > 30:
                text += f"  ├ <b>{name}:</b> 🟢 Норма\n  └ <i>Замена через ~{days_left} дн.</i>\n"
            elif 0 <= days_left <= 30:
                text += f"  ├ <b>{name}:</b> 🟡 Скоро замена\n  └ <i>Осталось: {days_left} дн.</i>\n"
                filters_needing_replacement.add(i-1)
            else:
                text += f"  ├ <b>{name}:</b> 🔴 <b>ПРОСРОЧЕНО</b>\n  └ <i>На {abs(days_left)} дн.</i>\n"
                filters_needing_replacement.add(i-1)
        text += "\n"
        
    if filters_needing_replacement:
        kb = types.InlineKeyboardMarkup(row_width=1)
        for f_idx in filters_needing_replacement:
            model_name = user_filters[f_idx]['model']
            short_model = model_name if len(model_name) <= 25 else model_name[:22] + "..."
            kb.add(types.InlineKeyboardButton(text=f"🛒 Купить картриджи для: {short_model}", callback_data=f"selbuy_{f_idx}"))
        await message.answer(text, reply_markup=kb)
    else:
        await message.answer(text)

# --- НАСТРОЙКИ ---
@dp.message_handler(lambda m: m.text == "⚙️ Настройки")
async def menu_settings(message: types.Message):
    text = (
        "⚙️ <b>НАСТРОЙКИ ПРОФИЛЯ</b>\n\n"
        "<i>Здесь вы можете изменить стандартные сроки "
        "замены картриджей под вашу воду или полностью очистить данные.</i>"
    )
    await message.answer(text, reply_markup=get_settings_kb())

@dp.callback_query_handler(lambda c: c.data == 'set_intervals')
async def settings_intervals(callback_query: types.CallbackQuery):
    user_filters = users_db.get(callback_query.from_user.id, [])
    if not user_filters:
        return await callback_query.answer("Нет добавленных фильтров", show_alert=True)
    if len(user_filters) == 1:
        await bot.edit_message_text(f"⏱ <b>Интервалы:</b> {user_filters[0]['model']}\n<i>Выберите ступень:</i>", callback_query.message.chat.id, callback_query.message.message_id, reply_markup=get_intervals_kb(callback_query.from_user.id, 0))
    else:
        await bot.edit_message_text("<b>Для какой системы настроим интервалы?</b>", callback_query.message.chat.id, callback_query.message.message_id, reply_markup=get_user_filters_kb(callback_query.from_user.id, 'selint'))

@dp.callback_query_handler(lambda c: c.data.startswith('selint_'))
async def process_selint(callback_query: types.CallbackQuery):
    f_idx = int(callback_query.data.split('_')[1])
    model = users_db[callback_query.from_user.id][f_idx]['model']
    await bot.edit_message_text(f"⏱ <b>Интервалы:</b> {model}\n<i>Выберите ступень:</i>", callback_query.message.chat.id, callback_query.message.message_id, reply_markup=get_intervals_kb(callback_query.from_user.id, f_idx))

@dp.callback_query_handler(lambda c: c.data.startswith('editint_'))
async def process_editint(callback_query: types.CallbackQuery, state: FSMContext):
    parts = callback_query.data.split('_')
    item_code = parts[1]
    f_idx = int(parts[2])
    
    await state.update_data(f_idx=f_idx, item_code=item_code)
    await FilterStates.waiting_for_interval.set()
    
    category = users_db[callback_query.from_user.id][f_idx]["category"]
    item_name = FILTER_CONFIGS[category][item_code]["name"]
    
    text = (
        f"Укажите новый интервал замены для <b>{item_name}</b> (в месяцах).\n\n"
        f"<i>Просто отправьте цифру, например:</i> <code>6</code>"
    )
    await bot.edit_message_text(text, callback_query.message.chat.id, callback_query.message.message_id)

@dp.message_handler(state=FilterStates.waiting_for_interval)
async def handle_new_interval(message: types.Message, state: FSMContext):
    if not message.text.isdigit():
        return await message.answer("⚠️ Пожалуйста, введите только цифру (например, 6).")
    
    months = int(message.text)
    data = await state.get_data()
    user_id = message.from_user.id
    
    users_db[user_id][data['f_idx']]["intervals"][data['item_code']] = months
    
    await state.finish()
    await message.answer(f"✅ Новый интервал сохранен: <b>{months} мес.</b>", reply_markup=get_main_menu())

@dp.callback_query_handler(lambda c: c.data == 'set_clear')
async def settings_clear(callback_query: types.CallbackQuery):
    users_db[callback_query.from_user.id] = []
    await bot.edit_message_text("🗑 <b>Профиль очищен.</b>\nДобавьте систему заново.", callback_query.message.chat.id, callback_query.message.message_id)

# --- ЗАМЕНА ---
@dp.message_handler(lambda m: m.text == "📅 Заменил картридж")
async def menu_replacement(message: types.Message):
    user_filters = users_db.get(message.from_user.id, [])
    if not user_filters:
        return await message.answer("Сначала добавьте систему через кнопку «➕ Добавить фильтр».")
    
    if len(user_filters) == 1:
        await message.answer(f"🛠 <b>Система:</b> {user_filters[0]['model']}\n<i>Что именно заменили?</i>", reply_markup=get_replacement_kb(message.from_user.id, 0))
    else:
        await message.answer("<b>В какой системе произвели замену?</b>", reply_markup=get_user_filters_kb(message.from_user.id, 'selrep'))

@dp.callback_query_handler(lambda c: c.data.startswith('selrep_'))
async def process_rep_selection(callback_query: types.CallbackQuery):
    f_idx = int(callback_query.data.split('_')[1])
    model = users_db[callback_query.from_user.id][f_idx]['model']
    await bot.edit_message_text(f"🛠 <b>Система:</b> {model}\n<i>Что именно заменили?</i>", callback_query.message.chat.id, callback_query.message.message_id, reply_markup=get_replacement_kb(callback_query.from_user.id, f_idx))

@dp.callback_query_handler(lambda c: c.data.startswith('rep_'))
async def handle_replace_action(callback_query: types.CallbackQuery):
    parts = callback_query.data.split('_')
    item_code = parts[1]
    f_idx = int(parts[2])
    
    category = users_db[callback_query.from_user.id][f_idx]["category"]
    item_name = FILTER_CONFIGS[category][item_code]["name"]
    
    await bot.edit_message_text(f"Выбрано: <b>{item_name}</b>\n<i>Когда была замена?</i>", callback_query.message.chat.id, callback_query.message.message_id, reply_markup=get_date_choice_kb(item_code, f_idx))

@dp.callback_query_handler(lambda c: c.data.startswith('date_'))
async def process_date_selection(callback_query: types.CallbackQuery, state: FSMContext):
    parts = callback_query.data.split('_')
    action = parts[1]
    item_code = parts[2]
    f_idx = int(parts[3])
    
    user_id = callback_query.from_user.id
    category = users_db[user_id][f_idx]["category"]
    item_name = FILTER_CONFIGS[category][item_code]["name"]
    model_name = users_db[user_id][f_idx]["model"]
    
    if action == "today":
        date_now = datetime.now().strftime("%d.%m.%Y")
        users_db[user_id][f_idx]["history"].append({"date": date_now, "item": item_name})
        text = (
            f"✅ <b>Успешно сохранено!</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🔹 <b>Система:</b> {model_name}\n"
            f"🔹 <b>Элемент:</b> {item_name}\n"
            f"🔹 <b>Дата:</b> <code>{date_now}</code>"
        )
        await bot.edit_message_text(text, callback_query.message.chat.id, callback_query.message.message_id)
    elif action == "manual":
        await state.update_data(f_idx=f_idx, item_name=item_name, model_name=model_name)
        await FilterStates.waiting_for_date.set()
        await bot.edit_message_text(f"Отправьте дату замены для <b>{item_name}</b>\n<i>Формат:</i> <code>ДД.ММ.ГГГГ</code> (например, 15.08.2023):", callback_query.message.chat.id, callback_query.message.message_id)

@dp.message_handler(state=FilterStates.waiting_for_date)
async def manual_date_input(message: types.Message, state: FSMContext):
    try:
        valid_date = datetime.strptime(message.text.strip(), "%d.%m.%Y").strftime("%d.%m.%Y")
    except ValueError:
        return await message.answer("⚠️ Ошибка формата!\n<i>Ожидается:</i> <code>ДД.ММ.ГГГГ</code>")
    
    data = await state.get_data()
    users_db[message.from_user.id][data['f_idx']]["history"].append({"date": valid_date, "item": data['item_name']})
    await state.finish()
    await message.answer(f"✅ Дата <code>{valid_date}</code> успешно сохранена в историю!", reply_markup=get_main_menu())

# --- ИСТОРИЯ И ПОКУПКА ---
@dp.message_handler(lambda m: m.text == "📜 История")
async def menu_history(message: types.Message):
    user_filters = users_db.get(message.from_user.id, [])
    if not user_filters:
        return await message.answer("У вас нет добавленных систем.")
    
    text = "📜 <b>ИСТОРИЯ ОБСЛУЖИВАНИЯ</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
    has_history = False
    for f in user_filters:
        if f["history"]:
            has_history = True
            text += f"🚰 <b>{f['model']}</b>:\n"
            for entry in sorted(f["history"], key=lambda x: datetime.strptime(x['date'], "%d.%m.%Y"), reverse=True)[:5]:
                text += f"  ▫️ <code>{entry['date']}</code> — {entry['item']}\n"
            text += "\n"
    await message.answer(text if has_history else "<i>История пока пуста. Ресурс считается от даты добавления фильтра.</i>")

@dp.message_handler(lambda m: m.text == "🛒 Купить картриджи")
async def menu_buy(message: types.Message):
    user_filters = users_db.get(message.from_user.id, [])
    if not user_filters:
        return await message.answer("Сначала добавьте систему через кнопку «➕ Добавить фильтр».")
    if len(user_filters) == 1:
        await message.answer(f"Где будем искать картриджи для <b>{user_filters[0]['model']}</b>?", reply_markup=get_market_kb(user_filters[0]['model']))
    else:
        await message.answer("<b>Для какой системы ищем?</b>", reply_markup=get_user_filters_kb(message.from_user.id, 'selbuy'))

@dp.callback_query_handler(lambda c: c.data.startswith('selbuy_'))
async def process_buy_sel(callback_query: types.CallbackQuery):
    f_idx = int(callback_query.data.split('_')[1])
    model = users_db[callback_query.from_user.id][f_idx]['model']
    
    await bot.edit_message_text(
        f"🛒 <b>Поиск картриджей</b>\n━━━━━━━━━━━━━━━━━━━━\nСистема: <b>{model}</b>\n\n<i>Выберите маркетплейс:</i>", 
        callback_query.message.chat.id, 
        callback_query.message.message_id, 
        reply_markup=get_market_kb(model)
    )

# --- РУЧНОЙ ВВОД ---
@dp.callback_query_handler(lambda c: c.data.startswith('manual_'))
async def manual_input_start(callback_query: types.CallbackQuery, state: FSMContext):
    category = callback_query.data.split('_')[1]
    await state.update_data(manual_category=category)
    await FilterStates.waiting_for_manual_name.set()
    await bot.send_message(callback_query.from_user.id, "<b>Введите полное название вашей системы:</b>")

@dp.message_handler(state=FilterStates.waiting_for_manual_name)
async def manual_input_done(message: types.Message, state: FSMContext):
    data = await state.get_data()
    category = data.get("manual_category", "osmos")
    
    add_new_filter(message.from_user.id, message.text, category)
    await state.finish()
    
    intervals_text = ""
    for comp_data in FILTER_CONFIGS[category].values():
        intervals_text += f"  ▫️ {comp_data['name']}: {comp_data['interval']} мес.\n"

    text = (
        f"✅ Добавлена система: <b>{message.text}</b>\n\n"
        f"💡 <b>Рекомендуемый ресурс:</b>\n{intervals_text}\n"
        f"<i>⚙️ Изменить сроки можно в меню «Настройки».</i>\n\n"
        f"Отсчет начат с сегодняшнего дня."
    )
    await message.answer(text, reply_markup=get_main_menu())

# --- УМНЫЕ НАПОМИНАНИЯ (Фоновая задача) ---
async def notification_scheduler():
    while True:
        now = datetime.now()
        today_str = now.strftime("%d.%m.%Y")
        
        for user_id, user_filters in users_db.items():
            for i, f in enumerate(user_filters):
                category = f["category"]
                intervals = f["intervals"]
                history = f["history"]
                components = FILTER_CONFIGS[category]
                
                # Добавляем словарь для отметок, если вдруг профиль был создан на старом коде
                if "notified" not in f:
                    f["notified"] = {}
                
                sorted_history = sorted(history, key=lambda x: datetime.strptime(x['date'], "%d.%m.%Y"))
                
                for code, comp_data in components.items():
                    name = comp_data["name"]
                    last_rep = next((item for item in reversed(sorted_history) if item["item"] == name), None)
                    
                    if last_rep:
                        last_date = datetime.strptime(last_rep["date"], "%d.%m.%Y")
                    else:
                        created_at_str = f.get("created_at", now.strftime("%d.%m.%Y"))
                        last_date = datetime.strptime(created_at_str, "%d.%m.%Y")
                        
                    next_rep_date = last_date + timedelta(days=intervals[code] * 30.4)
                    days_left = (next_rep_date - now).days
                    
                    # Напоминаем за 7 дней, за 3 дня, день в день, и если просрочено на неделю
                    if days_left in [7, 3, 0, -7]:
                        # Проверяем, не отправляли ли мы уже уведомление конкретно сегодня
                        if f["notified"].get(code) != today_str:
                            try:
                                text = (
                                    f"🔔 <b>НАПОМИНАНИЕ!</b>\n━━━━━━━━━━━━━━━━━━━━\n"
                                    f"🚰 Система: <b>{f['model']}</b>\n"
                                    f"🔄 Элемент: <b>{name}</b>\n\n"
                                )
                                
                                if days_left > 0:
                                    text += f"🟡 <i>Ресурс заканчивается. Осталось дней: {days_left}</i>"
                                elif days_left == 0:
                                    text += f"🔴 <b>Срок вышел! Пора менять прямо сегодня.</b>"
                                else:
                                    text += f"🚨 <b>ПРОСРОЧЕНО на {abs(days_left)} дн.!</b>"
                                    
                                kb = types.InlineKeyboardMarkup(row_width=1)
                                kb.add(
                                    types.InlineKeyboardButton(text="🛒 Найти и купить картридж", callback_data=f"selbuy_{i}"),
                                    types.InlineKeyboardButton(text="✅ Отметить как замененный", callback_data=f"rep_{code}_{i}")
                                )
                                
                                await bot.send_message(user_id, text, reply_markup=kb)
                                f["notified"][code] = today_str # Ставим галочку, что сегодня уже писали
                                
                            except Exception as e:
                                logging.error(f"Не удалось отправить уведомление пользователю {user_id}: {e}")
                                
        await asyncio.sleep(3600) # Планировщик засыпает на 1 час, затем проверяет снова

# --- ЗАПУСК ---
if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    loop.create_task(start_webserver())
    loop.create_task(notification_scheduler()) # Запускаем фоновый планировщик
    executor.start_polling(dp, skip_updates=True)
