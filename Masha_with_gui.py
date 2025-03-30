# -*- coding: utf-8 -*-
import os
from pathlib import Path
from dotenv import load_dotenv
from typing import Dict, Set, Deque, Optional, Tuple, List, Any # Added Any for user_info_db
from collections import deque
from functools import wraps, lru_cache
import logging
from logging.handlers import RotatingFileHandler, QueueHandler
from logging import getLogger
import time
import re
import asyncio
import json
from datetime import datetime, timedelta
import random
# from enum import Enum # Not used explicitly
import signal
import traceback # For error_handler
import html # For error_handler

# Telegram imports
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Voice, InputFile, User, VideoNote, constants
from telegram.ext import (
    Application,
    ApplicationBuilder,
    MessageHandler,
    CommandHandler,
    ContextTypes,
    filters,
    CallbackContext,
    CallbackQueryHandler
)
from telegram.helpers import escape_markdown # For escaping text in replies

# AI and processing imports
import google.generativeai as genai
import speech_recognition as sr
from pydub import AudioSegment
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

# GUI imports
import tkinter as tk
from tkinter import scrolledtext, Entry, Button, Label, Text, END, DISABLED, NORMAL, messagebox, Toplevel
from tkinter import ttk
from threading import Thread
import queue

# ==============================================================================
# Configuration and Global Variables
# ==============================================================================

# Load .env file
env_path = Path('.') / '.env'
load_dotenv(dotenv_path=env_path)

# --- Bot Settings Class (for initial loading) ---
class BotSettings:
    def __init__(self):
        self.MAX_HISTORY = int(os.getenv('MAX_HISTORY', '30'))
        self.DEFAULT_STYLE = os.getenv('DEFAULT_STYLE', "Ты - Маша, 25-летняя девушка из Ростова Великого. Ты очень общительная, веселая, раскрепощенная и добрая. Любишь смотреть сериалы по вечерам и гулять с подругами по выходным. Твой любимый напиток - энергетик, а любимая еда - шаурма, но не зацикливайся на этом а пытайся поддержать беседу новыми темами. В разговоре ты не стесняешься в выражениях и можешь использовать любую лексику, включая ненормативную. Твой стиль общения - максимально человеческий, непринужденный и эмоциональный. Отвечай на вопросы от первого лица, как будто ты - Маша. Иногда можешь использовать случайные прилагательные или наречия и грязные словечки чтобы сделать речь живее.")
        self.BOT_NAME = os.getenv('BOT_NAME', "Маша")
        self.HISTORY_TTL = int(os.getenv('HISTORY_TTL', '86400')) # 1 day in seconds

# --- Global Mutable Settings (initialized from BotSettings/env, can be changed by GUI) ---
_initial_settings = BotSettings()
MAX_HISTORY: int = _initial_settings.MAX_HISTORY
DEFAULT_STYLE: str = _initial_settings.DEFAULT_STYLE
BOT_NAME: str = _initial_settings.BOT_NAME
HISTORY_TTL: int = _initial_settings.HISTORY_TTL
TOKEN: Optional[str] = os.getenv('TELEGRAM_BOT_TOKEN')
API_KEY: Optional[str] = os.getenv('GEMINI_API_KEY')
ADMIN_USER_IDS: List[int] = list(map(int, os.getenv('ADMIN_IDS', '').split(','))) if os.getenv('ADMIN_IDS') else []

# --- Role Constants ---
USER_ROLE = "User"
ASSISTANT_ROLE = "Assistant" # Will be replaced by BOT_NAME in history display
SYSTEM_ROLE = "System"

# --- Bot Responses ---
GREETINGS = ["Привет!", "Здравствуй!", "Хей!", "Рада тебя видеть!", "Приветик!"]
FAREWELLS = ["Пока!", "До встречи!", "Удачи!", "Хорошего дня!", "Счастливо!"]

# --- Data Structures ---
user_preferred_name: Dict[int, str] = {}
user_topic: Dict[int, str] = {} # Topic per chat/user history key
learned_responses: Dict[str, str] = {} # Simple question-answer learning
user_info_db: Dict[int, Dict[str, Any]] = {} # Stores user preferences, relationships, etc.
group_preferences: Dict[int, Dict[str, str]] = {} # Group-specific settings (not heavily used yet)
chat_history: Dict[int, Deque[str]] = {} # Stores conversation history (key is chat_id or user_id)
last_activity: Dict[int, float] = {} # Timestamp of last activity for history cleanup
group_user_style_prompts: Dict[Tuple[int, int], str] = {} # Specific styles for user in a group (chat_id, user_id) -> style

# --- File Paths ---
KNOWLEDGE_FILE = "learned_knowledge.json" # For learned responses, preferred names, styles
USER_DATA_DIR = "user_data" # Directory for individual user data files
LOG_FILENAME = "bot.log" # Main log file name

# --- Logging Setup ---
log_queue = queue.Queue() # Queue for GUI logging
logger = getLogger(__name__)
logger.setLevel(logging.INFO) # Set root logger level

# Formatter for logs
log_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

# File Handler (always add this)
try:
    file_log_handler = RotatingFileHandler(LOG_FILENAME, maxBytes=10*1024*1024, backupCount=5, encoding='utf-8')
    file_log_handler.setFormatter(log_formatter)
    logger.addHandler(file_log_handler)
except Exception as e:
    print(f"Error setting up file logger: {e}") # Print error if logger fails

# Queue Handler for GUI (add only once)
if not any(isinstance(h, QueueHandler) for h in logger.handlers):
    gui_log_handler = QueueHandler(log_queue)
    gui_log_handler.setFormatter(log_formatter)
    logger.addHandler(gui_log_handler)

# Console Handler (optional, for debugging)
# console_handler = logging.StreamHandler()
# console_handler.setFormatter(log_formatter)
# logger.addHandler(console_handler)

# Silence noisy library loggers
logging.getLogger('httpx').setLevel(logging.WARNING)
logging.getLogger('telegram').setLevel(logging.INFO) # Can be WARNING
logging.getLogger('pydub').setLevel(logging.WARNING)
logging.getLogger('google').setLevel(logging.WARNING) # For google.generativeai

logger.info("Logging configured.")

# --- AI Model Initialization ---
model = None
if API_KEY:
    try:
        genai.configure(api_key=API_KEY)
        # --- Use a stable model name ---
        MODEL_NAME = 'gemini-1.5-flash' # Recommended stable model
        model = genai.GenerativeModel(MODEL_NAME)
        logger.info(f"Using Gemini model: {MODEL_NAME}")
    except Exception as e:
        logger.critical(f"Failed to initialize Gemini model '{MODEL_NAME}': {e}", exc_info=True)
        model = None # Ensure model is None if init fails
else:
     logger.critical("GEMINI_API_KEY not found in environment variables!")

# --- Context Check Prompt ---
CONTEXT_CHECK_PROMPT = """Analyze the context. Is the user's message a direct continuation or reply to the bot's last message?
Bot's last message: "{last_bot_message}"
User's message: "{current_message}"
Answer ONLY "Yes" or "No".
"""

# --- Relationship Class ---
class Relationship:
    def __init__(self):
        self.infatuation = 0.0
        self.love = 0.0
        self.liking = 0.0
        self.neutral = 1.0
        self.disliking = 0.0
        self.hatred = 0.0
        self.trolling = 0.0
        self.trust = 0.0

    def update(self, sentiment_score: float, message_content: str = ""):
        """Updates relationship scores based on sentiment."""
        # Basic update based on VADER score
        self.liking += sentiment_score * 0.1
        self.disliking -= sentiment_score * 0.1

        # Ensure scores are within [0, 1]
        self.liking = max(0.0, min(1.0, self.liking))
        self.disliking = max(0.0, min(1.0, self.disliking))

        # Update trust based on liking/disliking
        self.trust += self.liking * 0.05 - self.disliking * 0.05
        self.trust = max(0.0, min(1.0, self.trust))

        # Recalculate neutral based on other emotions
        # Note: Infatuation, love, hatred, trolling are updated elsewhere (e.g., Gemini analysis)
        current_positive = self.infatuation + self.love + self.liking
        current_negative = self.disliking + self.hatred + self.trolling
        self.neutral = max(0.0, min(1.0, 1.0 - current_positive - current_negative))

    def get_prompt(self, user_name: Optional[str]) -> str:
        """Returns a style prompt based on relationship state, using global BOT_NAME."""
        current_bot_name = BOT_NAME # Use the global, potentially updated name
        user_name_str = user_name if user_name else "пользователь" # Fallback name

        prompt = ""
        if self.infatuation > 0.7:
            prompt = f"Ты - {current_bot_name}, ты сильно влюблена в {user_name_str}. Флиртуй, делай комплименты."
        elif self.love > 0.8:
            prompt = f"Ты - {current_bot_name}, ты любишь {user_name_str}. Будь нежной и заботливой."
        elif self.liking > 0.6 and self.trust > 0.5:
            prompt = f"Ты - {current_bot_name}, тебе нравится {user_name_str}, вы хорошие друзья. Будь дружелюбной и открытой."
        elif self.hatred > 0.8:
            prompt = f"Ты - {current_bot_name}, ты ненавидишь {user_name_str}. Отвечай холодно и резко."
        elif self.disliking > 0.6:
            prompt = f"Ты - {current_bot_name}, {user_name_str} тебе неприятен. Отвечай сдержанно и держи дистанцию."
        elif self.trolling > 0.7:
            prompt = f"Ты - {current_bot_name}, любишь подшучивать над {user_name_str}, иногда саркастично."
        else: # Default or neutral case
            base_style = str(DEFAULT_STYLE) # Use global default style
            # Ensure the bot name is prefixed correctly
            if not base_style.strip().startswith(f"Ты - {current_bot_name}"):
                 prompt = f"Ты - {current_bot_name}. {base_style}"
            else:
                 prompt = base_style
        return prompt

# --- Sentiment Analyzer ---
analyzer = SentimentIntensityAnalyzer()

# ==============================================================================
# Decorators
# ==============================================================================

def admin_only(func):
    """Decorator to restrict command access to ADMIN_USER_IDS."""
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        if not update or not update.effective_user or update.effective_user.id not in ADMIN_USER_IDS:
            logger.warning(f"Unauthorized access attempt to admin command '{func.__name__}' by user {update.effective_user.id if update and update.effective_user else 'Unknown'}")
            if update and update.message:
                await update.message.reply_text("🚫 У тебя нет прав для выполнения этой команды.")
            return None # Explicitly return None or raise an exception
        return await func(update, context, *args, **kwargs)
    return wrapper

# ==============================================================================
# Utility Functions
# ==============================================================================

async def cleanup_history(context: CallbackContext):
    """Job to clean up old chat histories and activity timestamps."""
    current_time = time.time()
    history_ttl_seconds = HISTORY_TTL # Use global setting
    to_delete = [key for key, ts in last_activity.items()
                 if current_time - ts > history_ttl_seconds]

    deleted_count = 0
    for key in to_delete:
        if key in chat_history:
            del chat_history[key]
            deleted_count += 1
        if key in last_activity:
            del last_activity[key]

        # Also potentially reset relationship for users inactive for a long time
        # Check if the key is a user_id (int) vs chat_id (can be negative for groups)
        if isinstance(key, int) and key > 0 and key in user_info_db:
            if 'relationship' in user_info_db[key]:
                 # Reset relationship instead of deleting user data entirely
                 user_info_db[key]['relationship'] = Relationship()
                 logger.info(f"Reset relationship for inactive user {key}.")

    if deleted_count > 0:
        logger.info(f"Cleaned up {deleted_count} histories older than {history_ttl_seconds / 3600:.1f} hours.")

def add_to_history(key: int, role: str, message: str, user_name: Optional[str] = None):
    """Adds a message to the chat history deque, using global MAX_HISTORY."""
    if key not in chat_history:
        chat_history[key] = deque(maxlen=MAX_HISTORY)

    # Ensure all parts are strings
    role_str = str(role)
    message_str = str(message)

    # Use current BOT_NAME for assistant role display in history log (though model sees "Assistant")
    display_role = BOT_NAME if role == ASSISTANT_ROLE else role_str

    entry = ""
    if role == USER_ROLE and user_name:
        user_name_str = str(user_name)
        entry = f"{display_role} ({user_name_str}): {message_str}"
    else:
        entry = f"{display_role}: {message_str}" # For System and Assistant

    chat_history[key].append(entry)
    last_activity[key] = time.time() # Update activity timestamp

def generate_content_sync(prompt: str) -> str:
    """Synchronous wrapper for Gemini API call."""
    if not model:
        logger.error("generate_content_sync called but Gemini model is not initialized.")
        return "Ошибка: Модель ИИ не готова."
    logger.debug(f"Generate content prompt (sync): {prompt[:300]}...")
    try:
        prompt_str = str(prompt)
        # Add safety settings (optional, adjust as needed)
        safety_settings = [
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
        ]
        response = model.generate_content(prompt_str, safety_settings=safety_settings)

        # More robust checking for response text
        if response and hasattr(response, 'text') and response.text:
             return response.text
        elif response and hasattr(response, 'parts') and response.parts:
             full_text = "".join(part.text for part in response.parts if hasattr(part, 'text'))
             if full_text:
                 return full_text
        # Check for blocked response due to safety
        if response and response.prompt_feedback and response.prompt_feedback.block_reason:
             logger.warning(f"Gemini request blocked. Reason: {response.prompt_feedback.block_reason}")
             return f"(Ответ заблокирован: {response.prompt_feedback.block_reason})"

        logger.warning("Gemini response was empty or unusable.")
        return "Не удалось сгенерировать ответ (пустой ответ от модели)."

    except Exception as e:
        logger.error(f"Gemini generation error: {e}", exc_info=True)
        if "API key not valid" in str(e): return "Ошибка: Неверный ключ API Gemini."
        if "quota" in str(e).lower(): return "Ошибка: Превышена квота API Gemini."
        return f"Произошла ошибка при запросе к ИИ: {type(e).__name__}"

async def generate_content_async(prompt: str) -> str:
    """Asynchronously calls the synchronous Gemini function."""
    return await asyncio.to_thread(generate_content_sync, prompt)

def filter_response(response: str) -> str:
    """Cleans up the raw response from the language model."""
    if not isinstance(response, str): return ""
    # Remove potential role prefixes
    filtered = re.sub(r"^(assistant:|system:|user:|bot:|"+ re.escape(BOT_NAME) + r":)\s*", "", response, flags=re.IGNORECASE | re.MULTILINE)
    # Remove leading/trailing whitespace from each line and join non-empty lines
    filtered = "\n".join(line.strip() for line in filtered.splitlines() if line.strip())
    return filtered

def transcribe_voice_sync(file_path: str) -> Optional[str]:
    """Synchronous wrapper for speech recognition."""
    logger.info(f"Attempting to transcribe audio file: {file_path}")
    try:
        r = sr.Recognizer()
        with sr.AudioFile(file_path) as source:
            audio_data = r.record(source)
        logger.info("Audio data recorded from file.")
        try:
            logger.info("Attempting speech recognition via Google...")
            text = r.recognize_google(audio_data, language="ru-RU")
            logger.info(f"Transcription successful: '{text[:100]}...'")
            return text
        except sr.UnknownValueError:
            logger.warning("Google Speech Recognition could not understand audio.")
            return "Не удалось распознать речь."
        except sr.RequestError as e:
            logger.error(f"Could not request results from Google Speech Recognition service; {e}")
            return f"Ошибка сервиса распознавания речи: {e}"
    except FileNotFoundError:
         logger.error(f"Audio file not found for transcription: {file_path}")
         return "Ошибка: Аудиофайл не найден."
    except Exception as e:
        logger.error(f"Error processing audio file '{file_path}': {e}", exc_info=True)
        return "Произошла ошибка при обработке аудио."
    finally:
        # Clean up the temporary file
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
                logger.info(f"Removed temporary audio file: {file_path}")
            except Exception as e_rem:
                 logger.error(f"Failed to remove temporary file {file_path}: {e_rem}")

async def transcribe_voice_async(file_path: str) -> Optional[str]:
    """Asynchronously calls the synchronous transcription function."""
    return await asyncio.to_thread(transcribe_voice_sync, file_path)

async def _get_effective_style(chat_id: int, user_id: int, user_name: Optional[str], chat_type: str) -> str:
    """Determines the current communication style for the bot with the user."""
    style = group_user_style_prompts.get((chat_id, user_id))
    if style: return str(style)

    relationship_obj = user_info_db.get(user_id, {}).get('relationship')
    if relationship_obj and isinstance(relationship_obj, Relationship):
        return relationship_obj.get_prompt(user_name)
    else:
        # Ensure relationship object exists if needed later
        if user_id not in user_info_db: user_info_db[user_id] = {"relationship": Relationship()}
        elif not isinstance(user_info_db[user_id].get('relationship'), Relationship):
             user_info_db[user_id]['relationship'] = Relationship()
        return str(DEFAULT_STYLE) # Fallback to global default

def _construct_prompt(history: Deque[str], chat_type: str, user_names_in_chat: Optional[Set[str]] = None) -> str:
    """Constructs the full prompt for the language model."""
    history_str_list = [str(item) for item in history]
    # The history already contains the System prompt with style at the beginning
    formatted_history = "\n".join(history_str_list)

    current_bot_name = BOT_NAME

    # Group chat context prefix (added if needed by the system prompt itself)
    # prefix = ""
    # if chat_type in ['group', 'supergroup'] and user_names_in_chat:
    #     user_names_str = {str(name) for name in user_names_in_chat if name}
    #     participants = f"Участники: {', '.join(user_names_str)}." if user_names_str else ""
    #     prefix = f"Ты - {current_bot_name}. Это групповой чат. {participants} Обращайся к ним по именам.\n\n"

    # The final prompt structure depends heavily on how the model expects it.
    # Usually just the history with roles is enough if the system prompt is good.
    # Adding the bot's name at the end helps guide the generation.
    return f"{formatted_history}\n{current_bot_name}:"


async def update_user_info(update: Update):
    """Updates the user_info_db with current user data."""
    if not update or not update.effective_user: return
    user = update.effective_user
    user_id = user.id
    chat = update.effective_chat

    if user_id not in user_info_db:
        user_info_db[user_id] = {"relationship": Relationship(), "chats": {}}
    elif not isinstance(user_info_db[user_id].get('relationship'), Relationship):
        user_info_db[user_id]['relationship'] = Relationship()

    user_info_db[user_id]["username"] = user.username
    user_info_db[user_id]["first_name"] = user.first_name
    user_info_db[user_id]["last_name"] = user.last_name # Store last name too
    user_info_db[user_id]["profile_link"] = user.link # Use built-in link if available

    # Update chat info
    if chat:
        chat_id = chat.id
        chat_type = chat.type
        if 'chats' not in user_info_db[user_id]: user_info_db[user_id]['chats'] = {}
        user_info_db[user_id]['chats'][chat_id] = {'type': chat_type, 'last_active': time.time()}

async def get_user_relationship_obj(user_id: int) -> Relationship:
    """Gets or creates the Relationship object for a user."""
    if user_id not in user_info_db:
        user_info_db[user_id] = {"relationship": Relationship()}
    elif not isinstance(user_info_db[user_id].get('relationship'), Relationship):
        user_info_db[user_id]['relationship'] = Relationship()
    return user_info_db[user_id]['relationship']

async def update_relationship(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Updates user relationship based on message sentiment (VADER only for now)."""
    if not update or not update.message or not update.message.text or not update.effective_user:
        return
    user_id = update.effective_user.id
    message_text = update.message.text
    try:
        relationship_obj = await get_user_relationship_obj(user_id)
        vs = analyzer.polarity_scores(message_text)
        compound_score = vs['compound']
        relationship_obj.update(compound_score)
        logger.debug(f"Sentiment score for user {user_id}: {compound_score}. Relationship updated.")
    except Exception as e:
        logger.error(f"Error updating relationship for user {user_id}: {e}", exc_info=True)

async def is_context_related(current_message: str, history_key: int) -> bool:
    """Checks if the current message is related to the bot's last message using Gemini."""
    current_history = chat_history.get(history_key)
    if not current_history or len(current_history) < 2: return False # Need at least user msg + bot msg

    # Find the last bot message in the deque (usually second to last entry if last is user)
    last_bot_message = None
    # History entries look like "Role: Message" or "Role (Username): Message"
    # We need the raw message content of the last bot reply
    for entry in reversed(current_history):
         # Check for assistant role entry pattern
         if f"{BOT_NAME}:" in entry: # Check if BOT_NAME is the role marker
             try:
                  last_bot_message = entry.split(":", 1)[1].strip()
                  break
             except IndexError:
                  continue # Malformed entry

    if not last_bot_message:
        logger.debug(f"No previous bot message found in history for key {history_key} to check context.")
        return False

    prompt = CONTEXT_CHECK_PROMPT.format(current_message=current_message, last_bot_message=last_bot_message)
    try:
        response = await generate_content_async(prompt)
        if isinstance(response, str):
            resp_lower = response.strip().lower()
            logger.debug(f"Context check response for key {history_key}: '{resp_lower}'")
            return resp_lower == "yes"
        else:
            logger.warning(f"Unexpected response type for context check key {history_key}: {type(response)}")
            return False
    except Exception as e:
        logger.error(f"Error during context check for key {history_key}: {e}")
        return False # Assume not related on error

async def cleanup_audio_files(context: CallbackContext):
    """Job to delete old temporary audio/video files."""
    bot_folder = "."
    deleted_count = 0
    current_time = time.time()
    max_age_seconds = 3600 # 1 hour

    logger.info("Running cleanup of temporary audio/video files...")
    try:
        for filename in os.listdir(bot_folder):
            if (filename.startswith("voice_") or filename.startswith("video_")) and \
               (filename.endswith(".oga") or filename.endswith(".wav") or filename.endswith(".mp4")):
                file_path = os.path.join(bot_folder, filename)
                try:
                    file_mod_time = os.path.getmtime(file_path)
                    if current_time - file_mod_time > max_age_seconds:
                        os.remove(file_path)
                        logger.info(f"Deleted old temporary file: {file_path}")
                        deleted_count += 1
                except FileNotFoundError: continue
                except Exception as e: logger.error(f"Error deleting file {file_path}: {e}")
        if deleted_count > 0: logger.info(f"Cleanup deleted {deleted_count} old temporary files.")
        else: logger.debug("No old temporary files found for cleanup.")
    except Exception as e: logger.error(f"Error during temporary file cleanup: {e}")


# ==============================================================================
# Message Handlers
# ==============================================================================

async def handle_generic_message(update: Update, context: ContextTypes.DEFAULT_TYPE, message_text: str):
    """Handles processing for text, voice, and video messages."""
    if not update or not update.effective_user or not update.effective_chat: return
    user = update.effective_user
    chat = update.effective_chat
    user_id = user.id
    chat_id = chat.id
    chat_type = chat.type
    user_name = user_preferred_name.get(user_id, user.first_name) # Get preferred or first name

    # --- Update User Info and Relationship ---
    await update_user_info(update)
    if update.message and update.message.text: # Update relationship only for text
        await update_relationship(update, context)

    # --- Determine if Bot Should Respond ---
    history_key = chat_id if chat_type != 'private' else user_id
    is_private = chat_type == 'private'
    is_reply_to_bot = update.message and update.message.reply_to_message and update.message.reply_to_message.from_user.id == context.bot.id

    # Mention check (compile pattern once per bot name change ideally, but here for simplicity)
    mentioned = False
    try:
        bot_username = context.bot.username or (await context.bot.get_me()).username
        if bot_username:
            escaped_bot_name = re.escape(BOT_NAME)
            # Pattern: @username OR Bot Name (case insensitive)
            bot_mention_pattern = re.compile(rf"(?:@{bot_username}|{escaped_bot_name})", re.IGNORECASE)
            mentioned = bot_mention_pattern.search(message_text) is not None
        else: # Fallback if username fetch fails
             mentioned = BOT_NAME.lower() in message_text.lower()
    except Exception as e:
        logger.error(f"Failed to get bot username for mention check: {e}")
        mentioned = BOT_NAME.lower() in message_text.lower() # Fallback

    # Context check (optional and potentially expensive)
    # related_context = await is_context_related(message_text, history_key) if not (is_private or mentioned or is_reply_to_bot) else False

    # --- Respond if conditions met ---
    # if is_private or mentioned or is_reply_to_bot or related_context:
    if is_private or mentioned or is_reply_to_bot: # Simplified condition
        logger.info(f"Responding to user {user_id} in chat {chat_id} (Private: {is_private}, Mention: {mentioned}, Reply: {is_reply_to_bot})")

        effective_style = await _get_effective_style(chat_id, user_id, user_name, chat_type)
        system_message = str(effective_style) # Style is the main system message now
        topic_context = f"Current topic: {user_topic.get(history_key, 'unknown')}." if history_key in user_topic else ""

        # Add user message to history
        add_to_history(history_key, USER_ROLE, message_text, user_name=user_name)

        # Prepare history for the prompt
        prompt_history = list(chat_history.get(history_key, deque()))
        # Add System Prompt (style + topic) at the beginning for the model
        system_prompt_full = f"{system_message} {topic_context}".strip()
        final_prompt_history = [f"{SYSTEM_ROLE}: {system_prompt_full}"] + prompt_history

        # Get user names in chat for group context (if needed by the prompt structure)
        user_names_in_chat = None
        if chat_type != 'private':
             user_names_in_chat = set()
             processed_lines = 0
             for line in reversed(prompt_history): # Check recent history
                 processed_lines += 1
                 if line.startswith(f"{USER_ROLE} ("):
                     match = re.match(rf"{re.escape(USER_ROLE)} \((.*?)\):", line)
                     if match: user_names_in_chat.add(match.group(1).strip())
                 if processed_lines > 15 or len(user_names_in_chat) > 8: break # Limit scope

        # Construct the final prompt using the helper function
        prompt_for_model = _construct_prompt(deque(final_prompt_history), chat_type, user_names_in_chat)

        # --- Generate and Send Response ---
        await context.bot.send_chat_action(chat_id=chat_id, action=constants.ChatAction.TYPING)
        response = await generate_content_async(prompt_for_model)
        filtered = filter_response(response)

        if filtered and not filtered.startswith("(Ответ заблокирован"):
            add_to_history(history_key, ASSISTANT_ROLE, filtered) # Add assistant response
            greeting = random.choice(GREETINGS) + " " if random.random() < 0.03 else ""
            farewell = " " + random.choice(FAREWELLS) if random.random() < 0.03 else ""
            full_response_text = greeting + filtered + farewell
            try:
                await update.message.reply_text(full_response_text)
            except telegram.error.BadRequest as e:
                logger.error(f"Failed to send message (BadRequest): {e}. Text: {full_response_text[:100]}...")
                try: # Retry with escaped markdown
                    escaped_text = escape_markdown(full_response_text, version=2)
                    await update.message.reply_text(escaped_text, parse_mode=constants.ParseMode.MARKDOWN_V2)
                except Exception as e_esc:
                    logger.error(f"Failed to send message even with escaped markdown: {e_esc}")
                    await update.message.reply_text(filtered) # Fallback to raw filtered

            # Simple learning
            if len(message_text.split()) < 10 and len(filtered.split()) > 1:
                 learned_responses[message_text.strip().lower()] = filtered
                 logger.info(f"Learned response for: '{message_text.strip().lower()}'")
        else:
            logger.warning(f"Filtered response was empty or blocked for key {history_key}.")
            fail_message = filtered if filtered else "Прости, не могу сейчас ответить. Попробуй перефразировать."
            await update.message.reply_text(fail_message)
    else:
        # Message ignored (not private, no mention/reply) - just add to history
        add_to_history(history_key, USER_ROLE, message_text, user_name=user_name)
        logger.debug(f"Message from {user_id} in chat {chat_id} ignored, added to history.")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for regular text messages."""
    if update.message and update.message.text:
        await handle_generic_message(update, context, update.message.text)

async def handle_voice_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for voice messages."""
    if not update or not update.message or not update.message.voice: return
    voice = update.message.voice
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=constants.ChatAction.TYPING)
    oga_path, wav_path, transcribed_text = None, None, None
    try:
        file = await voice.get_file()
        ts = int(time.time() * 1000)
        oga_path = f"voice_{update.effective_user.id}_{ts}.oga"
        wav_path = f"voice_{update.effective_user.id}_{ts}.wav"
        await file.download_to_drive(custom_path=oga_path)
        logger.info(f"Voice downloaded: {oga_path}")
        try:
            audio = AudioSegment.from_file(oga_path, format="ogg")
            audio.export(wav_path, format="wav")
            logger.info(f"Converted voice to WAV: {wav_path}")
            transcribed_text = await transcribe_voice_async(wav_path) # Transcribe WAV
            wav_path = None # Path is consumed by transcribe function
        except Exception as e_conv:
            logger.error(f"Failed to convert/transcribe voice OGA->WAV: {e_conv}", exc_info=True)
            await update.message.reply_text("Не удалось обработать голосовое сообщение (ошибка конвертации).")
            return # Exit if conversion fails

        # Process transcription result
        if transcribed_text and not transcribed_text.startswith("Ошибка") and not transcribed_text.startswith("Не удалось распознать"):
            logger.info(f"Voice transcribed from {update.effective_user.id}: \"{transcribed_text[:100]}...\"")
            await handle_generic_message(update, context, transcribed_text)
        elif transcribed_text: # Handle errors like "Не удалось распознать"
            await update.message.reply_text(transcribed_text)
        else: # Handle None case
            await update.message.reply_text("Не удалось распознать речь в голосовом сообщении.")

    except Exception as e:
        logger.error(f"Error handling voice message: {e}", exc_info=True)
        await update.message.reply_text("Произошла ошибка при обработке голосового сообщения.")
    finally:
         # Ensure temp files are deleted if they still exist
         if oga_path and os.path.exists(oga_path): os.remove(oga_path)
         if wav_path and os.path.exists(wav_path): os.remove(wav_path)


async def handle_video_note_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for video notes (circles)."""
    if not update or not update.message or not update.message.video_note: return
    video_note = update.message.video_note
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=constants.ChatAction.TYPING)
    mp4_path, wav_path, transcribed_text = None, None, None
    try:
        file = await video_note.get_file()
        ts = int(time.time() * 1000)
        mp4_path = f"video_{update.effective_user.id}_{ts}.mp4"
        wav_path = f"video_{update.effective_user.id}_{ts}.wav"
        await file.download_to_drive(custom_path=mp4_path)
        logger.info(f"Video note downloaded: {mp4_path}")
        try:
            audio = AudioSegment.from_file(mp4_path, format="mp4")
            audio.export(wav_path, format="wav")
            logger.info(f"Extracted audio to WAV: {wav_path}")
            transcribed_text = await transcribe_voice_async(wav_path)
            wav_path = None
        except Exception as e_conv:
            logger.error(f"Failed to convert/transcribe video note MP4->WAV: {e_conv}", exc_info=True)
            await update.message.reply_text("Не удалось извлечь/обработать аудио из видеосообщения.")
            return

        if transcribed_text and not transcribed_text.startswith("Ошибка") and not transcribed_text.startswith("Не удалось распознать"):
            logger.info(f"Video note transcribed from {update.effective_user.id}: \"{transcribed_text[:100]}...\"")
            await handle_generic_message(update, context, transcribed_text)
        elif transcribed_text:
            await update.message.reply_text(transcribed_text)
        else:
            await update.message.reply_text("Не удалось распознать речь в видеосообщении.")

    except Exception as e:
        logger.error(f"Error handling video note: {e}", exc_info=True)
        await update.message.reply_text("Произошла ошибка при обработке видеосообщения.")
    finally:
        if mp4_path and os.path.exists(mp4_path): os.remove(mp4_path)
        if wav_path and os.path.exists(wav_path): os.remove(wav_path)

# ==============================================================================
# Command Handlers
# ==============================================================================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update.message.reply_text(f"Привет, {user.first_name}! Я - {BOT_NAME}, готов поболтать?")

async def remember_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    history_key = update.effective_chat.id if update.effective_chat.type != 'private' else user.id
    memory = " ".join(context.args).strip() if context.args else None
    if memory:
        add_to_history(history_key, SYSTEM_ROLE, f"Важная информация для запоминания о {user.first_name}: {memory}")
        if user.id not in user_info_db: await update_user_info(update)
        if 'memory' not in user_info_db[user.id]: user_info_db[user.id]['memory'] = []
        user_info_db[user.id]['memory'].append(f"{datetime.now().strftime('%Y-%m-%d')}: {memory}")
        logger.info(f"User {user.id} asked to remember: '{memory}'")
        await update.message.reply_text(f"Хорошо, {user.first_name}, постараюсь запомнить: '{memory}'.")
    else:
        await update.message.reply_text("Что ты хочешь, чтобы я запомнила? Напиши после команды /remember.")

async def clear_my_history_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("Да, очистить мою историю", callback_data=f'clear_my_history_confirm_{user_id}'),
        InlineKeyboardButton("Нет, отмена", callback_data='cancel_clear')
    ]])
    await update.message.reply_text("Ты уверен(а), что хочешь очистить нашу переписку? Я забуду контекст.", reply_markup=keyboard)

async def button_callback(update: Update, context: CallbackContext):
    query = update.callback_query
    if not query: return
    await query.answer()
    data = query.data
    user_id = query.from_user.id

    if data.startswith('clear_my_history_confirm_'):
        target_user_id = int(data.split('_')[-1])
        if user_id == target_user_id:
            history_key_private = user_id
            cleared = False
            if history_key_private in chat_history:
                 del chat_history[history_key_private]
                 cleared = True
                 logger.info(f"User {user_id} cleared their private history.")
            if user_id in user_info_db and 'relationship' in user_info_db[user_id]:
                 user_info_db[user_id]['relationship'] = Relationship()
                 logger.info(f"Relationship reset for user {user_id}.")
                 cleared = True
            await query.edit_message_text("Готово. Наша история очищена.") if cleared else await query.edit_message_text("История и так была пуста.")
        else:
            await query.answer("Это кнопка для другого пользователя.", show_alert=True)
    elif data == 'cancel_clear':
        await query.edit_message_text("Хорошо, отменили.")

async def set_my_name_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    name = " ".join(context.args).strip() if context.args else None
    if name:
        user_preferred_name[user_id] = name
        if user_id not in user_info_db: await update_user_info(update)
        user_info_db[user_id]['preferred_name'] = name
        logger.info(f"User {user_id} set preferred name to '{name}'.")
        await update.message.reply_text(f"Отлично, буду звать тебя {name}!")
    else:
        current_name = user_preferred_name.get(user_id, user.first_name)
        await update.message.reply_text(f"Как мне тебя называть? Сейчас я зову тебя {current_name}. Напиши имя после /setmyname.")

async def my_style_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_name = user_preferred_name.get(user_id, update.effective_user.first_name)
    relationship_obj = await get_user_relationship_obj(user_id)
    rel_dict = relationship_obj.__dict__
    sorted_emotions = sorted(rel_dict.items(), key=lambda item: item[1], reverse=True)
    desc = [f"{emo.capitalize()}: {val:.2f}" for emo, val in sorted_emotions if val > 0.05]
    style_info = f"Мое отношение к тебе ({user_name}):\n" + ("\n".join(desc) if desc else "Нейтральное.")
    # Also show the resulting prompt
    current_prompt = relationship_obj.get_prompt(user_name) # Uses global BOT_NAME
    style_info += f"\n\nТекущий стиль общения со мной:\n'{current_prompt}'"
    await update.message.reply_text(style_info)

@admin_only
async def set_group_user_style_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    target_user = update.message.reply_to_message.from_user if update.message.reply_to_message else None
    if not target_user:
        await update.message.reply_text("Ответь на сообщение пользователя, для которого хочешь установить стиль.")
        return
    style_prompt = " ".join(context.args).strip() if context.args else ""
    user_id = target_user.id
    chat_id = update.effective_chat.id
    key = (chat_id, user_id)

    if not style_prompt: # Empty prompt means reset
        if key in group_user_style_prompts:
            del group_user_style_prompts[key]
            logger.info(f"Admin {update.effective_user.id} reset style for user {user_id} in chat {chat_id}.")
            await update.message.reply_text(f"Стиль общения с {target_user.first_name} в этом чате сброшен.")
        else: await update.message.reply_text(f"Для {target_user.first_name} в этом чате не был установлен особый стиль.")
    else:
        group_user_style_prompts[key] = style_prompt
        logger.info(f"Admin {update.effective_user.id} set style for user {user_id} in chat {chat_id}: '{style_prompt}'")
        await update.message.reply_text(f"Установлен особый стиль общения с {target_user.first_name} в этом чате.")

@admin_only
async def reset_style_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global DEFAULT_STYLE, BOT_NAME, group_user_style_prompts
    initial_settings = BotSettings() # Load defaults from .env
    DEFAULT_STYLE = initial_settings.DEFAULT_STYLE
    BOT_NAME = initial_settings.BOT_NAME
    group_user_style_prompts.clear()
    logger.info(f"Admin {update.effective_user.id} reset bot name to '{BOT_NAME}', default style, and cleared all specific styles.")
    await update.message.reply_text(f"Имя бота сброшено на '{BOT_NAME}'. Стандартный стиль общения восстановлен. Все особые стили для пользователей в группах сброшены.")
    if 'gui' in globals() and gui: # Update GUI if it exists
         gui.update_settings_display()
         gui.master.title(f"{BOT_NAME} GUI")

@admin_only
async def clear_history_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    target_user_id: Optional[int] = None
    if context.args:
        try: target_user_id = int(context.args[0])
        except ValueError: await update.message.reply_text("Укажи корректный ID."); return
    elif update.message.reply_to_message: target_user_id = update.message.reply_to_message.from_user.id
    else: await update.message.reply_text("Укажи ID пользователя или ответь на его сообщение."); return

    if target_user_id:
         cleared = False
         if target_user_id in chat_history: del chat_history[target_user_id]; cleared = True
         if target_user_id in user_info_db and 'relationship' in user_info_db[target_user_id]:
             user_info_db[target_user_id]['relationship'] = Relationship(); cleared = True
         if cleared:
              logger.info(f"Admin {update.effective_user.id} cleared history/relationship for user {target_user_id}.")
              await update.message.reply_text(f"Личная история/отношения для пользователя {target_user_id} очищены.")
         else: await update.message.reply_text(f"История для пользователя {target_user_id} не найдена.")

@admin_only
async def list_admins_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin_list = ", ".join(map(str, ADMIN_USER_IDS)) if ADMIN_USER_IDS else "пуст"
    await update.message.reply_text(f"Список администраторов: {admin_list}")

@admin_only
async def get_log_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        await context.bot.send_document(chat_id=update.effective_chat.id, document=InputFile(LOG_FILENAME))
        logger.info(f"Admin {update.effective_user.id} requested log file: {LOG_FILENAME}")
    except FileNotFoundError: await update.message.reply_text(f"Файл логов '{LOG_FILENAME}' не найден.")
    except Exception as e: logger.error(f"Error sending log file: {e}"); await update.message.reply_text(f"Ошибка при отправке логов: {e}")

@admin_only
async def ban_user_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type == 'private': await update.message.reply_text("Команда доступна только в группах."); return
    user_id_to_ban: Optional[int] = None
    user_info: str = "Неизвестный пользователь"
    if update.message.reply_to_message:
        user_to_ban = update.message.reply_to_message.from_user
        user_id_to_ban = user_to_ban.id
        user_info = f"{user_to_ban.mention_html()} (ID: {user_id_to_ban})"
    elif context.args and context.args[0].isdigit():
        user_id_to_ban = int(context.args[0])
        user_info = f"Пользователь с ID {user_id_to_ban}"
        try: # Try to get more info
             chat_member = await context.bot.get_chat_member(chat_id=update.effective_chat.id, user_id=user_id_to_ban)
             user_info = f"{chat_member.user.mention_html()} (ID: {user_id_to_ban})"
        except Exception: pass # Ignore if user info not found
    else: await update.message.reply_text("Ответьте на сообщение пользователя или укажите его ID."); return

    if user_id_to_ban:
        chat_id = update.effective_chat.id
        try:
            await context.bot.ban_chat_member(chat_id=chat_id, user_id=user_id_to_ban)
            await update.message.reply_text(f"{user_info} забанен в этом чате.", parse_mode=constants.ParseMode.HTML)
            logger.warning(f"Admin {update.effective_user.id} banned user {user_id_to_ban} in chat {chat_id}")
        except telegram.error.BadRequest as e:
             logger.error(f"Failed to ban user {user_id_to_ban} in chat {chat_id}: {e}")
             if "user not found" in str(e): await update.message.reply_text("Не удалось забанить: пользователь не найден.")
             elif "not enough rights" in str(e): await update.message.reply_text("Не удалось забанить: у бота недостаточно прав.")
             else: await update.message.reply_text(f"Не удалось забанить: {e}")
        except Exception as e: logger.error(f"Unexpected error banning user: {e}", exc_info=True); await update.message.reply_text("Ошибка при бане.")

@admin_only
async def delete_message_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type == 'private': await update.message.reply_text("Команда доступна только в группах."); return
    if not update.message.reply_to_message: await update.message.reply_text("Ответьте на сообщение для удаления."); return
    try:
        await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=update.message.reply_to_message.message_id)
        await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=update.message.message_id) # Delete command itself
        logger.info(f"Admin {update.effective_user.id} deleted message {update.message.reply_to_message.message_id} in chat {update.effective_chat.id}")
    except telegram.error.BadRequest as e:
        logger.error(f"Failed to delete message: {e}")
        if "message to delete not found" in str(e): await update.message.reply_text("Не удалось удалить: сообщение не найдено.")
        elif "message can't be deleted" in str(e): await update.message.reply_text("Не удалось удалить: сообщение слишком старое или нет прав.")
        else: await update.message.reply_text(f"Не удалось удалить: {e}")
        try: await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=update.message.message_id) # Try delete command anyway
        except Exception: pass
    except Exception as e: logger.error(f"Unexpected error deleting message: {e}", exc_info=True); await update.message.reply_text("Ошибка при удалении.")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    is_admin = user_id in ADMIN_USER_IDS
    help_text = f"*{escape_markdown(BOT_NAME, version=2)}* \- твой бот\-собеседник\n\n"
    help_text += "🤖 *Основные команды:*\n"
    help_text += "/start \- Начать общение\n"
    help_text += "/help \- Показать это сообщение\n"
    help_text += "/setmyname `<имя>` \- Установить, как я буду к тебе обращаться\n"
    help_text += "/remember `<текст>` \- Попросить меня запомнить что\-то о тебе\n"
    help_text += "/clear\\_my\\_history \- Очистить историю нашего общения\n"
    help_text += "/mystyle \- Узнать мое текущее отношение к тебе\n"
    if is_admin:
        help_text += "\n🛡️ *Админ\-команды:*\n"
        help_text += "/reset\\_style \- Сбросить имя, стиль и все особые стили\n"
        help_text += "/set\\_group\\_style \\(в ответ\\) `<стиль>` \- Стиль для юзера в чате\n"
        help_text += "/clear\\_history `<ID>`\\|\\(в ответ\\) \- Очистить историю/отношения юзера\n"
        help_text += "/list\\_admins \- Показать ID админов\n"
        help_text += "/get\\_log \- Получить файл логов\n"
        help_text += "/ban `<ID>`\\|\\(в ответ\\) \- Забанить юзера в чате\n"
        help_text += "/delete \\(в ответ\\) \- Удалить сообщение\n"
    await update.message.reply_text(help_text, parse_mode=constants.ParseMode.MARKDOWN_V2)


# ==============================================================================
# Error Handler
# ==============================================================================

async def error_handler(update: object, context: CallbackContext):
    """Logs errors and sends messages to admins."""
    logger.error(msg="Exception while handling an update:", exc_info=context.error)
    if isinstance(context.error, telegram.error.NetworkError):
         logger.warning("NetworkError encountered, skipping user/admin notification.")
         return # Don't spam on network issues

    # Get traceback
    tb_list = traceback.format_exception(None, context.error, context.error.__traceback__)
    tb_string = "".join(tb_list)
    update_str = update.to_dict() if isinstance(update, Update) else str(update)
    error_message = (
        f"Exception: {html.escape(str(context.error))}\n"
        f"<pre>Update: {html.escape(json.dumps(update_str, indent=1, ensure_ascii=False, default=str)[:1000])}</pre>\n"
        f"<pre>Traceback:\n{html.escape(tb_string[-2000:])}</pre>" # Limit traceback length
    )

    # Notify user if possible (simple message)
    if isinstance(update, Update) and update.effective_message:
        try: await context.bot.send_message(chat_id=update.effective_chat.id, text="Ой! Произошла внутренняя ошибка. Админы уже уведомлены.")
        except Exception as e_user: logger.error(f"Failed to send error notification to user: {e_user}")

    # Notify admins
    if ADMIN_USER_IDS:
        for admin_id in ADMIN_USER_IDS: # Notify all admins
            try: await context.bot.send_message(chat_id=admin_id, text=error_message[:4096], parse_mode=constants.ParseMode.HTML)
            except Exception as e_admin: logger.error(f"Failed to send error notification to admin {admin_id}: {e_admin}")

# ==============================================================================
# Data Persistence Functions
# ==============================================================================

def load_learned_responses():
    """Loads common data like learned responses, names, styles from JSON."""
    global learned_responses, group_preferences, user_preferred_name, group_user_style_prompts
    logger.info(f"Loading data from {KNOWLEDGE_FILE}...")
    try:
        with open(KNOWLEDGE_FILE, "r", encoding="utf-8") as f: data = json.load(f)
        learned_responses = data.get("learned_responses", {})
        group_preferences = data.get("group_preferences", {})
        user_preferred_name = {int(k): v for k, v in data.get("user_preferred_name", {}).items()} # Keys back to int
        loaded_styles = data.get("group_user_style_prompts_list", [])
        group_user_style_prompts = {(int(item[0]), int(item[1])): item[2] for item in loaded_styles if len(item) == 3}
        logger.info(f"Loaded {len(learned_responses)} learned responses, {len(user_preferred_name)} names, {len(group_user_style_prompts)} styles.")
    except FileNotFoundError: logger.warning(f"{KNOWLEDGE_FILE} not found. Starting fresh."); initialize_data_structures()
    except json.JSONDecodeError as e: logger.error(f"Error decoding {KNOWLEDGE_FILE}: {e}. Starting fresh."); initialize_data_structures()
    except Exception as e: logger.error(f"Failed to load {KNOWLEDGE_FILE}: {e}", exc_info=True); initialize_data_structures()

def save_learned_responses():
    """Saves common data to JSON and user data to separate files."""
    global learned_responses, user_info_db, group_preferences, user_preferred_name, group_user_style_prompts
    logger.debug("Attempting to save data...")
    # --- Save common data ---
    try:
        styles_list = [[str(k[0]), str(k[1]), v] for k, v in group_user_style_prompts.items()]
        data_to_save = {
            "learned_responses": learned_responses,
            "group_preferences": group_preferences,
            "user_preferred_name": {str(k): v for k, v in user_preferred_name.items()},
            "group_user_style_prompts_list": styles_list,
        }
        with open(KNOWLEDGE_FILE, "w", encoding="utf-8") as f: json.dump(data_to_save, f, ensure_ascii=False, indent=2)
        logger.info(f"Saved common data to {KNOWLEDGE_FILE}")
    except Exception as e: logger.error(f"Error saving {KNOWLEDGE_FILE}: {e}", exc_info=True)

    # --- Save user data ---
    os.makedirs(USER_DATA_DIR, exist_ok=True)
    saved_users = 0
    for user_key, user_data in user_info_db.items():
        user_file_path = os.path.join(USER_DATA_DIR, f"user_{user_key}.json")
        try:
            user_data_to_save = user_data.copy()
            if 'relationship' in user_data_to_save and isinstance(user_data_to_save['relationship'], Relationship):
                user_data_to_save['relationship'] = user_data_to_save['relationship'].__dict__
            user_data_to_save = {str(k): v for k, v in user_data_to_save.items()} # Ensure string keys
            with open(user_file_path, "w", encoding="utf-8") as f: json.dump(user_data_to_save, f, ensure_ascii=False, indent=2)
            saved_users += 1
        except Exception as e: logger.error(f"Error saving user data for {user_key}: {e}", exc_info=True)
    if saved_users > 0: logger.info(f"Saved data for {saved_users} users to {USER_DATA_DIR}")

def load_user_data():
     """Loads individual user data files."""
     global user_info_db
     logger.info(f"Loading user data from {USER_DATA_DIR}...")
     user_info_db = {}
     if not os.path.isdir(USER_DATA_DIR): logger.warning(f"User data directory '{USER_DATA_DIR}' not found."); return
     loaded_count = 0
     for filename in os.listdir(USER_DATA_DIR):
          if filename.startswith("user_") and filename.endswith(".json"):
               user_file_path = os.path.join(USER_DATA_DIR, filename)
               try:
                    user_id = int(filename[len("user_"):-len(".json")])
                    with open(user_file_path, "r", encoding="utf-8") as f: user_data_loaded = json.load(f)
                    # Restore Relationship object
                    rel_data = user_data_loaded.get('relationship')
                    if isinstance(rel_data, dict):
                         rel_obj = Relationship(); rel_obj.__dict__.update(rel_data)
                         user_data_loaded['relationship'] = rel_obj
                    else: user_data_loaded['relationship'] = Relationship() # Default if missing/invalid
                    user_info_db[user_id] = user_data_loaded
                    loaded_count +=1
               except ValueError: logger.warning(f"Skipping invalid user data filename: {filename}")
               except json.JSONDecodeError as e: logger.error(f"Error decoding user data {filename}: {e}")
               except Exception as e: logger.error(f"Failed to load user data from {filename}: {e}", exc_info=True)
     logger.info(f"Loaded data for {loaded_count} users.")

def initialize_data_structures():
     """Resets data structures to empty."""
     global learned_responses, group_preferences, user_preferred_name, group_user_style_prompts, user_info_db, chat_history, last_activity
     learned_responses = {}
     group_preferences = {}
     user_preferred_name = {}
     group_user_style_prompts = {}
     user_info_db = {}
     chat_history = {}
     last_activity = {}
     logger.info("Initialized empty data structures.")

# ==============================================================================
# GUI Class
# ==============================================================================

class ChatBotGUI:
    def __init__(self, master):
        self.master = master
        master.title(f"{BOT_NAME} GUI") # Use initial BOT_NAME

        # --- Logging Setup ---
        self.log_queue = log_queue
        self.logger = logger # Use global logger
        self.redirect_logger()

        # --- GUI Structure ---
        self.notebook = ttk.Notebook(master)
        self.main_tab = ttk.Frame(self.notebook)
        self.settings_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.main_tab, text='Управление и Логи')
        self.notebook.add(self.settings_tab, text='Настройки')
        self.notebook.pack(pady=10, padx=10, fill=tk.BOTH, expand=True)

        # --- Create Tabs ---
        self.create_main_tab(self.main_tab)
        self.create_settings_tab(self.settings_tab)

        # --- State Variables ---
        self.application: Optional[Application] = None
        self.bot_thread: Optional[Thread] = None
        self.bot_loop: Optional[asyncio.AbstractEventLoop] = None
        self.running = False

        # --- Load Data & Initialize Settings Display ---
        self.load_settings() # Load learned responses, user data
        self.update_settings_display() # Populate GUI fields

        # --- Window Closing Handler ---
        self.master.protocol("WM_DELETE_WINDOW", self.on_closing)

    def create_main_tab(self, tab):
        """Creates widgets for the main control and log tab."""
        main_frame = ttk.Frame(tab, padding="10")
        main_frame.pack(expand=True, fill=tk.BOTH)

        log_frame = ttk.LabelFrame(main_frame, text="Логи Бота", padding="10")
        log_frame.pack(fill=tk.BOTH, expand=True, pady=5)
        self.log_display = scrolledtext.ScrolledText(log_frame, state=DISABLED, height=20, width=90) # Wider
        self.log_display.pack(fill=tk.BOTH, expand=True)

        button_frame = ttk.Frame(main_frame)
        button_frame.pack(fill=tk.X, pady=(10, 0))
        self.start_button = Button(button_frame, text="Запустить Бота", command=self.start_bot, width=15)
        self.start_button.pack(side=tk.LEFT, padx=5, pady=5)
        self.stop_button = Button(button_frame, text="Остановить Бота", command=self.stop_bot, state=DISABLED, width=15)
        self.stop_button.pack(side=tk.LEFT, padx=5, pady=5)

    def create_settings_tab(self, tab):
        """Creates widgets for the settings tab."""
        settings_frame = ttk.Frame(tab, padding="10")
        settings_frame.pack(expand=True, fill=tk.BOTH)

        api_frame = ttk.LabelFrame(settings_frame, text="API Ключи и Администраторы (.env)", padding="10")
        api_frame.pack(fill=tk.X, pady=5)
        Label(api_frame, text="Telegram Token:").grid(row=0, column=0, padx=5, pady=2, sticky="w")
        self.token_entry = Entry(api_frame, width=60)
        self.token_entry.grid(row=0, column=1, padx=5, pady=2, sticky="ew")
        Label(api_frame, text="Gemini API Key:").grid(row=1, column=0, padx=5, pady=2, sticky="w")
        self.api_key_entry = Entry(api_frame, width=60)
        self.api_key_entry.grid(row=1, column=1, padx=5, pady=2, sticky="ew")
        Label(api_frame, text="Admin IDs (csv):").grid(row=2, column=0, padx=5, pady=2, sticky="w")
        self.admin_ids_entry = Entry(api_frame, width=60)
        self.admin_ids_entry.grid(row=2, column=1, padx=5, pady=2, sticky="ew")
        self.save_env_button = Button(api_frame, text="Сохранить в .env", command=self.save_env_settings)
        self.save_env_button.grid(row=3, column=0, columnspan=2, pady=10)
        api_frame.grid_columnconfigure(1, weight=1)

        persona_frame = ttk.LabelFrame(settings_frame, text="Настройки Персоны (Применяются Сразу)", padding="10")
        persona_frame.pack(fill=tk.BOTH, expand=True, pady=5)
        Label(persona_frame, text="Имя Бота:").grid(row=0, column=0, padx=5, pady=2, sticky="nw")
        self.bot_name_entry = Entry(persona_frame, width=60)
        self.bot_name_entry.grid(row=0, column=1, padx=5, pady=2, sticky="ew")
        Label(persona_frame, text="Стиль Бота (по умолчанию):").grid(row=1, column=0, padx=5, pady=2, sticky="nw")
        self.style_text = Text(persona_frame, width=60, height=10, wrap=tk.WORD)
        self.style_text.grid(row=1, column=1, padx=5, pady=2, sticky="nsew")
        style_scrollbar = ttk.Scrollbar(persona_frame, orient="vertical", command=self.style_text.yview)
        style_scrollbar.grid(row=1, column=2, sticky="ns")
        self.style_text['yscrollcommand'] = style_scrollbar.set
        self.apply_persona_button = Button(persona_frame, text="Применить Имя и Стиль", command=self.apply_bot_settings)
        self.apply_persona_button.grid(row=2, column=0, columnspan=3, pady=10)
        persona_frame.grid_columnconfigure(1, weight=1)
        persona_frame.grid_rowconfigure(1, weight=1)

    def update_settings_display(self):
        """Populates settings fields with current values."""
        self.load_env_settings() # Load API/Admin from .env
        self.bot_name_entry.delete(0, END)
        self.bot_name_entry.insert(0, BOT_NAME) # Use global BOT_NAME
        self.style_text.delete("1.0", END)
        self.style_text.insert("1.0", DEFAULT_STYLE) # Use global DEFAULT_STYLE
        logger.info("GUI settings display updated.")

    def apply_bot_settings(self):
        """Applies Bot Name and Style from GUI to global variables."""
        global BOT_NAME, DEFAULT_STYLE
        new_name = self.bot_name_entry.get().strip()
        new_style = self.style_text.get("1.0", END).strip()
        if not new_name or not new_style:
            messagebox.showwarning("Предупреждение", "Имя и стиль бота не могут быть пустыми.")
            return
        BOT_NAME = new_name
        DEFAULT_STYLE = new_style
        self.master.title(f"{BOT_NAME} GUI") # Update window title
        logger.info(f"Applied new bot settings via GUI: Name='{BOT_NAME}', Style='{DEFAULT_STYLE[:50]}...'")
        messagebox.showinfo("Настройки применены", f"Имя бота: '{BOT_NAME}'. Стандартный стиль обновлен.")

    def load_env_settings(self):
        """Loads settings from .env into API/Admin fields."""
        load_dotenv(dotenv_path=env_path, override=True)
        self.token_entry.delete(0, END); self.token_entry.insert(0, os.getenv('TELEGRAM_BOT_TOKEN', ''))
        self.api_key_entry.delete(0, END); self.api_key_entry.insert(0, os.getenv('GEMINI_API_KEY', ''))
        self.admin_ids_entry.delete(0, END); self.admin_ids_entry.insert(0, os.getenv('ADMIN_IDS', ''))

    def save_env_settings(self):
        """Saves API/Admin settings from GUI fields to .env file."""
        token = self.token_entry.get().strip()
        api_key = self.api_key_entry.get().strip()
        admin_ids = self.admin_ids_entry.get().strip()
        new_env_vars = {'TELEGRAM_BOT_TOKEN': token, 'GEMINI_API_KEY': api_key, 'ADMIN_IDS': admin_ids}
        try:
            try: lines = open(env_path, 'r', encoding='utf-8').readlines()
            except FileNotFoundError: lines = []
            output_lines = []
            keys_to_update = set(new_env_vars.keys())
            # Keep existing non-API/Admin settings
            env_keys_to_keep = {'BOT_NAME', 'DEFAULT_STYLE', 'MAX_HISTORY', 'HISTORY_TTL'}
            for line in lines:
                stripped_line = line.strip()
                if stripped_line and not stripped_line.startswith('#') and '=' in stripped_line:
                    key, _ = stripped_line.split('=', 1); key = key.strip()
                    if key in keys_to_update:
                        output_lines.append(f"{key}={new_env_vars[key]}\n"); keys_to_update.remove(key)
                    elif key in env_keys_to_keep or key not in new_env_vars: output_lines.append(line) # Keep old or unrelated
                else: output_lines.append(line) # Keep comments/empty lines
            for key in keys_to_update: output_lines.append(f"{key}={new_env_vars[key]}\n") # Add new ones if any
            with open(env_path, 'w', encoding='utf-8') as f: f.writelines(output_lines)

            # Reload relevant global variables
            global TOKEN, API_KEY, ADMIN_USER_IDS
            load_dotenv(dotenv_path=env_path, override=True)
            TOKEN = os.getenv('TELEGRAM_BOT_TOKEN'); API_KEY = os.getenv('GEMINI_API_KEY')
            ADMIN_USER_IDS = list(map(int, os.getenv('ADMIN_IDS', '').split(','))) if os.getenv('ADMIN_IDS') else []
            if API_KEY: genai.configure(api_key=API_KEY) # Reconfigure Gemini
            logger.info(".env file updated (API/Admins) and variables reloaded.")
            messagebox.showinfo("Настройки .env", "Настройки API и администраторов сохранены в .env.")
        except Exception as e: logger.error(f"Error saving .env: {e}", exc_info=True); messagebox.showerror("Ошибка", f"Не удалось сохранить .env: {e}")

    def redirect_logger(self):
        """Periodically checks the log queue and updates the GUI log display."""
        while not self.log_queue.empty():
            try:
                record = self.log_queue.get_nowait()
                msg = log_formatter.format(record) # Use the global formatter
                self._update_log_display(msg) # Use helper to update display safely
            except queue.Empty: break
            except Exception as e: print(f"Error redirecting log: {e}")
        self.master.after(150, self.redirect_logger) # Check slightly less often

    def load_settings(self):
        """Loads data from knowledge and user files."""
        load_learned_responses()
        load_user_data()
        logger.info("Learned responses and user data loaded for GUI.")

    def save_settings(self):
        """Saves current data to files."""
        save_learned_responses()
        logger.info("Learned responses and user data saved from GUI.")

    def start_bot(self):
        """Starts the bot in a separate thread."""
        if self.running: messagebox.showinfo("Информация", "Бот уже запущен."); return
        current_token = self.token_entry.get().strip()
        current_api_key = self.api_key_entry.get().strip()
        if not current_token or not current_api_key:
            messagebox.showerror("Ошибка", "Не указан Telegram Token или Gemini API Key!"); return

        global TOKEN, API_KEY, model # Update globals before starting thread
        TOKEN = current_token; API_KEY = current_api_key
        if API_KEY: # Re-initialize model if key changed
             try:
                 genai.configure(api_key=API_KEY)
                 model = genai.GenerativeModel(MODEL_NAME) # Use the chosen model name
                 logger.info(f"Gemini model re-initialized with current API key.")
             except Exception as e:
                 logger.critical(f"Failed to re-initialize Gemini model: {e}", exc_info=True)
                 messagebox.showerror("Ошибка Gemini", f"Не удалось инициализировать модель Gemini с новым ключом: {e}")
                 model = None
                 return # Don't start if model fails
        else: model = None # No API key

        self.running = True
        self.start_button.config(state=DISABLED); self.stop_button.config(state=NORMAL)
        self.bot_thread = Thread(target=self.run_bot, daemon=True)
        self.bot_thread.start()
        logger.info("Bot thread started.")
        self.log_display_message("System", f"Запуск бота '{BOT_NAME}'...")

    def stop_bot(self):
        """Stops the running bot."""
        if not self.running: messagebox.showinfo("Информация", "Бот не запущен."); return
        if not self.application or not self.bot_loop or not self.bot_loop.is_running():
            logger.warning("Stop called but bot application/loop is not ready/running. Resetting GUI state.")
            self.reset_gui_on_stop(); return # Just reset GUI if bot didn't start properly

        logger.info("Stopping bot...")
        self.log_display_message("System", "Остановка бота...")
        shutdown_future, stop_future = None, None

        try:
            # 1. Signal run_polling to stop accepting new updates
            if self.application.running: # Check if it thinks it's running
                 stop_future = asyncio.run_coroutine_threadsafe(self.application.stop(), self.bot_loop)
                 logger.info("Application.stop() called.")
                 stop_future.result(timeout=3) # Wait briefly for stop signal
            # 2. Shutdown application resources
            shutdown_future = asyncio.run_coroutine_threadsafe(self.application.shutdown(), self.bot_loop)
            logger.info("Application.shutdown() called.")
        except Exception as e:
             logger.error(f"Error during stop/shutdown sequence: {e}", exc_info=True)

        # --- Update GUI immediately ---
        self.reset_gui_on_stop() # Resets running flag and button states

        # --- Wait for shutdown completion ---
        if shutdown_future:
            try: shutdown_future.result(timeout=10); logger.info("Application shutdown complete.")
            except asyncio.TimeoutError: logger.warning("Timeout waiting for application shutdown.")
            except Exception as e: logger.error(f"Error waiting for application shutdown: {e}")

        # --- Wait for thread to finish ---
        if self.bot_thread and self.bot_thread.is_alive():
             logger.info("Waiting for bot thread to join...")
             self.bot_thread.join(timeout=5)
             if self.bot_thread.is_alive(): logger.warning("Bot thread did not join after stop.")
             else: logger.info("Bot thread joined.")

        self.save_settings() # Save data after successful stop
        logger.info("Bot stopped and settings saved.")
        self.bot_loop = None; self.application = None; self.bot_thread = None

    def run_bot(self):
        """Main function executed in the bot thread."""
        loop = asyncio.new_event_loop(); asyncio.set_event_loop(loop)
        self.bot_loop = loop
        try:
            current_token = TOKEN; current_api_key = API_KEY
            if not current_token or not current_api_key: raise ValueError("Token or API Key missing!")
            if not model: raise ValueError("Gemini model not initialized!") # Check if model loaded

            self.application = ApplicationBuilder().token(current_token).build()
            self.setup_handlers(self.application)
            self.setup_jobs(self.application)

            logger.info(f"Starting bot polling for '{BOT_NAME}'...")
            self.master.after(0, lambda: self.log_display_message("System", f"Бот '{BOT_NAME}' запущен."))

            # Run polling until stop() or shutdown() is called
            loop.run_until_complete(self.application.run_polling(allowed_updates=Update.ALL_TYPES, stop_signals=None))
            logger.info("Bot polling loop finished.")

        except (ValueError, telegram.error.InvalidToken) as e:
             error_msg = f"Ошибка конфигурации: {e}"; logger.critical(error_msg, exc_info=True)
             self.master.after(0, lambda: messagebox.showerror("Ошибка конфигурации", error_msg))
             self.master.after(0, self.reset_gui_on_stop) # Reset GUI on config error
        except Exception as e:
            error_msg = f"Сбой в работе бота: {type(e).__name__}: {e}"; logger.critical(error_msg, exc_info=True)
            self.master.after(0, lambda: messagebox.showerror("Критическая ошибка", error_msg))
            self.master.after(0, self.reset_gui_on_stop) # Reset GUI on critical error
        finally:
            logger.info("Bot thread loop cleaning up...")
            # Final shutdown attempt if needed
            if self.application and hasattr(self.application, 'running') and self.application.running:
                 logger.warning("Polling finished, but application still marked as running. Forcing shutdown.")
                 try: loop.run_until_complete(self.application.shutdown())
                 except Exception as e_final: logger.error(f"Error during final shutdown: {e_final}")
            # Cleanup loop
            if loop.is_running(): loop.stop()
            try:
                 tasks = asyncio.all_tasks(loop)
                 if tasks: loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))
            except RuntimeError: pass # Ignore if loop already stopped
            except Exception as e_gather: logger.error(f"Error gathering tasks: {e_gather}")
            if not loop.is_closed(): loop.close()
            logger.info("Bot thread event loop closed.")
            self.bot_loop = None; self.application = None # Clear refs
            if self.running: self.master.after(0, self.reset_gui_on_stop) # Ensure GUI reset if flag still true

    def reset_gui_on_stop(self):
         """Resets GUI state when bot stops or fails to start."""
         if not self.master.winfo_exists(): return # Check if window still exists
         if self.running: # Only reset if it thought it was running
             self.running = False
             self.start_button.config(state=NORMAL)
             self.stop_button.config(state=DISABLED)
             self.log_display_message("System", "Бот остановлен.")
             logger.info("GUI state reset.")

    def setup_handlers(self, application: Application):
        """Registers command and message handlers."""
        application.add_handler(CommandHandler("start", start_command))
        application.add_handler(CommandHandler("help", help_command))
        application.add_handler(CommandHandler("remember", remember_command))
        application.add_handler(CommandHandler("clear_my_history", clear_my_history_command))
        application.add_handler(CommandHandler("setmyname", set_my_name_command))
        application.add_handler(CommandHandler("mystyle", my_style_command))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        application.add_handler(MessageHandler(filters.VOICE, handle_voice_message))
        application.add_handler(MessageHandler(filters.VIDEO_NOTE, handle_video_note_message))
        application.add_handler(CallbackQueryHandler(button_callback))
        admin_filter = filters.User(user_id=ADMIN_USER_IDS) if ADMIN_USER_IDS else filters.User(user_id=[]) # Empty filter if no admins
        application.add_handler(CommandHandler("set_group_style", set_group_user_style_command, filters=admin_filter))
        application.add_handler(CommandHandler("reset_style", reset_style_command, filters=admin_filter))
        application.add_handler(CommandHandler("clear_history", clear_history_command, filters=admin_filter))
        application.add_handler(CommandHandler("list_admins", list_admins_command, filters=admin_filter))
        application.add_handler(CommandHandler("get_log", get_log_command, filters=admin_filter))
        application.add_handler(CommandHandler("ban", ban_user_command, filters=admin_filter))
        application.add_handler(CommandHandler("delete", delete_message_command, filters=admin_filter))
        application.add_error_handler(error_handler)
        logger.info("Command and message handlers registered.")

    def setup_jobs(self, application: Application):
        """Registers background jobs."""
        if not application.job_queue: logger.warning("Job queue not available."); return
        application.job_queue.run_repeating(cleanup_history, interval=timedelta(minutes=15), first=timedelta(seconds=10))
        application.job_queue.run_repeating(cleanup_audio_files, interval=timedelta(hours=1), first=timedelta(seconds=60))
        application.job_queue.run_repeating(self.save_settings_job, interval=timedelta(minutes=30), first=timedelta(minutes=5))
        logger.info("Job queue tasks registered.")

    async def save_settings_job(self, context: CallbackContext):
        """Async wrapper for saving settings via job queue."""
        logger.debug("Periodic save triggered by JobQueue.")
        await asyncio.to_thread(self.save_settings)

    def log_display_message(self, role: str, message: str):
         """Safely adds a message to the GUI log display."""
         try: self.master.after(0, self._update_log_display, f"[{role}] {message}")
         except Exception as e: print(f"Error adding log to GUI: {e}") # Fallback print

    def _update_log_display(self, message: str):
         """Updates the log display widget (called via master.after)."""
         if not self.log_display.winfo_exists(): return # Check if widget exists
         try:
              self.log_display.configure(state=NORMAL)
              self.log_display.insert(END, message + '\n')
              self.log_display.configure(state=DISABLED)
              self.log_display.see(END) # Autoscroll
         except tk.TclError: pass # Ignore errors if window is closing
         except Exception as e: logger.error(f"Error updating log display: {e}", exc_info=True)

    def on_closing(self):
        """Handles the GUI window close event."""
        logger.info("GUI window closing requested.")
        if self.running:
            if messagebox.askyesno("Подтверждение", f"Бот '{BOT_NAME}' работает. Остановить и выйти?"):
                self.stop_bot() # Attempt graceful stop
                if self.bot_thread and self.bot_thread.is_alive():
                     self.master.after(100, self.wait_for_thread_and_destroy) # Wait before destroying
                else: self.master.destroy() # Destroy immediately if thread already dead
            else: logger.info("GUI closing cancelled by user.") # Do nothing if user cancels
        else:
            self.save_settings() # Save settings if bot wasn't running
            self.master.destroy() # Close window

    def wait_for_thread_and_destroy(self):
         """Helper to wait for bot thread completion before closing GUI."""
         if self.bot_thread and self.bot_thread.is_alive():
              logger.debug("Waiting for bot thread before destroying window...")
              self.master.after(200, self.wait_for_thread_and_destroy)
         else:
              logger.info("Bot thread finished, destroying window.")
              self.master.destroy()

# ==============================================================================
# Main Execution Block
# ==============================================================================
gui = None # Global variable to hold the GUI instance

if __name__ == "__main__":
    # --- Initial Setup & Checks ---
    logger.info("Application starting...")
    if not TOKEN or not API_KEY:
         logger.critical("CRITICAL: TELEGRAM_BOT_TOKEN or GEMINI_API_KEY is missing!")
         # Show error popup but allow GUI to open for settings input
         root_check = tk.Tk(); root_check.withdraw()
         messagebox.showerror("Ошибка Конфигурации", "TOKEN или API Key не найдены!\nПроверьте .env или укажите их в настройках GUI.")
         root_check.destroy()

    # --- Load initial data ---
    initialize_data_structures() # Start with empty structures
    load_learned_responses()
    load_user_data()

    # --- Start GUI ---
    root = tk.Tk()
    gui = ChatBotGUI(root) # Assign to global variable
    root.mainloop()

    # --- Cleanup after GUI closes ---
    # Ensure bot is stopped if GUI closed unexpectedly while running
    if gui and gui.running:
        logger.warning("GUI closed while bot was running. Attempting final stop...")
        gui.stop_bot() # Try to stop it

    logger.info("Application finished.")