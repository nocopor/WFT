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
    logging.error("ОШИБКА: BOT_TOKEN не найден в настройках Render!")
    exit(1)

# Временная история замен (в памяти)
REPLACEMENT_HISTORY = []

# Каталоги моделей
CATALOGS = {
    "osmos": ["Aquaphor DWM-101S", "Aquaphor Osmo Pro", "Geyser Prestige M", "Atoll A-550", "Prio Expert MO530"],
    "stage3": ["Aquaphor Trio", "Barrier Profi Standard", "Geyser Standard", "Aquaphor Crystal"],
    "flow": ["Barrier Expert Slim", "Aquaphor Favorit", "Geyser Bio", "Prio Praktic EU310"]
}

class FilterStates(StatesGroup):
    waiting_for_manual_name = State()

bot = Bot(token=TOKEN, parse_mode=types.ParseMode.MARKDOWN)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

# --- ВЕБ-СЕРВЕР ДЛЯ RENDER (чтобы сервис не засыпал) ---
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
        types.InlineKeyboardButton(text="💧 Обратный осмос", callback_data="cat_osmos"),
        types.InlineKeyboardButton(text="🧪 3-ступенчатый (стандарт)", callback_data="cat_stage3"),
        types.InlineKeyboardButton(text="🚰 Проточный (компакт)", callback_data="cat_flow")
    )
    return kb

def get_models_kb(category_key):
    kb = types.InlineKeyboardMarkup(row_width=1)
    models = CATALOGS.get(category_key, [])
    for model in models:
        # Обрезаем имя для callback_data, чтобы вписаться в лимит 64 байта
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

# --- ОБРАБОТКА КОМАНД И КАТЕГОРИЙ ---

@dp.message_handler(commands=['start'])
async def cmd_start(message: types.Message):
    await message.answer(
        f"Привет, {message.from_user.first_name}! 🚰\nВыберите тип вашей системы фильтрации:",
        reply_markup=get_categories_kb()
    )

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
    await bot.edit_message_text(f"✅ Система установлена: *{model_name}*", callback_query.message.chat.id, callback_query.message.message_id)
    await bot.send_message(callback_query.from_user.id, "Бот готов. Используйте меню для управления.", reply_markup=get_main_menu())
    await bot.answer_callback_query(callback_query.id)

# --- ГЛАВНОЕ МЕНЮ (Обработка текста) ---

@dp.message_handler(lambda m: m.text == "📊 Статус")
async def menu_status(message: types.Message):
    await message.answer("🔍 *Статус:* Все системы в норме.\nРесурс картриджей в среднем 80-90%.")

@dp.message_handler(lambda m: m.text == "📅 Заменил картридж")
async def menu_replacement(message: types.Message):
    await message.answer("Что именно вы заменили?", reply_markup=get_replacement_kb())

@dp.message_handler(lambda m: m.text == "📜 История")
async def menu_history(message: types.Message):
    if not REPLACEMENT_HISTORY:
        await message.answer("История замен пока пуста.")
        return
    
    text = "📜 *История замен (последние 10):*\n\n"
    for entry in REPLACEMENT_HISTORY[-10:]:
        text += f"▫️ {entry['date']} — {entry['item']}\n"
    await message.answer(text)

@dp.message_handler(lambda m: m.text == "⚙️ Настройки")
async def menu_settings(message: types.Message):
    await message.answer("Выберите новый тип или модель фильтра:", reply_markup=get_categories_kb())

# --- ОБРАБОТКА ЗАМЕН (Инлайн кнопки) ---

@dp.callback_query_handler(lambda c: c.data.startswith('rep_'))
async def handle_replace_action(callback_query: types.CallbackQuery):
    code = callback_query.data.split('_')[1]
    if code == "cancel":
        await bot.delete_message(callback_query.message.chat.id, callback_query.message.message_id)
        await bot.answer_callback_query(callback_query.id)
        return

    mapping = {"pre": "Предфильтры", "mem": "Мембрана", "post": "Постфильтр"}
    item_name = mapping.get(code, "Неизвестный блок")
    date_now = datetime.now().strftime("%d.%m.%Y %H:%M")
    
    # Сохраняем в список
    REPLACEMENT_HISTORY.append({"date": date_now, "item": item_name})
    
    await bot.edit_message_text(
        f"✅ Данные сохранены!\nЗаменено: *{item_name}*\nДата: {date_now}", 
        callback_query.message.chat.id, 
        callback_query.message.message_id
    )
    await bot.answer_callback_query(callback_query.id)

# --- РУЧНОЙ ВВОД ---

@dp.callback_query_handler(lambda c: c.data == 'mod_manual')
async def manual_input_start(callback_query: types.CallbackQuery):
    await FilterStates.waiting_for_manual_name.set()
    await bot.send_message(callback_query.from_user.id, "Введите название вашей системы:")
    await bot.answer_callback_query(callback_query.id)

@dp.message_handler(state=FilterStates.waiting_for_manual_name)
async def manual_input_done(message: types.Message, state: FSMContext):
    await state.finish()
    await message.answer(f"✅ Записал модель: *{message.text}*", reply_markup=get_main_menu())

# --- ЗАПУСК ---
if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    loop.create_task(start_webserver())
    executor.start_polling(dp, skip_updates=True)
