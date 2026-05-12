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
        "Атолл": ["A-550", "A-575", "A-450"],
        "Барьер": ["Профи Осмо 100", "Профи Осмо 100 М", "Compact Osmo"],
        "Гейзер": ["Престиж", "Аллегро", "Маэстро"],
        "Аквафор": ["DWM-101S Морион", "Осмо Про 50", "Осмо 50 ПН"],
        "Prio (Новая Вода)": ["Эксперт Osmos MO530", "Start Osmos"],
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
        "Гейзер": ["Тайфун 10SL", "Тайфун 10BB"],
        "Джилекс": ["Колба 10SL", "Колба 10BB"]
    }
}

# УСРЕДНЕННЫЕ БАЗОВЫЕ ЗНАЧЕНИЯ (Для других брендов и ручного ввода)
FILTER_CONFIGS = {
    "osmos": {
        "pre": {"name": "Предфильтры (ступ. 1-3)", "interval": 6},
        "mem": {"name": "Мембрана RO", "interval": 24}, 
        "post": {"name": "Постфильтр", "interval": 12},
        "min": {"name": "Минерализатор", "interval": 12}
    },
    "stage3": {
        "set": {"name": "Комплект картриджей (ступ. 1-3)", "interval": 12}
    },
    "flow": {
        "cart": {"name": "Сменный модуль", "interval": 6}
    }
}

# ИНДИВИДУАЛЬНЫЕ НАСТРОЙКИ ПО БРЕНДАМ
BRAND_INTERVALS = {
    "Аквафор": {
        "osmos": {"pre": 6, "mem": 18, "post": 12, "min": 12},
        "stage3": {"set": 12} 
    },
    "Атолл": {
        "osmos": {"pre": 6, "mem": 24, "post": 12, "min": 12},
        "stage3": {"set": 6}
    },
    "Барьер": {
        "osmos": {"pre": 6, "mem": 12, "post": 6, "min": 4},
        "stage3": {"set": 6}
    },
    "Гейзер": {
        "osmos": {"pre": 12, "mem": 24, "post": 12, "min": 12},
        "stage3": {"set": 12}
    }
}

class FilterStates(StatesGroup):
    waiting_for_manual_name = State()
    waiting_for_custom_interval = State()
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
        types.InlineKeyboardButton(text="🗑 Удалить систему", callback_data="set_del_filter"), # НОВАЯ КНОПКА
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
def add_new_filter(user_id, model_name, category, custom_intervals=None):
    if user_id not in users_db:
        users_db[user_id] = []
        
    if custom_intervals:
        intervals = custom_intervals
    else:
        intervals = {code: data["interval"] for code, data in FILTER_CONFIGS[category].items()}
        
    users_db[user_id].append({
        "model": model_name, 
        "category": category,
        "created_at": datetime.now().strftime("%d.%m.%Y"),
        "history": [],
        "intervals": intervals,
        "notified": {}
    })

@dp.message_handler(commands=['start'])
async def cmd_start(message: types.Message):
    text = (
        "💧 <b>Добро пожаловать!</b>\n\n"
        "Я — ваш личный помощник по обслуживанию домашних фильтров для воды.\n"
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
    
    intervals = {}
    brand_data = BRAND_INTERVALS.get(brand, {}).get(category)
    for code, comp_data in FILTER_CONFIGS[category].items():
        if brand_data and code in brand_data:
            intervals[code] = brand_data[code]
        else:
            intervals[code] = comp_data["interval"]
    
    add_new_filter(callback_query.from_user.id, full_model_name, category, intervals)
    
    intervals_text = ""
    for code, comp_data in FILTER_CONFIGS[category].items():
        intervals_text += f"  ▫️ {comp_data['name']}: {intervals[code]} мес.\n"
    
    text = (
        f"✅ Система добавлена: <b>{full_model_name}</b>\n\n"
        f"💡 <b>Рекомендуемый ресурс ({brand}):</b>\n{intervals_text}\n"
        f"<i>⚙️ Изменить сроки под свой расход воды можно в меню «Настройки».</i>\n\n"
    )
    
    if category == "osmos":
        text += "🛡 <b>Важное правило:</b> Регулярная замена недорогих предфильтров принимает на себя удар хлора и загрязнений, спасая самую дорогую деталь системы — мембрану!\n\n"
        
    if brand == "Гейзер":
        if category == "stage3":
            text += "⚠️ <i>Примечание: для Гейзера указан срок работы на мягкой воде (1 год). При очень жесткой воде накипь может появиться уже через 2 месяца! Вы можете сократить этот срок в Настройках.</i>\n\n"
        elif category == "osmos":
            text += "⚠️ <i>Примечание: мембрана Гейзер меняется строго при первом появлении накипи. Бот установил усредненный срок (2 года), следите за качеством воды.</i>\n\n"

    elif brand == "Аквафор":
        if category == "stage3":
            text += "⚠️ <i>Примечание: Картриджи серии Pro служат до 1.5 лет. Умягчающие модули (с пометкой Н) можно регенерировать солью. Если напор воды резко упал — пора менять полипропиленовый модуль!</i>\n\n"
        elif category == "osmos" and "DWM" in full_model_name:
            text += "🌟 <i>Супер: Ваша система серии DWM! За счет экономичного слива воды в дренаж, предфильтры в ней могут прослужить дольше стандартных.</i>\n\n"

    text += "Отсчет ресурса начат. Меню управления активно."

    await bot.edit_message_text(text, callback_query.message.chat.id, callback_query.message.message_id)
    await bot.send_message(callback_query.from_user.id, "Главное меню:", reply_markup=get_main_menu())


# --- РУЧНОЙ ВВОД ---
@dp.callback_query_handler(lambda c: c.data.startswith('manual_'))
async def manual_input_start(callback_query: types.CallbackQuery, state: FSMContext):
    category = callback_query.data.split('_')[1]
    
    await bot.edit_message_text(
        "📝 <i>Выбран ручной ввод названия системы...</i>", 
        callback_query.message.chat.id, 
        callback_query.message.message_id
    )
    
    await state.update_data(manual_category=category)
    await FilterStates.waiting_for_manual_name.set()
    await bot.send_message(callback_query.from_user.id, "<b>Введите полное название вашей системы:</b>")

@dp.message_handler(state=FilterStates.waiting_for_manual_name)
async def manual_input_done(message: types.Message, state: FSMContext):
    data = await state.get_data()
    category = data.get("manual_category", "osmos")
    model_name = message.text
    
    components = list(FILTER_CONFIGS[category].keys())
    
    await state.update_data(
        manual_model_name=model_name,
        components_to_ask=components,
        current_comp_idx=0,
        custom_intervals={}
    )
    
    await ask_next_custom_interval(message, state)

async def ask_next_custom_interval(message: types.Message, state: FSMContext):
    data = await state.get_data()
    components = data['components_to_ask']
    idx = data['current_comp_idx']
    category = data['manual_category']
    
    if idx >= len(components):
        custom_intervals = data['custom_intervals']
        model_name = data['manual_model_name']
        
        add_new_filter(message.from_user.id, model_name, category, custom_intervals)
        await state.finish()
        
        intervals_text = ""
        for comp_code, months in custom_intervals.items():
            comp_name = FILTER_CONFIGS[category][comp_code]['name']
            intervals_text += f"  ▫️ {comp_name}: {months} мес.\n"

        text = (
            f"✅ Добавлена система: <b>{model_name}</b>\n\n"
            f"💡 <b>Установленный ресурс:</b>\n{intervals_text}\n"
            f"<i>⚙️ Изменить сроки можно в меню «Настройки».</i>\n\n"
        )
        if category == "osmos":
             text += "🛡 <b>Важное правило:</b> Регулярная замена предфильтров спасает самую дорогую деталь системы — мембрану!\n\n"
             
        text += "Отсчет начат с сегодняшнего дня."
        await message.answer(text, reply_markup=get_main_menu())
        return
        
    comp_code = components[idx]
    comp_name = FILTER_CONFIGS[category][comp_code]['name']
    default_int = FILTER_CONFIGS[category][comp_code]['interval']
    
    await FilterStates.waiting_for_custom_interval.set()
    
    text = (
        f"⏱ <b>Укажите срок замены для: {comp_name}</b>\n\n"
        f"<i>💡 Среднее значение для подобных систем: <b>{default_int} мес.</b></i>\n\n"
        f"Введите количество месяцев (цифрой, например: {default_int}):"
    )
    await message.answer(text)

@dp.message_handler(state=FilterStates.waiting_for_custom_interval)
async def process_custom_interval(message: types.Message, state: FSMContext):
    if not message.text.isdigit():
        return await message.answer("⚠️ Пожалуйста, введите только цифру (например, 6).")
        
    months = int(message.text)
    data = await state.get_data()
    
    components = data['components_to_ask']
    idx = data['current_comp_idx']
    comp_code = components[idx]
    
    custom_intervals = data['custom_intervals']
    custom_intervals[comp_code] = months
    
    await state.update_data(
        custom_intervals=custom_intervals,
        current_comp_idx=idx + 1
    )
    
    await ask_next_custom_interval(message, state)


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
        "<i>Здесь вы можете изменить установленные сроки "
        "замены или удалить ненужные системы.</i>"
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

# НОВОЕ: Удаление конкретного фильтра
@dp.callback_query_handler(lambda c: c.data == 'set_del_filter')
async def settings_delete_filter(callback_query: types.CallbackQuery):
    user_filters = users_db.get(callback_query.from_user.id, [])
    if not user_filters:
        return await callback_query.answer("Нет добавленных фильтров", show_alert=True)
        
    await bot.edit_message_text(
        "<b>Какую систему вы хотите удалить?</b>", 
        callback_query.message.chat.id, 
        callback_query.message.message_id, 
        reply_markup=get_user_filters_kb(callback_query.from_user.id, 'seldel')
    )

@dp.callback_query_handler(lambda c: c.data.startswith('seldel_'))
async def process_delete_filter(callback_query: types.CallbackQuery):
    f_idx = int(callback_query.data.split('_')[1])
    user_id = callback_query.from_user.id
    
    if user_id in users_db and len(users_db[user_id]) > f_idx:
        deleted_model = users_db[user_id].pop(f_idx)['model']
        await bot.edit_message_text(
            f"🗑 Система <b>{deleted_model}</b> успешно удалена из вашего профиля!", 
            callback_query.message.chat.id, 
            callback_query.message.message_id
        )
    else:
        await callback_query.answer("Ошибка при удалении", show_alert=True)

@dp.callback_query_handler(lambda c: c.data == 'set_clear')
async def settings_clear(callback_query: types.CallbackQuery):
    users_db[callback_query.from_user.id] = []
    await bot.edit_message_text("🧨 <b>Профиль полностью очищен.</b>\nВсе системы удалены.", callback_query.message.chat.id, callback_query.message.message_id)

# --- ЗАМЕНА С СОВЕТАМИ ПО ПРОФИЛАКТИКЕ ---
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
    filter_data = users_db[user_id][f_idx]
    category = filter_data["category"]
    item_name = FILTER_CONFIGS[category][item_code]["name"]
    model_name = filter_data["model"]
    
    if action == "today":
        date_now_dt = datetime.now()
        date_now = date_now_dt.strftime("%d.%m.%Y")
        users_db[user_id][f_idx]["history"].append({"date": date_now, "item": item_name})
        
        text = (
            f"✅ <b>Успешно сохранено!</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🔹 <b>Система:</b> {model_name}\n"
            f"🔹 <b>Элемент:</b> {item_name}\n"
            f"🔹 <b>Дата:</b> <code>{date_now}</code>"
        )
        await bot.edit_message_text(text, callback_query.message.chat.id, callback_query.message.message_id)
        
        created_at_str = filter_data.get("created_at", date_now)
        try:
            created_at_dt = datetime.strptime(created_at_str, "%d.%m.%Y")
            system_age_days = (date_now_dt - created_at_dt).days
        except:
            system_age_days = 0

        tips_text = "💡 <b>Полезные советы при замене:</b>\n\n"
        tips_text += "🧽 При замене фильтроэлементов рекомендуется промывать колбы от налета при помощи ёршика и средства для мытья посуды.\n"
        
        if item_code == "pre" and category == "osmos":
            tips_text += "\n🛡 <b>Отлично!</b> Своевременная замена предфильтров принимает на себя основной удар и надежно защищает самую дорогую деталь системы — мембрану."

        if system_age_days >= 365 and (system_age_days % 365) < 7:
            tips_text += "\n\n🚨 <b>Внимание:</b> Вашей системе уже больше года. Производитель рекомендует заменять прокладки под крышками колб <b>один раз в год</b>, чтобы избежать протечек!"
        
        await bot.send_message(user_id, tips_text)
        
    elif action == "manual":
        await state.update_data(f_idx=f_idx, item_code=item_code, item_name=item_name, model_name=model_name, category=category)
        await FilterStates.waiting_for_date.set()
        await bot.edit_message_text(f"Отправьте дату замены для <b>{item_name}</b>\n<i>Формат:</i> <code>ДД.ММ.ГГГГ</code> (например, 15.08.2023):", callback_query.message.chat.id, callback_query.message.message_id)

@dp.message_handler(state=FilterStates.waiting_for_date)
async def manual_date_input(message: types.Message, state: FSMContext):
    user_input = message.text.strip()
    try:
        input_date_dt = datetime.strptime(user_input, "%d.%m.%Y")
        valid_date = input_date_dt.strftime("%d.%m.%Y")
    except ValueError:
        return await message.answer("⚠️ Ошибка формата!\n<i>Ожидается:</i> <code>ДД.ММ.ГГГГ</code>")
    
    data = await state.get_data()
    f_idx = data['f_idx']
    user_id = message.from_user.id
    
    users_db[user_id][f_idx]["history"].append({"date": valid_date, "item": data['item_name']})
    await state.finish()
    await message.answer(f"✅ Дата <code>{valid_date}</code> успешно сохранена в историю!", reply_markup=get_main_menu())
    
    tips_text = "💡 <b>Полезный совет:</b> При замене фильтроэлементов не забывайте промывать колбы от налета при помощи ёршика и средства для мытья посуды.\n"
    if data.get('item_code') == "pre" and data.get('category') == "osmos":
         tips_text += "\n🛡 <b>Отлично!</b> Замена предфильтров сбережет ресурс вашей мембраны."

    await message.answer(tips_text)

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

# --- УМНЫЕ НАПОМИНАНИЯ ---
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
                    
                    if days_left in [7, 3, 0, -7]:
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
                                f["notified"][code] = today_str
                                
                            except Exception as e:
                                logging.error(f"Не удалось отправить уведомление {user_id}: {e}")
                                
        await asyncio.sleep(3600)

if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    loop.create_task(start_webserver())
    loop.create_task(notification_scheduler())
    executor.start_polling(dp, skip_updates=True)
