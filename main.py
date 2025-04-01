# main.py
import asyncio
from datetime import timedelta

from telegram import Update
from telegram.ext import (ApplicationBuilder, CallbackContext, CallbackQueryHandler,
                          CommandHandler, ContextTypes, MessageHandler, filters)

# --- Импорт конфигурации ---
from config import (ADMIN_USER_IDS, TELEGRAM_BOT_TOKEN, logger) # Оставляем только необходимое для main

# --- Импорт состояния и функций управления им ---
from state import load_all_data, save_all_data, cleanup_history_job # Импортируем функции работы с данными

# --- Импорт утилит ---
from utils import cleanup_audio_files_job # Импортируем утилиты

# --- Импорт обработчиков и команд ---
from bot_commands import (ban_user_command, button_callback,
                          clear_history_command, clear_my_history_command,
                          error_handler, help_command, remember_command,
                          set_bot_name_command, set_default_style_command,
                          set_my_name_command, start_command)
# ВАЖНО: Теперь main импортирует handlers, а handlers НЕ импортирует main
from handlers import (handle_message, handle_photo, handle_video_note_message,
                    handle_voice_message)


# --- Настройка обработчиков и запуск бота ---

def setup_handlers(application):
    """Регистрирует все обработчики команд и сообщений."""
    # Команды пользователей
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("remember", remember_command))
    application.add_handler(CommandHandler("clear_my_history", clear_my_history_command))
    application.add_handler(CommandHandler("setmyname", set_my_name_command))

    # Обработчики сообщений (импортированные из handlers.py)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    application.add_handler(MessageHandler(filters.VOICE, handle_voice_message))
    application.add_handler(MessageHandler(filters.VIDEO_NOTE, handle_video_note_message))

    # Обработчик колбэков
    application.add_handler(CallbackQueryHandler(button_callback))

    # Административные команды
    admin_filter = filters.User(user_id=ADMIN_USER_IDS) if ADMIN_USER_IDS else filters.User(user_id=[])
    application.add_handler(CommandHandler("clear_history", clear_history_command, filters=admin_filter))
    application.add_handler(CommandHandler("ban", ban_user_command, filters=admin_filter))
    application.add_handler(CommandHandler("set_default_style", set_default_style_command, filters=admin_filter))
    application.add_handler(CommandHandler("set_bot_name", set_bot_name_command, filters=admin_filter))

    # Обработчик ошибок
    application.add_error_handler(error_handler)
    logger.info("All handlers registered.")

async def save_all_data_job_wrapper(context: CallbackContext):
    """Асинхронная обертка для сохранения данных из JobQueue."""
    logger.debug("Periodic save job triggered.")
    # Запускаем синхронную функцию сохранения в отдельном потоке, чтобы не блокировать event loop
    await asyncio.to_thread(save_all_data) # save_all_data импортирована из state.py

def setup_jobs(application):
    """Регистрирует фоновые задачи."""
    jq = application.job_queue
    # Используем импортированные функции _job
    jq.run_repeating(cleanup_history_job, interval=timedelta(minutes=10), first=timedelta(seconds=10), name="cleanup_history")
    jq.run_repeating(cleanup_audio_files_job, interval=timedelta(hours=1), first=timedelta(seconds=60), name="cleanup_audio_files")
    jq.run_repeating(save_all_data_job_wrapper, interval=timedelta(minutes=30), first=timedelta(minutes=5), name="save_all_data")
    logger.info("All jobs registered.")

# --- Точка входа ---
def main():
    """Основная функция запуска бота."""
    logger.info("----- Bot Starting -----")
    try:
        # Загружаем данные перед созданием приложения
        load_all_data() # Импортирована из state.py

        # Создаем приложение
        application = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build() # Токен из config.py

        # Настраиваем обработчики и задачи
        setup_handlers(application)
        setup_jobs(application)

        logger.info("Bot setup complete. Starting polling...")
        application.run_polling(allowed_updates=Update.ALL_TYPES, stop_signals=None)

    except Exception as e:
        logger.critical(f"Critical error during bot startup or execution: {e}", exc_info=True)
    finally:
        logger.info("----- Bot Stopping -----")
        # Сохраняем все данные перед выходом
        save_all_data() # Импортирована из state.py
        logger.info("----- Bot Stopped -----")

if __name__ == "__main__":
    main()