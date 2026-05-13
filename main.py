import logging
import os
import asyncio
import json
import aiohttp
from datetime import datetime, timedelta
from urllib.parse import quote
from aiogram import Bot, Dispatcher, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.utils import executor
from aiohttp import web

# --- КОНФИГУРАЦИЯ ---
TOKEN = os.getenv("BOT_TOKEN")
GIST_ID = os.getenv("GIST_ID")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

logging.basicConfig(level=logging.INFO)
users_db = {}

# --- СИНХРОНИЗАЦИЯ ---
async def sync_gist(action="load"):
    global users_db
    if not GIST_ID or not GITHUB_TOKEN: return
    headers = {"Authorization": f"token {GITHUB_TOKEN}"}
    url = f"https://api.github.com/gists/{GIST_ID}"
    async with aiohttp.ClientSession() as session:
        try:
            if action == "load":
                async with session.get(url, headers=headers) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        content = data['files']['filters_db.json']['content']
                        users_db = json.loads(content)
            elif action == "save":
                payload = {"files": {"filters_db.json": {"content": json.dumps(users_db, ensure_ascii=False, indent=2)}}}
                await session.patch(url, headers=headers, json=payload)
        except Exception as e: logging.error(f"Sync Error: {e}")

# --- КАТАЛОГ (18 БРЕНДОВ) ---
CATALOGS = {
    "osmos": {
        "Атолл": ["A-550", "A-575", "A-550m", "A-575m", "A-450"],
        "Барьер": ["Профи Осмо 100", "Профи Осмо 100 М", "Compact Osmo"],
        "Гейзер": ["Престиж", "Аллегро", "Престиж М", "Аллегро М"],
        "Аквафор": ["DWM-101S Морион", "DWM-102S", "Осмо Про 50"],
        "Prio": ["Expert Osmos MO530", "Expert Osmos MO600", "Start Osmos"],
        "Raifil": ["Grando 5", "Grando 6", "Grando 7"]
    },
    "stage3": {
        "Атолл": ["Патриот", "D-31"],
        "Гейзер": ["Макс", "Стандарт", "БИО"],
        "Аквафор": ["Кристалл Эко", "Трио", "Кристалл Н"],
        "Барьер": ["Эксперт Стандарт", "Эксперт Жесткость", "Эксперт Комплекс"],
        "Prio": ["Expert M300", "Expert M310", "Expert M410"],
        "Xiaomi": ["Mi Water Purifier (3 stage)", "Millet Water Purifier"]
    },
    "flow": {
        "Аквафор": ["Фаворит", "Викинг 10SL", "Викинг 10BB"],
        "Гейзер": ["Тайфун 10SL", "Тайфун 10BB", "Тайфун 20BB"],
        "Барьер": ["Профи В1", "Профи В2", "Профи В3"],
        "Джилекс": ["Колба 10SL", "Колба 10BB", "Колба 20BB"],
        "Honeywell": ["FF06", "FK06", "F76S"],
        "ITA Filter": ["ITA-01", "ITA-02", "ITA-03"]
    }
}

FILTER_CONFIGS = {
    "osmos": {
        "pre": {"name": "Предфильтры (1-3)", "interval": 6},
        "mem": {"name": "Мембрана RO", "interval": 24}, 
        "post": {"name": "Постфильтр", "interval": 12},
        "min": {"name": "Минерализатор", "interval": 12}
    },
    "stage3": {"set": {"name": "Комплект картриджей", "interval": 12}},
    "flow": {"cart": {"name": "Сменный модуль", "interval": 6}}
}

class FilterStates(StatesGroup):
    waiting_for_manual_name = State()
    waiting_for_custom_interval = State()
    waiting_for_date = State()
    waiting_for_interval_change = State()

bot = Bot(token=TOKEN, parse_mode=types.ParseMode.HTML)
dp = Dispatcher(bot, storage=MemoryStorage())

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---
def get_main_menu():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("📊 Статус", "➕ Добавить фильтр")
    kb.row("📅 Заменил картридж", "🛒 Купить картриджи")
    kb.row("📜 История", "⚙️ Настройки")
    return kb

def get_back_btn(callback_data):
    return types.InlineKeyboardButton("⬅️ Назад", callback_data=callback_data)

# --- ЛОГИКА НАПОМИНАНИЙ ---

async def reminder_scheduler():
    """Фоновая задача, которая проверяет ресурс раз в 4 часа"""
    while True:
        now = datetime.now()
        # Проверяем только в дневное время (с 10 до 21), чтобы не будить ночью
        if 10 <= now.hour <= 21:
            for uid, user_data in users_db.items():
                # Проверяем, не отложено ли напоминание
                snooze_until_str = user_data.get("snooze_until")
                if snooze_until_str:
                    snooze_dt = datetime.strptime(snooze_until_str, "%Y-%m-%d %H:%M:%S")
                    if now < snooze_dt: continue

                # Проверка систем
                alert_msg = ""
                for f_idx, filter_sys in enumerate(user_data.get("filters", [])):
                    for code, interval in filter_sys['intervals'].items():
                        if interval == 0: continue
                        
                        last_date_str = next((h['date'] for h in reversed(filter_sys['history']) if h['item'] != "Начало обслуживания"), filter_sys['created_at'])
                        last_date = datetime.strptime(last_date_str, "%d.%m.%Y")
                        
                        days_left = int(interval * 30.4 - (now - last_date).days)
                        if days_left <= 7: # Напоминаем, когда осталось меньше недели
                            name = FILTER_CONFIGS[filter_sys['category']][code]['name']
                            alert_msg += f"⚠️ <b>{filter_sys['model']}</b>\nНужно заменить: <i>{name}</i>\n"
                
                if alert_msg:
                    kb = types.InlineKeyboardMarkup(row_width=2)
                    kb.add(
                        types.InlineKeyboardButton("🕒 Завтра", callback_data="sn_1"),
                        types.InlineKeyboardButton("🗓 Через неделю", callback_data="sn_7"),
                        types.InlineKeyboardButton("🛒 Купить", callback_data="buy_0")
                    )
                    try:
                        await bot.send_message(uid, f"🔔 <b>ПОРА ОБСЛУЖИТЬ ФИЛЬТР!</b>\n\n{alert_msg}", reply_markup=kb)
                        # Чтобы не спамить каждый час, ставим авто-паузу на 24 часа после уведомления
                        users_db[uid]["snooze_until"] = (now + timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
                        await sync_gist("save")
                    except: pass
                    
        await asyncio.sleep(3600) # Проверка раз в час

@dp.callback_query_handler(lambda c: c.data.startswith("sn_"), state='*')
async def process_snooze(callback_query: types.CallbackQuery):
    days = int(callback_query.data.split('_')[1])
    uid = str(callback_query.from_user.id)
    until_dt = datetime.now() + timedelta(days=days)
    
    if uid not in users_db: users_db[uid] = {} # На всякий случай
    users_db[uid]["snooze_until"] = until_dt.strftime("%Y-%m-%d %H:%M:%S")
    await sync_gist("save")
    
    text = "на завтра" if days == 1 else "на неделю"
    await bot.answer_callback_query(callback_query.id, f"Напоминание отложено {text}")
    await bot.edit_message_text(f"✅ Хорошо! Напомню {text}.", callback_query.message.chat.id, callback_query.message.message_id)

# --- ДАЛЕЕ КОД ОСТАЕТСЯ ПРЕЖНИМ (ИСПРАВЛЕННЫЙ ИЗ v11.9) ---

@dp.message_handler(commands=['start'], state='*')
async def cmd_start(message: types.Message, state: FSMContext):
    await state.finish()
    msg = ("Привет! Рад тебя видеть. Я помогу следить за твоими фильтрами, чтобы вода дома всегда была чистой.")
    await message.answer(msg, reply_markup=get_main_menu())
    
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("💧 Обратный осмос", callback_data="cat_osmos"),
           types.InlineKeyboardButton("🧪 3-ступенчатый", callback_data="cat_stage3"),
           types.InlineKeyboardButton("🚰 Магистральный", callback_data="cat_flow"))
    await message.answer("Выберите тип вашей системы для начала:", reply_markup=kb)

@dp.message_handler(lambda m: m.text == "➕ Добавить фильтр", state='*')
async def add_filter_start(message: types.Message, state: FSMContext):
    await state.finish()
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("💧 Обратный осмос", callback_data="cat_osmos"),
           types.InlineKeyboardButton("🧪 3-ступенчатый", callback_data="cat_stage3"),
           types.InlineKeyboardButton("🚰 Магистральный", callback_data="cat_flow"),
           get_back_btn("main_menu"))
    await message.answer("Какой тип фильтра добавляем?", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith("cat_"), state='*')
async def process_cat(callback_query: types.CallbackQuery, state: FSMContext):
    await state.finish()
    cat = callback_query.data.split('_')[1]
    kb = types.InlineKeyboardMarkup(row_width=2)
    for b in CATALOGS[cat].keys(): kb.insert(types.InlineKeyboardButton(b, callback_data=f"br_{cat}_{b}"))
    kb.row(types.InlineKeyboardButton("📝 Свой вариант", callback_data=f"man_{cat}"))
    kb.row(get_back_btn("main_menu"))
    await bot.edit_message_text("Выберите производителя:", callback_query.message.chat.id, callback_query.message.message_id, reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith("br_"), state='*')
async def process_brand(callback_query: types.CallbackQuery, state: FSMContext):
    _, cat, brand = callback_query.data.split('_')
    kb = types.InlineKeyboardMarkup(row_width=1)
    for idx, m in enumerate(CATALOGS[cat][brand]): 
        kb.add(types.InlineKeyboardButton(m, callback_data=f"mod_{cat}_{brand}_{idx}"))
    kb.add(get_back_btn(f"cat_{cat}"))
    await bot.edit_message_text(f"Модели {brand}:", callback_query.message.chat.id, callback_query.message.message_id, reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith("mod_"), state='*')
async def process_model(callback_query: types.CallbackQuery, state: FSMContext):
    _, cat, brand, idx = callback_query.data.split('_')
    model_name = CATALOGS[cat][brand][int(idx)]
    uid = str(callback_query.from_user.id)
    now_date = datetime.now().strftime("%d.%m.%Y")
    
    intervals = {code: data['interval'] for code, data in FILTER_CONFIGS[cat].items()}
    if cat == "osmos" and not any(x in model_name.lower() for x in ["m", "мин", "морион"]): intervals["min"] = 0
    
    if uid not in users_db: users_db[uid] = {"filters": [], "snooze_until": None}
    if "filters" not in users_db[uid]: users_db[uid]["filters"] = []
    
    users_db[uid]["filters"].append({
        "model": f"{brand} {model_name}", 
        "category": cat, 
        "intervals": intervals, 
        "history": [{"date": now_date, "item": "Начало обслуживания"}], 
        "created_at": now_date
    })
    await sync_gist("save")
    await bot.send_message(uid, f"✅ <b>{brand} {model_name}</b> добавлена! История начата с сегодня.", reply_markup=get_main_menu())

@dp.message_handler(lambda m: m.text == "📊 Статус", state='*')
async def cmd_status(message: types.Message, state: FSMContext):
    await state.finish(); uid = str(message.from_user.id)
    if uid not in users_db or not users_db[uid].get("filters"): return await message.answer("Сначала добавьте фильтр.")
    
    res = "📊 <b>ТЕКУЩИЙ СТАТУС:</b>\n\n"; now = datetime.now(); kb = types.InlineKeyboardMarkup(row_width=1)
    for i, f in enumerate(users_db[uid]["filters"]):
        res += f"🚰 <b>{f['model']}</b>\n"
        needs_buy = False
        for code, interval in f['intervals'].items():
            if interval == 0: continue
            name = FILTER_CONFIGS[f['category']][code]['name']
            last_date_str = next((h['date'] for h in reversed(f['history']) if h['item'] != "Начало обслуживания"), f['created_at'])
            last_date = datetime.strptime(last_date_str, "%d.%m.%Y")
            total_days = interval * 30.4; elapsed = (now - last_date).days
            days_left = int(total_days - elapsed); pct = max(0, min(100, int((days_left / total_days) * 100)))
            icon = "🟢"
            if days_left <= 0: icon = "🔴"; needs_buy = True
            elif days_left <= 30 and pct <= 25: icon = "🟡"; needs_buy = True
            res += f"  ├ {icon} {name}\n  └ {'█'*(pct//10)+'░'*(10-pct//10)} {pct}% ({days_left} дн.)\n"
        if needs_buy: kb.add(types.InlineKeyboardButton(f"🛒 Купить для {f['model'][:15]}...", callback_data=f"buy_{i}"))
        res += "\n"
    await message.answer(res, reply_markup=kb if kb.inline_keyboard else None)

@dp.message_handler(lambda m: m.text == "📜 История", state='*')
async def cmd_history(message: types.Message, state: FSMContext):
    await state.finish(); uid = str(message.from_user.id)
    if uid not in users_db or not users_db[uid].get("filters"): return await message.answer("История пуста.")
    res = "📜 <b>ИСТОРИЯ ОБСЛУЖИВАНИЯ:</b>\n\n"
    for f in users_db[uid]["filters"]:
        res += f"🚰 <b>{f['model']}</b>:\n"
        for h in reversed(f['history'][-7:]): res += f"  ▫️ {h['date']} — {h['item']}\n"
        res += "\n"
    await message.answer(res)

@dp.message_handler(lambda m: m.text == "⚙️ Настройки", state='*')
async def cmd_settings(message: types.Message, state: FSMContext):
    await state.finish()
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("ℹ️ FAQ / Справка", callback_data="set_faq"),
           types.InlineKeyboardButton("⏱ Сроки замены", callback_data="set_ints"),
           types.InlineKeyboardButton("🗑 Удалить фильтр", callback_data="set_del"),
           get_back_btn("main_menu"))
    await message.answer("⚙️ <b>Настройки:</b>", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data == "set_faq", state='*')
async def set_faq(callback_query: types.CallbackQuery):
    await bot.delete_message(callback_query.message.chat.id, callback_query.message.message_id)
    faq_text = (
        "ℹ️ <b>СПРАВКА</b>\n\n"
        "📅 <b>История:</b> Чтобы расчет был точным, введите дату прошлой замены через кнопку «Заменил картридж».\n\n"
        "⏱ <b>Сроки:</b> Вы можете сами менять интервалы замены в настройках, если ваша вода грязнее обычного.\n\n"
        "🔔 <b>Напоминания:</b> Бот напишет вам, когда ресурс подойдет к концу. Вы сможете отложить уведомление на завтра или на неделю."
    )
    kb = types.InlineKeyboardMarkup(); kb.add(types.InlineKeyboardButton("⬅️ Назад", callback_data="set_back"))
    await bot.send_message(callback_query.from_user.id, faq_text, reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data == "set_back", state='*')
async def back_to_settings(callback_query: types.CallbackQuery):
    await bot.delete_message(callback_query.message.chat.id, callback_query.message.message_id)
    await cmd_settings(callback_query.message, None)

@dp.callback_query_handler(lambda c: c.data == "main_menu", state='*')
async def back_to_main_callback(callback_query: types.CallbackQuery, state: FSMContext):
    await state.finish()
    await bot.delete_message(callback_query.message.chat.id, callback_query.message.message_id)
    await bot.send_message(callback_query.from_user.id, "Главное меню:", reply_markup=get_main_menu())

# --- ЗАПУСК ---
async def start_webserver():
    app = web.Application()
    app.router.add_get("/", lambda r: web.Response(text="Running"))
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', int(os.environ.get("PORT", 10000))).start()

async def on_startup(dp):
    await sync_gist("load")
    await bot.delete_webhook(drop_pending_updates=True)
    asyncio.create_task(start_webserver())
    asyncio.create_task(reminder_scheduler()) # Запуск планировщика

if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True, on_startup=on_startup)
