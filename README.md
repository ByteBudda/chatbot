

 `README.md`:

```markdown
# MashaBotProject - Telegram чат-бот на Google Gemini

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

Это многофункциональный Telegram-бот по имени Маша, работающий на Python с использованием библиотеки `python-telegram-bot` и мультимодальной языковой модели **Google Gemini**. Маша обладает настраиваемой личностью, обрабатывает текстовые, голосовые и видеосообщения, **анализирует и реагирует на содержание фотографий** и может проявлять инициативу в групповых чатах.

## ✨ Возможности

*   **Обработка текстовых сообщений:** Понимание и ответы на текстовые сообщения в личных и групповых чатах с учетом контекста диалога.
*   **Личность "Маша":** Бот отвечает от лица 25-летней девушки Маши из Ростова Великого, используя характерный стиль общения (настраиваемый).
*   **Распознавание речи:** Транскрибация голосовых сообщений и видео-"кружочков" с последующей обработкой текста. (Требует `ffmpeg`).
*   **Анализ изображений (Gemini Vision):** Бот может **описывать содержание фотографий**, присланных пользователями, и реагировать на них и их подписи благодаря мультимодальным возможностям Gemini.
*   **Проактивные ответы в группах:** Бот может по своему усмотрению вступать в разговор в групповых чатах, если к нему напрямую не обратились (вероятность настраивается).
    *   Оценка необходимости ответа с помощью AI.
    *   Опциональный анализ настроения/темы чата для адаптации ответа.
    *   Настройка активности бота в групповых чатах
*   **Управление историей:** Ограничение длины истории, автоматическая очистка старой истории.
*   **Настройка:** Возможность смены имени и стиля общения бота администратором.
*   **Команды пользователей:** `/start`, `/help`, `/setmyname`, `/remember` (временное запоминание), `/clear_my_history` (только ЛС), /set_activity (чтобы уменьшить или увеличить активность бота в чате)
*   **Команды администраторов:** Управление стилем, банами, очисткой истории, получение логов, настройка вероятности проактивных ответов и др.
*   **Персистентность:** Сохранение состояния (история, настройки, предпочтения пользователей) в JSON-файлы.
*   **Модульная структура:** Код разбит на логические модули (`config`, `state`, `utils`, `handlers`, `bot_commands`, `bot_setup`, `main`).

## 🔧 Установка и Запуск

**Требования:**

*   Python 3.10+
*   `pip` (менеджер пакетов Python)
*   `ffmpeg` (для обработки аудио/видео) - см. ниже
*   Git (для клонирования)

**1. Клонирование репозитория:**

```bash
git clone https://github.com/ByteBudda/chatbot
cd chatbot
```

**2. Создание и активация виртуального окружения (Рекомендуется):**

```bash
# Создать окружение
python -m venv venv

# Активировать:
# Windows (bash/git bash)
source venv/Scripts/activate
# Windows (cmd.exe)
venv\Scripts\activate.bat
# Windows (PowerShell)
venv\Scripts\Activate.ps1
# macOS / Linux
source venv/bin/activate
```

**3. Установка зависимостей:**

Сначала создайте файл `requirements.txt`:

```bash
pip freeze > requirements.txt
```

Затем установите зависимости:

```bash
pip install -r requirements.txt
```
*(Если файла `requirements.txt` еще нет, установите библиотеки вручную и потом создайте файл):*
```bash
pip install python-telegram-bot==21.0.1 python-dotenv google-generativeai Pillow pydub SpeechRecognition requests vaderSentiment # Укажите правильную версию PTB
pip freeze > requirements.txt
```

**4. Установка `ffmpeg`:**

`ffmpeg` необходим библиотеке `pydub` для конвертации аудиоформатов.

*   **Debian/Ubuntu:** `sudo apt update && sudo apt install ffmpeg`
*   **Fedora:** `sudo dnf install ffmpeg`
*   **macOS (Homebrew):** `brew install ffmpeg`
*   **Windows:** Скачайте с [ffmpeg.org](https://ffmpeg.org/download.html), распакуйте архив и **добавьте путь к папке `bin` в системную переменную PATH**. Перезапустите терминал.

**5. Конфигурация:**

*   Переименуйте файл `.env.example` (если вы его создадите) в `.env` или создайте файл `.env` вручную в корневой директории проекта.
*   Заполните файл `.env` вашими данными:

```dotenv
# Обязательно
TELEGRAM_BOT_TOKEN="ВАШТОКЕН" # Ваш токен Telegram бота
GEMINI_API_KEY="ВАШ_API_КЛЮЧ_GEMINI_AI"                # Ваш API ключ Google Gemini AI
ADMIN_IDS=32423425,34234234                   # ID администраторов через запятую (без пробелов)

# Опционально (переопределяют значения по умолчанию в config.py)
# MAX_HISTORY=50
# DEFAULT_STYLE="Ты - строгий дворецкий Альфред."
# BOT_NAME="Альфред"
# HISTORY_TTL=3600 # Время жизни истории чата в секундах (1 час)
# GLOBAL_PROACTIVE_PROBABILITY=0.1 # Шанс проактивного ответа (от 0.0 до 1.0)
```
*   **Получение ключа Gemini API:** Ключ можно получить бесплатно в [Google AI Studio](https://aistudio.google.com/app/apikey).

**6. Запуск бота (Локально):**

```bash
python main.py
```

**7. Запуск бота как сервиса (systemd в Linux):**

*   Создайте файл сервиса: `sudo nano /etc/systemd/system/chatbot.service`
*   Вставьте и адаптируйте содержимое (замените `<...>`):

```ini
[Unit]
Description=Masha Telegram Bot (Google Gemini) # Изменено описание
After=network.target

[Service]
# Рекомендуется создать отдельного пользователя:
# sudo useradd -m -r -s /bin/false chatbotuser
# sudo chown -R chatbotuser:chatbotuser /полный/путь/к/папке/с/ботом
User=<имя_пользователя_linux> # Например: chatbotuser
Group=<имя_группы_linux>     # Например: chatbotuser

# Абсолютный путь к папке с main.py и .env
WorkingDirectory=</полный/путь/к/папке/с/ботом>

# Абсолютный путь к Python в venv и к main.py
ExecStart=</полный/путь/к/папке/с/ботом/venv/bin/python3> <полный/путь/к/папке/с/ботом/main.py>

Restart=on-failure
RestartSec=5s

StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

*   Активируйте и запустите сервис:
    ```bash
    sudo systemctl daemon-reload
    sudo systemctl enable chatbot.service
    sudo systemctl start chatbot.service
    ```
*   Проверка статуса и логов:
    ```bash
    sudo systemctl status chatbot.service
    sudo journalctl -u chatbot.service -f # Следить за логами
    ```

## 🚀 Использование

### Основные команды

*   `/start`: Начать диалог с ботом.
*   `/help`: Показать список доступных команд.
*   `/setmyname <имя>`: Установить, как бот будет к вам обращаться.
*   `/remember <текст>`: Попросить бота временно запомнить информацию.
*   `/clear_my_history`: (Только в ЛС) Очистить вашу историю общения с ботом.

### Команды в группах (через текст)

*   `Маша, замолчи`: Бот перестанет инициировать диалоги с вами в этом чате.
*   `Маша, начни говорить`: Бот снова сможет проявлять инициативу по отношению к вам.

*(Замените "Маша" на актуальное имя бота, если оно было изменено)*

### Команды администраторов

*(Доступны только пользователям, ID которых указаны в `ADMIN_IDS` в `.env`)*

*   `/set_group_style` (в ответ на сообщение) `<стиль>`: Установить стиль общения для конкретного пользователя в этом групповом чате.
*   `/clear_history <user_id>`: Очистить историю чата для указанного пользователя (по ID) в текущем чате (в группах работает некорректно!).
*   `/ban (@никнейм | ID | ответ)`: Забанить пользователя в текущем чате.
*   `/delete` (в ответ на сообщение): Удалить сообщение, на которое вы ответили.
*   `/set_proactive_probability <0.0-1.0>`: Установить вероятность проактивного ответа бота в текущем чате.
*   `/set_default_style <новый стиль>`: Установить новый глобальный стиль общения бота.
*   `/set_bot_name <новое имя>`: Установить новое глобальное имя для бота.
*   `/get_log`: Получить файл логов `bot.log`.
*   `/list_admins`: Показать список ID администраторов.

## 🤝 Вклад (Contributing)

Предложения и пул-реквесты приветствуются! Если вы нашли ошибку или хотите предложить улучшение, пожалуйста, создайте Issue.

## 📄 Лицензия

Этот проект распространяется под лицензией MIT. Смотрите файл `LICENSE` для подробностей.
(Не забудьте добавить файл `LICENSE` с текстом лицензии MIT в ваш репозиторий).

## 💡 Заметки

*   Для корректной работы распознавания голоса и видео требуется установленный `ffmpeg`.
*   **Анализ содержания изображений выполняется с помощью Google Gemini.**
*   Логика проактивных ответов может потребовать дополнительной настройки и тюнинга промптов.
*   Рекомендуется регулярно проверять логи (`bot.log` или `journalctl`) для отслеживания работы и ошибок.
*   Не забудьте добавить файл `.gitignore`, чтобы исключить из репозитория `.env`, `venv/`, `__pycache__/`, `*.pyc`, `bot.log`, `user_data/`, `learned_knowledge.json` и другие ненужные файлы.

```

Есть возможность переделать под другие ии (пробовал на мистраль, но он не придерживается промптов)
