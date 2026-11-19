import os
import re
import sys


def _clean(value: str) -> str:
    """Убирает пробелы и кавычки, которые часто попадают в значение переменной на хостинге."""
    return value.strip().strip("\"'").strip()


def _require(name: str) -> str:
    value = _clean(os.environ.get(name, ""))
    if not value:
        sys.exit(
            f"[ОШИБКА] Не задана переменная окружения {name}.\n"
            f"Добавьте её в настройках хостинга (Environment Variables) и перезапустите бота."
        )
    return value


BOT_TOKEN = _require("BOT_TOKEN")

_raw_admins = _require("ADMIN_IDS")
# допускаем разделители: запятая, пробел, точка с запятой, скобки
ADMIN_IDS = {int(x) for x in re.findall(r"-?\d+", _raw_admins)}
if not ADMIN_IDS:
    sys.exit(
        "[ОШИБКА] В ADMIN_IDS нет ни одного числового ID.\n"
        "Пример значения: 123456789 или 123456789,987654321"
    )

# необязательно: контакт поддержки, который покажется в разделе «Поддержка», например @username
SUPPORT_CONTACT = _clean(os.environ.get("SUPPORT_CONTACT", ""))

# необязательно: 1 — бот будет удалять и сообщения самих пользователей (текст, фото, видео)
CLEAN_USER_MESSAGES = _clean(os.environ.get("CLEAN_USER_MESSAGES", "")).lower() in ("1", "true", "yes", "on", "да")


# ---- ЮKassa через платежи Telegram (необязательно) ----
# Токен платёжного провайдера из @BotFather (Payments → ЮKassa). Пусто — ЮKassa отключена.
YOOKASSA_PROVIDER_TOKEN = _clean(os.environ.get("YOOKASSA_PROVIDER_TOKEN", ""))
# 1 — передавать данные чека (54-ФЗ) в ЮKassa и запрашивать у покупателя e-mail
YOOKASSA_RECEIPT = _clean(os.environ.get("YOOKASSA_RECEIPT", "")).lower() in ("1", "true", "yes", "on", "да")


def _int_env(name: str, default):
    raw = _clean(os.environ.get(name, ""))
    return int(raw) if raw.isdigit() else default


# код НДС для чека: 1 — без НДС, 2 — 0%, 3 — 10%, 4 — 20%
YOOKASSA_VAT_CODE = _int_env("YOOKASSA_VAT_CODE", 1)
# система налогообложения для чека (1–6); не задано — берётся из настроек магазина
YOOKASSA_TAX_SYSTEM = _int_env("YOOKASSA_TAX_SYSTEM", None)
