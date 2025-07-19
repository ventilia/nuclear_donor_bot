from aiogram.fsm.state import State, StatesGroup

class ProfilRegStates(StatesGroup):
    phone_confirm = State()
    fio = State()  # Объединённое поле для ФИО
    category = State()
    group = State()
    social_contacts = State()

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

class AskQuestionState(StatesGroup):
    text = State()

class AnswerQuestionState(StatesGroup):
    select = State()
    response = State()

class CancelReasonState(StatesGroup):
    reason = State()

class BroadcastState(StatesGroup):
    text = State()
    photo = State()
    confirm = State()

class AddAdminState(StatesGroup):  # Новое состояние для добавления админа
    telegram_id = State()
    confirm = State()

class RestoreState(StatesGroup):  # Новое состояние для восстановления бекапа
    file = State()
    confirm = State()