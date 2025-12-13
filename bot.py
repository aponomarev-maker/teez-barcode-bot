import telegram
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes
import requests
from barcode import Code128
from barcode.writer import ImageWriter
from io import BytesIO
import os
import telegram.ext # Добавляем для CommandHandler

# --- Настройки: Получение ключей из переменных окружения ---
# ВАШИ КЛЮЧИ БУДУТ ПЕРЕДАНЫ СЕРВЕРОМ RAILWAY
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN") 
GOOGLE_SHEETS_API_URL = os.environ.get("GOOGLE_SHEETS_API_URL")

# --- Генерация штрихкода (CODE-128) ---
def generate_barcode_image(data: str) -> BytesIO:
    """
    Генерирует изображение штрихкода CODE-128 с уменьшенной высотой 
    и возвращает его в виде BytesIO.
    (Исправлена ошибка 'module_height' для совместимости с python-barcode)
    """
    writer = ImageWriter() 
    
    code128 = Code128(data, writer=writer)
    buffer = BytesIO()
    
    # Передаем настройки (module_height) в метод .write()
    # Уменьшаем высоту до 6 для компактности.
    options = {'module_height': 6, 'write_text': True} 
    
    code128.write(buffer, options)
    buffer.seek(0)
    return buffer

# --- Обработчик команды /start ---
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Отправляет приветственное сообщение при команде /start."""
    welcome_message = (
        "Потерялся Акт отгрузки? Не грусти! Я всё исправлю! 👋\n\n"
        "**Пришли мне номер заказа (ШК)**, и я найду соответствующие акты и сгенерирую для тебя штрихкоды."
    )
    await update.message.reply_text(welcome_message, parse_mode=telegram.constants.ParseMode.MARKDOWN)


# --- Обработчик сообщений ---
async def handle_barcode_request(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обрабатывает любое текстовое сообщение как номер заказа и взаимодействует с G/A/S."""
    
    order_number = update.message.text.strip().upper()
    
    if not order_number:
        await update.message.reply_text("Пожалуйста, отправьте номер заказа (ШК).")
        return

    await update.message.reply_text(f"🔍 Ищу данные для заказа: **{order_number}**...", parse_mode=telegram.constants.ParseMode.MARKDOWN)

    # 1. Запрос к Google Sheets API
    try:
        # Увеличена задержка до 45 секунд для предотвращения таймаутов G/A/S
        response = requests.get(GOOGLE_SHEETS_API_URL, params={'order': order_number}, timeout=45) 
        
        response.raise_for_status() 
        data = response.json()
        
    except requests.exceptions.RequestException as e:
        # Сетевая/HTTP ошибка
        print(f"ОШИБКА HTTP/СЕТЬ: {e}")
        await update.message.reply_text("❌ Ошибка при обращении к серверу данных. Попробуйте позже.")
        return
        
    except ValueError:
        # Ошибка декодирования JSON
        print(f"ОШИБКА ДЕКОДИРОВАНИЯ JSON: {response.text}")
        await update.message.reply_text("❌ Ошибка: Получен некорректный ответ от сервера данных (не JSON).")
        return


    # 2. Обработка ответа 
    if 'error' in data:
        await update.message.reply_text(f"❌ Ошибка данных: {data['error']}")
        return
    
    act_to_warehouse = data.get('actToWarehouse')
    act_from_warehouse = data.get('actFromWarehouse')
    
    if not act_to_warehouse or not act_from_warehouse:
        await update.message.reply_text(f"⚠️ В таблице найдены пустые данные актов для заказа **{order_number}**.")
        return

    # 3. Генерация и отправка штрихкодов
    try:
        # Акт на склад
        img_to_buffer = generate_barcode_image(act_to_warehouse)
        caption_to = f"✅ **Акт на склад:** `{act_to_warehouse}`"
        await update.message.reply_photo(photo=img_to_buffer, caption=caption_to, 
                                         parse_mode=telegram.constants.ParseMode.MARKDOWN)

        # Акт со склада
        img_from_buffer = generate_barcode_image(act_from_warehouse)
        caption_from = f"✅ **Акт со склада:** `{act_from_warehouse}`"
        await update.message.reply_photo(photo=img_from_buffer, caption=caption_from, 
                                         parse_mode=telegram.constants.ParseMode.MARKDOWN)

    except Exception as e:
        print(f"Ошибка генерации или отправки штрихкода: {e}")
        await update.message.reply_text("❌ Произошла внутренняя ошибка при генерации штрихкода.")

# --- Основная функция запуска бота ---
def main() -> None:
    """Запуск бота."""
    # Проверка, что ключи доступны (важно для хостинга)
    if not TELEGRAM_BOT_TOKEN or not GOOGLE_SHEETS_API_URL:
        print("ОШИБКА: Токены не загружены из переменных окружения. Проверьте настройки Railway.")
        return

    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # 1. Обработчик команды /start
    application.add_handler(telegram.ext.CommandHandler("start", start_command)) 

    # 2. Обработчик: любое текстовое сообщение, которое не является командой
    text_handler = MessageHandler(filters.TEXT & ~filters.COMMAND, handle_barcode_request)
    application.add_handler(text_handler)

    print("Бот запущен...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()