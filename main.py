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

# --- БАЗА ДАННЫХ GIST ---
async def sync_gist(action="load"):
    global users_db
    if not GIST_ID or not GITHUB_TOKEN:
        logging.warning("GIST переменные не настроены!")
        return
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
        except Exception as e:
            logging.error(f"Gist Sync Error: {e}")

# --- КАТАЛОГ СИСТЕМ ---
CATALOGS = {
    "osmos": {
        "Атолл": ["A-550", "A-575", "A-550m", "A-575m", "A-450"],
        "Барьер": ["Профи Осмо 100", "Профи Осмо 100 М", "Compact Osmo"],
        "Гейзер": ["Престиж", "Аллегро", "Престиж М", "Аллегро М"],
        "Аквафор": ["DWM-101S Морион", "DWM-102S", "Осмо Про 50", "Осмо Про 50 М"]
    },
    "stage3": {
        "Атолл": ["Патриот", "D-31"],
        "Гейзер": ["Макс", "Стандарт", "БИО"],
        "Аквафор": ["Кристалл Эко", "Трио", "Кристалл Н"]
    },
    "flow": {
        "Аквафор": ["Фаворит", "Модерн", "Викинг 10SL", "Викинг 10BB"],
        "Гейзер": ["Тайфун 10SL", "Тайфун 10BB"],
        "Джилекс": ["Колба 10SL", "Колба 10BB"]
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
def get_progress_bar(percent):
    filled = int(percent / 10)
    bar = "█" * filled + "░" * (10 - filled)
    return f"[{bar}] {percent}%"

# --- КЛАВИАТУРЫ ---
def get_main_menu():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("📊 Статус", "➕ Добавить фильтр")
    kb.row("📅 Заменил картридж", "🛒 Купить картриджи")
    kb.row("📜 История", "⚙️ Настройки")
    return kb

def get_categories_kb():
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("💧 Обратный осмос", callback_data="cat_osmos"),
           types.InlineKeyboardButton("🧪 3-ступенчатый", callback_data="cat_stage3"),
           types.InlineKeyboardButton("🚰 Магистральный / Проточный", callback_data="cat_flow"))
    return kb

def get_settings_kb():
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("ℹ️ FAQ / Справка", callback_data="set_faq"),
           types.InlineKeyboardButton("⏱ Изменить интервалы", callback_data="set_ints"),
           types.InlineKeyboardButton("🗑 Удалить систему", callback_data="set_del"),
           types.InlineKeyboardButton("🧨 Очистить профиль", callback_data="set_clear"),
           types.InlineKeyboardButton("❌ Отмена", callback_data="cancel"))
    return kb

# --- ОБРАБОТЧИКИ ГЛАВНОГО МЕНЮ ---

@dp.message_handler(commands=['start'])
async def cmd_start(message: types.Message):
    await message.answer("💧 <b>Сервис контроля фильтров v11.0</b>\nВыберите действие в меню или добавьте новую систему.", reply_markup=get_main_menu())

@dp.message_handler(lambda m: m.text == "➕ Добавить фильтр")
async def add_filter_start(message: types.Message):
    await message.answer("Выберите тип вашей системы:", reply_markup=get_categories_kb())

@dp.message_handler(lambda m: m.text == "⚙️ Настройки")
async def cmd_settings(message: types.Message):
    await message.answer("⚙️ <b>Меню настроек:</b>", reply_markup=get_settings_kb())

@dp.message_handler(lambda m: m.text == "📊 Статус")
async def cmd_status(message: types.Message):
    uid = str(message.from_user.id)
    if uid not in users_db or not users_db[uid]:
        return await message.answer("У вас нет добавленных систем. Нажмите «➕ Добавить фильтр»")
    
    res = "📊 <b>ТЕКУЩИЙ МОНИТОРИНГ:</b>\n" + "━" * 15 + "\n\n"
    now = datetime.now()
    kb = types.InlineKeyboardMarkup(row_width=1)
    
    for i, f in enumerate(users_db[uid]):
        res += f"🚰 <b>{f['model']}</b>\n"
        needs_buy = False
        for code, interval in f['intervals'].items():
            if interval == 0: continue
            name = FILTER_CONFIGS[f['category']][code]['name']
            last_date_str = next((h['date'] for h in reversed(f['history']) if h['item'] == name), f['created_at'])
            last_date = datetime.strptime(last_date_str, "%d.%m.%Y")
            
            total_days = interval * 30.4
            elapsed_days = (now - last_date).days
            days_left = int(total_days - elapsed_days)
            percent = max(0, min(100, int((days_left / total_days) * 100)))
            
            icon = "🟢"
            if days_left <= 30: icon = "🟡"; needs_buy = True
            if days_left <= 0: icon = "🔴"; needs_buy = True
            
            res += f"  ├ {icon} <b>{name}</b>\n"
            res += f"  └ {get_progress_bar(percent)} (~{days_left} дн.)\n"
        
        if needs_buy:
            kb.add(types.InlineKeyboardButton(f"🛒 Купить для {f['model'][:15]}...", callback_data=f"buy_{i}"))
        res += "\n"
    await message.answer(res, reply_markup=kb if kb.inline_keyboard else None)

# --- НАВИГАЦИЯ КАТАЛОГА ---

@dp.callback_query_handler(lambda c: c.data == "back_cats")
async def back_to_cats(callback_query: types.CallbackQuery):
    await bot.edit_message_text("Выберите тип системы:", callback_query.message.chat.id, callback_query.message.message_id, reply_markup=get_categories_kb())

@dp.callback_query_handler(lambda c: c.data.startswith("cat_"))
async def process_cat(callback_query: types.CallbackQuery):
    cat = callback_query.data.split('_')[1]
    kb = types.InlineKeyboardMarkup(row_width=2)
    for b in CATALOGS[cat].keys(): kb.insert(types.InlineKeyboardButton(b, callback_data=f"br_{cat}_{b}"))
    kb.add(types.InlineKeyboardButton("📝 Свой вариант", callback_data=f"man_{cat}"),
           types.InlineKeyboardButton("⬅️ Назад", callback_data="back_cats"))
    await bot.edit_message_text("Выберите производителя:", callback_query.message.chat.id, callback_query.message.message_id, reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith("br_"))
async def process_brand(callback_query: types.CallbackQuery):
    _, cat, brand = callback_query.data.split('_')
    kb = types.InlineKeyboardMarkup(row_width=1)
    for idx, m in enumerate(CATALOGS[cat][brand]): kb.add(types.InlineKeyboardButton(m, callback_data=f"mod_{cat}_{brand}_{idx}"))
    kb.add(types.InlineKeyboardButton("📝 Свой вариант", callback_data=f"man_{cat}"),
           types.InlineKeyboardButton("⬅️ Назад", callback_data=f"cat_{cat}"))
    await bot.edit_message_text(f"Модели {brand}:", callback_query.message.chat.id, callback_query.message.message_id, reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith("mod_"))
async def process_model(callback_query: types.CallbackQuery):
    _, cat, brand, idx = callback_query.data.split('_')
    model_name = CATALOGS[cat][brand][int(idx)]
    uid = str(callback_query.from_user.id)
    
    intervals = {code: data['interval'] for code, data in FILTER_CONFIGS[cat].items()}
    if cat == "osmos" and not any(x in model_name.lower() for x in ["m", "мин", "морион"]): intervals["min"] = 0
    
    if uid not in users_db: users_db[uid] = []
    users_db[uid].append({"model": f"{brand} {model_name}", "category": cat, "intervals": intervals, "history": [], "created_at": datetime.now().strftime("%d.%m.%Y")})
    await sync_gist("save")
    
    msg = (f"✅ <b>{brand} {model_name}</b> добавлена!\n\n"
           f"📅 По умолчанию отсчет ресурса начат с сегодняшнего дня.\n\n"
           f"❓ <b>Меняли картриджи раньше?</b>\n"
           f"Нажмите кнопку «📅 Заменил картридж», выберите ступень и введите реальную дату прошлой замены — "
           f"я мгновенно пересчитаю актуальный статус!")
    await bot.send_message(uid, msg, reply_markup=get_main_menu())

# --- СВОЙ ВАРИАНТ ---
@dp.callback_query_handler(lambda c: c.data.startswith("man_"))
async def man_input_start(callback_query: types.CallbackQuery, state: FSMContext):
    cat = callback_query.data.split('_')[1]
    await state.update_data(manual_cat=cat)
    await FilterStates.waiting_for_manual_name.set()
    await bot.send_message(callback_query.from_user.id, "📝 <b>Введите название вашей системы:</b>", reply_markup=types.ReplyKeyboardRemove())

@dp.message_handler(state=FilterStates.waiting_for_manual_name)
async def man_name_msg(message: types.Message, state: FSMContext):
    data = await state.get_data()
    await state.update_data(manual_name=message.text, manual_idx=0, manual_ints={}, manual_comps=list(FILTER_CONFIGS[data['manual_cat']].keys()))
    await ask_man_step(message, state)

async def ask_man_step(message, state):
    data = await state.get_data()
    cat, idx = data['manual_cat'], data['manual_idx']
    if idx >= len(data['manual_comps']):
        uid = str(message.from_user.id)
        if uid not in users_db: users_db[uid] = []
        users_db[uid].append({"model": data['manual_name'], "category": cat, "intervals": data['manual_ints'], "history": [], "created_at": datetime.now().strftime("%d.%m.%Y")})
        await sync_gist("save"); await state.finish()
        return await message.answer(f"✅ Фильтр {data['manual_name']} создан!", reply_markup=get_main_menu())
    
    comp_info = FILTER_CONFIGS[cat][data['manual_comps'][idx]]
    await FilterStates.waiting_for_custom_interval.set()
    await message.answer(f"⏱ <b>Ступень: {comp_info['name']}</b>\n"
                         f"💡 Рекомендуемый срок: {comp_info['interval']} мес.\n\n"
                         f"Введите число месяцев. Если ступени нет — введите 0:")

@dp.message_handler(state=FilterStates.waiting_for_custom_interval)
async def man_int_msg(message: types.Message, state: FSMContext):
    if not message.text.isdigit(): return await message.answer("⚠️ Введите число.")
    data = await state.get_data()
    data['manual_ints'][data['manual_comps'][data['manual_idx']]] = int(message.text)
    await state.update_data(manual_ints=data['manual_ints'], manual_idx=data['manual_idx'] + 1)
    await ask_man_step(message, state)

# --- ЗАМЕНА КАРТРИДЖЕЙ ---

@dp.message_handler(lambda m: m.text == "📅 Заменил картридж")
async def cmd_replace(message: types.Message):
    uid = str(message.from_user.id)
    if uid not in users_db or not users_db[uid]: return await message.answer("Сначала добавьте фильтр.")
    kb = types.InlineKeyboardMarkup(row_width=1)
    for i, f in enumerate(users_db[uid]): kb.add(types.InlineKeyboardButton(f['model'], callback_data=f"sr_{i}"))
    await message.answer("В какой системе произведена замена?", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith("sr_"))
async def sr_choice_cb(callback_query: types.CallbackQuery):
    idx = int(callback_query.data.split('_')[1])
    f = users_db[str(callback_query.from_user.id)][idx]
    kb = types.InlineKeyboardMarkup(row_width=1)
    for code, val in f['intervals'].items():
        if val > 0: kb.add(types.InlineKeyboardButton(FILTER_CONFIGS[f['category']][code]['name'], callback_data=f"opt_{code}_{idx}"))
    await bot.edit_message_text("Что именно заменили?", callback_query.message.chat.id, callback_query.message.message_id, reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith("opt_"))
async def opt_cb(callback_query: types.CallbackQuery):
    _, code, idx = callback_query.data.split('_')
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("📅 Сегодня", callback_data=f"dn_{code}_{idx}"),
           types.InlineKeyboardButton("✍️ Ввести дату вручную", callback_data=f"dm_{code}_{idx}"))
    await bot.edit_message_text("Когда была замена?", callback_query.message.chat.id, callback_query.message.message_id, reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith("dn_"))
async def date_now_cb(callback_query: types.CallbackQuery):
    _, code, idx = callback_query.data.split('_')
    uid = str(callback_query.from_user.id)
    name = FILTER_CONFIGS[users_db[uid][int(idx)]['category']][code]['name']
    users_db[uid][int(idx)]['history'].append({"date": datetime.now().strftime("%d.%m.%Y"), "item": name})
    await sync_gist("save")
    await bot.edit_message_text(f"✅ Замена {name} сохранена сегодня!", callback_query.message.chat.id, callback_query.message.message_id)

@dp.callback_query_handler(lambda c: c.data.startswith("dm_"))
async def date_manual_start(callback_query: types.CallbackQuery, state: FSMContext):
    _, code, idx = callback_query.data.split('_')
    await state.update_data(dm_code=code, dm_idx=int(idx))
    await FilterStates.waiting_for_date.set()
    msg = ("✍️ <b>Введите дату замены в формате ДД.ММ.ГГГГ</b>\n(например: 15.01.2024)\n\n"
           "💡 <i>Вы можете вводить старые даты — бот пересчитает ресурс, и вы сразу увидите актуальный статус в меню Статус.</i>")
    await bot.send_message(callback_query.from_user.id, msg)

@dp.message_handler(state=FilterStates.waiting_for_date)
async def process_manual_date(message: types.Message, state: FSMContext):
    try:
        valid_date = datetime.strptime(message.text, "%d.%m.%Y").strftime("%d.%m.%Y")
        data = await state.get_data(); uid = str(message.from_user.id)
        name = FILTER_CONFIGS[users_db[uid][data['dm_idx']]['category']][data['dm_code']]['name']
        users_db[uid][data['dm_idx']]['history'].append({"date": valid_date, "item": name})
        await sync_gist("save"); await state.finish()
        await message.answer(f"✅ Сохранено на дату {valid_date}!", reply_markup=get_main_menu())
    except: await message.answer("⚠️ Введите дату как ДД.ММ.ГГГГ")

# --- НАСТРОЙКИ И FAQ ---

@dp.callback_query_handler(lambda c: c.data == "set_faq")
async def settings_faq(callback_query: types.CallbackQuery):
    faq_text = (
        "ℹ️ <b>СПРАВКА И СОВЕТЫ</b>\n" + "━" * 15 + "\n\n"
        "📅 <b>Старые даты:</b> Если вы только начали пользоваться ботом, внесите даты последних замен через кнопку «Заменил картридж». Бот пересчитает остаток ресурса автоматически.\n\n"
        "☁️ <b>Ваши данные:</b> Все настройки синхронизируются с GitHub Gist. Вы не потеряете данные при обновлении бота или смене сервера.\n\n"
        "⏱ <b>Интервалы:</b> Вы можете менять сроки под свой расход воды. Если поставить 0, бот перестанет отслеживать эту ступень.\n\n"
        "🔴 <b>Статусы:</b>\n"
        "🟢 — Ресурс > 30 дней\n"
        "🟡 — Осталось меньше месяца\n"
        "🔴 — Срок замены вышел\n\n"
        "🛠 <b>Совет инженера:</b> При замене картриджей всегда промывайте колбы и проверяйте состояние резиновых прокладок. Рекомендуется менять прокладки раз в год."
    )
    await bot.edit_message_text(faq_text, callback_query.message.chat.id, callback_query.message.message_id, reply_markup=get_settings_kb())

@dp.callback_query_handler(lambda c: c.data == "set_ints")
async def settings_ints(callback_query: types.CallbackQuery):
    uid = str(callback_query.from_user.id)
    kb = types.InlineKeyboardMarkup(row_width=1)
    for i, f in enumerate(users_db.get(uid, [])): kb.add(types.InlineKeyboardButton(f['model'], callback_data=f"si_{i}"))
    await bot.edit_message_text("Изменить сроки для системы:", callback_query.message.chat.id, callback_query.message.message_id, reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith("si_"))
async def si_cb(callback_query: types.CallbackQuery):
    idx = int(callback_query.data.split('_')[1])
    f = users_db[str(callback_query.from_user.id)][idx]
    kb = types.InlineKeyboardMarkup(row_width=1)
    for code, val in f['intervals'].items():
        name = FILTER_CONFIGS[f['category']][code]['name']
        kb.add(types.InlineKeyboardButton(f"{name} ({val} мес)", callback_data=f"ei_{code}_{idx}"))
    await bot.edit_message_text(f"Ступень {f['model']}:", callback_query.message.chat.id, callback_query.message.message_id, reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith("ei_"))
async def ei_cb(callback_query: types.CallbackQuery, state: FSMContext):
    _, code, idx = callback_query.data.split('_')
    await state.update_data(ei_code=code, ei_idx=int(idx))
    await FilterStates.waiting_for_interval_change.set()
    await bot.send_message(callback_query.from_user.id, "Введите новый интервал в месяцах (0 - отключить):")

@dp.message_handler(state=FilterStates.waiting_for_interval_change)
async def ei_msg(message: types.Message, state: FSMContext):
    if not message.text.isdigit(): return await message.answer("Число!")
    data = await state.get_data(); uid = str(message.from_user.id)
    users_db[uid][data['ei_idx']]['intervals'][data['ei_code']] = int(message.text)
    await sync_gist("save"); await state.finish()
    await message.answer("✅ Срок изменен!", reply_markup=get_main_menu())

@dp.callback_query_handler(lambda c: c.data == "set_del")
async def set_del_list(callback_query: types.CallbackQuery):
    uid = str(callback_query.from_user.id)
    kb = types.InlineKeyboardMarkup(row_width=1)
    for i, f in enumerate(users_db.get(uid, [])): kb.add(types.InlineKeyboardButton(f"🗑 {f['model']}", callback_data=f"del_{i}"))
    await bot.edit_message_text("Удалить систему:", callback_query.message.chat.id, callback_query.message.message_id, reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith("del_"))
async def del_confirm(callback_query: types.CallbackQuery):
    idx, uid = int(callback_query.data.split('_')[1]), str(callback_query.from_user.id)
    name = users_db[uid].pop(idx)['model']
    await sync_gist("save")
    await bot.edit_message_text(f"✅ Удалено: {name}", callback_query.message.chat.id, callback_query.message.message_id)

@dp.callback_query_handler(lambda c: c.data == "set_clear")
async def set_clear_all(callback_query: types.CallbackQuery):
    users_db[str(callback_query.from_user.id)] = []
    await sync_gist("save")
    await bot.edit_message_text("🧨 Профиль очищен.", callback_query.message.chat.id, callback_query.message.message_id)

@dp.callback_query_handler(lambda c: c.data == "cancel")
async def cancel_action(callback_query: types.CallbackQuery):
    await bot.delete_message(callback_query.message.chat.id, callback_query.message.message_id)

@dp.callback_query_handler(lambda c: c.data.startswith("buy_"))
async def buy_links_cb(callback_query: types.CallbackQuery):
    idx = int(callback_query.data.split('_')[1])
    f = users_db[str(callback_query.from_user.id)][idx]
    q = quote(f"картриджи {f['model']}")
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("🛒 На Ozon", url=f"https://www.ozon.ru/search/?text={q}"),
           types.InlineKeyboardButton("🟣 На Wildberries", url=f"https://www.wildberries.ru/catalog/0/search.aspx?search={q}"))
    await bot.send_message(callback_query.from_user.id, f"🛒 Рассходники для <b>{f['model']}</b>:", reply_markup=kb)

# --- ЗАПУСК ---
async def start_webserver():
    app = web.Application()
    app.router.add_get("/", lambda r: web.Response(text="Bot is running"))
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', int(os.environ.get("PORT", 10000))).start()

async def on_startup(dp):
    await sync_gist("load")
    await bot.delete_webhook(drop_pending_updates=True)
    asyncio.create_task(start_webserver())

if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True, on_startup=on_startup)
