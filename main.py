import asyncio
import csv
import html
import io
import logging
import random
import time
from collections import defaultdict

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    BufferedInputFile, CallbackQuery, ChatMemberUpdated, InlineKeyboardButton,
    InlineKeyboardMarkup, InputMediaPhoto, KeyboardButton, Message,
    ReplyKeyboardMarkup, ReplyParameters,
)

import db
import logic
from config import ADMIN_IDS, BOT_TOKEN, SUPPORT_CONTACT

log = logging.getLogger("giveaway")

# Админ-роутер подключается первым: его обработчики видят только админов.
admin_router = Router(name="admin")
admin_router.message.filter(F.from_user.id.in_(ADMIN_IDS))
admin_router.callback_query.filter(F.from_user.id.in_(ADMIN_IDS))
user_router = Router(name="user")

esc = html.escape
QTY_OPTIONS = [1, 2, 3, 5, 10]
BTN_OPTIONS = ["Участвовать", "🎁 Участвую!", "Принять участие"]
STATUS_ICON = {
    "scheduled": "🕐", "publishing": "🕐", "active": "🟢", "ended": "🟡",
    "drawing": "🟡", "finished": "🏁", "cancelled": "⛔", "error": "⚠️",
}
STATUS_NAME = {
    "scheduled": "запланирован", "publishing": "публикуется", "active": "идёт приём участников",
    "ended": "приём закрыт, ждёт победителей", "drawing": "подведение итогов",
    "finished": "завершён", "cancelled": "отменён", "error": "ошибка публикации",
}

ADMIN_KB = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="➕ Создать розыгрыш"), KeyboardButton(text="🎁 Мои розыгрыши")],
        [KeyboardButton(text="📢 Мои каналы"), KeyboardButton(text="💳 Реквизиты")],
        [KeyboardButton(text="🛟 Поддержка")],
    ],
    resize_keyboard=True,
)


class Create(StatesGroup):
    title = State()
    text = State()
    photos = State()
    price = State()
    btn = State()
    btn_custom = State()
    winners = State()
    channel = State()
    extra = State()
    extra_add = State()
    start_mode = State()
    start_at = State()
    end_mode = State()
    end_at = State()
    end_count = State()
    draw_mode = State()
    options = State()
    confirm = State()


class Buy(StatesGroup):
    receipt = State()


class Join(StatesGroup):
    captcha = State()


class Manual(StatesGroup):
    tickets = State()


class Requisites(StatesGroup):
    text = State()


class AddChannel(StatesGroup):
    username = State()


# ======================================================================
# Общие помощники
# ======================================================================

def kb(rows):
    return InlineKeyboardMarkup(inline_keyboard=rows)


def btn(text, data):
    return InlineKeyboardButton(text=text, callback_data=data)


CANCEL = [btn("❌ Отмена", "cancel")]


async def safe_send(bot: Bot, chat_id, text, **kw):
    try:
        return await bot.send_message(chat_id, text, **kw)
    except Exception as e:
        log.warning("не удалось отправить сообщение %s: %s", chat_id, e)
        return None


def mention(uid, username, full_name):
    name = full_name or username or str(uid)
    return f'<a href="tg://user?id={uid}">{esc(name)}</a>'


def required_ids(g: dict) -> list:
    """Каналы, на которые нужно подписаться: канал розыгрыша + дополнительные."""
    return [g["post_chat_id"], *g["extra"]]


def channel_title(chat_id) -> str:
    row = db.get_channel(chat_id)
    return row["title"] if row else str(chat_id)


async def is_subscribed(bot: Bot, user_id: int, chat_ids) -> bool:
    for cid in chat_ids:
        try:
            member = await bot.get_chat_member(cid, user_id)
        except Exception:
            return False
        if member.status in ("left", "kicked"):
            return False
        if member.status == "restricted" and not getattr(member, "is_member", True):
            return False
    return True


async def sub_rows(bot: Bot, g: dict) -> list:
    rows = []
    for cid in required_ids(g):
        row = db.get_channel(cid)
        title = row["title"] if row else str(cid)
        link = row["link"] if row else None
        if not link:
            try:
                chat = await bot.get_chat(cid)
                title = chat.title or title
                if chat.username:
                    link = f"https://t.me/{chat.username}"
            except Exception:
                pass
        if link:
            rows.append([InlineKeyboardButton(text=f"📢 {title}", url=link)])
    return rows


async def store_channel(bot: Bot, chat, added_by: int):
    """Запоминает канал; для приватных пытается создать пригласительную ссылку (один раз)."""
    existing = db.get_channel(chat.id)
    link = f"https://t.me/{chat.username}" if chat.username else (existing or {}).get("link")
    if not link:
        try:
            link = await bot.export_chat_invite_link(chat.id)
        except Exception:
            link = None
    db.upsert_channel(chat.id, chat.title or str(chat.id), chat.username, link, added_by)


async def register_channel(bot: Bot, ref: str, added_by: int):
    """Подключение канала по @username или ID. Возвращает (chat, ошибка)."""
    ref = ref.strip()
    if ref.startswith("@"):
        target = ref
    elif ref.lstrip("-").isdigit():
        target = int(ref)
    else:
        return None, "Пришлите @username канала."
    try:
        chat = await bot.get_chat(target)
    except Exception:
        return None, "Не нашёл такой канал. Проверьте @username и попробуйте снова."
    try:
        me = await bot.get_me()
        member = await bot.get_chat_member(chat.id, me.id)
    except Exception:
        return None, "Бот не имеет доступа к этому каналу. Добавьте его администратором и повторите."
    if member.status != "administrator":
        return None, "Бот не администратор этого канала. Добавьте его администратором и повторите."
    await store_channel(bot, chat, added_by)
    return chat, None


# ======================================================================
# Публикация, завершение приёма, подведение итогов
# ======================================================================

def post_text(g: dict) -> str:
    lines = [f"🎁 <b>{esc(g['title'])}</b>", "", g["text"], ""]
    if g["price"] > 0:
        lines.append(f"💰 Стоимость билета: <b>{g['price']} ₽</b> (больше билетов — больше шансов)")
    else:
        lines.append("💰 Участие бесплатное")
    lines.append(f"🏆 Победителей: <b>{g['winners_count']}</b>")
    if g["end_mode"] == "time":
        lines.append(f"⏰ Итоги: <b>{logic.fmt_msk(g['end_at'])}</b>")
    else:
        lines.append(f"👥 Итоги, когда наберётся <b>{g['end_count']}</b> участников")
    titles = ", ".join(esc(channel_title(c)) for c in required_ids(g))
    lines.append(f"📢 Нужна подписка: {titles}")
    return "\n".join(lines)


async def publish(bot: Bot, g: dict) -> int:
    me = await bot.get_me()
    markup = kb([[InlineKeyboardButton(
        text=g["btn_text"], url=f"https://t.me/{me.username}?start=g_{g['id']}")]])
    text, photos, chat = post_text(g), g["photos"], g["post_chat_id"]
    if not photos:
        msg = await bot.send_message(chat, text, reply_markup=markup)
    elif len(photos) == 1:
        if len(text) <= 1000:
            msg = await bot.send_photo(chat, photos[0], caption=text, reply_markup=markup)
        else:
            await bot.send_photo(chat, photos[0])
            msg = await bot.send_message(chat, text, reply_markup=markup)
    else:
        await bot.send_media_group(chat, [InputMediaPhoto(media=p) for p in photos])
        msg = await bot.send_message(chat, text, reply_markup=markup)
    return msg.message_id


async def start_giveaway(bot: Bot, gid: int):
    if not db.set_status_if(gid, ("scheduled",), "publishing"):
        return
    g = db.get_giveaway(gid)
    try:
        mid = await publish(bot, g)
    except Exception as e:
        log.exception("публикация розыгрыша %s не удалась", gid)
        db.set_status_if(gid, ("publishing",), "error")
        await safe_send(
            bot, g["owner_id"],
            f"⚠️ Не удалось опубликовать розыгрыш «{esc(g['title'])}»: {esc(str(e))}\n"
            "Проверьте, что бот администратор канала с правом публикации.",
        )
        return
    db.update_giveaway(gid, post_message_id=mid)
    db.set_status_if(gid, ("publishing",), "active")
    await safe_send(bot, g["owner_id"], f"🚀 Розыгрыш «{esc(g['title'])}» опубликован.")


async def remove_post_button(bot: Bot, g: dict):
    if g.get("post_message_id"):
        try:
            await bot.edit_message_reply_markup(
                chat_id=g["post_chat_id"], message_id=g["post_message_id"], reply_markup=None)
        except Exception:
            pass


def draw_buttons(gid: int):
    return kb([
        [btn("🎲 Рандом", f"gvr:{gid}"), btn("✍️ Вручную", f"gvm:{gid}")],
        [btn("👥 Участники", f"gvp:{gid}")],
    ])


async def close_entries(bot: Bot, gid: int):
    """Закрывает приём участников; дальше либо авто-рандом, либо ждём выбор админа."""
    if not db.set_status_if(gid, ("active",), "ended"):
        return
    g = db.get_giveaway(gid)
    await remove_post_button(bot, g)
    pending = db.count_pending_orders(gid)
    note = (f"\n⚠️ Необработанных заявок на оплату: {pending}. Билеты по ним уже не выдаются."
            if pending else "")
    if g["draw_mode"] == "random":
        if note:
            await safe_send(bot, g["owner_id"], f"⏰ Приём участников в «{esc(g['title'])}» закрыт.{note}")
        await do_random_draw(bot, gid)
    else:
        await safe_send(
            bot, g["owner_id"],
            f"⏰ Приём участников в розыгрыше «{esc(g['title'])}» закрыт.\n"
            f"Участников: {db.count_participants(gid)}, билетов: {db.count_tickets(gid)}.{note}\n\n"
            "Выберите победителей:",
            reply_markup=draw_buttons(gid),
        )


async def maybe_close_by_count(bot: Bot, gid: int):
    g = db.get_giveaway(gid)
    if (g and g["status"] == "active" and g["end_mode"] == "count"
            and db.count_participants(gid) >= g["end_count"]):
        await close_entries(bot, gid)


async def build_entries(bot: Bot, g: dict) -> dict:
    """user_id -> число слотов: каждый билет = 1 слот, буст канала даёт +1."""
    slots = defaultdict(int)
    for t in db.all_tickets(g["id"]):
        slots[t["user_id"]] += 1
    if g["boost"]:
        for uid in list(slots):
            try:
                boosts = await bot.get_user_chat_boosts(g["post_chat_id"], uid)
                if boosts.boosts:
                    slots[uid] += 1
            except Exception:
                pass
            await asyncio.sleep(0.05)
    return dict(slots)


async def do_random_draw(bot: Bot, gid: int) -> bool:
    if not db.set_status_if(gid, ("active", "ended"), "drawing"):
        return False
    g = db.get_giveaway(gid)
    await remove_post_button(bot, g)
    entries = await build_entries(bot, g)
    uids = logic.weighted_draw(entries, g["winners_count"])
    by_user = defaultdict(list)
    for t in db.all_tickets(gid):
        by_user[t["user_id"]].append(t["number"])
    pairs = [(u, random.choice(by_user[u])) for u in uids]
    await finish(bot, g, pairs)
    return True


async def finish(bot: Bot, g: dict, pairs: list):
    gid = g["id"]
    db.set_winners(gid, pairs)
    db.set_status_if(gid, ("drawing",), "finished")
    reply = None
    if g.get("post_message_id"):
        reply = ReplyParameters(message_id=g["post_message_id"], allow_sending_without_reply=True)

    if not pairs:
        text = f"🏁 Розыгрыш «{esc(g['title'])}» завершён. Участников не было, победителей нет."
        await safe_send(bot, g["post_chat_id"], text, reply_parameters=reply)
        await safe_send(bot, g["owner_id"], text)
        return

    info = {w["user_id"]: w for w in db.get_winners(gid)}
    channel_lines, owner_lines = [], []
    for i, (uid, ticket) in enumerate(pairs, 1):
        w = info.get(uid, {})
        uname = f" (@{w['username']})" if w.get("username") else ""
        channel_lines.append(f"{i}. {mention(uid, w.get('username'), w.get('full_name'))}{uname} — билет №{ticket}")
        owner_lines.append(f"{i}. {esc(w.get('full_name') or str(uid))}{uname}, ID {uid} — билет №{ticket}")

    text = (f"🏆 <b>Розыгрыш «{esc(g['title'])}» завершён!</b>\n\nПобедители:\n"
            + "\n".join(channel_lines) + "\n\nПоздравляем!")
    sent = await safe_send(bot, g["post_chat_id"], text, reply_parameters=reply)
    if sent is None:
        await safe_send(bot, g["owner_id"],
                        "⚠️ Не удалось опубликовать итоги в канале. Проверьте права бота.")
    for uid, ticket in pairs:
        await safe_send(
            bot, uid,
            f"🏆 Поздравляем! Вы выиграли в розыгрыше «{esc(g['title'])}» с билетом №{ticket}. "
            "Организатор свяжется с вами.",
        )
    await safe_send(bot, g["owner_id"],
                    f"✅ Розыгрыш «{esc(g['title'])}» завершён. Победители:\n" + "\n".join(owner_lines))


# ======================================================================
# Участие (для всех пользователей)
# ======================================================================

@admin_router.message(CommandStart())
@user_router.message(CommandStart())
async def start(m: Message, state: FSMContext, command: CommandObject, bot: Bot):
    await state.clear()
    args = command.args or ""
    if args.startswith("g_") and args[2:].isdigit():
        return await join_flow(m, state, bot, m.from_user, int(args[2:]))
    if m.from_user.id in ADMIN_IDS:
        return await m.answer("👋 Админ-панель. Выберите действие:", reply_markup=ADMIN_KB)
    await m.answer("👋 Чтобы принять участие в розыгрыше, нажмите кнопку под постом розыгрыша в канале.")


@user_router.message(Command("cancel"))
async def user_cancel(m: Message, state: FSMContext):
    await state.clear()
    await m.answer("Отменено.")


async def join_flow(message: Message, state: FSMContext, bot: Bot, user, gid: int):
    """Проверки по порядку: статус → подписка → капча → бесплатный билет или выбор количества."""
    g = db.get_giveaway(gid)
    if not g or g["status"] != "active":
        return await message.answer("Розыгрыш не найден или уже завершён.")
    if not await is_subscribed(bot, user.id, required_ids(g)):
        rows = await sub_rows(bot, g)
        rows.append([btn("✅ Я подписался", f"check:{gid}")])
        return await message.answer(
            "Чтобы участвовать, подпишитесь на все каналы и нажмите кнопку:", reply_markup=kb(rows))
    if g["captcha"] and not db.captcha_passed(gid, user.id):
        return await send_captcha(message, state, gid)
    if g["price"] == 0:
        return await free_join(message, bot, user, g)
    await send_qty(message, g, user.id)


async def send_captcha(message: Message, state: FSMContext, gid: int):
    a, b = random.randint(2, 9), random.randint(2, 9)
    answer = a + b
    options = {answer}
    while len(options) < 4:
        options.add(random.randint(4, 18))
    shuffled = random.sample(sorted(options), 4)
    await state.set_state(Join.captcha)
    await state.update_data(gid=gid, answer=answer)
    await message.answer(
        f"🤖 Проверка от ботов: сколько будет {a} + {b}?",
        reply_markup=kb([[btn(str(o), f"cap:{o}") for o in shuffled]]),
    )


async def free_join(message: Message, bot: Bot, user, g: dict):
    gid = g["id"]
    existing = db.user_tickets(gid, user.id)
    if existing:
        return await message.answer(
            f"✅ Вы уже участвуете. Ваш билет: <code>{existing[0]}</code>")
    db.upsert_participant(gid, user.id, user.username, user.full_name)
    numbers = db.issue_tickets(gid, user.id, 1)
    await message.answer(
        f"🎉 Вы участвуете в розыгрыше «{esc(g['title'])}»!\nВаш билет: <code>{numbers[0]}</code>")
    await maybe_close_by_count(bot, gid)


async def send_qty(message: Message, g: dict, user_id: int):
    have = db.user_tickets(g["id"], user_id)
    head = f"У вас уже есть билетов: {len(have)}. " if have else ""
    rows = [[btn(f"{q} шт. — {g['price'] * q} ₽", f"qty:{g['id']}:{q}")] for q in QTY_OPTIONS]
    await message.answer(
        head + "Выберите количество билетов. Каждый билет — отдельный слот с уникальным номером:",
        reply_markup=kb(rows),
    )


@user_router.callback_query(F.data.startswith("check:"))
async def check_sub(cb: CallbackQuery, state: FSMContext, bot: Bot):
    gid = int(cb.data.split(":")[1])
    g = db.get_giveaway(gid)
    if not g or g["status"] != "active":
        return await cb.answer("Розыгрыш завершён", show_alert=True)
    if not await is_subscribed(bot, cb.from_user.id, required_ids(g)):
        return await cb.answer("Вы ещё не подписаны на все каналы", show_alert=True)
    await cb.answer()
    try:
        await cb.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await join_flow(cb.message, state, bot, cb.from_user, gid)


@user_router.callback_query(Join.captcha, F.data.startswith("cap:"))
async def captcha_answer(cb: CallbackQuery, state: FSMContext, bot: Bot):
    d = await state.get_data()
    gid = d["gid"]
    if int(cb.data.split(":")[1]) != d["answer"]:
        await cb.answer("Неверно, попробуйте ещё раз", show_alert=True)
        try:
            await cb.message.delete()
        except Exception:
            pass
        return await send_captcha(cb.message, state, gid)
    db.mark_captcha(gid, cb.from_user.id)
    await state.clear()
    await cb.answer("✅ Проверка пройдена")
    try:
        await cb.message.delete()
    except Exception:
        pass
    await join_flow(cb.message, state, bot, cb.from_user, gid)


@user_router.callback_query(F.data.startswith("qty:"))
async def choose_qty(cb: CallbackQuery, state: FSMContext, bot: Bot):
    _, gid, qty = cb.data.split(":")
    gid, qty = int(gid), int(qty)
    g = db.get_giveaway(gid)
    if not g or g["status"] != "active":
        return await cb.answer("Розыгрыш завершён", show_alert=True)
    if not await is_subscribed(bot, cb.from_user.id, required_ids(g)):
        return await cb.answer("Подпишитесь на все каналы", show_alert=True)
    if g["captcha"] and not db.captcha_passed(gid, cb.from_user.id):
        await cb.answer()
        return await send_captcha(cb.message, state, gid)
    details = db.get_setting(f"payment:{g['owner_id']}")
    if not details:
        return await cb.answer("Оплата временно недоступна, попробуйте позже", show_alert=True)
    await state.set_state(Buy.receipt)
    await state.update_data(gid=gid, qty=qty)
    await cb.message.answer(
        f"К оплате: <b>{g['price'] * qty} ₽</b> ({qty} шт.)\n\n"
        f"Реквизиты:\n{esc(details)}\n\n"
        "После оплаты пришлите фото или PDF чека."
    )
    await cb.answer()


@user_router.message(Buy.receipt, F.photo | F.document)
async def got_receipt(m: Message, state: FSMContext):
    if m.photo:
        rid, rtype = m.photo[-1].file_id, "photo"
    else:
        rid, rtype = m.document.file_id, "document"
    await state.update_data(receipt_id=rid, receipt_type=rtype)
    await m.answer(
        "Чек получен. Нажмите кнопку, чтобы отправить заявку организатору:",
        reply_markup=kb([[btn("✅ Я оплатил", "paid")]]),
    )


@user_router.message(Buy.receipt)
async def receipt_hint(m: Message):
    await m.answer("Пришлите фото или PDF чека об оплате. Для отмены: /cancel")


@user_router.callback_query(F.data == "paid", Buy.receipt)
async def paid(cb: CallbackQuery, state: FSMContext, bot: Bot):
    d = await state.get_data()
    if "receipt_id" not in d:
        return await cb.answer("Сначала пришлите чек", show_alert=True)
    g = db.get_giveaway(d["gid"])
    if not g or g["status"] != "active":
        await state.clear()
        return await cb.answer("Розыгрыш уже завершён", show_alert=True)
    user = cb.from_user
    username = user.username or str(user.id)
    db.upsert_participant(g["id"], user.id, user.username, user.full_name)
    oid = db.add_order(g["id"], user.id, username, d["qty"], d["receipt_id"], d["receipt_type"])
    await state.clear()

    markup = kb([[btn("✅ Подтвердить", f"ok:{oid}"), btn("❌ Отклонить", f"no:{oid}")]])
    caption = (
        f"💸 Заявка #{oid}\n"
        f"Розыгрыш: {esc(g['title'])}\n"
        f"Пользователь: @{esc(username)} (ID {user.id})\n"
        f"Билетов: {d['qty']} | Сумма: {g['price'] * d['qty']} ₽"
    )
    try:
        if d["receipt_type"] == "photo":
            await bot.send_photo(g["owner_id"], d["receipt_id"], caption=caption, reply_markup=markup)
        else:
            await bot.send_document(g["owner_id"], d["receipt_id"], caption=caption, reply_markup=markup)
    except Exception:
        log.exception("не удалось отправить заявку %s организатору", oid)
        return await cb.message.edit_text("⚠️ Не удалось отправить заявку организатору. Попробуйте позже.")
    await cb.message.edit_text("⏳ Заявка отправлена. Ожидайте подтверждения оплаты.")
    await cb.answer()


@user_router.my_chat_member()
async def on_bot_membership(event: ChatMemberUpdated, bot: Bot):
    """Бота добавили в канал/группу админом или убрали оттуда."""
    chat = event.chat
    if chat.type not in ("channel", "supergroup", "group"):
        return
    status = event.new_chat_member.status
    who = event.from_user.id
    if status == "administrator":
        if who not in ADMIN_IDS:
            return
        await store_channel(bot, chat, who)
        await safe_send(
            bot, who,
            f"✅ Бот добавлен в «{esc(chat.title or str(chat.id))}». "
            "Канал доступен в разделе «📢 Мои каналы».")
    elif status in ("left", "kicked", "member", "restricted"):
        row = db.get_channel(chat.id)
        if row:
            db.remove_channel(chat.id)
            await safe_send(bot, row["added_by"],
                            f"ℹ️ Бот больше не администратор в «{esc(row['title'])}», канал убран из списка.")


# ======================================================================
# Админ-панель: меню, отмена, каналы, реквизиты, поддержка
# (меню регистрируется раньше FSM-обработчиков, чтобы кнопки работали в любом состоянии)
# ======================================================================

@admin_router.message(Command("cancel"))
async def admin_cancel_cmd(m: Message, state: FSMContext):
    await state.clear()
    await m.answer("Отменено.", reply_markup=ADMIN_KB)


@admin_router.callback_query(F.data == "cancel")
async def admin_cancel_cb(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.answer("Отменено")
    await cb.message.answer("Отменено.", reply_markup=ADMIN_KB)


@admin_router.message(F.text == "🛟 Поддержка")
async def support(m: Message, state: FSMContext):
    await state.clear()
    text = (
        "🛟 <b>Поддержка</b>\n\n"
        "<b>Как начать:</b>\n"
        "1. Добавьте бота администратором в свой канал (профиль бота → «Добавить в группу или канал»).\n"
        "2. Нажмите «➕ Создать розыгрыш» и пройдите шаги.\n"
        "3. Если участие платное, задайте «💳 Реквизиты». Заявки с чеками придут вам, "
        "после подтверждения участник получит номера билетов.\n"
        "4. Победителей можно выбрать рандомом или вручную по номерам билетов.\n\n"
        "<b>Частые вопросы:</b>\n"
        "• Бот не публикует пост — проверьте, что он админ канала с правом публикации.\n"
        "• Не проверяется подписка — бот должен быть админом во всех каналах из условий.\n"
        "• Время указывается по Москве (МСК).\n"
    )
    if SUPPORT_CONTACT:
        text += f"\nКонтакт: {esc(SUPPORT_CONTACT)}"
    await m.answer(text, reply_markup=ADMIN_KB)


@admin_router.message(F.text == "💳 Реквизиты")
async def req_start(m: Message, state: FSMContext):
    await state.clear()
    current = db.get_setting(f"payment:{m.from_user.id}", "не заданы")
    await state.set_state(Requisites.text)
    await m.answer(
        f"Текущие реквизиты:\n{esc(current)}\n\n"
        "Пришлите новый текст реквизитов. Они показываются участникам платных розыгрышей. "
        "Для отмены: /cancel")


@admin_router.message(F.text == "📢 Мои каналы")
async def my_channels(m: Message, state: FSMContext):
    await state.clear()
    chans = db.list_channels(m.from_user.id)
    if chans:
        text = "📢 <b>Ваши каналы</b>, где бот администратор:\n\n" + "\n".join(
            f"• {esc(c['title'])}" + (f" (@{esc(c['username'])})" if c["username"] else "") for c in chans)
    else:
        text = "У вас пока нет подключённых каналов."
    text += ("\n\nЧтобы добавить канал: откройте профиль бота → «Добавить в группу или канал» "
             "и выдайте права администратора. Либо подключите по @username кнопкой ниже.")
    rows = [[btn(f"🗑 Убрать «{c['title']}»", f"chdel:{c['chat_id']}")] for c in chans]
    rows.append([btn("➕ Добавить по @username", "chadd")])
    await m.answer(text, reply_markup=kb(rows))


@admin_router.message(F.text == "➕ Создать розыгрыш")
async def create_start(m: Message, state: FSMContext):
    await state.clear()
    if not db.list_channels(m.from_user.id):
        return await m.answer(
            "Сначала подключите канал: добавьте бота администратором в канал "
            "или используйте «📢 Мои каналы» → «Добавить по @username».")
    await state.set_state(Create.title)
    await m.answer("📝 Название розыгрыша (для вашего списка и заголовка поста):",
                   reply_markup=kb([CANCEL]))


@admin_router.message(F.text == "🎁 Мои розыгрыши")
async def my_giveaways(m: Message, state: FSMContext):
    await state.clear()
    gs = db.list_giveaways(m.from_user.id)
    if not gs:
        return await m.answer("У вас пока нет розыгрышей.")
    rows = [[btn(f"{STATUS_ICON.get(g['status'], '•')} #{g['id']} {g['title']}", f"gv:{g['id']}")]
            for g in gs]
    await m.answer("🎁 <b>Ваши розыгрыши</b>:", reply_markup=kb(rows))


@admin_router.message(Requisites.text, F.text)
async def req_save(m: Message, state: FSMContext):
    db.set_setting(f"payment:{m.from_user.id}", m.text.strip())
    await state.clear()
    await m.answer("✅ Реквизиты сохранены.", reply_markup=ADMIN_KB)


@admin_router.callback_query(F.data.startswith("chdel:"))
async def channel_delete(cb: CallbackQuery):
    chat_id = int(cb.data.split(":", 1)[1])
    row = db.get_channel(chat_id)
    if row and row["added_by"] == cb.from_user.id:
        db.remove_channel(chat_id)
        await cb.message.answer(f"Канал «{esc(row['title'])}» убран из списка.")
    await cb.answer()


@admin_router.callback_query(F.data == "chadd")
async def channel_add_start(cb: CallbackQuery, state: FSMContext):
    await state.set_state(AddChannel.username)
    await cb.message.answer(
        "Пришлите @username канала. Бот должен быть в нём администратором. Для отмены: /cancel")
    await cb.answer()


@admin_router.message(AddChannel.username, F.text)
async def channel_add_save(m: Message, state: FSMContext, bot: Bot):
    chat, error = await register_channel(bot, m.text, m.from_user.id)
    if error:
        return await m.answer(error)
    await state.clear()
    await m.answer(f"✅ Канал «{esc(chat.title or str(chat.id))}» подключён.", reply_markup=ADMIN_KB)


# ======================================================================
# Создание розыгрыша
# ======================================================================

@admin_router.message(Create.title, F.text)
async def c_title(m: Message, state: FSMContext):
    await state.update_data(title=m.text.strip()[:100])
    await state.set_state(Create.text)
    await m.answer(
        "✍️ Текст розыгрыша: что разыгрываем и условия. Форматирование сохранится.",
        reply_markup=kb([CANCEL]))


@admin_router.message(Create.text, F.text)
async def c_text(m: Message, state: FSMContext):
    if len(m.text) > 2500:
        return await m.answer("Слишком длинный текст (максимум 2500 символов). Сократите.")
    await state.update_data(text=m.html_text, photos=[])
    await state.set_state(Create.photos)
    await m.answer(
        "📷 Пришлите до 3 фото приза или нажмите «Далее», чтобы пропустить.",
        reply_markup=kb([[btn("➡️ Далее", "ph:next")], CANCEL]))


@admin_router.message(Create.photos, F.photo)
async def c_photo(m: Message, state: FSMContext):
    d = await state.get_data()
    photos = d.get("photos", [])
    if len(photos) >= 3:
        return await m.answer("Максимум 3 фото. Нажмите «Далее».",
                              reply_markup=kb([[btn("➡️ Далее", "ph:next")], CANCEL]))
    photos.append(m.photo[-1].file_id)
    await state.update_data(photos=photos)
    await m.answer(f"📷 Фото добавлено ({len(photos)}/3).",
                   reply_markup=kb([[btn("➡️ Далее", "ph:next")], CANCEL]))


@admin_router.callback_query(Create.photos, F.data == "ph:next")
async def c_photos_next(cb: CallbackQuery, state: FSMContext):
    await state.set_state(Create.price)
    await cb.message.answer(
        "💰 Стоимость одного билета в рублях (целое число). Введите 0, если участие бесплатное:",
        reply_markup=kb([CANCEL]))
    await cb.answer()


@admin_router.message(Create.price, F.text)
async def c_price(m: Message, state: FSMContext):
    if not m.text.strip().isdigit():
        return await m.answer("Введите целое число, например 100 (или 0 для бесплатного).")
    await state.update_data(price=int(m.text.strip()))
    await state.set_state(Create.btn)
    rows = [[btn(t, f"bt:{i}")] for i, t in enumerate(BTN_OPTIONS)]
    rows.append([btn("✏️ Свой текст кнопки", "bt:c")])
    rows.append(CANCEL)
    await m.answer("🔘 Текст кнопки под постом:", reply_markup=kb(rows))


@admin_router.callback_query(Create.btn, F.data.startswith("bt:"))
async def c_btn(cb: CallbackQuery, state: FSMContext):
    choice = cb.data.split(":")[1]
    await cb.answer()
    if choice == "c":
        await state.set_state(Create.btn_custom)
        return await cb.message.answer("Пришлите свой текст кнопки (до 40 символов):",
                                       reply_markup=kb([CANCEL]))
    await state.update_data(btn_text=BTN_OPTIONS[int(choice)])
    await ask_winners(cb.message, state)


@admin_router.message(Create.btn_custom, F.text)
async def c_btn_custom(m: Message, state: FSMContext):
    if len(m.text) > 40:
        return await m.answer("Слишком длинно, максимум 40 символов.")
    await state.update_data(btn_text=m.text.strip())
    await ask_winners(m, state)


async def ask_winners(message: Message, state: FSMContext):
    await state.set_state(Create.winners)
    await message.answer("🏆 Сколько будет победителей? (от 1 до 100)", reply_markup=kb([CANCEL]))


@admin_router.message(Create.winners, F.text)
async def c_winners(m: Message, state: FSMContext):
    t = m.text.strip()
    if not t.isdigit() or not 1 <= int(t) <= 100:
        return await m.answer("Введите число от 1 до 100.")
    await state.update_data(winners=int(t))
    await ask_channel(m, state, m.from_user.id)


async def ask_channel(message: Message, state: FSMContext, uid: int):
    chans = db.list_channels(uid)
    if not chans:
        await state.clear()
        return await message.answer("У вас нет подключённых каналов.", reply_markup=ADMIN_KB)
    rows = [[btn(f"📢 {c['title']}", f"pc:{c['chat_id']}")] for c in chans]
    rows.append(CANCEL)
    await state.set_state(Create.channel)
    await message.answer("📢 В каком канале опубликовать розыгрыш?", reply_markup=kb(rows))


@admin_router.callback_query(Create.channel, F.data.startswith("pc:"))
async def c_channel(cb: CallbackQuery, state: FSMContext):
    chat_id = int(cb.data.split(":", 1)[1])
    if chat_id not in {c["chat_id"] for c in db.list_channels(cb.from_user.id)}:
        return await cb.answer("Канал не найден", show_alert=True)
    await state.update_data(post_chat=chat_id, extra=[])
    await cb.answer()
    await ask_extras(cb.message, state, cb.from_user.id)


async def extras_markup(uid: int, post_chat: int, selected: list):
    rows = []
    for c in db.list_channels(uid):
        if c["chat_id"] == post_chat:
            continue
        mark = "✅" if c["chat_id"] in selected else "▫️"
        rows.append([btn(f"{mark} {c['title']}", f"ex:{c['chat_id']}")])
    rows.append([btn("➕ Добавить по @username", "exadd")])
    rows.append([btn("➡️ Далее", "exdone")])
    rows.append(CANCEL)
    return kb(rows)


async def ask_extras(message: Message, state: FSMContext, uid: int):
    d = await state.get_data()
    await state.set_state(Create.extra)
    await message.answer(
        "🔗 Дополнительные каналы для обязательной подписки (необязательно).\n"
        "Подписка на канал розыгрыша проверяется всегда. Отметьте остальные каналы, "
        "где бот администратор, или добавьте по @username:",
        reply_markup=await extras_markup(uid, d["post_chat"], d.get("extra", [])))


@admin_router.callback_query(Create.extra, F.data.startswith("ex:"))
async def c_extra_toggle(cb: CallbackQuery, state: FSMContext):
    chat_id = int(cb.data.split(":", 1)[1])
    d = await state.get_data()
    extra = d.get("extra", [])
    if chat_id in extra:
        extra.remove(chat_id)
    else:
        extra.append(chat_id)
    await state.update_data(extra=extra)
    await cb.message.edit_reply_markup(
        reply_markup=await extras_markup(cb.from_user.id, d["post_chat"], extra))
    await cb.answer()


@admin_router.callback_query(Create.extra, F.data == "exadd")
async def c_extra_add_start(cb: CallbackQuery, state: FSMContext):
    await state.set_state(Create.extra_add)
    await cb.message.answer("Пришлите @username канала (бот должен быть в нём администратором):",
                            reply_markup=kb([CANCEL]))
    await cb.answer()


@admin_router.message(Create.extra_add, F.text)
async def c_extra_add(m: Message, state: FSMContext, bot: Bot):
    chat, error = await register_channel(bot, m.text, m.from_user.id)
    if error:
        return await m.answer(error)
    d = await state.get_data()
    extra = d.get("extra", [])
    if chat.id != d["post_chat"] and chat.id not in extra:
        extra.append(chat.id)
    await state.update_data(extra=extra)
    await ask_extras(m, state, m.from_user.id)


@admin_router.callback_query(Create.extra, F.data == "exdone")
async def c_extra_done(cb: CallbackQuery, state: FSMContext):
    await state.set_state(Create.start_mode)
    await cb.message.answer(
        "🚀 Когда опубликовать розыгрыш?",
        reply_markup=kb([[btn("⚡ Сразу", "st:now"), btn("🕐 Указать время", "st:time")], CANCEL]))
    await cb.answer()


@admin_router.callback_query(Create.start_mode, F.data.startswith("st:"))
async def c_start_mode(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    if cb.data == "st:now":
        await state.update_data(start_at=int(time.time()))
        return await ask_end_mode(cb.message, state)
    await state.set_state(Create.start_at)
    await cb.message.answer("Время публикации по Москве, формат: <code>ДД.ММ.ГГГГ ЧЧ:ММ</code>",
                            reply_markup=kb([CANCEL]))


@admin_router.message(Create.start_at, F.text)
async def c_start_at(m: Message, state: FSMContext):
    ts = logic.parse_msk(m.text)
    if ts is None:
        return await m.answer("Не понял формат. Пример: <code>25.12.2026 18:30</code>")
    if ts <= time.time():
        return await m.answer("Это время уже прошло. Укажите время в будущем.")
    await state.update_data(start_at=ts)
    await ask_end_mode(m, state)


async def ask_end_mode(message: Message, state: FSMContext):
    await state.set_state(Create.end_mode)
    await message.answer(
        "🏁 Как завершить розыгрыш?",
        reply_markup=kb([[btn("⏰ По времени", "en:time"),
                          btn("👥 По числу участников", "en:count")], CANCEL]))


@admin_router.callback_query(Create.end_mode, F.data.startswith("en:"))
async def c_end_mode(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    if cb.data == "en:time":
        await state.update_data(end_mode="time")
        await state.set_state(Create.end_at)
        return await cb.message.answer(
            "Время завершения по Москве, формат: <code>ДД.ММ.ГГГГ ЧЧ:ММ</code>",
            reply_markup=kb([CANCEL]))
    await state.update_data(end_mode="count")
    await state.set_state(Create.end_count)
    await cb.message.answer("Сколько участников нужно набрать для завершения?",
                            reply_markup=kb([CANCEL]))


@admin_router.message(Create.end_at, F.text)
async def c_end_at(m: Message, state: FSMContext):
    ts = logic.parse_msk(m.text)
    d = await state.get_data()
    if ts is None:
        return await m.answer("Не понял формат. Пример: <code>31.12.2026 20:00</code>")
    if ts <= max(time.time(), d["start_at"]):
        return await m.answer("Время завершения должно быть позже публикации и в будущем.")
    await state.update_data(end_at=ts)
    await ask_draw_mode(m, state)


@admin_router.message(Create.end_count, F.text)
async def c_end_count(m: Message, state: FSMContext):
    t = m.text.strip()
    if not t.isdigit() or int(t) < 1:
        return await m.answer("Введите целое число больше нуля.")
    await state.update_data(end_count=int(t))
    await ask_draw_mode(m, state)


async def ask_draw_mode(message: Message, state: FSMContext):
    await state.set_state(Create.draw_mode)
    await message.answer(
        "🎯 Как выбрать победителей после завершения?\n\n"
        "🎲 Автоматически — бот сам выберет случайно и объявит итоги.\n"
        "✍️ Вручную — бот закроет приём участников и ждёт, пока вы укажете номера билетов "
        "(рандом тоже будет доступен кнопкой).",
        reply_markup=kb([[btn("🎲 Автоматически", "dm:random"), btn("✍️ Вручную", "dm:manual")], CANCEL]))


@admin_router.callback_query(Create.draw_mode, F.data.startswith("dm:"))
async def c_draw_mode(cb: CallbackQuery, state: FSMContext):
    d = await state.get_data()
    await state.update_data(draw_mode=cb.data.split(":")[1],
                            boost=0, captcha=1 if d["price"] == 0 else 0)
    await cb.answer()
    await ask_options(cb.message, state)


def options_markup(d: dict):
    return kb([
        [btn(f"🚀 Буст канала = +1 шанс: {'ВКЛ' if d.get('boost') else 'ВЫКЛ'}", "opt:boost")],
        [btn(f"🤖 Капча от накрутки: {'ВКЛ' if d.get('captcha') else 'ВЫКЛ'}", "opt:captcha")],
        [btn("➡️ Далее", "opt:done")],
        CANCEL,
    ])


async def ask_options(message: Message, state: FSMContext):
    d = await state.get_data()
    await state.set_state(Create.options)
    await message.answer(
        "⚙️ Дополнительно:\n"
        "• Буст — участники, забустившие канал розыгрыша, получают дополнительный слот "
        "(проверяется при подведении итогов).\n"
        "• Капча — простая проверка от ботов перед участием.",
        reply_markup=options_markup(d))


@admin_router.callback_query(Create.options, F.data.in_({"opt:boost", "opt:captcha"}))
async def c_option_toggle(cb: CallbackQuery, state: FSMContext):
    key = cb.data.split(":")[1]
    d = await state.get_data()
    await state.update_data(**{key: 0 if d.get(key) else 1})
    d = await state.get_data()
    await cb.message.edit_reply_markup(reply_markup=options_markup(d))
    await cb.answer()


def summary(d: dict, uid: int) -> str:
    lines = [
        "📋 <b>Проверьте розыгрыш</b>", "",
        f"Название: {esc(d['title'])}",
        f"Фото: {len(d.get('photos', []))}",
        f"Билет: {str(d['price']) + ' ₽' if d['price'] else 'бесплатно'}",
        f"Кнопка: {esc(d['btn_text'])}",
        f"Победителей: {d['winners']}",
        f"Канал: {esc(channel_title(d['post_chat']))}",
    ]
    if d.get("extra"):
        lines.append("Доп. подписка: " + ", ".join(esc(channel_title(c)) for c in d["extra"]))
    lines.append("Публикация: " + ("сразу" if d["start_at"] <= time.time() + 5
                                   else logic.fmt_msk(d["start_at"])))
    lines.append("Завершение: " + (logic.fmt_msk(d["end_at"]) if d["end_mode"] == "time"
                                   else f"при {d['end_count']} участниках"))
    lines.append("Победители: " + ("рандом автоматически" if d["draw_mode"] == "random" else "выбираю вручную"))
    lines.append(f"Буст: {'да' if d.get('boost') else 'нет'}, капча: {'да' if d.get('captcha') else 'нет'}")
    if d["price"] > 0 and not db.get_setting(f"payment:{uid}"):
        lines.append("\n⚠️ Реквизиты не заданы. Добавьте их в «💳 Реквизиты», иначе оплата будет недоступна.")
    return "\n".join(lines)


@admin_router.callback_query(Create.options, F.data == "opt:done")
async def c_options_done(cb: CallbackQuery, state: FSMContext):
    d = await state.get_data()
    await state.set_state(Create.confirm)
    await cb.message.answer(
        summary(d, cb.from_user.id),
        reply_markup=kb([[btn("✅ Сохранить", "cf:save")], CANCEL]))
    await cb.answer()


@admin_router.callback_query(Create.confirm, F.data == "cf:save")
async def c_save(cb: CallbackQuery, state: FSMContext, bot: Bot):
    d = await state.get_data()
    uid = cb.from_user.id
    gid = db.add_giveaway(
        owner_id=uid, title=d["title"], text=d["text"], photos=d.get("photos", []),
        price=d["price"], btn_text=d["btn_text"], extra=d.get("extra", []),
        post_chat_id=d["post_chat"], winners_count=d["winners"], start_at=d["start_at"],
        end_mode=d["end_mode"], end_at=d.get("end_at"), end_count=d.get("end_count"),
        draw_mode=d["draw_mode"], boost=int(d.get("boost", 0)),
        captcha=int(d.get("captcha", 0)), status="scheduled",
    )
    await state.clear()
    await cb.answer("Сохранено")
    if d["start_at"] <= time.time() + 5:
        await cb.message.answer(f"✅ Розыгрыш #{gid} создан, публикую…", reply_markup=ADMIN_KB)
        await start_giveaway(bot, gid)
    else:
        await cb.message.answer(
            f"✅ Розыгрыш #{gid} создан. Он будет опубликован {logic.fmt_msk(d['start_at'])}.",
            reply_markup=ADMIN_KB)


# ======================================================================
# Мои розыгрыши, участники, выбор победителей
# ======================================================================

def giveaway_card(g: dict) -> str:
    gid = g["id"]
    lines = [
        f"{STATUS_ICON.get(g['status'], '•')} <b>#{gid} {esc(g['title'])}</b>",
        f"Статус: {STATUS_NAME.get(g['status'], g['status'])}",
        f"Канал: {esc(channel_title(g['post_chat_id']))}",
        f"Билет: {str(g['price']) + ' ₽' if g['price'] else 'бесплатно'}",
        f"Победителей: {g['winners_count']} ({'рандом' if g['draw_mode'] == 'random' else 'вручную'})",
        "Публикация: " + logic.fmt_msk(g["start_at"]),
        "Завершение: " + (logic.fmt_msk(g["end_at"]) if g["end_mode"] == "time"
                          else f"при {g['end_count']} участниках"),
        f"Участников: {db.count_participants(gid)}, билетов: {db.count_tickets(gid)}",
    ]
    if g["status"] == "finished":
        winners = db.get_winners(gid)
        if winners:
            lines.append("\n🏆 Победители:")
            for w in winners:
                name = f"@{w['username']}" if w.get("username") else (w.get("full_name") or str(w["user_id"]))
                lines.append(f"• {esc(name)} (ID {w['user_id']}) — билет №{w['ticket']}")
        else:
            lines.append("\nПобедителей нет.")
    return "\n".join(lines)


def giveaway_buttons(g: dict):
    gid, st = g["id"], g["status"]
    rows = []
    if st == "active":
        rows.append([btn("🏁 Завершить сейчас", f"gvf:{gid}")])
    if st == "ended":
        rows.append([btn("🎲 Рандом", f"gvr:{gid}"), btn("✍️ Вручную", f"gvm:{gid}")])
    if st in ("active", "ended", "finished"):
        rows.append([btn("👥 Участники (CSV)", f"gvp:{gid}")])
    if st in ("scheduled", "active"):
        rows.append([btn("⛔ Отменить розыгрыш", f"gvx:{gid}")])
    return kb(rows) if rows else None


def own_giveaway(cb: CallbackQuery):
    gid = int(cb.data.split(":")[1])
    g = db.get_giveaway(gid)
    if not g or g["owner_id"] != cb.from_user.id:
        return None
    return g


@admin_router.callback_query(F.data.startswith("gv:"))
async def giveaway_open(cb: CallbackQuery):
    g = own_giveaway(cb)
    if not g:
        return await cb.answer("Розыгрыш не найден", show_alert=True)
    await cb.message.answer(giveaway_card(g), reply_markup=giveaway_buttons(g))
    await cb.answer()


@admin_router.callback_query(F.data.startswith("gvp:"))
async def giveaway_participants(cb: CallbackQuery):
    g = own_giveaway(cb)
    if not g:
        return await cb.answer("Розыгрыш не найден", show_alert=True)
    rows = db.participants_report(g["id"])
    if not rows:
        return await cb.answer("Участников пока нет", show_alert=True)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["user_id", "username", "name", "tickets_count", "tickets"])
    for r in rows:
        w.writerow([r["user_id"], r["username"] or "", r["full_name"] or "", r["n"], r["tickets"]])
    doc = BufferedInputFile(buf.getvalue().encode("utf-8-sig"),
                            filename=f"giveaway_{g['id']}_participants.csv")
    await cb.message.answer_document(doc, caption=f"👥 Участники розыгрыша #{g['id']}")
    await cb.answer()


@admin_router.callback_query(F.data.startswith("gvf:"))
async def giveaway_finish_menu(cb: CallbackQuery):
    g = own_giveaway(cb)
    if not g or g["status"] != "active":
        return await cb.answer("Недоступно", show_alert=True)
    await cb.message.answer(
        f"Завершить «{esc(g['title'])}» сейчас? Приём участников закроется. Выберите способ:",
        reply_markup=draw_buttons(g["id"]))
    await cb.answer()


@admin_router.callback_query(F.data.startswith("gvr:"))
async def giveaway_random(cb: CallbackQuery, bot: Bot):
    g = own_giveaway(cb)
    if not g:
        return await cb.answer("Розыгрыш не найден", show_alert=True)
    await cb.answer("Выбираю…")
    if not await do_random_draw(bot, g["id"]):
        return await cb.message.answer("Розыгрыш уже завершён или обрабатывается.")


@admin_router.callback_query(F.data.startswith("gvm:"))
async def giveaway_manual_start(cb: CallbackQuery, state: FSMContext):
    g = own_giveaway(cb)
    if not g or g["status"] not in ("active", "ended"):
        return await cb.answer("Недоступно", show_alert=True)
    await state.set_state(Manual.tickets)
    await state.update_data(gid=g["id"])
    await cb.message.answer(
        f"✍️ Введите номера билетов победителей (до {g['winners_count']}) "
        "через пробел или запятую. Список билетов — кнопка «👥 Участники». Для отмены: /cancel")
    await cb.answer()


@admin_router.message(Manual.tickets, F.text)
async def manual_pick(m: Message, state: FSMContext, bot: Bot):
    d = await state.get_data()
    g = db.get_giveaway(d["gid"])
    if not g or g["status"] not in ("active", "ended"):
        await state.clear()
        return await m.answer("Розыгрыш уже завершён.", reply_markup=ADMIN_KB)
    nums = logic.parse_ticket_numbers(m.text)
    if not nums:
        return await m.answer("Не нашёл номеров билетов. Введите числа через пробел или запятую.")
    if len(nums) > g["winners_count"]:
        return await m.answer(f"Можно выбрать не больше {g['winners_count']} победителей.")
    pairs, seen = [], set()
    for n in nums:
        t = db.find_ticket(g["id"], n)
        if not t:
            return await m.answer(f"Билета №{n} нет в этом розыгрыше.")
        if t["user_id"] in seen:
            return await m.answer("Среди номеров есть несколько билетов одного участника. Укажите разные.")
        seen.add(t["user_id"])
        pairs.append((t["user_id"], n))
    if not db.set_status_if(g["id"], ("active", "ended"), "drawing"):
        await state.clear()
        return await m.answer("Розыгрыш уже завершён.", reply_markup=ADMIN_KB)
    await state.clear()
    g = db.get_giveaway(g["id"])
    await remove_post_button(bot, g)
    await finish(bot, g, pairs)
    await m.answer("✅ Победители выбраны и объявлены.", reply_markup=ADMIN_KB)


@admin_router.callback_query(F.data.startswith("gvx:"))
async def giveaway_cancel(cb: CallbackQuery, bot: Bot):
    g = own_giveaway(cb)
    if not g:
        return await cb.answer("Розыгрыш не найден", show_alert=True)
    if not db.set_status_if(g["id"], ("scheduled", "active"), "cancelled"):
        return await cb.answer("Нельзя отменить в текущем статусе", show_alert=True)
    await remove_post_button(bot, g)
    await cb.message.answer(f"⛔ Розыгрыш #{g['id']} отменён.")
    await cb.answer()


# ======================================================================
# Подтверждение оплаты
# ======================================================================

@admin_router.callback_query(F.data.startswith(("ok:", "no:")))
async def decide_order(cb: CallbackQuery, bot: Bot):
    action, oid = cb.data.split(":")
    oid = int(oid)
    approve = action == "ok"
    order = db.get_order(oid)
    g = db.get_giveaway(order["giveaway_id"]) if order else None
    if not order or not g or g["owner_id"] != cb.from_user.id:
        return await cb.answer("Заявка не найдена", show_alert=True)
    if approve and g["status"] != "active":
        return await cb.answer(
            "Приём участников в этом розыгрыше уже закрыт. Билеты выдать нельзя — "
            "отклоните заявку и свяжитесь с участником.", show_alert=True)
    if not db.decide_order(oid, "approved" if approve else "rejected"):
        return await cb.answer("Заявка уже обработана", show_alert=True)

    try:
        await cb.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    if approve:
        numbers = db.issue_tickets(g["id"], order["user_id"], order["qty"])
        nums_html = ", ".join(f"<code>{n}</code>" for n in numbers)
        await safe_send(
            bot, order["user_id"],
            f"✅ Оплата подтверждена! Ваши номера билетов в розыгрыше «{esc(g['title'])}»: {nums_html}\n"
            "Чем больше билетов — тем больше шансов!")
        await cb.message.answer(f"Заявка #{oid} подтверждена. Номера: {', '.join(map(str, numbers))}")
        await maybe_close_by_count(bot, g["id"])
    else:
        await safe_send(
            bot, order["user_id"],
            f"❌ Оплата в розыгрыше «{esc(g['title'])}» не подтверждена. Свяжитесь с организатором.")
        await cb.message.answer(f"Заявка #{oid} отклонена.")
    await cb.answer()


# ======================================================================
# Планировщик и запуск
# ======================================================================

async def scheduler(bot: Bot):
    """Каждые 15 секунд: публикует запланированные и закрывает завершившиеся по времени."""
    while True:
        try:
            now = int(time.time())
            for g in db.due_scheduled(now):
                await start_giveaway(bot, g["id"])
            for g in db.due_active(now):
                await close_entries(bot, g["id"])
        except Exception:
            log.exception("ошибка планировщика")
        await asyncio.sleep(15)


async def main():
    logging.basicConfig(level=logging.INFO)
    db.init_db()
    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(admin_router)
    dp.include_router(user_router)
    scheduler_task = asyncio.create_task(scheduler(bot))
    try:
        await dp.start_polling(bot)
    finally:
        scheduler_task.cancel()


if __name__ == "__main__":
    asyncio.run(main())
