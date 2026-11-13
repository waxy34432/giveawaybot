import asyncio
import logging
import os
from datetime import datetime, timezone
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from dotenv import load_dotenv

from db import Database
from keyboards import main_admin_kb, cancel_kb, giveaway_kb, admin_payment_kb

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN", "")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
DB_PATH = os.getenv("DB_PATH", "giveaways.sqlite3")
db = Database(DB_PATH)

logging.basicConfig(level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO))
log = logging.getLogger("giveaway-bot")

class CreateGiveaway(StatesGroup):
    title = State()
    description = State()
    price = State()
    payment = State()
    channel = State()
    channel2 = State()
    photo = State()
    finish = State()

class BuyTickets(StatesGroup):
    quantity = State()
    receipt = State()

def is_owner(user_id: int) -> bool:
    return user_id == OWNER_ID and OWNER_ID != 0

def admin_only(message: Message) -> bool:
    return is_owner(message.from_user.id)

async def safe_edit_or_send(message: Message, text: str, reply_markup=None):
    try:
        await message.edit_text(text, reply_markup=reply_markup)
    except Exception:
        await message.answer(text, reply_markup=reply_markup)

async def start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "Привет! Здесь можно участвовать в розыгрышах.\n"
        "Открой публикацию розыгрыша в канале и нажми «Участвовать»."
    )

async def admin(message: Message):
    if not admin_only(message):
        return await message.answer("Доступ запрещён.")
    await message.answer("Панель управления розыгрышами:", reply_markup=main_admin_kb())

async def create_start(message: Message, state: FSMContext):
    if not admin_only(message):
        return
    await state.clear()
    await state.set_state(CreateGiveaway.title)
    await message.answer("Введите название розыгрыша:", reply_markup=cancel_kb())

async def cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Действие отменено.", reply_markup=main_admin_kb() if is_owner(message.from_user.id) else None)

async def get_title(message: Message, state: FSMContext):
    await state.update_data(title=message.text.strip())
    await state.set_state(CreateGiveaway.description)
    await message.answer("Введите описание и приз:")

async def get_description(message: Message, state: FSMContext):
    await state.update_data(description=message.text.strip())
    await state.set_state(CreateGiveaway.price)
    await message.answer("Укажите стоимость одного билета числом в рублях, например 150:")

async def get_price(message: Message, state: FSMContext):
    try:
        price = int(message.text.strip())
        if price <= 0 or price > 100000000:
            raise ValueError()
    except Exception:
        return await message.answer("Введите целое положительное число.")
    await state.update_data(price=price)
    await state.set_state(CreateGiveaway.payment)
    await message.answer("Введите реквизиты и инструкцию по оплате. Не вставляйте сюда секретные данные.")

async def get_payment(message: Message, state: FSMContext):
    await state.update_data(payment=message.text.strip())
    await state.set_state(CreateGiveaway.channel)
    await message.answer("Введите @username или числовой ID первого канала обязательной подписки.\n"
                         "Бот должен быть администратором канала.")

async def get_channel(message: Message, state: FSMContext):
    await state.update_data(channel=message.text.strip())
    await state.set_state(CreateGiveaway.channel2)
    await message.answer("Введите @username или ID второго канала обязательной подписки:")

async def get_channel2(message: Message, state: FSMContext):
    await state.update_data(channel2=message.text.strip())
    await state.set_state(CreateGiveaway.photo)
    await message.answer("Отправьте фото для карточки розыгрыша или напишите «нет»:")

async def get_photo(message: Message, state: FSMContext):
    photo_id = None
    if message.photo:
        photo_id = message.photo[-1].file_id
    elif (message.text or "").lower() not in ("нет", "no", "-"):
        return await message.answer("Отправьте фото или напишите «нет».")
    await state.update_data(photo_id=photo_id)
    await state.set_state(CreateGiveaway.finish)
    data = await state.get_data()
    preview = (f"<b>{data['title']}</b>\n\n{data['description']}\n\n"
               f"Билет: <b>{data['price']} ₽</b>\n\nОплата:\n{data['payment']}\n\n"
               f"Подписка: {data['channel']} и {data['channel2']}")
    await message.answer("Предпросмотр:\n\n" + preview + "\n\nОпубликовать? Отправьте «да» или «нет».", parse_mode=ParseMode.HTML)

async def finish_create(message: Message, state: FSMContext, bot: Bot):
    answer = (message.text or "").strip().lower()
    if answer not in ("да", "yes", "y"):
        await state.clear()
        return await message.answer("Создание отменено.", reply_markup=main_admin_kb())
    data = await state.get_data()
    gid = await db.create_giveaway(data, message.from_user.id)
    await state.clear()
    await message.answer(f"Розыгрыш #{gid} создан как черновик. Для публикации нажмите кнопку ниже.",
                         reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                             InlineKeyboardButton(text="Опубликовать", callback_data=f"publish:{gid}")
                         ]]))
    await message.answer("Панель управления:", reply_markup=main_admin_kb())

async def publish_callback(call: CallbackQuery, bot: Bot):
    if not is_owner(call.from_user.id):
        return await call.answer("Доступ запрещён", show_alert=True)
    gid = int(call.data.split(":")[1])
    g = await db.get_giveaway(gid)
    if not g:
        return await call.answer("Не найдено", show_alert=True)
    try:
        await bot.get_chat(g["channel1"])
        await bot.get_chat(g["channel2"])
        text = (f"<b>{g['title']}</b>\n\n{g['description']}\n\n"
                f"Стоимость билета: <b>{g['price']} ₽</b>\n"
                "Для участия нажмите кнопку ниже и следуйте инструкциям.")
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🎟 Участвовать", callback_data=f"join:{gid}")
        ]])
        if g["photo_id"]:
            sent = await bot.send_photo(g["channel1"], g["photo_id"], caption=text, reply_markup=kb)
        else:
            sent = await bot.send_message(g["channel1"], text, reply_markup=kb)
        await db.mark_published(gid, str(g["channel1"]), sent.message_id)
        await call.message.edit_text(f"Розыгрыш #{gid} опубликован в {g['channel1']}.")
    except Exception as e:
        log.exception("publish failed")
        await call.answer(f"Не удалось опубликовать: {e}", show_alert=True)

async def join_callback(call: CallbackQuery, state: FSMContext, bot: Bot):
    gid = int(call.data.split(":")[1])
    g = await db.get_giveaway(gid)
    if not g or g["status"] != "published":
        return await call.answer("Этот розыгрыш сейчас недоступен.", show_alert=True)
    try:
        for ch in (g["channel1"], g["channel2"]):
            member = await bot.get_chat_member(ch, call.from_user.id)
            if member.status in ("left", "kicked"):
                raise ValueError()
    except ValueError:
        return await call.answer("Сначала подпишитесь на оба канала.", show_alert=True)
    except Exception:
        return await call.answer("Не удалось проверить подписку. Проверьте, что бот администратор обоих каналов.", show_alert=True)
    await state.clear()
    await state.update_data(gid=gid)
    await state.set_state(BuyTickets.quantity)
    await bot.send_message(call.from_user.id, f"Сколько билетов хотите купить? Цена одного: {g['price']} ₽")
    await call.answer()

async def quantity(message: Message, state: FSMContext):
    try:
        q = int(message.text.strip())
        if q < 1 or q > 100:
            raise ValueError()
    except Exception:
        return await message.answer("Введите число от 1 до 100.")
    data = await state.get_data()
    g = await db.get_giveaway(data["gid"])
    if not g or g["status"] != "published":
        await state.clear()
        return await message.answer("Розыгрыш уже закрыт.")
    total = q * g["price"]
    await state.update_data(quantity=q, total=total)
    await state.set_state(BuyTickets.receipt)
    await message.answer(f"К оплате: <b>{total} ₽</b> за {q} билет(ов).\n\n"
                         f"Реквизиты:\n{g['payment']}\n\n"
                         "После оплаты отправьте фото чека или PDF-документ.", parse_mode=ParseMode.HTML)

async def receipt(message: Message, state: FSMContext, bot: Bot):
    if message.photo:
        file_id, kind = message.photo[-1].file_id, "photo"
    elif message.document and message.document.mime_type == "application/pdf":
        file_id, kind = message.document.file_id, "document"
    else:
        return await message.answer("Пришлите фото чека или PDF-документ.")
    data = await state.get_data()
    uid = message.from_user.id
    request_id = await db.create_payment_request(data["gid"], uid, data["quantity"], data["total"], file_id, kind)
    await state.clear()
    await message.answer("Чек отправлен администратору. Билеты будут начислены только после проверки оплаты.")
    g = await db.get_giveaway(data["gid"])
    caption = (f"Заявка #{request_id}\nРозыгрыш: {g['title']} (#{g['id']})\n"
               f"Пользователь: {message.from_user.full_name} (@{message.from_user.username or 'нет'})\n"
               f"ID: {uid}\nБилетов: {data['quantity']}\nСумма: {data['total']} ₽")
    kb = admin_payment_kb(request_id)
    if OWNER_ID:
        if kind == "photo":
            await bot.send_photo(OWNER_ID, file_id, caption=caption, reply_markup=kb)
        else:
            await bot.send_document(OWNER_ID, file_id, caption=caption, reply_markup=kb)

async def payment_decision(call: CallbackQuery, bot: Bot):
    if not is_owner(call.from_user.id):
        return await call.answer("Доступ запрещён", show_alert=True)
    _, action, rid_s = call.data.split(":")
    rid = int(rid_s)
    req = await db.get_payment_request(rid)
    if not req or req["status"] != "pending":
        return await call.answer("Заявка уже обработана.", show_alert=True)
    if action == "approve":
        tickets = await db.approve_payment(rid)
        await bot.send_message(req["user_id"], f"Оплата подтверждена! Вам начислено билетов: {len(tickets)}.\nНомера: {', '.join(map(str, tickets))}")
        await call.message.edit_reply_markup(reply_markup=None)
        await call.answer("Оплата подтверждена")
    else:
        await db.reject_payment(rid)
        await bot.send_message(req["user_id"], "Оплата отклонена администратором. Если это ошибка, свяжитесь с организатором.")
        await call.message.edit_reply_markup(reply_markup=None)
        await call.answer("Заявка отклонена")

async def my_giveaways(message: Message):
    if not is_owner(message.from_user.id):
        return
    rows = await db.list_giveaways()
    if not rows:
        return await message.answer("Пока розыгрышей нет.")
    for g in rows:
        await message.answer(f"#{g['id']} — {g['title']}\nСтатус: {g['status']}\nБилетов: {g['ticket_count']}")

async def draw(message: Message, bot: Bot):
    if not is_owner(message.from_user.id):
        return
    parts = (message.text or "").split()
    if len(parts) != 2 or not parts[1].isdigit():
        return await message.answer("Использование: /draw ID_розыгрыша")
    gid = int(parts[1])
    result = await db.draw_winner(gid)
    if not result:
        return await message.answer("Нет подтверждённых билетов или розыгрыш не найден/уже завершён.")
    await message.answer(f"Победитель розыгрыша #{gid}:\nПользователь ID: {result['user_id']}\nБилет №{result['ticket_no']}")
    g = await db.get_giveaway(gid)
    if g and g["channel1"]:
        await bot.send_message(g["channel1"], f"🏆 Розыгрыш завершён!\nПобедитель: билет №{result['ticket_no']}")

async def stats(message: Message):
    if not is_owner(message.from_user.id):
        return
    s = await db.stats()
    await message.answer(f"Розыгрышей: {s['giveaways']}\nЗаявок на оплату: {s['requests']}\nПодтверждённых билетов: {s['tickets']}")

async def main():
    if not TOKEN or not OWNER_ID:
        raise RuntimeError("Заполните BOT_TOKEN и OWNER_ID в .env")
    bot = Bot(TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.message.register(start, CommandStart())
    dp.message.register(admin, Command("admin"))
    dp.message.register(create_start, F.text == "➕ Создать розыгрыш")
    dp.message.register(my_giveaways, F.text == "📋 Мои розыгрыши")
    dp.message.register(stats, F.text == "📊 Статистика")
    dp.message.register(draw, Command("draw"))
    dp.message.register(cancel, Command("cancel"))
    dp.message.register(get_title, CreateGiveaway.title)
    dp.message.register(get_description, CreateGiveaway.description)
    dp.message.register(get_price, CreateGiveaway.price)
    dp.message.register(get_payment, CreateGiveaway.payment)
    dp.message.register(get_channel, CreateGiveaway.channel)
    dp.message.register(get_channel2, CreateGiveaway.channel2)
    dp.message.register(get_photo, CreateGiveaway.photo)
    dp.message.register(finish_create, CreateGiveaway.finish)
    dp.message.register(quantity, BuyTickets.quantity)
    dp.message.register(receipt, BuyTickets.receipt)
    dp.callback_query.register(publish_callback, F.data.startswith("publish:"))
    dp.callback_query.register(join_callback, F.data.startswith("join:"))
    dp.callback_query.register(payment_decision, F.data.startswith("pay:"))
    log.info("Bot started")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
