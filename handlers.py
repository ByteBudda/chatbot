# handlers.py
import asyncio
import os
import random
from io import BytesIO
from collections import deque
import time
from PIL import Image
from telegram import Update, constants
from telegram.ext import ContextTypes
import pydub
from pydub import AudioSegment

from config import (ASSISTANT_ROLE, BOT_NAME, DEFAULT_STYLE, SYSTEM_ROLE, USER_ROLE,
                    logger, settings)
from state import (add_to_history, chat_history, learned_responses,
                   user_preferred_name, user_topic)
from utils import (filter_response, generate_content_sync, generate_vision_content_async,
                   is_context_related, transcribe_voice, update_user_info,
                   _get_effective_style, should_process_message,
                   get_bot_activity_percentage, get_ner_pipeline,
                   get_sentiment_pipeline, PromptBuilder)

prompt_builder = PromptBuilder(settings.BOT_NAME, DEFAULT_STYLE)

async def _process_generation_and_reply(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    history_key: int,
    prompt: str,
    original_input: str
):
    chat_id = update.effective_chat.id
    chat_type = update.effective_chat.type

    await context.bot.send_chat_action(chat_id=chat_id, action=constants.ChatAction.TYPING)
    await asyncio.sleep(random.uniform(0.4, 1.2))

    response = await asyncio.to_thread(generate_content_sync, prompt)
    logger.info(f"Raw Gemini response for key {history_key}: {response[:100]}...")

    filtered = filter_response(response)
    logger.info(f"Filtered response for key {history_key}: {filtered[:100]}...")

    if filtered and not filtered.startswith("["):
        add_to_history(history_key, ASSISTANT_ROLE, filtered)
        logger.debug(f"Sending response to chat {chat_id}")
        if chat_type == 'private':
            await context.bot.send_message(chat_id=chat_id, text=filtered, parse_mode=None)
        else:
            await update.message.reply_text(filtered, parse_mode=None)

        if len(original_input.split()) < 10:
            learned_responses[original_input] = filtered
            logger.info(f"Learned response for '{original_input[:50]}...': '{filtered[:50]}...'")
    elif filtered.startswith("["):
         logger.warning(f"Response from Gemini indicates an issue: {filtered}")
         await update.message.reply_text("Извините, не могу сейчас ответить на это. Попробуйте переформулировать.")
    else:
        logger.warning(f"Filtered response was empty for key {history_key}. Original: {response[:100]}...")
        await update.message.reply_text("Простите, у меня возникли сложности с ответом. Попробуйте еще раз или задайте другой вопрос.")


async def handle_text_voice_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat: return

    user_id = user.id
    chat_id = chat.id
    chat_type = chat.type
    effective_message = None
    prompt_text = None
    message_type = "text"

    if update.message and update.message.text:
        prompt_text = update.message.text
        effective_message = update.message
        message_type = "text"
    elif update.message and update.message.voice:
        message_type = "voice"
        effective_message = update.message
        voice = update.message.voice
        file_to_transcribe = None
        original_file_path = None
        wav_path = None
        try:
            await context.bot.send_chat_action(chat_id=chat_id, action=constants.ChatAction.RECORD_AUDIO)
            voice_file = await voice.get_file()
            original_file_path = f"voice_{user_id}_{int(time.time())}.oga"
            await voice_file.download_to_drive(original_file_path)

            try:
                audio = AudioSegment.from_file(original_file_path, format="ogg")
                wav_path = original_file_path.rsplit('.', 1)[0] + ".wav"
                audio.export(wav_path, format="wav")
                file_to_transcribe = wav_path
                logger.debug(f"Converted {original_file_path} to {wav_path}")
            except Exception as e:
                logger.error(f"Error converting voice to WAV: {e}. Check if ffmpeg is installed and in PATH.", exc_info=True)
                if original_file_path and os.path.exists(original_file_path): os.remove(original_file_path)
                await update.message.reply_text("Произошла ошибка при обработке голосового сообщения (конвертация).")
                return

            transcribed_text = await transcribe_voice(file_to_transcribe)

            if original_file_path and os.path.exists(original_file_path):
                try:
                    os.remove(original_file_path)
                    logger.debug(f"Removed temporary OGG file: {original_file_path}")
                except OSError as e:
                    logger.warning(f"Could not remove temporary OGG file {original_file_path}: {e}")
            if wav_path and os.path.exists(wav_path):
                try:
                    os.remove(wav_path)
                    logger.debug(f"Removed temporary WAV file: {wav_path}")
                except OSError as e:
                    logger.warning(f"Could not remove temporary WAV file {wav_path}: {e}")

            if transcribed_text and not transcribed_text.startswith("["):
                prompt_text = transcribed_text + " (голосовое сообщение)"
            elif transcribed_text and transcribed_text.startswith("["):
                logger.warning(f"Transcription failed: {transcribed_text}")
                await update.message.reply_text(f"Не удалось распознать речь. {transcribed_text}")
                return
            else:
                logger.warning("Transcription returned empty or None")
                await update.message.reply_text("Не удалось обработать голосовое сообщение (речь не распознана).")
                return

        except Exception as e:
            logger.error(f"Error handling voice message: {e}", exc_info=True)
            await update.message.reply_text("Произошла ошибка при обработке голосового сообщения.")
            return

    elif update.message and update.message.video_note:
        message_type = "video_note"
        effective_message = update.message
        video_note = update.message.video_note
        file_to_transcribe = None
        original_file_path = None
        wav_path = None
        try:
            await context.bot.send_chat_action(chat_id=chat_id, action=constants.ChatAction.RECORD_VIDEO_NOTE)
            video_note_file = await video_note.get_file()
            original_file_path = f"video_note_{user_id}_{int(time.time())}.mp4"
            await video_note_file.download_to_drive(original_file_path)

            try:
                audio = AudioSegment.from_file(original_file_path)
                wav_path = original_file_path.rsplit('.', 1)[0] + ".wav"
                audio.export(wav_path, format="wav")
                file_to_transcribe = wav_path
            except Exception as e:
                logger.error(f"Error extracting/converting audio from video note: {e}. Check ffmpeg.", exc_info=True)
                await update.message.reply_text("Ошибка извлечения аудио из видео. Убедитесь, что ffmpeg доступен.")
                if original_file_path and os.path.exists(original_file_path): os.remove(original_file_path)
                return

            transcribed_text = await transcribe_voice(file_to_transcribe)

            if original_file_path and os.path.exists(original_file_path):
                try: os.remove(original_file_path)
                except OSError: pass
            if wav_path and os.path.exists(wav_path):
                try: os.remove(wav_path)
                except OSError: pass

            if transcribed_text and not transcribed_text.startswith("["):
                prompt_text = transcribed_text + " (видеосообщение)"
            elif transcribed_text and transcribed_text.startswith("["):
                logger.warning(f"Transcription failed (video): {transcribed_text}")
                await update.message.reply_text(f"Не удалось распознать речь в видео. {transcribed_text}")
                return
            else:
                logger.warning("Transcription returned empty or None (video)")
                await update.message.reply_text("Не удалось обработать видеосообщение.")
                return

        except Exception as e:
            logger.error(f"Error handling video note: {e}", exc_info=True)
            await update.message.reply_text("Произошла ошибка при обработке видеосообщения.")
            return

    if not prompt_text:
        logger.debug("No text to process in the update.")
        return

    await update_user_info(update)
    user_name = user_preferred_name.get(user_id, user.first_name)
    history_key = chat_id if chat_type in ['group', 'supergroup'] else user_id

    ner_model = get_ner_pipeline()
    entities = ner_model(prompt_text) if ner_model else None
    logger.info(f"RuBERT Entities: {entities}")

    sentiment_model = get_sentiment_pipeline()
    sentiment_result = sentiment_model(prompt_text) if sentiment_model else None
    sentiment = sentiment_result[0] if sentiment_result else None
    logger.info(f"RuBERT Sentiment: {sentiment}")

    if chat_type == 'private':
        logger.info(f"Processing {message_type} message from {user_name} ({user_id}).")
        effective_style = await _get_effective_style(chat_id, user_id, user_name, chat_type)
        system_message_base = f"{effective_style} Ты - {settings.BOT_NAME}. Не начинай свои ответы с приветствия, если пользователь не поприветствовал тебя в своем сообщении."
        topic = user_topic.get(user_id)
        topic_context = f"Тема: {topic}." if topic else ""

        add_to_history(history_key, USER_ROLE, prompt_text, user_name=user_name if chat_type != 'private' else None)
        add_to_history(history_key, SYSTEM_ROLE, f"{system_message_base} {topic_context}")

        current_history = chat_history.get(history_key, deque())
        history_str = "\n".join(current_history)

        prompt = prompt_builder.build_prompt(
            history_str=history_str,
            user_name=user_name,
            prompt_text=prompt_text,
            system_message_base=system_message_base,
            topic_context=topic_context,
            entities=entities,
            sentiment=sentiment
        )

        await _process_generation_and_reply(update, context, history_key, prompt, prompt_text)
    else:
        if not should_process_message(get_bot_activity_percentage()):
            logger.debug(f"Message from {user_id} skipped due to reduced bot activity.")
            return

        try: bot_username = (await context.bot.get_me()).username
        except Exception: bot_username = settings.BOT_NAME

        mentioned = f"@{bot_username}".lower() in prompt_text.lower() or settings.BOT_NAME.lower() in prompt_text.lower()
        is_reply_to_bot = effective_message.reply_to_message and effective_message.reply_to_message.from_user.id == context.bot.id
        logger.debug(f"Message from {user_id}. Mentioned: {mentioned}, Reply: {is_reply_to_bot}")

        should_check_context = not (mentioned or is_reply_to_bot)
        is_related = await is_context_related(prompt_text, user_id, chat_id, chat_type) if should_check_context else False

        if mentioned or is_reply_to_bot or is_related:
            logger.info(f"Processing {message_type} message from {user_name} ({user_id}). Reason: M={mentioned}, R={is_reply_to_bot}, C={is_related}")

            effective_style = await _get_effective_style(chat_id, user_id, user_name, chat_type)
            system_message_base = f"{effective_style} Обращайся к {user_name}. Отвечай от первого лица как {settings.BOT_NAME}. Не начинай свои ответы с приветствия, если пользователь не поприветствовал тебя в своем сообщении."
            topic = user_topic.get(user_id)
            topic_context = f"Тема: {topic}." if topic else ""

            add_to_history(history_key, USER_ROLE, prompt_text, user_name=user_name if chat_type != 'private' else None)
            add_to_history(history_key, SYSTEM_ROLE, f"{system_message_base} {topic_context}")

            current_history = chat_history.get(history_key, deque())
            history_str = "\n".join(current_history)

            prompt = prompt_builder.build_prompt(
                history_str=history_str,
                user_name=user_name,
                prompt_text=prompt_text,
                system_message_base=system_message_base,
                topic_context=topic_context,
                entities=entities,
                sentiment=sentiment
            )

            await _process_generation_and_reply(update, context, history_key, prompt, prompt_text)
        else:
            logger.info(f"{message_type.capitalize()} message from {user_id} ignored: '{prompt_text[:50]}...'")


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat: return

    user_id = user.id
    chat_id = chat.id
    chat_type = chat.type
    caption = update.message.caption or ""

    if chat_type != 'private' and not should_process_message(get_bot_activity_percentage()):
        logger.debug(f"Photo from {user_id} skipped due to reduced bot activity.")
        return

    await update_user_info(update)
    user_name = user_preferred_name.get(user_id, user.first_name)
    history_key = chat_id if chat_type in ['group', 'supergroup'] else user_id

    logger.info(f"Processing photo from {user_name} ({user_id}) in chat {chat_id}")
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=constants.ChatAction.UPLOAD_PHOTO)

    try:
        photo_file = await update.message.photo[-1].get_file()
        file_bytes = await photo_file.download_as_bytearray()
        image = Image.open(BytesIO(file_bytes))

        effective_style = await _get_effective_style(chat_id, user_id, user_name, chat_type)
        system_message_base = f"{effective_style} Обращайся к {user_name}. Отвечай от первого лица как {settings.BOT_NAME}. Не начинай свои ответы с приветствия, если пользователь не поприветствовал тебя в своем сообщении."
        topic = user_topic.get(user_id)
        topic_context = f"Тема: {topic}." if topic else ""

        add_to_history(history_key, USER_ROLE, f"Получено фото от {user_name}" + (f" с подписью: '{caption}'" if caption else ""), user_name=user_name if chat_type != 'private' else None)
        add_to_history(history_key, SYSTEM_ROLE, f"{system_message_base} {topic_context}")

        current_history = chat_history.get(history_key, deque())
        history_str = "\n".join(current_history)

        ner_model = get_ner_pipeline()
        entities = ner_model(caption) if ner_model else None
        logger.info(f"RuBERT Entities (photo caption): {entities}")

        sentiment_model = get_sentiment_pipeline()
        sentiment_result = sentiment_model(caption) if sentiment_model else None
        sentiment = sentiment_result[0] if sentiment_result else None
        logger.info(f"RuBERT Sentiment (photo caption): {sentiment}")

        prompt = prompt_builder.build_prompt(
            history_str=history_str,
            user_name=user_name,
            prompt_text=caption,
            system_message_base=system_message_base,
            topic_context=topic_context,
            entities=entities,
            sentiment=sentiment
        )

        vision_prompt = f"Сопроводи это изображение ответом, основанным на следующем запросе: {prompt}"
        contents = [vision_prompt, image]

        logger.debug(f"Sending image/prompt to Gemini Vision for key {history_key}")
        await asyncio.sleep(random.uniform(0.5, 1.5))

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

handle_message = handle_text_voice_video
handle_voice_message = handle_text_voice_video
handle_video_note_message = handle_text_voice_video
