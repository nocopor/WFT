import os, asyncio, json, logging, httpx
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiohttp import web

TOKEN = os.getenv("BOT_TOKEN")
GIST_ID = os.getenv("GIST_ID")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
PORT = int(os.getenv("PORT", 10000))

logging.basicConfig(level=logging.INFO)
bot = Bot(token=TOKEN)
dp = Dispatcher()

class FilterStates(StatesGroup):
    add_name = State()
    add_interval = State()
    add_date = State()

async def load_db():
    url = f"https://api.github.com/gists/{GIST_ID}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}"}
    async with httpx.AsyncClient() as client:
        try:
            r = await client.get(url, headers=headers)
            # Берем содержимое файла filters_data.json
            content = r.json()['files']['filters_data.json']['content']
            return json.loads(content)
        except Exception as e:
            logging.error(f"Ошибка Gist: {e}")
            return {}

async def save_db(data):
    url = f"https://api.github.com/gists/{GIST_ID}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}"}
    payload = {"files": {"filters_data.json": {"content": json.dumps(data, indent=2)}}}
    async with httpx.AsyncClient() as client:
        await client.patch(url, headers=headers, json=payload)

def main_kb():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="📊 Статус")], [KeyboardButton(text="➕ Добавить фильтр")]
    ], resize_keyboard=True)

@dp.message(Command("start"))
async def start(message: types.Message):
    await message.answer("✅ Бот готов к работе с Gist!", reply_markup=main_kb())

@dp.message(F.text == "➕ Добавить фильтр")
async def add_f(message: types.Message, state: FSMContext):
    await state.set_state(FilterStates.add_name)
    await message.answer("Введите название (например: Холодная вода):")

@dp.message(FilterStates.add_name)
async def add_n(message: types.Message, state: FSMContext):
    await state.update_data(name=message.text)
    await state.set_state(FilterStates.add_interval)
    await message.answer("Раз в сколько месяцев менять? (число):")

@dp.message(FilterStates.add_interval)
async def add_i(message: types.Message, state: FSMContext):
    if not message.text.isdigit(): return await message.answer("Нужно число.")
    await state.update_data(interval=int(message.text))
    await state.set_state(FilterStates.add_date)
    await message.answer("Когда меняли последний раз? (ДД.ММ.ГГГГ)\nИли напиши 'сегодня'")

@dp.message(FilterStates.add_date)
async def add_d(message: types.Message, state: FSMContext):
    user_date = message.text.lower()
    if user_date == "сегодня":
        clean_date = datetime.now().strftime("%Y-%m-%d")
    else:
        try:
            clean_date = datetime.strptime(user_date, "%d.%m.%Y").strftime("%Y-%m-%d")
        except:
            return await message.answer("❌ Формат должен быть ДД.ММ.ГГГГ (например 20.01.2024)")

    data = await state.get_data()
    uid = str(message.from_user.id)
    
    db = await load_db()
    if uid not in db: db[uid] = []
    
    db[uid].append({
        "name": data['name'],
        "interval": data['interval'],
        "last_date": clean_date
    })
    
    await save_db(db)
    await message.answer(f"✅ Фильтр '{data['name']}' сохранен!", reply_markup=main_kb())
    await state.clear()

@dp.message(F.text == "📊 Статус")
async def show(message: types.Message):
    db = await load_db()
    user_filters = db.get(str(message.from_user.id), [])
    if not user_filters: return await message.answer("Пусто.")
    
    res = "📋 **Сроки замены:**\n\n"
    for f in user_filters:
        last = datetime.strptime(f['last_date'], "%Y-%m-%d")
        next_d = last + timedelta(days=f['interval']*30)
        days = (next_d - datetime.now()).days
        icon = "🟢" if days > 15 else "🔴"
        res += f"{icon} **{f['name']}**\n└ До: {next_d.strftime('%d.%m.%Y')} ({days} дн.)\n\n"
    await message.answer(res, parse_mode="Markdown")

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
