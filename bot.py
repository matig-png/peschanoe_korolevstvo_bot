import asyncio
import logging
import os
import re
from datetime import datetime
from typing import Optional, Dict, Any, List

from aiogram import Bot, Dispatcher, types, F, Router
from aiogram.filters import Command, StateFilter, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, User, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

# ====================== КОНФИГУРАЦИЯ ======================
TOKEN = "ТОКЕН_ТВОЕГО_БОТА_ИСКР"
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
OWNER_ID = int(os.getenv("MAIN_ADMIN_ID")) # Твой ID из .env

BOT_ID = "sparks"        
MAIN_BOT_ID = "main"     
STARTING_BALANCE = 400   

# КУРС: 1 Луна = 2 Искры
EXCHANGE_RATES = {"main": 1.0, "sparks": 0.5}

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

    def get_balance(self, user_id: int, b_id: str) -> float:
        res = self.supabase.table('balances').select('*').eq('user_id', user_id).eq('bot_id', b_id).execute()
        if not res.data: return None
        row = res.data[0]
        return float('inf') if row['is_infinite'] else float(row['balance'])

    def set_balance(self, user_id: int, amount: float, b_id: str):
        is_inf = (amount == float('inf'))
        val = 0 if is_inf else amount
        existing = self.supabase.table('balances').select('*').eq('user_id', user_id).eq('bot_id', b_id).execute()
        if existing.data:
            self.supabase.table('balances').update({'balance': val, 'is_infinite': is_inf}).eq('user_id', user_id).eq('bot_id', b_id).execute()
        else:
            self.supabase.table('balances').insert({'user_id': user_id, 'bot_id': b_id, 'balance': val, 'is_infinite': is_inf}).execute()

    def get_bot_data(self, user_id: int) -> Dict:
        res = self.supabase.table('user_bot_data').select('*').eq('user_id', user_id).eq('bot_id', BOT_ID).execute()
        return res.data[0] if res.data else {'is_frozen': False}

    def set_bot_data(self, user_id: int, **kwargs):
        existing = self.supabase.table('user_bot_data').select('*').eq('user_id', user_id).eq('bot_id', BOT_ID).execute()
        if existing.data:
            self.supabase.table('user_bot_data').update(kwargs).eq('user_id', user_id).eq('bot_id', BOT_ID).execute()
        else:
            data = {'user_id': user_id, 'bot_id': BOT_ID, **kwargs}
            self.supabase.table('user_bot_data').insert(data).execute()

    def find_user(self, input_str: str) -> Optional[int]:
        input_str = input_str.strip().lstrip('@').lower()
        try:
            uid = int(input_str)
            if self.get_user(uid): return uid
        except: pass
        res = self.supabase.table('users').select('user_id').or_(f'username.ilike.%{input_str}%,name.ilike.%{input_str}%').execute()
        return res.data[0]['user_id'] if res.data else None

    def get_top_sparks(self):
        res = self.supabase.table('balances').select('user_id, balance, is_infinite').eq('bot_id', BOT_ID).order('balance', desc=True).limit(10).execute()
        return [u for u in res.data if not u['is_infinite']]

db = Database()

# ====================== СОСТОЯНИЯ ======================
class EconomyStates(StatesGroup):
    WaitingConvertAmount = State()
    WaitingAdminAction = State()

# ====================== ФИЛЬТРЫ ======================
async def is_owner_filter(callback: CallbackQuery):
    if callback.message.reply_to_message:
        if callback.from_user.id != callback.message.reply_to_message.from_user.id:
            await callback.answer("❌ Это не ваше меню! Вызовите своё через /start", show_alert=True)
            return False
    return True

# ====================== КЛАВИАТУРЫ ======================
def main_menu(user_id: int):
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="✨ Мой Баланс", callback_data="bal"),
                InlineKeyboardButton(text="🏆 Топ Искр", callback_data="top"))
    builder.row(InlineKeyboardButton(text="💱 Обмен валют", callback_data="conv"))
    if user_id == OWNER_ID:
        builder.row(InlineKeyboardButton(text="⚙️ Админ-панель", callback_data="adm"))
    return builder.as_markup()

# ====================== ХЭНДЛЕРЫ ======================
router = Router()

@router.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    db.create_or_update_user(message.from_user)
    db.set_bot_data(message.from_user.id, activated_at=datetime.now().isoformat())
    
    # Регистрация баланса (для всех, включая владельца)
    sp_bal = db.get_balance(message.from_user.id, BOT_ID)
    if sp_bal is None:
        db.set_balance(message.from_user.id, STARTING_BALANCE, BOT_ID)
        await message.answer(f"🎁 Вам начислено приветственные {STARTING_BALANCE} ✨ Искр!")

    await message.reply(f"✨ **Добро пожаловать в систему Искр!**\n\nВы зашли как: {'Администратор' if message.from_user.id == OWNER_ID else 'Пользователь'}\n\nКоманда для перевода:\n`перевести @ник сумма`", 
                         reply_markup=main_menu(message.from_user.id), parse_mode="Markdown")

@router.callback_query(F.data == "back_main")
async def call_back(callback: types.CallbackQuery, state: FSMContext):
    if not await is_owner_filter(callback): return
    await state.clear()
    await callback.message.edit_text("✨ Главное меню системы Искр:", reply_markup=main_menu(callback.from_user.id))

# --- БАЛАНС ---
@router.callback_query(F.data == "bal")
async def call_bal(callback: types.CallbackQuery):
    if not await is_owner_filter(callback): return
    s_bal = db.get_balance(callback.from_user.id, BOT_ID) or 0
    m_bal = db.get_balance(callback.from_user.id, MAIN_BOT_ID) or 0
    status = "❄️ ЗАМОРОЖЕН" if db.get_bot_data(callback.from_user.id).get('is_frozen') else "✅ Активен"
    
    s_str = "∞" if s_bal == float('inf') else f"{s_bal:.0f}"
    m_str = "∞" if m_bal == float('inf') else f"{m_bal:.0f}"
    
    await callback.message.edit_text(
        f"💳 **Ваши счета:**\n\n"
        f"✨ Искры: `{s_str}`\n"
        f"🌗 Луны: `{m_str}`\n\n"
        f"Статус счета: {status}", 
        reply_markup=main_menu(callback.from_user.id), parse_mode="Markdown"
    )

# --- ТОП ---
@router.callback_query(F.data == "top")
async def call_top(callback: types.CallbackQuery):
    if not await is_owner_filter(callback): return
    top = db.get_top_sparks()
    text = "🏆 **Богатейшие (✨ Искры):**\n\n"
    for i, u in enumerate(top, 1):
        user = db.get_user(u['user_id'])
        name = f"@{user['username']}" if user and user['username'] else f"ID:{u['user_id']}"
        text += f"{i}. {name} — `{u['balance']:.0f}` ✨\n"
    await callback.message.edit_text(text or "Топ пуст", reply_markup=main_menu(callback.from_user.id), parse_mode="Markdown")

# ====================== ПЕРЕВОДЫ (ВЕЗДЕ) ======================
@router.message(F.text.regexp(r'(?i)^перевести\s+(.+)\s+(\d+)$'))
async def fast_transfer(message: types.Message):
    match = re.match(r'(?i)^перевести\s+(.+)\s+(\d+)$', message.text)
    who, amount = match.group(1).strip(), int(match.group(2))
    sender_id = message.from_user.id

    if amount <= 0: return await message.reply("❌ Сумма должна быть больше нуля.")
    if db.get_bot_data(sender_id).get('is_frozen'):
        return await message.reply("❄️ Ваш счёт заморожен!")

    target_id = db.find_user(who)
    if not target_id or target_id == sender_id:
        return await message.reply("❌ Пользователь не найден или вы указали себя.")

    if db.get_bot_data(target_id).get('is_frozen'):
        return await message.reply("❄️ Счёт получателя заморожен.")

    s_bal = db.get_balance(sender_id, BOT_ID) or 0
    if s_bal != float('inf') and s_bal < amount:
        return await message.reply(f"❌ Недостаточно ✨ Искр.")

    if s_bal != float('inf'):
        db.set_balance(sender_id, s_bal - amount, BOT_ID)
    
    t_bal = db.get_balance(target_id, BOT_ID) or 0
    if t_bal != float('inf'):
        db.set_balance(target_id, t_bal + amount, BOT_ID)

    t_user = db.get_user(target_id)
    t_name = f"@{t_user['username']}" if t_user and t_user['username'] else f"ID:{target_id}"
    await message.reply(f"✅ Успешно переведено `{amount}` ✨ пользователю {t_name}", parse_mode="Markdown")
    
    try:
        await message.bot.send_message(target_id, f"✨ Вам пришло `{amount}` искр от {message.from_user.full_name}")
    except: pass

# ====================== КОНВЕРТАЦИЯ ======================
@router.callback_query(F.data == "conv")
async def call_conv(callback: types.CallbackQuery):
    if not await is_owner_filter(callback): return
    if db.get_bot_data(callback.from_user.id).get('is_frozen'):
        return await callback.answer("❄️ Ваш счет заморожен!", show_alert=True)
    
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="✨ Искры -> 🌗 Луны", callback_data="c_to_m"),
           InlineKeyboardButton(text="🌗 Луны -> ✨ Искры", callback_data="c_fr_m"))
    kb.row(InlineKeyboardButton(text="◀️ Назад", callback_data="back_main"))
    await callback.message.edit_text("💱 **Обмен валют**\n\nКурс: 1 🌗 = 2 ✨", reply_markup=kb.as_markup(), parse_mode="Markdown")

@router.callback_query(F.data.startswith("c_"))
async def conv_start(callback: types.CallbackQuery, state: FSMContext):
    if not await is_owner_filter(callback): return
    await state.update_data(dir=callback.data)
    src = "✨ Искр" if callback.data == "c_to_m" else "🌗 Лун"
    await callback.message.edit_text(f"Введите сумму {src} для обмена:", 
                                     reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="back_main")]]))
    await state.set_state(EconomyStates.WaitingConvertAmount)

@router.message(EconomyStates.WaitingConvertAmount)
async def conv_proc(message: types.Message, state: FSMContext):
    if not message.text.isdigit(): return await message.answer("Введите целое число.")
    amt = int(message.text)
    data = await state.get_data()
    uid = message.from_user.id

    f_bot = "sparks" if data['dir'] == "c_to_m" else "main"
    t_bot = "main" if data['dir'] == "c_to_m" else "sparks"
    
    s_bal = db.get_balance(uid, f_bot) or 0
    if s_bal == float('inf'): 
        return await message.answer("❌ Ошибка: Нельзя конвертировать бесконечность.")
    if s_bal < amt: 
        return await message.answer("❌ Недостаточно средств.")

    res = amt * (EXCHANGE_RATES[f_bot] / EXCHANGE_RATES[t_bot])
    db.set_balance(uid, s_bal - amt, f_bot)
    
    target_bal = db.get_balance(uid, t_bot) or 0
    if target_bal != float('inf'):
        db.set_balance(uid, target_bal + res, t_bot)

    await message.answer(f"✅ Обмен завершен! Получено: `{res:.0f}`", reply_markup=main_menu(uid), parse_mode="Markdown")
    await state.clear()

# ====================== АДМИНКА (ТОЛЬКО ПО OWNER_ID) ======================
@router.callback_query(F.data == "adm")
async def call_adm(callback: types.CallbackQuery):
    if callback.from_user.id != OWNER_ID: return
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="❄️ Заморозить", callback_data="a_frz"),
           InlineKeyboardButton(text="🔥 Разморозить", callback_data="a_unf"))
    kb.row(InlineKeyboardButton(text="💰 Выдать искры ✨", callback_data="a_give"))
    kb.row(InlineKeyboardButton(text="◀️ Назад", callback_data="back_main"))
    await callback.message.edit_text("⚙️ **Админ-панель владельца**", reply_markup=kb.as_markup(), parse_mode="Markdown")

@router.callback_query(F.data.startswith("a_"), F.from_user.id == OWNER_ID)
async def adm_act(callback: types.CallbackQuery, state: FSMContext):
    act = callback.data[2:]
    await state.update_data(act=act)
    p = "💰 Введите `@ник Сумма`:" if act == "give" else "❄️ Введите `@ник` пользователя:"
    await callback.message.edit_text(p, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="adm")]]))
    await state.set_state(EconomyStates.WaitingAdminAction)

@router.message(EconomyStates.WaitingAdminAction, F.from_user.id == OWNER_ID)
async def adm_end(message: types.Message, state: FSMContext):
    d = await state.get_data()
    try:
        if d['act'] == "give":
            parts = message.text.split()
            u_id, a = db.find_user(parts[0]), int(parts[1])
            if u_id:
                curr = db.get_balance(u_id, BOT_ID) or 0
                db.set_balance(u_id, curr + a, BOT_ID)
                await message.answer(f"✅ Выдано {a} ✨ пользователю {u_id}")
        else:
            u_id = db.find_user(message.text)
            if u_id:
                st = (d['act'] == "frz")
                db.set_bot_data(u_id, is_frozen=st)
                await message.answer(f"✅ Статус {u_id} изменен.")
    except: await message.answer("❌ Ошибка ввода.")
    await state.clear()
    await message.answer("Меню:", reply_markup=main_menu(message.from_user.id))

@router.message(EconomyStates.WaitingAdminAction)
async def adm_trap(message: types.Message):
    pass 

# ====================== ЗАПУСК ======================
async def main():
    bot = Bot(token=TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    logger.info("✨ Sparks Bot (dual-role mode) started!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
