import asyncio
import logging
import os
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from dotenv import load_dotenv

from db import Database
from keyboards import main_admin_kb, cancel_kb, admin_payment_kb, publish_kb, draw_list_kb

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN", "")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
# Дополнительные администраторы: Telegram ID через запятую, например 111,222
ADMIN_IDS = {int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()}
ADMIN_IDS.add(OWNER_ID) if OWNER_ID else None
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
    """Проверка доступа для владельца и всех дополнительных администраторов."""
    return user_id in ADMIN_IDS

async def show_admin(message: Message):
    if not is_owner(message.from_user.id):
        return await message.answer("Привет! Чтобы участвовать, открой публикацию розыгрыша в канале.")
    await message.answer("🎛 <b>Панель администратора</b>\nВыбери действие:", reply_markup=main_admin_kb())

async def start(message: Message, state: FSMContext):
    await state.clear()
    if is_owner(message.from_user.id):
        await show_admin(message)
    else:
        await message.answer("Привет! Чтобы участвовать в розыгрыше, открой его публикацию в канале и нажми «Участвовать».")

async def create_start(user_id: int, target, state: FSMContext):
    if not is_owner(user_id):
        if isinstance(target, CallbackQuery):
            await target.answer("Доступ запрещён", show_alert=True)
        return
    await state.clear()
    await state.set_state(CreateGiveaway.title)
    text = "Введите название розыгрыша:"
    if isinstance(target, CallbackQuery):
        await target.message.answer(text, reply_markup=cancel_kb())
        await target.answer()
    else:
        await target.answer(text, reply_markup=cancel_kb())

async def get_title(message: Message, state: FSMContext):
    await state.update_data(title=(message.text or "").strip())
    await state.set_state(CreateGiveaway.description)
    await message.answer("Введите описание и приз:", reply_markup=cancel_kb())

async def get_description(message: Message, state: FSMContext):
    await state.update_data(description=(message.text or "").strip())
    await state.set_state(CreateGiveaway.price)
    await message.answer("Укажите стоимость одного билета целым числом в рублях:", reply_markup=cancel_kb())

async def get_price(message: Message, state: FSMContext):
    try:
        price = int((message.text or "").strip())
        if price <= 0 or price > 100000000:
            raise ValueError()
    except Exception:
        return await message.answer("Введите целое положительное число.")
    await state.update_data(price=price)
    await state.set_state(CreateGiveaway.payment)
    await message.answer("Введите реквизиты и инструкцию по оплате:", reply_markup=cancel_kb())

async def get_payment(message: Message, state: FSMContext):
    await state.update_data(payment=(message.text or "").strip())
    await state.set_state(CreateGiveaway.channel)
    await message.answer("Введите @username или числовой ID первого канала подписки:", reply_markup=cancel_kb())

async def get_channel(message: Message, state: FSMContext):
    await state.update_data(channel=(message.text or "").strip())
    await state.set_state(CreateGiveaway.channel2)
    await message.answer("Введите @username или ID второго канала подписки:", reply_markup=cancel_kb())

async def get_channel2(message: Message, state: FSMContext):
    await state.update_data(channel2=(message.text or "").strip())
    await state.set_state(CreateGiveaway.photo)
    await message.answer("Отправьте фото для карточки или напишите «нет»:", reply_markup=cancel_kb())

async def get_photo(message: Message, state: FSMContext):
    photo_id = message.photo[-1].file_id if message.photo else None
    if not photo_id and (message.text or "").lower() not in ("нет", "no", "-"):
        return await message.answer("Отправьте фото или напишите «нет».")
    await state.update_data(photo_id=photo_id)
    await state.set_state(CreateGiveaway.finish)
    d = await state.get_data()
    preview = (f"<b>{d['title']}</b>\n\n{d['description']}\n\nБилет: <b>{d['price']} ₽</b>\n\n"
               f"Оплата:\n{d['payment']}\n\nПодписка: {d['channel']} и {d['channel2']}")
    await message.answer("👀 <b>Предпросмотр</b>\n\n" + preview + "\n\nСохранить розыгрыш?", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Сохранить", callback_data="admin:save")],
        [InlineKeyboardButton(text="✖️ Отмена", callback_data="admin:cancel")]
    ]))

async def save_giveaway(call: CallbackQuery, state: FSMContext):
    if not is_owner(call.from_user.id):
        return await call.answer("Доступ запрещён", show_alert=True)
    d = await state.get_data()
    if not d.get("title"):
        await state.clear()
        return await call.answer("Черновик не найден. Начните заново.", show_alert=True)
    gid = await db.create_giveaway(d, call.from_user.id)
    await state.clear()
    await call.message.edit_text(f"✅ Розыгрыш #{gid} сохранён как черновик.", reply_markup=publish_kb(gid))
    await call.answer()

async def list_giveaways(call: CallbackQuery):
    if not is_owner(call.from_user.id):
        return await call.answer("Доступ запрещён", show_alert=True)
    rows = await db.list_giveaways()
    if not rows:
        await call.message.edit_text("Пока розыгрышей нет.", reply_markup=main_admin_kb())
    else:
        lines = [f"#{g['id']} · <b>{g['title']}</b> · {g['status']} · билетов: {g['ticket_count']}" for g in rows]
        await call.message.edit_text("📋 <b>Мои розыгрыши</b>\n\n" + "\n".join(lines), reply_markup=draw_list_kb(rows))
    await call.answer()

async def show_stats(call: CallbackQuery):
    if not is_owner(call.from_user.id):
        return await call.answer("Доступ запрещён", show_alert=True)
    s = await db.stats()
    await call.message.edit_text(f"📊 <b>Статистика</b>\n\nРозыгрышей: {s['giveaways']}\nЗаявок на оплату: {s['requests']}\nПодтверждённых билетов: {s['tickets']}", reply_markup=main_admin_kb())
    await call.answer()

async def draw_callback(call: CallbackQuery, bot: Bot):
    if not is_owner(call.from_user.id):
        return await call.answer("Доступ запрещён", show_alert=True)
    gid = int(call.data.rsplit(":", 1)[1])
    result = await db.draw_winner(gid)
    if not result:
        return await call.answer("Нельзя определить победителя: нет билетов или розыгрыш уже завершён.", show_alert=True)
    g = await db.get_giveaway(gid)
    await call.message.edit_text(f"🏆 <b>Победитель определён!</b>\n\nРозыгрыш: {g['title']}\nID пользователя: {result['user_id']}\nНомер билета: <b>{result['ticket_no']}</b>", reply_markup=main_admin_kb())
    if g and g["channel1"]:
        try:
            await bot.send_message(g["channel1"], f"🏆 Розыгрыш завершён!\nПобедитель: билет №{result['ticket_no']}")
        except Exception:
            log.exception("Could not post winner announcement")
    await call.answer()

async def publish_callback(call: CallbackQuery, bot: Bot):
    if not is_owner(call.from_user.id):
        return await call.answer("Доступ запрещён", show_alert=True)
    gid = int(call.data.split(":")[1])
    g = await db.get_giveaway(gid)
    if not g or g["status"] != "draft":
        return await call.answer("Черновик не найден или уже опубликован.", show_alert=True)
    try:
        for ch in (g["channel1"], g["channel2"]):
            await bot.get_chat(ch)
        text = (f"<b>{g['title']}</b>\n\n{g['description']}\n\n"
                f"Стоимость билета: <b>{g['price']} ₽</b>\n"
                "Для участия нажмите кнопку ниже.")
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🎟 Участвовать", callback_data=f"join:{gid}")
        ]])
        if g["photo_id"]:
            sent = await bot.send_photo(g["channel1"], g["photo_id"], caption=text, reply_markup=kb)
        else:
            sent = await bot.send_message(g["channel1"], text, reply_markup=kb)
        await db.mark_published(gid, str(g["channel1"]), sent.message_id)
        await call.message.edit_text(f"🚀 Розыгрыш #{gid} опубликован в {g['channel1']}.", reply_markup=main_admin_kb())
        await call.answer()
    except Exception as e:
        log.exception("publish failed")
        await call.answer(f"Не удалось опубликовать: {e}", show_alert=True)

async def join_callback(call: CallbackQuery, state: FSMContext, bot: Bot):
    gid = int(call.data.split(":")[1])
    g = await db.get_giveaway(gid)
    if not g or g["status"] != "published":
        return await call.answer("Розыгрыш сейчас недоступен.", show_alert=True)
    try:
        for ch in (g["channel1"], g["channel2"]):
            member = await bot.get_chat_member(ch, call.from_user.id)
            if member.status in ("left", "kicked"):
                return await call.answer("Сначала подпишитесь на оба канала.", show_alert=True)
    except Exception:
        return await call.answer("Не удалось проверить подписку. Проверьте права бота в каналах.", show_alert=True)
    await state.clear()
    await state.update_data(gid=gid)
    await state.set_state(BuyTickets.quantity)
    await bot.send_message(call.from_user.id, f"Сколько билетов хотите купить? Цена одного: {g['price']} ₽", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✖️ Отмена", callback_data="user:cancel")]
    ]))
    await call.answer("Написал вам в личные сообщения.")

async def quantity(message: Message, state: FSMContext):
    try:
        q = int((message.text or "").strip())
        if q < 1 or q > 100:
            raise ValueError()
    except Exception:
        return await message.answer("Введите число от 1 до 100.")
    d = await state.get_data()
    g = await db.get_giveaway(d["gid"])
    if not g or g["status"] != "published":
        await state.clear()
        return await message.answer("Розыгрыш уже закрыт.")
    total = q * g["price"]
    await state.update_data(quantity=q, total=total)
    await state.set_state(BuyTickets.receipt)
    await message.answer(f"К оплате: <b>{total} ₽</b> за {q} билет(ов).\n\nРеквизиты:\n{g['payment']}\n\nПосле оплаты отправьте фото чека или PDF.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✖️ Отмена", callback_data="user:cancel")]
    ]))

async def receipt(message: Message, state: FSMContext, bot: Bot):
    if message.photo:
        file_id, kind = message.photo[-1].file_id, "photo"
    elif message.document and message.document.mime_type == "application/pdf":
        file_id, kind = message.document.file_id, "document"
    else:
        return await message.answer("Пришлите фото чека или PDF-документ.")
    d = await state.get_data()
    rid = await db.create_payment_request(d["gid"], message.from_user.id, d["quantity"], d["total"], file_id, kind)
    await state.clear()
    await message.answer("✅ Чек отправлен администратору. Билеты будут начислены после проверки оплаты.")
    g = await db.get_giveaway(d["gid"])
    caption = (f"Заявка #{rid}\nРозыгрыш: {g['title']} (#{g['id']})\nПользователь: {message.from_user.full_name}\n"
               f"ID: {message.from_user.id}\nБилетов: {d['quantity']}\nСумма: {d['total']} ₽")
    if kind == "photo":
        await bot.send_photo(OWNER_ID, file_id, caption=caption, reply_markup=admin_payment_kb(rid))
    else:
        await bot.send_document(OWNER_ID, file_id, caption=caption, reply_markup=admin_payment_kb(rid))

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
        if not tickets:
            return await call.answer("Не удалось начислить билеты: проверьте статус розыгрыша.", show_alert=True)
        await bot.send_message(req["user_id"], f"Оплата подтверждена! Начислено билетов: {len(tickets)}.\nНомера: {', '.join(map(str, tickets))}")
        await call.message.edit_reply_markup(reply_markup=None)
        await call.answer("Оплата подтверждена")
    else:
        await db.reject_payment(rid)
        await bot.send_message(req["user_id"], "Оплата отклонена администратором.")
        await call.message.edit_reply_markup(reply_markup=None)
        await call.answer("Заявка отклонена")

async def cancel_callback(call: CallbackQuery, state: FSMContext):
    await state.clear()
    if is_owner(call.from_user.id):
        await call.message.edit_text("Действие отменено.", reply_markup=main_admin_kb())
    else:
        await call.message.edit_text("Действие отменено.")
    await call.answer()

async def main():
    if not TOKEN or not OWNER_ID:
        raise RuntimeError("Заполните BOT_TOKEN и OWNER_ID в .env; ADMIN_IDS можно оставить пустым")
    bot = Bot(TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.message.register(start, CommandStart())
    # All administrative navigation/actions use inline buttons; no /admin or /draw commands.
    dp.message.register(get_title, CreateGiveaway.title)
    dp.message.register(get_description, CreateGiveaway.description)
    dp.message.register(get_price, CreateGiveaway.price)
    dp.message.register(get_payment, CreateGiveaway.payment)
    dp.message.register(get_channel, CreateGiveaway.channel)
    dp.message.register(get_channel2, CreateGiveaway.channel2)
    dp.message.register(get_photo, CreateGiveaway.photo)
    dp.message.register(quantity, BuyTickets.quantity)
    dp.message.register(receipt, BuyTickets.receipt)

    dp.callback_query.register(lambda c, s: create_start(c.from_user.id, c, s), F.data == "admin:create")
    dp.callback_query.register(lambda c: list_giveaways(c), F.data == "admin:list")
    dp.callback_query.register(lambda c: show_stats(c), F.data == "admin:stats")
    dp.callback_query.register(lambda c, s: save_giveaway(c, s), F.data == "admin:save")
    dp.callback_query.register(lambda c: cancel_callback(c, dp.fsm.get_context(bot=c.bot, chat_id=c.message.chat.id, user_id=c.from_user.id)), F.data == "admin:cancel")
    dp.callback_query.register(lambda c: cancel_callback(c, dp.fsm.get_context(bot=c.bot, chat_id=c.message.chat.id, user_id=c.from_user.id)), F.data == "user:cancel")
    dp.callback_query.register(lambda c: show_admin(c.message), F.data == "admin:home")
    dp.callback_query.register(lambda c, b: draw_callback(c, b), F.data.startswith("admin:draw:"))
    dp.callback_query.register(lambda c, b: publish_callback(c, b), F.data.startswith("publish:"))
    dp.callback_query.register(lambda c, s, b: join_callback(c, s, b), F.data.startswith("join:"))
    dp.callback_query.register(lambda c, b: payment_decision(c, b), F.data.startswith("pay:"))
    log.info("Bot started")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
