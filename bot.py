import os
import requests
import logging
from telegram import Update, InputFile
from telegram.ext import Application, MessageHandler, filters, CommandHandler 
from io import BytesIO 
from barcode import Code128 
from barcode.writer import ImageWriter 

# Настройка логирования
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

# Глобальные переменные (токен и URL берутся из переменных окружения Render)
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
GOOGLE_SHEETS_API_URL = os.environ.get("GOOGLE_SHEETS_API_URL")

# --- ФУНКЦИЯ: Генерация изображения штрихкода ---
def generate_barcode_image(data_text):
    """Генерирует изображение штрихкода Code128 в памяти."""
    if not data_text:
        return None
        
    buffer = BytesIO()
    
    writer_options = {
        'module_width': 0.3,
        'module_height': 15,
        'write_text': True,
        'font_size': 12,
        'text_distance': 5,
        'quiet_zone': 4,
    }

    code128 = Code128(data_text, writer=ImageWriter())
    code128.write(buffer, options=writer_options)
    buffer.seek(0)
    return buffer

# --- Функция для запроса данных из Google Apps Script ---
def find_order_info(order_number):
    """Отправляет запрос на Google Apps Script и возвращает JSON с данными и текстом."""
    if not GOOGLE_SHEETS_API_URL:
        return {'error': "⚠️ Ошибка конфигурации: GOOGLE_SHEETS_API_URL не задан."}

    try:
        # Увеличенный таймаут до 30 секунд
        response = requests.get(GOOGLE_SHEETS_API_URL, params={'order': order_number}, timeout=30)
        response.raise_for_status()

        response_data = response.json()
        return response_data

    except requests.exceptions.RequestException as e:
        logging.error(f"Ошибка HTTP-запроса к Google Apps Script: {e}")
        return {'error': "❌ Ошибка связи с сервером данных. Попробуйте позже."}
    except ValueError:
        logging.error(f"Ошибка декодирования JSON: {response.text}")
        return {'error': "❌ Ошибка: Ответ сервера данных не является корректным JSON."}
    except Exception as e:
        logging.error(f"Неизвестная ошибка при обработке запроса: {e}")
        return {'error': "❌ Произошла непредвиденная ошибка."}


async def message_handler(update: Update, context):
    """Обрабатывает входящие сообщения, генерирует штрихкоды и отправляет ответ."""
    order_number = update.message.text.strip()
    
    if order_number.lower() == '/start':
        return

    # Отправляем немедленный ответ с новым текстом "Ищу Акты"
    await update.message.reply_text(f"🔍 Ищу Акты для заказа: **{order_number}**...", parse_mode='Markdown')

    # Получаем JSON-ответ от GAS
    response_data = find_order_info(order_number)
    
    # 1. Обработка ошибки
    if 'error' in response_data:
        await update.message.reply_text(response_data['error'])
        return

    # 2. Получение данных и текста из ответа GAS
    info_message = response_data.get('text', "Информация не найдена.")
    act_to_data = response_data.get('actToWarehouse', '').strip()
    act_from_data = response_data.get('actFromWarehouse', '').strip()

    # Сначала отправляем главное текстовое сообщение (об успехе/ошибке)
    await update.message.reply_text(info_message, parse_mode='Markdown')
    
    # 3. Отправка штрихкодов в виде изображений
    
    # Акт на склад
    if act_to_data:
        image_buffer = generate_barcode_image(act_to_data)
        if image_buffer:
            await update.message.reply_photo(
                photo=InputFile(image_buffer, filename='act_to_warehouse.png'),
                caption=f"Акт на склад: `{act_to_data}`",
                parse_mode='Markdown'
            )

    # Акт со склада
    if act_from_data:
        image_buffer = generate_barcode_image(act_from_data)
        if image_buffer:
            await update.message.reply_photo(
                photo=InputFile(image_buffer, filename='act_from_warehouse.png'),
                caption=f"Акт со склада: `{act_from_data}`",
                parse_mode='Markdown'
            )


async def start_command(update: Update, context):
    """Отправляет приветственное сообщение при команде /start."""
    welcome_message = (
        "👋 Привет! Я бот для проверки актов. "
        "Пришли мне **номер заказа** и я найду соответствующие акты и сгенерирую для тебя штрихкоды."
    )
    await update.message.reply_text(welcome_message, parse_mode='Markdown')

def main():
    """Запускает бота."""
    if not TELEGRAM_BOT_TOKEN:
        logging.error("TELEGRAM_BOT_TOKEN не найден. Бот не может быть запущен.")
        return

    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))

    logging.info("Бот запущен...")
    # poll_interval установлен на 5 секунд для предотвращения двойных ответов
    application.run_polling(poll_interval=5)

if __name__ == "__main__":
    main()
