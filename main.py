import asyncio
import json
import logging
import os
import sys
from datetime import datetime
from aiogram import Bot, Dispatcher, Router, types
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from aiogram.filters import CommandStart, StateFilter
from colorama import init, Fore, Back, Style

# Для healthcheck-сервера
from aiohttp import web

# Инициализация colorama для цветного логирования в Docker
init(autoreset=True, strip=False)
os.environ['PYTHONUNBUFFERED'] = '1'

# --- ФУНКЦИИ ЛОГИРОВАНИЯ (как во втором боте) ---
def get_time():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def get_user_info(user):
    username = f"@{user.username}" if user.username else "NoUsername"
    return f"ID:{user.id} | {username} | {user.first_name}"

def log_user_action(user, action):
    """Белый текст на Розовом фоне для действий пользователя"""
    print(f"{Fore.WHITE}{Back.MAGENTA}[{get_time()}] ПОЛЬЗОВАТЕЛЬ [{get_user_info(user)}] >>> {action}{Style.RESET_ALL}", flush=True)

def log_bot_reply(user, reply_text):
    """Белый текст на Синем фоне для ответов бота"""
    short_text = (reply_text[:100] + '...') if len(reply_text) > 100 else reply_text
    print(f"{Fore.WHITE}{Back.BLUE}[{get_time()}] БОТ ОТВЕТИЛ [{get_user_info(user)}] <<< {short_text}{Style.RESET_ALL}", flush=True)

def log_system(text):
    """Зеленый текст для системных событий"""
    print(f"{Fore.GREEN}[{get_time()}] СИСТЕМА: {text}{Style.RESET_ALL}", flush=True)

def log_error(text):
    """Красный текст для ошибок"""
    print(f"{Fore.RED}[{get_time()}] ОШИБКА: {text}{Style.RESET_ALL}", flush=True)

# Токен бота (читается из переменной окружения `BOT_TOKEN`)
TOKEN = os.getenv('BOT_TOKEN')
if not TOKEN or TOKEN.strip() == '':
    log_error("BOT_TOKEN не задан — установите переменную окружения BOT_TOKEN")
    raise SystemExit(1)

# --- ЗАГРУЗКА ДАННЫХ ---
VUZ_DATA = []
try:
    with open('vuz_data.json', 'r', encoding='utf-8') as f:
        raw = json.load(f)
        for i, entry in enumerate(raw):
            if not isinstance(entry, dict):
                log_error(f"Запись {i} не является объектом, пропущена")
                continue
            norm = {k.lower(): v for k, v in entry.items()}
            if 'вуз' not in norm or 'специальность' not in norm or 'предметы_список' not in norm:
                log_error(f"Запись {i} не содержит обязательных полей (вуз/специальность/предметы_список)")
                continue
            for k in list(norm.keys()):
                if 'мин' in k or 'проходной' in k or k.endswith('_балл') or k.endswith('_сумма'):
                    try:
                        norm[k] = int(norm[k])
                    except Exception:
                        try:
                            norm[k] = int(float(norm[k]))
                        except Exception:
                            norm[k] = 0
            VUZ_DATA.append(norm)
    log_system(f"Загружено {len(VUZ_DATA)} записей из vuz_data.json")
except Exception as e:
    log_error(f"Ошибка загрузки vuz_data.json: {e}")

# Список всех возможных предметов
ALL_SUBJECTS = [
    'математика', 'русский', 'информатика', 'физика', 'биология',
    'химия', 'иностранный_язык', 'география', 'литература', 'история', 'общество'
]

# FSM состояния
class UserState(StatesGroup):
    choosing_subjects = State()
    entering_scores = State()
    viewing_results = State()
    start_menu = State()
    choosing_university = State()
    viewing_direction = State()

# Инициализация бота и диспетчера
bot = Bot(token=TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
router = Router()
dp.include_router(router)

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---
def parse_subjects(subjects_str):
    subjects = [s.strip() for s in str(subjects_str).split(',') if s.strip()]
    required = []
    alternatives = []
    for subj in subjects:
        subj = subj.lower().replace(' ', '_')
        subj = subj.replace('иностарнный', 'иностранный_язык')
        subj = subj.replace('иностранный', 'иностранный_язык')
        if '/' in subj:
            alt = [a.strip().replace(' ', '_') for a in subj.split('/') if a.strip()]
            alternatives.append(alt)
        else:
            required.append(subj)
    return required, alternatives

SUBJECT_DISPLAY = {
    'математика': 'математика',
    'русский': 'русский',
    'информатика': 'информатика',
    'физика': 'физика',
    'биология': 'биология',
    'химия': 'химия',
    'иностранный язык': 'иностранный_язык',
    'география': 'география',
    'литература': 'литература',
    'история': 'история',
    'общество': 'общество'
}
KEY_TO_DISPLAY = {v: k for k, v in SUBJECT_DISPLAY.items()}

def check_fit(user_scores, entry):
    try:
        subjects_str = entry.get('предметы_список') or entry.get('Предметы_список')
        required, alternatives = parse_subjects(subjects_str)
        for req in required:
            if req not in user_scores:
                return False
        for alt_group in alternatives:
            has_one = False
            for alt in alt_group:
                if alt in user_scores:
                    has_one = True
                    break
            if not has_one:
                return False
        min_fields = {
            'математика': 'мин_математика',
            'русский': 'мин_русский',
            'информатика': 'мин_информатика',
            'физика': 'мин_физика',
            'биология': 'мин_биология',
            'химия': 'мин_химия',
            'иностранный_язык': 'мин_иностранный_язык',
            'география': 'мин_география',
            'литература': 'мин_литература',
            'история': 'мин_история',
            'общество': 'мин_общество'
        }
        total_score = 0
        for subj, score in user_scores.items():
            total_score += score
            min_field = min_fields.get(subj)
            if min_field:
                min_score = int(entry.get(min_field, 0))
                if score < min_score:
                    return False
        passing_score = int(entry.get('проходной_балл_сумма', entry.get('Проходной_балл_сумма', 0)))
        return total_score >= passing_score
    except Exception as e:
        log_error(f"Ошибка проверки соответствия: {e}")
        return False

def find_matching_vuz(user_scores):
    matches = []
    for entry in VUZ_DATA:
        if check_fit(user_scores, entry):
            matches.append(entry)
    return matches

# --- ОБРАБОТЧИКИ ---
@router.message(CommandStart(), StateFilter('*'))
async def start_command(message: types.Message, state: FSMContext):
    try:
        await state.clear()
        log_user_action(message.from_user, "Команда /start")
        kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text='Подобрать по ЕГЭ')], [KeyboardButton(text='Просмотреть направления')]], resize_keyboard=True)
        await message.answer("Выберите действие:", reply_markup=kb)
        await state.set_state(UserState.start_menu)
        log_bot_reply(message.from_user, "Главное меню")
    except Exception as e:
        log_error(f"start_command error: {e}")

@router.callback_query(lambda c: c.data.startswith('select_'), StateFilter(UserState.choosing_subjects))
async def select_subject(callback_query: types.CallbackQuery, state: FSMContext):
    try:
        subj = callback_query.data[7:]
        data = await state.get_data()
        selected = data.get('selected_subjects', [])
        if subj in selected:
            selected.remove(subj)
        else:
            selected.append(subj)
        await state.update_data(selected_subjects=selected)

        keyboard = InlineKeyboardMarkup(inline_keyboard=[])
        buttons = []
        for s in ALL_SUBJECTS:
            disp = KEY_TO_DISPLAY.get(s, s)
            text = f"{'✔ ' if s in selected else ''}{disp}"
            buttons.append(types.InlineKeyboardButton(text=text, callback_data=f'select_{s}'))
        buttons.append(types.InlineKeyboardButton(text='Подтвердить выбор', callback_data='confirm'))
        for i in range(0, len(buttons), 3):
            keyboard.inline_keyboard.append(buttons[i:i+3])
        try:
            await callback_query.message.edit_reply_markup(reply_markup=keyboard)
        except Exception:
            pass
        log_user_action(callback_query.from_user, f"Выбрал/снял предмет: {subj}")
    except Exception as e:
        log_error(f"select_subject error: {e}")

@router.callback_query(lambda c: c.data == 'confirm', StateFilter(UserState.choosing_subjects))
async def confirm_selection(callback_query: types.CallbackQuery, state: FSMContext):
    try:
        data = await state.get_data()
        selected = data.get('selected_subjects', [])
        if not selected:
            await callback_query.answer("Выберите хотя бы один предмет!")
            return
        await state.update_data(selected_subjects=selected, scores={})
        kb = InlineKeyboardMarkup(inline_keyboard=[])
        rows = []
        for subj in selected:
            rows.append([types.InlineKeyboardButton(text=f"Ввести {KEY_TO_DISPLAY.get(subj, subj)}", callback_data=f'enter_{subj}')])
        rows.append([types.InlineKeyboardButton(text='Готово', callback_data='done_scores')])
        kb.inline_keyboard = rows
        await callback_query.message.answer("Нажмите на предмет, чтобы ввести балл, или нажмите 'Готово' после ввода всех баллов:", reply_markup=kb)
        log_user_action(callback_query.from_user, f"Подтвердил выбор предметов: {selected}")
        try:
            await callback_query.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        await callback_query.answer()
        await state.set_state(UserState.entering_scores)
    except Exception as e:
        log_error(f"confirm_selection error: {e}")

@router.callback_query(lambda c: c.data == 'main_menu', StateFilter(UserState.choosing_subjects))
async def main_menu_callback(callback_query: types.CallbackQuery, state: FSMContext):
    try:
        await callback_query.answer()
        await state.clear()
        await start_command(callback_query.message, state)
    except Exception as e:
        log_error(f"main_menu_callback error: {e}")

@router.callback_query(lambda c: c.data.startswith('enter_'), StateFilter(UserState.entering_scores))
async def enter_score(callback_query: types.CallbackQuery, state: FSMContext):
    try:
        subj = callback_query.data[6:]
        await state.update_data(current_subject=subj)
        await callback_query.message.answer(f"Введите балл по предмету '{KEY_TO_DISPLAY.get(subj, subj)}' (целое число от 0 до 100):")
        log_user_action(callback_query.from_user, f"Начал ввод балла по предмету: {subj}")
        await callback_query.answer()
    except Exception as e:
        log_error(f"enter_score error: {e}")

@router.message(lambda message: message.text and message.text.isdigit(), StateFilter(UserState.entering_scores))
async def process_score(message: types.Message, state: FSMContext):
    try:
        score = int(message.text)
        if not 0 <= score <= 100:
            raise ValueError
    except Exception:
        await message.answer("Введите корректное целое число от 0 до 100!")
        log_user_action(message.from_user, f"Ввёл некорректный балл: {message.text}")
        return
    try:
        data = await state.get_data()
        subj = data.get('current_subject')
        if not subj:
            await message.answer("Сначала выберите предмет, нажав кнопку 'Ввести <предмет>'")
            return
        scores = data.get('scores', {})
        scores[subj] = score
        await state.update_data(scores=scores)
        log_user_action(message.from_user, f"Ввёл балл {score} по предмету {subj}")

        selected = data.get('selected_subjects', [])
        next_subj = None
        for s in selected:
            if s not in scores:
                next_subj = s
                break
        if next_subj:
            await message.answer(f"Балл сохранен. Нажмите кнопку 'Ввести {KEY_TO_DISPLAY.get(next_subj, next_subj)}' чтобы ввести следующий балл.")
            return
        matches = find_matching_vuz(scores)
        if not matches:
            await message.answer("Извините, по вашим баллам ничего не подошло.")
            log_system(f"Пользователь {message.from_user.id} не нашёл подходящих вузов")
            await state.clear()
            return
        await state.update_data(matches=matches, current_index=0, last_result_message_id=None)
        await state.set_state(UserState.viewing_results)
        await send_result(message.chat.id, state)
    except Exception as e:
        log_error(f"process_score error: {e}")

async def send_result(chat_id: int, state: FSMContext):
    try:
        data = await state.get_data()
        matches = data.get('matches') or data.get('filtered_directions') or []
        index = data.get('current_index', 0)
        if not matches:
            await bot.send_message(chat_id, "Нет результатов для отображения.")
            return
        if index < 0:
            index = 0
        if index >= len(matches):
            index = len(matches) - 1
        entry = matches[index]

        title = f"{entry.get('вуз', 'ВУЗ')} — {entry.get('специальность', '')}"
        desc_lines = []
        passing = entry.get('проходной_балл_сумма') or entry.get('Проходной_балл_сумма') or entry.get('проходной_балл')
        if passing:
            desc_lines.append(f"Проходной балл: {passing}")
        min_fields = {
            'математика': 'мин_математика',
            'русский': 'мин_русский',
            'информатика': 'мин_информатика',
            'физика': 'мин_физика',
            'биология': 'мин_биология',
            'химия': 'мин_химия',
            'иностранный_язык': 'мин_иностранный_язык',
            'география': 'мин_география',
            'литература': 'мин_литература',
            'история': 'мин_история',
            'общество': 'мин_общество'
        }
        for subj, field in min_fields.items():
            val = entry.get(field)
            if val:
                try:
                    iv = int(val)
                    if iv > 0:
                        desc_lines.append(f"{KEY_TO_DISPLAY.get(subj, subj)} — мин: {iv}")
                except:
                    pass
        for key in ('форма', 'срок', 'бюджет', 'платно', 'места'):
            if entry.get(key):
                desc_lines.append(f"{key}: {entry.get(key)}")
        desc = entry.get('описание')
        if desc:
            desc_lines.append('')
            desc_lines.append(desc[:800])
        text = f"<b>{title}</b>\n" + "\n".join(desc_lines)

        keyboard = InlineKeyboardMarkup(inline_keyboard=[])
        nav = []
        if index > 0:
            nav.append(types.InlineKeyboardButton(text='⬅️ Назад', callback_data='prev'))
        if index < len(matches) - 1:
            nav.append(types.InlineKeyboardButton(text='Далее ➡️', callback_data='next'))
        if nav:
            keyboard.inline_keyboard.append(nav)

        link_buttons = []
        url_univ = entry.get('ссылка_вуз') or entry.get('сайт')
        url_spec = entry.get('ссылка_специальность') or entry.get('ссылка')
        if url_univ:
            link_buttons.append(types.InlineKeyboardButton(text='Сайт вуза', url=url_univ if url_univ.startswith('http') else f'https://{url_univ}'))
        if url_spec:
            link_buttons.append(types.InlineKeyboardButton(text='Страница специальности', url=url_spec))
        if link_buttons:
            keyboard.inline_keyboard.append(link_buttons)
        keyboard.inline_keyboard.append([types.InlineKeyboardButton(text='Завершить', callback_data='finish')])

        result_msg = None
        photo = entry.get('фото_специальности')
        try:
            if photo:
                try:
                    result_msg = await bot.send_photo(chat_id, photo, caption=text, parse_mode='HTML', reply_markup=keyboard)
                except Exception:
                    result_msg = await bot.send_message(chat_id, text + (f"\n\nФото: {photo}" if photo else ''), parse_mode='HTML', reply_markup=keyboard)
            else:
                result_msg = await bot.send_message(chat_id, text, parse_mode='HTML', reply_markup=keyboard)
        except Exception as e:
            log_error(f"Не удалось отправить результат: {e}")
            return

        last_id = data.get('last_result_message_id')
        try:
            if last_id and last_id != result_msg.message_id:
                await bot.delete_message(chat_id, last_id)
        except Exception:
            pass

        await state.update_data(last_result_message_id=result_msg.message_id, current_index=index)
        log_system(f"Пользователь {chat_id} просмотрел результат {index+1}/{len(matches)}")
    except Exception as e:
        log_error(f"send_result error: {e}")

@router.callback_query(lambda c: c.data in ['prev', 'next'], StateFilter(UserState.viewing_results))
async def navigate_results(callback_query: types.CallbackQuery, state: FSMContext):
    try:
        data = await state.get_data()
        index = data.get('current_index', 0)
        if callback_query.data == 'prev':
            index = max(0, index - 1)
        elif callback_query.data == 'next':
            matches = data.get('matches') or data.get('filtered_directions') or []
            index = min(len(matches) - 1 if matches else 0, index + 1)
        await state.update_data(current_index=index)
        await callback_query.answer()
        await send_result(callback_query.message.chat.id, state)
        log_user_action(callback_query.from_user, f"Переключил результат на {index+1}")
    except Exception as e:
        log_error(f"navigate_results error: {e}")

@router.callback_query(lambda c: c.data == 'finish', StateFilter(UserState.viewing_results))
async def finish_results(callback_query: types.CallbackQuery, state: FSMContext):
    try:
        await state.clear()
        await callback_query.message.answer("Спасибо — сессия завершена.")
        await callback_query.answer()
        log_user_action(callback_query.from_user, "Завершил сессию")
    except Exception as e:
        log_error(f"finish_results error: {e}")

@router.message(lambda m: m.text == 'Подобрать по ЕГЭ', StateFilter(UserState.start_menu))
async def menu_pick_by_scores(message: types.Message, state: FSMContext):
    await state.clear()
    keyboard = InlineKeyboardMarkup(inline_keyboard=[])
    buttons = []
    for subj in ALL_SUBJECTS:
        disp = KEY_TO_DISPLAY.get(subj, subj)
        buttons.append(types.InlineKeyboardButton(text=disp, callback_data=f'select_{subj}'))
    buttons.append(types.InlineKeyboardButton(text='Подтвердить выбор', callback_data='confirm'))
    buttons.append(types.InlineKeyboardButton(text='Главное меню', callback_data='main_menu'))
    for i in range(0, len(buttons), 3):
        keyboard.inline_keyboard.append(buttons[i:i+3])
    await message.answer('Выберите предметы, которые вы сдавали (нажмите по каждому):', reply_markup=keyboard)
    await state.set_state(UserState.choosing_subjects)
    log_user_action(message.from_user, "Начал подбор по ЕГЭ")

@router.message(lambda m: m.text == 'Просмотреть направления', StateFilter(UserState.start_menu))
async def menu_view_directions(message: types.Message, state: FSMContext):
    await state.clear()
    univs = []
    for e in VUZ_DATA:
        name = e.get('вуз', e.get('Вуз', ''))
        if name and name not in univs:
            univs.append(name)
    keyboard_rows = [[KeyboardButton(text=u)] for u in univs]
    keyboard_rows.append([KeyboardButton(text='Главное меню')])
    kb = ReplyKeyboardMarkup(keyboard=keyboard_rows, resize_keyboard=True)
    await message.answer('Выберите вуз:', reply_markup=kb)
    await state.set_state(UserState.choosing_university)
    log_user_action(message.from_user, "Начал просмотр направлений")

@router.message(StateFilter(UserState.choosing_university))
async def choose_university(message: types.Message, state: FSMContext):
    text = message.text.strip()
    if text == 'Главное меню':
        await start_command(message, state)
        return
    filtered = [e for e in VUZ_DATA if (e.get('вуз', e.get('Вуз', '')).strip().lower() == text.lower())]
    if not filtered:
        await message.answer('Не найдено направлений для выбранного вуза. Выберите другой вуз или главное меню.')
        log_user_action(message.from_user, f"Выбрал несуществующий вуз: {text}")
        return
    await state.update_data(filtered_directions=filtered, current_index=0, last_result_message_id=None)
    await send_result(message.chat.id, state)
    await state.set_state(UserState.viewing_results)
    log_user_action(message.from_user, f"Выбрал вуз: {text}")

@router.message(StateFilter(UserState.entering_scores))
async def handle_non_text(message: types.Message, state: FSMContext):
    if not message.text:
        await message.answer("Пожалуйста, отправьте число от 0 до 100 в виде текста.")
        log_user_action(message.from_user, f"Отправил не-текст при вводе баллов: {message.content_type}")

@router.message()
async def handle_any_non_text(message: types.Message):
    if not message.text:
        await message.answer("Пожалуйста, используйте текстовые команды или кнопки.")
        log_user_action(message.from_user, f"Отправил не-текст вне диалога: {message.content_type}")

# --- ОБРАБОТКА ОШИБОК AIOGRAM ---
@dp.error()
async def errors_handler(event: types.ErrorEvent):
    log_error(f"Ошибка aiogram: {event.exception}")

# ---------- Healthcheck HTTP-сервер для Render ----------
async def run_health_server():
    app = web.Application()
    async def health_handler(request):
        return web.Response(text="OK", status=200)
    app.router.add_get('/{tail:.*}', health_handler)
    app.router.add_post('/{tail:.*}', health_handler)
    port = int(os.getenv('PORT', 10000))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    log_system(f"Healthcheck сервер запущен на порту {port}")
    await asyncio.Event().wait()

# ---------------------------------------------------------

if __name__ == '__main__':
    log_system("Инициализация систем...")
    print(f"{Fore.CYAN}================================================={Style.RESET_ALL}")
    print(f"{Fore.CYAN}   VUZ BOT ЗАПУЩЕН И СМОТРИТ В ЛОГИ              {Style.RESET_ALL}")
    print(f"{Fore.CYAN}================================================={Style.RESET_ALL}")

    async def _runner():
        asyncio.create_task(run_health_server())
        backoff = 1
        while True:
            try:
                log_system("Запуск polling...")
                await dp.start_polling(bot)
                log_system("Polling завершился корректно, перезапуск через 1 сек.")
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log_error(f"Ошибка запуска/прохождения polling: {e}")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

    try:
        asyncio.run(_runner())
    except Exception as e:
        log_error(f"Фатальная ошибка: {e}")
