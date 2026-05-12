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
# Используем именно BOT_TOKEN, как в твоем Render
TOKEN = os.getenv("BOT_TOKEN")

logging.basicConfig(level=logging.INFO)

if not TOKEN:
    logging.error("ОШИБКА: Переменная BOT_TOKEN не найдена!")
    exit(1)

# Большой каталог моделей
RO_MODELS = [
    "Aquaphor DWM-101S Morion", "Aquaphor DWM-202S", "Aquaphor Osmo Pro 50",
    "Barrier Profi Osmo 100", "Barrier Compact Osmo", "Geyser Prestige M",
    "Geyser Allegro", "Atoll A-550 (Patriot)", "Atoll A-575m STD",
    "Prio Expert Osmos MO530", "Xiaomi Mi Water Purifier", "Ecosoft Standard",
    "Angstra R-5C", "Raifil Grando 5", "Honeywell OT300"
]

class FilterStates(StatesGroup):
    waiting_for_manual_name = State()

bot = Bot(token=TOKEN, parse_mode=types.ParseMode.MARKDOWN)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

# --- МИКРО-СЕРВЕР ДЛЯ RENDER (Порт 10000) ---
async def handle(request):
    return web.Response(text="Bot is alive and kicking!")

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
    kb.add("📊 Статус", "📅 Заменил картридж")
    kb.add("⚙️ Настройки")
    return kb

def get_osmos_kb():
    kb = types.InlineKeyboardMarkup(row_width=1)
    for model in RO_MODELS:
        # В 2.x callback_data — это просто строка (лимит 64 байта)
        kb.add(types.InlineKeyboardButton(text=model, callback_data=f"set_{model[:25]}"))
    kb.add(types.InlineKeyboardButton(text="📝 Свой вариант", callback_data="set_manual"))
    return kb

# --- ХЕНДЛЕРЫ ---

@dp.message_handler(commands=['start'])
async def cmd_start(message: types.Message):
    await message.answer(
        f"Привет, {message.from_user.first_name}! 🚰\n\n"
        "Давай настроим твою систему фильтрации. Выбери модель из списка ниже:",
        reply_markup=get_osmos_kb()
    )

# Обработка выбора из каталога
@dp.callback_query_handler(lambda c: c.data.startswith('set_') and c.data != 'set_manual')
async def process_selection(callback_query: types.CallbackQuery):
    selected_model = callback_query.data.replace('set_', '')
    await bot.answer_callback_query(callback_query.id)
    
    # Редактируем сообщение с каталогом
    await bot.edit_message_text(
        chat_id=callback_query.message.chat.id,
        message_id=callback_query.message.message_id,
        text=f"✅ Выбрана система: *{selected_model}*"
    )
    
    # Отправляем главное меню
    await bot.send_message(
        callback_query.from_user.id, 
        "Бот настроен. Теперь ты можешь проверять статус или отмечать замену картриджей.", 
        reply_markup=get_main_menu()
    )

# Кнопка ручного ввода
@dp.callback_query_handler(lambda c: c.data == 'set_manual')
async def process_manual_start(callback_query: types.CallbackQuery):
    await FilterStates.waiting_for_manual_name.set()
    await bot.answer_callback_query(callback_query.id)
    await bot.send_message(callback_query.from_user.id, "Введите название вашей системы вручную:")

# Прием ручного названия
@dp.message_handler(state=FilterStates.waiting_for_manual_name)
async def process_manual_name(message: types.Message, state: FSMContext):
    user_model = message.text
    await state.finish()
    await message.answer(f"✅ Записал: *{user_model}*.\n\nДобро пожаловать в меню!", reply_markup=get_main_menu())

# --- ЗАПУСК ---
if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    # Запускаем "заглушку" для порта 10000
    loop.create_task(start_webserver())
    
    # Запускаем поллинг бота
    executor.start_polling(dp, skip_updates=True)
