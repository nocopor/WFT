import logging
import os
import asyncio
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
    logging.error("ОШИБКА: BOT_TOKEN не найден в Render!")
    exit(1)

# Данные моделей
CATALOGS = {
    "osmos": [
        "Aquaphor DWM-101S", "Aquaphor Osmo Pro", "Geyser Prestige M", 
        "Atoll A-550", "Barrier Profi Osmo", "Prio Expert MO530"
    ],
    "stage3": [
        "Aquaphor Trio", "Barrier Profi Standard", "Geyser Standard",
        "Aquaphor Crystal", "Prio Expert M310"
    ],
    "flow": [
        "Barrier Expert Slim", "Aquaphor Favorit", "Geyser Bio",
        "Prio Praktic EU310"
    ]
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
    kb.row("⚙️ Настройки")
    return kb

def get_categories_kb():
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(
        types.InlineKeyboardButton(text="💧 Обратный осмос", callback_query_data="cat_osmos"),
        types.InlineKeyboardButton(text="🧪 3-ступенчатый (стандарт)", callback_query_data="cat_stage3"),
        types.InlineKeyboardButton(text="🚰 Проточный (компакт)", callback_query_data="cat_flow")
    )
    return kb

def get_models_kb(category_key):
    kb = types.InlineKeyboardMarkup(row_width=1)
    models = CATALOGS.get(category_key, [])
    for model in models:
        kb.add(types.InlineKeyboardButton(text=model, callback_data=f"model_{model[:25]}"))
    kb.add(types.InlineKeyboardButton(text="📝 Свой вариант", callback_data="model_manual"))
    kb.add(types.InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_cats"))
    return kb

# --- ХЕНДЛЕРЫ ВЫБОРА ---

@dp.message_handler(commands=['start'])
async def cmd_start(message: types.Message):
    await message.answer(
        "Добро пожаловать! 🚰\nВыберите тип вашей системы фильтрации:",
        reply_markup=get_categories_kb()
    )

@dp.callback_query_handler(lambda c: c.data.startswith('cat_'))
async def process_category(callback_query: types.CallbackQuery):
    cat_key = callback_query.data.split('_')[1]
    await bot.edit_message_text(
        chat_id=callback_query.message.chat.id,
        message_id=callback_query.message.message_id,
        text="Теперь выберите модель из списка:",
        reply_markup=get_models_kb(cat_key)
    )
    await bot.answer_callback_query(callback_query.id)

@dp.callback_query_handler(lambda c: c.data == 'back_to_cats')
async def back_to_categories(callback_query: types.CallbackQuery):
    await bot.edit_message_text(
        chat_id=callback_query.message.chat.id,
        message_id=callback_query.message.message_id,
        text="Выберите тип вашей системы фильтрации:",
        reply_markup=get_categories_kb()
    )
    await bot.answer_callback_query(callback_query.id)

@dp.callback_query_handler(lambda c: c.data.startswith('model_') and c.data != 'model_manual')
async def process_model_selection(callback_query: types.CallbackQuery):
    selected_model = callback_query.data.replace('model_', '')
    await bot.edit_message_text(
        chat_id=callback_query.message.chat.id,
        message_id=callback_query.message.message_id,
        text=f"✅ Установлена система: *{selected_model}*"
    )
    await bot.send_message(
        callback_query.from_user.id, 
        "Настройка завершена! Используйте меню ниже:", 
        reply_markup=get_main_menu()
    )
    await bot.answer_callback_query(callback_query.id)

@dp.callback_query_handler(lambda c: c.data == 'model_manual')
async def process_manual_start(callback_query: types.CallbackQuery):
    await FilterStates.waiting_for_manual_name.set()
    await bot.send_message(callback_query.from_user.id, "Введите название вашей системы вручную:")
    await bot.answer_callback_query(callback_query.id)

@dp.message_handler(state=FilterStates.waiting_for_manual_name)
async def process_manual_name(message: types.Message, state: FSMContext):
    await state.finish()
    await message.answer(f"✅ Записал: *{message.text}*", reply_markup=get_main_menu())

# --- ХЕНДЛЕРЫ ГЛАВНОГО МЕНЮ (То, что не работало) ---

@dp.message_handler(lambda message: message.text == "📊 Статус")
async def menu_status(message: types.Message):
    # Здесь позже добавим логику расчета дат
    await message.answer("🔍 *Статус фильтров:*\n\nВсе картриджи в норме. До замены осталось примерно 180 дней.")

@dp.message_handler(lambda message: message.text == "📅 Заменил картридж")
async def menu_replacement(message: types.Message):
    await message.answer("Какую ступень вы заменили? (Здесь будет выбор ступеней)")

@dp.message_handler(lambda message: message.text == "⚙️ Настройки")
async def menu_settings(message: types.Message):
    await message.answer("⚙️ *Настройки:*\n\nЗдесь можно изменить модель фильтра или настроить уведомления.", reply_markup=get_categories_kb())

# --- ЗАПУСК ---
if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    loop.create_task(start_webserver())
    executor.start_polling(dp, skip_updates=True)
