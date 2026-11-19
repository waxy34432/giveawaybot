"""Чистая логика без зависимостей от Telegram: время, розыгрыш, разбор номеров."""
import html
import random
import re
from datetime import datetime, timedelta, timezone

MSK = timezone(timedelta(hours=3))


def parse_msk(text: str):
    """'25.12.2026 18:30' (МСК) -> unix-время в секундах или None."""
    text = text.strip()
    for fmt in ("%d.%m.%Y %H:%M", "%d.%m.%y %H:%M"):
        try:
            return int(datetime.strptime(text, fmt).replace(tzinfo=MSK).timestamp())
        except ValueError:
            continue
    return None


def fmt_msk(ts: int) -> str:
    return datetime.fromtimestamp(ts, MSK).strftime("%d.%m.%Y %H:%M") + " МСК"


def weighted_draw(entries: dict, count: int, rng=random) -> list:
    """Выбирает до count разных пользователей. entries: user_id -> число слотов (вес)."""
    pool = {u: w for u, w in entries.items() if w > 0}
    winners = []
    while pool and len(winners) < count:
        users = list(pool)
        weights = [pool[u] for u in users]
        chosen = rng.choices(users, weights=weights, k=1)[0]
        winners.append(chosen)
        del pool[chosen]
    return winners


def parse_ticket_numbers(text: str) -> list:
    """Достаёт номера билетов из строки, сохраняя порядок и убирая повторы."""
    seen, result = set(), []
    for x in re.findall(r"\d+", text):
        n = int(x)
        if n not in seen:
            seen.add(n)
            result.append(n)
    return result


_BULLET_RE = re.compile(r"^([-–—*•·+]|\d+[.)])\s+(.*)$")


def autoformat(text: str) -> str:
    """Приводит текст поста к аккуратному виду (HTML Telegram сохраняется).

    - убирает лишние пробелы и пустые строки;
    - маркеры списка (-, *, •, 1)) превращает в ▫️ или оставляет нумерацию;
    - строки-заголовки, заканчивающиеся на «:», выделяет жирным;
    - первую букву строки делает заглавной.
    Функция идемпотентна: повторный вызов ничего не меняет.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    out, prev_blank = [], True
    for raw in text.split("\n"):
        line = re.sub(r"[ \t]+", " ", raw).strip()
        if not line:
            if not prev_blank:
                out.append("")
            prev_blank = True
            continue
        prev_blank = False
        m = _BULLET_RE.match(line)
        if m:
            marker, rest = m.groups()
            prefix = f"{marker} " if marker[0].isdigit() else "▫️ "
            line = prefix + (rest[:1].upper() + rest[1:] if rest[:1].isalpha() else rest)
        else:
            if line[0].isalpha():
                line = line[0].upper() + line[1:]
            if line.endswith(":") and "<" not in line and len(line) <= 60:
                line = f"<b>{line}</b>"
        out.append(line)
    while out and out[-1] == "":
        out.pop()
    return "\n".join(out)


def visible_len(html_text: str) -> int:
    """Длина текста так, как её считает Telegram: без HTML-тегов, с раскрытыми &amp; и т. п."""
    return len(html.unescape(re.sub(r"<[^>]+>", "", html_text)))


def build_receipt(description: str, qty: int, unit_price: int, vat_code: int = 1, tax_system=None) -> dict:
    """Данные чека для provider_data ЮKassa: одна позиция «билеты» (цена за штуку × количество)."""
    receipt = {
        "items": [{
            "description": description[:128],
            "quantity": f"{qty}.00",
            "amount": {"value": f"{unit_price}.00", "currency": "RUB"},
            "vat_code": vat_code,
            "payment_mode": "full_payment",
            "payment_subject": "service",
        }]
    }
    if tax_system:
        receipt["tax_system_code"] = tax_system
    return {"receipt": receipt}


def parse_invoice_payload(payload: str):
    """'gv:12:3' -> (12, 3) или None."""
    parts = (payload or "").split(":")
    if len(parts) == 3 and parts[0] == "gv" and parts[1].isdigit() and parts[2].isdigit():
        return int(parts[1]), int(parts[2])
    return None
