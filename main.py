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
ADMIN_ID = 191012763 # <--- ОБЯЗАТЕЛЬНО ВСТАВЬ СВОЙ ID

logging.basicConfig(level=logging.INFO)
users_db = {}

# --- БАЗА АВТО-НАЗВАНИЙ (ДЛЯ ПОПУЛЯРНЫХ МОДЕЛЕЙ) ---
AUTO_NAMES = {
    "Атолл A-550": {"pre": "Набор №202", "mem": "Мембрана 50 GPD", "post": "Постфильтр Atoll"},
    "Атолл A-550m": {"pre": "Набор №202", "mem": "Мембрана 50 GPD", "post": "Постфильтр Atoll", "min": "Минерализатор Atoll"},
    "Атолл A-575": {"pre": "Набор №203", "mem": "Мембрана 75 GPD", "post": "Постфильтр Atoll"},
    "Атолл A-575m": {"pre": "Набор №203", "mem": "Мембрана 75 GPD", "post": "Постфильтр Atoll", "min": "Минерализатор Atoll"},
    "Аквафор DWM-101S Морион": {"pre": "Модули К5 и К2", "mem": "Мембрана КО-50S", "min": "Модуль К7М"},
    "Аквафор DWM-102S": {"pre": "Модули К5 и К2", "mem": "Мембрана КО-100S", "min": "Модуль К7М"},
    "Гейзер Престиж": {"pre": "Набор №6", "mem": "Мембрана Гейзер 50 GPD", "post": "Постфильтр Т33"},
    "Гейзер Аллегро": {"pre": "Набор №10", "mem": "Мембрана Гейзер 50 GPD", "post": "Постфильтр Т33"},
    "Гейзер Макс": {"set": "Набор Гейзер Макс"},
    "Аквафор Кристалл Эко": {"set": "Комплект К3-К7B-K7"}
}

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
                        raw_data = json.loads(content)
                        for uid, val in raw_data.items():
                            if isinstance(val, list):
                                users_db[uid] = {"filters": val, "snooze_until": None, "username": "—", "last_seen": "—"}
                            else: users_db[uid] = val
            elif action == "save":
                payload = {"files": {"filters_db.json": {"content": json.dumps(users_db, ensure_ascii=False, indent=2)}}}
                await session.patch(url, headers=headers, json=payload)
        except Exception as e: logging.error(f"Sync Error: {e}")

# --- КАТАЛОГ И КОНФИГИ ---
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
    waiting_for_cartridge_rename = State()

bot = Bot(token=TOKEN, parse_mode=types.ParseMode.HTML)
dp = Dispatcher(bot, storage=MemoryStorage())

# --- УТИЛИТЫ ---
def get_user_filters(uid):
    data = users_db.get(str(uid), {})
    if isinstance(data, list): return data
    return data.get("filters", [])

def get_item_name(f, code):
    # 1. Ручное название
    custom = f.get("custom_names", {}).get(code)
    if custom: return custom
    # 2. Авто-название по модели
    model_auto = AUTO_NAMES.get(f['model'], {}).get(code)
    if model_auto: return model_auto
    # 3. Дефолт
    return FILTER_CONFIGS[f['category']][code]['name']

async def track_activity(message: types.Message):
    uid = str(message.from_user.id)
    if uid not in users_db: users_db[uid] = {"filters": [], "snooze_until": None}
    elif isinstance(users_db[uid], list): users_db[uid] = {"filters": users_db[uid], "snooze_until": None}
    users_db[uid]["username"] = f"@{message.from_user.username}" if message.from_user.username else "—"
    users_db[uid]["full_name"] = message.from_user.full_name
    users_db[uid]["last_seen"] = datetime.now().strftime("%d.%m.%Y %H:%M")
    await sync_gist("save")

# --- ОСНОВНАЯ ЛОГИКА ---

@dp.message_handler(commands=['start'], state='*')
async def cmd_start(message: types.Message, state: FSMContext):
    await state.finish(); await track_activity(message)
    kb = get_main_menu()
    await message.answer("Привет! Я помогу следить за твоими фильтрами.", reply_markup=kb)

@dp.message_handler(lambda m: m.text == "📊 Статус", state='*')
async def cmd_status(message: types.Message, state: FSMContext):
    await state.finish(); await track_activity(message)
    filters = get_user_filters(message.from_user.id)
    if not filters: return await message.answer("Сначала добавьте фильтр.")
    
    res = "📊 <b>ТЕКУЩИЙ СТАТУС:</b>\n\n"; now = datetime.now(); kb = types.InlineKeyboardMarkup(row_width=1)
    for i, f in enumerate(filters):
        res += f"🚰 <b>{f['model']}</b>\n"
        needs_buy = False
        for code, interval in f['intervals'].items():
            if interval == 0: continue
            name = get_item_name(f, code)
            last_date_str = next((h['date'] for h in reversed(f['history']) if h['item'] not in ["Начало обслуживания", "Система добавлена"]), f['created_at'])
            last_date = datetime.strptime(last_date_str, "%d.%m.%Y")
            total_days = interval * 30.4; elapsed = (now - last_date).days
            days_left = int(total_days - elapsed); pct = max(0, min(100, int((days_left / total_days) * 100)))
            icon = "🟢" if days_left > 30 else ("🟡" if days_left > 0 else "🔴")
            if days_left <= 30: needs_buy = True
            res += f"  ├ {icon} {name}\n  └ {'█'*(pct//10)+'░'*(10-pct//10)} {pct}% ({days_left} дн.)\n"
        if needs_buy: kb.add(types.InlineKeyboardButton(f"🛒 Купить для {f['model'][:15]}...", callback_data=f"buy_{i}"))
        res += "\n"
    await message.answer(res, reply_markup=kb if kb.inline_keyboard else None)

@dp.message_handler(lambda m: m.text == "📅 Заменил картридж", state='*')
async def cmd_replace(message: types.Message, state: FSMContext):
    await state.finish(); await track_activity(message)
    filters = get_user_filters(message.from_user.id)
    if not filters: return await message.answer("Добавьте фильтр.")
    kb = types.InlineKeyboardMarkup(row_width=1)
    for i, f in enumerate(filters): kb.add(types.InlineKeyboardButton(f['model'], callback_data=f"sr_{i}"))
    kb.add(get_back_btn("main_menu"))
    await message.answer("В какой системе произведена замена?", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith("sr_"), state='*')
async def sr_choice(callback_query: types.CallbackQuery):
    idx = int(callback_query.data.split('_')[1]); uid = str(callback_query.from_user.id)
    filters = get_user_filters(uid)
    f = filters[idx]
    kb = types.InlineKeyboardMarkup(row_width=1)
    for code, val in f['intervals'].items():
        if val > 0: kb.add(types.InlineKeyboardButton(get_item_name(f, code), callback_data=f"opt_{code}_{idx}"))
    kb.add(get_back_btn("main_menu"))
    await bot.edit_message_text("Что именно заменили?", callback_query.message.chat.id, callback_query.message.message_id, reply_markup=kb)

# --- КУПИТЬ И ИСТОРИЯ ---

@dp.callback_query_handler(lambda c: c.data.startswith("buy_"), state='*')
async def buy_links(callback_query: types.CallbackQuery):
    idx = int(callback_query.data.split('_')[1]); uid = str(callback_query.from_user.id)
    filters = get_user_filters(uid); f = filters[idx]
    
    # Формируем умный запрос
    search_terms = []
    for code, val in f['intervals'].items():
        if val > 0: search_terms.append(get_item_name(f, code))
    
    q = quote(f"картриджи {f['model']} {' '.join(search_terms[:1])}")
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("🛒 На Ozon", url=f"https://www.ozon.ru/search/?text={q}"),
           types.InlineKeyboardButton("🟣 На Wildberries", url=f"https://www.wildberries.ru/catalog/0/search.aspx?search={q}"),
           get_back_btn("main_menu"))
    await bot.send_message(uid, f"🛒 Ищем расходники для <b>{f['model']}</b>:", reply_markup=kb)

@dp.message_handler(lambda m: m.text == "📜 История", state='*')
async def cmd_history(message: types.Message, state: FSMContext):
    await state.finish(); await track_activity(message)
    filters = get_user_filters(message.from_user.id)
    if not filters: return await message.answer("История пуста.")
    res = "📜 <b>ИСТОРИЯ ОБСЛУЖИВАНИЯ:</b>\n\n"
    for f in filters:
        res += f"🚰 <b>{f['model']}</b>:\n"
        for h in reversed(f['history'][-7:]): res += f"  ▫️ {h['date']} — {h['item']}\n"
        res += "\n"
    await message.answer(res)

# --- НАСТРОЙКИ И РУЧНОЙ ВВОД ---

@dp.message_handler(lambda m: m.text == "⚙️ Настройки", state='*')
async def cmd_settings(message: types.Message, state: FSMContext):
    await state.finish()
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("ℹ️ FAQ / Справка", callback_data="set_faq"),
           types.InlineKeyboardButton("✏️ Названия картриджей", callback_data="set_names"),
           types.InlineKeyboardButton("⏱ Сроки замены", callback_data="set_ints"),
           types.InlineKeyboardButton("🗑 Удалить фильтр", callback_data="set_del"),
           get_back_btn("main_menu"))
    await message.answer("⚙️ <b>Настройки:</b>", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data == "set_names", state='*')
async def set_names_list(callback_query: types.CallbackQuery):
    uid = str(callback_query.from_user.id); filters = get_user_filters(uid)
    kb = types.InlineKeyboardMarkup(row_width=1)
    for i, f in enumerate(filters): kb.add(types.InlineKeyboardButton(f['model'], callback_data=f"ren_{i}"))
    kb.add(get_back_btn("main_menu"))
    await bot.edit_message_text("Выберите систему для переименования картриджей:", callback_query.message.chat.id, callback_query.message.message_id, reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith("ren_"), state='*')
async def ren_step_choice(callback_query: types.CallbackQuery):
    idx = int(callback_query.data.split('_')[1]); uid = str(callback_query.from_user.id)
    f = get_user_filters(uid)[idx]
    kb = types.InlineKeyboardMarkup(row_width=1)
    for code, val in f['intervals'].items():
        if val > 0: kb.add(types.InlineKeyboardButton(f"Изменить: {get_item_name(f, code)}", callback_data=f"edn_{idx}_{code}"))
    kb.add(get_back_btn("set_names"))
    await bot.edit_message_text(f"Что переименовать в {f['model']}?", callback_query.message.chat.id, callback_query.message.message_id, reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith("edn_"), state='*')
async def ren_input_start(callback_query: types.CallbackQuery, state: FSMContext):
    _, idx, code = callback_query.data.split('_')
    await state.update_data(edit_idx=int(idx), edit_code=code)
    await FilterStates.waiting_for_cartridge_rename.set()
    await bot.send_message(callback_query.from_user.id, "✍️ <b>Введите новое название для этого картриджа:</b>\n(Например: МП-5В)")

@dp.message_handler(state=FilterStates.waiting_for_cartridge_rename)
async def ren_input_save(message: types.Message, state: FSMContext):
    data = await state.get_data(); uid = str(message.from_user.id)
    idx, code = data['edit_idx'], data['edit_code']
    if "custom_names" not in users_db[uid]["filters"][idx]: users_db[uid]["filters"][idx]["custom_names"] = {}
    users_db[uid]["filters"][idx]["custom_names"][code] = message.text
    await sync_gist("save"); await state.finish()
    await message.answer(f"✅ Название «{message.text}» сохранено!", reply_markup=get_main_menu())

# --- FAQ ---

@dp.callback_query_handler(lambda c: c.data == "set_faq", state='*')
async def set_faq_handler(callback_query: types.CallbackQuery):
    await bot.delete_message(callback_query.message.chat.id, callback_query.message.message_id)
    faq_text = (
        "ℹ️ <b>СПРАВКА И ПОМОЩЬ</b>\n" + "━" * 15 + "\n\n"
        "🎯 <b>Как улучшить поиск картриджей?</b>\n"
        "Если поиск выдает не то, зайдите в <b>Настройки -> Названия картриджей</b> и впишите точную модель (например, МП-5В). Бот будет использовать именно её.\n\n"
        "🤖 <b>Авто-названия:</b> Для популярных систем (Atoll, Аквафор Морион и др.) бот сам подставляет названия наборов (№202, №203 и т.д.).\n\n"
        "🔔 <b>Напоминания:</b> Бот пишет в 10:00, когда ресурс подходит к концу. Можно отложить на завтра или неделю."
    )
    kb = types.InlineKeyboardMarkup(); kb.add(types.InlineKeyboardButton("⬅️ Назад", callback_data="main_menu"))
    await bot.send_message(callback_query.from_user.id, faq_text, reply_markup=kb)

# --- ДОБАВЛЕНИЕ С К подсказкой ---

@dp.callback_query_handler(lambda c: c.data.startswith("cat_"), state='*')
async def process_cat(callback_query: types.CallbackQuery, state: FSMContext):
    cat = callback_query.data.split('_')[1]
    if cat == "flow":
        await bot.send_message(callback_query.from_user.id, "💡 <b>Подсказка:</b> Для магистральных фильтров подходят разные картриджи. Вы сможете указать точную модель (например, МП-5В) в Настройках после добавления.")
    kb = types.InlineKeyboardMarkup(row_width=2)
    for b in CATALOGS[cat].keys(): kb.insert(types.InlineKeyboardButton(b, callback_data=f"br_{cat}_{b}"))
    kb.row(get_back_btn("main_menu"))
    await bot.edit_message_text("Выберите производителя:", callback_query.message.chat.id, callback_query.message.message_id, reply_markup=kb)

# --- АДМИНКА (Упрощенная для v13.2) ---

@dp.message_handler(commands=['admin'], state='*')
async def cmd_admin(message: types.Message, state: FSMContext):
    if str(message.from_user.id) != str(ADMIN_ID): return
    total = len(users_db)
    report = f"👑 <b>АДМИН-ПАНЕЛЬ</b>\n👥 Пользователей: {total}\n\nКоманды: /broadcast (текст)"
    await message.answer(report)

# --- ЗАМЕНА СЕГОДНЯ / ВРУЧНУЮ ---

@dp.callback_query_handler(lambda c: c.data.startswith("opt_"), state='*')
async def opt_choice_step(callback_query: types.CallbackQuery):
    _, code, idx = callback_query.data.split('_')
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("📅 Сегодня", callback_data=f"dn_{code}_{idx}"),
           types.InlineKeyboardButton("✍️ Вручную", callback_data=f"dm_{code}_{idx}"),
           get_back_btn(f"sr_{idx}"))
    await bot.edit_message_text("Когда была замена?", callback_query.message.chat.id, callback_query.message.message_id, reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith("dn_"), state='*')
async def date_today(callback_query: types.CallbackQuery):
    _, code, idx = callback_query.data.split('_'); uid = str(callback_query.from_user.id)
    f = users_db[uid]["filters"][int(idx)]
    name = get_item_name(f, code)
    users_db[uid]["filters"][int(idx)]['history'].append({"date": datetime.now().strftime("%d.%m.%Y"), "item": name})
    await sync_gist("save")
    await bot.edit_message_text(f"✅ Замена {name} сохранена!", callback_query.message.chat.id, callback_query.message.message_id)

# --- ВСПОМОГАТЕЛЬНЫЕ ---

def get_main_menu():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("📊 Статус", "➕ Добавить фильтр")
    kb.row("📅 Заменил картридж", "🛒 Купить картриджи")
    kb.row("📜 История", "⚙️ Настройки")
    return kb

@dp.callback_query_handler(lambda c: c.data == "main_menu", state='*')
async def back_to_main_menu(callback_query: types.CallbackQuery, state: FSMContext):
    await state.finish(); await bot.delete_message(callback_query.message.chat.id, callback_query.message.message_id)
    await bot.send_message(callback_query.from_user.id, "Главное меню:", reply_markup=get_main_menu())

# (Функции reminder_scheduler, start_webserver и process_brand/process_model остаются без изменений из v13.0)
# --- ОСТАЛЬНЫЕ ФУНКЦИИ ВСТАВЛЯЮТСЯ ЗДЕСЬ ДЛЯ ПОЛНОТЫ ---
@dp.callback_query_handler(lambda c: c.data.startswith("br_"), state='*')
async def process_brand(callback_query: types.CallbackQuery):
    _, cat, brand = callback_query.data.split('_'); kb = types.InlineKeyboardMarkup(row_width=1)
    for idx, m in enumerate(CATALOGS[cat][brand]): kb.add(types.InlineKeyboardButton(m, callback_data=f"mod_{cat}_{brand}_{idx}"))
    await bot.edit_message_text(f"Модели {brand}:", callback_query.message.chat.id, callback_query.message.message_id, reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith("mod_"), state='*')
async def process_model(callback_query: types.CallbackQuery):
    _, cat, brand, idx = callback_query.data.split('_'); model_name = CATALOGS[cat][brand][int(idx)]
    uid = str(callback_query.from_user.id); now_date = datetime.now().strftime("%d.%m.%Y")
    intervals = {code: data['interval'] for code, data in FILTER_CONFIGS[cat].items()}
    if cat == "osmos" and not any(x in model_name.lower() for x in ["m", "мин", "морион"]): intervals["min"] = 0
    if uid not in users_db: users_db[uid] = {"filters": [], "snooze_until": None}
    users_db[uid]["filters"].append({
        "model": f"{brand} {model_name}", "category": cat, "intervals": intervals,
        "history": [{"date": now_date, "item": "Система добавлена"}], "created_at": now_date, "custom_names": {}
    })
    await sync_gist("save"); await bot.send_message(uid, f"✅ <b>{brand} {model_name}</b> добавлена!", reply_markup=get_main_menu())

async def reminder_scheduler():
    while True:
        now = datetime.now()
        if 10 <= now.hour <= 11: # Проверка раз в день утром
            for uid, data in users_db.items():
                filters = get_user_filters(uid)
                for f in filters:
                    for code, interval in f['intervals'].items():
                        if interval == 0: continue
                        last_date_str = next((h['date'] for h in reversed(f['history']) if h['item'] not in ["Начало обслуживания", "Система добавлена"]), f['created_at'])
                        last_date = datetime.strptime(last_date_str, "%d.%m.%Y")
                        if (interval * 30.4 - (now - last_date).days) <= 7:
                            try: await bot.send_message(uid, f"🔔 Пора менять <b>{get_item_name(f, code)}</b> в {f['model']}!")
                            except: pass
        await asyncio.sleep(3600)

async def start_webserver():
    app = web.Application(); app.router.add_get("/", lambda r: web.Response(text="Bot Running"))
    runner = web.AppRunner(app); await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', int(os.environ.get("PORT", 10000))).start()

async def on_startup(dp):
    await sync_gist("load"); await bot.delete_webhook(drop_pending_updates=True)
    asyncio.create_task(start_webserver()); asyncio.create_task(reminder_scheduler())

if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True, on_startup=on_startup)
