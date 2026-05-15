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
ADMIN_ID = 191012763 # <--- ОБЯЗАТЕЛЬНО ВСТАВЬ СВОЙ ID СЮДА

logging.basicConfig(level=logging.INFO)
users_db = {}

# --- БАЗА АВТО-НАЗВАНИЙ ПО МОДЕЛЯМ ---
AUTO_NAMES = {
    "Атолл A-550": {"pre": "Набор №202", "mem": "Мембрана 50 GPD", "post": "Постфильтр Atoll"},
    "Атолл A-550m": {"pre": "Набор №202", "mem": "Мембрана 50 GPD", "min": "Минерализатор Atoll"}, # Без постфильтра
    "Атолл A-575": {"pre": "Набор №203", "mem": "Мембрана 75 GPD", "post": "Постфильтр Atoll"},
    "Атолл A-575m": {"pre": "Набор №203", "mem": "Мембрана 75 GPD", "min": "Минерализатор Atoll"}, # Без постфильтра
    "Аквафор DWM-101S Морион": {"pre": "Модули К5 и К2", "mem": "Мембрана КО-50S", "min": "Модуль К7М"},
    "Аквафор DWM-102S": {"pre": "Модули К5 и К2", "mem": "Мембрана КО-100S", "min": "Модуль К7М"},
    "Гейзер Престиж": {"pre": "Набор №6", "mem": "Мембрана Гейзер 50 GPD", "post": "Постфильтр Т33"},
    "Гейзер Аллегро": {"pre": "Набор №10", "mem": "Мембрана Гейзер 50 GPD", "post": "Постфильтр Т33"},
    "Гейзер Макс": {"set": "Набор Гейзер Макс"},
    "Аквафор Кристалл Эко": {"set": "Комплект К3-К7B-K7"}
}

# --- СИНХРОНИЗАЦИЯ И МИГРАЦИЯ ДАННЫХ ---
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
                            else:
                                users_db[uid] = val
            elif action == "save":
                payload = {"files": {"filters_db.json": {"content": json.dumps(users_db, ensure_ascii=False, indent=2)}}}
                await session.patch(url, headers=headers, json=payload)
        except Exception as e: logging.error(f"Sync Error: {e}")

# --- ПОЛНЫЙ КАТАЛОГ СИСТЕМ (18 БРЕНДОВ) ---
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

# --- КЛАВИАТУРЫ И УТИЛИТЫ ---
def get_main_menu():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("📊 Статус", "➕ Добавить фильтр")
    kb.row("📅 Заменил картридж", "🛒 Купить картриджи")
    kb.row("📜 История", "⚙️ Настройки")
    return kb

def get_back_btn(callback_data):
    return types.InlineKeyboardButton("⬅️ Назад", callback_data=callback_data)

def get_user_filters(uid):
    data = users_db.get(str(uid), {})
    if isinstance(data, list): return data
    return data.get("filters", [])

def get_item_name(f, code):
    custom = f.get("custom_names", {}).get(code)
    if custom: return custom
    model_auto = AUTO_NAMES.get(f['model'], {}).get(code)
    if model_auto: return model_auto
    return FILTER_CONFIGS[f['category']][code]['name']

async def track_activity(message: types.Message):
    uid = str(message.from_user.id)
    if uid not in users_db: users_db[uid] = {"filters": [], "snooze_until": None}
    elif isinstance(users_db[uid], list): users_db[uid] = {"filters": users_db[uid], "snooze_until": None}
    users_db[uid]["username"] = f"@{message.from_user.username}" if message.from_user.username else "—"
    users_db[uid]["full_name"] = message.from_user.full_name
    users_db[uid]["last_seen"] = datetime.now().strftime("%d.%m.%Y %H:%M")
    await sync_gist("save")

# --- ОБРАБОТЧИКИ ГЛАВНОГО МЕНЮ (МАРШРУТИЗАЦИЯ ТЕКСТА) ---

@dp.message_handler(commands=['start'], state='*')
async def cmd_start(message: types.Message, state: FSMContext):
    await state.finish(); await track_activity(message)
    await message.answer("Привет! Я помогу следить за твоими фильтрами.", reply_markup=get_main_menu())

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

@dp.message_handler(lambda m: m.text == "➕ Добавить фильтр", state='*')
async def add_filter_start(message: types.Message, state: FSMContext):
    await state.finish(); await track_activity(message)
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("💧 Обратный осмос", callback_data="cat_osmos"),
           types.InlineKeyboardButton("🧪 3-ступенчатый", callback_data="cat_stage3"),
           types.InlineKeyboardButton("🚰 Магистральный", callback_data="cat_flow"),
           get_back_btn("main_menu"))
    await message.answer("Какой тип фильтра добавляем?", reply_markup=kb)

@dp.message_handler(lambda m: m.text == "📅 Заменил картридж", state='*')
async def cmd_replace(message: types.Message, state: FSMContext):
    await state.finish(); await track_activity(message)
    filters = get_user_filters(message.from_user.id)
    if not filters: return await message.answer("Добавьте фильтр.")
    kb = types.InlineKeyboardMarkup(row_width=1)
    for i, f in enumerate(filters): kb.add(types.InlineKeyboardButton(f['model'], callback_data=f"sr_{i}"))
    kb.add(get_back_btn("main_menu"))
    await message.answer("В какой системе произведена замена?", reply_markup=kb)

@dp.message_handler(lambda m: m.text == "🛒 Купить картриджи", state='*')
async def cmd_buy_menu(message: types.Message, state: FSMContext):
    await state.finish(); await track_activity(message)
    filters = get_user_filters(message.from_user.id)
    if not filters: return await message.answer("Сначала добавьте фильтр.")
    kb = types.InlineKeyboardMarkup(row_width=1)
    for i, f in enumerate(filters): kb.add(types.InlineKeyboardButton(f['model'], callback_data=f"buy_{i}"))
    kb.add(get_back_btn("main_menu"))
    await message.answer("Для какой системы ищем расходники?", reply_markup=kb)

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

@dp.message_handler(lambda m: m.text == "⚙️ Настройки", state='*')
async def cmd_settings(message: types.Message, state: FSMContext):
    await state.finish(); await track_activity(message)
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("ℹ️ FAQ / Справка", callback_data="set_faq"),
           types.InlineKeyboardButton("✏️ Названия картриджей", callback_data="set_names"),
           types.InlineKeyboardButton("⏱ Сроки замены", callback_data="set_ints"),
           types.InlineKeyboardButton("🗑 Удалить фильтр", callback_data="set_del"),
           get_back_btn("main_menu"))
    await message.answer("⚙️ <b>Настройки:</b>", reply_markup=kb)

# --- ЛОГИКА ДОБАВЛЕНИЯ ФИЛЬТРА ИЗ КАТАЛОГА ---

@dp.callback_query_handler(lambda c: c.data.startswith("cat_"), state='*')
async def process_cat(callback_query: types.CallbackQuery, state: FSMContext):
    cat = callback_query.data.split('_')[1]
    if cat == "flow":
        await bot.send_message(callback_query.from_user.id, "💡 <b>Подсказка:</b> Для магистральных фильтров подходят разные картриджи. Вы сможете указать точную модель (например, МП-5В) в Настройках после добавления.")
    kb = types.InlineKeyboardMarkup(row_width=2)
    for b in CATALOGS[cat].keys(): kb.insert(types.InlineKeyboardButton(b, callback_data=f"br_{cat}_{b}"))
    kb.row(get_back_btn("main_menu"))
    await bot.edit_message_text("Выберите производителя:", callback_query.message.chat.id, callback_query.message.message_id, reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith("br_"), state='*')
async def process_brand(callback_query: types.CallbackQuery, state: FSMContext):
    _, cat, brand = callback_query.data.split('_'); kb = types.InlineKeyboardMarkup(row_width=1)
    for idx, m in enumerate(CATALOGS[cat][brand]): kb.add(types.InlineKeyboardButton(m, callback_data=f"mod_{cat}_{brand}_{idx}"))
    kb.add(get_back_btn(f"cat_{cat}"))
    await bot.edit_message_text(f"Модели {brand}:", callback_query.message.chat.id, callback_query.message.message_id, reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith("mod_"), state='*')
async def process_model(callback_query: types.CallbackQuery, state: FSMContext):
    _, cat, brand, idx = callback_query.data.split('_'); model_name = CATALOGS[cat][brand][int(idx)]
    uid = str(callback_query.from_user.id); now_date = datetime.now().strftime("%d.%m.%Y")
    intervals = {code: data['interval'] for code, data in FILTER_CONFIGS[cat].items()}
    
    # СТРОГАЯ КОРРЕКЦИЯ СТУПЕНЕЙ ДЛЯ ОСМОСА С МИНЕРАЛИЗАТОРОМ (ATOLL М-СЕРИИ И ДР.)
    if cat == "osmos":
        if any(x in model_name.lower() for x in ["m", "мин", "морион"]):
            intervals["post"] = 0  # Принудительно отключаем постфильтр, оставляя ровно 5 ступеней
        else:
            intervals["min"] = 0   # Для обычных систем отключаем минерализатор
            
    if uid not in users_db: users_db[uid] = {"filters": [], "snooze_until": None}
    users_db[uid]["filters"].append({
        "model": f"{brand} {model_name}", "category": cat, "intervals": intervals,
        "history": [{"date": now_date, "item": "Система добавлена"}], "created_at": now_date, "custom_names": {}
    })
    await sync_gist("save")
    await bot.delete_message(callback_query.message.chat.id, callback_query.message.message_id)
    await bot.send_message(uid, f"✅ <b>{brand} {model_name}</b> успешно добавлена!", reply_markup=get_main_menu())

# --- ВЫБОР И ФИКСАЦИЯ ЗАМЕНЫ КАРТРИДЖЕЙ ---

@dp.callback_query_handler(lambda c: c.data.startswith("sr_"), state='*')
async def sr_choice(callback_query: types.CallbackQuery):
    idx = int(callback_query.data.split('_')[1]); uid = str(callback_query.from_user.id)
    f = get_user_filters(uid)[idx]
    kb = types.InlineKeyboardMarkup(row_width=1)
    for code, val in f['intervals'].items():
        if val > 0: kb.add(types.InlineKeyboardButton(get_item_name(f, code), callback_data=f"opt_{code}_{idx}"))
    kb.add(get_back_btn("main_menu"))
    await bot.edit_message_text("Что именно заменили?", callback_query.message.chat.id, callback_query.message.message_id, reply_markup=kb)

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
    idx = int(idx); f = users_db[uid]["filters"][idx]
    name = get_item_name(f, code)
    users_db[uid]["filters"][idx]['history'].append({"date": datetime.now().strftime("%d.%m.%Y"), "item": name})
    await sync_gist("save")
    await bot.delete_message(callback_query.message.chat.id, callback_query.message.message_id)
    await bot.send_message(uid, f"✅ Замена {name} сохранена!", reply_markup=get_main_menu())

@dp.callback_query_handler(lambda c: c.data.startswith("dm_"), state='*')
async def date_man_start(callback_query: types.CallbackQuery, state: FSMContext):
    _, code, idx = callback_query.data.split('_')
    await state.update_data(dm_code=code, dm_idx=int(idx))
    await FilterStates.waiting_for_date.set()
    
    # СКРЫВАЕМ СТАРЫЕ КНОПКИ ПРИ РУЧНОМ ВВОДЕ
    try: await bot.delete_message(callback_query.message.chat.id, callback_query.message.message_id)
    except: pass
    
    # ПОДСКАЗКА №3: РУЧНОЙ ВВОД ДАТЫ
    hint_text = "💡 <b>Подсказка:</b> Ручной ввод полезен, если вы фактически поменяли картридж несколько дней назад. Это сохранит точный календарь обслуживания."
    await bot.send_message(callback_query.from_user.id, hint_text)
    await bot.send_message(callback_query.from_user.id, "✍️ <b>Введите дату замены (ДД.ММ.ГГГГ):</b>")

@dp.message_handler(state=FilterStates.waiting_for_date)
async def date_man_msg(message: types.Message, state: FSMContext):
    try:
        valid = datetime.strptime(message.text, "%d.%m.%Y").strftime("%d.%m.%Y")
        data = await state.get_data(); uid = str(message.from_user.id)
        idx, code = data['dm_idx'], data['dm_code']
        f = users_db[uid]["filters"][idx]
        name = get_item_name(f, code)
        users_db[uid]["filters"][idx]['history'].append({"date": valid, "item": name})
        await sync_gist("save"); await state.finish()
        await message.answer(f"✅ Сохранено на {valid}!", reply_markup=get_main_menu())
    except:
        await message.answer("⚠️ Ошибка! Шаблон: ДД.ММ.ГГГГ")

# --- ПОДМЕНЮ НАСТРОЕК: ИЗМЕНЕНИЕ ИМЕН КАРТРИДЖЕЙ ---

@dp.callback_query_handler(lambda c: c.data == "set_names", state='*')
async def set_names_list(callback_query: types.CallbackQuery):
    uid = str(callback_query.from_user.id); filters = get_user_filters(uid)
    kb = types.InlineKeyboardMarkup(row_width=1)
    for i, f in enumerate(filters): kb.add(types.InlineKeyboardButton(f['model'], callback_data=f"ren_{i}"))
    kb.add(get_back_btn("set_back_to_settings"))
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
    
    # СКРЫВАЕМ СТАРЫЕ КНОПКИ ПРИ НАЧАЛЕ ПЕРЕИМЕНОВАНИЯ
    try: await bot.delete_message(callback_query.message.chat.id, callback_query.message.message_id)
    except: pass
    
    # ПОДСКАЗКА №1: НАЗВАНИЯ КАРТРИДЖЕЙ
    hint_text = "💡 <b>Подсказка:</b> Вы можете присвоить ступеням любые понятные вам имена. Введённое название автоматически добавится к названию фильтра при поиске на Ozon/Wildberries."
    await bot.send_message(callback_query.from_user.id, hint_text)
    await bot.send_message(callback_query.from_user.id, "✍️ <b>Введите новое точное название картриджа:</b>\n(Например: МП-5В)")

@dp.message_handler(state=FilterStates.waiting_for_cartridge_rename)
async def ren_input_save(message: types.Message, state: FSMContext):
    data = await state.get_data(); uid = str(message.from_user.id)
    idx, code = data['edit_idx'], data['edit_code']
    if "custom_names" not in users_db[uid]["filters"][idx]: users_db[uid]["filters"][idx]["custom_names"] = {}
    users_db[uid]["filters"][idx]["custom_names"][code] = message.text
    await sync_gist("save"); await state.finish()
    await message.answer(f"✅ Название «{message.text}» сохранено!", reply_markup=get_main_menu())

# --- ПОДМЕНЮ НАСТРОЕК: СРОКИ ЗАМЕНЫ ---

@dp.callback_query_handler(lambda c: c.data == "set_ints", state='*')
async def set_ints_list(callback_query: types.CallbackQuery):
    uid = str(callback_query.from_user.id); filters = get_user_filters(uid)
    kb = types.InlineKeyboardMarkup(row_width=1)
    for i, f in enumerate(filters): kb.add(types.InlineKeyboardButton(f['model'], callback_data=f"si_{i}"))
    kb.add(get_back_btn("set_back_to_settings"))
    await bot.edit_message_text("Изменить сроки для системы:", callback_query.message.chat.id, callback_query.message.message_id, reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith("si_"), state='*')
async def si_choice(callback_query: types.CallbackQuery):
    idx = int(callback_query.data.split('_')[1]); uid = str(callback_query.from_user.id); f = get_user_filters(uid)[idx]
    kb = types.InlineKeyboardMarkup(row_width=1)
    for code, val in f['intervals'].items():
        if val > 0:  # Показываем только активные ступени (у Atoll 575m постфильтр со значением 0 тут не появится)
            kb.add(types.InlineKeyboardButton(f"{get_item_name(f, code)} ({val} мес)", callback_data=f"ei_{code}_{idx}"))
    kb.add(get_back_btn("set_ints"))
    await bot.edit_message_text(f"Ступень {f['model']}:", callback_query.message.chat.id, callback_query.message.message_id, reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith("ei_"), state='*')
async def ei_start(callback_query: types.CallbackQuery, state: FSMContext):
    _, code, idx = callback_query.data.split('_')
    await state.update_data(ei_code=code, ei_idx=int(idx))
    await FilterStates.waiting_for_interval_change.set()
    
    # СКРЫВАЕМ СТАРЫЕ КНОПКИ ПРИ СМЕНЕ ИНТЕРВАЛА
    try: await bot.delete_message(callback_query.message.chat.id, callback_query.message.message_id)
    except: pass
    
    # ПОДСКАЗКА №2: СМЕНА ИНТЕРВАЛОВ
    hint_text = "💡 <b>Подсказка:</b> Если вода жесткая или ржавая, стандартный интервал (например, 6 месяцев) рекомендуется уменьшить вручную, чтобы получать своевременные оповещения."
    await bot.send_message(callback_query.from_user.id, hint_text)
    await bot.send_message(callback_query.from_user.id, "Введите новый срок службы в месяцах (0 - отключить):")

@dp.message_handler(state=FilterStates.waiting_for_interval_change)
async def ei_msg(message: types.Message, state: FSMContext):
    if not message.text.isdigit(): return await message.answer("⚠️ Введите число.")
    data = await state.get_data(); uid = str(message.from_user.id); idx, code = data['ei_idx'], data['ei_code']
    users_db[uid]["filters"][idx]['intervals'][code] = int(message.text)
    await sync_gist("save"); await state.finish()
    await message.answer("✅ Срок изменен!", reply_markup=get_main_menu())

# --- ПОДМЕНЮ НАСТРОЕК: УДАЛЕНИЕ СИСТЕМЫ ---

@dp.callback_query_handler(lambda c: c.data == "set_del", state='*')
async def set_del_list(callback_query: types.CallbackQuery):
    uid = str(callback_query.from_user.id); filters = get_user_filters(uid)
    kb = types.InlineKeyboardMarkup(row_width=1)
    for i, f in enumerate(filters): kb.add(types.InlineKeyboardButton(f"🗑 {f['model']}", callback_data=f"del_{i}"))
    kb.add(get_back_btn("set_back_to_settings"))
    await bot.edit_message_text("Какую систему удалить?", callback_query.message.chat.id, callback_query.message.message_id, reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith("del_"), state='*')
async def del_confirm(callback_query: types.CallbackQuery):
    idx = int(callback_query.data.split('_')[1]); uid = str(callback_query.from_user.id)
    name = users_db[uid]["filters"].pop(idx)['model']; await sync_gist("save")
    await bot.delete_message(callback_query.message.chat.id, callback_query.message.message_id)
    await bot.send_message(uid, f"✅ Система {name} удалена.", reply_markup=get_main_menu())

# --- ИСПРАВЛЕННАЯ СИСТЕМА УМНОГО ПОИСКА НА МАРКЕТПЛЕЙСАХ ---

@dp.callback_query_handler(lambda c: c.data.startswith("buy_"), state='*')
async def process_buy_filter(callback_query: types.CallbackQuery):
    idx = int(callback_query.data.split('_')[1])
    uid = str(callback_query.from_user.id)
    filters = get_user_filters(uid)
    if idx >= len(filters): return await bot.answer_callback_query(callback_query.id, "Система не найдена.")
    
    f = filters[idx]
    kb = types.InlineKeyboardMarkup(row_width=1)
    for code, val in f['intervals'].items():
        if val > 0:
            item_name = get_item_name(f, code)
            kb.add(types.InlineKeyboardButton(f"🔍 Найти: {item_name}", callback_data=f"bi_{idx}_{code}"))
            
    kb.add(get_back_btn("main_menu"))
    await bot.edit_message_text(f"Какой картридж для системы <b>{f['model']}</b> вы хотите заказать?", 
                                callback_query.message.chat.id, callback_query.message.message_id, reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith("bi_"), state='*')
async def process_buy_item(callback_query: types.CallbackQuery):
    _, idx, code = callback_query.data.split('_')
    idx = int(idx); uid = str(callback_query.from_user.id)
    f = users_db[uid]["filters"][idx]
    item_name = get_item_name(f, code)
    
    # СКЛЕИВАЕМ НАЗВАНИЕ ФИЛЬТРА И МОДЬ КАРТРИДЖА ДЛЯ ТОЧНОГО РЕЗУЛЬТАТА ПОИСКА
    search_query = f"{f['model']} {item_name}"
    
    ozon_url = f"https://www.ozon.ru/search/?text={quote(search_query)}"
    wb_url = f"https://www.wildberries.ru/catalog/0/search.aspx?search={quote(search_query)}"
    
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(
        types.InlineKeyboardButton("🛒 Перейти на Ozon", url=ozon_url),
        types.InlineKeyboardButton("🛍️ Перейти на Wildberries", url=wb_url),
        types.InlineKeyboardButton("⬅️ Назад к списку", callback_data=f"buy_{idx}")
    )
    
    await bot.edit_message_text(f"Сформированы ссылки для точного поиска:\n📦 <b>{search_query}</b>", 
                                callback_query.message.chat.id, callback_query.message.message_id, reply_markup=kb)

# --- ИСПРАВЛЕННОЕ ПОДМЕНЮ НАСТРОЕК: РАБОЧИЙ FAQ И СТАТИЧЕСКАЯ НАВИГАЦИЯ ---

@dp.callback_query_handler(lambda c: c.data == "set_faq", state='*')
async def set_faq_handler(callback_query: types.CallbackQuery):
    faq_text = (
        "ℹ️ <b>СПРАВКА И ПОМОЩЬ (FAQ)</b>\n" + "━" * 15 + "\n\n"
        "📌 <b>1. Названия картриджей</b>\n"
        "Вы можете присвоить ступеням кастомные текстовые имена (например, 'Мембрана 75', 'Механика'). Перейдите в <i>Настройки -> Названия картриджей</i>. Бот автоматически объединит это имя с моделью фильтра для генерации точных ссылок моментальной покупки на маркетплейсах.\n\n"
        "📌 <b>2. Смена интервалов</b>\n"
        "Для калибровки под индивидуальное качество воды сроки обслуживания настраиваются под себя. В разделе <i>Настройки -> Сроки замены</i> вы можете переписать базовый период обслуживания в месяцах для любой ступени.\n\n"
        "📌 <b>3. Ручной ввод даты замены</b>\n"
        "Если вы провели обслуживание картриджей ранее и забыли вовремя зафиксировать это в боте — нажмите кнопку <i>📅 Заменил картридж</i> и выберите пункт <i>Вручную</i>. Введите дату по строгому шаблону <code>ДД.ММ.ГГГГ</code> (к примеру: 15.05.2026).\n\n"
        "━" * 15 + "\n\n"
        "🎯 <b>Как улучшить поиск картриджей?</b>\n"
        "Если поиск маркетплейса выдает не то, зайдите в <b>Настройки -> Названия картриджей</b> и впишите точную модель (например, МП-5В). Бот при нажатии «Купить» подставит именно её.\n\n"
        "🤖 <b>Авто-названия:</b> Для известных систем (Atoll, Аквафор Морион) бот автоматически подбирает заводские наименования коробок (№202, №203).\n\n"
        "🔔 <b>Напоминания:</b> Бот проверяет ресурс в районе 10:00 утра. Оповещения можно откладывать на 1 день или 7 дней кнопками."
    )
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("⬅️ Назад", callback_data="set_back_to_settings"))
    await bot.edit_message_text(faq_text, callback_query.message.chat.id, callback_query.message.message_id, reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data == "set_back_to_settings", state='*')
async def set_back_to_settings_cb(callback_query: types.CallbackQuery, state: FSMContext):
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("ℹ️ FAQ / Справка", callback_data="set_faq"),
           types.InlineKeyboardButton("✏️ Названия картриджей", callback_data="set_names"),
           types.InlineKeyboardButton("⏱ Сроки замены", callback_data="set_ints"),
           types.InlineKeyboardButton("🗑 Удалить фильтр", callback_data="set_del"),
           get_back_btn("main_menu"))
    await bot.edit_message_text("⚙️ <b>Настройки:</b>", callback_query.message.chat.id, callback_query.message.message_id, reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data == "main_menu", state='*')
async def back_to_main_menu(callback_query: types.CallbackQuery, state: FSMContext):
    await state.finish()
    try: await bot.delete_message(callback_query.message.chat.id, callback_query.message.message_id)
    except: pass
    await bot.send_message(callback_query.from_user.id, "Главное меню:", reply_markup=get_main_menu())

# --- СИСТЕМА УМНЫХ ОПОВЕЩЕНИЙ И СНА КНОПОК ---

@dp.callback_query_handler(lambda c: c.data.startswith("sn_"), state='*')
async def process_snooze(callback_query: types.CallbackQuery):
    days = int(callback_query.data.split('_')[1]); uid = str(callback_query.from_user.id)
    until_dt = datetime.now() + timedelta(days=days)
    if uid in users_db:
        users_db[uid]["snooze_until"] = until_dt.strftime("%Y-%m-%d %H:%M:%S")
        await sync_gist("save")
    await bot.answer_callback_query(callback_query.id, "Отложено")
    await bot.edit_message_text(f"✅ Напомню через {days} дн.", callback_query.message.chat.id, callback_query.message.message_id)

async def reminder_scheduler():
    while True:
        now = datetime.now()
        if now.hour == 10:  # Проверка строго раз в сутки в 10 утра
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
                        last_date_str = next((h['date'] for h in reversed(f['history']) if h['item'] not in ["Начало обслуживания", "Система добавлена"]), f['created_at'])
                        last_date = datetime.strptime(last_date_str, "%d.%m.%Y")
                        if int(interval * 30.4 - (now - last_date).days) <= 7: 
                            alert_msg += f"⚠️ <b>{f['model']}</b>: {get_item_name(f, code)}\n"
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

# --- МЕНЮ СТАТИСТИКИ АДМИНИСТРАТОРА ---

@dp.message_handler(commands=['admin'], state='*')
async def cmd_admin(message: types.Message, state: FSMContext):
    if str(message.from_user.id) != str(ADMIN_ID): return
    await state.finish(); total = len(users_db)
    await message.answer(f"👑 <b>АДМИН-ПАНЕЛЬ</b>\n👥 Юзеров в базе: {total}\n\nДля сообщений юзерам: /broadcast ТЕКСТ")

@dp.message_handler(commands=['broadcast'], state='*')
async def cmd_broadcast(message: types.Message, state: FSMContext):
    if str(message.from_user.id) != str(ADMIN_ID): return
    text = message.get_args()
    if not text: return await message.answer("Синтаксис: /broadcast Текст сообщения")
    for uid in users_db.keys():
        try: await bot.send_message(uid, f"📢 <b>СООБЩЕНИЕ:</b>\n\n{text}")
        except: pass
    await message.answer("✅ Уведомления доставлены.")

# --- ВЕБ-СЕРВЕР ДЛЯ РАБОТЫ НА ХОСТИНГЕ ---
async def start_webserver():
    app = web.Application()
    app.router.add_get("/", lambda r: web.Response(text="Bot Running"))
    runner = web.AppRunner(app); await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', int(os.environ.get("PORT", 10000))).start()

async def on_startup(dp):
    await bot.delete_webhook(drop_pending_updates=True)
    await sync_gist("load")
    asyncio.create_task(start_webserver())
    asyncio.create_task(reminder_scheduler())

if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True, on_startup=on_startup)
