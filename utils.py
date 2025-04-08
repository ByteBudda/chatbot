# utils.py
import os
import re
import time
import asyncio
from io import BytesIO
from functools import lru_cache
from typing import Dict, Set, Deque, Optional
import json
import google.generativeai as genai
import speech_recognition as sr
from PIL import Image
from pydub import AudioSegment
from telegram import Update
import random
from transformers import pipeline

# Импорты из других модулей проекта
from config import (logger, GEMINI_API_KEY, DEFAULT_STYLE, BOT_NAME,
                    CONTEXT_CHECK_PROMPT, ASSISTANT_ROLE, settings) # Добавили settings
# Импортируем нужные части состояния из state.py
from state import chat_history, user_info_db, group_preferences, group_user_style_prompts, user_preferred_name, bot_activity_percentage

# --- Инициализация AI, Анализатора и RuBERT ---
try:
    genai.configure(api_key=GEMINI_API_KEY)
    # Используем 'gemini-1.5-flash' как рекомендованный для текста и изображений
    model = genai.GenerativeModel('gemini-2.0-flash')
    logger.info("Gemini AI model initialized successfully in utils.")
except Exception as e:
    logger.critical(f"Failed to configure Gemini AI in utils: {e}")
    model = None # Установим в None, чтобы проверки ниже работали


# --- RuBERT Pipelines ---
_ner_pipeline = None
_sentiment_pipeline = None

def get_ner_pipeline():
    global _ner_pipeline
    if _ner_pipeline is None:
        try:
            _ner_pipeline = pipeline("ner", model="Data-Lab/rubert-base-cased-conversational_ner-v3", tokenizer="Data-Lab/rubert-base-cased-conversational_ner-v3")
            logger.info("RuBERT NER pipeline initialized.")
        except Exception as e:
            logger.error(f"Error initializing RuBERT NER pipeline: {e}", exc_info=True)
            _ner_pipeline = None
    return _ner_pipeline

def get_sentiment_pipeline():
    global _sentiment_pipeline
    if _sentiment_pipeline is None:
        try:
            _sentiment_pipeline = pipeline("sentiment-analysis", model="blanchefort/rubert-base-cased-sentiment")
            logger.info("RuBERT Sentiment Analysis pipeline initialized.")
        except Exception as e:
            logger.error(f"Error initializing RuBERT Sentiment Analysis pipeline: {e}", exc_info=True)
            _sentiment_pipeline = None
    return _sentiment_pipeline

# --- Prompt Builder Class ---
class PromptBuilder:
    def __init__(self, bot_name, default_style):
        self.bot_name = bot_name
        self.default_style = default_style

    def build_prompt(self, history_str, user_name, prompt_text, system_message_base, topic_context="", entities=None, sentiment=None):
        prompt = f"{system_message_base} {topic_context}\n\nИстория диалога:\n{history_str}\n\n{user_name}: {prompt_text}\n{self.bot_name}:"
        if entities:
            prompt += f"\n\nИзвлеченные сущности: {entities}"
        if sentiment:
            prompt += f"\n\nТональность сообщения: {sentiment}"
        return prompt

# --- AI и Вспомогательные Функции ---

@lru_cache(maxsize=128)
def generate_content_sync(prompt: str) -> str:
    """Синхронная обертка для вызова Gemini API (текст)."""
    if not model:
        logger.error("Gemini model not initialized. Cannot generate content.")
        return "[Ошибка: Модель AI не инициализирована]"

    logger.info(f"Sending prompt to Gemini (first 100 chars): {prompt[:100]}...")
    try:
        response = model.generate_content(prompt)
        if hasattr(response, 'text') and response.text:
            logger.info(f"Received response from Gemini (first 100 chars): {response.text[:100]}...")
            return response.text
        elif response.prompt_feedback.block_reason:
             logger.warning(f"Gemini response blocked. Reason: {response.prompt_feedback.block_reason}")
             return f"[Ответ заблокирован: {response.prompt_feedback.block_reason}]"
        else:
            logger.warning("Gemini response was empty or lacked text attribute.")
            return "[Пустой ответ от Gemini]"
    except Exception as e:
        logger.error(f"Gemini content generation error: {e}", exc_info=True)
        return f"[Произошла ошибка при генерации ответа: {type(e).__name__}]"

async def generate_vision_content_async(contents: list) -> str:
    """Асинхронная функция для вызова Gemini Vision API."""
    if not model:
        logger.error("Gemini model not initialized. Cannot generate vision content.")
        return "[Ошибка: Модель AI не инициализирована]"

    logger.info("Sending image/prompt to Gemini Vision...")
    try:
        # Вызов может быть синхронным внутри to_thread, если SDK не async
        response = await asyncio.to_thread(model.generate_content, contents)
        response_text = response.text if hasattr(response, 'text') else ''
        if not response_text and hasattr(response, 'prompt_feedback') and response.prompt_feedback.block_reason:
            response_text = f"[Ответ на изображение заблокирован: {response.prompt_feedback.block_reason}]"
        elif not response_text:
            response_text = "[Не удалось получить описание изображения]"
        logger.info(f"Received vision response (first 100 chars): {response_text[:100]}...")
        return response_text
    except Exception as e:
        logger.error(f"Gemini Vision API error: {e}", exc_info=True)
        return "[Ошибка при анализе изображения]"


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
    """Распознает речь из аудиофайла."""
    logger.info(f"Attempting to transcribe audio file: {file_path}")
    recognizer = sr.Recognizer()
    try:
        with sr.AudioFile(file_path) as source:
            audio_data = recognizer.record(source)
            logger.info("Audio data recorded from file.")
            try:
                text = await asyncio.to_thread(recognizer.recognize_google, audio_data, language="ru-RU")
                logger.info(f"Transcription successful: {text}")
                return text
            except sr.UnknownValueError:
                logger.warning("Google Speech Recognition could not understand audio.")
                return "[Не удалось распознать речь]"
            except sr.RequestError as e:
                logger.error(f"Could not request results from Google Speech Recognition service; {e}")
                return f"[Ошибка сервиса распознавания: {e}]"
    except FileNotFoundError:
         logger.error(f"Audio file not found for transcription: {file_path}")
         return "[Ошибка: Файл не найден]"
    except Exception as e:
        logger.error(f"Error processing audio file {file_path}: {e}", exc_info=True)
        return "[Ошибка обработки аудио]"
    finally:
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
                logger.info(f"Temporary audio file deleted: {file_path}")
            except OSError as e:
                logger.error(f"Error deleting temporary file {file_path}: {e}")

async def _get_effective_style(chat_id: int, user_id: int, user_name: Optional[str], chat_type: str) -> str:
    """Определяет эффективный стиль общения."""
    # Используем переменные состояния, импортированные из state.py
    if chat_type in ['group', 'supergroup']:
        group_style = group_preferences.get(chat_id, {}).get("style")
        user_group_style = group_user_style_prompts.get((chat_id, user_id))
        if user_group_style: return user_group_style
        if group_style: return group_style

    user_personal_style = user_info_db.get(user_id, {}).get("preferences", {}).get("style")
    if user_personal_style: return user_personal_style

    relationship_obj = user_info_db.get(user_id, {}).get('relationship')
    if relationship_obj and hasattr(relationship_obj, 'get_prompt'):
         return relationship_obj.get_prompt(user_name)

    return DEFAULT_STYLE # Из config.py

async def is_context_related(current_message: str, user_id: int, chat_id: int, chat_type: str) -> bool:
    """Проверяет, связано ли сообщение пользователя с последним ответом бота."""
    history_key = chat_id if chat_type in ['group', 'supergroup'] else user_id
    history = chat_history.get(history_key) # Из state.py
    if not history: return False

    last_bot_message = None
    for entry in reversed(history):
        if entry.startswith(f"{ASSISTANT_ROLE}:"): # Из config.py
            last_bot_message = entry[len(ASSISTANT_ROLE)+1:].strip()
            break
    if not last_bot_message: return False
    if len(current_message.split()) < 2: return False

    prompt = CONTEXT_CHECK_PROMPT.format(current_message=current_message, last_bot_message=last_bot_message) # Из config.py
    logger.debug(f"Checking context for user {user_id} in chat {chat_id}")
    try:
        response_text = await asyncio.to_thread(generate_content_sync, prompt) # Используем общую функцию
        logger.debug(f"Context check response: {response_text}")
        is_related = response_text.strip().lower().startswith("да")
        logger.info(f"Context check result for user {user_id}: {is_related}")
        return is_related
    except Exception as e:
        logger.error(f"Error during context check: {e}", exc_info=True)
        return False

async def update_user_info(update: Update):
    """Обновляет информацию о пользователе в user_info_db."""
    if not update.effective_user: return

    user = update.effective_user
    user_id = user.id
    if user_id not in user_info_db: # user_info_db из state.py
        user_info_db[user_id] = {"preferences": {}, "first_seen": time.time()}

    user_info_db[user_id]["username"] = user.username
    user_info_db[user_id]["first_name"] = user.first_name
    user_info_db[user_id]["last_name"] = user.last_name
    user_info_db[user_id]["is_bot"] = user.is_bot
    user_info_db[user_id]["language_code"] = user.language_code
    user_info_db[user_id]["last_seen"] = time.time()

    if user_id not in user_preferred_name: # user_preferred_name из state.py
        user_preferred_name[user_id] = user.first_name

    logger.debug(f"User info updated for user_id: {user_id}")

async def cleanup_audio_files_job(context): # Переименовано для ясности
    """Периодическая задача для удаления временных аудиофайлов."""
    # context не используется
    bot_folder = "."
    deleted_count = 0
    logger.debug("Starting temporary audio file cleanup...")
    try:
        for filename in os.listdir(bot_folder):
            if filename.lower().endswith((".wav", ".oga", ".mp4")): # Добавили mp4 для видеозаметок
                file_path = os.path.join(bot_folder, filename)
                try:
                    os.remove(file_path)
                    logger.info(f"Deleted temporary audio/video file: {file_path}")
                    deleted_count += 1
                except Exception as e:
                    logger.error(f"Error deleting file {file_path}: {e}")
        if deleted_count > 0:
            logger.info(f"Temporary audio/video file cleanup finished. Deleted {deleted_count} files.")
        else:
            logger.debug("Temporary audio/video file cleanup: No files found to delete.")
    except Exception as e:
        logger.error(f"Error during temporary audio/video file cleanup: {e}")

def should_process_message(activity_percentage: int) -> bool:
    """Определяет, следует ли обрабатывать сообщение на основе процента активности."""
    return random.randint(1, 100) <= activity_percentage

def get_bot_activity_percentage() -> int:
    """Возвращает текущий процент активности бота."""
    from state import bot_activity_percentage
    return bot_activity_percentage
