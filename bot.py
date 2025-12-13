import os
import requests
import logging
from telegram import Update
# Импортируем нужные классы напрямую из telegram.ext, чтобы не использовать префикс 'telegram.ext'
from telegram.ext import Application, MessageHandler, filters, CommandHandler 

# Настройка логирования
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

# Глобальные переменные (токен и URL берутся из переменных окружения Render)
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
GOOGLE_SHEETS_API_URL = os.environ.get("GOOGLE_SHEETS_API_URL")

# Функция для запроса данных из Google Apps Script
def find_order_info(order_number):
    """Отправляет запрос на Google Apps Script и возвращает готовое сообщение."""
    if not GOOGLE_SHEETS_API_URL:
        return "⚠️ Ошибка конфигурации: GOOGLE_SHEETS_API_URL не задан."

    try:
        # Отправляем номер заказа как параметр 'order'
        response = requests.get(GOOGLE_SHEETS_API_URL, params={'order': order_number}, timeout=10)
        response.raise_for_status()  # Вызовет исключение, если HTTP-код 4xx или 5xx

        response_data = response.json()
        
        # 1. Обработка ошибок, возвращенных Apps Script (ключ 'error')
        if 'error' in response_data:
            # Для ошибок поиска ШК:
            if "не найден" in response_data['error'] or "не найдены" in response_data['error']:
                return f"❌ {response_data['error']}"
            return f"❌ Ошибка данных: {response_data['error']}"

        # 2. Обработка готового сообщения, возвращенного Apps Script (ключ 'text')
        # Новый код GAS возвращает форматированный ответ под ключом 'text'
        if 'text' in response_data:
            return response_data['text']
            
        # 3. Если ответ пустой или не содержит ожидаемых ключей
        return "⚠️ Неизвестный формат ответа от сервера данных."

    except requests.exceptions.RequestException as e:
        logging.error(f"Ошибка HTTP-запроса к Google Apps Script: {e}")
        return "❌ Ошибка связи с сервером данных. Попробуйте позже."
    except ValueError:
        logging.error(f"Ошибка декодирования JSON: {response.text}")
        return "❌ Ошибка: Ответ сервера данных не является корректным JSON."
    except Exception as e:
        logging.error(f"Неизвестная ошибка при обработке запроса: {e}")
        return "❌ Произошла непредвиденная ошибка."

# Асинхронный обработчик сообщений
async def message_handler(update: Update, context):
    """Обрабатывает входящие сообщения, содержащие только текст."""
    order_number = update.message.text.strip()
    
    # Игнорируем команду /start, если она попала сюда
    if order_number.lower() == '/start':
        return

    # Получаем информацию о заказе
    info_message = find_order_info(order_number)

    # Отправляем ответ пользователю
    await update.message.reply_text(
        info_message, 
        parse_mode='Markdown' # Используем Markdown для жирного текста (**) и блоков кода (```)
    )

async def start_command(update: Update, context):
    """Отправляет приветственное сообщение при команде /start."""
    welcome_message = (
        "👋 Привет! Я бот для проверки актов. "
        "Отправьте мне **номер заказа** или **ШК** для поиска информации об актах."
    )
    await update.message.reply_text(welcome_message, parse_mode='Markdown')

def main():
    """Запускает бота."""
    if not TELEGRAM_BOT_TOKEN:
        logging.error("TELEGRAM_BOT_TOKEN не найден. Бот не может быть запущен.")
        return

    # Создаем Application
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Регистрируем обработчики команд и сообщений
    # *** ИСПРАВЛЕННЫЕ СТРОКИ: теперь используется CommandHandler/MessageHandler напрямую ***
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))

    # Запускаем бота
    logging.info("Бот запущен...")
    # Используем run_polling, чтобы Render Worker не закрылся
    application.run_polling(poll_interval=3)

if __name__ == "__main__":
    main()
