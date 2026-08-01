import asyncio
import logging
import sys
import os
import re
from datetime import datetime
from typing import Optional, List, Dict, Tuple

from aiogram import Bot, Dispatcher, types, F, Router
from aiogram.filters import Command, StateFilter, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, User
from aiogram.utils.keyboard import InlineKeyboardBuilder
from supabase import create_client, Client
from dotenv import load_dotenv

# Загрузка переменных окружения
load_dotenv()

# ====================== КОНФИГУРАЦИЯ ======================
TOKEN = "ТОКЕН_ТВОЕГО_НОВОГО_БОТА"
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
OWNER_ID = int(os.getenv("MAIN_ADMIN_ID"))

BOT_ID = "sparks"        # ID текущего бота
MAIN_BOT_ID = "main"     # ID основного бота (для Лун)

# КУРС: 1 Луна = 2 Искры (База: Луна=1.0, Искра=0.5)
EXCHANGE_RATES = {
    "main": 1.0,   # 🌗 Луны
    "sparks": 0.5  # ✨ Искры
}

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ====================== БАЗА ДАННЫХ ======================
class Database:
    def __init__(self):
        self.supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

    def get_user(self, user_id: int) -> Optional[Dict]:
        res = self.supabase.table('users').select('*').eq('user_id', user_id).execute()
        return res.data[0] if res.data else None

    def create_or_update_user(self, user: User):
        data = {'user_id': user.id, 'username': user.username, 'name': user.full_name}
        existing = self.get_user(user.id)
        if existing:
            self.supabase.table('users').update(data).eq('user_id', user.id).execute()
        else:
            data['created_at'] = datetime.now().isoformat()
            self.supabase.table('users').insert(data).execute()

    def get_balance(self, user_id: int, bot_id: str = BOT_ID) -> float:
        res = self.supabase.table('balances').select('*').eq('user_id', user_id).eq('bot_id', bot_id).execute()
        if not res.data: return 0
        row = res.data[0]
        return float('inf') if row['is_infinite'] else float(row['balance'])

    def set_balance(self, user_id: int, amount: float, bot_id: str = BOT_ID):
        is_inf = amount == float('inf')
        val = 0 if is_inf else amount
        existing = self.supabase.table('balances').select('*').eq('user_id', user_id).eq('bot_id', bot_id).execute()
        if existing.data:
            self.supabase.table('balances').update({'balance': val, 'is_infinite': is_inf}).eq('user_id', user_id).eq('bot_id', bot_id).execute()
        else:
            self.supabase.table('balances').insert({'user_id': user_id, 'bot_id': bot_id, 'balance': val, 'is_infinite': is_inf}).execute()

    def get_bot_data(self, user_id: int) -> Dict:
        res = self.supabase.table('user_bot_data').select('*').eq('user_id', user_id).eq('bot_id', BOT_ID).execute()
        if res.data: return res.data[0]
        return {'is_frozen': False}

    def set_bot_data(self, user_id: int, **kwargs):
        existing = self.supabase.table('user_bot_data').select('*').eq('user_id', user_id).eq('bot_id', BOT_ID).execute()
        if existing.data:
            self.supabase.table('user_bot_data').update(kwargs).eq('user_id', user_id).eq('bot_id', BOT_ID).execute()
        else:
            data = {'user_id': user_id, 'bot_id': BOT_ID, **kwargs}
            self.supabase.table('user_bot_data').insert(data).execute()

    def get_top_users(self):
        res = self.supabase.table('balances').select('user_id, balance, is_infinite').eq('bot_id', BOT_ID).order('balance', desc=True).execute()
        return [u for u in res.data if not u['is_infinite']][:10]

    def find_user(self, input_str: str) -> Optional[int]:
        input_str = input_str.strip().lstrip('@').lower()
        try:
            uid = int(input_str)
            if self.get_user(uid): return uid
        except: pass
        res = self.supabase.table('users').select('user_id').or_(f'username.ilike.%{input_str}%,name.ilike.%{input_str}%').execute()
        return res.data[0]['user_id'] if res.data else None

db = Database()

# ====================== СОСТОЯНИЯ ======================
class EconomyStates(StatesGroup):
    WaitingConvertAmount = State()
    WaitingAdminAction = State()

# ====================== КЛАВИАТУРЫ ======================
def main_menu(user_id: int):
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="✨ Баланс", callback_data="bal"),
                InlineKeyboardButton(text="🏆 Топ богачей", callback_data="top"))
    builder.row(InlineKeyboardButton(text="💱 Конвертация", callback_data="convert"))
    if user_id == OWNER_ID:
        builder.row(InlineKeyboardButton(text="⚙️ Админ-панель", callback_data="admin"))
    return builder.as_markup()

def cancel_kb():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="back_main")]])

# ====================== ХЭНДЛЕРЫ ======================
router = Router()

@router.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    db.create_or_update_user(message.from_user)
    if message.from_user.id == OWNER_ID:
        db.set_balance(message.from_user.id, float('inf'))
    await message.answer(f"✨ Добро пожаловать в систему Искр!\n\nИспользуйте меню или команды:\n• `перевести @ник сумма`", 
                         reply_markup=main_menu(message.from_user.id), parse_mode="Markdown")

@router.callback_query(F.data == "back_main")
async def call_back(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("✨ Главное меню системы Искр:", reply_markup=main_menu(callback.from_user.id))

@router.callback_query(F.data == "bal")
async def call_bal(callback: types.CallbackQuery):
    bal_sparks = db.get_balance(callback.from_user.id, BOT_ID)
    bal_moons = db.get_balance(callback.from_user.id, MAIN_BOT_ID)
    data = db.get_bot_data(callback.from_user.id)
    
    status = "❄️ Счёт ЗАМОРОЖЕН" if data.get('is_frozen') else "✅ Активен"
    text = (f"💳 Ваши счета:\n\n"
            f"✨ Искры: {bal_sparks:.0f}\n"
            f"🌗 Луны: {bal_moons:.0f}\n\n"
            f"Статус: {status}")
    await callback.message.edit_text(text, reply_markup=main_menu(callback.from_user.id))

@router.callback_query(F.data == "top")
async def call_top(callback: types.CallbackQuery):
    top = db.get_top_users()
    text = "🏆 Топ владельцев Искр ✨:\n\n"
    for i, u in enumerate(top, 1):
        user = db.get_user(u['user_id'])
        name = f"@{user['username']}" if user['username'] else user['name']
        text += f"{i}. {name} — {u['balance']:.0f} ✨\n"
    await callback.message.edit_text(text or "Пока пусто", reply_markup=main_menu(callback.from_user.id))

# ====================== ПЕРЕВОДЫ ТЕКСТОМ ======================
@router.message(F.text.regexp(r'(?i)^перевести\s+(.+)\s+(\d+)$'))
async def fast_transfer(message: types.Message):
    match = re.match(r'(?i)^перевести\s+(.+)\s+(\d+)$', message.text)
    receiver_input, amount = match.group(1).strip(), int(match.group(2))
    
    if db.get_bot_data(message.from_user.id).get('is_frozen'):
        return await message.reply("❄️ Ваш счёт заморожен.")

    target_id = db.find_user(receiver_input)
    if not target_id or target_id == message.from_user.id:
        return await message.reply("❌ Пользователь не найден.")

    if db.get_bot_data(target_id).get('is_frozen'):
        return await message.reply("❄️ Счёт получателя заморожен.")

    sender_bal = db.get_balance(message.from_user.id)
    if sender_bal != float('inf') and sender_bal < amount:
        return await message.reply("❌ Недостаточно ✨ Искр.")

    if sender_bal != float('inf'):
        db.set_balance(message.from_user.id, sender_bal - amount)
    db.set_balance(target_id, db.get_balance(target_id) + amount)

    await message.reply(f"✅ Переведено {amount} ✨!")
    try: await message.bot.send_message(target_id, f"✨ Вам пришло {amount} искр от @{message.from_user.username or message.from_user.id}")
    except: pass

# ====================== КОНВЕРТАЦИЯ ======================
@router.callback_query(F.data == "convert")
async def convert_menu(callback: types.CallbackQuery):
    if db.get_bot_data(callback.from_user.id).get('is_frozen'):
        return await callback.answer("❄️ Счёт заморожен!", show_alert=True)
    
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="✨ Искры -> 🌗 Луны", callback_data="conv_to_main"))
    builder.row(InlineKeyboardButton(text="🌗 Луны -> ✨ Искры", callback_data="conv_from_main"))
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="back_main"))
    
    rate_text = f"📊 Курс: 1 🌗 = 2 ✨"
    await callback.message.edit_text(f"{rate_text}\n\nВыберите направление:", reply_markup=builder.as_markup())

@router.callback_query(F.data.startswith("conv_"))
async def convert_start(callback: types.CallbackQuery, state: FSMContext):
    direction = callback.data
    await state.update_data(direction=direction)
    source = "✨ Искр" if direction == "conv_to_main" else "🌗 Лун"
    await callback.message.edit_text(f"Введите количество {source} для обмена:", reply_markup=cancel_kb())
    await state.set_state(EconomyStates.WaitingConvertAmount)

@router.message(EconomyStates.WaitingConvertAmount)
async def convert_process(message: types.Message, state: FSMContext):
    if not message.text.isdigit(): return await message.answer("Введите число!")
    amount = int(message.text)
    data = await state.get_data()
    uid = message.from_user.id

    from_bot = "sparks" if data['direction'] == "conv_to_main" else "main"
    to_bot = "main" if data['direction'] == "conv_to_main" else "sparks"
    
    source_bal = db.get_balance(uid, from_bot)
    if source_bal == float('inf'): return await message.answer("❌ Недоступно для владельцев.")
    if source_bal < amount: return await message.answer("❌ Недостаточно средств.")

    res_amount = amount * (EXCHANGE_RATES[from_bot] / EXCHANGE_RATES[to_bot])
    
    db.set_balance(uid, source_bal - amount, from_bot)
    db.set_balance(uid, db.get_balance(uid, to_bot) + res_amount, to_bot)

    await message.answer(f"✅ Обмен завершен!\nПолучено: {res_amount:.0f}", reply_markup=main_menu(uid))
    await state.clear()

# ====================== АДМИНКА ======================
@router.callback_query(F.data == "admin")
async def call_admin(callback: types.CallbackQuery):
    if callback.from_user.id != OWNER_ID: return
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="❄️ Заморозить", callback_data="adm_freeze"),
                InlineKeyboardButton(text="🔥 Разморозить", callback_data="adm_unfreeze"))
    builder.row(InlineKeyboardButton(text="💰 Выдать искры", callback_data="adm_give"))
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="back_main"))
    await callback.message.edit_text("⚙️ Админ-панель:", reply_markup=builder.as_markup())

@router.callback_query(F.data.startswith("adm_"))
async def adm_action(callback: types.CallbackQuery, state: FSMContext):
    action = callback.data[4:]
    await state.update_data(action=action)
    prompt = "выдать искры (ID Сумма):" if action == "give" else "введите ID/Ник для действия:"
    await callback.message.edit_text(prompt, reply_markup=cancel_kb())
    await state.set_state(EconomyStates.WaitingAdminAction)

@router.message(EconomyStates.WaitingAdminAction)
async def adm_process(message: types.Message, state: FSMContext):
    data = await state.get_data()
    try:
        if data['action'] == "give":
            parts = message.text.split()
            uid, amt = db.find_user(parts[0]), int(parts[1])
            db.set_balance(uid, db.get_balance(uid) + amt)
            await message.answer(f"✅ Выдано {amt} ✨")
        else:
            uid = db.find_user(message.text)
            status = (data['action'] == "freeze")
            db.set_bot_data(uid, is_frozen=status)
            await message.answer(f"✅ Статус изменен.")
    except: await message.answer("❌ Ошибка.")
    await state.clear()

# ====================== ЗАПУСК ======================
async def main():
    bot = Bot(token=TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    logger.info("✨ Бот Искр запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
