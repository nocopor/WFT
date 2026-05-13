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
from aiohttp import webimport logging
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
ADMIN_ID = 191012763 # <--- ВСТАВИТЬ СВОЙ ID СЮДА

logging.basicConfig(level=logging.INFO)
users_db = {}

# --- СИНХРОНИЗАЦИЯ С GIST ---
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
                        raw_data = json.loads(content)
                        # Миграция данных
                        for uid, val in raw_data.items():
                            if isinstance(val, list):
                                users_db[uid] = {"filters": val, "snooze_until": None, "username": "—", "last_seen": "—"}
                            else:
                                users_db[uid] = val
            elif action == "save":
                payload = {"files": {"filters_db.json": {"content": json.dumps(users_db, ensure_ascii=False, indent=2)}}}
                await session.patch(url, headers=headers, json=payload)
        except Exception as e: logging.error(f"Sync Error: {e}")

# --- КАТАЛОГ СИСТЕМ ---
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
    waiting_for_broadcast = State()

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

async def track_activity(message: types.Message):
    uid = str(message.from_user.id)
    if uid not in users_db: users_db[uid] = {"filters": [], "snooze_until": None}
    users_db[uid]["username"] = f"@{message.from_user.username}" if message.from_user.username else "—"
    users_db[uid]["full_name"] = message.from_user.full_name
    users_db[uid]["last_seen"] = datetime.now().strftime("%d.%m.%Y %H:%M")
    await sync_gist("save")

# --- АДМИНИСТРИРОВАНИЕ ---

@dp.message_handler(commands=['admin'], state='*')
async def cmd_admin(message: types.Message, state: FSMContext):
    if str(message.from_user.id) != str(ADMIN_ID): return
    await state.finish()
    
    total_users = len(users_db)
    active_users = sum(1 for d in users_db.values() if d.get("filters"))
    
    # Популярные модели
    models = []
    red_alerts = 0
    now = datetime.now()
    
    for uid, data in users_db.items():
        for f in data.get("filters", []):
            models.append(f['model'])
            for code, interval in f['intervals'].items():
                if interval == 0: continue
                last_date_str = next((h['date'] for h in reversed(f['history']) if h['item'] != "Начало обслуживания"), f['created_at'])
                last_date = datetime.strptime(last_date_str, "%d.%m.%Y")
                if (interval * 30.4 - (now - last_date).days) <= 0: red_alerts += 1

    pop_models = "—"
    if models:
        from collections import Counter
        top = Counter(models).most_common(3)
        pop_models = "\n".join([f"• {m}: {c}" for m, c in top])

    report = (
        f"👑 <b>АДМИН-ПАНЕЛЬ</b>\n" + "━" * 15 + "\n"
        f"👥 Всего в базе: <b>{total_users}</b>\n"
        f"✅ С фильтрами: <b>{active_users}</b>\n"
        f"🔴 Просроченных ступеней: <b>{red_alerts}</b>\n\n"
        f"🔥 <b>ТОП-3 МОДЕЛИ:</b>\n{pop_models}\n\n"
        f"👇 Выберите действие:"
    )
    
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("📋 Список ников", callback_data="adm_list"),
           types.InlineKeyboardButton("📢 Сделать рассылку", callback_data="adm_bc"),
           types.InlineKeyboardButton("❌ Закрыть", callback_data="main_menu"))
    await message.answer(report, reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data == "adm_list", state='*')
async def adm_list_users(callback_query: types.CallbackQuery):
    if str(callback_query.from_user.id) != str(ADMIN_ID): return
    res = "👤 <b>АКТИВНОСТЬ:</b>\n\n"
    for uid, d in users_db.items():
        res += f"<b>{d.get('username')}</b> ({d.get('full_name')})\n└ 📅 {d.get('last_seen')} | 🚰 {len(d.get('filters', []))} шт.\n\n"
    
    if len(res) > 4096:
        for x in range(0, len(res), 4096): await bot.send_message(ADMIN_ID, res[x:x+4096])
    else: await bot.edit_message_text(res, callback_query.message.chat.id, callback_query.message.message_id, reply_markup=types.InlineKeyboardMarkup().add(get_back_btn("adm_back")))

@dp.callback_query_handler(lambda c: c.data == "adm_bc", state='*')
async def adm_broadcast_start(callback_query: types.CallbackQuery, state: FSMContext):
    if str(callback_query.from_user.id) != str(ADMIN_ID): return
    await FilterStates.waiting_for_broadcast.set()
    await bot.send_message(ADMIN_ID, "📝 <b>Введите текст для рассылки всем пользователям:</b>\n\nИспользуйте HTML для оформления.", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add("❌ Отмена"))

@dp.message_handler(state=FilterStates.waiting_for_broadcast)
async def adm_broadcast_send(message: types.Message, state: FSMContext):
    if str(message.from_user.id) != str(ADMIN_ID): return
    if message.text == "❌ Отмена":
        await state.finish()
        return await message.answer("Отменено.", reply_markup=get_main_menu())
    
    count = 0
    await message.answer("🚀 Начинаю рассылку...")
    for uid in users_db.keys():
        try:
            await bot.send_message(uid, f"📢 <b>СООБЩЕНИЕ ОТ СЕРВИСА:</b>\n\n{message.text}")
            count += 1
            await asyncio.sleep(0.05) # Защита от спам-фильтра ТГ
        except: pass
    
    await state.finish()
    await message.answer(f"✅ Рассылка завершена! Получили: {count} чел.", reply_markup=get_main_menu())

@dp.callback_query_handler(lambda c: c.data == "adm_back", state='*')
async def adm_back_cb(callback_query: types.CallbackQuery, state: FSMContext):
    await bot.delete_message(callback_query.message.chat.id, callback_query.message.message_id)
    await cmd_admin(callback_query.message, state)

# --- ЛОГИКА НАПОМИНАНИЙ ---

async def reminder_scheduler():
    while True:
        now = datetime.now()
        if 10 <= now.hour <= 21:
            for uid, user_data in users_db.items():
                if not isinstance(user_data, dict): continue
                snooze_until_str = user_data.get("snooze_until")
                if snooze_until_str:
                    try:
                        if now < datetime.strptime(snooze_until_str, "%Y-%m-%d %H:%M:%S"): continue
                    except: pass
                alert_msg = ""
                for f in user_data.get("filters", []):
                    for code, interval in f['intervals'].items():
                        if interval == 0: continue
                        last_date_str = next((h['date'] for h in reversed(f['history']) if h['item'] != "Начало обслуживания"), f['created_at'])
                        last_date = datetime.strptime(last_date_str, "%d.%m.%Y")
                        days_left = int(interval * 30.4 - (now - last_date).days)
                        if days_left <= 7: alert_msg += f"⚠️ <b>{f['model']}</b>: {FILTER_CONFIGS[f['category']][code]['name']}\n"
                if alert_msg:
                    kb = types.InlineKeyboardMarkup(row_width=2)
                    kb.add(types.InlineKeyboardButton("🕒 Завтра", callback_data="sn_1"),
                           types.InlineKeyboardButton("🗓 Через неделю", callback_data="sn_7"))
                    try:
                        await bot.send_message(uid, f"🔔 <b>ПОРА ОБСЛУЖИТЬ ФИЛЬТР!</b>\n\n{alert_msg}", reply_markup=kb)
                        users_db[uid]["snooze_until"] = (now + timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
                        await sync_gist("save")
                    except: pass
        await asyncio.sleep(3600)

@dp.callback_query_handler(lambda c: c.data.startswith("sn_"), state='*')
async def process_snooze(callback_query: types.CallbackQuery):
    days = int(callback_query.data.split('_')[1])
    uid = str(callback_query.from_user.id)
    until_dt = datetime.now() + timedelta(days=days)
    if uid in users_db:
        users_db[uid]["snooze_until"] = until_dt.strftime("%Y-%m-%d %H:%M:%S")
        await sync_gist("save")
    await bot.answer_callback_query(callback_query.id, "Отложено")
    await bot.edit_message_text(f"✅ Хорошо, напомню через {days} дн.", callback_query.message.chat.id, callback_query.message.message_id)

# --- ОСНОВНЫЕ ОБРАБОТЧИКИ ---

@dp.message_handler(commands=['start'], state='*')
async def cmd_start(message: types.Message, state: FSMContext):
    await state.finish()
    await track_activity(message)
    await message.answer("Привет! Рад тебя видеть. Я помогу следить за твоими фильтрами.", reply_markup=get_main_menu())
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("💧 Обратный осмос", callback_data="cat_osmos"),
           types.InlineKeyboardButton("🧪 3-ступенчатый", callback_data="cat_stage3"),
           types.InlineKeyboardButton("🚰 Магистральный", callback_data="cat_flow"))
    await message.answer("Выберите тип вашей системы для начала:", reply_markup=kb)

@dp.message_handler(lambda m: m.text == "➕ Добавить фильтр", state='*')
async def add_filter_start(message: types.Message, state: FSMContext):
    await state.finish()
    await track_activity(message)
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("💧 Обратный осмос", callback_data="cat_osmos"),
           types.InlineKeyboardButton("🧪 3-ступенчатый", callback_data="cat_stage3"),
           types.InlineKeyboardButton("🚰 Магистральный", callback_data="cat_flow"),
           get_back_btn("main_menu"))
    await message.answer("Какой тип фильтра добавляем?", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data == "main_menu", state='*')
async def back_to_main(callback_query: types.CallbackQuery, state: FSMContext):
    await state.finish()
    await bot.delete_message(callback_query.message.chat.id, callback_query.message.message_id)
    await bot.send_message(callback_query.from_user.id, "Главное меню:", reply_markup=get_main_menu())

@dp.callback_query_handler(lambda c: c.data.startswith("cat_"), state='*')
async def process_cat(callback_query: types.CallbackQuery, state: FSMContext):
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
    for idx, m in enumerate(CATALOGS[cat][brand]): kb.add(types.InlineKeyboardButton(m, callback_data=f"mod_{cat}_{brand}_{idx}"))
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
    users_db[uid]["filters"].append({
        "model": f"{brand} {model_name}", "category": cat, "intervals": intervals,
        "history": [{"date": now_date, "item": "Начало обслуживания"}], "created_at": now_date
    })
    await sync_gist("save")
    await bot.send_message(uid, f"✅ <b>{brand} {model_name}</b> добавлена!", reply_markup=get_main_menu())

@dp.message_handler(lambda m: m.text == "📊 Статус", state='*')
async def cmd_status(message: types.Message, state: FSMContext):
    await state.finish(); uid = str(message.from_user.id)
    await track_activity(message)
    user_data = users_db.get(uid, {})
    filters = user_data.get("filters", [])
    if not filters: return await message.answer("Сначала добавьте фильтр.")
    res = "📊 <b>ТЕКУЩИЙ СТАТУС:</b>\n\n"; now = datetime.now(); kb = types.InlineKeyboardMarkup(row_width=1)
    for i, f in enumerate(filters):
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
    await track_activity(message)
    user_data = users_db.get(uid, {})
    filters = user_data.get("filters", [])
    if not filters: return await message.answer("История пуста.")
    res = "📜 <b>ИСТОРИЯ ОБСЛУЖИВАНИЯ:</b>\n\n"
    for f in filters:
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
async def set_faq_handler(callback_query: types.CallbackQuery):
    await bot.delete_message(callback_query.message.chat.id, callback_query.message.message_id)
    faq_text = (
        "ℹ️ <b>СПРАВКА И ПОМОЩЬ</b>\n" + "━" * 15 + "\n\n"
        "⚙️ <b>Как самому изменить сроки?</b>\n"
        "Перейдите в <b>«Настройки» -> «⏱ Сроки замены»</b> и введите свои значения в месяцах.\n\n"
        "🔔 <b>Напоминания:</b> Бот напишет вам, когда ресурс подойдет к концу. Вы сможете отложить уведомление на завтра или на неделю.\n\n"
        "📊 <b>Статусы:</b> 🟢 — Ок, 🟡 — Скоро замена, 🔴 — Пора менять!"
    )
    kb = types.InlineKeyboardMarkup(); kb.add(types.InlineKeyboardButton("⬅️ Назад", callback_data="set_back_to_settings"))
    await bot.send_message(callback_query.from_user.id, faq_text, reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data == "set_back_to_settings", state='*')
async def set_back_to_settings_cb(callback_query: types.CallbackQuery):
    await bot.delete_message(callback_query.message.chat.id, callback_query.message.message_id)
    await cmd_settings(callback_query.message, None)

@dp.callback_query_handler(lambda c: c.data.startswith("buy_"), state='*')
async def buy_links(callback_query: types.CallbackQuery):
    idx = int(callback_query.data.split('_')[1]); uid = str(callback_query.from_user.id)
    f = users_db[uid]["filters"][idx]
    q = quote(f"картриджи {f['model']}"); kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("🛒 На Ozon", url=f"https://www.ozon.ru/search/?text={q}"),
           types.InlineKeyboardButton("🟣 Wildberries", url=f"https://www.wildberries.ru/catalog/0/search.aspx?search={q}"),
           get_back_btn("main_menu"))
    await bot.send_message(callback_query.from_user.id, f"🛒 Расходники для <b>{f['model']}</b>:", reply_markup=kb)

# --- ЗАПУСК ---
async def start_webserver():
    app = web.Application()
    app.router.add_get("/", lambda r: web.Response(text="Bot Running"))
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', int(os.environ.get("PORT", 10000))).start()

async def on_startup(dp):
    await sync_gist("load")
    await bot.delete_webhook(drop_pending_updates=True)
    asyncio.create_task(start_webserver())
    asyncio.create_task(reminder_scheduler())

if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True, on_startup=on_startup)


# --- КОНФИГУРАЦИЯ ---
TOKEN = os.getenv("BOT_TOKEN")
GIST_ID = os.getenv("GIST_ID")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

logging.basicConfig(level=logging.INFO)
users_db = {}

# --- СИНХРОНИЗАЦИЯ И МИГРАЦИЯ ---
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
                        raw_data = json.loads(content)
                        # МИГРАЦИЯ: если данные старого формата (список), конвертируем в новый (словарь)
                        for uid, val in raw_data.items():
                            if isinstance(val, list):
                                users_db[uid] = {"filters": val, "snooze_until": None}
                            else:
                                users_db[uid] = val
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

# --- КЛАВИАТУРЫ ---
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
    while True:
        now = datetime.now()
        if 10 <= now.hour <= 21:
            for uid, user_data in users_db.items():
                if not isinstance(user_data, dict): continue
                snooze_until_str = user_data.get("snooze_until")
                if snooze_until_str:
                    try:
                        if now < datetime.strptime(snooze_until_str, "%Y-%m-%d %H:%M:%S"): continue
                    except: pass

                alert_msg = ""
                filters = user_data.get("filters", [])
                for f in filters:
                    for code, interval in f['intervals'].items():
                        if interval == 0: continue
                        last_date_str = next((h['date'] for h in reversed(f['history']) if h['item'] != "Начало обслуживания"), f['created_at'])
                        last_date = datetime.strptime(last_date_str, "%d.%m.%Y")
                        days_left = int(interval * 30.4 - (now - last_date).days)
                        if days_left <= 7:
                            alert_msg += f"⚠️ <b>{f['model']}</b>: {FILTER_CONFIGS[f['category']][code]['name']}\n"
                
                if alert_msg:
                    kb = types.InlineKeyboardMarkup(row_width=2)
                    kb.add(types.InlineKeyboardButton("🕒 Завтра", callback_data="sn_1"),
                           types.InlineKeyboardButton("🗓 Через неделю", callback_data="sn_7"))
                    try:
                        await bot.send_message(uid, f"🔔 <b>ПОРА ОБСЛУЖИТЬ ФИЛЬТР!</b>\n\n{alert_msg}", reply_markup=kb)
                        users_db[uid]["snooze_until"] = (now + timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
                        await sync_gist("save")
                    except: pass
        await asyncio.sleep(3600)

@dp.callback_query_handler(lambda c: c.data.startswith("sn_"), state='*')
async def process_snooze(callback_query: types.CallbackQuery):
    days = int(callback_query.data.split('_')[1])
    uid = str(callback_query.from_user.id)
    until_dt = datetime.now() + timedelta(days=days)
    if uid in users_db:
        users_db[uid]["snooze_until"] = until_dt.strftime("%Y-%m-%d %H:%M:%S")
        await sync_gist("save")
    await bot.answer_callback_query(callback_query.id, "Отложено")
    await bot.edit_message_text(f"✅ Хорошо, напомню через {days} дн.", callback_query.message.chat.id, callback_query.message.message_id)

# --- ОСНОВНЫЕ ОБРАБОТЧИКИ ---

@dp.message_handler(commands=['start'], state='*')
async def cmd_start(message: types.Message, state: FSMContext):
    await state.finish()
    await message.answer("Привет! Рад тебя видеть. Я помогу следить за твоими фильтрами.", reply_markup=get_main_menu())
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

@dp.callback_query_handler(lambda c: c.data == "main_menu", state='*')
async def back_to_main(callback_query: types.CallbackQuery, state: FSMContext):
    await state.finish()
    await bot.delete_message(callback_query.message.chat.id, callback_query.message.message_id)
    await bot.send_message(callback_query.from_user.id, "Главное меню:", reply_markup=get_main_menu())

@dp.callback_query_handler(lambda c: c.data.startswith("cat_"), state='*')
async def process_cat(callback_query: types.CallbackQuery, state: FSMContext):
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
    users_db[uid]["filters"].append({
        "model": f"{brand} {model_name}", "category": cat, "intervals": intervals,
        "history": [{"date": now_date, "item": "Начало обслуживания"}], "created_at": now_date
    })
    await sync_gist("save")
    await bot.send_message(uid, f"✅ <b>{brand} {model_name}</b> добавлена!", reply_markup=get_main_menu())

# --- СТАТУС, КУПИТЬ, ЗАМЕНИТЬ (С ПОДДЕРЖКОЙ СТРУКТУРЫ) ---

@dp.message_handler(lambda m: m.text == "📊 Статус", state='*')
async def cmd_status(message: types.Message, state: FSMContext):
    await state.finish(); uid = str(message.from_user.id)
    user_data = users_db.get(uid, {})
    filters = user_data.get("filters", []) if isinstance(user_data, dict) else []
    if not filters: return await message.answer("Сначала добавьте фильтр.")
    
    res = "📊 <b>ТЕКУЩИЙ СТАТУС:</b>\n\n"; now = datetime.now(); kb = types.InlineKeyboardMarkup(row_width=1)
    for i, f in enumerate(filters):
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

@dp.message_handler(lambda m: m.text == "🛒 Купить картриджи", state='*')
async def cmd_buy(message: types.Message, state: FSMContext):
    await state.finish(); uid = str(message.from_user.id)
    user_data = users_db.get(uid, {})
    filters = user_data.get("filters", []) if isinstance(user_data, dict) else []
    if not filters: return await message.answer("Добавьте фильтр.")
    kb = types.InlineKeyboardMarkup(row_width=1)
    for i, f in enumerate(filters): kb.add(types.InlineKeyboardButton(f['model'], callback_data=f"buy_{i}"))
    kb.add(get_back_btn("main_menu"))
    await message.answer("Для какой системы ищем расходники?", reply_markup=kb)

@dp.message_handler(lambda m: m.text == "📜 История", state='*')
async def cmd_history(message: types.Message, state: FSMContext):
    await state.finish(); uid = str(message.from_user.id)
    user_data = users_db.get(uid, {})
    filters = user_data.get("filters", []) if isinstance(user_data, dict) else []
    if not filters: return await message.answer("История пуста.")
    res = "📜 <b>ИСТОРИЯ ОБСЛУЖИВАНИЯ:</b>\n\n"
    for f in filters:
        res += f"🚰 <b>{f['model']}</b>:\n"
        for h in reversed(f['history'][-7:]): res += f"  ▫️ {h['date']} — {h['item']}\n"
        res += "\n"
    await message.answer(res)

@dp.message_handler(lambda m: m.text == "📅 Заменил картридж", state='*')
async def cmd_replace(message: types.Message, state: FSMContext):
    await state.finish(); uid = str(message.from_user.id)
    user_data = users_db.get(uid, {})
    filters = user_data.get("filters", []) if isinstance(user_data, dict) else []
    if not filters: return await message.answer("Добавьте фильтр.")
    kb = types.InlineKeyboardMarkup(row_width=1)
    for i, f in enumerate(filters): kb.add(types.InlineKeyboardButton(f['model'], callback_data=f"sr_{i}"))
    kb.add(get_back_btn("main_menu"))
    await message.answer("В какой системе произведена замена?", reply_markup=kb)

# --- ЗАМЕНА КАРТРИДЖЕЙ (ВНУТРЕННЯЯ ЛОГИКА) ---

@dp.callback_query_handler(lambda c: c.data.startswith("sr_"), state='*')
async def sr_choice(callback_query: types.CallbackQuery):
    idx = int(callback_query.data.split('_')[1])
    uid = str(callback_query.from_user.id)
    f = users_db[uid]["filters"][idx]
    kb = types.InlineKeyboardMarkup(row_width=1)
    for code, val in f['intervals'].items():
        if val > 0: kb.add(types.InlineKeyboardButton(FILTER_CONFIGS[f['category']][code]['name'], callback_data=f"opt_{code}_{idx}"))
    kb.add(get_back_btn("main_menu"))
    await bot.edit_message_text("Что именно заменили?", callback_query.message.chat.id, callback_query.message.message_id, reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith("opt_"), state='*')
async def opt_choice(callback_query: types.CallbackQuery):
    _, code, idx = callback_query.data.split('_')
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("📅 Сегодня", callback_data=f"dn_{code}_{idx}"),
           types.InlineKeyboardButton("✍️ Вручную", callback_data=f"dm_{code}_{idx}"),
           get_back_btn(f"sr_{idx}"))
    await bot.edit_message_text("Когда была замена?", callback_query.message.chat.id, callback_query.message.message_id, reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith("dn_"), state='*')
async def date_today(callback_query: types.CallbackQuery):
    _, code, idx = callback_query.data.split('_'); uid = str(callback_query.from_user.id)
    name = FILTER_CONFIGS[users_db[uid]["filters"][int(idx)]['category']][code]['name']
    users_db[uid]["filters"][int(idx)]['history'].append({"date": datetime.now().strftime("%d.%m.%Y"), "item": name})
    await sync_gist("save")
    await bot.edit_message_text(f"✅ Замена {name} сохранена!", callback_query.message.chat.id, callback_query.message.message_id)

@dp.callback_query_handler(lambda c: c.data.startswith("dm_"), state='*')
async def date_man_start(callback_query: types.CallbackQuery, state: FSMContext):
    _, code, idx = callback_query.data.split('_')
    await state.update_data(dm_code=code, dm_idx=int(idx))
    await FilterStates.waiting_for_date.set()
    await bot.send_message(callback_query.from_user.id, "✍️ <b>Введите дату замены (ДД.ММ.ГГГГ):</b>")

@dp.message_handler(state=FilterStates.waiting_for_date)
async def date_man_msg(message: types.Message, state: FSMContext):
    try:
        valid = datetime.strptime(message.text, "%d.%m.%Y").strftime("%d.%m.%Y")
        data = await state.get_data(); uid = str(message.from_user.id)
        name = FILTER_CONFIGS[users_db[uid]["filters"][data['dm_idx']]['category']][data['dm_code']]['name']
        users_db[uid]["filters"][data['dm_idx']]['history'].append({"date": valid, "item": name})
        await sync_gist("save"); await state.finish()
        await message.answer(f"✅ Сохранено на {valid}!", reply_markup=get_main_menu())
    except: await message.answer("⚠️ Формат: ДД.ММ.ГГГГ")

# --- FAQ И НАСТРОЙКИ ---

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
async def set_faq_handler(callback_query: types.CallbackQuery):
    await bot.delete_message(callback_query.message.chat.id, callback_query.message.message_id)
    faq_text = (
        "ℹ️ <b>СПРАВКА И ПОМОЩЬ</b>\n" + "━" * 15 + "\n\n"
        "⚙️ <b>Как самому изменить сроки?</b>\n"
        "Перейдите в <b>«Настройки» -> «⏱ Сроки замены»</b> и введите свои значения в месяцах.\n\n"
        "🔔 <b>Напоминания:</b> Бот напишет вам, когда ресурс подойдет к концу. Вы сможете отложить уведомление на завтра или на неделю.\n\n"
        "📊 <b>Статусы:</b> 🟢 — Ок, 🟡 — Скоро замена, 🔴 — Пора менять!"
    )
    kb = types.InlineKeyboardMarkup(); kb.add(types.InlineKeyboardButton("⬅️ Назад", callback_data="set_back_to_settings"))
    await bot.send_message(callback_query.from_user.id, faq_text, reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data == "set_back_to_settings", state='*')
async def set_back_to_settings_cb(callback_query: types.CallbackQuery):
    await bot.delete_message(callback_query.message.chat.id, callback_query.message.message_id)
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("ℹ️ FAQ / Справка", callback_data="set_faq"),
           types.InlineKeyboardButton("⏱ Сроки замены", callback_data="set_ints"),
           types.InlineKeyboardButton("🗑 Удалить фильтр", callback_data="set_del"),
           get_back_btn("main_menu"))
    await bot.send_message(callback_query.from_user.id, "⚙️ <b>Настройки:</b>", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith("buy_"), state='*')
async def buy_links(callback_query: types.CallbackQuery):
    idx = int(callback_query.data.split('_')[1]); uid = str(callback_query.from_user.id)
    f = users_db[uid]["filters"][idx]
    q = quote(f"картриджи {f['model']}"); kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("🛒 На Ozon", url=f"https://www.ozon.ru/search/?text={q}"),
           types.InlineKeyboardButton("🟣 Wildberries", url=f"https://www.wildberries.ru/catalog/0/search.aspx?search={q}"),
           get_back_btn("main_menu"))
    await bot.send_message(callback_query.from_user.id, f"🛒 Расходники для <b>{f['model']}</b>:", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data == "set_del", state='*')
async def set_del_list(callback_query: types.CallbackQuery):
    uid = str(callback_query.from_user.id); kb = types.InlineKeyboardMarkup(row_width=1)
    filters = users_db.get(uid, {}).get("filters", [])
    for i, f in enumerate(filters): kb.add(types.InlineKeyboardButton(f"🗑 {f['model']}", callback_data=f"del_{i}"))
    kb.add(get_back_btn("main_menu"))
    await bot.edit_message_text("Какую систему удалить?", callback_query.message.chat.id, callback_query.message.message_id, reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith("del_"), state='*')
async def del_confirm(callback_query: types.CallbackQuery):
    idx, uid = int(callback_query.data.split('_')[1]), str(callback_query.from_user.id)
    name = users_db[uid]["filters"].pop(idx)['model']; await sync_gist("save")
    await bot.edit_message_text(f"✅ Система {name} удалена.", callback_query.message.chat.id, callback_query.message.message_id)

# --- ЗАПУСК ---
async def start_webserver():
    app = web.Application()
    app.router.add_get("/", lambda r: web.Response(text="Bot Running"))
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', int(os.environ.get("PORT", 10000))).start()

async def on_startup(dp):
    await sync_gist("load")
    await bot.delete_webhook(drop_pending_updates=True)
    asyncio.create_task(start_webserver())
    asyncio.create_task(reminder_scheduler())

if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True, on_startup=on_startup)
