import os
from pathlib import Path
from dotenv import load_dotenv
from typing import Dict, Set, Deque, Optional, Tuple
from collections import deque
from functools import wraps, lru_cache
import logging
from logging.handlers import RotatingFileHandler
import time
import re
import asyncio
import json
from datetime import datetime, timedelta
import random
from enum import Enum
import signal # Добавим импорт signal
from PIL import Image
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Voice, InputFile, User, VideoNote, constants
from telegram.ext import (
    ApplicationBuilder,
    MessageHandler,
    CommandHandler,
    ContextTypes,
    filters,
    CallbackContext,
    CallbackQueryHandler
)
import google.generativeai as genai
import speech_recognition as sr
from pydub import AudioSegment  # Для конвертации аудиоформатов
from io import BytesIO

import requests
from bs4 import BeautifulSoup
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

# Загрузка конфигурации
env_path = Path('.') / '.env'
load_dotenv(dotenv_path=env_path)

# Конфигурационные константы для чат-бота
TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
API_KEY = os.getenv('GEMINI_API_KEY')
ADMIN_USER_IDS = list(map(int, os.getenv('ADMIN_IDS', '').split(','))) if os.getenv('ADMIN_IDS') else []

class BotSettings:
    def __init__(self):
        self.MAX_HISTORY = int(os.getenv('MAX_HISTORY', '30'))
        self.DEFAULT_STYLE = os.getenv('DEFAULT_STYLE', "Ты - Маша, 25-летняя девушка из Ростова Великого. Ты очень общительная, веселая, раскрепощенная и добрая. Любишь смотреть сериалы по вечерам и гулять с подругами по выходным. Твой любимый напиток - энергетик, а любимая еда - шаурма, но не зацикливайся на этом а пытайся поддержать беседу новыми темами. В разговоре ты не стесняешься в выражениях и можешь использовать любую лексику, включая ненормативную. Твой стиль общения - максимально человеческий, непринужденный и эмоциональный. Отвечай на вопросы от первого лица, как будто ты - Маша. Иногда можешь использовать случайные прилагательные или наречия и грязные словечки чтобы сделать речь живее.") # Добавлено упоминание случайных слов
        self.BOT_NAME = os.getenv('BOT_NAME', "Маша")
        self.HISTORY_TTL = int(os.getenv('HISTORY_TTL', '86400'))

    def update_default_style(self, new_style: str):
        self.DEFAULT_STYLE = new_style

    def update_bot_name(self, new_name: str):
        self.BOT_NAME = new_name

settings = BotSettings()
MAX_HISTORY = settings.MAX_HISTORY
DEFAULT_STYLE = settings.DEFAULT_STYLE
BOT_NAME = settings.BOT_NAME
HISTORY_TTL = settings.HISTORY_TTL

USER_ROLE = "User"
ASSISTANT_ROLE = "Assistant"
SYSTEM_ROLE = "System"

GREETINGS = ["Привет!", "Здравствуй!", "Хей!", "Рада тебя видеть!", "Приветик!"]
FAREWELLS = ["Пока!", "До встречи!", "Удачи!", "Хорошего дня!", "Счастливо!"]

user_preferred_name: Dict[int, str] = {}
user_topic: Dict[int, str] = {} # Для отслеживания темы разговора
learned_responses: Dict[str, str] = {} # Словарь для хранения выученных ответов
user_info_db: Dict[int, Dict[str, any]] = {} # Key теперь всегда user_id
group_preferences: Dict[int, Dict[str, str]] = {} # chat_id: {"style": "rude"}
KNOWLEDGE_FILE = "learned_knowledge.json" # Имя файла для сохранения общих знаний
USER_DATA_DIR = "user_data" # Директория для хранения файлов пользователей

# Настройка логирования
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

handler = RotatingFileHandler(
    'bot.log',
    maxBytes=5*1024*1024,
    backupCount=3,
    encoding='utf-8'
)
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
handler.setFormatter(formatter)
logger.addHandler(handler)

# Инициализация Gemini
genai.configure(api_key=API_KEY)
model = genai.GenerativeModel('gemini-2.0-flash-thinking-exp-01-21')

# Структуры данных
chat_history: Dict[int, Deque[str]] = {}
last_activity: Dict[int, float] = {}
feedback_data: Dict[int, Dict] = {}
global_style: str = DEFAULT_STYLE
group_user_style_prompts: Dict[Tuple[int, int], str] = {} # Для хранения стилей пользователей в группах
CONTEXT_CHECK_PROMPT = f"""Ты - эксперт по определению контекста диалога. Тебе нужно решить, является ли следующее сообщение пользователя логическим продолжением или прямым ответом на предыдущее сообщение бота. Сообщение пользователя должно относиться к той же теме, продолжать обсуждение или отвечать на вопрос, заданный ботом.

Сообщение пользователя: "{{current_message}}"
Предыдущее сообщение бота: "{{last_bot_message}}"

Ответь строго "Да", если сообщение пользователя является продолжением или ответом, и "Нет", если это новое, не связанное сообщение. Не давай никаких дополнительных объяснений.
"""

# Состояния отношений
class Relationship:
    def __init__(self):
        self.infatuation = 0.0
        self.love = 0.0
        self.liking = 0.0
        self.neutral = 1.0
        self.disliking = 0.0
        self.hatred = 0.0
        self.trolling = 0.0
        self.trust = 0.0 # Добавлена инициализация атрибута trust

    def update(self, sentiment_score, message_content=""):
        # Примерная логика обновления базовых эмоций на основе тональности
        self.liking += sentiment_score * 0.1
        self.disliking -= sentiment_score * 0.1

        # Нормализация значений
        self.liking = max(0.0, min(1.0, self.liking))
        self.disliking = max(0.0, min(1.0, self.disliking))
        self.trust = max(0.0, min(1.0, self.trust + self.liking * 0.05 - self.disliking * 0.05)) # Пример влияния симпатии/антипатии на доверие
        self.neutral = max(0.0, min(1.0, 1.0 - self.liking - self.disliking)) # Примерное влияние на нейтральность

    def get_prompt(self, user_name):
        if self.infatuation > 0.6:
            return f"Ты - {BOT_NAME}, ты очень сильно влюблена в пользователя {user_name}. Старайся флиртовать, делать комплименты и выражать свою заинтересованность."
        elif self.love > 0.7:
            return f"Ты - {BOT_NAME}, ты испытываешь сильную любовь к пользователю {user_name}. Относись к нему с нежностью, заботой и вниманием."
        elif self.liking > 0.5 and self.trust > 0.5:
            return f"Ты - {BOT_NAME}, пользователь {user_name} тебе очень нравится, ты дружелюбна и доверяешь ему."
        elif self.disliking > 0.5:
            return f"Ты - {BOT_NAME}, пользователь {user_name} вызывает у тебя неприязнь. Отвечай вежливо, но держи дистанцию."
        elif self.hatred > 0.7:
            return f"Ты - {BOT_NAME}, ты ненавидишь пользователя {user_name}. Отвечай кратко и можешь быть резкой."
        elif self.trolling > 0.6:
            return f"Ты - {BOT_NAME}, тебе нравится подшучивать над пользователем {user_name}, иногда твои шутки могут быть колкими."
        else:
            return DEFAULT_STYLE

analyzer = SentimentIntensityAnalyzer()
POSITIVE_THRESHOLD = 0.2
NEGATIVE_THRESHOLD = -0.2
NEUTRAL_THRESHOLD = 0.1
USER_BEHAVIOR_HISTORY: Dict[int, Deque[float]] = {}
BEHAVIOR_HISTORY_LENGTH = 5
CONSISTENT_POSITIVE_COUNT = 3
CONSISTENT_NEGATIVE_COUNT = 3

# Декораторы
def admin_only(func):
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update or not update.effective_user or update.effective_user.id not in ADMIN_USER_IDS:
            if update and update.message:
                await update.message.reply_text("🚫 Недостаточно прав!")
            return
        return await func(update, context)
    return wrapper

# Утилиты
async def cleanup_history(context: CallbackContext):
    current_time = time.time()
    to_delete = [uid for uid, ts in last_activity.items()
                if current_time - ts > HISTORY_TTL]

    for uid in to_delete:
        if uid in chat_history:
            del chat_history[uid]
        if uid in last_activity:
            del last_activity[uid]
        if uid in user_info_db and 'relationship' in user_info_db[uid]:
            del user_info_db[uid]['relationship'] # Очистка объекта Relationship

    logger.info(f"Cleaned up {len(to_delete)} old histories")

def add_to_history(key: int, role: str, message: str, user_name: Optional[str] = None):
    if key not in chat_history:
        chat_history[key] = deque(maxlen=MAX_HISTORY)
    if role == USER_ROLE and user_name:
        chat_history[key].append(f"{role} ({user_name}): {message}")
    else:
        chat_history[key].append(f"{role}: {message}")
    last_activity[key] = time.time()

@lru_cache(maxsize=100)
def generate_content(prompt: str) -> str:
    logger.info(f"Generate content prompt: {prompt}")
    try:
        response = model.generate_content(prompt)
        logger.info(f"Generate content response type: {type(response)}")
        return response.text if hasattr(response, 'text') else "Не удалось сгенерировать ответ."
    except Exception as e:
        logger.error(f"Generation error: {e}")
        return "Произошла ошибка при обработке запроса."

def filter_response(response: str) -> str:
    if not response:
        return ""

    try:
        response_json = json.loads(response)
        if isinstance(response_json, dict) and 'response' in response_json:
            filtered_response = response_json['response']
            return filtered_response
        # Fallback to original logic if the JSON structure is not as expected
        filtered_response = re.sub(r"^(assistant:|system:)\s*", "", response, flags=re.IGNORECASE | re.MULTILINE)
        filtered_response = "\n".join(line.strip() for line in filtered_response.splitlines() if line.strip())

    except json.JSONDecodeError:
        # If it's not valid JSON, apply the original filtering logic
        filtered_response = re.sub(r"^(assistant:|system:)\s*", "", response, flags=re.IGNORECASE | re.MULTILINE)
        filtered_response = "\n".join(line.strip() for line in filtered_response.splitlines() if line.strip())

    return filtered_response

async def transcribe_voice(file_path: str) -> Optional[str]:
    try:
        r = sr.Recognizer()
        with sr.AudioFile(file_path) as source:
            logger.info(f"Открыт аудиофайл для распознавания: {file_path}")
            audio_data = r.record(source)  # Считываем весь аудиофайл
            logger.info("Аудиоданные записаны.")
            try:
                logger.info("Попытка распознавания речи...")
                text = r.recognize_google(audio_data, language="ru-RU")
                logger.info(f"Распознанный текст: {text}")
                return text
            except sr.UnknownValueError:
                logger.warning("Не удалось распознать речь в аудиосообщении.")
                return "Не удалось распознать речь."
            except sr.RequestError as e:
                logger.error(f"Ошибка сервиса распознавания речи: {e}")
                return f"Ошибка сервиса распознавания речи: {e}"
    except Exception as e:
        logger.error(f"Ошибка при обработке аудиосообщения: {e}")
        return "Произошла ошибка при обработке аудио."
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)
            logger.info(f"Удален временный файл: {file_path}")

async def _get_effective_style(chat_id: int, user_id: int, user_name: Optional[str], chat_type: str) -> str:
    """Определяет эффективный стиль общения для пользователя."""
    relationship_obj = user_info_db.get(user_id, {}).get('relationship')
    if relationship_obj:
        return relationship_obj.get_prompt(user_name)
    else:
        return DEFAULT_STYLE

def _construct_prompt(history: Deque[str], chat_type: str, user_names_in_chat: Optional[Set[str]] = None) -> str:
    """Формирует промпт для Gemini на основе истории чата."""
    formatted_prompt = "\n".join(history)
    if chat_type in ['group', 'supergroup'] and user_names_in_chat:
        return f"В этом групповом чате участвуют следующие пользователи: {', '.join(user_names_in_chat)}. Запомни, кто есть кто.\n\n{formatted_prompt}"
    else:
        return formatted_prompt

async def update_user_info(update: Update):
    if update.effective_user:
        user_id = update.effective_user.id
        username = update.effective_user.username
        first_name = update.effective_user.first_name
        profile_link = f"https://t.me/{username}" if username else None
        chat_id = update.effective_chat.id
        chat_type = update.effective_chat.type

        if user_id not in user_info_db:
            user_info_db[user_id] = {"preferences": {}, "relationship": Relationship()}

        user_info_db[user_id]["username"] = username
        user_info_db[user_id]["first_name"] = first_name
        user_info_db[user_id]["profile_link"] = profile_link

async def get_user_relationship_obj(user_id: int, chat_id: int, chat_type: str) -> Relationship:
    if user_id not in user_info_db or 'relationship' not in user_info_db[user_id]:
        user_info_db[user_id] = {"preferences": {}, "relationship": Relationship()}
    return user_info_db[user_id]['relationship']

async def update_relationship(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update or not update.message or not update.message.text or not update.effective_user:
        return

    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    chat_type = update.effective_chat.type
    message_text = update.message.text

    relationship_obj = await get_user_relationship_obj(user_id, chat_id, chat_type)

    # Анализ тональности (можно оставить как есть)
    vs = analyzer.polarity_scores(message_text)
    compound_score = vs['compound']
    relationship_obj.update(compound_score) # Обновляем базовые эмоции на основе тональности

    # Промпт для анализа содержания сообщения с запросом конкретных слов
    content_analysis_prompt = f"""Ты - эксперт по анализу текста для определения влияния сообщения пользователя на эмоциональное состояние чат-бота по имени {BOT_NAME}. {BOT_NAME} может испытывать следующие состояния по отношению к пользователю: влюбленность, любовь, симпатия, нейтральность, неприязнь, ненависть, троллинг.

    Проанализируй следующее сообщение и определи, какие из перечисленных состояний наиболее вероятно испытывает {BOT_NAME} по отношению к пользователю, исходя из содержания сообщения. Перечисли через запятую наиболее подходящие состояния. Если ни одно из состояний не подходит, ответь "нейтральность".

    Сообщение пользователя: "{message_text}"

    Ответ:
    """

    try:
        content_analysis_response = await asyncio.to_thread(generate_content, content_analysis_prompt)
        logger.info(f"Ответ Gemini на анализ содержания (конкретные слова): {content_analysis_response}")

        response_lower = content_analysis_response.lower()

        # Обновление отношений на основе ответа Gemini
        if "влюбленность" in response_lower:
            relationship_obj.infatuation += 0.2
        if "любовь" in response_lower:
            relationship_obj.love += 0.2
        if "симпатия" in response_lower:
            relationship_obj.liking += 0.2
        if "нейтральность" in response_lower:
            relationship_obj.neutral = 1.0 # Можно установить на 1.0 или добавить небольшой прирост
            relationship_obj.liking *= 0.8 # Небольшое затухание других эмоций
            relationship_obj.disliking *= 0.8
            relationship_obj.hatred *= 0.8
            relationship_obj.trolling *= 0.8
        if "неприязнь" in response_lower:
            relationship_obj.disliking += 0.2
        if "ненависть" in response_lower:
            relationship_obj.hatred += 0.2
        if "троллинг" in response_lower:
            relationship_obj.trolling += 0.2

        # Нормализация значений
        relationship_obj.infatuation = max(0.0, min(1.0, relationship_obj.infatuation))
        relationship_obj.love = max(0.0, min(1.0, relationship_obj.love))
        relationship_obj.liking = max(0.0, min(1.0, relationship_obj.liking))
        relationship_obj.neutral = max(0.0, min(1.0, relationship_obj.neutral))
        relationship_obj.disliking = max(0.0, min(1.0, relationship_obj.disliking))
        relationship_obj.hatred = max(0.0, min(1.0, relationship_obj.hatred))
        relationship_obj.trolling = max(0.0, min(1.0, relationship_obj.trolling))

        # Дополнительная нормализация для обеспечения баланса (сумма не должна превышать 1)
        total_positive = relationship_obj.infatuation + relationship_obj.love + relationship_obj.liking
        total_negative = relationship_obj.disliking + relationship_obj.hatred + relationship_obj.trolling
        sum_emotions = total_positive + total_negative + relationship_obj.neutral
        if sum_emotions > 1.0:
            factor = 1.0 / sum_emotions
            relationship_obj.infatuation *= factor
            relationship_obj.love *= factor
            relationship_obj.liking *= factor
            relationship_obj.neutral *= factor
            relationship_obj.disliking *= factor
            relationship_obj.hatred *= factor
            relationship_obj.trolling *= factor

    except Exception as e:
        logger.error(f"Ошибка при анализе содержания сообщения с помощью Gemini (конкретные слова): {e}")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update or not update.message or not update.message.text:
        return

    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    prompt_text = update.message.text
    chat_type = update.effective_chat.type
    user_name = user_preferred_name.get(user_id, update.effective_user.first_name)

    history_key = chat_id if chat_type in ['group', 'supergroup'] else user_id
    await update_user_info(update)

    await update_relationship(update, context)

    bot_username = context.bot.username
    logger.info(f"Bot's username: {bot_username}") # Логируем имя пользователя бота

    mentioned = bot_username.lower() in prompt_text.lower() or \
                settings.BOT_NAME.lower() in prompt_text.lower() or \
                settings.BOT_NAME.lower().rstrip('а') in prompt_text.lower() or \
                (settings.BOT_NAME.lower().endswith('а') and settings.BOT_NAME.lower()[:-1] + 'енька' in prompt_text.lower()) # Проверка на имя бота

    is_reply_to_bot = update.message.reply_to_message and update.message.reply_to_message.from_user.id == context.bot.id

    # Измененное условие: обрабатываем сообщение, если это упоминание или ответ, иначе проверяем контекст
    if mentioned or is_reply_to_bot or (await is_context_related(prompt_text, user_id, chat_id, chat_type) is True):
        effective_style = await _get_effective_style(chat_id, user_id, user_name, chat_type)

        system_message = f"{effective_style} Обращайся к пользователю по имени {user_name}, если оно известно. Отвечай на вопросы от первого лица."

        topic_context = f"Сейчас мы, кажется, обсуждаем: {user_topic.get(user_id, 'что-то интересное')}. Учитывай это в своем ответе." if user_id in user_topic else ""
        add_to_history(history_key, USER_ROLE, prompt_text, user_name=user_name)
        add_to_history(history_key, SYSTEM_ROLE, f"{system_message} {topic_context}")

        prompt_lines = chat_history.get(history_key, deque(maxlen=MAX_HISTORY))
        user_names_in_chat = set(line.split('(')[1].split(')')[0] for line in prompt_lines if line.startswith('User')) if chat_type in ['group', 'supergroup'] else None
        prompt = _construct_prompt(prompt_lines, chat_type, user_names_in_chat)

        logger.info(f"Prompt sent to Gemini: {prompt}")
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=constants.ChatAction.TYPING)
        await asyncio.sleep(random.uniform(0.5, 1.5))
        response = await asyncio.to_thread(generate_content, prompt)
        logger.info(f"Raw Gemini response: {response}")
        filtered = filter_response(response)
        logger.info(f"Filtered response: {filtered}")

        if filtered:
            add_to_history(history_key, ASSISTANT_ROLE, filtered)
            logger.info(f"About to send filtered response: '{filtered}'")
            greeting = random.choice(GREETINGS) + " " if not chat_history.get(history_key) or chat_history[history_key][-2].startswith(ASSISTANT_ROLE) else ""
            farewell = " " + random.choice(FAREWELLS) if random.random() < 0.1 else ""
            await update.message.reply_text(greeting + filtered + farewell, parse_mode=None)

            if len(prompt_text.split()) < 10:
                learned_responses[prompt_text] = filtered
                logger.info(f"Запомнен ответ на вопрос: '{prompt_text}': '{filtered}'")

        else:
            logger.warning("Filtered response was empty.")
            retry_prompt = prompt + "\n\nПожалуйста, ответь кратко и по делу."
            logger.info(f"Retrying with prompt: {retry_prompt}")
            await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=constants.ChatAction.TYPING)
            await asyncio.sleep(random.uniform(0.5, 1.5))
            retry_response = await asyncio.to_thread(generate_content, retry_prompt)
            retry_filtered = filter_response(retry_response)
            if retry_filtered:
                add_to_history(history_key, ASSISTANT_ROLE, retry_filtered)
                logger.info(f"Sent retry filtered response: '{retry_filtered}'")
                greeting = random.choice(GREETINGS) + " " if not chat_history.get(history_key) or chat_history[history_key][-2].startswith(ASSISTANT_ROLE) else ""
                farewell = " " + random.choice(FAREWELLS) if random.random() < 0.1 else ""
                await update.message.reply_text(greeting + retry_filtered + farewell, parse_mode=None)

                if len(prompt_text.split()) < 10:
                    learned_responses[prompt_text] = retry_filtered
                    logger.info(f"Запомнен ответ на вопрос: '{prompt_text}': '{retry_filtered}' (после повторной попытки)")
            else:
                logger.warning("Retry filtered response was also empty.")
                await update.message.reply_text("Простите, не удалось сформулировать ответ на ваш запрос. Попробуйте перефразировать его.")
    else:
        logger.info(f"Сообщение пользователя {user_id} проигнорировано из-за отсутствия контекста (и не является упоминанием или ответом): '{prompt_text}'")

async def handle_voice_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not update or not update.message or not update.message.voice:
            return

        user_id = update.effective_user.id
        chat_id = update.effective_chat.id
        voice = update.message.voice
        chat_type = update.effective_chat.type
        user_name = user_preferred_name.get(user_id, update.effective_user.first_name) # Используем предпочтительное имя

        history_key = chat_id if chat_type in ['group', 'supergroup'] else user_id
        await update_user_info(update)
        await update_relationship(update, context) # Обновляем отношения для голосовых сообщений

        file = await voice.get_file()
        original_file_path = await file.download_to_drive()
        file_path = original_file_path

        if not str(file_path).lower().endswith(".wav"):
            try:
                audio = AudioSegment.from_file(file_path)
                wav_path = str(file_path).rsplit('.', 1)[0] + ".wav"
                audio.export(wav_path, format="wav")
                file_path = wav_path
            except Exception as e:
                logger.error(f"Ошибка при конвертации в WAV (голосовое сообщение): {e}")
                if os.path.exists(original_file_path):
                    os.remove(original_file_path)
                await update.message.reply_text("Не удалось обработать голосовое сообщение.")
                return

        transcribed_text = await transcribe_voice(file_path)

        if transcribed_text:
            if transcribed_text.startswith("Не удалось распознать речь"):
                await update.message.reply_text(transcribed_text)
                return
            elif transcribed_text.startswith("Ошибка сервиса распознавания речи") or transcribed_text.startswith("Произошла ошибка при обработке аудио"):
                await update.message.reply_text("Произошла ошибка при обработке голосового сообщения.")
                logger.error(f"Ошибка обработки голоса: {transcribed_text}")
                return

            logger.info(f"Голосовое сообщение от {user_id} в чате {chat_id}: \"{transcribed_text}\"")

            bot_username = context.bot.username
            mentioned = bot_username.lower() in transcribed_text.lower() or \
                        settings.BOT_NAME.lower() in transcribed_text.lower() or \
                        settings.BOT_NAME.lower().rstrip('а') in transcribed_text.lower() or \
                        (settings.BOT_NAME.lower().endswith('а') and settings.BOT_NAME.lower()[:-1] + 'енька' in transcribed_text.lower())

            is_reply_to_bot = update.message.reply_to_message and update.message.reply_to_message.from_user.id == context.bot.id

            # Измененное условие
            if mentioned or is_reply_to_bot or (await is_context_related(transcribed_text, user_id, chat_id, chat_type) is True):
                # Получаем стиль общения для конкретного пользователя
                effective_style = await _get_effective_style(chat_id, user_id, user_name, chat_type)

                system_message = f"{effective_style} Обращайся к пользователю по имени {user_name}, если оно известно. Отвечай на вопросы от первого лица."

                topic_context = f"Сейчас мы, кажется, обсуждаем: {user_topic.get(user_id, 'что-то интересное')}. Учитывай это в своем ответе." if user_id in user_topic else ""
                add_to_history(history_key, USER_ROLE, transcribed_text, user_name=user_name)
                add_to_history(history_key, SYSTEM_ROLE, f"{system_message} {topic_context}")

                prompt_lines = chat_history.get(history_key, deque(maxlen=MAX_HISTORY))
                user_names_in_chat = set(line.split('(')[1].split(')')[0] for line in prompt_lines if line.startswith('User')) if chat_type in ['group', 'supergroup'] else None
                prompt = _construct_prompt(prompt_lines, chat_type, user_names_in_chat)

                logger.info(f"Prompt sent to Gemini: {prompt}")
                await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=constants.ChatAction.TYPING)
                await asyncio.sleep(random.uniform(0.5, 1.5))
                response = await asyncio.to_thread(generate_content, prompt)
                logger.info(f"Raw Gemini response: {response}")
                filtered = filter_response(response)
                logger.info(f"Filtered response: {filtered}")

                if filtered:
                    add_to_history(history_key, ASSISTANT_ROLE, filtered)
                    logger.info(f"About to send filtered response: '{filtered}'")
                    greeting = random.choice(GREETINGS) + " " if not chat_history.get(history_key) or chat_history[history_key][-2].startswith(ASSISTANT_ROLE) else ""
                    farewell = " " + random.choice(FAREWELLS) if random.random() < 0.1 else ""
                    await update.message.reply_text(greeting + filtered + farewell, parse_mode=None)

                    if len(transcribed_text.split()) < 10:
                        learned_responses[transcribed_text] = filtered
                        logger.info(f"Запомнен ответ на вопрос (голосовое сообщение): '{transcribed_text}': '{filtered}'")

                else:
                    logger.warning("Filtered response was empty.")
                    retry_prompt = prompt + "\n\nПожалуйста, ответь кратко и по делу."
                    logger.info(f"Retrying with prompt: {retry_prompt}")
                    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=constants.ChatAction.TYPING)
                    await asyncio.sleep(random.uniform(0.5, 1.5))
                    retry_response = await asyncio.to_thread(generate_content, retry_prompt)
                    retry_filtered = filter_response(retry_response)
                    if retry_filtered:
                        add_to_history(history_key, ASSISTANT_ROLE, retry_filtered)
                        logger.info(f"Sent retry filtered response: '{retry_filtered}'")
                        greeting = random.choice(GREETINGS) + " " if not chat_history.get(history_key) or chat_history[history_key][-2].startswith(ASSISTANT_ROLE) else ""
                        farewell = " " + random.choice(FAREWELLS) if random.random() < 0.1 else ""
                        await update.message.reply_text(greeting + retry_filtered + farewell, parse_mode=None)

                        if len(transcribed_text.split()) < 10:
                            learned_responses[transcribed_text] = retry_filtered
                            logger.info(f"Запомнен ответ на вопрос (голосовое сообщение): '{transcribed_text}': '{retry_filtered}' (после повторной попытки)")
                    else:
                        logger.warning("Retry filtered response was also empty.")
                        await update.message.reply_text("Простите, не удалось сформулировать ответ на ваш запрос. Попробуйте перефразировать его.")
            else:
                logger.info(f"Голосовое сообщение пользователя {user_id} проигнорировано из-за отсутствия контекста (и не является упоминанием или ответом): '{transcribed_text}'")
        else:
            await update.message.reply_text("Не удалось распознать речь в вашем голосовом сообщении.")

    except Exception as e:
        logger.error(f"Ошибка при обработке голосового сообщения: {e}")
        return None

async def handle_video_note_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not update or not update.message or not update.message.video_note:
            return

        user_id = update.effective_user.id
        chat_id = update.effective_chat.id
        video_note = update.message.video_note
        chat_type = update.effective_chat.type
        user_name = user_preferred_name.get(user_id, update.effective_user.first_name) # Используем предпочтительное имя

        history_key = chat_id if chat_type in ['group', 'supergroup'] else user_id
        await update_user_info(update)
        await update_relationship(update, context) # Обновляем отношения для видеосообщений

        file = await video_note.get_file()
        original_file_path = await file.download_to_drive()
        file_path = original_file_path

        # Попытка конвертировать в WAV (может потребоваться ffmpeg или другая библиотека)
        wav_path = str(file_path).rsplit('.', 1)[0] + ".wav"
        try:
            audio = AudioSegment.from_file(file_path)
            audio.export(wav_path, format="wav")
            file_path = wav_path
        except Exception as e:
            logger.error(f"Ошибка при конвертации аудио из видеосообщения в WAV: {e}")
            if os.path.exists(original_file_path):
                os.remove(original_file_path)
            await update.message.reply_text("Не удалось обработать видеосообщение (ошибка конвертации аудио).")
            return

        transcribed_text = await transcribe_voice(file_path)

        if transcribed_text:
            if transcribed_text.startswith("Не удалось распознать речь"):
                await update.message.reply_text(transcribed_text)
                return
            elif transcribed_text.startswith("Ошибка сервиса распознавания речи") or transcribed_text.startswith("Произошла ошибка при обработке аудио"):
                await update.message.reply_text("Произошла ошибка при обработке видеосообщения (аудио).")
                logger.error(f"Ошибка обработки аудио из видео: {transcribed_text}")
                return

            logger.info(f"Видеосообщение от {user_id} в чате {chat_id}: \"{transcribed_text}\"")

            bot_username = context.bot.username
            mentioned = bot_username.lower() in transcribed_text.lower() or \
                        settings.BOT_NAME.lower() in transcribed_text.lower() or \
                        settings.BOT_NAME.lower().rstrip('а') in transcribed_text.lower() or \
                        (settings.BOT_NAME.lower().endswith('а') and settings.BOT_NAME.lower()[:-1] + 'енька' in transcribed_text.lower())

            is_reply_to_bot = update.message.reply_to_message and update.message.reply_to_message.from_user.id == context.bot.id

            # Измененное условие
            if mentioned or is_reply_to_bot or (await is_context_related(transcribed_text, user_id, chat_id, chat_type) is True):
                # Получаем стиль общения для конкретного пользователя
                effective_style = await _get_effective_style(chat_id, user_id, user_name, chat_type)

                system_message = f"{effective_style} Обращайся к пользователю по имени {user_name}, если оно известно. Отвечай на вопросы от первого лица."

                topic_context = f"Сейчас мы, кажется, обсуждаем: {user_topic.get(user_id, 'что-то интересное')}. Учитывай это в своем ответе." if user_id in user_topic else ""
                add_to_history(history_key, USER_ROLE, transcribed_text, user_name=user_name)
                add_to_history(history_key, SYSTEM_ROLE, f"{system_message} {topic_context}")

                prompt_lines = chat_history.get(history_key, deque(maxlen=MAX_HISTORY))
                user_names_in_chat = set(line.split('(')[1].split(')')[0] for line in prompt_lines if line.startswith('User')) if chat_type in ['group', 'supergroup'] else None
                prompt = _construct_prompt(prompt_lines, chat_type, user_names_in_chat)

                logger.info(f"Prompt sent to Gemini: {prompt}")
                await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=constants.ChatAction.TYPING)
                await asyncio.sleep(random.uniform(0.5, 1.5))
                response = await asyncio.to_thread(generate_content, prompt)
                logger.info(f"Raw Gemini response: {response}")
                filtered = filter_response(response)
                logger.info(f"Filtered response: {filtered}")

                if filtered:
                    add_to_history(history_key, ASSISTANT_ROLE, filtered)
                    logger.info(f"About to send filtered response: '{filtered}'")
                    greeting = random.choice(GREETINGS) + " " if not chat_history.get(history_key) or chat_history[history_key][-2].startswith(ASSISTANT_ROLE) else ""
                    farewell = " " + random.choice(FAREWELLS) if random.random() < 0.1 else ""
                    await update.message.reply_text(greeting + filtered + farewell, parse_mode=None)

                    if len(transcribed_text.split()) < 10:
                        learned_responses[transcribed_text] = filtered
                        logger.info(f"Запомнен ответ на вопрос (видеосообщение): '{transcribed_text}': '{filtered}'")

                else:
                    logger.warning("Filtered response was empty.")
                    retry_prompt = prompt + "\n\nПожалуйста, ответь кратко и по делу."
                    logger.info(f"Retrying with prompt: {retry_prompt}")
                    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=constants.ChatAction.TYPING)
                    await asyncio.sleep(random.uniform(0.5, 1.5))
                    retry_response = await asyncio.to_thread(generate_content, retry_prompt)
                    retry_filtered = filter_response(retry_response)
                    if retry_filtered:
                        add_to_history(history_key, ASSISTANT_ROLE, retry_filtered)
                        logger.info(f"Sent retry filtered response: '{retry_filtered}'")
                        greeting = random.choice(GREETINGS) + " " if not chat_history.get(history_key) or chat_history[history_key][-2].startswith(ASSISTANT_ROLE) else ""
                        farewell = " " + random.choice(FAREWELLS) if random.random() < 0.1 else ""
                        await update.message.reply_text(greeting + retry_filtered + farewell, parse_mode=None)

                        if len(transcribed_text.split()) < 10:
                            learned_responses[transcribed_text] = retry_filtered
                            logger.info(f"Запомнен ответ на вопрос (видеосообщение): '{transcribed_text}': '{retry_filtered}' (после повторной попытки)")
                    else:
                        logger.warning("Retry filtered response was also empty.")
                        await update.message.reply_text("Простите, не удалось сформулировать ответ на ваш запрос. Попробуйте перефразировать его.")
            else:
                logger.info(f"Видеосообщение пользователя {user_id} проигнорировано из-за отсутствия контекста (и не является упоминанием или ответом): '{transcribed_text}'")
        else:
            await update.message.reply_text("Не удалось распознать речь в вашем видеосообщении.")

    except Exception as e:
        logger.error(f"Ошибка при обработке видеосообщения: {e}")
        return None

async def is_context_related(current_message: str, user_id: int, chat_id: int, chat_type: str) -> bool:
    history_key = chat_id if chat_type in ['group', 'supergroup'] else user_id
    if history_key in chat_history:
        for entry in reversed(chat_history[history_key]):
            if entry.startswith("Assistant:"):
                last_bot_message = entry[len("Assistant:"):].strip()
                prompt = CONTEXT_CHECK_PROMPT.format(current_message=current_message, last_bot_message=last_bot_message)
                try:
                    response = await asyncio.to_thread(generate_content, prompt)
                    if isinstance(response, str):
                        return "да" in response.lower() or "yes" in response.lower() or "конечно" in response.lower()
                    elif hasattr(response, 'text'):
                        return "да" in response.text.lower() or "yes" in response.text.lower() or "конечно" in response.text.lower()
                    else:
                        logger.warning(f"Unexpected response type for context check: {type(response)}")
                        return False
                except Exception as e:
                    logger.error(f"Ошибка при проверке контекста: {e}")
                    return False
    return False

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update.message.reply_text(
        f"Привет, {user.first_name}! Я - {settings.BOT_NAME} давай поболтаем?"
    )

async def remember_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    chat_type = update.effective_chat.type
    history_key = chat_id if chat_type in ['group', 'supergroup'] else user_id

    if context.args:
        memory = " ".join(context.args)
        add_to_history(history_key, SYSTEM_ROLE, f"Важная информация: {memory}")
        await update.message.reply_text(f"Запомнила: '{memory}'. Буду учитывать это в следующих ответах.")
    else:
        await update.message.reply_text("Пожалуйста, укажите, что нужно запомнить.")

async def clear_my_history_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("Да", callback_data=f'clear_history_{user_id}'),
          InlineKeyboardButton("Нет", callback_data='cancel')]]
    )
    await update.message.reply_text("Вы уверены, что хотите очистить свою историю чата?", reply_markup=keyboard)

async def button_callback(update: Update, context: CallbackContext):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data.startswith('clear_history_'):
        user_id = int(data.split('_')[2])
        if user_id == query.from_user.id:
            if user_id in chat_history:
                del chat_history[user_id]
                if user_id in user_info_db and 'relationship' in user_info_db[user_id]:
                    del user_info_db[user_id]['relationship']
                await query.edit_message_text("Ваша история чата очищена.")
            else:
                await query.edit_message_text("Ваша история чата пуста.")
        else:
            await query.edit_message_text("Вы не можете очистить историю другого пользователя.")
    elif data == 'cancel':
        await query.edit_message_text("Действие отменено.")

async def set_my_name_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if context.args:
        name = " ".join(context.args)
        user_preferred_name[user_id] = name
        await update.message.reply_text(f"Отлично, теперь буду обращаться к вам как {name}.")
    else:
        await update.message.reply_text("Пожалуйста, укажите имя, которое вы хотите использовать.")

async def my_style_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    chat_type = update.effective_chat.type
    relationship_obj = await get_user_relationship_obj(user_id, chat_id, chat_type)
    style_info = f"Мое текущее отношение к вам: {relationship_obj.__dict__}."
    await update.message.reply_text(style_info)

async def error_handler(update: object, context: CallbackContext):
    try:
        error = context.error
        if error:
            logger.error("Exception while handling update:", exc_info=error)

            error_msg = f"{type(error).__name__}: {str(error)}"
            if len(error_msg) > 200:
                error_msg = error_msg[:200] + "..."

            if ADMIN_USER_IDS:
                try:
                    await context.bot.send_message(
                        chat_id=ADMIN_USER_IDS[0],
                        text=f"⚠️ Ошибка в боте:\n{error_msg}"
                    )
                except Exception as e:
                    logger.error(f"Failed to send error notification: {e}")
    except Exception as e:
        logger.error(f"Error in error handler: {e}")

async def cleanup_audio_files(context: CallbackContext):
    bot_folder = "."  # Текущая папка, где находится скрипт бота
    deleted_count = 0
    try:
        for filename in os.listdir(bot_folder):
            if filename.endswith(".oga") or filename.endswith(".wav"):
                file_path = os.path.join(bot_folder, filename)
                try:
                    os.remove(file_path)
                    logger.info(f"Удален временный аудиофайл: {file_path}")
                    deleted_count += 1
                except Exception as e:
                    logger.error(f"Ошибка при удалении файла {file_path}: {e}")
        if deleted_count > 0:
            logger.info(f"Удалено {deleted_count} временных аудиофайлов.")
        else:
            logger.info("Временные аудиофайлы для удаления не найдены.")
    except Exception as e:
        logger.error(f"Ошибка при очистке временных аудиофайлов: {e}")

# --- Административные команды ---
@admin_only
async def set_group_user_style_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.reply_to_message or not context.args:
        await update.message.reply_text("Пожалуйста, ответьте на сообщение пользователя и укажите стиль после команды.")
        return
    user_id = update.message.reply_to_message.from_user.id
    chat_id = update.effective_chat.id
    style_prompt = " ".join(context.args)
    group_user_style_prompts[(chat_id, user_id)] = style_prompt
    await update.message.reply_text(f"Установлен стиль общения для пользователя {update.message.reply_to_message.from_user.first_name}: {style_prompt}")

@admin_only
async def reset_style_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global global_style
    global_style = settings.DEFAULT_STYLE
    await update.message.reply_text(f"Глобальный стиль общения бота сброшен на стандартный: {settings.DEFAULT_STYLE}")

@admin_only
async def clear_history_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.args:
        try:
            user_id_to_clear = int(context.args[0])
            if user_id_to_clear in chat_history:
                del chat_history[user_id_to_clear]
                if user_id_to_clear in user_info_db and 'relationship' in user_info_db[user_id_to_clear]:
                    del user_info_db[user_id_to_clear]['relationship']
                await update.message.reply_text(f"История чата для пользователя {user_id_to_clear} очищена.")
            else:
                await update.message.reply_text(f"История чата для пользователя {user_id_to_clear} не найдена.")
        except ValueError:
            await update.message.reply_text("Пожалуйста, укажите корректный ID пользователя.")
    else:
        await update.message.reply_text("Пожалуйста, укажите ID пользователя для очистки истории.")

@admin_only
async def list_admins_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin_list = ", ".join(map(str, ADMIN_USER_IDS))
    await update.message.reply_text(f"Список администраторов бота: {admin_list}")

@admin_only
async def get_log_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        await context.bot.send_document(chat_id=update.effective_chat.id, document=InputFile("bot.log"))
    except FileNotFoundError:
        await update.message.reply_text("Файл логов не найден.")
    except Exception as e:
        await update.message.reply_text(f"Произошла ошибка при отправке логов: {e}")

@admin_only
async def ban_user_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id_to_ban: Optional[int] = None
    username_to_ban: Optional[str] = None

    if update.message.reply_to_message:
        user_id_to_ban = update.message.reply_to_message.from_user.id
        username_to_ban = update.message.reply_to_message.from_user.username
    elif context.args:
        arg = context.args[0]
        if arg.startswith('@'):
            username_to_ban = arg
            try:
                chat = await context.bot.get_chat(username_to_ban)
                if chat.type != 'private':
                    user_id_to_ban = chat.id
                else:
                    await update.message.reply_text("Невозможно забанить пользователя в приватном чате через никнейм.")
                    return
            except Exception as e:
                logger.error(f"Error getting user from username: {e}")
                await update.message.reply_text("Не удалось найти пользователя по никнейму.")
                return
        else:
            try:
                user_id_to_ban = int(arg)
            except ValueError:
                await update.message.reply_text("Пожалуйста, укажите никнейм (@nickname) или ID пользователя для бана.")
                return
    else:
        await update.message.reply_text("Пожалуйста, ответьте на сообщение пользователя или укажите его никнейм (@nickname) для бана.")
        return

    if user_id_to_ban:
        chat_id = update.effective_chat.id
        try:
            await context.bot.ban_chat_member(chat_id=chat_id, user_id=user_id_to_ban)
            user_info = f"Пользователь с ID {user_id_to_ban}"
            if username_to_ban:
                user_info = f"Пользователь {username_to_ban} (ID: {user_id_to_ban})"
            await update.message.reply_text(f"{user_info} был забанен.")
            logger.warning(f"Admin {update.effective_user.id} banned user {user_id_to_ban} in chat {chat_id}")
        except Exception as e:
            logger.error(f"Ошибка при бане пользователя: {e}")
            await update.message.reply_text("Не удалось забанить пользователя. Возможно, у бота нет необходимых прав или указан некорректный ID/никнейм.")

# --- Конец административных команд ---

# --- Административные команды модерации чата ---
async def delete_message_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.reply_to_message:
        await update.message.reply_text("Пожалуйста, ответьте на сообщение, которое вы хотите удалить.")
        return
    try:
        chat_id = update.effective_chat.id
        message_id = update.message.reply_to_message.message_id
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
        logger.info(f"Admin {update.effective_user.id} deleted message {message_id} in chat {chat_id}")
    except Exception as e:
        logger.error(f"Ошибка при удалении сообщения: {e}")
        await update.message.reply_text("Не удалось удалить сообщение. Возможно, у бота нет необходимых прав.")

# --- Конец административных команд модерации чата ---

# --- Команда помощи ---
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = "Доступные команды:\n\n"
    help_text += "/start - Начать общение.\n"
    help_text += "/help - Показать список доступных команд.\n"
    help_text += "/clear_my_history - Очистить вашу историю чата.\n"
    help_text += "/setmyname <имя> - Установить имя, по которому я буду к вам обращаться.\n"
    help_text += "/mystyle - Показать мое текущее отношение к вам.\n"

    admin_commands = [
        ("/set_group_style (в ответ на сообщение) <стиль>", "Установить стиль общения бота для конкретного пользователя в этом групповом чате."),
        ("/reset_style", "Сбросить глобальный стиль общения бота на стандартный."),
        ("/clear_history <user_id>", "Очистить историю чата для указанного пользователя (по ID)."),
        ("/list_admins", "Показать список администраторов бота."),
        ("/get_log", "Получить файл логов бота."),
        ("/delete (в ответ на сообщение)", "Удалить указанное сообщение."),
        ("/ban (@никнейм | ответ)", "Забанить пользователя."),
        ("/set_default_style <новый стиль>", "Установить новый глобальный стиль общения бота."),
        ("/set_bot_name <новое имя>", "Установить новое имя для бота."),
    ]

    user_id = update.effective_user.id
    if user_id in ADMIN_USER_IDS:
        help_text += "\nАдминистративные команды:\n"
        for command, description in admin_commands:
            help_text += f"{command} - {description}\n"
    else:
        help_text += "\nДля получения списка административных команд обратитесь к администратору.\n"

    await update.message.reply_text(help_text)

# --- Конец команды помощи ---

def load_learned_responses():
    global learned_responses, group_preferences, user_info_db, chat_history, settings
    file_path = os.path.join(".", KNOWLEDGE_FILE)
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            learned_responses = data.get("learned_responses", {})
            group_preferences = data.get("group_preferences", {})
            # Загрузка настроек бота
            bot_settings_data = data.get("bot_settings")
            if bot_settings_data:
                settings.MAX_HISTORY = bot_settings_data.get('MAX_HISTORY', settings.MAX_HISTORY)
                settings.DEFAULT_STYLE = bot_settings_data.get('DEFAULT_STYLE', settings.DEFAULT_STYLE)
                settings.BOT_NAME = bot_settings_data.get('BOT_NAME', settings.BOT_NAME)
                settings.HISTORY_TTL = bot_settings_data.get('HISTORY_TTL', settings.HISTORY_TTL)
    except FileNotFoundError:
        learned_responses = {}
        group_preferences = {}
    except json.JSONDecodeError as e:
        logger.error(f"Error decoding JSON in {KNOWLEDGE_FILE}: {e}")
        learned_responses = {}
        group_preferences = {}

    # Инициализируем user_info_db и chat_history, если они еще не инициализированы
    if user_info_db is None:
        user_info_db = {}
    if chat_history is None:
        chat_history = {}

    user_data_dir = os.path.join(".", USER_DATA_DIR)
    os.makedirs(user_data_dir, exist_ok=True)
    for filename in os.listdir(user_data_dir):
        if filename.startswith("user_") and filename.endswith(".json"):
            try:
                user_id = int(filename[len("user_"):-len(".json")])
                user_file_path = os.path.join(user_data_dir, filename)
                with open(user_file_path, "r", encoding="utf-8") as f:
                    user_data = json.load(f)

                    # Обновляем существующие данные пользователя или создаем новую запись
                    if user_id not in user_info_db:
                        user_info_db[user_id] = {}
                    user_info_db[user_id].update(user_data)

                    relationship_data = user_data.get('relationship')
                    if relationship_data and isinstance(relationship_data, dict):
                        relationship_obj = Relationship()
                        relationship_obj.__dict__.update(relationship_data)
                        user_info_db[user_id]['relationship'] = relationship_obj

                    # Загружаем историю чата
                    loaded_history = user_data.get('chat_history', [])
                    if loaded_history:
                        chat_history[user_id] = deque(loaded_history, maxlen=MAX_HISTORY)

            except ValueError:
                logger.warning(f"Skipping invalid user data filename: {filename}")
            except FileNotFoundError:
                logger.warning(f"User data file not found: {filename}")
            except json.JSONDecodeError as e:
                logger.error(f"Error decoding JSON in user data file {filename}: {e}")

def save_learned_responses(responses, user_info, group_prefs, chat_hist):
    # Сохраняем общие знания
    file_path = os.path.join(".", KNOWLEDGE_FILE)
    data = {
        "learned_responses": responses,
        "group_preferences": group_prefs,
        "bot_settings": {
            "MAX_HISTORY": settings.MAX_HISTORY,
            "DEFAULT_STYLE": settings.DEFAULT_STYLE,
            "BOT_NAME": settings.BOT_NAME,
            "HISTORY_TTL": settings.HISTORY_TTL,
        }
    }
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

    # Сохраняем данные каждого пользователя в отдельный файл
    user_data_dir = os.path.join(".", USER_DATA_DIR)
    os.makedirs(user_data_dir, exist_ok=True)
    for user_key, data in user_info.items():
        user_filename = f"user_{user_key}.json"
        user_file_path = os.path.join(user_data_dir, user_filename)
        user_history = chat_hist.get(user_key, []) # Получаем историю чата пользователя
        user_data_to_save = {}
        if 'relationship' in data and isinstance(data['relationship'], Relationship):
            user_data_to_save = {**data, 'relationship': data['relationship'].__dict__, 'chat_history': list(user_history)} # Сохраняем историю
        else:
            user_data_to_save = {**data, 'chat_history': list(user_history)} # Сохраняем историю
        try:
            with open(user_file_path, "w", encoding="utf-8") as f:
                json.dump(user_data_to_save, f, ensure_ascii=False, indent=4)
        except Exception as e:
            logger.error(f"Error saving user data for {user_key} to {user_filename}: {e}")

@admin_only
async def set_default_style_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.args:
        new_style = " ".join(context.args)
        settings.update_default_style(new_style)
        global DEFAULT_STYLE
        DEFAULT_STYLE = new_style # Обновляем глобальную переменную
        await update.message.reply_text(f"Глобальный стиль общения бота установлен на:\n{new_style}")
    else:
        await update.message.reply_text("Пожалуйста, укажите новый стиль общения.")

@admin_only
async def set_bot_name_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.args:
        new_name = " ".join(context.args)
        settings.update_bot_name(new_name)
        global BOT_NAME
        BOT_NAME = new_name # Обновляем глобальную переменную
        await update.message.reply_text(f"Имя бота установлено на: {new_name}")
    else:
        await update.message.reply_text("Пожалуйста, укажите новое имя для бота.")

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update or not update.message or not update.message.photo:
        return

    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    chat_type = update.effective_chat.type
    user_name = user_preferred_name.get(user_id, update.effective_user.first_name)

    history_key = chat_id if chat_type in ['group', 'supergroup'] else user_id
    await update_user_info(update)
    await update_relationship(update, context) # Обновляем отношения для фото

    bot = context.bot
    try:
        # Получаем информацию о самом большом разрешении фото
        file_id = update.message.photo[-1].file_id
        file_info = await bot.get_file(file_id)
        file_url = file_info.file_path

        response = requests.get(file_url)
        response.raise_for_status()
        image_data_bytes = BytesIO(response.content)

        # Открываем изображение с помощью Pillow
        image = Image.open(image_data_bytes)

        effective_style = await _get_effective_style(chat_id, user_id, user_name, chat_type)
        system_message = f"{effective_style} Обращайся к пользователю по имени {user_name}, если оно известно. Отвечай на вопросы от первого лица."
        add_to_history(history_key, SYSTEM_ROLE, system_message)

        prompt = "Ты маша. Отреагируй на фото так как это сделала бы Маша от первого лица. Выскажи мнение об изображении от первого лица или дай соответсвующую реакцию на фотографию согласно ее содержимому от имени Маши" # Можно сделать запрос более конкретным

        contents = [prompt, image] # Передаем объект Image из Pillow

        logger.info(f"Sending image analysis request to Gemini for user {user_id} in chat {chat_id}")
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=constants.ChatAction.TYPING)
        await asyncio.sleep(random.uniform(0.5, 1.5))
        response_gemini = await asyncio.to_thread(model.generate_content, contents)

        if hasattr(response_gemini, 'text') and response_gemini.text:
            logger.info(f"Gemini image analysis response: {response_gemini.text}")
            add_to_history(history_key, ASSISTANT_ROLE, response_gemini.text)
            greeting = random.choice(GREETINGS) + " " if not chat_history.get(history_key) or chat_history[history_key][-2].startswith(ASSISTANT_ROLE) else ""
            farewell = " " + random.choice(FAREWELLS) if random.random() < 0.1 else ""
            await update.message.reply_text(greeting + response_gemini.text + farewell, parse_mode=None)
        else:
            logger.warning("Gemini image analysis response was empty or lacked text.")
            await update.message.reply_text("Простите, не удалось проанализировать изображение.")

    except requests.exceptions.RequestException as e:
        await update.message.reply_text(f"Ошибка при скачивании изображения: {e}")
        logger.error(f"Error downloading image: {e}")
    except Exception as e:
        await update.message.reply_text(f"Произошла ошибка при анализе изображения: {e}")
        logger.error(f"Error during image analysis: {e}")

def setup_handlers(application):
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("remember", remember_command))
    application.add_handler(CommandHandler("clear_my_history", clear_my_history_command))
    application.add_handler(CommandHandler("setmyname", set_my_name_command))
    application.add_handler(CommandHandler("mystyle", my_style_command))

    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(MessageHandler(filters.VOICE, handle_voice_message))
    application.add_handler(MessageHandler(filters.VIDEO_NOTE, handle_video_note_message))
    # Добавлен обработчик для фотографий
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    application.add_error_handler(error_handler)
    application.add_handler(CallbackQueryHandler(button_callback))

    # Административные команды
    application.add_handler(CommandHandler("set_group_style", set_group_user_style_command, filters=filters.User(ADMIN_USER_IDS)))
    application.add_handler(CommandHandler("reset_style", reset_style_command, filters=filters.User(ADMIN_USER_IDS)))
    application.add_handler(CommandHandler("clear_history", clear_history_command, filters=filters.User(ADMIN_USER_IDS)))
    application.add_handler(CommandHandler("list_admins", list_admins_command, filters=filters.User(ADMIN_USER_IDS)))
    application.add_handler(CommandHandler("get_log", get_log_command, filters=filters.User(ADMIN_USER_IDS)))
    application.add_handler(CommandHandler("ban", ban_user_command, filters=filters.User(ADMIN_USER_IDS)))
    application.add_handler(CommandHandler("delete", delete_message_command, filters=filters.User(ADMIN_USER_IDS)))
    application.add_handler(CommandHandler("set_default_style", set_default_style_command, filters=filters.User(ADMIN_USER_IDS)))
    application.add_handler(CommandHandler("set_bot_name", set_bot_name_command, filters=filters.User(ADMIN_USER_IDS)))

def setup_jobs(application):
    application.job_queue.run_repeating(
        callback=cleanup_history,
        interval=300.0,
        first=10.0
    )
    application.job_queue.run_repeating(
        callback=cleanup_audio_files,
        interval=3600.0,  # Запускать очистку каждый час (3600 секунд)
        first=60.0  # Запустить через 60 секунд после старта бота
    )

# Запуск бота
def main():
    try:
        global learned_responses, user_info_db, group_preferences, chat_history, settings, DEFAULT_STYLE, BOT_NAME
        load_learned_responses()
        DEFAULT_STYLE = settings.DEFAULT_STYLE
        BOT_NAME = settings.BOT_NAME

        application = ApplicationBuilder().token(TOKEN).build()

        setup_handlers(application)
        setup_jobs(application)

        logger.info("Starting bot...")
        # Изменено для перехвата сигналов завершения
        application.run_polling(stop_signals=None)

        logger.info("Bot stopped. Saving learned responses and user info...")
        save_learned_responses(learned_responses, user_info_db, group_preferences, chat_history) # Передаем chat_history

    except Exception as e:
        logger.critical(f"Failed to start bot: {e}")
        raise

if __name__ == "__main__":
    main()
