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
TOKEN = os.getenv("TOKEN")
logging.basicConfig(level=logging.INFO)

# Список моделей
RO_MODELS = [
    "Aquaphor DWM-101S Morion", "Aquaphor DWM-202S", "Aquaphor Osmo Pro 50",
    "Barrier Profi Osmo 100", "Barrier Compact Osmo", "Geyser Prestige M",
    "Geyser Allegro", "Atoll A-550 (Patriot)", "Atoll A-575m STD",
    "Prio Expert Osmos MO530", "Xiaomi Mi Water Purifier", "Ecosoft Standard"
]

# Состояния
class FilterStates(StatesGroup):
    waiting_for_manual_name = State()

bot = Bot(token=TOKEN, parse_mode=types.ParseMode.MARKDOWN)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

# --- ВЕБ-СЕРВЕР ДЛЯ RENDER (чтобы не падал Deploy) ---
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
    kb.add("📊 Статус", "📅 Заменил картридж")
    kb.add("⚙️ Настройки")
    return kb

def get_osmos_kb():
    kb = types.InlineKeyboardMarkup(row_width=1)
    for model in RO_MODELS:
        # В 2.x callback_data — это просто строка
        kb.add(types.InlineKeyboardButton(text=model, callback_data=f"set_{model[:20]}"))
    kb.add(types.InlineKeyboardButton(text="📝 Свой вариант", callback_data="set_manual"))
    return kb

# --- ХЕНДЛЕРЫ ---

@dp.message_handler(commands=['start'])
async def cmd_start(message: types.Message):
    await message.answer(
        f"Привет, {message.from_user.first_name}! 🚰\n"
        "Сразу выберем твою систему осмоса, чтобы я мог следить за фильтрами:",
        reply_markup=get_osmos_kb()
    )

# Выбор из списка
@dp.callback_query_handler(lambda c: c.data.startswith('set_') and c.data != 'set_manual')
async def process_callback_set(callback_query: types.CallbackQuery):
    selected_model = callback_query.data.replace('set_', '')
    await bot.answer_callback_query(callback_query.id)
    await bot.edit_message_text(
        chat_id=callback_query.message.chat.id,
        message_id=callback_query.message.message_id,
        text=f"✅ Выбрана система: *{selected_model}*\nДобро пожаловать!"
    )
    await bot.send_message(callback_query.from_user.id, "Теперь используй меню:", reply_markup=get_main_menu())

# Переход в ручной ввод
@dp.callback_query_handler(lambda c: c.data == 'set_manual')
async def process_manual_start(callback_query: types.CallbackQuery):
    await FilterStates.waiting_for_manual_name.set()
    await bot.answer_callback_query(callback_query.id)
    await bot.send_message(callback_query.from_user.id, "Введите название вашей системы вручную:")

# Обработка ручного ввода
@dp.message_handler(state=FilterStates.waiting_for_manual_name)
async def load_name(message: types.Message, state: FSMContext):
    user_model = message.text
    await state.finish()
    await message.answer(f"✅ Записал: *{user_model}*", reply_markup=get_main_menu())

# --- ЗАПУСК ---
if __name__ == '__main__':
    # Запускаем веб-сервер в фоне
    loop = asyncio.get_event_loop()
    loop.create_task(start_webserver())
    
    # Запускаем бота
    executor.start_polling(dp, skip_updates=True)
