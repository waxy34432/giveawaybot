from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton

def main_admin_kb():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="➕ Создать розыгрыш")],
        [KeyboardButton(text="📋 Мои розыгрыши"), KeyboardButton(text="📊 Статистика")]
    ], resize_keyboard=True)

def cancel_kb():
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="/cancel")]], resize_keyboard=True)

def giveaway_kb(gid):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🎟 Участвовать", callback_data=f"join:{gid}")]])

def admin_payment_kb(rid):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"pay:approve:{rid}"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"pay:reject:{rid}")
    ]])
