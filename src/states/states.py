from aiogram.fsm.state import State, StatesGroup

class ProfilRegStates(StatesGroup):
    phone_confirm = State()
    name = State()
    surname = State()
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