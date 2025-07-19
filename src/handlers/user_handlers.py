import re
from datetime import datetime, timedelta
from aiogram import types, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardMarkup, KeyboardButton

from src.states.states import ProfilRegStates, ConsentStates, CancelReasonState, AskQuestionState
from src.database.db import get_user_by_telegram_id, get_profile_status_by_telegram_id, get_active_events
from src.database.db import get_donations_count_by_center, get_last_donation, get_donations_history, \
    get_user_registrations, get_user_registrations_count
from src.database.db import get_user_id_by_telegram_id, get_event_capacity, \
    get_registrations_count as get_event_reg_count
from src.database.db import get_event_date, add_registration, add_reminder, cancel_registration
from src.database.db import add_non_attendance_reason, save_or_update_user, get_user_by_phone, get_consent_by_phone
from src.database.db import update_consent_by_phone, add_question, logger, get_user_by_fio, get_connection

user_router = Router()


@user_router.message(Command(commands=['start']))
async def start_handler(message: types.Message, state: FSMContext):
    # Локальный импорт bot внутри функции для избежания циклического импорта
    from src.bot import bot
    logger.info(f"Пользователь {message.from_user.id} вызвал команду /start")
    try:
        await bot.send_photo(message.chat.id, types.FSInputFile('frame 3.jpg'),
                             caption="Добро пожаловать в бот Дня Донора МИФИ! 💉❤️")
    except FileNotFoundError:
        logger.warning("Файл 'frame 3.jpg' не найден, баннер не отправлен")
        await message.answer("Добро пожаловать в бот Дня Донора МИФИ! 💉❤️")
    try:
        await bot.send_document(message.chat.id, types.FSInputFile('privacy_policy.pdf'),
                                caption="Ознакомьтесь с пользовательским соглашением (политика конфиденциальности). 📄")
    except FileNotFoundError:
        logger.warning("Файл 'privacy_policy.pdf' не найден")
        await message.answer("Файл с пользовательским соглашением не найден. Обратитесь к администратору. ⚠️")
        return
    except Exception as e:
        logger.error(f"Ошибка при отправке PDF: {e}")
        await message.answer("Произошла ошибка при отправке соглашения. Попробуйте позже. ⚠️")
        return
    await state.set_state(ConsentStates.consent)
    keyboard = InlineKeyboardBuilder()
    keyboard.button(text="Согласен ✅", callback_data="consent_yes")
    keyboard.button(text="Нет ❌", callback_data="consent_no")
    await message.answer(
        "Я ознакомился с пользовательским соглашением и обязуюсь прочитать информацию из /info перед регистрацией на любое мероприятие. 📄",
        reply_markup=keyboard.as_markup())


@user_router.callback_query(lambda c: c.data in ['consent_yes', 'consent_no'], ConsentStates.consent)
async def process_initial_consent(callback_query: types.CallbackQuery, state: FSMContext):
    if callback_query.data == 'consent_yes':
        await state.update_data(initial_consent=True)
        # Если пользователь уже в базе (по телефону), обновляем consent
        data = await state.get_data()
        phone = data.get('phone')
        if phone:
            try:
                update_consent_by_phone(phone, 1)
                logger.info(f"Обновлено согласие (consent=1) для телефона {phone} в process_initial_consent")
            except Exception as e:
                logger.error(f"Ошибка при обновлении согласия для телефона {phone}: {e}")
        keyboard = ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="Поделиться номером телефона 📞", request_contact=True)]],
            resize_keyboard=True,
            one_time_keyboard=True
        )
        await callback_query.message.answer("Спасибо! Теперь поделитесь номером телефона для авторизации. 🔑",
                                            reply_markup=keyboard)
        await state.set_state(ProfilRegStates.phone_confirm)
        logger.info(f"Пользователь {callback_query.from_user.id} принял согласие (ознакомление с PDF и /info)")
    else:
        await callback_query.message.answer("Без согласия бот не может работать. До свидания. 👋")
        await state.clear()
        logger.info(f"Пользователь {callback_query.from_user.id} отказался от согласия")
    await callback_query.answer()


@user_router.message(ProfilRegStates.phone_confirm)
async def process_phone(message: types.Message, state: FSMContext):
    if not message.contact:
        await message.answer("Пожалуйста, используйте кнопку для отправки контакта. ⚠️")
        logger.warning(f"Пользователь {message.from_user.id} не отправил контакт")
        return
    phone = message.contact.phone_number
    try:
        user = get_user_by_phone(phone)
        await state.update_data(phone=phone)
        if user:
            await state.update_data(fio=user[3], category=user[4], group=user[5], social_contacts=user[6])
            response = f"Вы уже в базе: {user[3]}, категория: {user[4]}, группа: {user[5] or 'Нет'}. Подтверждаете? (Да/Нет) ✅/❌"
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="Да ✅", callback_data="confirm_yes")
            keyboard.button(text="Нет ❌", callback_data="confirm_no")
            await message.answer(response, reply_markup=keyboard.as_markup())
            logger.info(f"Найден существующий пользователь с телефоном {phone}")
        else:
            await profil_reg_handler(message, state)
    except Exception as e:
        logger.error(f"Ошибка при проверке телефона {phone}: {e}")
        await message.answer("Произошла ошибка при проверке телефона. Попробуйте позже. ⚠️")


@user_router.callback_query(lambda c: c.data in ['confirm_yes', 'confirm_no'])
async def confirm_existing(callback_query: types.CallbackQuery, state: FSMContext):
    # Локальный импорт bot внутри функции
    from src.bot import bot
    if callback_query.data == 'confirm_yes':
        data = await state.get_data()
        phone = data.get('phone')
        initial_consent = data.get('initial_consent', False)
        try:
            # Если пользователь дал начальное согласие, обновляем consent в базе
            if initial_consent:
                update_consent_by_phone(phone, 1)
                logger.info(f"Обновлено согласие (consent=1) для телефона {phone} после подтверждения профиля")

            await callback_query.message.answer("Авторизация успешна! 🎉")
            help_text = ("Вот команды пользователя: 📋\n"
                         "/profilReg - Зарегистрировать профиль ✍️\n"
                         "/reg - Записаться на мероприятие 📅\n"
                         "/profil - Посмотреть/изменить профиль 👤\n"
                         "/stats - Моя статистика 📊\n"
                         "/info - Информационные разделы 📖\n"
                         "/ask - Задать вопрос организаторам ❓\n"
                         "/help - Показать этот список ❓")
            await callback_query.message.answer(help_text)
            await state.clear()
            logger.info(f"Успешная авторизация пользователя с телефоном {phone}")
        except Exception as e:
            logger.error(f"Ошибка при авторизации для телефона {phone}: {e}")
            await callback_query.message.answer("Произошла ошибка. Попробуйте позже. ⚠️")
    else:
        await profil_reg_handler(callback_query.message, state)
    await callback_query.answer()


@user_router.callback_query(lambda c: c.data in ['consent_yes', 'consent_no'], ConsentStates.consent)
async def process_consent(callback_query: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    phone = data.get('phone')
    try:
        if callback_query.data == 'consent_yes':
            if phone:
                update_consent_by_phone(phone, 1)
            await callback_query.message.answer("Согласие принято! Авторизация успешна. 🎉")
            help_text = ("Вот команды пользователя: 📋\n"
                         "/profilReg - Зарегистрировать профиль ✍️\n"
                         "/reg - Записаться на мероприятие 📅\n"
                         "/profil - Посмотреть/изменить профиль 👤\n"
                         "/stats - Моя статистика 📊\n"
                         "/info - Информационные разделы 📖\n"
                         "/ask - Задать вопрос организаторам ❓\n"
                         "/help - Показать этот список ❓")
            await callback_query.message.answer(help_text)
            logger.info(
                f"Пользователь {phone or callback_query.from_user.id} принял согласие (ознакомление с PDF и /info)")
        else:
            await callback_query.message.answer("Без согласия бот не может работать. До свидания. 👋")
            logger.info(f"Пользователь {phone or callback_query.from_user.id} отказался от согласия")
        await state.clear()
    except Exception as e:
        logger.error(f"Ошибка при обновлении согласия для телефона {phone}: {e}")
        await callback_query.message.answer("Произошла ошибка. Попробуйте позже. ⚠️")
    await callback_query.answer()


@user_router.message(Command(commands=['profilReg']))
async def profil_reg_handler(message: types.Message, state: FSMContext):
    await state.set_state(ProfilRegStates.fio)
    keyboard = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="Назад 🔙")]],
        resize_keyboard=True
    )
    await message.answer("Введите ваше ФИО (только буквы и пробелы, минимум фамилия и имя): ✍️", reply_markup=keyboard)
    logger.info(f"Пользователь {message.from_user.id} начал регистрацию профиля")


@user_router.message(ProfilRegStates.fio)
async def process_fio(message: types.Message, state: FSMContext):
    if message.text == "Назад 🔙":
        await state.clear()
        await message.answer("Регистрация отменена.", reply_markup=types.ReplyKeyboardRemove())
        return
    fio = message.text.strip().title()
    if not re.match(r'^[А-Яа-яA-Za-z\s]+$', fio) or len(fio.split()) < 2:
        await message.answer(
            "ФИО должно содержать только буквы и пробелы, минимум два слова (фамилия и имя). Попробуйте снова. ⚠️")
        logger.warning(f"Некорректное ФИО от пользователя {message.from_user.id}: {fio}")
        return
    await state.update_data(fio=fio)
    # Проверка на существование пользователя с таким ФИО
    try:
        existing_user = get_user_by_fio(fio)
        if existing_user and existing_user[2] is None:  # Если phone None
            await state.update_data(existing_user_id=existing_user[0])
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="Да ✅", callback_data="previously_used_yes")
            keyboard.button(text="Нет ❌", callback_data="previously_used_no")
            await message.answer("Пользовались ли вы ботом ранее? (Мы нашли совпадение по ФИО без номера телефона)",
                                 reply_markup=keyboard.as_markup())
        else:
            await state.set_state(ProfilRegStates.category)
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="Студент 🎓", callback_data="cat_student")
            keyboard.button(text="Сотрудник 👔", callback_data="cat_employee")
            keyboard.button(text="Внешний донор 🌍", callback_data="cat_external")
            await message.answer("Выберите категорию: 📂", reply_markup=keyboard.as_markup())
    except Exception as e:
        logger.error(f"Ошибка при проверке ФИО {fio}: {e}")
        await message.answer("Произошла ошибка. Попробуйте позже. ⚠️")


@user_router.callback_query(lambda c: c.data in ['previously_used_yes', 'previously_used_no'])
async def process_previously_used(callback_query: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    telegram_id = callback_query.from_user.id
    phone = data.get('phone')
    fio = data.get('fio')
    existing_user_id = data.get


    if callback_query.data == 'previously_used_yes':
        # Проверяем, не занят ли номер телефона другим пользователем
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT id FROM users WHERE phone = ? AND id != ?', (phone, existing_user_id))
                if cursor.fetchone():
                    await callback_query.message.answer(
                        "Этот номер телефона уже используется другим пользователем. Пожалуйста, начните регистрацию заново с другим номером. ⚠️")
                    await state.clear()
                    logger.warning(f"Попытка обновления профиля по ФИО {fio} с занятым телефоном {phone}")
                    await callback_query.answer()
                    return
            # Переходим к заполнению остальных полей
            await state.set_state(ProfilRegStates.category)
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="Студент 🎓", callback_data="cat_student")
            keyboard.button(text="Сотрудник 👔", callback_data="cat_employee")
            keyboard.button(text="Внешний донор 🌍", callback_data="cat_external")
            await callback_query.message.answer("Выберите категорию: 📂", reply_markup=keyboard.as_markup())
            logger.info(
                f"Пользователь {telegram_id} подтвердил использование профиля по ФИО {fio}, переходит к выбору категории")
        except Exception as e:
            logger.error(f"Ошибка при проверке телефона {phone} для профиля по ФИО {fio}: {e}")
            await callback_query.message.answer("Произошла ошибка. Попробуйте позже. ⚠️")
    else:
        # Продолжаем как новый
        await state.set_state(ProfilRegStates.category)
        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="Студент 🎓", callback_data="cat_student")
        keyboard.button(text="Сотрудник 👔", callback_data="cat_employee")
        keyboard.button(text="Внешний донор 🌍", callback_data="cat_external")
        await callback_query.message.answer("Выберите категорию: 📂", reply_markup=keyboard.as_markup())
        logger.info(f"Пользователь {telegram_id} выбрал создание нового профиля для ФИО {fio}")
    await callback_query.answer()


@user_router.callback_query(lambda c: c.data.startswith('cat_'))
async def process_category(callback_query: types.CallbackQuery, state: FSMContext):
    category = callback_query.data.split('_')[1]
    await state.update_data(category=category)
    if category == 'student':
        await state.set_state(ProfilRegStates.group)
        keyboard = ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="Назад 🔙")]],
            resize_keyboard=True
        )
        await callback_query.message.answer("Введите номер группы (формат: Б21-302): 📚", reply_markup=keyboard)
    else:
        await state.set_state(ProfilRegStates.social_contacts)
        keyboard = ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="Назад 🔙")]],
            resize_keyboard=True
        )
        await callback_query.message.answer("Введите контакты в соцсетях (или 'нет'): 🔗", reply_markup=keyboard)
    await callback_query.answer()
    logger.info(f"Пользователь {callback_query.from_user.id} выбрал категорию: {category}")


@user_router.message(ProfilRegStates.group)
async def process_group(message: types.Message, state: FSMContext):
    if message.text == "Назад 🔙":
        await state.set_state(ProfilRegStates.fio)
        keyboard = ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="Назад 🔙")]],
            resize_keyboard=True
        )
        await message.answer("Введите ваше ФИО (только буквы и пробелы, минимум фамилия и имя): ✍️",
                             reply_markup=keyboard)
        return
    group = message.text.strip().upper()
    if not re.match(r'^[А-Я]\d{2}-\d{3}$', group):
        await message.answer("Неверный формат группы (пример: Б21-302). Попробуйте снова. ⚠️")
        logger.warning(f"Некорректный формат группы от пользователя {message.from_user.id}: {group}")
        return
    await state.update_data(group=group)
    await state.set_state(ProfilRegStates.social_contacts)
    keyboard = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="Назад 🔙")]],
        resize_keyboard=True
    )
    await message.answer("Введите контакты в соцсетях (или 'нет'): 🔗", reply_markup=keyboard)


@user_router.message(ProfilRegStates.social_contacts)
async def process_social_contacts(message: types.Message, state: FSMContext):
    if message.text == "Назад 🔙":
        data = await state.get_data()
        category = data.get('category')
        if category == 'student':
            await state.set_state(ProfilRegStates.group)
            keyboard = ReplyKeyboardMarkup(
                keyboard=[[KeyboardButton(text="Назад 🔙")]],
                resize_keyboard=True
            )
            await message.answer("Введите номер группы (формат: Б21-302): 📚", reply_markup=keyboard)
        else:
            await state.set_state(ProfilRegStates.fio)
            keyboard = ReplyKeyboardMarkup(
                keyboard=[[KeyboardButton(text="Назад 🔙")]],
                resize_keyboard=True
            )
            await message.answer("Введите ваше ФИО (только буквы и пробелы, минимум фамилия и имя): ✍️",
                                 reply_markup=keyboard)
        return
    social_contacts = message.text.strip() if message.text.strip().lower() != 'нет' else None
    data = await state.get_data()
    try:
        # Если пользователь выбрал "previously_used_yes", обновляем существующего
        if data.get('existing_user_id'):
            with get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''UPDATE users SET 
                    telegram_id = ?, phone = ?, category = ?, user_group = ?, social_contacts = ?, profile_status = 'pending'
                    WHERE id = ?''',
                               (message.from_user.id, data.get('phone'), data['category'], data.get('group'),
                                social_contacts, data['existing_user_id']))
                conn.commit()
            logger.info(
                f"Обновлён существующий профиль ID {data['existing_user_id']} для ФИО {data['fio']} с telegram_id {message.from_user.id}")
        else:
            # Создаём нового пользователя
            save_or_update_user(message.from_user.id, data.get('phone'), data['fio'],
                                data['category'], data.get('group'), social_contacts)
            logger.info(f"Создан или обновлён профиль для ФИО {data['fio']} с telegram_id {message.from_user.id}")
        await state.clear()
        await message.answer("Ваш профиль отправлен на модерацию. ⏳", reply_markup=types.ReplyKeyboardRemove())
    except Exception as e:
        logger.error(f"Ошибка при сохранении/обновлении профиля пользователя {data.get('fio', 'Unknown')}: {e}")
        await message.answer("Произошла ошибка при сохранении профиля. Попробуйте позже. ⚠️")


@user_router.message(Command(commands=['help']))
async def help_handler(message: types.Message):
    await message.answer("Вот команды пользователя:\n"
                         "/profilReg - Зарегистрировать профиль\n"
                         "/reg - Записаться на мероприятие\n"
                         "/profil - Посмотреть/изменить профиль\n"
                         "/stats - Моя статистика\n"
                         "/info - Информационные разделы\n"
                         "/ask - Задать вопрос организаторам ❓\n"
                         "/help - Показать этот список")
    logger.info(f"Пользователь {message.from_user.id} вызвал команду /help")


@user_router.message(Command(commands=['reg']))
async def reg_handler(message: types.Message):
    try:
        profile_status = get_profile_status_by_telegram_id(message.from_user.id)
        if not profile_status or profile_status != 'approved':
            await message.answer("Ваш профиль не одобрен или не существует. Пожалуйста, зарегистрируйтесь через /profilReg. ⚠️")
            logger.warning(f"Пользователь {message.from_user.id} не имеет одобренного профиля для регистрации на мероприятие")
            return
        events = get_active_events()
        if not events:
            await message.answer("Нет доступных мероприятий. 📅")
            logger.info(f"Пользователь {message.from_user.id} запросил регистрацию, но нет доступных мероприятий")
            return
        keyboard = InlineKeyboardBuilder()
        for event in events:
            if len(event) < 6:
                logger.error(f"Некорректные данные мероприятия: {event}")
                continue
            keyboard.button(text=f"{event[1]} {event[2]} - {event[4]} 📆", callback_data=f"reg_{event[0]}")
        await message.answer("Выберите мероприятие: 📋", reply_markup=keyboard.as_markup())
        logger.info(f"Пользователь {message.from_user.id} запросил регистрацию на мероприятие, найдено {len(events)} активных мероприятий")
    except Exception as e:
        logger.error(f"Ошибка при получении мероприятий для пользователя {message.from_user.id}: {e}")
        await message.answer("Произошла ошибка при загрузке мероприятий. Попробуйте позже. ⚠️")


@user_router.callback_query(lambda c: c.data.startswith('reg_'))
async def process_register(callback_query: types.CallbackQuery):
    event_id = int(callback_query.data.split('_')[1])
    user_id = callback_query.from_user.id
    try:
        db_user_id = get_user_id_by_telegram_id(user_id)
        if not db_user_id:
            await callback_query.answer("Пользователь не найден. Зарегистрируйтесь через /profilReg. ⚠️")
            logger.warning(f"Пользователь {user_id} не найден в базе при попытке регистрации на мероприятие {event_id}")
            return
        capacity = get_event_capacity(event_id)
        if capacity is None:
            await callback_query.answer("Мероприятие не найдено. ⚠️")
            logger.error(f"Мероприятие {event_id} не найдено")
            return
        registered_count = get_event_reg_count(event_id)
        if registered_count is None:
            await callback_query.answer("Ошибка при проверке регистраций. ⚠️")
            logger.error(f"Не удалось получить количество регистраций для мероприятия {event_id}")
            return
        if registered_count >= capacity:
            await callback_query.answer("Мероприятие заполнено. ❌")
            logger.warning(f"Мероприятие {event_id} заполнено (регистраций: {registered_count}, вместимость: {capacity})")
            return
        event_date = get_event_date(event_id)
        if not event_date:
            await callback_query.answer("Ошибка: некорректная дата мероприятия. ⚠️")
            logger.error(f"Дата мероприятия {event_id} не найдена")
            return
        try:
            reminder_date = (datetime.strptime(event_date, '%Y-%m-%d') - timedelta(days=1)).strftime('%Y-%m-%d')
        except ValueError:
            await callback_query.answer("Ошибка: некорректный формат даты мероприятия. ⚠️")
            logger.error(f"Некорректная дата мероприятия {event_id}: {event_date}")
            return
        add_registration(db_user_id, event_id)
        add_reminder(db_user_id, event_id, reminder_date)
        logger.info(f"Пользователь {user_id} (DB ID: {db_user_id}) успешно зарегистрирован на мероприятие {event_id}, добавлено напоминание на {reminder_date}")
        await callback_query.answer("Вы зарегистрированы! ✅")
    except Exception as e:
        logger.error(f"Ошибка при регистрации пользователя {user_id} на мероприятие {event_id}: {e}")
        await callback_query.answer("Произошла ошибка при регистрации. Попробуйте позже. ⚠️")


@user_router.message(Command(commands=['profil']))
async def profil_handler(message: types.Message, state: FSMContext):
    try:
        user = get_user_by_telegram_id(message.from_user.id)
        if not user:
            await message.answer("Профиль не найден. Пожалуйста, зарегистрируйтесь через /profilReg. ⚠️")
            logger.warning(f"Профиль пользователя {message.from_user.id} не найден")
            return
        if len(user) < 7:
            logger.error(f"Некорректные данные профиля для пользователя {message.from_user.id}: {user}")
            await message.answer("Ошибка в данных профиля. Обратитесь к администратору. ⚠️")
            return
        user_id = user[0]
        count_gavrilov = get_donations_count_by_center(user_id, "Гаврилова")
        count_fmba = get_donations_count_by_center(user_id, "ФМБА")
        sum_donations = count_gavrilov + count_fmba
        last_donation = get_last_donation(user_id)
        last_date_center = f"{last_donation[0]} / {last_donation[1]}" if last_donation else "Нет"
        history = get_donations_history(user_id)
        history_str = "\n".join([f"{d[0]} - {d[1]}" for d in history]) if history else "Нет истории"
        dkm_str = "Да" if user[5] else "Нет"  # user[5] - dkm (было user[6], исправлено на правильный индекс)
        response = (
            f"Ваш профиль: 📋\nФИО: {user[1]}\nКатегория: {user[2]}\nГруппа: {user[3] or 'Нет'}\n"
            f"Соцсети: {user[4] or 'Нет'} 🔗\nСтатус: {user[6]} ⚙️\n"
            f"Количество донаций: {sum_donations} 💉\nПоследняя донация: {last_date_center} 📅\n"
            f"Вступление в ДКМ: {dkm_str} 🦴\nИстория донаций:\n{history_str}")
        registrations = get_user_registrations(user_id)
        if registrations:
            response += "\n\nВаши текущие регистрации: 📅"
            keyboard = InlineKeyboardBuilder()
            for reg in registrations:
                if len(reg) < 4:
                    logger.error(f"Некорректные данные регистрации: {reg}")
                    continue
                response += f"\n- {reg[1]} {reg[2]} - {reg[3]}"
                keyboard.button(text=f"Отменить {reg[1]} ❌", callback_data=f"unreg_{reg[0]}")
            await message.answer(response, reply_markup=keyboard.as_markup())
        else:
            await message.answer(response)
        logger.info(f"Пользователь {message.from_user.id} запросил просмотр профиля")
    except Exception as e:
        logger.error(f"Ошибка при получении профиля пользователя {message.from_user.id}: {e}")
        await message.answer("Произошла ошибка при загрузке профиля. Попробуйте позже. ⚠️")


@user_router.callback_query(lambda c: c.data.startswith('unreg_'))
async def process_unreg(callback_query: types.CallbackQuery, state: FSMContext):
    event_id = int(callback_query.data.split('_')[1])
    user_id = callback_query.from_user.id
    try:
        db_user_id = get_user_id_by_telegram_id(user_id)
        if not db_user_id:
            await callback_query.answer("Пользователь не найден. ⚠️")
            logger.warning(f"Пользователь {user_id} не найден при отмене регистрации на мероприятие {event_id}")
            return
        cancel_registration(db_user_id, event_id)
        await state.set_state(CancelReasonState.reason)
        await state.update_data(reg_id=get_registration_id(db_user_id, event_id))
        keyboard = ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="Медотвод ⚕️")],
                      [KeyboardButton(text="Личные причины 👤")],
                      [KeyboardButton(text="Не захотел 😔")]],
            resize_keyboard=True
        )
        await callback_query.message.answer("Регистрация отменена. Пожалуйста, укажите причину отмены:",
                                            reply_markup=keyboard)
        logger.info(f"Пользователь {user_id} отменил регистрацию на мероприятие {event_id}, запрошенная причина")
    except Exception as e:
        logger.error(f"Ошибка при отмене регистрации пользователя {user_id} на {event_id}: {e}")
        await callback_query.answer("Произошла ошибка. Попробуйте позже. ⚠️")


def get_registration_id(user_id, event_id):
    # Используем get_connection из db.py
    from src.database.db import get_connection
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT id FROM registrations WHERE user_id = ? AND event_id = ?', (user_id, event_id))
        result = cursor.fetchone()
        return result[0] if result else None


@user_router.message(CancelReasonState.reason)
async def process_cancel_reason(message: types.Message, state: FSMContext):
    reason = message.text
    data = await state.get_data()
    reg_id = data.get('reg_id')
    if reg_id:
        add_non_attendance_reason(reg_id, reason)
        await message.answer("Причина отмены записана. Спасибо!", reply_markup=types.ReplyKeyboardRemove())
        logger.info(f"Записана причина отмены для reg {reg_id}: {reason}")
    else:
        logger.warning(f"Не найден reg_id для отмены регистрации пользователем {message.from_user.id}")
        await message.answer("Ошибка при записи причины отмены. Попробуйте снова. ⚠️")
    await state.clear()


@user_router.message(Command(commands=['stats']))
async def stats_handler(message: types.Message):
    try:
        user_id = get_user_id_by_telegram_id(message.from_user.id)
        if user_id:
            reg_count = get_user_registrations_count(user_id)
            await message.answer(f"Ваша статистика: 📊\nЗарегистрировано на мероприятий: {reg_count} 📅")
            logger.info(f"Пользователь {message.from_user.id} запросил статистику: {reg_count} регистраций")
        else:
            await message.answer("Вы не зарегистрированы. Пожалуйста, используйте /profilReg. ⚠️")
            logger.warning(f"Пользователь {message.from_user.id} не зарегистрирован")
    except Exception as e:
        logger.error(f"Ошибка при получении статистики пользователя {message.from_user.id}: {e}")
        await message.answer("Произошла ошибка. Попробуйте позже. ⚠️")


@user_router.message(Command(commands=['ask']))
async def ask_handler(message: types.Message, state: FSMContext):
    profile_status = get_profile_status_by_telegram_id(message.from_user.id)
    if not profile_status or profile_status != 'approved':
        await message.answer("Ваш профиль не одобрен или не существует. Пожалуйста, зарегистрируйтесь через /profilReg. ⚠️")
        return
    await state.set_state(AskQuestionState.text)
    keyboard = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="Назад 🔙")]],
        resize_keyboard=True
    )
    await message.answer("Введите ваш вопрос или сообщение организаторам:", reply_markup=keyboard)


@user_router.message(AskQuestionState.text)
async def process_ask_text(message: types.Message, state: FSMContext):
    # Локальный импорт bot внутри функции
    from src.bot import bot
    if message.text == "Назад 🔙":
        await state.clear()
        await message.answer("Операция отменена.", reply_markup=types.ReplyKeyboardRemove())
        return
    text = message.text.strip()
    if not text:
        await message.answer("Сообщение не может быть пустым. Попробуйте снова.")
        return
    try:
        user_id = get_user_id_by_telegram_id(message.from_user.id)
        if not user_id:
            await message.answer("Пользователь не найден. Пожалуйста, зарегистрируйтесь через /profilReg. ⚠️")
            logger.warning(f"Пользователь {message.from_user.id} не найден при попытке задать вопрос")
            return
        question_id = add_question(user_id, text)
        await message.answer("Ваш вопрос отправлен организаторам. Они ответят в ближайшее время.",
                             reply_markup=types.ReplyKeyboardRemove())
        admins = [123456789, 1653833795, 1191457973]
        for admin_id in admins:
            try:
                await bot.send_message(admin_id,
                                       f"Новый вопрос от пользователя ID {user_id}: {text}\nОтветьте через /answer")
            except Exception as e:
                logger.error(f"Ошибка при отправке уведомления админу {admin_id}: {e}")
        logger.info(f"Пользователь {message.from_user.id} задал вопрос: {text}")
    except Exception as e:
        logger.error(f"Ошибка при отправке вопроса от {message.from_user.id}: {e}")
        await message.answer("Произошла ошибка. Попробуйте позже.")
    await state.clear()


def register_user_handlers(dp):
    dp.include_router(user_router)