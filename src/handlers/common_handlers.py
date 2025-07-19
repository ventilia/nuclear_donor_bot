from aiogram import types, Router, Dispatcher
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder

from src.states.states import ProfilEditStates
from src.database.db import logger, add_non_attendance_reason

common_router = Router()

@common_router.message(Command(commands=['info']))
async def info_handler(message: types.Message):
    keyboard = InlineKeyboardBuilder()
    keyboard.button(text="О донорстве крови", callback_data="info_blood")
    keyboard.button(text="О донорстве костного мозга", callback_data="info_bone")
    keyboard.button(text="О донациях в МИФИ", callback_data="info_mifi")
    await message.answer("Выберите раздел информации: 📖", reply_markup=keyboard.as_markup())
    logger.info(f"Пользователь {message.from_user.id} запросил информационные разделы")

@common_router.callback_query(lambda c: c.data.startswith('info_'))
async def process_info(callback_query: types.CallbackQuery):
    section_map = {
        'blood': 'src/info_texts/blood_donation.txt',
        'bone': 'src/info_texts/bone_marrow_donation.txt',
        'mifi': 'src/info_texts/mifi_donations.txt'
    }
    file_name = section_map.get(callback_query.data.split('_')[1])
    if not file_name:
        await callback_query.answer("Некорректный раздел. ⚠️")
        return
    try:
        with open(file_name, 'r', encoding='utf-8') as f:
            text = f.read()
        await callback_query.message.answer(text)
        logger.info(f"Пользователь {callback_query.from_user.id} просмотрел раздел из {file_name}")
    except FileNotFoundError:
        await callback_query.message.answer("Текст раздела не найден. ⚠️")
        logger.warning(f"Файл {file_name} не найден")
    except Exception as e:
        logger.error(f"Ошибка при чтении файла {file_name}: {e}")
        await callback_query.answer("Произошла ошибка. Попробуйте позже. ⚠️")
    await callback_query.answer()

@common_router.callback_query(lambda c: c.data.startswith('reason_'))
async def process_non_attendance_reason(callback_query: types.CallbackQuery):
    parts = callback_query.data.split('_')
    reason_type = parts[1]
    reg_id = int(parts[2])
    reason_map = {'med': 'медотвод', 'personal': 'личные причины', 'no': 'не захотел'}
    reason = reason_map.get(reason_type)
    if not reason:
        await callback_query.answer("Некорректная причина. ⚠️")
        return
    try:
        add_non_attendance_reason(reg_id, reason)
        await callback_query.answer("Причина записана. ✅")
        logger.info(f"Записана причина неявки для reg {reg_id}: {reason}")
    except Exception as e:
        logger.error(f"Ошибка записи причины неявки для reg {reg_id}: {e}")
        await callback_query.answer("Ошибка. Попробуйте позже. ⚠️")

# Функция регистрации хендлеров
def register_common_handlers(dp: Dispatcher):
    dp.include_router(common_router)