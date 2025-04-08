from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, InputFile
from telegram.ext import ContextTypes, CallbackContext, CommandHandler, CallbackQueryHandler, filters
import logging
import os
from functools import wraps
from typing import Any
from dotenv import load_dotenv
from config import BOT_NAME, DEFAULT_STYLE, SYSTEM_ROLE, USER_ROLE, ASSISTANT_ROLE, MAX_HISTORY, HISTORY_TTL
from collections import deque
from state import add_to_history, chat_history, last_activity, user_preferred_name, group_user_style_prompts, bot_activity_percentage
from config import logger, ADMIN_USER_IDS, settings # Импортируем настройки из config

# bot_commands.py
# Этот файл содержит команды и обработчики для бота Telegram, включая команды для администраторов и пользователей.
# Он также включает обработку ошибок и управление историей чата.
# --- Глобальные переменные ---
logger = logging.getLogger(__name__)
chat_history = {}
last_activity = {}
user_preferred_name = {}
group_user_style_prompts = {}
ADMIN_USER_IDS = list(map(int, os.getenv('ADMIN_IDS', '').split(','))) if os.getenv('ADMIN_IDS') else []
# Декораторы
# Декоратор для проверки прав администратора
def admin_only(func):
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update or not update.effective_user or update.effective_user.id not in ADMIN_USER_IDS:
            if update and update.message:
                await update.message.reply_text("🚫 Недостаточно прав!")
            return
        return await func(update, context)
    return wrapper
#
# --- Команды бота ---
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update.message.reply_text(
        f"Привет, {user.first_name}! Я - {BOT_NAME}, давай поболтаем?"
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
                if user_id in last_activity:
                    del last_activity[user_id]
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
    style_info = f"Мой текущий стиль общения: {settings.DEFAULT_STYLE}."
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

# --- Административные команды ---
async def set_group_user_style_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.reply_to_message or not context.args:
        await update.message.reply_text("Пожалуйста, ответьте на сообщение пользователя и укажите стиль после команды.")
        return
    user_id = update.message.reply_to_message.from_user.id
    chat_id = update.effective_chat.id
    style_prompt = " ".join(context.args)
    group_user_style_prompts[(chat_id, user_id)] = style_prompt
    await update.message.reply_text(f"Установлен стиль общения для пользователя {update.message.reply_to_message.from_user.first_name}: {style_prompt}")

async def reset_style_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global DEFAULT_STYLE
    DEFAULT_STYLE = "дружелюбный"
    await update.message.reply_text(f"Глобальный стиль общения бота сброшен на стандартный: {settings.DEFAULT_STYLE}")

async def clear_history_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.args:
        try:
            user_id_to_clear = int(context.args[0])
            if user_id_to_clear in chat_history:
                del chat_history[user_id_to_clear]
                await update.message.reply_text(f"История чата для пользователя {user_id_to_clear} очищена.")
            else:
                await update.message.reply_text(f"История чата для пользователя {user_id_to_clear} не найдена.")
        except ValueError:
            await update.message.reply_text("Пожалуйста, укажите корректный ID пользователя.")
    else:
        await update.message.reply_text("Пожалуйста, укажите ID пользователя для очистки истории.")

async def list_admins_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin_list = ", ".join(map(str, ADMIN_USER_IDS))
    await update.message.reply_text(f"Список администраторов бота: {admin_list}")

async def get_log_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        await context.bot.send_document(chat_id=update.effective_chat.id, document=InputFile("bot.log"))
    except FileNotFoundError:
        await update.message.reply_text("Файл логов не найден.")
    except Exception as e:
        await update.message.reply_text(f"Произошла ошибка при отправке логов: {e}")

async def ban_user_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Реализация команды бана пользователя
    pass

async def set_default_style_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.args:
        new_style = " ".join(context.args)
        settings.update_default_style(new_style)
        await update.message.reply_text(f"Глобальный стиль общения бота установлен на:\n{settings.DEFAULT_STYLE}")
    else:
        await update.message.reply_text("Пожалуйста, укажите новый стиль общения.")

async def set_bot_name_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.args:
        new_name = " ".join(context.args)
        settings.update_bot_name(new_name)
        await update.message.reply_text(f"Имя бота установлено на: {settings.BOT_NAME}")
    else:
        await update.message.reply_text("Пожалуйста, укажите новое имя для бота.")

@admin_only
async def set_activity_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Устанавливает процент активности бота."""
    if context.args:
        try:
            percentage = int(context.args[0])
            if 0 <= percentage <= 100:
                from state import bot_activity_percentage
                bot_activity_percentage = percentage
                await update.message.reply_text(f"Активность бота установлена на {percentage}%")
                logger.info(f"Bot activity set to {percentage}% by admin {update.effective_user.id}")
            else:
                await update.message.reply_text("Процент активности должен быть в диапазоне от 0 до 100.")
        except ValueError:
            await update.message.reply_text("Пожалуйста, введите числовое значение процента.")
    else:
        await update.message.reply_text("Пожалуйста, укажите процент активности (0-100).")

async def reset_context_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    chat_type = update.effective_chat.type
    history_key = chat_id if chat_type in ['group', 'supergroup'] else user_id

    if history_key in chat_history:
        chat_history[history_key] = deque(maxlen=MAX_HISTORY) # Очищаем историю
        await update.message.reply_text("Контекст разговора сброшен. Можем начать заново.")
    else:
        await update.message.reply_text("Контекст разговора и так был пуст.")

# --- Команда помощи ---
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = "Доступные команды:\n\n"
    help_text += "/start - Начать общение.\n"
    help_text += "/help - Показать список доступных команд.\n"
    help_text += "/clear_my_history - Очистить вашу историю чата.\n"
    help_text += "/setmyname <имя> - Установить имя, по которому я буду к вам обращаться.\n"
    help_text += "/reset_context - Сбросить контекст разговора.\n" # Добавлена команда

    admin_commands = [
        ("/clear_history <user_id>", "Очистить историю чата для указанного пользователя (по ID)."),
        ("/list_admins", "Показать список администраторов бота."),
        ("/get_log", "Получить файл логов бота."),
        ("/ban (@никнейм | ответ)", "Забанить пользователя."),
        ("/set_bot_name <новое имя>", "Установить новое имя для бота."),
        ("/set_default_style <стиль>", "Установить глобальный стиль общения бота."),
        ("/set_group_user_style <стиль>", "Установить стиль общения для конкретного пользователя в группе."),
        ("/reset_style", "Сбросить глобальный стиль общения бота на стандартный."),
        ("/set_activity <0-100>", "Установить процент активности бота.")
    ]

    user_id = update.effective_user.id
    if user_id in ADMIN_USER_IDS:
        help_text += "\nАдминистративные команды:\n"
        for command, description in admin_commands:
            help_text += f"{command} - {description}\n"
    else:
        help_text += "\nДля получения списка административных команд обратитесь к администратору.\n"

    await update.message.reply_text(help_text)
