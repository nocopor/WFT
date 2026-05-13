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
# pre - предфильтры, mem - мембрана, post - постфильтр, min - минерализатор, set - комплект
MODELS_DATA = {
    "osmos": {
        "Атолл": {
            "A-550": ["pre", "mem", "post"],
            "A-575": ["pre", "mem", "post"],
            "A-450": ["pre", "mem", "post"],
            "A-550m (с минерализатором)": ["pre", "mem", "post", "min"]
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
        },
        "Другие": {
            "Xiaomi Mi Water": ["pre", "mem", "post"],
            "Экософт Стандарт": ["pre", "mem", "post"]
        }
    },
    "stage3": {
        "Атолл": {"Патриот": ["set"], "D-31": ["set"]},
        "Барьер": {"Профи Стандарт": ["set"], "Профи Смягчение": ["set"], "Профи Комплекс": ["set"]},
        "Гейзер": {"Макс": ["set"], "Стандарт": ["set"], "БИО": ["set"]},
        "Аквафор": {"Кристалл Эко": ["set"], "Трио": ["set"], "Кристалл Н": ["set"]},
        "Prio (Новая Вода)": {"Expert M310": ["set"], "Praktic EU310": ["set"]}
    },
    "flow": {
        "Аквафор": {"Фаворит": ["cart"], "Модерн": ["cart"], "Викинг 10SL": ["cart"]},
        "Барьер": {"In-Line Механика": ["cart"], "In-Line Уголь": ["cart"]},
        "Гейзер": {"Тайфун 10SL": ["cart"], "Тайфун 10BB": ["cart"]},
        "Джилекс": {"Колба 10SL": ["cart"], "Колба 10BB": ["cart"]}
    }
}

FILTER_CONFIGS = {
    "osmos": {
        "pre": {"name": "Предфильтры (ступ. 1-3)", "interval": 6},
        "mem": {"name": "Мембрана RO", "interval": 24}, 
        "post": {"name": "Постфильтр", "interval": 12},
        "min": {"name": "Минерализатор", "interval": 12}
    },
    "stage3": {
        "set": {"name": "Комплект картриджей", "interval": 12}
    },
    "flow": {
        "cart": {"name": "Сменный модуль", "interval": 6}
    }
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
    category = filter_data["category"]
    intervals = filter_data["intervals"]
    
    # Кнопка быстрой замены всех предфильтров
    if category == "osmos" and "pre" in intervals and intervals["pre"] > 0:
        kb.add(types.InlineKeyboardButton(text="🔄 ЗАМЕНИТЬ ВЕСЬ КОМПЛЕКТ ПРЕДФИЛЬТРОВ", callback_data=f"repall_{filter_index}"))
        
    components = FILTER_CONFIGS[category]
    for code, comp_data in components.items():
        if intervals.get(code, 0) > 0:
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

# --- ДОБАВЛЕНИЕ СИСТЕМЫ И АВТО-ПРЕСЕТЫ ---
def add_new_filter(user_id, model_name, category, custom_intervals=None):
    if user_id not in users_db:
        users_db[user_id] = []
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
    text = "💧 <b>Добро пожаловать!</b>\nЯ — ваш личный помощник по обслуживанию домашних фильтров.\n<i>Какую систему добавим в ваш профиль?</i>"
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
        _, cat_key, brand_key = callback_query.data.split('_')
        await bot.edit_message_text(f"Производитель: <b>{brand_key}</b>\n<i>Выберите модель:</i>", callback_query.message.chat.id, callback_query.message.message_id, reply_markup=get_models_kb(cat_key, brand_key))

@dp.callback_query_handler(lambda c: c.data.startswith('mod_'))
async def process_model(callback_query: types.CallbackQuery):
    _, category, brand, model_name = callback_query.data.split('_', 3)
    full_model_name = f"{brand} {model_name}"
    
    # Берем ступени из пресета
    active_stages = MODELS_DATA[category][brand][model_name]
    intervals = {}
    brand_data = BRAND_INTERVALS.get(brand, {}).get(category)
    
    for code in active_stages:
        if brand_data and code in brand_data:
            intervals[code] = brand_data[code]
        else:
            intervals[code] = FILTER_CONFIGS[category][code]["interval"]
            
    # Добавляем отключенные ступени как 0, чтобы юзер мог включить их потом
    for code in FILTER_CONFIGS[category].keys():
        if code not in intervals:
            intervals[code] = 0
    
    add_new_filter(callback_query.from_user.id, full_model_name, category, intervals)
    
    intervals_text = ""
    for code, months in intervals.items():
        if months > 0:
            comp_name = FILTER_CONFIGS[category][code]['name']
            intervals_text += f"  ▫️ {comp_name}: {months} мес.\n"
    
    text = (
        f"✅ Система добавлена: <b>{full_model_name}</b>\n\n"
        f"💡 <b>Установленный ресурс:</b>\n{intervals_text}\n"
        f"<i>⚙️ Лишние ступени отключены. Изменить сроки можно в меню «Настройки».</i>\n\n"
        "Отсчет ресурса начат. Меню управления активно."
    )
    await bot.edit_message_text(text, callback_query.message.chat.id, callback_query.message.message_id)
    await bot.send_message(callback_query.from_user.id, "Главное меню:", reply_markup=get_main_menu())


# --- РУЧНОЙ ВВОД ---
@dp.callback_query_handler(lambda c: c.data.startswith('manual_'))
async def manual_input_start(callback_query: types.CallbackQuery, state: FSMContext):
    category = callback_query.data.split('_')[1]
    await state.update_data(manual_category=category)
    await FilterStates.waiting_for_manual_name.set()
    await bot.edit_message_text("📝 <i>Ручной ввод...</i>", callback_query.message.chat.id, callback_query.message.message_id)
    await bot.send_message(callback_query.from_user.id, "<b>Введите полное название вашей системы:</b>")

@dp.message_handler(state=FilterStates.waiting_for_manual_name)
async def manual_input_done(message: types.Message, state: FSMContext):
    data = await state.get_data()
    category = data.get("manual_category", "osmos")
    await state.update_data(manual_model_name=message.text, components_to_ask=list(FILTER_CONFIGS[category].keys()), current_comp_idx=0, custom_intervals={})
    await ask_next_custom_interval(message, state)

async def ask_next_custom_interval(message: types.Message, state: FSMContext):
    data = await state.get_data()
    components, idx, category = data['components_to_ask'], data['current_comp_idx'], data['manual_category']
    
    if idx >= len(components):
        add_new_filter(message.from_user.id, data['manual_model_name'], category, data['custom_intervals'])
        await state.finish()
        await message.answer("✅ Система добавлена вручную! Отсчет начат.", reply_markup=get_main_menu())
        return
        
    comp_code = components[idx]
    comp_name = FILTER_CONFIGS[category][comp_code]['name']
    default_int = FILTER_CONFIGS[category][comp_code]['interval']
    
    await FilterStates.waiting_for_custom_interval.set()
    await message.answer(f"⏱ <b>{comp_name}</b>\nУкажите ресурс в месяцах (цифрой, среднее: {default_int}). Введите 0, если ступени нет:")

@dp.message_handler(state=FilterStates.waiting_for_custom_interval)
async def process_custom_interval(message: types.Message, state: FSMContext):
    if not message.text.isdigit():
        return await message.answer("⚠️ Введите только цифру.")
    data = await state.get_data()
    idx, comp_code = data['current_comp_idx'], data['components_to_ask'][data['current_comp_idx']]
    data['custom_intervals'][comp_code] = int(message.text)
    await state.update_data(custom_intervals=data['custom_intervals'], current_comp_idx=idx + 1)
    await ask_next_custom_interval(message, state)


# --- СТАТУС ---
@dp.message_handler(lambda m: m.text == "📊 Статус")
async def menu_status(message: types.Message):
    user_filters = users_db.get(message.from_user.id, [])
    if not user_filters:
        return await message.answer("У вас нет добавленных систем.")
    
    text = "📊 <b>ТЕКУЩИЙ СТАТУС СИСТЕМ</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
    now = datetime.now()
    filters_needing_replacement = set()
    
    for i, f in enumerate(user_filters, 1):
        text += f"🚰 <b>{i}. {f['model']}</b>\n"
        sorted_history = sorted(f["history"], key=lambda x: datetime.strptime(x['date'], "%d.%m.%Y"))
        
        for code, comp_data in FILTER_CONFIGS[f["category"]].items():
            current_interval = f["intervals"].get(code, 0)
            if current_interval == 0: continue 
                
            name = comp_data["name"]
            last_rep = next((item for item in reversed(sorted_history) if item["item"] == name), None)
            last_date = datetime.strptime(last_rep["date"], "%d.%m.%Y") if last_rep else datetime.strptime(f.get("created_at", now.strftime("%d.%m.%Y")), "%d.%m.%Y")
                
            days_left = (last_date + timedelta(days=current_interval * 30.4) - now).days
            
            if days_left > 30: text += f"  ├ <b>{name}:</b> 🟢 Норма\n  └ <i>Через ~{days_left} дн.</i>\n"
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
            kb.add(types.InlineKeyboardButton(text=f"🛒 Купить картриджи для: {user_filters[f_idx]['model'][:22]}...", callback_data=f"selbuy_{f_idx}"))
        await message.answer(text, reply_markup=kb)
    else:
        await message.answer(text)


# --- НАСТРОЙКИ ---
@dp.message_handler(lambda m: m.text == "⚙️ Настройки")
async def menu_settings(message: types.Message):
    await message.answer("⚙️ <b>НАСТРОЙКИ ПРОФИЛЯ</b>", reply_markup=get_settings_kb())

@dp.callback_query_handler(lambda c: c.data == 'set_intervals')
async def settings_intervals(callback_query: types.CallbackQuery):
    user_filters = users_db.get(callback_query.from_user.id, [])
    if not user_filters: return await callback_query.answer("Нет фильтров", show_alert=True)
    if len(user_filters) == 1:
        await bot.edit_message_text(f"⏱ <b>Интервалы:</b> {user_filters[0]['model']}\n<i>Выберите ступень:</i>", callback_query.message.chat.id, callback_query.message.message_id, reply_markup=get_intervals_kb(callback_query.from_user.id, 0))
    else:
        await bot.edit_message_text("<b>Для какой системы настроим интервалы?</b>", callback_query.message.chat.id, callback_query.message.message_id, reply_markup=get_user_filters_kb(callback_query.from_user.id, 'selint'))

@dp.callback_query_handler(lambda c: c.data.startswith('selint_'))
async def process_selint(callback_query: types.CallbackQuery):
    f_idx = int(callback_query.data.split('_')[1])
    await bot.edit_message_text(f"⏱ <b>Интервалы:</b> {users_db[callback_query.from_user.id][f_idx]['model']}\n<i>Выберите ступень:</i>", callback_query.message.chat.id, callback_query.message.message_id, reply_markup=get_intervals_kb(callback_query.from_user.id, f_idx))

@dp.callback_query_handler(lambda c: c.data.startswith('editint_'))
async def process_editint(callback_query: types.CallbackQuery, state: FSMContext):
    _, item_code, f_idx = callback_query.data.split('_')
    await state.update_data(f_idx=int(f_idx), item_code=item_code)
    await FilterStates.waiting_for_interval.set()
    await bot.edit_message_text("Укажите новый интервал (в месяцах) или 0 для отключения:", callback_query.message.chat.id, callback_query.message.message_id)

@dp.message_handler(state=FilterStates.waiting_for_interval)
async def handle_new_interval(message: types.Message, state: FSMContext):
    if not message.text.isdigit(): return await message.answer("⚠️ Только цифры.")
    data = await state.get_data()
    users_db[message.from_user.id][data['f_idx']]["intervals"][data['item_code']] = int(message.text)
    await state.finish()
    await message.answer("✅ Настройки сохранены.", reply_markup=get_main_menu())

@dp.callback_query_handler(lambda c: c.data == 'set_del_filter')
async def settings_delete_filter(callback_query: types.CallbackQuery):
    if not users_db.get(callback_query.from_user.id): return await callback_query.answer("Нет фильтров", show_alert=True)
    await bot.edit_message_text("<b>Какую систему удалить?</b>", callback_query.message.chat.id, callback_query.message.message_id, reply_markup=get_user_filters_kb(callback_query.from_user.id, 'seldel'))

@dp.callback_query_handler(lambda c: c.data.startswith('seldel_'))
async def process_delete_filter(callback_query: types.CallbackQuery):
    deleted_model = users_db[callback_query.from_user.id].pop(int(callback_query.data.split('_')[1]))['model']
    await bot.edit_message_text(f"🗑 Система <b>{deleted_model}</b> удалена!", callback_query.message.chat.id, callback_query.message.message_id)

@dp.callback_query_handler(lambda c: c.data == 'set_clear')
async def settings_clear(callback_query: types.CallbackQuery):
    users_db[callback_query.from_user.id] = []
    await bot.edit_message_text("🧨 <b>Профиль очищен.</b>", callback_query.message.chat.id, callback_query.message.message_id)


# --- ЗАМЕНА И ИСТОРИЯ ---
@dp.message_handler(lambda m: m.text == "📅 Заменил картридж")
async def menu_replacement(message: types.Message):
    filters = users_db.get(message.from_user.id, [])
    if not filters: return await message.answer("Добавьте систему.")
    if len(filters) == 1:
        await message.answer(f"🛠 <b>Система:</b> {filters[0]['model']}\n<i>Что заменили?</i>", reply_markup=get_replacement_kb(message.from_user.id, 0))
    else:
        await message.answer("<b>Где произвели замену?</b>", reply_markup=get_user_filters_kb(message.from_user.id, 'selrep'))

@dp.callback_query_handler(lambda c: c.data.startswith('selrep_'))
async def process_rep_selection(callback_query: types.CallbackQuery):
    f_idx = int(callback_query.data.split('_')[1])
    await bot.edit_message_text(f"🛠 <b>Система:</b> {users_db[callback_query.from_user.id][f_idx]['model']}\n<i>Что заменили?</i>", callback_query.message.chat.id, callback_query.message.message_id, reply_markup=get_replacement_kb(callback_query.from_user.id, f_idx))

@dp.callback_query_handler(lambda c: c.data.startswith('repall_'))
async def handle_replace_all_pre(callback_query: types.CallbackQuery):
    f_idx = int(callback_query.data.split('_')[1])
    user_id = callback_query.from_user.id
    date_now = datetime.now().strftime("%d.%m.%Y")
    
    users_db[user_id][f_idx]["history"].append({"date": date_now, "item": "Предфильтры (ступ. 1-3)"})
    await bot.edit_message_text(f"✅ Отмечена замена <b>всего комплекта предфильтров (1-3)</b>!\nДата: <code>{date_now}</code>", callback_query.message.chat.id, callback_query.message.message_id)
    await bot.send_message(user_id, "💡 <b>Совет:</b> Своевременная замена предфильтров спасает мембрану!")

@dp.callback_query_handler(lambda c: c.data.startswith('rep_'))
async def handle_replace_action(callback_query: types.CallbackQuery):
    _, item_code, f_idx = callback_query.data.split('_')
    cat = users_db[callback_query.from_user.id][int(f_idx)]["category"]
    item_name = FILTER_CONFIGS[cat][item_code]["name"]
    await bot.edit_message_text(f"Выбрано: <b>{item_name}</b>\n<i>Когда была замена?</i>", callback_query.message.chat.id, callback_query.message.message_id, reply_markup=get_date_choice_kb(item_code, int(f_idx)))

@dp.callback_query_handler(lambda c: c.data.startswith('date_'))
async def process_date_selection(callback_query: types.CallbackQuery, state: FSMContext):
    _, action, item_code, f_idx = callback_query.data.split('_')
    f_idx = int(f_idx)
    user_id = callback_query.from_user.id
    filter_data = users_db[user_id][f_idx]
    item_name = FILTER_CONFIGS[filter_data["category"]][item_code]["name"]
    
    if action == "today":
        date_now = datetime.now().strftime("%d.%m.%Y")
        users_db[user_id][f_idx]["history"].append({"date": date_now, "item": item_name})
        await bot.edit_message_text(f"✅ <b>Сохранено!</b>\nЭлемент: {item_name}\nДата: <code>{date_now}</code>", callback_query.message.chat.id, callback_query.message.message_id)
        await bot.send_message(user_id, "💡 Не забывайте промывать колбы от налета при помощи ёршика.")
    elif action == "manual":
        await state.update_data(f_idx=f_idx, item_code=item_code, item_name=item_name)
        await FilterStates.waiting_for_date.set()
        await bot.edit_message_text(f"Отправьте дату для <b>{item_name}</b> (ДД.ММ.ГГГГ):", callback_query.message.chat.id, callback_query.message.message_id)

@dp.message_handler(state=FilterStates.waiting_for_date)
async def manual_date_input(message: types.Message, state: FSMContext):
    try:
        valid_date = datetime.strptime(message.text.strip(), "%d.%m.%Y").strftime("%d.%m.%Y")
    except ValueError:
        return await message.answer("⚠️ Ошибка формата! Ожидается: ДД.ММ.ГГГГ")
    
    data = await state.get_data()
    users_db[message.from_user.id][data['f_idx']]["history"].append({"date": valid_date, "item": data['item_name']})
    await state.finish()
    await message.answer(f"✅ Дата <code>{valid_date}</code> сохранена!", reply_markup=get_main_menu())


@dp.message_handler(lambda m: m.text == "📜 История")
async def menu_history(message: types.Message):
    user_filters = users_db.get(message.from_user.id, [])
    if not user_filters: return await message.answer("Нет добавленных систем.")
    text = "📜 <b>ИСТОРИЯ ОБСЛУЖИВАНИЯ</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
    has_history = False
    for f in user_filters:
        if f["history"]:
            has_history = True
            text += f"🚰 <b>{f['model']}</b>:\n"
            for entry in sorted(f["history"], key=lambda x: datetime.strptime(x['date'], "%d.%m.%Y"), reverse=True)[:5]:
                text += f"  ▫️ <code>{entry['date']}</code> — {entry['item']}\n"
            text += "\n"
    await message.answer(text if has_history else "<i>История пока пуста.</i>")

@dp.message_handler(lambda m: m.text == "🛒 Купить картриджи")
async def menu_buy(message: types.Message):
    filters = users_db.get(message.from_user.id, [])
    if not filters: return await message.answer("Сначала добавьте систему.")
    if len(filters) == 1:
        await message.answer(f"Ищем картриджи для <b>{filters[0]['model']}</b>:", reply_markup=get_market_kb(filters[0]['model']))
    else:
        await message.answer("<b>Для какой системы ищем?</b>", reply_markup=get_user_filters_kb(message.from_user.id, 'selbuy'))

@dp.callback_query_handler(lambda c: c.data.startswith('selbuy_'))
async def process_buy_sel(callback_query: types.CallbackQuery):
    model = users_db[callback_query.from_user.id][int(callback_query.data.split('_')[1])]['model']
    await bot.edit_message_text(f"🛒 <b>Поиск картриджей</b>\nСистема: <b>{model}</b>\n<i>Выберите магазин:</i>", callback_query.message.chat.id, callback_query.message.message_id, reply_markup=get_market_kb(model))


# --- УВЕДОМЛЕНИЯ И ЗАПУСК ---
async def notification_scheduler():
    while True:
        now = datetime.now()
        today_str = now.strftime("%d.%m.%Y")
        for user_id, filters in users_db.items():
            for i, f in enumerate(filters):
                sorted_history = sorted(f["history"], key=lambda x: datetime.strptime(x['date'], "%d.%m.%Y"))
                for code, comp_data in FILTER_CONFIGS[f["category"]].items():
                    interval = f["intervals"].get(code, 0)
                    if interval == 0: continue 
                        
                    name = comp_data["name"]
                    last_rep = next((item for item in reversed(sorted_history) if item["item"] == name), None)
                    last_date = datetime.strptime(last_rep["date"], "%d.%m.%Y") if last_rep else datetime.strptime(f.get("created_at", now.strftime("%d.%m.%Y")), "%d.%m.%Y")
                        
                    days_left = (last_date + timedelta(days=interval * 30.4) - now).days
                    
                    if days_left in [7, 3, 0, -7] and f.setdefault("notified", {}).get(code) != today_str:
                        text = f"🔔 <b>НАПОМИНАНИЕ!</b>\n🚰 Система: {f['model']}\n🔄 Элемент: {name}\n"
                        if days_left > 0: text += f"🟡 <i>Осталось дней: {days_left}</i>"
                        elif days_left == 0: text += f"🔴 <b>Пора менять сегодня.</b>"
                        else: text += f"🚨 <b>ПРОСРОЧЕНО на {abs(days_left)} дн.!</b>"
                            
                        kb = types.InlineKeyboardMarkup(row_width=1)
                        kb.add(types.InlineKeyboardButton(text="🛒 Найти картридж", callback_data=f"selbuy_{i}"),
                               types.InlineKeyboardButton(text="✅ Заменено", callback_data=f"rep_{code}_{i}"))
                        
                        try:
                            await bot.send_message(user_id, text, reply_markup=kb)
                            f["notified"][code] = today_str
                        except: pass
        await asyncio.sleep(3600)

async def start_webserver():
    app = web.Application()
    app.router.add_get("/", lambda req: web.Response(text="Bot is alive!"))
    runner = web.AppRunner(app)
    await runner.setup()
    
    # Render передает порт в переменной окружения PORT.
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    logging.info(f"Веб-сервер запущен на порту {port}")

async def on_startup(dp):
    # Убиваем старые сессии (решение ошибки TerminatedByOtherGetUpdates)
    await bot.delete_webhook(drop_pending_updates=True)
    # Запускаем фоновые задачи (веб-сервер для Render и уведомления)
    asyncio.create_task(start_webserver())
    asyncio.create_task(notification_scheduler())

if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True, on_startup=on_startup)
