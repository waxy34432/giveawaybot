from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

def main_admin_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Создать розыгрыш", callback_data="admin:create")],
        [InlineKeyboardButton(text="📋 Мои розыгрыши", callback_data="admin:list")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin:stats")],
    ])

def cancel_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✖️ Отмена", callback_data="admin:cancel")]
    ])

def giveaway_kb(gid):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎟 Участвовать", callback_data=f"join:{gid}")]
    ])

def admin_payment_kb(rid):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"pay:approve:{rid}"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"pay:reject:{rid}")
    ]])

def publish_kb(gid):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Опубликовать", callback_data=f"publish:{gid}")],
        [InlineKeyboardButton(text="✖️ Отмена", callback_data="admin:cancel")]
    ])

def draw_list_kb(rows):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"🏆 Определить победителя · #{g['id']}", callback_data=f"admin:draw:{g['id']}")]
        for g in rows if g["status"] == "published" and g["ticket_count"] > 0
    ] or [[InlineKeyboardButton(text="⬅️ В меню", callback_data="admin:home")]])
