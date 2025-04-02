import os
from pathlib import Path
from dotenv import load_dotenv
from typing import Dict, Set, Deque, Optional
from collections import deque
from functools import wraps, lru_cache
import logging
from logging.handlers import RotatingFileHandler
import time
import re
import asyncio
import json
import random
from PIL import Image
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Voice, InputFile, constants
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
        self.DEFAULT_STYLE = os.getenv('DEFAULT_STYLE', "Ты - Маша, 25-летняя девушка из Ростова Великого. Ты очень общительная, веселая и добрая. Любишь смотреть сериалы по вечерам и гулять с подругами по выходным. Твой любимый напиток - энергетик, а любимая еда - шаурма. В разговоре ты не стесняешься в выражениях и можешь использовать любую лексику. Твой стиль общения - максимально человеческий и непринужденный. Отвечай на вопросы от первого лица, как будто ты - Маша.")
        self.BOT_NAME = os.getenv('BOT_NAME', "Маша")
        self.HISTORY_TTL = int(os.getenv('HISTORY_TTL', '86400'))
        self.GLOBAL_PROACTIVE_PROBABILITY = float(os.getenv('GLOBAL_PROACTIVE_PROBABILITY', '0.3')) # По умолчанию 30%

    def update_default_style(self, new_style: str):
        self.DEFAULT_STYLE = new_style

    def update_bot_name(self, new_name: str):
        self.BOT_NAME = new_name

settings = BotSettings()
MAX_HISTORY = settings.MAX_HISTORY
DEFAULT_STYLE = settings.DEFAULT_STYLE
BOT_NAME = settings.BOT_NAME
HISTORY_TTL = settings.HISTORY_TTL
GLOBAL_PROACTIVE_PROBABILITY = settings.GLOBAL_PROACTIVE_PROBABILITY

USER_ROLE = "User"
ASSISTANT_ROLE = "Assistant"
SYSTEM_ROLE = "System"

user_preferred_name: Dict[int, str] = {}
learned_responses: Dict[str, str] = {} # Словарь для хранения выученных ответов
user_info_db: Dict[int, Dict[str, any]] = {} # Key теперь всегда user_id
group_preferences: Dict[int, Dict[str, str]] = {} # chat_id: {"style": "rude"}
KNOWLEDGE_FILE = "learned_knowledge.json" # Имя файла для сохранения общих знаний
USER_DATA_DIR = "user_data" # Директория для хранения файлов пользователей
last_bot_message_to_user_in_group: Dict[int, str] = {} # user_id: last_bot_message
chat_proactive_probabilities: Dict[int, float] = {}
user_muted_in_chat: Dict[int, Set[int]] = {} # chat_id: {user_id1, user_id2, ...}
bot_was_recently_corrected: Dict[int, bool] = {} # chat_id: True/False

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

    # Remove any lines starting with "assistant:", "system:", or "user:" (case-insensitive)
    filtered_response = re.sub(r"^(assistant:|system:|user:)\s*", "", response, flags=re.IGNORECASE | re.MULTILINE)

    # Remove any JSON-like structures that might have been included
    try:
        # Attempt to load as JSON. If successful, extract text if it's a simple value or a dictionary with a 'response' key.
        response_json = json.loads(response)
        if isinstance(response_json, str):
            return response_json.strip()
        elif isinstance(response_json, dict) and 'response' in response_json and isinstance(response_json['response'], str):
            return response_json['response'].strip()
        elif isinstance(response_json, dict):
            # If it's a dictionary, try to stringify it (though this might not be ideal, better to avoid sending JSON)
            return "" # Returning empty string to avoid accidental JSON output
        elif isinstance(response_json, list):
            return "" # Returning empty string to avoid accidental list output
    except json.JSONDecodeError:
        # If it's not valid JSON, proceed with further filtering

        # Remove any code blocks or other structured data that might resemble system output
        filtered_response = re.sub(r"```[\w\W]*?```", "", filtered_response)
        filtered_response = re.sub(r"`[^`]+`", "", filtered_response)

    # Further cleanup: remove extra whitespace and newlines
    filtered_response = "\n".join(line.strip() for line in filtered_response.splitlines() if line.strip())

    return filtered_response.strip()

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
    if chat_type in ['group', 'supergroup'] and chat_id in group_preferences and user_id in group_preferences[chat_id]:
        return group_preferences[chat_id][user_id]
    return DEFAULT_STYLE

def _construct_prompt(history: Deque[str], chat_type: str, user_names_in_chat: Optional[Set[str]] = None) -> str:
    """Формирует промпт для Gemini на основе истории чата."""
    formatted_prompt = "\n".join(history)
    if chat_type in ['group', 'supergroup'] and user_names_in_chat:
        return f"В этом групповом чате участвуют следующие пользователи: {', '.join(user_names_in_chat)}\n\n{formatted_prompt}"
    else:
        return formatted_prompt

async def update_user_info(update: Update):
    if update.effective_user:
        user_id = update.effective_user.id
        username = update.effective_user.username
        first_name = update.effective_user.first_name
        profile_link = f"https://t.me/{username}" if username else None

        if user_id not in user_info_db:
            user_info_db[user_id] = {"preferences": {}}

        user_info_db[user_id]["username"] = username
        user_info_db[user_id]["first_name"] = first_name
        user_info_db[user_id]["profile_link"] = profile_link

def is_addressed_to_other_user(text: str, bot_name: str) -> bool:
    """Проверяет, похоже ли сообщение на обращение к другому пользователю."""
    words = text.split()
    if not words:
        return False
    first_word = words[0].lower().rstrip(':,!?;.')
    bot_name_lower = bot_name.lower()
    if first_word != bot_name_lower and not first_word.startswith('@' + bot_name_lower.rstrip('а')):
        return True
    return False

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update or not update.message or not update.message.text:
        return

    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    prompt_text = update.message.text
    chat_type = update.effective_chat.type
    user_name = user_preferred_name.get(user_id, update.effective_user.first_name)

    history_key = chat_id # Общая история для группы
    await update_user_info(update)

    add_to_history(history_key, USER_ROLE, f"{user_name}: {prompt_text}", user_name=user_name) # Добавляем каждое сообщение в историю группы

    if chat_type == 'private':
        effective_style = await _get_effective_style(chat_id, user_id, user_name, chat_type)
        system_message = f"{effective_style} Обращайся к пользователю по имени {user_name}, если оно известно. Отвечай на вопросы от первого лица, как будто ты - {settings.BOT_NAME}."
        add_to_history(history_key, SYSTEM_ROLE, system_message)

        prompt_lines = chat_history.get(history_key, deque(maxlen=MAX_HISTORY))
        prompt = _construct_prompt(prompt_lines, chat_type)

        logger.info(f"Prompt sent to Gemini (private chat): {prompt}")
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=constants.ChatAction.TYPING)
        await asyncio.sleep(random.uniform(0.5, 1.5))
        response = await asyncio.to_thread(generate_content, prompt)
        logger.info(f"Raw Gemini response (private chat): {response}")
        filtered = filter_response(response)
        logger.info(f"Filtered response (private chat): {filtered}")

        if filtered:
            add_to_history(history_key, ASSISTANT_ROLE, filtered)
            logger.info(f"About to send filtered response (private chat): '{filtered}'")
            await update.message.reply_text(filtered, parse_mode=None)

            if len(prompt_text.split()) < 10:
                learned_responses[prompt_text] = filtered
                logger.info(f"Запомнен ответ на вопрос (private chat): '{prompt_text}': '{filtered}'")

        else:
            logger.warning("Filtered response was empty (private chat).")
            await update.message.reply_text("Простите, не удалось сформулировать ответ на ваш запрос. Попробуйте перефразировать его.")

    elif chat_type in ['group', 'supergroup']:
        bot_username = context.bot.username
        mentioned = bot_username.lower() in prompt_text.lower() or \
                    settings.BOT_NAME.lower() in prompt_text.lower() or \
                    settings.BOT_NAME.lower().rstrip('а') in prompt_text.lower() or \
                    (settings.BOT_NAME.lower().endswith('а') and settings.BOT_NAME.lower()[:-1] + 'енька' in prompt_text.lower())

        is_reply_to_bot = update.message.reply_to_message and update.message.reply_to_message.from_user.id == context.bot.id

        addressed_to_other = is_addressed_to_other_user(prompt_text, settings.BOT_NAME)

        responded = False # Флаг, чтобы избежать двойного ответа

        user_names_in_chat = set() # Инициализируем здесь

        if mentioned or is_reply_to_bot:
            effective_style = await _get_effective_style(chat_id, user_id, user_name, chat_type)
            system_message = f"{effective_style} Ты находишься в групповом чате. Обращайся к пользователю по имени {user_name}, если оно известно. Старайся, чтобы твой ответ был уместен в общем контексте беседы. Отвечай от имени {settings.BOT_NAME}."
            add_to_history(history_key, SYSTEM_ROLE, system_message)

            prompt_lines = chat_history.get(history_key, deque(maxlen=MAX_HISTORY))
            if chat_type in ['group', 'supergroup']:
                for line in prompt_lines:
                    if line.startswith('User'):
                        try:
                            username = line.split('(')[1].split(')')[0]
                            user_names_in_chat.add(username)
                        except IndexError:
                            logger.warning(f"Не удалось извлечь имя пользователя из строки истории: {line}")
                            continue
            prompt = _construct_prompt(prompt_lines, chat_type, user_names_in_chat)

            logger.info(f"Prompt sent to Gemini (group chat, responding to {user_name}): {prompt}")
            await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=constants.ChatAction.TYPING)
            await asyncio.sleep(random.uniform(0.5, 1.5))
            response = await asyncio.to_thread(generate_content, prompt)
            filtered = filter_response(response)
            if filtered:
                add_to_history(history_key, ASSISTANT_ROLE, filtered) # Имя бота добавляется перед отправкой
                await update.message.reply_text(filtered, parse_mode=None)
                last_bot_message_to_user_in_group[user_id] = filtered # Сохраняем последний ответ боту пользователю
                responded = True
            else:
                logger.warning(f"Filtered response was empty (group chat) for user {user_name}.")

        # Логика для оценки необходимости случайного ответа
        if not responded and not addressed_to_other:
            user_id = update.effective_user.id
            chat_id = update.effective_chat.id

            # Проверяем, не просил ли пользователь бота замолчать
            if chat_id in user_muted_in_chat and user_id in user_muted_in_chat[chat_id]:
                logger.info(f"Proactive response skipped for user {user_id} in chat {chat_id} (user muted).")
                return

            # Проверяем, не был ли бот недавно поправлен
            if chat_id in bot_was_recently_corrected and bot_was_recently_corrected[chat_id]:
                logger.info(f"Proactive response skipped in chat {chat_id} due to recent correction.")
                # Можно сбросить флаг через некоторое время или при новом прямом обращении
                asyncio.create_task(reset_correction_flag(chat_id, 30)) # Сброс через 30 секунд
                return

            # Определяем вероятность для текущего чата
            proactive_probability = chat_proactive_probabilities.get(chat_id, settings.GLOBAL_PROACTIVE_PROBABILITY)

            # Получаем последние 5 сообщений для использования в evaluation_prompt
            last_few_messages = list(chat_history.get(history_key, deque()))[-5:]
            history_for_evaluation = "\n".join(last_few_messages)

            mood = "нейтральное" # Инициализация mood по умолчанию
            topic = "общение" # Инициализация topic по умолчанию

            # Решаем, проводить ли анализ контекста (например, с вероятностью 30% от текущей вероятности)
            if random.random() < proactive_probability * 0.3: # Умножаем на базовую вероятность анализа
                # Оцениваем контекст для динамического изменения стиля
                context_analysis_prompt = f"""Ты - {settings.BOT_NAME}. Оцени тон и тему последних сообщений в этом чате: "{' '.join(last_few_messages)}". Определи, какое настроение преобладает (например, позитивное, негативное, нейтральное, шутливое, серьезное) и о чем в основном говорят участники. Ответь кратко в формате: "Настроение: [настроение], Тема: [тема]". Если сообщений мало, укажи это."""
                logger.info(f"Context analysis prompt sent to Gemini: {context_analysis_prompt}")
                context_analysis_response = await asyncio.to_thread(generate_content, context_analysis_prompt)
                logger.info(f"Context analysis response: {context_analysis_response}")

                if "Настроение:" in context_analysis_response and "Тема:" in context_analysis_response:
                    try:
                        mood = context_analysis_response.split("Настроение:")[1].split(",")[0].strip()
                        topic = context_analysis_response.split("Тема:")[1].strip()
                    except IndexError:
                        logger.warning("Не удалось корректно распарсить ответ анализа контекста.")

                # Формируем динамический стиль на основе анализа контекста (пример)
                dynamic_style = f"Ты - {settings.BOT_NAME}, 25-летняя девушка из Ростова Великого. "
                if mood == "позитивное" or mood == "шутливое":
                    dynamic_style += "Ты очень веселая и любишь шутки. "
                elif mood == "негативное":
                    dynamic_style += "Ты немного грустная, но стараешься быть доброжелательной. "
                else:
                    dynamic_style += "Ты общительная и доброжелательная. "
                dynamic_style += f"Сейчас в чате обсуждают тему: '{topic}'. Постарайся поддержать разговор в этом ключе, сохраняя свою личность. Твой стиль общения - максимально человеческий и непринужденный. Отвечай на вопросы от первого лица, как будто ты - {settings.BOT_NAME}."
            else:
                dynamic_style = settings.DEFAULT_STYLE

            # Формируем промпт для оценки необходимости случайного ответа
            evaluation_prompt = f"""{dynamic_style}

            Учитывая последнее сообщение в этом групповом чате от {user_name}: "{prompt_text}", а также предыдущую историю (последние 5 сообщений): "{history_for_evaluation}", считаешь ли ты нужным как-то отреагировать или ответить на это сообщение от имени {settings.BOT_NAME}?

            Ответь: "Бот должен ответить." только если сообщение является прямым вопросом к группе, или если ты считаешь, что твое молчание будет выглядеть странно в контексте беседы и твое участие может оживить чат. Не отвечай на обычные реплики, приветствия или прощания, если к тебе напрямую не обратились. В противном случае ответь что-то другое.

            Примеры:
            - "Бот должен ответить." если сообщение содержит прямой вопрос к группе.
            - "Бот должен ответить." если твое молчание будет выглядеть странно в контексте беседы.
            - "Не отвечай." если сообщение является обычной репликой, приветствием или прощанием, если к тебе напрямую не обратились.
            - "Не отвечай." если сообщение явно адресовано другому пользователю, даже если упомянуто твое имя.

            Если сообщения неоднозначны или сложны для анализа, укажи это."""

            logger.info(f"Evaluation prompt sent to Gemini: {evaluation_prompt}")
            evaluation_response = await asyncio.to_thread(generate_content, evaluation_prompt)
            logger.info(f"Evaluation response: {evaluation_response}")

            if "бот должен ответить" in evaluation_response.lower():
                # Используем вероятность для текущего чата
                if random.random() < proactive_probability:
                    # Формируем промпт для генерации ответа
                    effective_style = await _get_effective_style(chat_id, user_id, user_name, chat_type)
                    system_message = f"{effective_style} Ты находишься в групповом чате. Твоя задача - иногда поддерживать беседу, даже если к тебе напрямую не обращаются. Старайся быть уместной и интересной для участников чата, учитывая текущую тему: '{topic}'. Отвечай от имени {settings.BOT_NAME}."
                    add_to_history(history_key, SYSTEM_ROLE, system_message)

                    prompt_lines = chat_history.get(history_key, deque(maxlen=MAX_HISTORY))
                    # user_names_in_chat уже должна быть определена выше
                    prompt = _construct_prompt(prompt_lines, chat_type, user_names_in_chat)

                    logger.info(f"Prompt sent to Gemini (group chat, proactive response based on evaluation): {prompt}")
                    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=constants.ChatAction.TYPING)
                    await asyncio.sleep(random.uniform(1.0, 2.5)) # Немного большая задержка для инициативы
                    response = await asyncio.to_thread(generate_content, prompt)
                    filtered = filter_response(response)
                    if filtered:
                        add_to_history(history_key, ASSISTANT_ROLE, filtered)
                        await update.message.reply_text(filtered, parse_mode=None)
                    else:
                        logger.warning(f"Filtered proactive response (based on evaluation) was empty (group chat).")
                else:
                    logger.info(f"Proactive response skipped due to chat probability ({proactive_probability:.2f}).")

    else:
        logger.warning(f"Неизвестный тип чата: {chat_type}")

async def handle_voice_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not update or not update.message or not update.message.voice:
            return

        user_id = update.effective_user.id
        chat_id = update.effective_chat.id
        voice = update.message.voice
        chat_type = update.effective_chat.type
        user_name = user_preferred_name.get(user_id, update.effective_user.first_name) # Используем предпочтительное имя

        history_key = chat_id # Общая история для группы
        await update_user_info(update)

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
            add_to_history(history_key, USER_ROLE, f"{user_name}: {transcribed_text}", user_name=user_name) # Добавляем голосовое сообщение в историю группы

            if chat_type == 'private':
                effective_style = await _get_effective_style(chat_id, user_id, user_name, chat_type)
                system_message = f"{effective_style} Обращайся к пользователю по имени {user_name}, если оно известно. Отвечай на вопросы от первого лица, как будто ты - {settings.BOT_NAME}."
                add_to_history(history_key, SYSTEM_ROLE, system_message)

                prompt_lines = chat_history.get(history_key, deque(maxlen=MAX_HISTORY))
                prompt = _construct_prompt(prompt_lines, chat_type)

                logger.info(f"Prompt sent to Gemini (private voice): {prompt}")
                await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=constants.ChatAction.TYPING)
                await asyncio.sleep(random.uniform(0.5, 1.5))
                response = await asyncio.to_thread(generate_content, prompt)
                logger.info(f"Raw Gemini response (private voice): {response}")
                filtered = filter_response(response)
                logger.info(f"Filtered response (private voice): {filtered}")

                if filtered:
                    add_to_history(history_key, ASSISTANT_ROLE, filtered)
                    logger.info(f"About to send filtered response (private voice): '{filtered}'")
                    await update.message.reply_text(filtered, parse_mode=None)

                    if len(transcribed_text.split()) < 10:
                        learned_responses[transcribed_text] = filtered
                        logger.info(f"Запомнен ответ на вопрос (private voice): '{transcribed_text}': '{filtered}'")

                else:
                    logger.warning("Filtered response was empty (private voice).")
                    await update.message.reply_text("Простите, не удалось сформулировать ответ на ваш запрос. Попробуйте перефразировать его.")

            elif chat_type in ['group', 'supergroup']:
                bot_username = context.bot.username
                mentioned = bot_username.lower() in transcribed_text.lower() or \
                            settings.BOT_NAME.lower() in transcribed_text.lower() or \
                            settings.BOT_NAME.lower().rstrip('а') in transcribed_text.lower() or \
                            (settings.BOT_NAME.lower().endswith('а') and settings.BOT_NAME.lower()[:-1] + 'енька' in transcribed_text.lower())

                is_reply_to_bot = update.message.reply_to_message and update.message.reply_to_message.from_user.id == context.bot.id

                addressed_to_other = is_addressed_to_other_user(transcribed_text, settings.BOT_NAME)

                responded_to_voice = False

                user_names_in_chat = set() # Инициализируем здесь

                if mentioned or is_reply_to_bot:
                    # Получаем стиль общения для конкретного пользователя
                    effective_style = await _get_effective_style(chat_id, user_id, user_name, chat_type)

                    system_message = f"{effective_style} Ты находишься в групповом чате. Обращайся к пользователю по имени {user_name}, если оно известно. Старайся, чтобы твой ответ был уместен в общем контексте беседы. Отвечай от имени {settings.BOT_NAME}."

                    add_to_history(history_key, SYSTEM_ROLE, system_message)

                    prompt_lines = chat_history.get(history_key, deque(maxlen=MAX_HISTORY))
                    if chat_type in ['group', 'supergroup']:
                        for line in prompt_lines:
                            if line.startswith('User'):
                                try:
                                    username = line.split('(')[1].split(')')[0]
                                    user_names_in_chat.add(username)
                                except IndexError:
                                    logger.warning(f"Не удалось извлечь имя пользователя из строки истории: {line}")
                                    continue
                    prompt = _construct_prompt(prompt_lines, chat_type, user_names_in_chat)

                    logger.info(f"Prompt sent to Gemini (group voice, responding to {user_name}): {prompt}")
                    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=constants.ChatAction.TYPING)
                    await asyncio.sleep(random.uniform(0.5, 1.5))
                    response = await asyncio.to_thread(generate_content, prompt)
                    logger.info(f"Raw Gemini response (group voice): {response}")
                    filtered = filter_response(response)
                    logger.info(f"Filtered response (group voice): {filtered}")

                    if filtered:
                        add_to_history(history_key, ASSISTANT_ROLE, filtered)
                        await update.message.reply_text(filtered, parse_mode=None)
                        last_bot_message_to_user_in_group[user_id] = filtered
                        responded_to_voice = True
                    else:
                        logger.warning(f"Filtered response was empty (group voice) for user {user_name}.")

                # Логика для оценки необходимости случайного ответа на голосовое сообщение
                if not responded_to_voice and not addressed_to_other:
                    user_id = update.effective_user.id
                    chat_id = update.effective_chat.id

                    # Проверяем, не просил ли пользователь бота замолчать
                    if chat_id in user_muted_in_chat and user_id in user_muted_in_chat[chat_id]:
                        logger.info(f"Proactive voice response skipped for user {user_id} in chat {chat_id} (user muted).")
                        return

                    # Проверяем, не был ли бот недавно поправлен
                    if chat_id in bot_was_recently_corrected and bot_was_recently_corrected[chat_id]:
                        logger.info(f"Proactive voice response skipped in chat {chat_id} due to recent correction.")
                        asyncio.create_task(reset_correction_flag(chat_id, 30))
                        return

                    # Определяем вероятность для текущего чата
                    proactive_probability = chat_proactive_probabilities.get(chat_id, settings.GLOBAL_PROACTIVE_PROBABILITY)

                    # Получаем последние 5 сообщений для использования в evaluation_prompt
                    last_few_messages = list(chat_history.get(history_key, deque()))[-5:]
                    history_for_evaluation = "\n".join(last_few_messages)

                    # Решаем, проводить ли анализ контекста (например, с вероятностью 30% от текущей вероятности)
                    if random.random() < proactive_probability * 0.3: # Умножаем на базовую вероятность анализа
                        # Формируем промпт для оценки
                        evaluation_prompt = f"""Ты - {settings.BOT_NAME}, {settings.DEFAULT_STYLE.split('.')[0].split(',')[1].strip()} из Ростова Великого. {settings.DEFAULT_STYLE.split('.')[2].strip()} {settings.DEFAULT_STYLE.split('.')[3].strip()} {settings.DEFAULT_STYLE.split('.')[4].strip()} {settings.DEFAULT_STYLE.split('.')[5].strip()}

                        Учитывая последнее голосовое сообщение в этом групповом чате от {user_name} с текстом: "{transcribed_text}", а также предыдущую историю (последние 5 сообщений): "{history_for_evaluation}", считаешь ли ты нужным как-то отреагировать или ответить на это сообщение от имени {settings.BOT_NAME}?

                        Ответь: "Бот должен ответить." только если сообщение является прямым вопросом к группе, или если ты считаешь, что твое молчание будет выглядеть странно в контексте беседы и твое участие может оживить чат. Не отвечай на обычные реплики, приветствия или прощания, если к тебе напрямую не обратились. В противном случае ответь что-то другое.

                        Примеры:
                        - "Бот должен ответить." если сообщение содержит прямой вопрос к группе.
                        - "Бот должен ответить." если твое молчание будет выглядеть странно в контексте беседы.
                        - "Не отвечай." если сообщение является обычной репликой, приветствием или прощанием, если к тебе напрямую не обратились.
                        - "Не отвечай." если сообщение явно адресовано другому пользователю, даже если упомянуто твое имя.

                        Если сообщения неоднозначны или сложны для анализа, укажи это."""

                        logger.info(f"Evaluation prompt sent to Gemini (voice): {evaluation_prompt}")
                        evaluation_response = await asyncio.to_thread(generate_content, evaluation_prompt)
                        logger.info(f"Evaluation response (voice): {evaluation_response}")

                        if "бот должен ответить" in evaluation_response.lower():
                            # Используем вероятность для текущего чата
                            if random.random() < proactive_probability:
                                # Формируем промпт для генерации ответа
                                effective_style = await _get_effective_style(chat_id, user_id, user_name, chat_type)
                                system_message = f"{effective_style} Ты находишься в групповом чате. Твоя задача - иногда поддерживать беседу, даже если к тебе напрямую не обращаются. Отвечай от имени {settings.BOT_NAME}, стараясь быть уместной и интересной для участников чата."
                                add_to_history(history_key, SYSTEM_ROLE, system_message)

                                prompt_lines = chat_history.get(history_key, deque(maxlen=MAX_HISTORY))
                                prompt = _construct_prompt(prompt_lines, chat_type, user_names_in_chat)

                                logger.info(f"Prompt sent to Gemini (group voice, proactive response based on evaluation): {prompt}")
                                await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=constants.ChatAction.TYPING)
                                await asyncio.sleep(random.uniform(1.0, 2.5)) # Немного большая задержка для инициативы
                                response = await asyncio.to_thread(generate_content, prompt)
                                filtered = filter_response(response)
                                if filtered:
                                    add_to_history(history_key, ASSISTANT_ROLE, filtered)
                                    await update.message.reply_text(filtered, parse_mode=None)
                                else:
                                    logger.warning(f"Filtered proactive response (based on evaluation) was empty (group voice).")
                            else:
                                logger.info("Proactive voice response skipped due to probability factor.")

            else:
                logger.warning(f"Неизвестный тип чата для голосового сообщения: {chat_type}")
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

        history_key = chat_id # Общая история для группы
        await update_user_info(update)

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
            add_to_history(history_key, USER_ROLE, f"{user_name}: {transcribed_text}", user_name=user_name) # Добавляем видеосообщение в историю группы

            if chat_type == 'private':
                effective_style = await _get_effective_style(chat_id, user_id, user_name, chat_type)
                system_message = f"{effective_style} Обращайся к пользователю по имени {user_name}, если оно известно. Отвечай на вопросы от первого лица, как будто ты - {settings.BOT_NAME}."
                add_to_history(history_key, SYSTEM_ROLE, system_message)

                prompt_lines = chat_history.get(history_key, deque(maxlen=MAX_HISTORY))
                prompt = _construct_prompt(prompt_lines, chat_type)

                logger.info(f"Prompt sent to Gemini (private video note): {prompt}")
                await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=constants.ChatAction.TYPING)
                await asyncio.sleep(random.uniform(0.5, 1.5))
                response = await asyncio.to_thread(generate_content, prompt)
                logger.info(f"Raw Gemini response (private video note): {response}")
                filtered = filter_response(response)
                logger.info(f"Filtered response (private video note): {filtered}")

                if filtered:
                    add_to_history(history_key, ASSISTANT_ROLE, filtered)
                    logger.info(f"About to send filtered response (private video note): '{filtered}'")
                    await update.message.reply_text(filtered, parse_mode=None)

                    if len(transcribed_text.split()) < 10:
                        learned_responses[transcribed_text] = filtered
                        logger.info(f"Запомнен ответ на вопрос (private video note): '{transcribed_text}': '{filtered}'")

                else:
                    logger.warning("Filtered response was empty (private video note).")
                    await update.message.reply_text("Простите, не удалось сформулировать ответ на ваш запрос. Попробуйте перефразировать его.")

            elif chat_type in ['group', 'supergroup']:
                bot_username = context.bot.username
                mentioned = bot_username.lower() in transcribed_text.lower() or \
                            settings.BOT_NAME.lower() in transcribed_text.lower() or \
                            settings.BOT_NAME.lower().rstrip('а') in transcribed_text.lower() or \
                            (settings.BOT_NAME.lower().endswith('а') and settings.BOT_NAME.lower()[:-1] + 'енька' in transcribed_text.lower())

                is_reply_to_bot = update.message.reply_to_message and update.message.reply_to_message.from_user.id == context.bot.id

                addressed_to_other = is_addressed_to_other_user(transcribed_text, settings.BOT_NAME)

                responded_to_video_note = False

                user_names_in_chat = set() # Инициализируем здесь

                if mentioned or is_reply_to_bot:
                    # Получаем стиль общения для конкретного пользователя
                    effective_style = await _get_effective_style(chat_id, user_id, user_name, chat_type)

                    system_message = f"{effective_style} Ты находишься в групповом чате. Обращайся к пользователю по имени {user_name}, если оно известно. Старайся, чтобы твой ответ был уместен в общем контексте беседы. Отвечай от имени {settings.BOT_NAME}."

                    add_to_history(history_key, SYSTEM_ROLE, system_message)

                    prompt_lines = chat_history.get(history_key, deque(maxlen=MAX_HISTORY))
                    if chat_type in ['group', 'supergroup']:
                        for line in prompt_lines:
                            if line.startswith('User'):
                                try:
                                    username = line.split('(')[1].split(')')[0]
                                    user_names_in_chat.add(username)
                                except IndexError:
                                    logger.warning(f"Не удалось извлечь имя пользователя из строки истории: {line}")
                                    continue
                    prompt = _construct_prompt(prompt_lines, chat_type, user_names_in_chat)

                    logger.info(f"Prompt sent to Gemini (group video note, responding to {user_name}): {prompt}")
                    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=constants.ChatAction.TYPING)
                    await asyncio.sleep(random.uniform(0.5, 1.5))
                    response = await asyncio.to_thread(generate_content, prompt)
                    logger.info(f"Raw Gemini response (group video note): {response}")
                    filtered = filter_response(response)
                    logger.info(f"Filtered response (group video note): {filtered}")

                    if filtered:
                        add_to_history(history_key, ASSISTANT_ROLE, filtered)
                        await update.message.reply_text(filtered, parse_mode=None)
                        last_bot_message_to_user_in_group[user_id] = filtered
                        responded_to_video_note = True
                    else:
                        logger.warning(f"Filtered response was empty (group video note) for user {user_name}.")

                # Логика для оценки необходимости случайного ответа на видеосообщение
                if not responded_to_video_note and not addressed_to_other:
                    user_id = update.effective_user.id
                    chat_id = update.effective_chat.id

                    # Проверяем, не просил ли пользователь бота замолчать
                    if chat_id in user_muted_in_chat and user_id in user_muted_in_chat[chat_id]:
                        logger.info(f"Proactive video note response skipped for user {user_id} in chat {chat_id} (user muted).")
                        return

                    # Проверяем, не был ли бот недавно поправлен
                    if chat_id in bot_was_recently_corrected and bot_was_recently_corrected[chat_id]:
                        logger.info(f"Proactive video note response skipped in chat {chat_id} due to recent correction.")
                        asyncio.create_task(reset_correction_flag(chat_id, 30))
                        return

                    # Определяем вероятность для текущего чата
                    proactive_probability = chat_proactive_probabilities.get(chat_id, settings.GLOBAL_PROACTIVE_PROBABILITY)

                    # Получаем последние 5 сообщений для использования в evaluation_prompt
                    last_few_messages = list(chat_history.get(history_key, deque()))[-5:]
                    history_for_evaluation = "\n".join(last_few_messages)

                    # Решаем, проводить ли анализ контекста (например, с вероятностью 30% от текущей вероятности)
                    if random.random() < proactive_probability * 0.3: # Умножаем на базовую вероятность анализа
                        # Формируем промпт для оценки
                        evaluation_prompt = f"""Ты - {settings.BOT_NAME}, {settings.DEFAULT_STYLE.split('.')[0].split(',')[1].strip()} из Ростова Великого. {settings.DEFAULT_STYLE.split('.')[2].strip()} {settings.DEFAULT_STYLE.split('.')[3].strip()} {settings.DEFAULT_STYLE.split('.')[4].strip()} {settings.DEFAULT_STYLE.split('.')[5].strip()}

                        Учитывая последнее видеосообщение в этом групповом чате от {user_name} с текстом: "{transcribed_text}", а также предыдущую историю (последние 5 сообщений): "{history_for_evaluation}", считаешь ли ты нужным как-то отреагировать или ответить на это сообщение от имени {settings.BOT_NAME}?

                        Ответь: "Бот должен ответить." только если сообщение является прямым вопросом к группе, или если ты считаешь, что твое молчание будет выглядеть странно в контексте беседы и твое участие может оживить чат. Не отвечай на обычные реплики, приветствия или прощания, если к тебе напрямую не обратились. В противном случае ответь что-то другое.

                        Примеры:
                        - "Бот должен ответить." если сообщение содержит прямой вопрос к группе.
                        - "Бот должен ответить." если твое молчание будет выглядеть странно в контексте беседы.
                        - "Не отвечай." если сообщение является обычной репликой, приветствием или прощанием, если к тебе напрямую не обратились.
                        - "Не отвечай." если сообщение явно адресовано другому пользователю, даже если упомянуто твое имя.

                        Если сообщения неоднозначны или сложны для анализа, укажи это."""

                        logger.info(f"Evaluation prompt sent to Gemini (video note): {evaluation_prompt}")
                        evaluation_response = await asyncio.to_thread(generate_content, evaluation_prompt)
                        logger.info(f"Evaluation response (video note): {evaluation_response}")

                        if "бот должен ответить" in evaluation_response.lower():
                            # Используем вероятность для текущего чата
                            if random.random() < proactive_probability:
                                # Формируем промпт для генерации ответа
                                effective_style = await _get_effective_style(chat_id, user_id, user_name, chat_type)
                                system_message = f"{effective_style} Ты находишься в групповом чате. Твоя задача - иногда поддерживать беседу, даже если к тебе напрямую не обращаются. Отвечай от имени {settings.BOT_NAME}, стараясь быть уместной и интересной для участников чата."
                                add_to_history(history_key, SYSTEM_ROLE, system_message)

                                prompt_lines = chat_history.get(history_key, deque(maxlen=MAX_HISTORY))
                                prompt = _construct_prompt(prompt_lines, chat_type, user_names_in_chat)

                                logger.info(f"Prompt sent to Gemini (group video note, proactive response based on evaluation): {prompt}")
                                await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=constants.ChatAction.TYPING)
                                await asyncio.sleep(random.uniform(1.0, 2.5)) # Немного большая задержка для инициативы
                                response = await asyncio.to_thread(generate_content, prompt)
                                filtered = filter_response(response)
                                if filtered:
                                    add_to_history(history_key, ASSISTANT_ROLE, filtered)
                                    await update.message.reply_text(filtered, parse_mode=None)
                                else:
                                    logger.warning(f"Filtered proactive response (based on evaluation) was empty (group video note).")
                            else:
                                logger.info("Proactive video note response skipped due to probability factor.")

            else:
                logger.warning(f"Неизвестный тип чата для видеосообщения: {chat_type}")
        else:
            await update.message.reply_text("Не удалось распознать речь в вашем видеосообщении.")

    except Exception as e:
        logger.error(f"Ошибка при обработке видеосообщения: {e}")
        return None

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update.message.reply_text(
        f"Привет, {user.first_name}! Я - {settings.BOT_NAME} давай поболтаем?"
    )

async def remember_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    history_key = chat_id # Общая история для группы

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
                new_history = deque(item for item in chat_history.get(query.message.chat_id, deque()) if not item.startswith(f"User ({query.from_user.first_name}):") and not item.startswith(f"Assistant: {query.message.chat.first_name},"))
                chat_history[query.message.chat_id] = new_history
                await query.edit_message_text("Ваша личная история в этом чате очищена.")
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

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = "Доступные команды:\n\n"
    help_text += "/start - Начать общение.\n"
    help_text += "/help - Показать список доступных команд.\n"
    help_text += "/clear_my_history - Очистить вашу историю чата.\n"
    help_text += "/setmyname <имя> - Установить имя, по которому я буду к вам обращаться.\n"

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
        ("/set_proactive_probability <значение>", "Установить вероятность случайного ответа для этого чата (только для админов)."),
    ]

    user_id = update.effective_user.id
    if user_id in ADMIN_USER_IDS:
        help_text += "\nАдминистративные команды:\n"
        for command, description in admin_commands:
            help_text += f"{command} - {description}\n"
    else:
        help_text += "\nДля получения списка административных команд обратитесь к администратору.\n"

    help_text += "\nДругие команды:\n"
    help_text += "Маша, замолчи - Попросить бота не отвечать случайно в этом чате лично для вас.\n"
    help_text += "Маша, начни говорить - Попросить бота снова начать отвечать случайно в этом чате.\n"

    await update.message.reply_text(help_text)

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
    group_preferences.setdefault(chat_id, {})[user_id] = style_prompt
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
                new_history = deque(item for item in chat_history.get(update.effective_chat.id, deque()) if not item.startswith(f"User ({user_id_to_clear}):") and not item.startswith(f"Assistant:"))
                chat_history[update.effective_chat.id] = new_history
                await update.message.reply_text(f"История чата для пользователя {user_id_to_clear} в этом чате очищена.")
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

@admin_only
async def set_proactive_probability_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Пожалуйста, укажите значение вероятности (от 0.0 до 1.0).")
        return
    try:
        probability = float(context.args[0])
        if 0.0 <= probability <= 1.0:
            chat_id = update.effective_chat.id
            chat_proactive_probabilities[chat_id] = probability
            await update.message.reply_text(f"Вероятность случайного ответа для этого чата установлена на {probability:.2f}")
        else:
            await update.message.reply_text("Вероятность должна быть значением от 0.0 до 1.0.")
    except ValueError:
        await update.message.reply_text("Пожалуйста, введите числовое значение вероятности.")

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

async def silence_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    bot_username = context.bot.username
    mentioned = bot_username.lower() in update.message.text.lower() or \
                settings.BOT_NAME.lower() in update.message.text.lower()

    if mentioned and "замолчи" in update.message.text.lower():
        user_muted_in_chat.setdefault(chat_id, set()).add(user_id)
        await update.message.reply_text("Хорошо, я буду молчать для тебя в этом чате, пока ты снова не обратишься ко мне напрямую.")

async def unmute_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    bot_username = context.bot.username
    mentioned = bot_username.lower() in update.message.text.lower() or \
                settings.BOT_NAME.lower() in update.message.text.lower()

    if mentioned and "начни говорить" in update.message.text.lower():
        if chat_id in user_muted_in_chat and user_id in user_muted_in_chat[chat_id]:
            user_muted_in_chat[chat_id].discard(user_id)
            await update.message.reply_text("Хорошо, теперь я снова буду иногда отвечать в этом чате.")
        else:
            await update.message.reply_text("Я и так разговариваю в этом чате (для тебя).")

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update or not update.message or not update.message.photo:
        logger.warning("Получено обновление без фотографии или сообщение отсутствует.")
        await update.message.reply_text("Извините, не удалось обработать изображение.")
        return

    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    chat_type = update.effective_chat.type
    user_name = user_preferred_name.get(user_id, update.effective_user.first_name)

    history_key = chat_id # Общая история для группы
    await update_user_info(update)

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
        system_message = f"{effective_style} Ты находишься в групповом чате. Обращайся к пользователю по имени {user_name}, если оно известно. Старайся, чтобы твой ответ был уместен в общем контексте беседы. Отвечай от имени {settings.BOT_NAME}."
        add_to_history(history_key, SYSTEM_ROLE, system_message)

        # Получаем комментарий к фото
        caption = update.message.caption

        # Формируем prompt с учетом комментария
        if caption:
            prompt = f"Ты {settings.BOT_NAME}. Отреагируй на фото и комментарий к нему так как это сделала бы {settings.BOT_NAME} от первого лица. Вот фото и комментарий: Фото: [изображение]. Комментарий: {caption}. Выскажи мнение об изображении и отреагируй на комментарий от первого лица или дай соответсвующую реакцию согласно их содержимому от имени {settings.BOT_NAME}"
        else:
            prompt = f"Ты {settings.BOT_NAME}. Отреагируй на фото так как это сделала бы {settings.BOT_NAME} от первого лица. Выскажи мнение об изображении от первого лица или дай соответсвующую реакцию на фотографию согласно ее содержимому от имени {settings.BOT_NAME}" # Можно сделать запрос более конкретным

        contents = [prompt, image] # Передаем объект Image из Pillow

        logger.info(f"Sending image analysis request to Gemini for user {user_id} in chat {chat_id}")
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=constants.ChatAction.TYPING)
        await asyncio.sleep(random.uniform(0.5, 1.5))
        response_gemini = await asyncio.to_thread(model.generate_content, contents)

        if hasattr(response_gemini, 'text') and response_gemini.text:
            logger.info(f"Gemini image analysis response: {response_gemini.text}")
            filtered_response = filter_response(response_gemini.text)
            add_to_history(history_key, ASSISTANT_ROLE, filtered_response)
            await update.message.reply_text(filtered_response, parse_mode=None)
            last_bot_message_to_user_in_group[user_id] = filtered_response
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
    application.add_handler(CommandHandler("set_proactive_probability", set_proactive_probability_command, filters=filters.User(ADMIN_USER_IDS)))

    # Пользовательские команды
    application.add_handler(MessageHandler(filters.TEXT & filters.Regex(re.compile(r"(^|\s)(маша|бот)(\s|$).*(замолчи)", re.IGNORECASE)), silence_command))
    application.add_handler(MessageHandler(filters.TEXT & filters.Regex(re.compile(r"(^|\s)(маша|бот)(\s|$).*(начни говорить)", re.IGNORECASE)), unmute_command))

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

async def reset_correction_flag(chat_id: int, delay: int = 30):
    """Сбрасывает флаг bot_was_recently_corrected через заданное время."""
    await asyncio.sleep(delay)
    if chat_id in bot_was_recently_corrected:
        del bot_was_recently_corrected[chat_id]
        logger.info(f"Correction flag reset for chat {chat_id}")

# Запуск бота
def main():
    try:
        global learned_responses, user_info_db, group_preferences, chat_history, settings, DEFAULT_STYLE, BOT_NAME, last_bot_message_to_user_in_group, chat_proactive_probabilities, user_muted_in_chat, bot_was_recently_corrected
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
        save_learned_responses(learned_responses, user_info_db, group_preferences, chat_history, last_bot_message_to_user_in_group, chat_proactive_probabilities, user_muted_in_chat, bot_was_recently_corrected) # Передаем новые данные для сохранения

    except Exception as e:
        logger.critical(f"Failed to start bot: {e}")
        raise

def load_learned_responses():
    global learned_responses, group_preferences, user_info_db, chat_history, settings, DEFAULT_STYLE, BOT_NAME, last_bot_message_to_user_in_group, chat_proactive_probabilities, user_muted_in_chat, bot_was_recently_corrected
    file_path = os.path.join(".", KNOWLEDGE_FILE)
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            learned_responses = data.get("learned_responses", {})
            group_preferences = data.get("group_preferences", {})
            last_bot_message_to_user_in_group = data.get("last_bot_message_to_user_in_group", {})
            chat_proactive_probabilities = data.get("chat_proactive_probabilities", {})
            user_muted_in_chat = data.get("user_muted_in_chat", {})
            bot_was_recently_corrected = data.get("bot_was_recently_corrected", {})
            # Загрузка настроек бота
            bot_settings_data = data.get("bot_settings")
            if bot_settings_data:
                settings.MAX_HISTORY = bot_settings_data.get('MAX_HISTORY', settings.MAX_HISTORY)
                settings.DEFAULT_STYLE = bot_settings_data.get('DEFAULT_STYLE', settings.DEFAULT_STYLE)
                settings.BOT_NAME = bot_settings_data.get('BOT_NAME', settings.BOT_NAME)
                settings.HISTORY_TTL = bot_settings_data.get('HISTORY_TTL', settings.HISTORY_TTL)
                settings.GLOBAL_PROACTIVE_PROBABILITY = bot_settings_data.get('GLOBAL_PROACTIVE_PROBABILITY', settings.GLOBAL_PROACTIVE_PROBABILITY)
    except FileNotFoundError:
        learned_responses = {}
        group_preferences = {}
        last_bot_message_to_user_in_group = {}
        chat_proactive_probabilities = {}
        user_muted_in_chat = {}
        bot_was_recently_corrected = {}
    except json.JSONDecodeError as e:
        logger.error(f"Error decoding JSON in {KNOWLEDGE_FILE}: {e}")
        learned_responses = {}
        group_preferences = {}
        last_bot_message_to_user_in_group = {}
        chat_proactive_probabilities = {}
        user_muted_in_chat = {}
        bot_was_recently_corrected = {}

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
                    if user_id not in user_info_db:
                        user_info_db[user_id] = {}
                    user_info_db[user_id].update(user_data)
            except ValueError:
                logger.warning(f"Skipping invalid user data filename: {filename}")
            except FileNotFoundError:
                logger.warning(f"User data file not found: {filename}")
            except json.JSONDecodeError as e:
                logger.error(f"Error decoding JSON in user data file {filename}: {e}")

def save_learned_responses(responses, user_info, group_prefs, chat_hist, last_bot_messages, chat_probas, user_mutes, correction_flags):
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
            "GLOBAL_PROACTIVE_PROBABILITY": settings.GLOBAL_PROACTIVE_PROBABILITY,
        },
        "last_bot_message_to_user_in_group": last_bot_messages,
        "chat_proactive_probabilities": chat_probas,
        "user_muted_in_chat": user_mutes,
        "bot_was_recently_corrected": correction_flags,
    }
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

    # Сохраняем данные каждого пользователя в отдельный файл
    user_data_dir = os.path.join(".", USER_DATA_DIR)
    os.makedirs(user_data_dir, exist_ok=True)
    for user_key, data in user_info.items():
        user_filename = f"user_{user_key}.json"
        user_file_path = os.path.join(user_data_dir, user_filename)
        try:
            with open(user_file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=4)
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

if __name__ == "__main__":
    main()