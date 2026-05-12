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

CATALOGS = {
    "osmos": ["Aquaphor DWM-101S", "Aquaphor Osmo Pro", "Geyser Prestige", "Geyser Allegro", "Atoll A-550", "Atoll A-575", "Barrier Profi Osmo", "Prio Expert MO530", "Xiaomi Mi Water", "Ecosoft Standard"],
    "stage3": ["Аквафор Кристалл", "Аквафор Трио", "Гейзер Макс", "Гейзер Стандарт", "Гейзер Классик", "Барьер Эксперт", "Барьер Профи", "Prio Новая Вода Expert", "Атолл Патриот"],
    "flow": ["Аквафор Фаворит", "Аквафор Модерн", "Гейзер 1УЖ Евро", "Гейзер Тайфун (магистраль)", "Аквафор Викинг", "Барьер In-Line"]
}

# Маппинг для удобства
ITEM_NAMES = {"pre": "Предфильтры", "mem": "Мембрана", "post": "Постфильтр"}
# Стандартные интервалы (в месяцах)
DEFAULT_INTERVALS = {"pre": 6, "mem": 24, "post": 12}

class FilterStates(StatesGroup):
    waiting_for_manual_name = State()
    waiting_for_date = State()
    waiting_for_interval = State() # Стейт для ввода нового интервала

bot = Bot(token=TOKEN, parse_mode=types.ParseMode.MARKDOWN)
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
    kb.row("📊 Статус", "📅 Заменил картридж")
    kb.row("📜 История", "🛒 Купить картриджи")
    kb.row("⚙️ Настройки")
    return kb

def get_settings_kb():
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(
        types.InlineKeyboardButton(text="➕ Добавить систему", callback_data="set_add"),
        types.InlineKeyboardButton(text="⏱ Настроить интервалы замен", callback_data="set_intervals"),
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

def get_models_kb(category_key):
    kb = types.InlineKeyboardMarkup(row_width=1)
    for model in CATALOGS.get(category_key, []):
        kb.add(types.InlineKeyboardButton(text=model, callback_data=f"mod_{model[:30]}"))
    kb.add(types.InlineKeyboardButton(text="📝 Свой вариант", callback_data="mod_manual"))
    kb.add(types.InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_cats"))
    return kb

def get_user_filters_kb(user_id, action_prefix):
    kb = types.InlineKeyboardMarkup(row_width=1)
    filters = users_db.get(user_id, [])
    for i, f in enumerate(filters):
        kb.add(types.InlineKeyboardButton(text=f['model'], callback_data=f"{action_prefix}_{i}"))
    kb.add(types.InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_action"))
    return kb

def get_replacement_kb(filter_index):
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(
        types.InlineKeyboardButton(text="1. Предфильтры", callback_data=f"rep_pre_{filter_index}"),
        types.InlineKeyboardButton(text="2. Мембрана", callback_data=f"rep_mem_{filter_index}"),
        types.InlineKeyboardButton(text="3. Постфильтр", callback_data=f"rep_post_{filter_index}"),
        types.InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_action")
    )
    return kb

def get_intervals_kb(user_id, filter_index):
    kb = types.InlineKeyboardMarkup(row_width=1)
    intervals = users_db[user_id][filter_index]["intervals"]
    kb.add(
        types.InlineKeyboardButton(text=f"Предфильтры ({intervals['pre']} мес) ✏️", callback_data=f"editint_pre_{filter_index}"),
        types.InlineKeyboardButton(text=f"Мембрана ({intervals['mem']} мес) ✏️", callback_data=f"editint_mem_{filter_index}"),
        types.InlineKeyboardButton(text=f"Постфильтр ({intervals['post']} мес) ✏️", callback_data=f"editint_post_{filter_index}"),
        types.InlineKeyboardButton(text="⬅️ Назад", callback_data="set_intervals")
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

def get_market_kb(model_name):
    kb = types.InlineKeyboardMarkup(row_width=1)
    query = quote(f"картриджи для фильтра {model_name}")
    kb.add(
        types.InlineKeyboardButton(text="🛒 Найти на Ozon", url=f"https://www.ozon.ru/search/?text={query}"),
        types.InlineKeyboardButton(text="🟣 Найти на Wildberries", url=f"https://www.wildberries.ru/catalog/0/search.aspx?search={query}")
    )
    return kb

# --- ДОБАВЛЕНИЕ СИСТЕМЫ ---
def add_new_filter(user_id, model_name):
    if user_id not in users_db:
        users_db[user_id] = []
    # При создании сразу добавляем стандартные интервалы
    users_db[user_id].append({
        "model": model_name, 
        "history": [],
        "intervals": DEFAULT_INTERVALS.copy()
    })

@dp.message_handler(commands=['start'])
async def cmd_start(message: types.Message):
    await message.answer("Привет! 🚰\nКакую систему фильтрации добавим?", reply_markup=get_categories_kb())

@dp.callback_query_handler(lambda c: c.data == 'cancel_action')
async def handle_cancel(callback_query: types.CallbackQuery):
    await bot.delete_message(callback_query.message.chat.id, callback_query.message.message_id)

@dp.callback_query_handler(lambda c: c.data == 'set_add')
async def settings_add(callback_query: types.CallbackQuery):
    await bot.edit_message_text("Какую систему добавим?", callback_query.message.chat.id, callback_query.message.message_id, reply_markup=get_categories_kb())

@dp.callback_query_handler(lambda c: c.data == 'back_to_cats')
@dp.callback_query_handler(lambda c: c.data.startswith('cat_'))
async def process_category(callback_query: types.CallbackQuery):
    if callback_query.data == 'back_to_cats':
        await bot.edit_message_text("Выберите тип системы:", callback_query.message.chat.id, callback_query.message.message_id, reply_markup=get_categories_kb())
    else:
        cat_key = callback_query.data.split('_')[1]
        await bot.edit_message_text("Теперь выберите модель:", callback_query.message.chat.id, callback_query.message.message_id, reply_markup=get_models_kb(cat_key))

@dp.callback_query_handler(lambda c: c.data.startswith('mod_') and c.data != 'mod_manual')
async def process_model(callback_query: types.CallbackQuery):
    model_name = callback_query.data.replace('mod_', '')
    add_new_filter(callback_query.from_user.id, model_name)
    await bot.edit_message_text(f"✅ Система добавлена: *{model_name}*", callback_query.message.chat.id, callback_query.message.message_id)
    await bot.send_message(callback_query.from_user.id, "Бот готов к работе!", reply_markup=get_main_menu())

# --- ГЛАВНОЕ МЕНЮ (Умный Статус) ---
@dp.message_handler(lambda m: m.text == "📊 Статус")
async def menu_status(message: types.Message):
    user_filters = users_db.get(message.from_user.id, [])
    if not user_filters:
        return await message.answer("У вас нет добавленных систем. Перейдите в Настройки ⚙️")
    
    text = "🔍 *Текущий статус:*\n\n"
    now = datetime.now()
    
    for i, f in enumerate(user_filters, 1):
        text += f"*{i}. {f['model']}*\n"
        intervals = f["intervals"]
        history = f["history"]
        
        for code, name in ITEM_NAMES.items():
            # Ищем последнюю замену этого элемента
            last_rep = next((item for item in reversed(history) if item["item"] == name), None)
            
            if not last_rep:
                text += f"▫️ {name}: ❓ Неизвестно (отметьте дату)\n"
            else:
                last_date = datetime.strptime(last_rep["date"], "%d.%m.%Y")
                # Считаем когда следующая замена (интервал в месяцах ~ 30.4 дней)
                next_rep_date = last_date + timedelta(days=intervals[code] * 30.4)
                days_left = (next_rep_date - now).days
                
                if days_left > 30:
                    text += f"▫️ {name}: Норма (осталось ~{days_left} дн.)\n"
                elif 0 <= days_left <= 30:
                    text += f"▫️ {name}: ⚠️ Скоро замена (осталось {days_left} дн.)\n"
                else:
                    text += f"▫️ {name}: 🚨 ПРОСРОЧЕНО на {abs(days_left)} дн.!\n"
        text += "\n"
        
    await message.answer(text)

# --- НАСТРОЙКИ (и изменение интервалов) ---
@dp.message_handler(lambda m: m.text == "⚙️ Настройки")
async def menu_settings(message: types.Message):
    await message.answer("⚙️ *Настройки профиля*", reply_markup=get_settings_kb())

@dp.callback_query_handler(lambda c: c.data == 'set_intervals')
async def settings_intervals(callback_query: types.CallbackQuery):
    user_filters = users_db.get(callback_query.from_user.id, [])
    if not user_filters:
        return await callback_query.answer("Нет добавленных фильтров", show_alert=True)
    
    if len(user_filters) == 1:
        await bot.edit_message_text(f"⏱ *Интервалы для {user_filters[0]['model']}*\nВыберите ступень:", callback_query.message.chat.id, callback_query.message.message_id, reply_markup=get_intervals_kb(callback_query.from_user.id, 0))
    else:
        await bot.edit_message_text("Для какой системы настроим интервалы?", callback_query.message.chat.id, callback_query.message.message_id, reply_markup=get_user_filters_kb(callback_query.from_user.id, 'selint'))

@dp.callback_query_handler(lambda c: c.data.startswith('selint_'))
async def process_selint(callback_query: types.CallbackQuery):
    f_idx = int(callback_query.data.split('_')[1])
    model = users_db[callback_query.from_user.id][f_idx]['model']
    await bot.edit_message_text(f"⏱ *Интервалы для {model}*\nВыберите ступень:", callback_query.message.chat.id, callback_query.message.message_id, reply_markup=get_intervals_kb(callback_query.from_user.id, f_idx))

@dp.callback_query_handler(lambda c: c.data.startswith('editint_'))
async def process_editint(callback_query: types.CallbackQuery, state: FSMContext):
    parts = callback_query.data.split('_')
    item_code = parts[1]
    f_idx = int(parts[2])
    
    await state.update_data(f_idx=f_idx, item_code=item_code)
    await FilterStates.waiting_for_interval.set()
    
    item_name = ITEM_NAMES.get(item_code)
    await bot.edit_message_text(
        f"Укажите новый интервал замены для *{item_name}* (в месяцах).\nНапример, напишите цифру `6` или `12`:",
        callback_query.message.chat.id, callback_query.message.message_id
    )

@dp.message_handler(state=FilterStates.waiting_for_interval)
async def handle_new_interval(message: types.Message, state: FSMContext):
    if not message.text.isdigit():
        return await message.answer("⚠️ Пожалуйста, введите только цифру (количество месяцев).")
    
    months = int(message.text)
    data = await state.get_data()
    user_id = message.from_user.id
    
    # Сохраняем новый интервал
    users_db[user_id][data['f_idx']]["intervals"][data['item_code']] = months
    
    await state.finish()
    await message.answer(f"✅ Новый интервал сохранен: *{months} мес.*", reply_markup=get_main_menu())

@dp.callback_query_handler(lambda c: c.data == 'set_clear')
async def settings_clear(callback_query: types.CallbackQuery):
    users_db[callback_query.from_user.id] = []
    await bot.edit_message_text("🗑 Фильтры удалены.", callback_query.message.chat.id, callback_query.message.message_id, reply_markup=get_categories_kb())

# --- ЗАМЕНА (С историей и ручной датой) ---
@dp.message_handler(lambda m: m.text == "📅 Заменил картридж")
async def menu_replacement(message: types.Message):
    user_filters = users_db.get(message.from_user.id, [])
    if not user_filters:
        return await message.answer("Добавьте систему в настройках ⚙️")
    
    if len(user_filters) == 1:
        await message.answer(f"🛠 *Система:* {user_filters[0]['model']}\nЧто заменили?", reply_markup=get_replacement_kb(0))
    else:
        await message.answer("В какой системе произвели замену?", reply_markup=get_user_filters_kb(message.from_user.id, 'selrep'))

@dp.callback_query_handler(lambda c: c.data.startswith('selrep_'))
async def process_rep_selection(callback_query: types.CallbackQuery):
    f_idx = int(callback_query.data.split('_')[1])
    model = users_db[callback_query.from_user.id][f_idx]['model']
    await bot.edit_message_text(f"🛠 *Система:* {model}\nЧто заменили?", callback_query.message.chat.id, callback_query.message.message_id, reply_markup=get_replacement_kb(f_idx))

@dp.callback_query_handler(lambda c: c.data.startswith('rep_'))
async def handle_replace_action(callback_query: types.CallbackQuery):
    parts = callback_query.data.split('_')
    item_code = parts[1]
    f_idx = int(parts[2])
    item_name = ITEM_NAMES.get(item_code)
    
    await bot.edit_message_text(f"Выбрано: *{item_name}*.\nКогда была замена?", callback_query.message.chat.id, callback_query.message.message_id, reply_markup=get_date_choice_kb(item_code, f_idx))

@dp.callback_query_handler(lambda c: c.data.startswith('date_'))
async def process_date_selection(callback_query: types.CallbackQuery, state: FSMContext):
    parts = callback_query.data.split('_')
    action = parts[1]
    item_code = parts[2]
    f_idx = int(parts[3])
    
    user_id = callback_query.from_user.id
    item_name = ITEM_NAMES.get(item_code)
    model_name = users_db[user_id][f_idx]["model"]
    
    if action == "today":
        date_now = datetime.now().strftime("%d.%m.%Y")
        users_db[user_id][f_idx]["history"].append({"date": date_now, "item": item_name})
        await bot.edit_message_text(f"✅ Записано!\nСистема: *{model_name}*\nЗаменено: *{item_name}*\nДата: {date_now}", callback_query.message.chat.id, callback_query.message.message_id)
    elif action == "manual":
        await state.update_data(f_idx=f_idx, item_name=item_name, model_name=model_name)
        await FilterStates.waiting_for_date.set()
        await bot.edit_message_text(f"Введите дату замены для *{item_name}* (в формате ДД.ММ.ГГГГ):", callback_query.message.chat.id, callback_query.message.message_id)

@dp.message_handler(state=FilterStates.waiting_for_date)
async def manual_date_input(message: types.Message, state: FSMContext):
    try:
        valid_date = datetime.strptime(message.text.strip(), "%d.%m.%Y").strftime("%d.%m.%Y")
    except ValueError:
        return await message.answer("⚠️ Неверный формат! Введите ДД.ММ.ГГГГ (например, 15.08.2023).")
    
    data = await state.get_data()
    users_db[message.from_user.id][data['f_idx']]["history"].append({"date": valid_date, "item": data['item_name']})
    await state.finish()
    await message.answer(f"✅ Дата {valid_date} записана в историю!", reply_markup=get_main_menu())

# --- ИСТОРИЯ И ПОКУПКА ---
@dp.message_handler(lambda m: m.text == "📜 История")
async def menu_history(message: types.Message):
    user_filters = users_db.get(message.from_user.id, [])
    if not user_filters:
        return await message.answer("У вас нет добавленных систем.")
    
    text = "📜 *История замен:*\n\n"
    has_history = False
    for f in user_filters:
        if f["history"]:
            has_history = True
            text += f"🔹 *{f['model']}*:\n"
            for entry in sorted(f["history"], key=lambda x: datetime.strptime(x['date'], "%d.%m.%Y"), reverse=True)[:5]:
                text += f"  ▫️ {entry['date']} — {entry['item']}\n"
            text += "\n"
    await message.answer(text if has_history else "📜 История пуста.")

@dp.message_handler(lambda m: m.text == "🛒 Купить картриджи")
async def menu_buy(message: types.Message):
    user_filters = users_db.get(message.from_user.id, [])
    if not user_filters:
        return await message.answer("Добавьте систему в настройках ⚙️")
    if len(user_filters) == 1:
        await message.answer(f"Ищем картриджи для *{user_filters[0]['model']}*:", reply_markup=get_market_kb(user_filters[0]['model']))
    else:
        await message.answer("Для какой системы ищем?", reply_markup=get_user_filters_kb(message.from_user.id, 'selbuy'))

@dp.callback_query_handler(lambda c: c.data.startswith('selbuy_'))
async def process_buy_sel(callback_query: types.CallbackQuery):
    f_idx = int(callback_query.data.split('_')[1])
    model = users_db[callback_query.from_user.id][f_idx]['model']
    await bot.edit_message_text(f"Ищем картриджи для *{model}*:", callback_query.message.chat.id, callback_query.message.message_id, reply_markup=get_market_kb(model))

# --- ЗАПУСК ---
if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    loop.create_task(start_webserver())
    executor.start_polling(dp, skip_updates=True)
