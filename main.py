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
    logging.error("ОШИБКА: BOT_TOKEN не найден в настройках Render!")
    exit(1)

# База данных пользователей (в памяти)
# Формат: {user_id: {"model": "Название", "history": [{"date": "...", "item": "..."}]}}
users_db = {}

# Расширенные каталоги
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

# --- ВЕБ-СЕРВЕР ДЛЯ RENDER ---
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

def get_replacement_kb():
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(
        types.InlineKeyboardButton(text="1. Предфильтры", callback_data="rep_pre"),
        types.InlineKeyboardButton(text="2. Мембрана", callback_data="rep_mem"),
        types.InlineKeyboardButton(text="3. Постфильтр", callback_data="rep_post"),
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

# --- ХЕНДЛЕРЫ НАСТРОЙКИ ---
@dp.message_handler(commands=['start'])
async def cmd_start(message: types.Message):
    await message.answer("Привет! 🚰\nКакую систему фильтрации будем отслеживать?", reply_markup=get_categories_kb())

@dp.callback_query_handler(lambda c: c.data == 'back_to_cats')
@dp.callback_query_handler(lambda c: c.data.startswith('cat_'))
async def process_category(callback_query: types.CallbackQuery):
    if callback_query.data == 'back_to_cats':
        await bot.edit_message_text("Выберите тип системы:", callback_query.message.chat.id, callback_query.message.message_id, reply_markup=get_categories_kb())
    else:
        cat_key = callback_query.data.split('_')[1]
        await bot.edit_message_text("Теперь выберите модель:", callback_query.message.chat.id, callback_query.message.message_id, reply_markup=get_models_kb(cat_key))
    await bot.answer_callback_query(callback_query.id)

@dp.callback_query_handler(lambda c: c.data.startswith('mod_') and c.data != 'mod_manual')
async def process_model(callback_query: types.CallbackQuery):
    model_name = callback_query.data.replace('mod_', '')
    user_id = callback_query.from_user.id
    
    # Сохраняем систему за пользователем
    if user_id not in users_db:
        users_db[user_id] = {"history": []}
    users_db[user_id]["model"] = model_name

    await bot.edit_message_text(f"✅ Система привязана: *{model_name}*", callback_query.message.chat.id, callback_query.message.message_id)
    await bot.send_message(user_id, "Бот готов. Используйте меню:", reply_markup=get_main_menu())
    await bot.answer_callback_query(callback_query.id)

# --- ГЛАВНОЕ МЕНЮ ---
@dp.message_handler(lambda m: m.text == "📊 Статус")
async def menu_status(message: types.Message):
    user_data = users_db.get(message.from_user.id)
    if not user_data or "model" not in user_data:
        return await message.answer("Сначала выберите систему в настройках ⚙️")
    
    await message.answer(f"🔍 *Текущая система:* {user_data['model']}\n\n▫️ Предфильтры: в норме (замена через ~180 дней)\n▫️ Мембрана: в норме (замена через ~730 дней)\n▫️ Постфильтр: в норме (замена через ~365 дней)")

@dp.message_handler(lambda m: m.text == "📅 Заменил картридж")
async def menu_replacement(message: types.Message):
    user_data = users_db.get(message.from_user.id)
    if not user_data or "model" not in user_data:
        return await message.answer("Сначала выберите систему в настройках ⚙️")
    
    await message.answer(f"🛠 *Система:* {user_data['model']}\nЧто именно вы заменили сейчас?", reply_markup=get_replacement_kb())

@dp.message_handler(lambda m: m.text == "🛒 Купить картриджи")
async def menu_buy(message: types.Message):
    user_data = users_db.get(message.from_user.id)
    if not user_data or "model" not in user_data:
        return await message.answer("Сначала выберите систему в настройках ⚙️")
    
    model = user_data['model']
    await message.answer(f"Где будем искать картриджи для *{model}*?", reply_markup=get_market_kb(model))

@dp.message_handler(lambda m: m.text == "📜 История")
async def menu_history(message: types.Message):
    user_data = users_db.get(message.from_user.id)
    if not user_data or not user_data.get("history"):
        return await message.answer("📜 История замен пуста.")
    
    text = f"📜 *История замен для {user_data.get('model', 'вашей системы')}:*\n\n"
    for entry in user_data["history"][-10:]:
        text += f"▫️ {entry['date']} — {entry['item']}\n"
    await message.answer(text)

@dp.message_handler(lambda m: m.text == "⚙️ Настройки")
async def menu_settings(message: types.Message):
    await message.answer("Смена системы фильтрации:", reply_markup=get_categories_kb())

# --- ОБРАБОТКА ЗАМЕН (Инлайн) ---
@dp.callback_query_handler(lambda c: c.data.startswith('rep_'))
async def handle_replace_action(callback_query: types.CallbackQuery):
    code = callback_query.data.split('_')[1]
    if code == "cancel":
        await bot.delete_message(callback_query.message.chat.id, callback_query.message.message_id)
        return await bot.answer_callback_query(callback_query.id)

    user_id = callback_query.from_user.id
    user_data = users_db.get(user_id)
    
    if not user_data or "model" not in user_data:
        return await bot.answer_callback_query(callback_query.id, "Ошибка: система не выбрана")

    mapping = {"pre": "Предфильтры", "mem": "Мембрана", "post": "Постфильтр"}
    item_name = mapping.get(code, "Деталь")
    date_now = datetime.now().strftime("%d.%m.%Y")
    
    # Сохраняем в историю конкретного пользователя
    user_data["history"].append({"date": date_now, "item": item_name})
    
    await bot.edit_message_text(
        f"✅ Обслуживание записано!\nСистема: *{user_data['model']}*\nЗаменено: *{item_name}*\nДата: {date_now}", 
        callback_query.message.chat.id, 
        callback_query.message.message_id
    )
    await bot.answer_callback_query(callback_query.id)

# --- РУЧНОЙ ВВОД ---
@dp.callback_query_handler(lambda c: c.data == 'mod_manual')
async def manual_input_start(callback_query: types.CallbackQuery):
    await FilterStates.waiting_for_manual_name.set()
    await bot.send_message(callback_query.from_user.id, "Введите полное название вашей системы:")
    await bot.answer_callback_query(callback_query.id)

@dp.message_handler(state=FilterStates.waiting_for_manual_name)
async def manual_input_done(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    if user_id not in users_db:
        users_db[user_id] = {"history": []}
    users_db[user_id]["model"] = message.text

    await state.finish()
    await message.answer(f"✅ Установлена система: *{message.text}*", reply_markup=get_main_menu())

# --- ЗАПУСК ---
if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    loop.create_task(start_webserver())
    executor.start_polling(dp, skip_updates=True)
