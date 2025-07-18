import sqlite3
import asyncio
import schedule
import logging
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardMarkup, KeyboardButton
import re
import openpyxl

# Настройка логирования в файл donor_bot.txt
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('donor_bot.txt'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Токен бота
TOKEN = "7893139526:AAEw3mRwp8btOI4HWWhbLzL0j48kaQBUa50"
bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# Функция инициализации базы данных
def init_db():
    try:
        with sqlite3.connect('donor_bot.db', timeout=10) as conn:
            cursor = conn.cursor()
            # Таблица users адаптирована под Excel: без student_id, blood_group, medical_exemption
            cursor.execute('''CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER UNIQUE,
                phone TEXT,
                name TEXT,
                surname TEXT,
                category TEXT,
                user_group TEXT,
                social_contacts TEXT,
                dkm BOOLEAN DEFAULT 0,
                consent BOOLEAN DEFAULT 0,
                profile_status TEXT DEFAULT 'pending'
            )''')
            cursor.execute('''CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT,
                time TEXT,
                location TEXT,
                description TEXT,
                capacity INTEGER,
                status TEXT DEFAULT 'active'
            )''')
            cursor.execute('''CREATE TABLE IF NOT EXISTS registrations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                event_id INTEGER,
                status TEXT DEFAULT 'registered',
                attended BOOLEAN DEFAULT 0,
                FOREIGN KEY (user_id) REFERENCES users(id),
                FOREIGN KEY (event_id) REFERENCES events(id)
            )''')
            cursor.execute('''CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                event_id INTEGER,
                reminder_date TEXT,
                FOREIGN KEY (user_id) REFERENCES users(id),
                FOREIGN KEY (event_id) REFERENCES events(id)
            )''')
            cursor.execute('''CREATE TABLE IF NOT EXISTS donations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                date TEXT,
                center TEXT,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )''')
            cursor.execute('''CREATE TABLE IF NOT EXISTS non_attendance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                registration_id INTEGER,
                reason TEXT,
                FOREIGN KEY (registration_id) REFERENCES registrations(id)
            )''')
            cursor.execute('''CREATE TABLE IF NOT EXISTS info_sections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                section_name TEXT UNIQUE,
                text TEXT
            )''')
            # Инициализация дефолтных информационных разделов
            default_sections = [
                ('О донорстве крови', 'Требования к донорам: ... (текст из ТЗ). Подготовка: ... Противопоказания: ...'),
                ('О донорстве костного мозга', 'Важность: ... Процедура: ...'),
                ('О донациях в МИФИ', 'Пошаговое описание: ... Ближайший ДД: ...')
            ]
            for name, text in default_sections:
                cursor.execute('INSERT OR IGNORE INTO info_sections (section_name, text) VALUES (?, ?)', (name, text))
            conn.commit()
            logger.info("База данных успешно инициализирована")
    except sqlite3.Error as e:
        logger.error(f"Ошибка инициализации базы данных: {e}")
        raise

# Функция импорта данных из Excel (адаптировано: без student_id и т.п., добавлены social_contacts, dkm=0 по умолчанию)
def import_from_excel():
    try:
        logger.info("Начало импорта данных из Excel")
        wb = openpyxl.load_workbook('База ДД (1).xlsx')
        sheet = wb.active
        with sqlite3.connect('donor_bot.db', timeout=10) as conn:
            cursor = conn.cursor()
            # Проверяем, пуста ли таблица users
            cursor.execute('SELECT COUNT(*) FROM users')
            if cursor.fetchone()[0] > 0:
                logger.info("Таблица users не пуста, пропуск импорта")
                return
            imported_count = 0
            skipped_count = 0
            for row in sheet.iter_rows(min_row=2, values_only=True):
                fio = str(row[0]).strip() if row[0] else ''
                if not fio:
                    logger.warning(f"Пропущена строка {row}: пустое ФИО")
                    skipped_count += 1
                    continue
                # Разбиваем ФИО на фамилию и имя
                parts = fio.split(maxsplit=1)
                surname = parts[0] if parts else ''
                name = parts[1] if len(parts) > 1 else ''
                user_group = str(row[1]).strip() if row[1] else ''
                # Определяем категорию
                category = ('сотрудник' if 'сотрудник' in user_group.lower() or 'инженер' in user_group.lower()
                            else 'студент' if re.match(r'^[А-Я]\d{2}-\d{3}$', user_group)
                            else 'внешний')
                social_contacts = str(row[7]).strip() if row[7] else None
                phone = str(row[8]).strip() if row[8] else None
                if not phone:
                    logger.warning(f"Пропущена строка для ФИО {fio}: отсутствует номер телефона")
                    skipped_count += 1
                    continue
                try:
                    cursor.execute('''INSERT OR REPLACE INTO users 
                        (phone, name, surname, category, user_group, social_contacts, profile_status)
                        VALUES (?, ?, ?, ?, ?, ?, 'approved')''',
                        (phone, name, surname, category, user_group, social_contacts))
                    user_id = cursor.lastrowid
                    # Импорт донаций (кол-во -> добавить записи, но поскольку дат много, добавим placeholder даты)
                    count_gavrilov = int(row[2]) if row[2] else 0
                    count_fmba = int(row[3]) if row[3] else 0
                    last_gavrilov = row[5] if row[5] else None
                    last_fmba = row[6] if row[6] else None
                    for _ in range(count_gavrilov):
                        cursor.execute('INSERT INTO donations (user_id, date, center) VALUES (?, ?, ?)',
                                       (user_id, last_gavrilov or 'unknown', 'Гаврилова'))
                    for _ in range(count_fmba):
                        cursor.execute('INSERT INTO donations (user_id, date, center) VALUES (?, ?, ?)',
                                       (user_id, last_fmba or 'unknown', 'ФМБА'))
                    imported_count += 1
                    logger.info(f"Импортирован пользователь: {name} {surname}, телефон: {phone}, категория: {category}, группа: {user_group}")
                except sqlite3.Error as e:
                    logger.error(f"Ошибка при вставке пользователя {name} {surname} (телефон: {phone}): {e}")
                    skipped_count += 1
            conn.commit()
            logger.info(f"Импорт завершен: импортировано {imported_count} записей, пропущено {skipped_count} записей")
    except FileNotFoundError:
        logger.error("Файл 'База ДД (1).xlsx' не найден")
        raise
    except Exception as e:
        logger.error(f"Ошибка импорта Excel: {e}")
        raise

# Классы состояний (адаптировано: убраны blood_group, medical_exemption, student_id)
class ProfilRegStates(StatesGroup):
    phone_confirm = State()
    name = State()
    surname = State()
    category = State()
    group = State()
    social_contacts = State()  # Добавлено для соцсетей (optional)

class ConsentStates(StatesGroup):
    consent = State()

class ProfilEditStates(StatesGroup):
    field = State()
    value = State()

class AddEventStates(StatesGroup):
    date = State()
    time = State()
    location = State()
    description = State()
    capacity = State()

class EditInfoStates(StatesGroup):
    section = State()
    text = State()

def is_admin(user_id):
    admins = [123456789, 1653833795]
    return user_id in admins

# --- Команды пользователя ---

@dp.message(Command(commands=['start']))
async def start_handler(message: types.Message, state: FSMContext):
    logger.info(f"Пользователь {message.from_user.id} вызвал команду /start")
    keyboard = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="Поделиться номером телефона", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True
    )
    await message.answer("Привет! Я бот донорского центра МИФИ. Поделитесь номером телефона для авторизации.", reply_markup=keyboard)
    await state.set_state(ProfilRegStates.phone_confirm)

@dp.message(ProfilRegStates.phone_confirm)
async def process_phone(message: types.Message, state: FSMContext):
    if not message.contact:
        await message.answer("Пожалуйста, используйте кнопку для отправки контакта.")
        logger.warning(f"Пользователь {message.from_user.id} не отправил контакт")
        return
    phone = message.contact.phone_number
    try:
        with sqlite3.connect('donor_bot.db', timeout=10) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM users WHERE phone = ?', (phone,))
            user = cursor.fetchone()
        await state.update_data(phone=phone)
        if user:
            await state.update_data(name=user[3], surname=user[2], category=user[5], group=user[6], social_contacts=user[7])
            response = f"Вы уже в базе: {user[3]} {user[2]}, категория: {user[5]}, группа: {user[6]}.\nПодтверждаете? (Да/Нет)"
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="Да", callback_data="confirm_yes")
            keyboard.button(text="Нет", callback_data="confirm_no")
            await message.answer(response, reply_markup=keyboard.as_markup())
            logger.info(f"Найден существующий пользователь с телефоном {phone}")
        else:
            await profil_reg_handler(message, state)
    except sqlite3.Error as e:
        logger.error(f"Ошибка при проверке телефона {phone}: {e}")
        await message.answer("Произошла ошибка при проверке телефона. Попробуйте позже.")

@dp.callback_query(lambda c: c.data in ['confirm_yes', 'confirm_no'])
async def confirm_existing(callback_query: types.CallbackQuery, state: FSMContext):
    if callback_query.data == 'confirm_yes':
        data = await state.get_data()
        phone = data['phone']
        try:
            with sqlite3.connect('donor_bot.db', timeout=10) as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT consent FROM users WHERE phone = ?', (phone,))
                consent = cursor.fetchone()[0]
            if not consent:
                await state.set_state(ConsentStates.consent)
                keyboard = InlineKeyboardBuilder()
                keyboard.button(text="Согласен", callback_data="consent_yes")
                keyboard.button(text="Нет", callback_data="consent_no")
                await callback_query.message.answer("Примите условия: согласие на обработку ПДн и рассылки.",
                                                    reply_markup=keyboard.as_markup())
                logger.info(f"Пользователь {phone} должен подтвердить согласие")
            else:
                await callback_query.message.answer("Авторизация успешна!")
                await state.clear()
                logger.info(f"Успешная авторизация пользователя {phone}")
        except sqlite3.Error as e:
            logger.error(f"Ошибка при проверке согласия для телефона {phone}: {e}")
            await callback_query.message.answer("Произошла ошибка. Попробуйте позже.")
    else:
        await profil_reg_handler(callback_query.message, state)
    await callback_query.answer()

@dp.callback_query(lambda c: c.data in ['consent_yes', 'consent_no'])
async def process_consent(callback_query: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    phone = data['phone']
    try:
        with sqlite3.connect('donor_bot.db', timeout=10) as conn:
            cursor = conn.cursor()
            if callback_query.data == 'consent_yes':
                cursor.execute('UPDATE users SET consent = 1 WHERE phone = ?', (phone,))
                conn.commit()
                await callback_query.message.answer("Согласие принято! Авторизация успешна.")
                logger.info(f"Пользователь {phone} принял согласие")
            else:
                await callback_query.message.answer("Без согласия бот не может работать. До свидания.")
                logger.info(f"Пользователь {phone} отказался от согласия")
        await state.clear()
    except sqlite3.Error as e:
        logger.error(f"Ошибка при обновлении согласия для телефона {phone}: {e}")
        await callback_query.message.answer("Произошла ошибка. Попробуйте позже.")
    await callback_query.answer()

@dp.message(Command(commands=['profilReg']))
async def profil_reg_handler(message: types.Message, state: FSMContext):
    await state.set_state(ProfilRegStates.name)
    await message.answer("Введите ваше имя (только буквы):")
    logger.info(f"Пользователь {message.from_user.id} начал регистрацию профиля")

@dp.message(ProfilRegStates.name)
async def process_name(message: types.Message, state: FSMContext):
    name = message.text.strip().capitalize()
    if not re.match(r'^[А-Яа-яA-Za-z\s]+$', name):
        await message.answer("Имя должно содержать только буквы. Попробуйте снова.")
        logger.warning(f"Некорректное имя от пользователя {message.from_user.id}: {name}")
        return
    await state.update_data(name=name)
    await state.set_state(ProfilRegStates.surname)
    await message.answer("Введите вашу фамилию (только буквы):")

@dp.message(ProfilRegStates.surname)
async def process_surname(message: types.Message, state: FSMContext):
    surname = message.text.strip().capitalize()
    if not re.match(r'^[А-Яа-яA-Za-z\s]+$', surname):
        await message.answer("Фамилия должна содержать только буквы. Попробуйте снова.")
        logger.warning(f"Некорректная фамилия от пользователя {message.from_user.id}: {surname}")
        return
    await state.update_data(surname=surname)
    await state.set_state(ProfilRegStates.category)
    keyboard = InlineKeyboardBuilder()
    keyboard.button(text="Студент", callback_data="cat_student")
    keyboard.button(text="Сотрудник", callback_data="cat_employee")
    keyboard.button(text="Внешний донор", callback_data="cat_external")
    await message.answer("Выберите категорию:", reply_markup=keyboard.as_markup())

@dp.callback_query(lambda c: c.data.startswith('cat_'))
async def process_category(callback_query: types.CallbackQuery, state: FSMContext):
    category = callback_query.data.split('_')[1]
    await state.update_data(category=category)
    if category == 'student':
        await state.set_state(ProfilRegStates.group)
        await callback_query.message.answer("Введите номер группы (формат: Б21-302):")
    else:
        await state.set_state(ProfilRegStates.social_contacts)
        await callback_query.message.answer("Введите контакты в соцсетях (или 'нет'):")
    await callback_query.answer()
    logger.info(f"Пользователь {callback_query.from_user.id} выбрал категорию: {category}")

@dp.message(ProfilRegStates.group)
async def process_group(message: types.Message, state: FSMContext):
    group = message.text.strip().upper()
    if not re.match(r'^[А-Я]\d{2}-\d{3}$', group):
        await message.answer("Неверный формат группы (пример: Б21-302). Попробуйте снова.")
        logger.warning(f"Некорректный формат группы от пользователя {message.from_user.id}: {group}")
        return
    await state.update_data(group=group)
    await state.set_state(ProfilRegStates.social_contacts)
    await message.answer("Введите контакты в соцсетях (или 'нет'):")

@dp.message(ProfilRegStates.social_contacts)
async def process_social_contacts(message: types.Message, state: FSMContext):
    social_contacts = message.text.strip() if message.text.strip().lower() != 'нет' else None
    data = await state.get_data()
    try:
        with sqlite3.connect('donor_bot.db', timeout=10) as conn:
            cursor = conn.cursor()
            cursor.execute('''INSERT OR IGNORE INTO users 
                (telegram_id, phone, name, surname, category, user_group, social_contacts, profile_status)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')''',
                (message.from_user.id, data.get('phone'), data['name'], data['surname'],
                 data['category'], data.get('group'), social_contacts))
            conn.commit()
            logger.info(f"Профиль пользователя {data['name']} {data['surname']} отправлен на модерацию")
        await state.clear()
        await message.answer("Ваш профиль отправлен на модерацию.")
    except sqlite3.Error as e:
        logger.error(f"Ошибка при сохранении профиля пользователя {data.get('name', 'Unknown')}: {e}")
        await message.answer("Произошла ошибка при сохранении профиля. Попробуйте позже.")

@dp.message(Command(commands=['help']))
async def help_handler(message: types.Message):
    await message.answer("Вот команды пользователя:\n"
                         "/profilReg - Зарегистрировать профиль\n"
                         "/reg - Записаться на мероприятие\n"
                         "/profil - Посмотреть/изменить профиль\n"
                         "/stats - Моя статистика\n"
                         "/info - Информационные разделы\n"
                         "/help - Показать этот список")
    logger.info(f"Пользователь {message.from_user.id} вызвал команду /help")

@dp.message(Command(commands=['reg']))
async def reg_handler(message: types.Message):
    try:
        with sqlite3.connect('donor_bot.db', timeout=10) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT profile_status FROM users WHERE telegram_id = ?', (message.from_user.id,))
            user = cursor.fetchone()
            if not user or user[0] != 'approved':
                await message.answer("Ваш профиль не одобрен или не существует.")
                logger.warning(f"Пользователь {message.from_user.id} не имеет одобренного профиля")
                return
            cursor.execute('SELECT id, date, time, location, description, capacity FROM events WHERE status = "active"')
            events = cursor.fetchall()
        if not events:
            await message.answer("Нет доступных мероприятий.")
            logger.info("Нет доступных мероприятий для регистрации")
            return
        keyboard = InlineKeyboardBuilder()
        for event in events:
            keyboard.button(text=f"{event[1]} {event[2]} - {event[4]}", callback_data=f"reg_{event[0]}")
        await message.answer("Выберите мероприятие:", reply_markup=keyboard.as_markup())
        logger.info(f"Пользователь {message.from_user.id} запросил регистрацию на мероприятие")
    except sqlite3.Error as e:
        logger.error(f"Ошибка при получении мероприятий: {e}")
        await message.answer("Произошла ошибка. Попробуйте позже.")

@dp.callback_query(lambda c: c.data.startswith('reg_'))
async def process_register(callback_query: types.CallbackQuery):
    event_id = int(callback_query.data.split('_')[1])
    user_id = callback_query.from_user.id
    try:
        with sqlite3.connect('donor_bot.db', timeout=10) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT id FROM users WHERE telegram_id = ?', (user_id,))
            db_user_id = cursor.fetchone()[0]
            cursor.execute('SELECT capacity FROM events WHERE id = ?', (event_id,))
            capacity = cursor.fetchone()[0]
            cursor.execute('SELECT COUNT(*) FROM registrations WHERE event_id = ? AND status = "registered"', (event_id,))
            registered_count = cursor.fetchone()[0]
            if registered_count >= capacity:
                await callback_query.answer("Мероприятие заполнено.")
                logger.warning(f"Мероприятие {event_id} заполнено")
                return
            cursor.execute('SELECT date FROM events WHERE id = ?', (event_id,))
            event_date = cursor.fetchone()[0]
            try:
                reminder_date = (datetime.strptime(event_date, '%Y-%m-%d') - timedelta(days=1)).strftime('%Y-%m-%d')
            except ValueError:
                await callback_query.answer("Ошибка: некорректная дата мероприятия.")
                logger.error(f"Некорректная дата мероприятия {event_id}: {event_date}")
                return
            cursor.execute('INSERT INTO registrations (user_id, event_id) VALUES (?, ?)', (db_user_id, event_id))
            cursor.execute('INSERT INTO reminders (user_id, event_id, reminder_date) VALUES (?, ?, ?)',
                           (db_user_id, event_id, reminder_date))
            conn.commit()
            logger.info(f"Пользователь {user_id} зарегистрирован на мероприятие {event_id}")
        await callback_query.answer("Вы зарегистрированы!")
    except sqlite3.Error as e:
        logger.error(f"Ошибка при регистрации пользователя {user_id} на мероприятие {event_id}: {e}")
        await callback_query.answer("Произошла ошибка при регистрации. Попробуйте позже.")

@dp.message(Command(commands=['profil']))
async def profil_handler(message: types.Message, state: FSMContext):
    try:
        with sqlite3.connect('donor_bot.db', timeout=10) as conn:
            cursor = conn.cursor()
            cursor.execute(
                'SELECT id, name, surname, category, user_group, social_contacts, dkm, profile_status FROM users WHERE telegram_id = ?',
                (message.from_user.id,))
            user = cursor.fetchone()
            if not user:
                await message.answer("Профиль не найден.")
                logger.warning(f"Профиль пользователя {message.from_user.id} не найден")
                return
            user_id = user[0]
            # Вычисление статистики из donations
            cursor.execute('SELECT COUNT(*) FROM donations WHERE user_id = ? AND center = "Гаврилова"', (user_id,))
            count_gavrilov = cursor.fetchone()[0]
            cursor.execute('SELECT COUNT(*) FROM donations WHERE user_id = ? AND center = "ФМБА"', (user_id,))
            count_fmba = cursor.fetchone()[0]
            sum_donations = count_gavrilov + count_fmba
            cursor.execute('SELECT MAX(date), center FROM donations WHERE user_id = ? GROUP BY center ORDER BY date DESC LIMIT 1', (user_id,))
            last_donation = cursor.fetchone()
            last_date_center = f"{last_donation[0]} / {last_donation[1]}" if last_donation else "Нет"
            cursor.execute('SELECT date, center FROM donations WHERE user_id = ? ORDER BY date DESC', (user_id,))
            history = cursor.fetchall()
            history_str = "\n".join([f"{d[0]} - {d[1]}" for d in history]) if history else "Нет истории"
            dkm_str = "Да" if user[6] else "Нет"
            response = (
                f"Ваш профиль:\nИмя: {user[1]}\nФамилия: {user[2]}\nКатегория: {user[3]}\nГруппа: {user[4]}\n"
                f"Соцсети: {user[5] or 'Нет'}\nСтатус: {user[7]}\n"
                f"Количество донаций: {sum_donations}\nПоследняя донация: {last_date_center}\n"
                f"Вступление в ДКМ: {dkm_str}\nИстория донаций:\n{history_str}")
        await message.answer(response)
        logger.info(f"Пользователь {message.from_user.id} запросил просмотр профиля")
    except sqlite3.Error as e:
        logger.error(f"Ошибка при получении профиля пользователя {message.from_user.id}: {e}")
        await message.answer("Произошла ошибка. Попробуйте позже.")

@dp.message(Command(commands=['stats']))
async def stats_handler(message: types.Message):
    try:
        with sqlite3.connect('donor_bot.db', timeout=10) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT id FROM users WHERE telegram_id = ?', (message.from_user.id,))
            user = cursor.fetchone()
            if user:
                user_id = user[0]
                cursor.execute('SELECT COUNT(*) FROM registrations WHERE user_id = ? AND status = "registered"', (user_id,))
                reg_count = cursor.fetchone()[0]
                await message.answer(f"Ваша статистика:\nЗарегистрировано на мероприятий: {reg_count}")
                logger.info(f"Пользователь {message.from_user.id} запросил статистику: {reg_count} регистраций")
            else:
                await message.answer("Вы не зарегистрированы.")
                logger.warning(f"Пользователь {message.from_user.id} не зарегистрирован")
    except sqlite3.Error as e:
        logger.error(f"Ошибка при получении статистики пользователя {message.from_user.id}: {e}")
        await message.answer("Произошла ошибка. Попробуйте позже.")

# --- Админские команды ---

@dp.message(Command(commands=['admin_stats']))
async def admin_stats_handler(message: types.Message):
    if not is_admin(message.from_user.id):
        await message.answer("Нет прав.")
        logger.warning(f"Пользователь {message.from_user.id} пытался получить админскую статистику")
        return
    try:
        with sqlite3.connect('donor_bot.db', timeout=10) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT COUNT(*) FROM users WHERE profile_status = "approved"')
            users_count = cursor.fetchone()[0]
            cursor.execute('SELECT COUNT(*) FROM events')
            events_count = cursor.fetchone()[0]
            cursor.execute('SELECT COUNT(*) FROM registrations WHERE status = "registered"')
            reg_count = cursor.fetchone()[0]
        await message.answer(
            f"Статистика:\nПользователей: {users_count}\nМероприятий: {events_count}\nРегистраций: {reg_count}")
        logger.info(f"Админ {message.from_user.id} запросил статистику")
    except sqlite3.Error as e:
        logger.error(f"Ошибка при получении админской статистики: {e}")
        await message.answer("Произошла ошибка. Попробуйте позже.")

@dp.message(Command(commands=['admin_reg']))
async def admin_reg_handler(message: types.Message):
    if not is_admin(message.from_user.id):
        await message.answer("Нет прав.")
        logger.warning(f"Пользователь {message.from_user.id} пытался управлять заявками")
        return
    try:
        with sqlite3.connect('donor_bot.db', timeout=10) as conn:
            cursor = conn.cursor()
            cursor.execute(
                'SELECT id, name, surname, user_group, social_contacts FROM users WHERE profile_status = "pending"')
            pending_users = cursor.fetchall()
        if not pending_users:
            await message.answer("Нет заявок.")
            logger.info("Нет заявок на модерацию")
            return
        for user in pending_users:
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="Принять", callback_data=f"approve_{user[0]}")
            keyboard.button(text="Отклонить", callback_data=f"reject_{user[0]}")
            await message.answer(
                f"Заявка: {user[1]} {user[2]}\nГруппа: {user[3]}\nСоцсети: {user[4]}",
                reply_markup=keyboard.as_markup())
            logger.info(f"Отображена заявка пользователя {user[1]} {user[2]} для админа {message.from_user.id}")
    except sqlite3.Error as e:
        logger.error(f"Ошибка при получении заявок на модерацию: {e}")
        await message.answer("Произошла ошибка. Попробуйте позже.")

@dp.callback_query(lambda c: c.data.startswith('approve_') or c.data.startswith('reject_'))
async def process_profile_action(callback_query: types.CallbackQuery):
    action, user_id = callback_query.data.split('_')
    user_id = int(user_id)
    try:
        with sqlite3.connect('donor_bot.db', timeout=10) as conn:
            cursor = conn.cursor()
            status = 'approved' if action == 'approve' else 'rejected'
            cursor.execute('UPDATE users SET profile_status = ? WHERE id = ?', (status, user_id))
            if action == 'approve':
                cursor.execute('SELECT telegram_id FROM users WHERE id = ?', (user_id,))
                telegram_id = cursor.fetchone()[0]
                await bot.send_message(telegram_id, "Ваш профиль был принят администратором.")
            conn.commit()
            logger.info(f"Профиль пользователя ID {user_id} {status} админом {callback_query.from_user.id}")
        await callback_query.answer(f"Профиль {'принят' if action == 'approve' else 'отклонен'}.")
    except sqlite3.Error as e:
        logger.error(f"Ошибка при обработке профиля ID {user_id}: {e}")
        await callback_query.answer("Произошла ошибка. Попробуйте позже.")

@dp.message(Command(commands=['admin_help']))
async def admin_help_handler(message: types.Message):
    if not is_admin(message.from_user.id):
        await message.answer("Нет прав.")
        logger.warning(f"Пользователь {message.from_user.id} пытался получить админскую помощь")
        return
    await message.answer("/admin_stats - Статистика проекта\n"
                         "/admin_reg - Управление заявками\n"
                         "/add_event - Добавить мероприятие\n"
                         "/stats_event - Статистика мероприятий\n"
                         "/see_profile - Просмотр профилей\n"
                         "/import_excel - Импорт из Excel\n"
                         "/edit_info - Редактировать инфо разделы\n"
                         "/upload_stats - Загрузить статистику из Excel\n"
                         "/export_stats - Выгрузить статистику в Excel\n"
                         "/help - Список пользовательских команд")
    logger.info(f"Админ {message.from_user.id} запросил список админских команд")

@dp.message(Command(commands=['add_event']))
async def add_event_handler(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.answer("Нет прав.")
        logger.warning(f"Пользователь {message.from_user.id} пытался добавить мероприятие")
        return
    await state.set_state(AddEventStates.date)
    await message.answer("Введите дату (YYYY-MM-DD):")
    logger.info(f"Админ {message.from_user.id} начал добавление мероприятия")

@dp.message(AddEventStates.date)
async def process_event_date(message: types.Message, state: FSMContext):
    try:
        datetime.strptime(message.text, '%Y-%m-%d')
    except ValueError:
        await message.answer("Некорректный формат даты. Пожалуйста, введите дату в формате YYYY-MM-DD.")
        logger.warning(f"Некорректный формат даты от админа {message.from_user.id}: {message.text}")
        return
    await state.update_data(date=message.text)
    await state.set_state(AddEventStates.time)
    await message.answer("Введите время (HH:MM):")

@dp.message(AddEventStates.time)
async def process_event_time(message: types.Message, state: FSMContext):
    await state.update_data(time=message.text)
    await state.set_state(AddEventStates.location)
    await message.answer("Введите место:")

@dp.message(AddEventStates.location)
async def process_event_location(message: types.Message, state: FSMContext):
    await state.update_data(location=message.text)
    await state.set_state(AddEventStates.description)
    await message.answer("Введите описание:")

@dp.message(AddEventStates.description)
async def process_event_description(message: types.Message, state: FSMContext):
    await state.update_data(description=message.text)
    await state.set_state(AddEventStates.capacity)
    await message.answer("Введите вместимость:")

@dp.message(AddEventStates.capacity)
async def process_event_capacity(message: types.Message, state: FSMContext):
    try:
        capacity = int(message.text)
        if capacity <= 0:
            raise ValueError("Вместимость должна быть положительным числом")
    except ValueError:
        await message.answer("Вместимость должна быть числом больше 0.")
        logger.warning(f"Некорректная вместимость от админа {message.from_user.id}: {message.text}")
        return
    data = await state.get_data()
    try:
        with sqlite3.connect('donor_bot.db', timeout=10) as conn:
            cursor = conn.cursor()
            cursor.execute('''INSERT INTO events (date, time, location, description, capacity)
                            VALUES (?, ?, ?, ?, ?)''',
                           (data['date'], data['time'], data['location'], data['description'], capacity))
            conn.commit()
            logger.info(f"Админ {message.from_user.id} добавил мероприятие: {data['description']}")
        await state.clear()
        await message.answer("Мероприятие добавлено.")
    except sqlite3.Error as e:
        logger.error(f"Ошибка при добавлении мероприятия: {e}")
        await message.answer("Произошла ошибка при добавлении мероприятия. Попробуйте позже.")

@dp.message(Command(commands=['stats_event']))
async def stats_event_handler(message: types.Message):
    if not is_admin(message.from_user.id):
        await message.answer("Нет прав.")
        logger.warning(f"Пользователь {message.from_user.id} пытался получить статистику мероприятий")
        return
    try:
        with sqlite3.connect('donor_bot.db', timeout=10) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT id, date, time, description, capacity, status FROM events')
            events = cursor.fetchall()
            for event in events:
                cursor.execute('SELECT COUNT(*) FROM registrations WHERE event_id = ? AND status = "registered"', (event[0],))
                reg_count = cursor.fetchone()[0]
                cursor.execute('SELECT COUNT(*) FROM registrations WHERE event_id = ? AND attended = 1', (event[0],))
                donors_count = cursor.fetchone()[0]
                keyboard = InlineKeyboardBuilder()
                keyboard.button(text="Заморозить" if event[5] == 'active' else "Разморозить",
                                callback_data=f"toggle_{event[0]}")
                await message.answer(f"Мероприятие: {event[1]} {event[2]} - {event[3]}\n"
                                     f"Вместимость: {event[4]}\nЗарегистрировано: {reg_count}\nДоноров: {donors_count}\nСтатус: {event[5]}",
                                     reply_markup=keyboard.as_markup())
                logger.info(f"Админ {message.from_user.id} запросил статистику мероприятия ID {event[0]}")
    except sqlite3.Error as e:
        logger.error(f"Ошибка при получении статистики мероприятий: {e}")
        await message.answer("Произошла ошибка. Попробуйте позже.")

@dp.callback_query(lambda c: c.data.startswith('toggle_'))
async def toggle_event(callback_query: types.CallbackQuery):
    event_id = int(callback_query.data.split('_')[1])
    try:
        with sqlite3.connect('donor_bot.db', timeout=10) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT status FROM events WHERE id = ?', (event_id,))
            current_status = cursor.fetchone()[0]
            new_status = 'frozen' if current_status == 'active' else 'active'
            cursor.execute('UPDATE events SET status = ? WHERE id = ?', (new_status, event_id))
            conn.commit()
            logger.info(f"Админ {callback_query.from_user.id} изменил статус мероприятия {event_id} на {new_status}")
        await callback_query.answer(f"Мероприятие {'заморожено' if new_status == 'frozen' else 'разморожено'}.")
    except sqlite3.Error as e:
        logger.error(f"Ошибка при изменении статуса мероприятия {event_id}: {e}")
        await callback_query.answer("Произошла ошибка. Попробуйте позже.")

@dp.message(Command(commands=['see_profile']))
async def see_profile_handler(message: types.Message):
    if not is_admin(message.from_user.id):
        await message.answer("Нет прав.")
        logger.warning(f"Пользователь {message.from_user.id} пытался просмотреть профили")
        return
    await show_profiles(message, offset=0)

async def show_profiles(message: types.Message, offset: int):
    try:
        with sqlite3.connect('donor_bot.db', timeout=10) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT id, name, surname, user_group FROM users LIMIT 5 OFFSET ?', (offset,))
            users = cursor.fetchall()
            if not users:
                await message.answer("Профили не найдены.")
                logger.info("Профили пользователей не найдены")
                return
            keyboard = InlineKeyboardBuilder()
            for user in users:
                cursor.execute('SELECT COUNT(*) FROM registrations WHERE user_id = ? AND status = "registered"', (user[0],))
                reg_count = cursor.fetchone()[0]
                text = f"{user[2]} {user[1]}, Группа: {user[3]}, (ID: {user[0]}), Регистраций: {reg_count}"
                keyboard.button(text="Подробнее", callback_data=f"detail_{user[0]}")
                keyboard.button(text=text, callback_data="noop")
            if offset > 0:
                keyboard.button(text="Назад", callback_data=f"prev_{offset - 5}")
            if len(users) == 5:
                keyboard.button(text="Вперед", callback_data=f"next_{offset + 5}")
            keyboard.adjust(1, 1)
            await message.answer("Список профилей:", reply_markup=keyboard.as_markup())
            logger.info(f"Админ {message.from_user.id} запросил список профилей, offset: {offset}")
    except sqlite3.Error as e:
        logger.error(f"Ошибка при получении списка профилей: {e}")
        await message.answer("Произошла ошибка. Попробуйте позже.")

@dp.callback_query(lambda c: c.data.startswith('detail_'))
async def show_user_detail(callback_query: types.CallbackQuery):
    user_id = int(callback_query.data.split('_')[1])
    try:
        with sqlite3.connect('donor_bot.db', timeout=10) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM users WHERE id = ?', (user_id,))
            user = cursor.fetchone()
        if not user:
            await callback_query.answer("Пользователь не найден.")
            logger.warning(f"Пользователь ID {user_id} не найден")
            return
        response = (f"Полная анкета:\n"
                    f"ID: {user[0]}\n"
                    f"Telegram ID: {user[1]}\n"
                    f"Телефон: {user[2]}\n"
                    f"Имя: {user[3]}\n"
                    f"Фамилия: {user[4]}\n"
                    f"Категория: {user[5]}\n"
                    f"Группа: {user[6]}\n"
                    f"Соцсети: {user[7]}\n"
                    f"DKM: {'Да' if user[8] else 'Нет'}\n"
                    f"Статус: {user[10]}")
        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="Кикнуть", callback_data=f"kick_{user_id}")
        await callback_query.message.answer(response, reply_markup=keyboard.as_markup())
        logger.info(f"Админ {callback_query.from_user.id} запросил детали профиля пользователя ID {user_id}")
    except sqlite3.Error as e:
        logger.error(f"Ошибка при получении деталей профиля ID {user_id}: {e}")
        await callback_query.answer("Произошла ошибка. Попробуйте позже.")

@dp.callback_query(lambda c: c.data.startswith('kick_'))
async def kick_user(callback_query: types.CallbackQuery):
    user_id = int(callback_query.data.split('_')[1])
    try:
        with sqlite3.connect('donor_bot.db', timeout=10) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT telegram_id FROM users WHERE id = ?', (user_id,))
            telegram_id = cursor.fetchone()[0]
            cursor.execute('DELETE FROM users WHERE id = ?', (user_id,))
            conn.commit()
            await bot.send_message(telegram_id, "Ваш профиль был удален администратором.")
            logger.info(f"Админ {callback_query.from_user.id} удалил пользователя ID {user_id}")
        await callback_query.answer("Пользователь удален.")
    except sqlite3.Error as e:
        logger.error(f"Ошибка при удалении пользователя ID {user_id}: {e}")
        await callback_query.answer("Произошла ошибка. Попробуйте позже.")

@dp.callback_query(lambda c: c.data.startswith('next_') or c.data.startswith('prev_'))
async def process_pagination(callback_query: types.CallbackQuery):
    action, offset = callback_query.data.split('_')
    offset = int(offset)
    await show_profiles(callback_query.message, offset)
    await callback_query.answer()
    logger.info(f"Админ {callback_query.from_user.id} запросил пагинацию профилей, offset: {offset}")

@dp.message(Command(commands=['import_excel']))
async def import_excel_handler(message: types.Message):
    if not is_admin(message.from_user.id):
        await message.answer("Нет прав.")
        logger.warning(f"Пользователь {message.from_user.id} пытался выполнить импорт Excel")
        return
    try:
        import_from_excel()
        await message.answer("Данные из Excel успешно импортированы.")
        logger.info(f"Админ {message.from_user.id} выполнил импорт данных из Excel")
    except Exception as e:
        await message.answer(f"Ошибка при импорте данных: {str(e)}")
        logger.error(f"Ошибка при импорте Excel админом {message.from_user.id}: {e}")

@dp.message(Command(commands=['upload_stats']))
async def upload_stats_handler(message: types.Message):
    if not is_admin(message.from_user.id):
        await message.answer("Нет прав.")
        return
    await message.answer("Отправьте файл Excel со статистикой (ФИО, дата, ЦК).")

@dp.message(lambda message: message.document and message.document.file_name.endswith('.xlsx'))
async def process_upload_stats(message: types.Message):
    if not is_admin(message.from_user.id):
        await message.answer("Нет прав.")
        return
    try:
        file_id = message.document.file_id
        file = await bot.get_file(file_id)
        file_path = file.file_path
        await bot.download_file(file_path, "temp_stats.xlsx")
        wb = openpyxl.load_workbook('temp_stats.xlsx')
        sheet = wb.active
        with sqlite3.connect('donor_bot.db', timeout=10) as conn:
            cursor = conn.cursor()
            for row in sheet.iter_rows(min_row=2, values_only=True):
                fio = str(row[0]).strip()
                parts = fio.split(maxsplit=1)
                surname = parts[0]
                name = parts[1] if len(parts) > 1 else ''
                date = str(row[5]) if row[5] else None  # Пример: дата Гаврилова или ФМБА
                center = 'Гаврилова' if row[2] else 'ФМБА' if row[3] else None
                if not center or not date:
                    continue
                cursor.execute('SELECT id FROM users WHERE name = ? AND surname = ?', (name, surname))
                user = cursor.fetchone()
                if user:
                    user_id = user[0]
                    cursor.execute('INSERT INTO donations (user_id, date, center) VALUES (?, ?, ?)', (user_id, date, center))
                # Если DKM (допустим, добавим колонку 9 как DKM)
                if len(row) > 9 and row[9]:
                    cursor.execute('UPDATE users SET dkm = 1 WHERE id = ?', (user_id,))
            conn.commit()
        await message.answer("Статистика загружена и БД обновлена.")
        logger.info(f"Админ {message.from_user.id} загрузил статистику из Excel")
    except Exception as e:
        await message.answer(f"Ошибка при загрузке: {str(e)}")
        logger.error(f"Ошибка загрузки stats Excel: {e}")

@dp.message(Command(commands=['export_stats']))
async def export_stats_handler(message: types.Message):
    if not is_admin(message.from_user.id):
        await message.answer("Нет прав.")
        return
    try:
        wb = openpyxl.Workbook()
        sheet = wb.active
        sheet.append(['ID', 'ФИО', 'Группа', 'Кол-во Гаврилова', 'Кол-во ФМБА', 'Сумма', 'Последняя Гаврилова', 'Последняя ФМБА', 'Телефон'])
        with sqlite3.connect('donor_bot.db', timeout=10) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM users')
            users = cursor.fetchall()
            for user in users:
                user_id = user[0]
                fio = f"{user[4]} {user[3]}"
                group = user[6]
                phone = user[2]
                cursor.execute('SELECT COUNT(*) FROM donations WHERE user_id = ? AND center = "Гаврилова"', (user_id,))
                count_g = cursor.fetchone()[0]
                cursor.execute('SELECT COUNT(*) FROM donations WHERE user_id = ? AND center = "ФМБА"', (user_id,))
                count_f = cursor.fetchone()[0]
                sum_d = count_g + count_f
                cursor.execute('SELECT MAX(date) FROM donations WHERE user_id = ? AND center = "Гаврилова"', (user_id,))
                last_g = cursor.fetchone()[0] or ''
                cursor.execute('SELECT MAX(date) FROM donations WHERE user_id = ? AND center = "ФМБА"', (user_id,))
                last_f = cursor.fetchone()[0] or ''
                sheet.append([user_id, fio, group, count_g, count_f, sum_d, last_g, last_f, phone])
        wb.save('export_stats.xlsx')
        await bot.send_document(message.chat.id, types.FSInputFile('export_stats.xlsx'))
        logger.info(f"Админ {message.from_user.id} выгрузил статистику")
    except Exception as e:
        await message.answer(f"Ошибка при выгрузке: {str(e)}")
        logger.error(f"Ошибка выгрузки stats: {e}")

# --- Напоминания и опросы ---

async def check_reminders():
    try:
        with sqlite3.connect('donor_bot.db', timeout=10) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT id, user_id, event_id, reminder_date FROM reminders WHERE reminder_date <= ?',
                           (datetime.now().strftime('%Y-%m-%d'),))
            reminders = cursor.fetchall()  # Исправлено имя переменной
            for reminder in reminders:
                user_id = reminder[1]
                event_id = reminder[2]
                cursor.execute('SELECT date, time, location FROM events WHERE id = ?', (event_id,))
                event = cursor.fetchone()
                if event:
                    await bot.send_message(user_id, f"Напоминание: {event[0]} {event[1]} в {event[2]} скоро начнется!")
                    logger.info(f"Отправлено напоминание пользователю {user_id} для мероприятия {event_id}")
                cursor.execute('DELETE FROM reminders WHERE id = ?', (reminder[0],))
            conn.commit()
    except sqlite3.Error as e:
        logger.error(f"Ошибка при проверке напоминаний: {e}")

async def check_non_attendance():
    try:
        today = datetime.now().strftime('%Y-%m-%d')
        with sqlite3.connect('donor_bot.db', timeout=10) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT id FROM events WHERE date < ? AND status = "active"', (today,))
            past_events = cursor.fetchall()
            for event in past_events:
                event_id = event[0]
                cursor.execute('SELECT id, user_id FROM registrations WHERE event_id = ? AND status = "registered" AND attended = 0', (event_id,))
                non_attended = cursor.fetchall()
                for reg in non_attended:
                    reg_id = reg[0]
                    user_id = reg[1]
                    keyboard = InlineKeyboardBuilder()
                    keyboard.button(text="Медотвод", callback_data=f"reason_med_{reg_id}")
                    keyboard.button(text="Личные причины", callback_data=f"reason_personal_{reg_id}")
                    keyboard.button(text="Не захотел", callback_data=f"reason_no_want_{reg_id}")
                    await bot.send_message(user_id, "Вы зарегистрировались на прошедшее мероприятие, но не пришли. Укажите причину:", reply_markup=keyboard.as_markup())
                    logger.info(f"Отправлен опрос неявки пользователю {user_id} для reg {reg_id}")
    except sqlite3.Error as e:
        logger.error(f"Ошибка при проверке неявок: {e}")

@dp.callback_query(lambda c: c.data.startswith('reason_'))
async def process_non_attendance_reason(callback_query: types.CallbackQuery):
    parts = callback_query.data.split('_')
    reason_type = parts[1]
    reg_id = int(parts[2])
    reason = {'med': 'медотвод', 'personal': 'личные причины', 'no': 'не захотел'}[reason_type]
    try:
        with sqlite3.connect('donor_bot.db', timeout=10) as conn:
            cursor = conn.cursor()
            cursor.execute('INSERT INTO non_attendance (registration_id, reason) VALUES (?, ?)', (reg_id, reason))
            conn.commit()
        await callback_query.answer("Причина записана.")
        logger.info(f"Записана причина неявки для reg {reg_id}: {reason}")
    except sqlite3.Error as e:
        logger.error(f"Ошибка записи причины неявки для reg {reg_id}: {e}")
        await callback_query.answer("Ошибка. Попробуйте позже.")

async def schedule_checker():
    while True:
        schedule.run_pending()
        await asyncio.sleep(1)

async def on_startup(_):
    asyncio.create_task(schedule_checker())
    logger.info("Бот успешно запущен")

async def main():
    try:
        init_db()
        import_from_excel()
        schedule.every(10).minutes.do(lambda: asyncio.create_task(check_reminders()))  # Изменено на 10 мин
        schedule.every().day.at("00:00").do(lambda: asyncio.create_task(check_non_attendance()))  # Ежедневный опрос
        await dp.start_polling(bot, skip_updates=True, on_startup=on_startup)
    except Exception as e:
        logger.error(f"Критическая ошибка при запуске бота: {e}")
        raise

if __name__ == '__main__':
    asyncio.run(main())