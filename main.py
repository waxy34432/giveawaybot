import json
import logging
import random

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
    InputMediaPhoto, KeyboardButton, Message, ReplyKeyboardMarkup,
)

import db
from config import ADMIN_IDS, BOT_TOKEN

router = Router()
QTY_OPTIONS = [1, 2, 3, 5, 10]

ADMIN_KB = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="➕ Создать розыгрыш")],
        [KeyboardButton(text="🏆 Выбрать победителя")],
        [KeyboardButton(text="💳 Реквизиты")],
        [KeyboardButton(text="📢 Канал для публикаций")],
    ],
    resize_keyboard=True,
)


class Create(StatesGroup):
    title = State()
    description = State()
    price = State()
    photos = State()
    ch1 = State()
    ch2 = State()


class Buy(StatesGroup):
    receipt = State()


class Manual(StatesGroup):
    number = State()


class Requisites(StatesGroup):
    text = State()


class PostChannel(StatesGroup):
    waiting = State()


def post_channel() -> int | None:
    """ID канала для публикаций, который админ подключил через бота."""
    value = db.get_setting("post_channel")
    return int(value) if value else None


def channel_url(ch: str) -> str:
    return f"https://t.me/{ch.lstrip('@')}"


async def channel_link(bot: Bot, chat_id: int) -> str:
    """Ссылка на канал: публичная @username или пригласительная, если username нет."""
    chat = await bot.get_chat(chat_id)
    if chat.username:
        return f"https://t.me/{chat.username}"
    return await bot.export_chat_invite_link(chat_id)


def required_channels(g: dict) -> list:
    """Все каналы, на которые нужно подписаться: два из настроек розыгрыша и канал публикации."""
    chans = [g["ch1"], g["ch2"]]
    if g.get("post_ch"):
        chans.append(int(g["post_ch"]))
    return chans


async def is_subscribed(bot: Bot, user_id: int, channels) -> bool:
    for ch in channels:
        try:
            member = await bot.get_chat_member(ch, user_id)
        except Exception:
            return False
        if member.status in ("left", "kicked"):
            return False
    return True


def giveaway_text(g: dict) -> str:
    return (
        f"🎁 <b>{g['title']}</b>\n\n{g['description']}\n\n"
        f"💰 Стоимость билета: {g['price']} ₽\n"
        f"📢 Для участия подпишитесь на {g['ch1']} и {g['ch2']}, а также оставайтесь в этом канале"
    )


# ---------- Старт и участие ----------

@router.message(CommandStart())
async def start(m: Message, state: FSMContext, command: CommandObject, bot: Bot):
    await state.clear()
    if command.args and command.args.startswith("g_"):
        return await offer_participation(m, int(command.args[2:]), bot, m.from_user.id)
    if m.from_user.id in ADMIN_IDS:
        return await m.answer("Админ-панель:", reply_markup=ADMIN_KB)
    await m.answer("Перейдите по ссылке из канала, чтобы участвовать в розыгрыше.")


async def offer_participation(message: Message, gid: int, bot: Bot, user_id: int):
    g = db.get_giveaway(gid)
    if not g or g["status"] != "active":
        return await message.answer("Розыгрыш не найден или уже завершён.")
    if not await is_subscribed(bot, user_id, required_channels(g)):
        rows = [
            [InlineKeyboardButton(text=f"📢 {g['ch1']}", url=channel_url(g["ch1"]))],
            [InlineKeyboardButton(text=f"📢 {g['ch2']}", url=channel_url(g["ch2"]))],
        ]
        if g.get("post_ch"):
            rows.append([InlineKeyboardButton(
                text="📢 Канал розыгрыша",
                url=await channel_link(bot, int(g["post_ch"])),
            )])
        rows.append([InlineKeyboardButton(text="✅ Я подписался", callback_data=f"check:{gid}")])
        return await message.answer(
            "Чтобы участвовать, подпишитесь на все каналы и нажмите кнопку:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )
    await send_qty(message, g)


async def send_qty(message: Message, g: dict):
    rows = [[InlineKeyboardButton(
        text=f"{q} шт. — {g['price'] * q} ₽",
        callback_data=f"qty:{g['id']}:{q}",
    )] for q in QTY_OPTIONS]
    await message.answer(
        "Выберите количество билетов. Каждый билет — отдельный слот с уникальным номером:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.callback_query(F.data.startswith("check:"))
async def check_sub(cb: CallbackQuery, bot: Bot):
    gid = int(cb.data.split(":")[1])
    g = db.get_giveaway(gid)
    if not g or g["status"] != "active":
        return await cb.answer("Розыгрыш завершён", show_alert=True)
    if not await is_subscribed(bot, cb.from_user.id, required_channels(g)):
        return await cb.answer("Вы ещё не подписаны на оба канала", show_alert=True)
    await cb.message.edit_reply_markup(reply_markup=None)
    await send_qty(cb.message, g)
    await cb.answer()


@router.callback_query(F.data.startswith("qty:"))
async def choose_qty(cb: CallbackQuery, state: FSMContext, bot: Bot):
    _, gid, qty = cb.data.split(":")
    gid, qty = int(gid), int(qty)
    g = db.get_giveaway(gid)
    if not g or g["status"] != "active":
        return await cb.answer("Розыгрыш завершён", show_alert=True)
    if not await is_subscribed(bot, cb.from_user.id, required_channels(g)):
        return await cb.answer("Подпишитесь на оба канала", show_alert=True)
    details = db.get_setting("payment_details")
    if not details:
        return await cb.answer("Оплата временно недоступна, попробуйте позже", show_alert=True)
    await state.set_state(Buy.receipt)
    await state.update_data(gid=gid, qty=qty)
    await cb.message.answer(
        f"К оплате: <b>{g['price'] * qty} ₽</b> ({qty} шт.)\n\n"
        f"Реквизиты:\n{details}\n\n"
        "После оплаты пришлите фото или PDF чека."
    )
    await cb.answer()


@router.message(Buy.receipt, F.photo | F.document)
async def got_receipt(m: Message, state: FSMContext):
    if m.photo:
        rid, rtype = m.photo[-1].file_id, "photo"
    else:
        rid, rtype = m.document.file_id, "document"
    await state.update_data(receipt_id=rid, receipt_type=rtype)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Я оплатил", callback_data="paid")]
    ])
    await m.answer("Чек получен. Нажмите кнопку, чтобы отправить заявку администратору:",
                   reply_markup=kb)


@router.callback_query(F.data == "paid", Buy.receipt)
async def paid(cb: CallbackQuery, state: FSMContext, bot: Bot):
    d = await state.get_data()
    if "receipt_id" not in d:
        return await cb.answer("Сначала пришлите чек", show_alert=True)
    g = db.get_giveaway(d["gid"])
    user = cb.from_user
    username = user.username or str(user.id)
    oid = db.add_order(d["gid"], user.id, username, d["qty"], d["receipt_id"], d["receipt_type"])
    await state.clear()

    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"ok:{oid}"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"no:{oid}"),
    ]])
    caption = (
        f"💸 Заявка #{oid}\n"
        f"Розыгрыш: {g['title']}\n"
        f"Пользователь: @{username} (ID {user.id})\n"
        f"Билетов: {d['qty']} | Сумма: {g['price'] * d['qty']} ₽"
    )
    for admin_id in ADMIN_IDS:
        if d["receipt_type"] == "photo":
            await bot.send_photo(admin_id, d["receipt_id"], caption=caption, reply_markup=kb)
        else:
            await bot.send_document(admin_id, d["receipt_id"], caption=caption, reply_markup=kb)

    await cb.message.edit_text("⏳ Заявка отправлена. Ожидайте подтверждения оплаты.")
    await cb.answer()


@router.callback_query(F.data.startswith(("ok:", "no:")), F.from_user.id.in_(ADMIN_IDS))
async def decide_order(cb: CallbackQuery, bot: Bot):
    action, oid = cb.data.split(":")
    oid = int(oid)
    approve = action == "ok"
    order = db.get_order(oid)
    if not db.decide_order(oid, "approved" if approve else "rejected"):
        return await cb.answer("Заявка уже обработана", show_alert=True)

    await cb.message.edit_reply_markup(reply_markup=None)
    if approve:
        numbers = db.issue_tickets(order["giveaway_id"], order["user_id"], order["qty"])
        nums_html = ", ".join(f"<code>{n}</code>" for n in numbers)
        await bot.send_message(
            order["user_id"],
            f"✅ Оплата подтверждена! Ваши номера билетов: {nums_html}\n"
            "Чем больше билетов — тем больше шансов!",
        )
        await cb.message.answer(f"Заявка #{oid} подтверждена. Номера: {', '.join(map(str, numbers))}")
    else:
        await bot.send_message(
            order["user_id"],
            "❌ Оплата не подтверждена. Свяжитесь с администратором.",
        )
    await cb.answer()


# ---------- Создание розыгрыша ----------

@router.message(F.text == "➕ Создать розыгрыш", F.from_user.id.in_(ADMIN_IDS))
async def create_start(m: Message, state: FSMContext):
    if post_channel() is None:
        return await m.answer("Сначала подключите канал: кнопка «📢 Канал для публикаций».")
    await state.set_state(Create.title)
    await m.answer("Название розыгрыша:")


@router.message(Command("cancel"))
async def cancel(m: Message, state: FSMContext):
    await state.clear()
    await m.answer("Отменено.", reply_markup=ADMIN_KB)


@router.message(Create.title, F.text)
async def c_title(m: Message, state: FSMContext):
    await state.update_data(title=m.text)
    await state.set_state(Create.description)
    await m.answer("Что разыгрываем? Опишите приз:")


@router.message(Create.description, F.text)
async def c_desc(m: Message, state: FSMContext):
    await state.update_data(description=m.text)
    await state.set_state(Create.price)
    await m.answer("Стоимость участия (1 билет) в рублях, целое число:")


@router.message(Create.price, F.text)
async def c_price(m: Message, state: FSMContext):
    if not m.text.isdigit():
        return await m.answer("Введите целое число.")
    await state.update_data(price=int(m.text), photos=[])
    await state.set_state(Create.photos)
    await m.answer("Пришлите до 3 фото приза. Когда закончите, отправьте /done")


@router.message(Create.photos, F.photo)
async def c_photo(m: Message, state: FSMContext):
    d = await state.get_data()
    photos = d["photos"]
    if len(photos) >= 3:
        return await m.answer("Максимум 3 фото. Отправьте /done")
    photos.append(m.photo[-1].file_id)
    await state.update_data(photos=photos)
    await m.answer(f"Фото добавлено ({len(photos)}/3)")


@router.message(Create.photos, Command("done"))
async def c_done(m: Message, state: FSMContext):
    await state.set_state(Create.ch1)
    await m.answer("Первый канал для обязательной подписки (например @channel1):")


@router.message(Create.ch1, F.text.startswith("@"))
async def c_ch1(m: Message, state: FSMContext):
    await state.update_data(ch1=m.text.strip())
    await state.set_state(Create.ch2)
    await m.answer("Второй канал для подписки:")


@router.message(Create.ch2, F.text.startswith("@"))
async def c_ch2(m: Message, state: FSMContext, bot: Bot):
    d = await state.get_data()
    await state.clear()
    gid = db.add_giveaway(d["title"], d["description"], d["price"],
                          d["photos"], d["ch1"], m.text.strip(), post_channel())
    g = db.get_giveaway(gid)
    await publish(bot, g)
    await m.answer(f"✅ Розыгрыш #{gid} опубликован в канал.", reply_markup=ADMIN_KB)


async def publish(bot: Bot, g: dict):
    channel = post_channel()
    if channel is None:
        raise RuntimeError("Канал для публикаций не подключён")
    me = await bot.get_me()
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🎁 Участвовать",
                             url=f"https://t.me/{me.username}?start=g_{g['id']}")
    ]])
    photos = json.loads(g["photos"])
    text = giveaway_text(g)
    if photos:
        await bot.send_photo(channel, photos[0], caption=text, reply_markup=kb)
        if len(photos) > 1:
            await bot.send_media_group(channel, [InputMediaPhoto(media=p) for p in photos[1:]])
    else:
        await bot.send_message(channel, text, reply_markup=kb)


# ---------- Канал для публикаций ----------

@router.message(F.text == "📢 Канал для публикаций", F.from_user.id.in_(ADMIN_IDS))
async def chan_start(m: Message, state: FSMContext):
    current = post_channel()
    await state.set_state(PostChannel.waiting)
    await m.answer(
        f"Текущий канал: {current if current else 'не подключён'}\n\n"
        "Перешлите сюда любой пост из канала или пришлите его @username.\n"
        "Бот должен быть в канале администратором с правом публикации. Для отмены: /cancel"
    )


@router.message(PostChannel.waiting)
async def chan_save(m: Message, state: FSMContext, bot: Bot):
    if m.forward_from_chat:
        chat_id = m.forward_from_chat.id
    elif m.text and m.text.strip().startswith("@"):
        try:
            chat_id = (await bot.get_chat(m.text.strip())).id
        except Exception:
            return await m.answer("Не нашёл такой канал. Проверьте @username и попробуйте снова.")
    else:
        return await m.answer("Перешлите пост из канала или пришлите @username канала.")

    me = await bot.get_me()
    try:
        member = await bot.get_chat_member(chat_id, me.id)
    except Exception:
        return await m.answer("Бот не имеет доступа к этому каналу. Добавьте его администратором и повторите.")
    if member.status not in ("administrator", "creator"):
        return await m.answer("Бот не администратор этого канала. Добавьте его с правом публикации и повторите.")

    db.set_setting("post_channel", str(chat_id))
    await state.clear()
    await m.answer("✅ Канал подключён.", reply_markup=ADMIN_KB)


# ---------- Реквизиты ----------

@router.message(F.text == "💳 Реквизиты", F.from_user.id.in_(ADMIN_IDS))
async def req_start(m: Message, state: FSMContext):
    current = db.get_setting("payment_details", "не заданы")
    await state.set_state(Requisites.text)
    await m.answer(
        f"Текущие реквизиты:\n{current}\n\n"
        "Пришлите новый текст реквизитов. Для отмены: /cancel"
    )


@router.message(Requisites.text, F.text)
async def req_save(m: Message, state: FSMContext):
    db.set_setting("payment_details", m.text.strip())
    await state.clear()
    await m.answer("✅ Реквизиты сохранены.", reply_markup=ADMIN_KB)


# ---------- Выбор победителя ----------

@router.message(F.text == "🏆 Выбрать победителя", F.from_user.id.in_(ADMIN_IDS))
async def pick_giveaway(m: Message):
    gs = db.active_giveaways()
    if not gs:
        return await m.answer("Активных розыгрышей нет.")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"#{g['id']} {g['title']}", callback_data=f"wg:{g['id']}")]
        for g in gs
    ])
    await m.answer("Выберите розыгрыш:", reply_markup=kb)


@router.callback_query(F.data.startswith("wg:"), F.from_user.id.in_(ADMIN_IDS))
async def pick_mode(cb: CallbackQuery):
    gid = cb.data.split(":")[1]
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🎲 Рандом", callback_data=f"wr:{gid}"),
        InlineKeyboardButton(text="✍️ Вручную", callback_data=f"wm:{gid}"),
    ]])
    await cb.message.answer("Как выбрать победителя?", reply_markup=kb)
    await cb.answer()


@router.callback_query(F.data.startswith("wr:"), F.from_user.id.in_(ADMIN_IDS))
async def pick_random(cb: CallbackQuery, bot: Bot):
    gid = int(cb.data.split(":")[1])
    tickets = db.all_tickets(gid)
    if not tickets:
        return await cb.answer("В розыгрыше нет билетов", show_alert=True)
    t = random.choice(tickets)  # каждый билет — равный шанс, у кого больше билетов — тот чаще выигрывает
    await finish(bot, gid, t["user_id"], t["number"])
    await cb.message.answer(f"🎲 Победитель выбран случайно: билет №{t['number']}")
    await cb.answer()


@router.callback_query(F.data.startswith("wm:"), F.from_user.id.in_(ADMIN_IDS))
async def pick_manual_start(cb: CallbackQuery, state: FSMContext):
    gid = int(cb.data.split(":")[1])
    await state.set_state(Manual.number)
    await state.update_data(gid=gid)
    await cb.message.answer("Введите номер билета победителя:")
    await cb.answer()


@router.message(Manual.number, F.text)
async def pick_manual(m: Message, state: FSMContext, bot: Bot):
    if not m.text.isdigit():
        return await m.answer("Введите номер билета цифрами.")
    d = await state.get_data()
    t = db.find_ticket(d["gid"], int(m.text))
    if not t:
        return await m.answer("Такого билета нет. Попробуйте ещё раз.")
    await state.clear()
    await finish(bot, d["gid"], t["user_id"], t["number"])
    await m.answer(f"✅ Победитель выбран: билет №{t['number']}", reply_markup=ADMIN_KB)


async def finish(bot: Bot, gid: int, user_id: int, number: int):
    db.finish_giveaway(gid, user_id, number)
    g = db.get_giveaway(gid)
    await bot.send_message(
        user_id,
        f"🏆 Поздравляем! Вы выиграли в розыгрыше «{g['title']}» с билетом №{number}. "
        "Администратор свяжется с вами.",
    )
    channel = post_channel()
    if channel is not None:
        await bot.send_message(
            channel,
            f"🏆 Розыгрыш «{g['title']}» завершён!\n"
            f"Победитель — билет №{number} (<a href='tg://user?id={user_id}'>открыть профиль</a>)",
        )


# ---------- Запуск ----------

async def main():
    logging.basicConfig(level=logging.INFO)
    db.init_db()
    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    await dp.start_polling(bot)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
