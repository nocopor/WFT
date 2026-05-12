import asyncio
import logging
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

# --- КОНФИГУРАЦИЯ ---
TOKEN = "ВАШ_ТЕЛЕГРАМ_ТОКЕН"

RO_MODELS = [
    "Aquaphor DWM-101S Morion", "Aquaphor DWM-202S", "Aquaphor Osmo Pro 50",
    "Barrier Profi Osmo 100", "Barrier Compact Osmo", "Geyser Prestige M",
    "Geyser Allegro", "Atoll A-550 (Patriot)", "Atoll A-575m STD",
    "Prio Expert Osmos MO530", "Xiaomi Mi Water Purifier", "Ecosoft Standard"
]

logging.basicConfig(level=logging.INFO)

# --- МОДЕЛИ И СОСТОЯНИЯ ---
class OsmosCallback(CallbackData, prefix="os"):
    action: str
    model: str

class FilterStates(StatesGroup):
    choosing_osmos = State()      # Состояние выбора из списка
    waiting_for_manual = State()  # Состояние ручного ввода

router = Router()

# --- КЛАВИАТУРЫ ---

def get_main_menu():
    """Главное меню, которое появится ПОСЛЕ выбора фильтра"""
    kb = [
        [KeyboardButton(text="📊 Статус фильтров")],
        [KeyboardButton(text="⚙️ Настройки"), KeyboardButton(text="📅 Заменил картридж")]
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def get_osmos_catalog_kb():
    """Инлайн-кнопки для выбора модели"""
    builder = InlineKeyboardBuilder()
    for model in RO_MODELS:
        builder.button(
            text=model,
            callback_data=OsmosCallback(action="select", model=model[:25])
        )
    builder.button(text="📝 Свой вариант", callback_data=OsmosCallback(action="manual", model="none"))
    builder.adjust(1)
    return builder.as_markup()

# --- ХЕНДЛЕРЫ ---

@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await state.clear() # Сбрасываем всё, если пользователь решил начать заново
    await message.answer(
        f"Привет, {message.from_user.first_name}! 🚰\n\n"
        "Я помогу тебе не забыть о замене картриджей в твоем осмосе.\n"
        "Для начала работы **выбери модель своей системы** из списка ниже:"
    )
    await message.answer("Каталог систем обратного осмоса:", reply_markup=get_osmos_catalog_kb())
    await state.set_state(FilterStates.choosing_osmos)

# 1. Обработка выбора из списка
@router.callback_query(OsmosCallback.filter(F.action == "select"))
async def process_selection(callback: CallbackQuery, callback_data: OsmosCallback, state: FSMContext):
    selected_model = callback_data.model
    await state.update_data(chosen_model=selected_model)
    
    await callback.message.edit_text(f"✅ Установлено: **{selected_model}**")
    await callback.message.answer(
        "Система настроена! Теперь ты можешь следить за ресурсом фильтров через меню.",
        reply_markup=get_main_menu()
    )
    await state.clear() # Выходим из режима настройки
    await callback.answer()

# 2. Переход к ручному вводу
@router.callback_query(OsmosCallback.filter(F.action == "manual"))
async def process_manual_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(FilterStates.waiting_for_manual)
    await callback.message.edit_text("Хорошо, введи название своей системы (например: 'Мой Осмос 500'):")
    await callback.answer()

# 3. Прием ручного названия
@router.message(FilterStates.waiting_for_manual)
async def process_manual_name(message: Message, state: FSMContext):
    user_model = message.text
    await message.answer(
        f"✅ Записал: **{user_model}**.\nДобро пожаловать в главное меню!",
        reply_markup=get_main_menu()
    )
    await state.clear()

# --- ЗАПУСК ---
async def main():
    bot = Bot(token=TOKEN)
    dp = Dispatcher()
    dp.include_router(router)
    
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
