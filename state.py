import os
import json
import time
from collections import deque
from typing import Dict, Deque, Optional, Tuple

# Импортируем нужные константы и логгер из config
from config import (logger, KNOWLEDGE_FILE, USER_DATA_DIR, MAX_HISTORY,
                    USER_ROLE, ASSISTANT_ROLE, SYSTEM_ROLE, settings, HISTORY_TTL) # Добавили HISTORY_TTL

# --- Глобальное состояние бота ---
user_preferred_name: Dict[int, str] = {}
user_topic: Dict[int, str] = {}
learned_responses: Dict[str, str] = {}
user_info_db: Dict[int, Dict[str, any]] = {}
group_preferences: Dict[int, Dict[str, str]] = {}
chat_history: Dict[int, Deque[str]] = {}
last_activity: Dict[int, float] = {}
feedback_data: Dict[int, Dict] = {} # Пока не используется
group_user_style_prompts: Dict[Tuple[int, int], str] = {} # Пока не используется
bot_activity_percentage: int = 100 # Добавляем процент активности бота

# --- Функции управления состоянием ---

def add_to_history(key: int, role: str, message: str, user_name: Optional[str] = None):
    """Добавляет сообщение в историю чата и обновляет время активности."""
    if key not in chat_history:
        chat_history[key] = deque(maxlen=MAX_HISTORY)
    entry = f"{role} ({user_name}): {message}" if role == USER_ROLE and user_name else f"{role}: {message}"
    chat_history[key].append(entry)
    last_activity[key] = time.time()
    logger.debug(f"History added for key {key}. Role: {role}. Message start: {message[:50]}...")

async def cleanup_history_job(context): # Переименовано для ясности, что это job callback
    """Периодическая задача для удаления старой истории чатов."""
    # context тут не используется, но нужен для callback JobQueue
    current_time = time.time()
    keys_to_delete = [key for key, ts in last_activity.items() if current_time - ts > HISTORY_TTL]
    deleted_count = 0
    for key in keys_to_delete:
        if key in chat_history:
            del chat_history[key]
            deleted_count +=1
        if key in last_activity:
            del last_activity[key]
        # Можно удалять user_topic и т.д.
        if key in user_topic: del user_topic[key]

    if deleted_count > 0:
        logger.info(f"Cleaned up history for {deleted_count} inactive chats/users.")
    else:
        logger.debug("History cleanup: No inactive chats found.")

def load_all_data():
    """Загружает общее состояние и данные пользователей при старте."""
    global learned_responses, group_preferences, user_info_db, chat_history, settings, user_preferred_name, last_activity, user_topic, bot_activity_percentage
    logger.info(f"Loading data from {KNOWLEDGE_FILE} and {USER_DATA_DIR}...")

    # --- Загрузка общих данных ---
    if os.path.exists(KNOWLEDGE_FILE):
        try:
            with open(KNOWLEDGE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                learned_responses = data.get("learned_responses", {})
                group_preferences = data.get("group_preferences", {})
                bot_settings_data = data.get("bot_settings")
                if bot_settings_data:
                    settings.MAX_HISTORY = bot_settings_data.get('MAX_HISTORY', settings.MAX_HISTORY)
                    settings.DEFAULT_STYLE = bot_settings_data.get('DEFAULT_STYLE', settings.DEFAULT_STYLE)
                    settings.BOT_NAME = bot_settings_data.get('BOT_NAME', settings.BOT_NAME)
                    settings.HISTORY_TTL = bot_settings_data.get('HISTORY_TTL', settings.HISTORY_TTL)
                    # Обновляем константы в config после загрузки
                    import config
                    config.MAX_HISTORY = settings.MAX_HISTORY
                    config.DEFAULT_STYLE = settings.DEFAULT_STYLE
                    config.BOT_NAME = settings.BOT_NAME
                    config.HISTORY_TTL = settings.HISTORY_TTL
                    logger.info("Bot settings loaded and applied from knowledge file.")
                bot_activity_percentage = data.get("bot_activity_percentage", 100) # Загрузка процента активности
        except Exception as e:
             logger.error(f"Error loading {KNOWLEDGE_FILE}: {e}", exc_info=True)
    else:
        logger.warning(f"{KNOWLEDGE_FILE} not found.")

    # --- Загрузка данных пользователей ---
    os.makedirs(USER_DATA_DIR, exist_ok=True)
    loaded_user_count = 0
    for filename in os.listdir(USER_DATA_DIR):
        if filename.startswith("user_") and filename.endswith(".json"):
            user_id_str = filename[len("user_"):-len(".json")]
            if user_id_str.isdigit():
                user_id = int(user_id_str)
                user_file_path = os.path.join(USER_DATA_DIR, filename)
                try:
                    with open(user_file_path, "r", encoding="utf-8") as f:
                        user_data = json.load(f)
                        user_info_db[user_id] = user_data.get("info", {"preferences": {}})
                        loaded_history = user_data.get('chat_history', [])
                        if loaded_history:
                            chat_history[user_id] = deque(loaded_history, maxlen=MAX_HISTORY)
                        pref_name = user_data.get('preferred_name')
                        if pref_name: user_preferred_name[user_id] = pref_name
                        last_act = user_data.get('last_activity')
                        if last_act: last_activity[user_id] = last_act
                        topic = user_data.get('topic')
                        if topic: user_topic[user_id] = topic
                        loaded_user_count += 1
                except Exception as e:
                    logger.error(f"Error loading user data file {filename}: {e}", exc_info=True)
            else:
                 logger.warning(f"Skipping file with non-integer user ID: {filename}")
    logger.info(f"Data loading complete. Loaded {loaded_user_count} user data files.")


def save_user_data(user_id):
    """Сохраняет данные конкретного пользователя в его файл."""
    user_data_dir = os.path.join(".", USER_DATA_DIR)
    os.makedirs(user_data_dir, exist_ok=True)
    user_filename = f"user_{user_id}.json"
    user_file_path = os.path.join(user_data_dir, user_filename)

    data_to_save = {
        "info": user_info_db.get(user_id, {}),
        "chat_history": list(chat_history.get(user_id, [])),
        "preferred_name": user_preferred_name.get(user_id),
        "last_activity": last_activity.get(user_id),
        "topic": user_topic.get(user_id)
    }
    data_to_save = {k: v for k, v in data_to_save.items() if v is not None}

    try:
        with open(user_file_path, "w", encoding="utf-8") as f:
            json.dump(data_to_save, f, ensure_ascii=False, indent=4)
        logger.debug(f"User data saved for user_id: {user_id}")
    except Exception as e:
        logger.error(f"Error saving user data for {user_id} to {user_filename}: {e}", exc_info=True)


def save_all_data():
    """Сохраняет все данные (общие и пользовательские) при остановке бота."""
    logger.info("Saving all data...")

    # --- Сохранение общих данных ---
    knowledge_data = {
        "learned_responses": learned_responses,
        "group_preferences": group_preferences,
        "bot_settings": {
            "MAX_HISTORY": settings.MAX_HISTORY,
            "DEFAULT_STYLE": settings.DEFAULT_STYLE,
            "BOT_NAME": settings.BOT_NAME,
            "HISTORY_TTL": settings.HISTORY_TTL,
        },
        "bot_activity_percentage": bot_activity_percentage, # Сохраняем процент активности
    }
    try:
        with open(KNOWLEDGE_FILE, "w", encoding="utf-8") as f:
            json.dump(knowledge_data, f, ensure_ascii=False, indent=4)
        logger.info(f"Knowledge file saved: {KNOWLEDGE_FILE}")
    except Exception as e:
         logger.error(f"Error saving knowledge file {KNOWLEDGE_FILE}: {e}", exc_info=True)

    # --- Сохранение данных всех активных пользователей ---
    saved_user_count = 0
    active_user_ids = list(user_info_db.keys()) # Копируем ключи, чтобы избежать ошибок при изменении словаря
    for user_id in active_user_ids:
        save_user_data(user_id)
        saved_user_count += 1

    logger.info(f"All data saving complete. Saved data for {saved_user_count} users.")
