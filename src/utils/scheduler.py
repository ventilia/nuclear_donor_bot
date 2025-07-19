import asyncio
import schedule
from datetime import datetime, timedelta

from aiogram.utils.keyboard import InlineKeyboardBuilder

from src.database.db import get_reminders_to_send, get_event_by_id, delete_reminder
from src.database.db import get_past_events, get_non_attended_registrations
from src.database.db import logger

async def check_reminders():
    # Локальный импорт bot внутри функции для избежания циклического импорта
    from src.bot import bot
    try:
        current_date = datetime.now().strftime('%Y-%m-%d')
        reminders = get_reminders_to_send(current_date)
        for reminder in reminders:
            user_id = reminder[1]
            event_id = reminder[2]
            event = get_event_by_id(event_id)
            if event:
                await bot.send_message(user_id, f"Напоминание: {event[0]} {event[1]} в {event[2]} скоро начнется! ⏰")
                logger.info(f"Отправлено напоминание пользователю {user_id} для мероприятия {event_id}")
            delete_reminder(reminder[0])
    except Exception as e:
        logger.error(f"Ошибка при проверке напоминаний: {e}")

async def check_non_attendance():
    # Локальный импорт bot внутри функции для избежания циклического импорта
    from src.bot import bot
    try:
        today = datetime.now().strftime('%Y-%m-%d')
        past_events = get_past_events(today)
        for event in past_events:
            event_id = event[0]
            non_attended = get_non_attended_registrations(event_id)
            for reg in non_attended:
                reg_id = reg[0]
                user_id = reg[1]
                keyboard = InlineKeyboardBuilder()
                keyboard.button(text="Медотвод ⚕️", callback_data=f"reason_med_{reg_id}")
                keyboard.button(text="Личные причины 👤", callback_data=f"reason_personal_{reg_id}")
                keyboard.button(text="Не захотел 😔", callback_data=f"reason_no_{reg_id}")
                await bot.send_message(user_id, "Вы зарегистрировались на прошедшее мероприятие, но не пришли. Укажите причину: ❓", reply_markup=keyboard.as_markup())
                logger.info(f"Отправлен опрос неявки пользователю {user_id} для reg {reg_id}")
    except Exception as e:
        logger.error(f"Ошибка при проверке неявок: {e}")

async def schedule_checker():
    while True:
        schedule.run_pending()
        await asyncio.sleep(1)

async def start_scheduler():
    schedule.every(10).minutes.do(lambda: asyncio.create_task(check_reminders()))
    schedule.every().day.at("00:00").do(lambda: asyncio.create_task(check_non_attendance()))
    await schedule_checker()