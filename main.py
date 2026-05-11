import os
import asyncio
import json
import logging
import httpx
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiohttp import web

# --- НАСТРОЙКИ ---
TOKEN = os.getenv("BOT_TOKEN")
GIST_ID = os.getenv("GIST_ID")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
PORT = int(os.getenv("PORT", 10000))

logging.basicConfig(level=logging.INFO)
bot = Bot(token=TOKEN)
dp = Dispatcher()

class FilterStates(StatesGroup):
    add_name = State()
    add_model = State()
    add_interval = State()

# --- РАБОТА С GITHUB GIST ---
async def load_db():
    url = f"https://api.github.com/gists/{GIST_ID}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}"}
    async with httpx.AsyncClient() as client:
        try:
            r = await client.get(url, headers=headers)
            content = r.json()['files']['filters_data.json']['content']
            return json.loads(content)
        except Exception as e:
            logging.error(f"Ошибка загрузки: {e}")
            return {}

async def save_db(data):
    url = f"https://api.github.com/gists/{GIST_ID}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}"}
    payload = {"files": {"filters_data.json": {"content": json.dumps(data, indent=2)}}}
    async with httpx.AsyncClient() as client:
        await client.patch(url, headers=headers, json=payload)

# --- КЛАВИАТУРА ---
def main_kb():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="📊 Мои фильтры")],
        [KeyboardButton(text="➕ Добавить фильтр")]
    ], resize_keyboard=True)

# --- ОБРАБОТЧИКИ ---
@dp.message(Command("start"))
async def start(message: types.Message):
    await message.answer(
        "🚰 **Бот-трекер фильтров готов!**\n\nТеперь все твои данные хранятся в GitHub Gist. Даже если сервер перезагрузится, ничего не сотрется.",
        reply_markup=main_kb(), parse_mode="Markdown"
    )

@dp.message(F.text == "➕ Добавить фильтр")
async def add_start(message: types.Message, state: FSMContext):
    await state.set_state(FilterStates.add_name)
    await message.answer("Введите название (например: Осмос 1-ступень):")

@dp.message(FilterStates.add_name)
async def add_name(message: types.Message, state: FSMContext):
    await state.update_data(name=message.text)
    await state.set_state(FilterStates.add_model)
    await message.answer("Введите модель (например: МП-5В):")

@dp.message(FilterStates.add_model)
async def add_model(message: types.Message, state: FSMContext):
    await state.update_data(model=message.text)
    await state.set_state(FilterStates.add_interval)
    await message.answer("Срок замены в месяцах (число):")

@dp.message(FilterStates.add_interval)
async def add_interval(message: types.Message, state: FSMContext):
    if not message.text.isdigit():
        return await message.answer("Пожалуйста, введите только число.")
    
    data = await state.get_data()
    uid = str(message.from_user.id)
    
    db = await load_db()
    if uid not in db: db[uid] = []
    
    db[uid].append({
        "name": data['name'],
        "model": data['model'],
        "interval": int(message.text),
        "last_date": datetime.now().strftime("%Y-%m-%d")
    })
    
    await save_db(db)
    await message.answer(f"✅ Фильтр '{data['name']}' добавлен и сохранен в облако!", reply_markup=main_kb())
    await state.clear()

@dp.message(F.text == "📊 Мои фильтры")
async def show_status(message: types.Message):
    db = await load_db()
    uid = str(message.from_user.id)
    user_filters = db.get(uid, [])
    
    if not user_filters:
        return await message.answer("У вас пока нет фильтров.")

    res = "📋 **Ваши фильтры:**\n\n"
    for f in user_filters:
        last_dt = datetime.strptime(f["last_date"], "%Y-%m-%d")
        next_dt = last_dt + timedelta(days=f["interval"] * 30)
        days_left = (next_dt - datetime.now()).days
        
        icon = "🟢" if days_left > 15 else "🟡" if days_left > 0 else "🔴"
        res += f"{icon} **{f['name']}** ({f['model']})\n└ Замена через: {days_left} дн.\n\n"
    
    await message.answer(res, parse_mode="Markdown")

# --- СЕРВЕР ДЛЯ RENDER ---
async def handle_hc(request): return web.Response(text="OK")

async def main():
    app = web.Application()
    app.router.add_get("/", handle_hc)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
