import telegram
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes
import requests
from barcode import Code128
from barcode.writer import ImageWriter
from io import BytesIO
from datetime import datetime, timezone, timedelta
import os
import telegram.ext

# ==============================================================================
# НАСТРОЙКИ — все значения берутся из переменных окружения Render
# ==============================================================================

TELEGRAM_BOT_TOKEN  = os.environ.get("TELEGRAM_BOT_TOKEN")
SUPERSET_URL        = os.environ.get("SUPERSET_URL", "https://superset.dwh.teez.kz")
SUPERSET_USERNAME   = os.environ.get("SUPERSET_USERNAME")
SUPERSET_PASSWORD   = os.environ.get("SUPERSET_PASSWORD")
SUPERSET_DATASET_ID = int(os.environ.get("SUPERSET_DATASET_ID", "0"))

# Статусные поля в порядке движения заказа
STATUS_FIELDS = [
    "Принят на ПВЗ (Отправка)",
    "Отгружен на склад",
    "Принят на складе",
    "Отгружен со склада",
    "Готово к выдаче на ПВЗ/Принят на ПВ",
    "Выдано",
    "Возвращено отправителю",
    "Срок хранения истек",
]

ONE_DAY_MS = 24 * 60 * 60 * 1000
TZ_PLUS5   = timezone(timedelta(hours=5))


# ==============================================================================
# РАБОТА С SUPERSET API
# ==============================================================================

def get_superset_token():
    url = f"{SUPERSET_URL}/api/v1/security/login"
    payload = {
        "username": SUPERSET_USERNAME,
        "password": SUPERSET_PASSWORD,
        "provider": "ldap",
        "refresh": True
    }
    try:
        response = requests.post(url, json=payload, timeout=15)
        response.raise_for_status()
        return response.json().get("access_token")
    except Exception as e:
        print(f"ОШИБКА АВТОРИЗАЦИИ SUPERSET: {e}")
        return None


def format_date_value(val):
    if not val:
        return None, None
    try:
        if isinstance(val, (int, float)):
            dt = datetime.fromtimestamp(val / 1000, tz=TZ_PLUS5)
            return dt.strftime("%d.%m.%Y %H:%M"), val
        else:
            s = str(val).replace("T", " ")[:16]
            return s, None
    except Exception:
        return str(val)[:16], None


def build_movement_status(row):
    lines = []
    prev_ts = None
    for field in STATUS_FIELDS:
        val = row.get(field)
        if not val:
            continue
        date_str, ts = format_date_value(val)
        if not date_str:
            continue
        delay = ""
        if prev_ts is not None and ts is not None:
            if (ts - prev_ts) > ONE_DAY_MS:
                delay = " 🔴"
        if ts is not None:
            prev_ts = ts
        lines.append(f"*{field}*: {date_str}{delay}")
    return "\n".join(lines)


def get_order_data(order_number):
    token = get_superset_token()
    if not token:
        return {"error": "Не удалось авторизоваться в базе данных."}

    url = f"{SUPERSET_URL}/api/v1/chart/data"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    payload = {
        "datasource": {"id": SUPERSET_DATASET_ID, "type": "table"},
        "force": False,
        "queries": [
            {
                "columns": [
                    "external_barcode",
                    "накладная на склад",
                    "накладная со склада",
                    "Принят на ПВЗ (Отправка)",
                    "Отгружен на склад",
                    "Принят на складе",
                    "Отгружен со склада",
                    "Готово к выдаче на ПВЗ/Принят на ПВ",
                    "Выдано",
                    "Возвращено отправителю",
                    "Срок хранения истек"
                ],
                "filters": [
                    {"col": "created_date", "op": "TEMPORAL_RANGE", "val": "No filter"},
                    {"col": "external_barcode", "op": "==", "val": order_number}
                ],
                "row_limit": 1
            }
        ]
    }

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=30)
        response.raise_for_status()
        result = response.json()

        data = result.get("result", [])
        if not data or not data[0].get("data"):
            return {"error": f"Заказ **{order_number}** не найден в базе данных."}

        row = data[0]["data"][0]
        act_to   = str(row.get("накладная на склад") or "").strip()
        act_from = str(row.get("накладная со склада") or "").strip()
        movement = build_movement_status(row)

        return {
            "actToWarehouse":   act_to,
            "actFromWarehouse": act_from,
            "movementStatus":   movement
        }

    except requests.exceptions.HTTPError as e:
        print(f"ОШИБКА HTTP SUPERSET: {e}")
        print(f"Ответ сервера: {e.response.text[:500]}")
        return {"error": "Ошибка при запросе к базе данных (HTTP)."}
    except Exception as e:
        print(f"ОШИБКА SUPERSET: {e}")
        return {"error": "Внутренняя ошибка при запросе к базе данных."}


# ==============================================================================
# ГЕНЕРАЦИЯ ШТРИХКОДА CODE-128
# ==============================================================================

def generate_barcode_image(data):
    writer = ImageWriter()
    code128 = Code128(data, writer=writer)
    buffer = BytesIO()
    code128.write(buffer, {'module_height': 6, 'write_text': True})
    buffer.seek(0)
    return buffer


# ==============================================================================
# ОБРАБОТЧИКИ TELEGRAM
# ==============================================================================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Потерялся Акт отгрузки? Не грусти! Я всё исправлю! 👋\n\n"
        "Пришли мне номер заказа (ШК), и я найду соответствующие акты "
        "и сгенерирую для тебя штрихкоды."
    )


async def handle_barcode_request(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    order_number = update.message.text.strip().upper()

    if not order_number:
        await update.message.reply_text("Пожалуйста, отправьте номер заказа (ШК).")
        return

    await update.message.reply_text(f"🔍 Ищу данные для заказа: {order_number}...")

    data = get_order_data(order_number)

    if not data or "error" in data:
        error_msg = data.get("error", "Неизвестная ошибка") if data else "Нет ответа от сервера"
        await update.message.reply_text(
            f"❌ {error_msg}",
            parse_mode=telegram.constants.ParseMode.MARKDOWN
        )
        return

    act_to_warehouse   = data.get("actToWarehouse")
    act_from_warehouse = data.get("actFromWarehouse")
    movement_status    = data.get("movementStatus", "")

    # Статусы движения заказа
    if movement_status:
        await update.message.reply_text(
            f"📦 *Движение заказа {order_number}:*\n\n{movement_status}",
            parse_mode=telegram.constants.ParseMode.MARKDOWN
        )

    if not act_to_warehouse and not act_from_warehouse:
        await update.message.reply_text(
            f"⚠️ Для заказа *{order_number}* данные актов пусты.",
            parse_mode=telegram.constants.ParseMode.MARKDOWN
        )
        return

    # Штрихкоды
    try:
        if act_to_warehouse:
            img = generate_barcode_image(act_to_warehouse)
            await update.message.reply_photo(
                photo=img,
                caption=f"✅ *Акт на склад:* `{act_to_warehouse}`",
                parse_mode=telegram.constants.ParseMode.MARKDOWN
            )
        if act_from_warehouse:
            img = generate_barcode_image(act_from_warehouse)
            await update.message.reply_photo(
                photo=img,
                caption=f"✅ *Акт со склада:* `{act_from_warehouse}`",
                parse_mode=telegram.constants.ParseMode.MARKDOWN
            )
    except Exception as e:
        print(f"Ошибка генерации штрихкода: {e}")
        await update.message.reply_text("❌ Внутренняя ошибка при генерации штрихкода.")


# ==============================================================================
# ЗАПУСК БОТА
# ==============================================================================

def main() -> None:
    if not TELEGRAM_BOT_TOKEN:
        print("ОШИБКА: TELEGRAM_BOT_TOKEN не задан.")
        return
    if not SUPERSET_USERNAME or not SUPERSET_PASSWORD:
        print("ОШИБКА: SUPERSET_USERNAME или SUPERSET_PASSWORD не заданы.")
        return
    if SUPERSET_DATASET_ID == 0:
        print("ОШИБКА: SUPERSET_DATASET_ID не задан (или равен 0).")
        return

    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    application.add_handler(telegram.ext.CommandHandler("start", start_command))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_barcode_request)
    )

    print("Бот запущен. Источник данных: Superset API.")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
