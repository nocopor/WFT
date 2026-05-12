import logging
import os
import asyncio
from datetime import datetime
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

# Временное хранилище истории (очищается при перезагрузке сервера)
REPLACEMENT_HISTORY = []

CATALOGS = {
    "osmos": ["Aquaphor DWM-101S", "Aquaphor Osmo Pro", "Geyser Prestige M", "Atoll A-550"],
    "stage3": ["Aquaphor Trio", "Barrier Profi Standard", "Geyser Standard"],
    "flow": ["Barrier Expert Slim", "Aquaphor Favorit", "Geyser Bio"]
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
    kb.row("📜 История", "⚙️ Настройки")
    return kb

def get_categories_kb():
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(
        types.InlineKeyboardButton(text="💧 Обратный осмос", callback_query_data="cat_osmos"),
        types.InlineKeyboardButton(text="🧪 3-ступенчатый", callback_query_data="cat_stage3"),
        types.InlineKeyboardButton(text="🚰 Проточный", callback_query_data="cat_flow")
    )
    return kb

def get_models_kb(category_key):
    kb = types.InlineKeyboardMarkup(row_width=1)
    for model in CATALOGS.get(category_key, []):
        kb.add(types.InlineKeyboardButton(text=model, callback_data=f"model_{model[:25]}"))
    kb.add(types.InlineKeyboardButton(text="📝 Свой вариант", callback_data="model_manual"))
    return kb

def get_replacement_kb():
    """Кнопки для выбора того, что именно заменили"""
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(
        types.InlineKeyboardButton(text="1. Набор предфильтров", callback_query_data="replace_prefilter"),
        types.InlineKeyboardButton(text="2. Мембрана", callback_query_data="replace_membrane"),
        types.InlineKeyboardButton(text="3. Постфильтр / Минерализатор", callback_query_data="replace_postfilter"),
        types.InlineKeyboardButton(text="❌ Отмена", callback_query_data="replace_cancel")
    )
    return kb

# --- ХЕНДЛЕРЫ НАСТРОЙКИ ---

@dp.message_handler(commands=['start'])
async def cmd_start(message: types.Message):
    await message.answer("Привет! Выберите тип вашей системы:", reply_markup=get_categories_kb())

@dp.callback_query_handler(lambda c: c.data.startswith('cat_'))
async def process_category(callback_query: types.CallbackQuery):
    cat_key = callback_query.data.split('_')[1]
    await bot.edit_message_text("Выберите модель:", callback_query.message.chat.id, callback_query.message.message_id, reply_markup=get_models_kb(cat_key))

@dp.callback_query_handler(lambda c: c.data.startswith('model_') and c.data != 'model_manual')
async def process_model(callback_query: types.CallbackQuery):
    model = callback_query.data.replace('model_', '')
    await bot.edit_message_text(f"✅ Система установлена: *{model}*", callback_query.message.chat.id, callback_query.message.message_id)
    await bot.send_message(callback_query.from_user.id, "Используйте меню:", reply_markup=get_main_menu())

# --- ХЕНДЛЕРЫ ГЛАВНОГО МЕНЮ ---

@dp.message_handler(lambda m: m.text == "📊 Статус")
async def menu_status(message: types.Message):
    await message.answer("🔍 *Статус:* Все системы работают штатно.\nБлижайшая замена через 6 месяцев.")

@dp.message_handler(lambda m: m.text == "📅 Заменил картридж")
async def menu_replacement(message: types.Message):
    await message.answer("Что именно вы заменили?", reply_markup=get_replacement_kb())

@dp.message_handler(lambda m: m.text == "📜 История")
async def menu_history(message: types.Message):
    if not REPLACEMENT_HISTORY:
        await message.answer("📜 История пока пуста. Вы еще не отмечали замену картриджей.")
        return
    
    history_text = "📜 *История замен:*\n\n"
    for entry in REPLACEMENT_HISTORY[-10:]: # Показываем последние 10 записей
        history_text += f"▫️ {entry['date']}: {entry['item']}\n"
    
    await message.answer(history_text)

@dp.message_handler(lambda m: m.text == "⚙️ Настройки")
async def menu_settings(message: types.Message):
    await message.answer("Выберите новый тип системы:", reply_markup=get_categories_kb())

# --- ОБРАБОТКА ЗАМЕНЫ (Callback) ---

@dp.callback_query_handler(lambda c: c.data.startswith('replace_'))
async def process_replacement_callback(callback_query: types.CallbackQuery):
    code = callback_query.data.replace('replace_', '')
    
    if code == "cancel":
        await bot.delete_message(callback_query.message.chat.id, callback_query.message.message_id)
        return

    # Маппинг названий
    names = {
        "prefilter": "Набор предфильтров",
        "membrane": "Мембрана",
        "postfilter": "Постфильтр"
    }
    
    item_name = names.get(code, "Неизвестный элемент")
    date_str = datetime.now().strftime("%d.%m.%Y")
    
    # Сохраняем в историю
    REPLACEMENT_HISTORY.append({"date": date_str, "item": item_name})
    
    await bot.edit_message_text(
        f"✅ Запись добавлена!\nДата: *{date_str}*\nЗаменено: *{item_name}*",
        callback_query.message.chat.id,
        callback_query.message.message_id
    )
    await bot.answer_callback_query(callback_query.id, text="Данные сохранены")

# --- ЗАПУСК ---
if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    loop.create_task(start_webserver())
    executor.start_polling(dp, skip_updates=True)
