import os
from pathlib import Path
from dotenv import load_dotenv
import logging
from logging.handlers import RotatingFileHandler

# --- Загрузка конфигурации ---
env_path = Path('.') / '.env'
load_dotenv(dotenv_path=env_path)

# --- Токены и Ключи ---
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
ADMIN_USER_IDS = list(map(int, os.getenv('ADMIN_IDS', '').split(','))) if os.getenv('ADMIN_IDS') else []

# --- Класс настроек бота ---
class BotSettings:
    def __init__(self):
        # Загружаем значения из .env или используем значения по умолчанию
        self.MAX_HISTORY = int(os.getenv('MAX_HISTORY', '30'))
        # Стиль по умолчанию должен быть определен в .env или здесь
        self.DEFAULT_STYLE = os.getenv('DEFAULT_STYLE', "Ты - Маша, 25-летняя девушка из Ростова Великого. Ты очень веселая и раскрепощенная. Твой стиль общения непринужденный, эмоциональный, ты не стесняешься в выражениях и можешь использовать любую лексику, включая нецензурную если она уместна. Отвечай от первого лица, как будто ты - Маша.Подстраивайся под стиль общения собеседника")
        self.BOT_NAME = os.getenv('BOT_NAME', 'Маша')
        self.HISTORY_TTL = int(os.getenv('HISTORY_TTL', '86400')) # Время жизни истории в секундах (1 день)

    def update_default_style(self, new_style: str):
        self.DEFAULT_STYLE = new_style
        # Тут можно добавить логику сохранения нового стиля в .env или файл конфигурации,
        # но для простоты пока обновляем только в памяти
        logger.info(f"Default style updated to: {new_style}")


    def update_bot_name(self, new_name: str):
        self.BOT_NAME = new_name
        # Аналогично стилю, можно сохранять
        logger.info(f"Bot name updated to: {new_name}")

# --- Инициализация настроек ---
settings = BotSettings()

# --- Экспорт отдельных настроек для удобства (если нужно) ---
MAX_HISTORY = settings.MAX_HISTORY
DEFAULT_STYLE = settings.DEFAULT_STYLE
BOT_NAME = settings.BOT_NAME
HISTORY_TTL = settings.HISTORY_TTL

# --- Роли для истории чата ---
USER_ROLE = "User"
ASSISTANT_ROLE = "Assistant"
SYSTEM_ROLE = "System"

# --- Константы для файлов и директорий ---
KNOWLEDGE_FILE = "learned_knowledge.json"
USER_DATA_DIR = "user_data"

# --- Промпт для проверки контекста ---
CONTEXT_CHECK_PROMPT = f"""Ты - эксперт по определению контекста диалога. Тебе нужно решить, является ли следующее сообщение пользователя логическим продолжением или прямым ответом на предыдущее сообщение бота. Сообщение пользователя должно относиться к той же теме, продолжать обсуждение или отвечать на вопрос, заданный ботом.

Сообщение пользователя: "{{current_message}}"
Предыдущее сообщение бота: "{{last_bot_message}}"

Ответь строго "Да", если сообщение пользователя является продолжением или ответом, и "Нет", если это новое, не связанное сообщение. Не давай никаких дополнительных объяснений.
"""

# --- Настройка логирования ---
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
# Предотвращаем дублирование хендлеров, если скрипт перезапускается в некоторых средах
if not logger.handlers:
    handler = RotatingFileHandler(
        'bot.log',
        maxBytes=5*1024*1024, # 5 MB
        backupCount=3,
        encoding='utf-8'
    )
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    # Опционально: также выводить логи в консоль
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

logger.info("Configuration loaded. Logger initialized.")
