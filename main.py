import asyncio
import json
import logging
import os
from aiogram import Bot, Dispatcher, Router, types
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from aiogram.filters import CommandStart, StateFilter
from colorama import init, Fore, Style

# Для healthcheck-сервера
from aiohttp import web

# Инициализация colorama для цветного логирования
init(autoreset=True)

# Настройка логирования

# Цвета для разных событий
START_COLOR = Fore.CYAN
USER_COLOR = Fore.MAGENTA
QUESTION_COLOR = Fore.YELLOW
RESULT_COLOR = Fore.GREEN
ERROR_COLOR = Fore.RED

class ColorFormatter(logging.Formatter):
    def format(self, record):
        msg = super().format(record)
        if hasattr(record, 'color'):
            return f"{record.color}{msg}{Style.RESET_ALL}"
        return msg

formatter = ColorFormatter('%(asctime)s - %(levelname)s - %(message)s')
file_handler = logging.FileHandler('bot.log', encoding='utf-8')
file_handler.setFormatter(formatter)
stream_handler = logging.StreamHandler()
stream_handler.setFormatter(formatter)
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logger.handlers = [file_handler, stream_handler]

# Токен бота (читается из переменной окружения `BOT_TOKEN`)
TOKEN = os.getenv('BOT_TOKEN')

# Загрузка данных из JSON с обработкой ошибок и валидацией
VUZ_DATA = []
try:
    with open('vuz_data.json', 'r', encoding='utf-8') as f:
        raw = json.load(f)
        # Нормализуем и валидируем записи
        for i, entry in enumerate(raw):
            if not isinstance(entry, dict):
                logger.warning(f"Запись {i} не является объектом, пропущена", extra={'color': ERROR_COLOR})
                continue
            # Привести ключи к нижнему регистру
            norm = {k.lower(): v for k, v in entry.items()}
            # Проверить обязательные поля
            if 'вуз' not in norm or 'специальность' not in norm or 'предметы_список' not in norm:
                logger.warning(f"Запись {i} не содержит обязательных полей (вуз/специальность/предметы_список)", extra={'color': ERROR_COLOR})
                continue
            # Конвертировать числовые поля
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
except Exception as e:
    logger.error(f"Ошибка загрузки vuz_data.json: {e}", extra={'color': ERROR_COLOR})

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
if not TOKEN or TOKEN.strip() == '':
    logger.error("BOT_TOKEN не задан — установите переменную окружения BOT_TOKEN", extra={'color': ERROR_COLOR})
    raise SystemExit(1)

bot = Bot(token=TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
router = Router()
dp.include_router(router)

# Функция для парсинга предметов из строки
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

# Маппинг отображаемых имён предметов -> внутренние ключи
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

DISPLAY_TO_KEY = {k: v for k, v in SUBJECT_DISPLAY.items()}
KEY_TO_DISPLAY = {v: k for k, v in SUBJECT_DISPLAY.items()}

# Функция для проверки соответствия баллов
def check_fit(user_scores, entry):
    try:
        subjects_str = entry.get('предметы_список') or entry.get('Предметы_список')
        required, alternatives = parse_subjects(subjects_str)
        # Проверить обязательные предметы
        for req in required:
            if req not in user_scores:
                return False
        # Проверить альтернативы (хотя бы один из группы)
        for alt_group in alternatives:
            has_one = False
            for alt in alt_group:
                if alt in user_scores:
                    has_one = True
                    break
            if not has_one:
                return False
        # Проверить минимальные баллы
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
        logger.error(f"Ошибка проверки соответствия: {e}", extra={'color': ERROR_COLOR})
        return False

# Функция для фильтрации подходящих вузов
def find_matching_vuz(user_scores):
    matches = []
    for entry in VUZ_DATA:
        if check_fit(user_scores, entry):
            matches.append(entry)
    return matches

# Команда /start — главное меню
@router.message(CommandStart(), StateFilter('*'))
async def start_command(message: types.Message, state: FSMContext):
    try:
        await state.clear()
        logger.info(f"Пользователь {message.from_user.id} ({message.from_user.full_name}) начал работу с ботом", extra={'color': START_COLOR})
        kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text='Подобрать по ЕГЭ')], [KeyboardButton(text='Просмотреть направления')]], resize_keyboard=True)
        await message.answer("Выберите действие:", reply_markup=kb)
        await state.set_state(UserState.start_menu)
    except Exception as e:
        logger.exception(f"start_command error for user {getattr(message.from_user, 'id', 'N/A')}: {e}", extra={'color': ERROR_COLOR})
        return

# Обработка выбора предметов
@router.callback_query(lambda c: c.data.startswith('select_'), StateFilter(UserState.choosing_subjects))
async def select_subject(callback_query: types.CallbackQuery, state: FSMContext):
    try:
        subj = callback_query.data[7:]  # Убрать 'select_'
        data = await state.get_data()
        selected = data.get('selected_subjects', [])

        if subj in selected:
            selected.remove(subj)
        else:
            selected.append(subj)

        await state.update_data(selected_subjects=selected)

        # Построим клавиатуру с отображаемыми именами предметов
        keyboard = InlineKeyboardMarkup(inline_keyboard=[])
        buttons = []
        for s in ALL_SUBJECTS:
            disp = KEY_TO_DISPLAY.get(s, s)
            text = f"{'✔ ' if s in selected else ''}{disp}"
            buttons.append(types.InlineKeyboardButton(text=text, callback_data=f'select_{s}'))
        # Кнопка подтверждения
        buttons.append(types.InlineKeyboardButton(text='Подтвердить выбор', callback_data='confirm'))
        # Разделим по рядам по 3
        for i in range(0, len(buttons), 3):
            keyboard.inline_keyboard.append(buttons[i:i+3])

        try:
            await callback_query.message.edit_reply_markup(reply_markup=keyboard)
        except Exception as e:
            logger.warning(f"Не удалось обновить клавиатуру: {e}", extra={'color': ERROR_COLOR})
        logger.info(f"Пользователь {callback_query.from_user.id} ({callback_query.from_user.full_name}) выбрал/снял предмет: {subj}", extra={'color': USER_COLOR})
    except Exception as e:
        logger.exception(f"select_subject error: {e}", extra={'color': ERROR_COLOR})
        return

# Подтверждение выбора
@router.callback_query(lambda c: c.data == 'confirm', StateFilter(UserState.choosing_subjects))
async def confirm_selection(callback_query: types.CallbackQuery, state: FSMContext):
    try:
        data = await state.get_data()
        selected = data.get('selected_subjects', [])
        if not selected:
            await callback_query.answer("Выберите хотя бы один предмет!")
            return
        await state.update_data(selected_subjects=selected, scores={})
        # Построим inline-кнопки для ввода баллов по каждому выбранному предмету
        kb = InlineKeyboardMarkup(inline_keyboard=[])
        rows = []
        for subj in selected:
            rows.append([types.InlineKeyboardButton(text=f"Ввести {KEY_TO_DISPLAY.get(subj, subj)}", callback_data=f'enter_{subj}')])
        # Кнопка Готово для завершения ввода
        rows.append([types.InlineKeyboardButton(text='Готово', callback_data='done_scores')])
        kb.inline_keyboard = rows

        await callback_query.message.answer("Нажмите на предмет, чтобы ввести балл, или нажмите 'Готово' после ввода всех баллов:", reply_markup=kb)
        logger.info(f"Пользователь {callback_query.from_user.id} ({callback_query.from_user.full_name}) подтвердил выбор предметов: {selected}", extra={'color': QUESTION_COLOR})
        # Убрать клавиатуру выбора предметов на предыдущем сообщении
        try:
            await callback_query.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        await callback_query.answer()
        await state.set_state(UserState.entering_scores)
    except Exception as e:
        logger.exception(f"confirm_selection error: {e}", extra={'color': ERROR_COLOR})
        return

# Обработчик возврата в главное меню через inline-кнопку
@router.callback_query(lambda c: c.data == 'main_menu', StateFilter(UserState.choosing_subjects))
async def main_menu_callback(callback_query: types.CallbackQuery, state: FSMContext):
    try:
        await callback_query.answer()
        await state.clear()
        # Вызовем стартовую команду, используя сообщение от callback
        await start_command(callback_query.message, state)
    except Exception as e:
        logger.exception(f"main_menu_callback error: {e}", extra={'color': ERROR_COLOR})
        return

# Ввод баллов (нажатие на кнопку предмета)
@router.callback_query(lambda c: c.data.startswith('enter_'), StateFilter(UserState.entering_scores))
async def enter_score(callback_query: types.CallbackQuery, state: FSMContext):
    try:
        subj = callback_query.data[6:]  # Убрать 'enter_'
        await state.update_data(current_subject=subj)
        # Запросим у пользователя числовой ввод
        await callback_query.message.answer(f"Введите балл по предмету '{KEY_TO_DISPLAY.get(subj, subj)}' (целое число от 0 до 100):")
        logger.info(f"Пользователь {callback_query.from_user.id} ({callback_query.from_user.full_name}) начал ввод балла по предмету: {subj}", extra={'color': QUESTION_COLOR})
        await callback_query.answer()
    except Exception as e:
        logger.exception(f"enter_score error: {e}", extra={'color': ERROR_COLOR})
        return

# Обработка ввода балла
@router.message(lambda message: message.text and message.text.isdigit(), StateFilter(UserState.entering_scores))
async def process_score(message: types.Message, state: FSMContext):
    try:
        # Проверка: только числа от 0 до 100
        score = int(message.text)
        if not 0 <= score <= 100:
            raise ValueError
    except Exception:
        await message.answer("Введите корректное целое число от 0 до 100!")
        logger.info(f"Пользователь {message.from_user.id} ({message.from_user.full_name}) ввел некорректный балл: {message.text}", extra={'color': ERROR_COLOR})
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
        logger.info(f"Пользователь {message.from_user.id} ({message.from_user.full_name}) ввел балл {score} по предмету {subj}", extra={'color': USER_COLOR})

        selected = data.get('selected_subjects', [])
        # Найти следующий предмет без балла
        next_subj = None
        for s in selected:
            if s not in scores:
                next_subj = s
                break
        if next_subj:
            await message.answer(f"Балл сохранен. Нажмите кнопку 'Ввести {KEY_TO_DISPLAY.get(next_subj, next_subj)}' чтобы ввести следующий балл.")
            return
        # Все баллы введены — расчёт
        matches = find_matching_vuz(scores)
        if not matches:
            await message.answer("Извините, по вашим баллам ничего не подошло.")
            logger.info(f"Пользователь {message.from_user.id} ({message.from_user.full_name}) не найдено подходящих вузов", extra={'color': RESULT_COLOR})
            await state.clear()
            return
        await state.update_data(matches=matches, current_index=0, last_result_message_id=None)
        await state.set_state(UserState.viewing_results)
        await send_result(message.chat.id, state)
    except Exception as e:
        logger.exception(f"process_score error: {e}", extra={'color': ERROR_COLOR})
        return

# Показ результата
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

        # Сформировать текст карточки: показать ВУЗ, специальность, проходной балл и мин. баллы по предметам
        title = f"{entry.get('вуз', 'ВУЗ')} — {entry.get('специальность', '')}"
        desc_lines = []
        # Проходной балл (сумма)
        passing = entry.get('проходной_балл_сумма') or entry.get('Проходной_балл_сумма') or entry.get('проходной_балл')
        if passing:
            desc_lines.append(f"Проходной балл: {passing}")
        # Минимальные баллы по предметам
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
            if val is None:
                continue
            try:
                iv = int(val)
            except Exception:
                continue
            if iv > 0:
                desc_lines.append(f"{KEY_TO_DISPLAY.get(subj, subj)} — мин: {iv}")
        # Доп. информация (коротко)
        for key in ('форма', 'срок', 'бюджет', 'платно', 'места'):
            if entry.get(key):
                desc_lines.append(f"{key}: {entry.get(key)}")

        # Полное описание, если есть (ограничим длину)
        desc = entry.get('описание')
        if desc:
            desc_lines.append('')
            desc_lines.append(desc[:800])

        text = f"<b>{title}</b>\n" + "\n".join(desc_lines)

        # Клавиатура навигации
        keyboard = InlineKeyboardMarkup(inline_keyboard=[])
        nav = []
        if index > 0:
            nav.append(types.InlineKeyboardButton(text='⬅️ Назад', callback_data='prev'))
        if index < len(matches) - 1:
            nav.append(types.InlineKeyboardButton(text='Далее ➡️', callback_data='next'))
        if nav:
            keyboard.inline_keyboard.append(nav)

        # Кнопки-ссылки
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

        # Сначала попытаемся отправить новую карточку — если отправка провалится, старое сообщение останется
        result_msg = None
        photo = entry.get('фото_специальности')
        try:
            if photo:
                try:
                    result_msg = await bot.send_photo(chat_id, photo, caption=text, parse_mode='HTML', reply_markup=keyboard)
                except Exception:
                    # fallback: отправить текст и ссылку на фото
                    result_msg = await bot.send_message(chat_id, text + (f"\n\nФото: {photo}" if photo else ''), parse_mode='HTML', reply_markup=keyboard)
            else:
                result_msg = await bot.send_message(chat_id, text, parse_mode='HTML', reply_markup=keyboard)
        except Exception as e:
            logger.warning(f"Не удалось отправить результат (везде): {e}", extra={'color': ERROR_COLOR})
            return

        # Удалить предыдущее сообщение результата (если было)
        last_id = data.get('last_result_message_id')
        try:
            if last_id and last_id != result_msg.message_id:
                await bot.delete_message(chat_id, last_id)
        except Exception:
            pass

        await state.update_data(last_result_message_id=result_msg.message_id, current_index=index)
        logger.info(f"Пользователь {chat_id} просмотрел результат {index+1}/{len(matches)}", extra={'color': RESULT_COLOR})
    except Exception as e:
        logger.exception(f"send_result error: {e}", extra={'color': ERROR_COLOR})
        return

# Навигация по результатам
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
        logger.info(f"Пользователь {callback_query.from_user.id} ({callback_query.from_user.full_name}) переключил результат на {index+1}", extra={'color': RESULT_COLOR})
    except Exception as e:
        logger.exception(f"navigate_results error: {e}", extra={'color': ERROR_COLOR})
        return

@router.callback_query(lambda c: c.data == 'finish', StateFilter(UserState.viewing_results))
async def finish_results(callback_query: types.CallbackQuery, state: FSMContext):
    try:
        await state.clear()
        await callback_query.message.answer("Спасибо — сессия завершена.")
        await callback_query.answer()
    except Exception as e:
        logger.exception(f"finish_results error: {e}", extra={'color': ERROR_COLOR})
        return

# Обработка главного меню (reply-клавиатура)
@router.message(lambda m: m.text == 'Подобрать по ЕГЭ', StateFilter(UserState.start_menu))
async def menu_pick_by_scores(message: types.Message, state: FSMContext):
    # Перевести в выбор предметов через inline-клавиатуру (callback'ы select_/confirm)
    await state.clear()
    keyboard = InlineKeyboardMarkup(inline_keyboard=[])
    buttons = []
    for subj in ALL_SUBJECTS:
        disp = KEY_TO_DISPLAY.get(subj, subj)
        buttons.append(types.InlineKeyboardButton(text=disp, callback_data=f'select_{subj}'))
    # Добавим кнопку подтверждения и возврата в главное меню
    buttons.append(types.InlineKeyboardButton(text='Подтвердить выбор', callback_data='confirm'))
    buttons.append(types.InlineKeyboardButton(text='Главное меню', callback_data='main_menu'))
    for i in range(0, len(buttons), 3):
        keyboard.inline_keyboard.append(buttons[i:i+3])
    await message.answer('Выберите предметы, которые вы сдавали (нажмите по каждому):', reply_markup=keyboard)
    await state.set_state(UserState.choosing_subjects)

@router.message(lambda m: m.text == 'Просмотреть направления', StateFilter(UserState.start_menu))
async def menu_view_directions(message: types.Message, state: FSMContext):
    # Показать список вузов (reply keyboard)
    await state.clear()
    # собрать уникальные названия вузов
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

# Обработка выбора вуза
@router.message(StateFilter(UserState.choosing_university))
async def choose_university(message: types.Message, state: FSMContext):
    text = message.text.strip()
    if text == 'Главное меню':
        await start_command(message, state)
        return
    # отфильтровать направления по вузу
    filtered = [e for e in VUZ_DATA if (e.get('вуз', e.get('Вуз', '')).strip().lower() == text.lower())]
    if not filtered:
        await message.answer('Не найдено направлений для выбранного вуза. Выберите другой вуз или главное меню.')
        return
    await state.update_data(filtered_directions=filtered, current_index=0, last_result_message_id=None)
    await send_result(message.chat.id, state)
    await state.set_state(UserState.viewing_results)

# Обработка не-текстовых сообщений (фото, видео, гиф, документы и т.д.)
@router.message(StateFilter(UserState.entering_scores))
async def handle_non_text(message: types.Message, state: FSMContext):
    if not message.text:
        await message.answer("Пожалуйста, отправьте число от 0 до 100 в виде текста.")
        logger.info(f"Пользователь {message.from_user.id} ({message.from_user.full_name}) отправил не-текстовое сообщение (тип: {message.content_type})", extra={'color': ERROR_COLOR})

# Глобальная обработка не-текстовых сообщений вне ввода баллов
@router.message()
async def handle_any_non_text(message: types.Message):
    if not message.text:
        await message.answer("Пожалуйста, используйте текстовые команды или кнопки.")
        logger.info(f"Пользователь {message.from_user.id} ({message.from_user.full_name}) отправил не-текстовое сообщение вне ожидания баллов (тип: {message.content_type})", extra={'color': ERROR_COLOR})

# Обработка ошибок
@dp.error()
async def errors_handler(*args, **kwargs):
    # aiogram может вызывать обработчик с разными сигнатурами; попробуем извлечь exception
    exception = kwargs.get('exception')
    if not exception:
        if len(args) >= 2:
            exception = args[1]
        elif len(args) == 1:
            exception = args[0]
    if exception:
        try:
            logger.exception(f"Ошибка: {exception}", extra={'color': ERROR_COLOR})
        except Exception:
            try:
                logger.error(f"Ошибка: {exception}", extra={'color': ERROR_COLOR})
            except Exception:
                pass
    else:
        try:
            logger.error(f"errors_handler вызван без exception; args={args} kwargs={kwargs}", extra={'color': ERROR_COLOR})
        except Exception:
            pass
    return True

# ---------- Healthcheck HTTP-сервер для Render ----------
async def run_health_server():
    """Запускает простой HTTP-сервер, который отвечает 200 OK на любой запрос.
    Render ожидает, что сервис слушает порт, указанный в переменной окружения PORT."""
    app = web.Application()
    # Обработчик для любого пути
    async def health_handler(request):
        return web.Response(text="OK", status=200)
    app.router.add_get('/{tail:.*}', health_handler)
    app.router.add_post('/{tail:.*}', health_handler)
    # Получаем порт из переменной окружения (Render передаёт её автоматически)
    port = int(os.getenv('PORT', 10000))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    logger.info(f"Healthcheck сервер запущен на порту {port}", extra={'color': START_COLOR})
    # Бесконечно ждём, чтобы сервер не завершился (все запросы обрабатываются в фоне)
    await asyncio.Event().wait()

# ---------------------------------------------------------

if __name__ == '__main__':
    logger.info("Бот запущен", extra={'color': START_COLOR})
    async def _runner():
        # Запускаем healthcheck-сервер в фоне
        asyncio.create_task(run_health_server())
        
        backoff = 1
        while True:
            try:
                logger.info("Запуск polling...", extra={'color': START_COLOR})
                await dp.start_polling(bot)
                logger.info("Polling завершился корректно, перезапуск через 1 сек.", extra={'color': START_COLOR})
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Ошибка запуска/прохождения polling: {e}", extra={'color': ERROR_COLOR})
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

    try:
        asyncio.run(_runner())
    except Exception as e:
        logger.error(f"Фатальная ошибка: {e}", extra={'color': ERROR_COLOR})
