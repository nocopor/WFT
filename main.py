import logging
import os
import asyncio
from datetime import datetime
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

# База данных пользователей теперь хранит СПИСОК фильтров
# Формат: {user_id: [ {"model": "Осмос", "history": [...]}, {"model": "Проточный", "history": [...]} ]}
users_db = {}

CATALOGS = {
    "osmos": ["Aquaphor DWM-101S", "Aquaphor Osmo Pro", "Geyser Prestige M", "Geyser Allegro", "Atoll A-550", "Atoll A-575m", "Barrier Profi Osmo", "Prio Expert MO530", "Xiaomi Mi Water", "Ecosoft Standard"],
    "stage3": ["Aquaphor Trio", "Aquaphor Crystal", "Barrier Profi Standard", "Barrier Expert", "Geyser Standard", "Geyser Bio", "Prio Expert M310", "Новая Вода Expert"],
    "flow": ["Barrier Expert Slim", "Aquaphor Favorit", "Geyser 1УЖ", "Prio Praktic", "Аквафор Модерн"]
}

class FilterStates(StatesGroup):
    waiting_for_manual_name = State()

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
        types.InlineKeyboardButton(text="🗑 Очистить все мои фильтры", callback_data="set_clear")
    )
    return kb

def get_categories_kb():
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(
        types.InlineKeyboardButton(text="💧 Обратный осмос", callback_data="cat_osmos"),
        types.InlineKeyboardButton(text="🧪 3-ступенчатый", callback_data="cat_stage3"),
        types.InlineKeyboardButton(text="🚰 Проточный", callback_data="cat_flow")
    )
    return kb

def get_models_kb(category_key):
    kb = types.InlineKeyboardMarkup(row_width=1)
    for model in CATALOGS.get(category_key, []):
        kb.add(types.InlineKeyboardButton(text=model, callback_data=f"mod_{model[:20]}"))
    kb.add(types.InlineKeyboardButton(text="📝 Свой вариант", callback_data="mod_manual"))
    kb.add(types.InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_cats"))
    return kb

# Выбор фильтра (если их несколько)
def get_user_filters_kb(user_id, action_prefix):
    kb = types.InlineKeyboardMarkup(row_width=1)
    filters = users_db.get(user_id, [])
    for i, f in enumerate(filters):
        kb.add(types.InlineKeyboardButton(text=f['model'], callback_data=f"{action_prefix}_{i}"))
    kb.add(types.InlineKeyboardButton(text="❌ Отмена", callback_data="rep_cancel"))
    return kb

# Клавиатура замены (привязана к индексу фильтра)
def get_replacement_kb(filter_index):
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(
        types.InlineKeyboardButton(text="1. Предфильтры", callback_data=f"rep_pre_{filter_index}"),
        types.InlineKeyboardButton(text="2. Мембрана", callback_data=f"rep_mem_{filter_index}"),
        types.InlineKeyboardButton(text="3. Постфильтр", callback_data=f"rep_post_{filter_index}"),
        types.InlineKeyboardButton(text="❌ Отмена", callback_data="rep_cancel")
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
@dp.message_handler(commands=['start'])
async def cmd_start(message: types.Message):
    await message.answer("Привет! 🚰\nКакую систему фильтрации добавим?", reply_markup=get_categories_kb())

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
    user_id = callback_query.from_user.id
    
    # ДОБАВЛЯЕМ в список, а не перезаписываем
    if user_id not in users_db:
        users_db[user_id] = []
    users_db[user_id].append({"model": model_name, "history": []})

    await bot.edit_message_text(f"✅ Система успешно добавлена: *{model_name}*", callback_query.message.chat.id, callback_query.message.message_id)
    await bot.send_message(user_id, "Используйте меню для управления:", reply_markup=get_main_menu())

# --- ГЛАВНОЕ МЕНЮ ---
@dp.message_handler(lambda m: m.text == "📊 Статус")
async def menu_status(message: types.Message):
    user_filters = users_db.get(message.from_user.id, [])
    if not user_filters:
        return await message.answer("У вас нет добавленных систем. Перейдите в Настройки ⚙️")
    
    text = "🔍 *Статус ваших систем:*\n\n"
    for i, f in enumerate(user_filters, 1):
        text += f"*{i}. {f['model']}*\n▫️ Предфильтры: в норме\n▫️ Мембрана: в норме\n▫️ Постфильтр: в норме\n\n"
    
    await message.answer(text)

@dp.message_handler(lambda m: m.text == "📜 История")
async def menu_history(message: types.Message):
    user_filters = users_db.get(message.from_user.id, [])
    if not user_filters:
        return await message.answer("У вас нет добавленных систем.")
    
    text = "📜 *История замен по системам:*\n\n"
    has_history = False
    
    for f in user_filters:
        if f["history"]:
            has_history = True
            text += f"🔹 *{f['model']}*:\n"
            for entry in f["history"][-5:]:
                text += f"  ▫️ {entry['date']} — {entry['item']}\n"
            text += "\n"
            
    if not has_history:
        text = "📜 История замен пока пуста."
        
    await message.answer(text)

@dp.message_handler(lambda m: m.text == "⚙️ Настройки")
async def menu_settings(message: types.Message):
    await message.answer("⚙️ *Настройки профиля*\nЗдесь можно добавить еще один фильтр или удалить текущие:", reply_markup=get_settings_kb())

@dp.callback_query_handler(lambda c: c.data == 'set_clear')
async def settings_clear(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    if user_id in users_db:
        users_db[user_id] = []
    await bot.edit_message_text("🗑 Все ваши фильтры удалены. Добавьте новые.", callback_query.message.chat.id, callback_query.message.message_id, reply_markup=get_categories_kb())

# --- ПОКУПКА ---
@dp.message_handler(lambda m: m.text == "🛒 Купить картриджи")
async def menu_buy(message: types.Message):
    user_filters = users_db.get(message.from_user.id, [])
    if not user_filters:
        return await message.answer("Сначала добавьте систему в настройках ⚙️")
    
    if len(user_filters) == 1:
        model = user_filters[0]['model']
        await message.answer(f"Где будем искать картриджи для *{model}*?", reply_markup=get_market_kb(model))
    else:
        await message.answer("Для какой системы ищем картриджи?", reply_markup=get_user_filters_kb(message.from_user.id, 'selbuy'))

@dp.callback_query_handler(lambda c: c.data.startswith('selbuy_'))
async def process_buy_selection(callback_query: types.CallbackQuery):
    f_idx = int(callback_query.data.split('_')[1])
    model = users_db[callback_query.from_user.id][f_idx]['model']
    await bot.edit_message_text(f"Где будем искать картриджи для *{model}*?", callback_query.message.chat.id, callback_query.message.message_id, reply_markup=get_market_kb(model))

# --- ЗАМЕНА ---
@dp.message_handler(lambda m: m.text == "📅 Заменил картридж")
async def menu_replacement(message: types.Message):
    user_filters = users_db.get(message.from_user.id, [])
    if not user_filters:
        return await message.answer("Сначала добавьте систему в настройках ⚙️")
    
    if len(user_filters) == 1:
        await message.answer(f"🛠 *Система:* {user_filters[0]['model']}\nЧто именно заменили?", reply_markup=get_replacement_kb(0))
    else:
        await message.answer("В какой системе вы произвели замену?", reply_markup=get_user_filters_kb(message.from_user.id, 'selrep'))

@dp.callback_query_handler(lambda c: c.data.startswith('selrep_'))
async def process_rep_selection(callback_query: types.CallbackQuery):
    f_idx = int(callback_query.data.split('_')[1])
    model = users_db[callback_query.from_user.id][f_idx]['model']
    await bot.edit_message_text(f"🛠 *Система:* {model}\nЧто именно заменили?", callback_query.message.chat.id, callback_query.message.message_id, reply_markup=get_replacement_kb(f_idx))

@dp.callback_query_handler(lambda c: c.data.startswith('rep_'))
async def handle_replace_action(callback_query: types.CallbackQuery):
    parts = callback_query.data.split('_')
    if parts[1] == "cancel":
        return await bot.delete_message(callback_query.message.chat.id, callback_query.message.message_id)

    code = parts[1]
    f_idx = int(parts[2])
    user_id = callback_query.from_user.id
    
    mapping = {"pre": "Предфильтры", "mem": "Мембрана", "post": "Постфильтр"}
    item_name = mapping.get(code, "Деталь")
    date_now = datetime.now().strftime("%d.%m.%Y")
    
    # Сохраняем историю именно для выбранного фильтра [f_idx]
    users_db[user_id][f_idx]["history"].append({"date": date_now, "item": item_name})
    model_name = users_db[user_id][f_idx]["model"]
    
    await bot.edit_message_text(
        f"✅ Сохранено!\nСистема: *{model_name}*\nЗаменено: *{item_name}*\nДата: {date_now}", 
        callback_query.message.chat.id, 
        callback_query.message.message_id
    )

# --- РУЧНОЙ ВВОД ---
@dp.callback_query_handler(lambda c: c.data == 'mod_manual')
async def manual_input_start(callback_query: types.CallbackQuery):
    await FilterStates.waiting_for_manual_name.set()
    await bot.send_message(callback_query.from_user.id, "Введите полное название вашей системы:")

@dp.message_handler(state=FilterStates.waiting_for_manual_name)
async def manual_input_done(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    if user_id not in users_db:
        users_db[user_id] = []
    users_db[user_id].append({"model": message.text, "history": []})

    await state.finish()
    await message.answer(f"✅ Добавлена система: *{message.text}*", reply_markup=get_main_menu())

# --- ЗАПУСК ---
if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    loop.create_task(start_webserver())
    executor.start_polling(dp, skip_updates=True)
