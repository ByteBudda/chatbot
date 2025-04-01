# handlers.py
import asyncio
import os
import random
import re
from io import BytesIO
from collections import deque # Добавили импорт deque
import time
import requests
from PIL import Image
from telegram import Update, constants
from telegram.ext import ContextTypes, CallbackContext

# --- Импорты из других модулей проекта ---
from config import (ASSISTANT_ROLE, BOT_NAME, CONTEXT_CHECK_PROMPT,
                    DEFAULT_STYLE, MAX_HISTORY, SYSTEM_ROLE, USER_ROLE,
                    logger, settings)
# --- Импорт состояния ---
from state import (add_to_history, chat_history, learned_responses,
                   user_preferred_name, user_topic) # Импортируем только нужное состояние
# --- Импорт утилит ---
from utils import (filter_response, generate_content_sync, generate_vision_content_async,
                   is_context_related, model, transcribe_voice, update_user_info,
                   _construct_prompt, _get_effective_style) # Импортируем утилиты

# --- Вспомогательная функция для обработки генерации и ответа ---
async def _process_generation_and_reply(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    history_key: int,
    prompt: str,
    original_input: str
):
    """Генерирует ответ AI, фильтрует, сохраняет и отправляет пользователю."""
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=constants.ChatAction.TYPING)
    await asyncio.sleep(random.uniform(0.4, 1.2))

    # Используем generate_content_sync из utils.py
    response = await asyncio.to_thread(generate_content_sync, prompt)
    logger.info(f"Raw Gemini response for key {history_key}: {response[:100]}...")

    # Используем filter_response из utils.py
    filtered = filter_response(response)
    logger.info(f"Filtered response for key {history_key}: {filtered[:100]}...")

    if filtered and not filtered.startswith("["):
        # Используем add_to_history из state.py
        add_to_history(history_key, ASSISTANT_ROLE, filtered)
        logger.debug(f"Sending response to chat {update.effective_chat.id}")
        await update.message.reply_text(filtered, parse_mode=None)

        if len(original_input.split()) < 10:
            # Используем learned_responses из state.py
            learned_responses[original_input] = filtered
            logger.info(f"Learned response for '{original_input[:50]}...': '{filtered[:50]}...'")
    elif filtered.startswith("["):
         logger.warning(f"Response from Gemini indicates an issue: {filtered}")
         await update.message.reply_text("Извините, не могу сейчас ответить на это. Попробуйте переформулировать.")
         # add_to_history(history_key, SYSTEM_ROLE, f"Gemini Error/Block: {filtered}") # Опционально
    else:
        logger.warning(f"Filtered response was empty for key {history_key}. Original: {response[:100]}...")
        await update.message.reply_text("Простите, у меня возникли сложности с ответом. Попробуйте еще раз или задайте другой вопрос.")


# --- Обработчик текстовых сообщений ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text: return
    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat: return

    user_id = user.id
    chat_id = chat.id
    prompt_text = update.message.text
    chat_type = chat.type

    # Используем утилиты и состояние из utils/state
    await update_user_info(update)
    user_name = user_preferred_name.get(user_id, user.first_name)
    history_key = chat_id if chat_type in ['group', 'supergroup'] else user_id

    try: bot_username = (await context.bot.get_me()).username
    except Exception: bot_username = settings.BOT_NAME

    mentioned = f"@{bot_username}".lower() in prompt_text.lower() or settings.BOT_NAME.lower() in prompt_text.lower()
    is_reply_to_bot = update.message.reply_to_message and update.message.reply_to_message.from_user.id == context.bot.id
    logger.debug(f"Message from {user_id}. Mentioned: {mentioned}, Reply: {is_reply_to_bot}")

    should_check_context = not (mentioned or is_reply_to_bot)
    is_related = await is_context_related(prompt_text, user_id, chat_id, chat_type) if should_check_context else False

    if mentioned or is_reply_to_bot or is_related:
        logger.info(f"Processing message from {user_name} ({user_id}). Reason: M={mentioned}, R={is_reply_to_bot}, C={is_related}")

        effective_style = await _get_effective_style(chat_id, user_id, user_name, chat_type)
        system_message = f"{effective_style} Обращайся к {user_name}. Отвечай от первого лица как {settings.BOT_NAME}."
        topic = user_topic.get(user_id)
        topic_context = f"Тема: {topic}." if topic else ""

        add_to_history(history_key, USER_ROLE, prompt_text, user_name=user_name if chat_type != 'private' else None)
        add_to_history(history_key, SYSTEM_ROLE, f"{system_message} {topic_context}")

        current_history = chat_history.get(history_key, deque())
        user_names_in_chat = None
        if chat_type in ['group', 'supergroup']:
             user_names_in_chat = set(m.group(1) for entry in current_history if (m := re.match(r"User \((.+)\):", entry)))
             if user_name: user_names_in_chat.add(user_name)

        prompt = _construct_prompt(current_history, chat_type, user_names_in_chat)
        await _process_generation_and_reply(update, context, history_key, prompt, prompt_text)
    else:
        logger.info(f"Message from {user_id} ignored: '{prompt_text[:50]}...'")


# --- Обработчик фотографий ---
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.photo: return
    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat: return

    user_id = user.id
    chat_id = chat.id
    chat_type = chat.type

    await update_user_info(update)
    user_name = user_preferred_name.get(user_id, user.first_name)
    history_key = chat_id if chat_type in ['group', 'supergroup'] else user_id

    logger.info(f"Processing photo from {user_name} ({user_id}) in chat {chat_id}")
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=constants.ChatAction.UPLOAD_PHOTO)

    try:
        photo_file = await update.message.photo[-1].get_file()
        file_bytes = await photo_file.download_as_bytearray()
        image = Image.open(BytesIO(file_bytes))
        caption = update.message.caption or ""

        effective_style = await _get_effective_style(chat_id, user_id, user_name, chat_type)
        vision_prompt = f"{effective_style} Я {settings.BOT_NAME}. Обращайся к {user_name}. "
        vision_prompt += f"Фото с подписью: '{caption}'. Опиши и отреагируй." if caption else "Фото без подписи. Опиши и отреагируй."

        contents = [vision_prompt, image] # Список для vision модели

        history_entry = f"Получено фото" + (f" с подписью: '{caption}'" if caption else "")
        add_to_history(history_key, USER_ROLE, history_entry, user_name=user_name if chat_type != 'private' else None)
        add_to_history(history_key, SYSTEM_ROLE, f"{effective_style} Обращайся к {user_name}. Отвечай от первого лица как {settings.BOT_NAME}.")

        logger.debug(f"Sending image/prompt to Gemini Vision for key {history_key}")
        await asyncio.sleep(random.uniform(0.5, 1.5))

        # Используем generate_vision_content_async из utils.py
        response_text = await generate_vision_content_async(contents)

        logger.info(f"Gemini Vision response for key {history_key}: {response_text[:100]}...")
        filtered = filter_response(response_text)
        logger.info(f"Filtered Vision response for key {history_key}: {filtered[:100]}...")

        if filtered and not filtered.startswith("["):
            add_to_history(history_key, ASSISTANT_ROLE, filtered)
            await update.message.reply_text(filtered, parse_mode=None)
        elif filtered.startswith("["):
            await update.message.reply_text("Не удалось обработать изображение.")
        else:
            await update.message.reply_text("Не могу ничего сказать об этом изображении.")

    except Exception as e:
        logger.error(f"Error handling photo for user {user_id}: {e}", exc_info=True)
        await update.message.reply_text("Произошла ошибка при обработке фото.")


# --- Обработчик голосовых сообщений ---
async def handle_voice_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.voice: return
    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat: return

    user_id = user.id
    chat_id = chat.id
    voice = update.message.voice
    chat_type = chat.type

    await update_user_info(update)
    user_name = user_preferred_name.get(user_id, user.first_name)
    history_key = chat_id if chat_type in ['group', 'supergroup'] else user_id

    logger.info(f"Processing voice message from {user_name} ({user_id})")
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=constants.ChatAction.RECORD_VOICE)

    original_file_path = None
    wav_path = None
    try:
        voice_file = await voice.get_file()
        original_file_path = f"voice_{user_id}_{int(time.time())}.oga"
        await voice_file.download_to_drive(original_file_path)

        try:
            audio = AudioSegment.from_file(original_file_path, format="ogg")
            wav_path = original_file_path.rsplit('.', 1)[0] + ".wav"
            audio.export(wav_path, format="wav")
            file_to_transcribe = wav_path
        except Exception as e:
            logger.error(f"Error converting voice to WAV: {e}. Check ffmpeg.", exc_info=True)
            # НЕ ПЫТАЕМСЯ распознать OGG напрямую, т.к. transcribe_voice ожидает WAV
            await update.message.reply_text("Ошибка конвертации аудио. Убедитесь, что ffmpeg доступен.")
            if original_file_path and os.path.exists(original_file_path): os.remove(original_file_path)
            return # Выходим, если конвертация не удалась

        # Используем transcribe_voice из utils.py (он сам удалит wav_path)
        transcribed_text = await transcribe_voice(file_to_transcribe)

        # Удаляем OGG файл
        if original_file_path and os.path.exists(original_file_path):
             try: os.remove(original_file_path)
             except OSError: pass

        if transcribed_text and not transcribed_text.startswith("["):
            logger.info(f"Transcription result: '{transcribed_text}'")
            await update.message.reply_text(f"Вы сказали: \"{transcribed_text}\"", quote=True)

            # --- Логика ответа (аналогично handle_message) ---
            try: bot_username = (await context.bot.get_me()).username
            except Exception: bot_username = settings.BOT_NAME

            mentioned = f"@{bot_username}".lower() in transcribed_text.lower() or settings.BOT_NAME.lower() in transcribed_text.lower()
            is_reply_to_bot = update.message.reply_to_message and update.message.reply_to_message.from_user.id == context.bot.id
            should_check_context = not (mentioned or is_reply_to_bot)
            is_related = await is_context_related(transcribed_text, user_id, chat_id, chat_type) if should_check_context else False

            if mentioned or is_reply_to_bot or is_related:
                logger.info(f"Processing transcribed text. Reason: M={mentioned}, R={is_reply_to_bot}, C={is_related}")
                effective_style = await _get_effective_style(chat_id, user_id, user_name, chat_type)
                system_message = f"{effective_style} Обращайся к {user_name}. Отвечай как {settings.BOT_NAME}."
                topic = user_topic.get(user_id)
                topic_context = f"Тема: {topic}." if topic else ""
                add_to_history(history_key, USER_ROLE, transcribed_text, user_name=user_name if chat_type != 'private' else None)
                add_to_history(history_key, SYSTEM_ROLE, f"{system_message} {topic_context}")
                current_history = chat_history.get(history_key, deque())
                user_names_in_chat = None
                if chat_type in ['group', 'supergroup']:
                     user_names_in_chat = set(m.group(1) for entry in current_history if (m := re.match(r"User \((.+)\):", entry)))
                     if user_name: user_names_in_chat.add(user_name)
                prompt = _construct_prompt(current_history, chat_type, user_names_in_chat)
                await _process_generation_and_reply(update, context, history_key, prompt, transcribed_text)
            else:
                 logger.info(f"Transcribed text ignored: '{transcribed_text[:50]}...'")
        elif transcribed_text and transcribed_text.startswith("["):
             logger.warning(f"Transcription failed: {transcribed_text}")
             await update.message.reply_text(f"Не удалось распознать речь. {transcribed_text}")
        else:
            logger.warning("Transcription returned empty or None")
            await update.message.reply_text("Не удалось обработать голосовое сообщение.")

    except Exception as e:
        logger.error(f"Error handling voice message: {e}", exc_info=True)
        await update.message.reply_text("Произошла ошибка при обработке голосового сообщения.")
    finally:
         # Доп очистка
        if wav_path and os.path.exists(wav_path):
             try: os.remove(wav_path)
             except OSError: pass
        if original_file_path and os.path.exists(original_file_path):
             try: os.remove(original_file_path)
             except OSError: pass


# --- Обработчик видеосообщений ("кружочков") ---
async def handle_video_note_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.video_note: return
    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat: return

    user_id = user.id
    chat_id = chat.id
    video_note = update.message.video_note
    chat_type = chat.type

    await update_user_info(update)
    user_name = user_preferred_name.get(user_id, user.first_name)
    history_key = chat_id if chat_type in ['group', 'supergroup'] else user_id

    logger.info(f"Processing video note from {user_name} ({user_id})")
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=constants.ChatAction.RECORD_VIDEO_NOTE)

    original_file_path = None
    wav_path = None
    try:
        video_note_file = await video_note.get_file()
        # Видео заметки обычно MP4
        original_file_path = f"video_note_{user_id}_{int(time.time())}.mp4"
        await video_note_file.download_to_drive(original_file_path)

        try:
            audio = AudioSegment.from_file(original_file_path) # Указываем format, если нужно
            wav_path = original_file_path.rsplit('.', 1)[0] + ".wav"
            audio.export(wav_path, format="wav")
            file_to_transcribe = wav_path
        except Exception as e:
            logger.error(f"Error extracting/converting audio from video note: {e}. Check ffmpeg.", exc_info=True)
            await update.message.reply_text("Ошибка извлечения аудио из видео. Убедитесь, что ffmpeg доступен.")
            if original_file_path and os.path.exists(original_file_path): os.remove(original_file_path)
            return

        transcribed_text = await transcribe_voice(file_to_transcribe) # Удалит wav

        if original_file_path and os.path.exists(original_file_path): # Удаляем mp4
             try: os.remove(original_file_path)
             except OSError: pass

        # --- Логика ответа (аналогично handle_voice_message) ---
        if transcribed_text and not transcribed_text.startswith("["):
            logger.info(f"Transcription result (video): '{transcribed_text}'")
            await update.message.reply_text(f"Вы сказали (видео): \"{transcribed_text}\"", quote=True)

            try: bot_username = (await context.bot.get_me()).username
            except Exception: bot_username = settings.BOT_NAME

            mentioned = f"@{bot_username}".lower() in transcribed_text.lower() or settings.BOT_NAME.lower() in transcribed_text.lower()
            is_reply_to_bot = update.message.reply_to_message and update.message.reply_to_message.from_user.id == context.bot.id
            should_check_context = not (mentioned or is_reply_to_bot)
            is_related = await is_context_related(transcribed_text, user_id, chat_id, chat_type) if should_check_context else False

            if mentioned or is_reply_to_bot or is_related:
                logger.info(f"Processing transcribed text (video). Reason: M={mentioned}, R={is_reply_to_bot}, C={is_related}")
                effective_style = await _get_effective_style(chat_id, user_id, user_name, chat_type)
                system_message = f"{effective_style} Обращайся к {user_name}. Отвечай как {settings.BOT_NAME}."
                topic = user_topic.get(user_id)
                topic_context = f"Тема: {topic}." if topic else ""
                add_to_history(history_key, USER_ROLE, transcribed_text + " (видео)", user_name=user_name if chat_type != 'private' else None)
                add_to_history(history_key, SYSTEM_ROLE, f"{system_message} {topic_context}")
                current_history = chat_history.get(history_key, deque())
                user_names_in_chat = None
                if chat_type in ['group', 'supergroup']:
                     user_names_in_chat = set(m.group(1) for entry in current_history if (m := re.match(r"User \((.+)\):", entry)))
                     if user_name: user_names_in_chat.add(user_name)
                prompt = _construct_prompt(current_history, chat_type, user_names_in_chat)
                await _process_generation_and_reply(update, context, history_key, prompt, transcribed_text)
            else:
                 logger.info(f"Transcribed text (video) ignored: '{transcribed_text[:50]}...'")
        elif transcribed_text and transcribed_text.startswith("["):
             logger.warning(f"Transcription failed (video): {transcribed_text}")
             await update.message.reply_text(f"Не удалось распознать речь в видео. {transcribed_text}")
        else:
             logger.warning("Transcription returned empty or None (video)")
             await update.message.reply_text("Не удалось обработать видеосообщение.")

    except Exception as e:
        logger.error(f"Error handling video note: {e}", exc_info=True)
        await update.message.reply_text("Произошла ошибка при обработке видеосообщения.")
    finally:
        # Доп очистка
        if wav_path and os.path.exists(wav_path):
             try: os.remove(wav_path)
             except OSError: pass
        if original_file_path and os.path.exists(original_file_path):
             try: os.remove(original_file_path)
             except OSError: pass