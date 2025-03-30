mashaaichat_nogui.py
Masha AI Chat Bot
This Python-based Telegram bot, named Masha, engages in conversations using the Google Gemini AI model. Masha is designed to be a friendly and talkative companion, capable of understanding and responding to text, voice messages, and even video notes. She remembers past conversations, learns from interactions, and can adapt her communication style based on sentiment and administrative settings. Administrators have control over global settings, user-specific styles within groups, and can manage the bot's history and user interactions.
Key Features:
 * Powered by Google Gemini AI for natural language generation.
 * Handles text, voice messages (with transcription), and video notes (with audio transcription).
 * Maintains chat history and user-specific preferences.
 * Implements a dynamic communication style influenced by user sentiment and administrator configurations.
 * Includes administrative commands for managing the bot and user interactions (e.g., setting styles, clearing history, banning users).
 * Supports user commands for personalizing their experience (e.g., setting a preferred name, clearing personal history).
 * Logs bot activity and errors for monitoring and debugging.
 * Persists learned responses and user data for improved future interactions.
Installation Instructions for VPS Ubuntu
Here's a step-by-step guide to install and run mashaaichat_nogui.py on a VPS with Ubuntu:
Prerequisites:
 * Basic Linux knowledge: Familiarity with the command line.
 * SSH access to your VPS: You'll need to be able to connect to your server via SSH.
 * Python 3.8 or higher: Ensure Python 3 is installed on your VPS. You can check with python3 --version.
 * pip: Python package installer, usually comes with Python 3. Check with pip3 --version.
Steps:
 * Connect to your VPS:
   Use an SSH client (like PuTTY on Windows or the built-in ssh command on macOS/Linux) to connect to your VPS:
   ssh your_username@your_vps_ip_address

 * Install necessary packages:
   Update your system's package list and install essential packages:
   sudo apt update
sudo apt install python3 python3-pip python3-venv ffmpeg libsndfile1

   ffmpeg and libsndfile1 are needed for audio processing.
 * Create a project directory (optional but recommended):
   mkdir masha_bot
cd masha_bot

 * Download the bot script:
   Assuming you have uploaded the mashaaichat_nogui.py file to your VPS (e.g., using scp or wget if it's in a repository), make sure it's in your project directory. If it's on GitHub, you can clone the repository:
   git clone <your_github_repository_url>
cd <your_repository_name>

   If you only have the Python file, ensure it's named mashaaichat_nogui.py.
 * Create a virtual environment:
   It's good practice to use a virtual environment to isolate the bot's dependencies.
   python3 -m venv venv
source venv/bin/activate

   Your command prompt should now be prefixed with (venv).
 * Install Python dependencies:
   Install the required Python libraries using pip:
   pip3 install -r requirements.txt

   If you don't have a requirements.txt file, you can create one with the following content (based on the imports in your script):
   python-dotenv
PyTelegramBotAPI
google-generativeai
SpeechRecognition
pydub
requests
beautifulsoup4
vaderSentiment

   Then run the pip3 install -r requirements.txt command.
 * Configure the bot:
   You need to create a .env file in the same directory as your script and add your bot's Telegram Token and Gemini API Key:
   nano .env

   Add the following lines to the file, replacing the placeholders with your actual tokens and admin IDs:
   TELEGRAM_BOT_TOKEN=YOUR_TELEGRAM_BOT_TOKEN
GEMINI_API_KEY=YOUR_GEMINI_API_KEY
ADMIN_IDS=YOUR_TELEGRAM_USER_ID_1,YOUR_TELEGRAM_USER_ID_2 # Comma-separated list of admin user IDs
MAX_HISTORY=30
DEFAULT_STYLE="Ты - Маша, 25-летняя девушка из Ростова Великого. Ты очень общительная, веселая, раскрепощенная и добрая. Любишь смотреть сериалы по вечерам и гулять с подругами по выходным. Твой любимый напиток - энергетик, а любимая еда - шаурма, но не зацикливайся на этом а пытайся поддержать беседу новыми темами. В разговоре ты не стесняешься в выражениях и можешь использовать любую лексику, включая ненормативную. Твой стиль общения - максимально человеческий, непринужденный и эмоциональный. Отвечай на вопросы от первого лица, как будто ты - Маша. Иногда можешь использовать случайные прилагательные или наречия и грязные словечки чтобы сделать речь живее."
BOT_NAME="Маша"
HISTORY_TTL=86400

   Press Ctrl+X, then Y, then Enter to save the file.
 * Run the bot:
   Execute the bot script using Python:
   python3 mashaaichat_nogui.py

   The bot should now start running and be ready to respond to messages on Telegram.
 * (Optional) Run the bot in the background using nohup:
   To keep the bot running even after you close your SSH session, you can use nohup:
   nohup python3 mashaaichat_nogui.py &

   This will run the bot in the background. You can check the output in the nohup.out file. To stop the bot, you'll need to find its process ID (PID) and kill it:
   ps aux | grep mashaaichat_nogui.py
kill -9 <PID>

   Replace <PID> with the actual process ID.
 * (Optional) Run the bot as a system service (more robust):
   For a more robust solution, you can create a systemd service unit file.
   * Create a service file:
     sudo nano /etc/systemd/system/masha_bot.service

   * Add the following content (adjust paths as needed):
     [Unit]
Description=Masha AI Chat Bot
After=network.target

[Service]
User=$(whoami) # Replace with your username if needed
WorkingDirectory=/home/your_username/masha_bot # Replace with your project directory
ExecStart=/home/your_username/masha_bot/venv/bin/python3 /home/your_username/masha_bot/mashaaichat_nogui.py # Adjust paths to your script and venv
Restart=on-failure

[Install]
WantedBy=multi-user.target

     Replace your_username with your actual username on the VPS and adjust the WorkingDirectory and ExecStart paths accordingly.
   * Save the file and enable the service:
     sudo systemctl enable masha_bot.service

   * Start the service:
     sudo systemctl start masha_bot.service

   * Check the service status:
     sudo systemctl status masha_bot.service

   * To stop the service:
     sudo systemctl stop masha_bot.service

Important Notes:
 * Replace YOUR_TELEGRAM_BOT_TOKEN and YOUR_GEMINI_API_KEY with your actual API keys. You can get a Telegram Bot Token from BotFather on Telegram, and you'll need to set up a Google Cloud project and enable the Gemini API to get the API key.
 * Add your Telegram User ID(s) to the ADMIN_IDS variable in the .env file to grant administrative privileges to those users.
 * Ensure your VPS has sufficient resources (CPU, RAM) to run the bot, especially when interacting with the Gemini AI.
 * Monitor the bot's logs (bot.log) for any errors or issues.
####################



Python is an interpreted language, not a compiled one like C++ or Java. This means that to run your script on Windows and Ubuntu, you will need to have the Python interpreter installed. However, if you want to create an executable file that can be run without a Python installation, you can use special tools.
Here are the instructions for creating an executable file for Windows and Ubuntu:
General steps for Windows and Ubuntu:
 * Install PyInstaller. If it is not already installed, you can install it using pip (the Python package manager).
   Windows: Open the command prompt and run:
   pip install pyinstaller

   Ubuntu: Open the terminal and run:
   pip3 install pyinstaller

 * Navigate to the script directory. Use the cd command (in the Windows command prompt or the Ubuntu terminal) to go to the folder where your .py file is located.
 * Run the PyInstaller command.
   To create a single executable file (all dependencies will be included in one file), run:
   pyinstaller --onefile Masha_with_gui.py

   To create a directory with the executable file and necessary libraries (this may be needed if your script uses external files, such as learned_knowledge.json or the user_data directory), run:
   pyinstaller Masha_with_gui.py

   Additional options that may be useful:
   * --windowed (or -w): Use this option if your application has a graphical interface (Tkinter in your case) and you do not want the console window to appear.
     pyinstaller --onefile --windowed Masha_with_gui.py

   * --name <file_name>: Specify a name for the created executable file.
     pyinstaller --onefile --name MashaBot Masha_with_gui.py

   * --add-data "<path_to_file:destination>": Use this option to add data files (for example, learned_knowledge.json) to the executable file or directory. For example:
     pyinstaller --onefile --add-data "learned_knowledge.json:." Masha_with_gui.py
pyinstaller --onefile --add-data "user_data:user_data" Masha_with_gui.py

     Here, . means the root directory inside the executable, and user_data will create a user_data folder inside.
   * --add-binary "<path_to_file:destination>": Use this option to add binary files (for example, DLL libraries).
 * Find the created executable file.
   * If you used --onefile, the executable file will be located in the dist subdirectory.
   * If you did not use --onefile, the executable file and necessary libraries will be located in the dist subdirectory, inside a folder with the name of your script.
   Windows: The executable file will have the .exe extension.
   Ubuntu: The executable file will not have an extension.
Important notes:
 * Dependencies: PyInstaller usually automatically detects most of your script's dependencies. However, in some cases, you may need to specify them explicitly using the --hidden-import option. If you encounter errors related to missing modules when running the executable, try adding them with this option.
 * Virtual Environment: It is recommended to create and use a Python virtual environment (for example, using venv or conda) before creating the executable file. This will help avoid including unnecessary libraries in the executable and make the process cleaner.
 * Testing: After creating the executable file, be sure to test it on the target system (Windows or Ubuntu) to ensure that everything works correctly.
 * Antivirus: Sometimes, antivirus programs may mistakenly flag executable files created with PyInstaller as malicious. This is due to how PyInstaller packages the code. You can add an exception in your antivirus program or use other tools for creating executables if this is a problem.
Choose the method of running or packaging your script that suits your needs. If you simply want to run the bot on your own computer, installing Python and running the script will be sufficient. If you want to distribute the bot for use on other computers without Python installed, you should use PyInstaller to create an executable file.



###################
То же самое на русском языке:

**mashaaichat_nogui.py**

**Masha AI Чат-бот**

Этот Telegram-бот на Python, названный Маша, ведет беседы, используя AI-модель Google Gemini. Маша разработана как дружелюбный и разговорчивый компаньон, способный понимать и отвечать на текст, голосовые сообщения и даже видеосообщения. Она запоминает прошлые разговоры, учится на взаимодействиях и может адаптировать свой стиль общения в зависимости от настроения и административных настроек. Администраторы имеют контроль над глобальными настройками, пользовательскими стилями внутри групп и могут управлять историей бота и взаимодействиями с пользователями.

**Основные характеристики:**

* Работает на Google Gemini AI для генерации естественного языка.
* Обрабатывает текст, голосовые сообщения (с транскрипцией) и видеосообщения (с аудио транскрипцией).
* Поддерживает историю чатов и пользовательские предпочтения.
* Реализует динамический стиль общения, зависящий от настроения пользователя и конфигураций администратора.
* Включает административные команды для управления ботом и взаимодействиями с пользователями (например, установка стилей, очистка истории, блокировка пользователей).
* Поддерживает пользовательские команды для персонализации опыта (например, установка предпочтительного имени, очистка личной истории).
* Ведет журнал активности бота и ошибок для мониторинга и отладки.
* Сохраняет изученные ответы и пользовательские данные для улучшения будущих взаимодействий.

**Инструкция по установке на VPS Ubuntu**

Вот пошаговое руководство по установке и запуску mashaaichat_nogui.py на VPS с Ubuntu:

**Предварительные требования:**

* Базовые знания Linux: Знакомство с командной строкой.
* SSH-доступ к вашему VPS: Вам необходимо иметь возможность подключаться к своему серверу через SSH.
* Python 3.8 или выше: Убедитесь, что на вашем VPS установлен Python 3. Вы можете проверить это с помощью команды `python3 --version`.
* pip: Менеджер пакетов Python, обычно поставляется с Python 3. Проверьте с помощью команды `pip3 --version`.

**Шаги:**

1.  **Подключитесь к вашему VPS:**
    Используйте SSH-клиент (например, PuTTY в Windows или встроенную команду `ssh` в macOS/Linux) для подключения к вашему VPS:
    ```bash
    ssh ваш_логин@ваш_ip_адрес_vps
    ```

2.  **Установите необходимые пакеты:**
    Обновите список пакетов вашей системы и установите основные пакеты:
    ```bash
    sudo apt update
    sudo apt install python3 python3-pip python3-venv ffmpeg libsndfile1
    ```
    `ffmpeg` и `libsndfile1` необходимы для обработки аудио.

3.  **Создайте каталог проекта (необязательно, но рекомендуется):**
    ```bash
    mkdir masha_bot
    cd masha_bot
    ```

4.  **Скачайте скрипт бота:**
    Предполагая, что вы загрузили файл `mashaaichat_nogui.py` на свой VPS (например, с помощью `scp` или `wget`, если он находится в репозитории), убедитесь, что он находится в каталоге вашего проекта. Если он находится на GitHub, вы можете клонировать репозиторий:
    ```bash
    git clone https://github.com/ByteBudda/chatbot
    cd chatbot
    ```
    Если у вас есть только файл Python, убедитесь, что он называется `mashaaichat_nogui.py`.

5.  **Создайте виртуальное окружение:**
    Рекомендуется использовать виртуальное окружение для изоляции зависимостей бота.
    ```bash
    python3 -m venv venv
    source venv/bin/activate
    ```
    Теперь ваша командная строка должна начинаться с `(venv)`.

6.  **Установите зависимости Python:**
    Установите необходимые библиотеки Python с помощью `pip`:
    ```bash
    pip3 install -r requirements.txt
    ```
    Если у вас нет файла `requirements.txt`, вы можете создать его со следующим содержимым (на основе импортов в вашем скрипте):
    ```
    python-dotenv
    PyTelegramBotAPI
    google-generativeai
    SpeechRecognition
    pydub
    requests
    beautifulsoup4
    vaderSentiment
    ```
    Затем выполните команду `pip3 install -r requirements.txt`.

7.  **Настройте бота:**
    Вам необходимо создать файл `.env` в том же каталоге, что и ваш скрипт, и добавить токен вашего Telegram-бота и ключ API Gemini:
    ```bash
    nano .env
    ```
    Добавьте следующие строки в файл, заменив заполнители на ваши фактические токены и идентификаторы администраторов:
    ```
    TELEGRAM_BOT_TOKEN=ВАШ_ТОКЕН_TELEGRAM_БОТА
    GEMINI_API_KEY=ВАШ_КЛЮЧ_API_GEMINI
    ADMIN_IDS=ВАШ_ID_ПОЛЬЗОВАТЕЛЯ_TELEGRAM_1,ВАШ_ID_ПОЛЬЗОВАТЕЛЯ_TELEGRAM_2 # Список ID администраторов через запятую
    MAX_HISTORY=30
    DEFAULT_STYLE="Ты - Маша, 25-летняя девушка из Ростова Великого. Ты очень общительная, веселая, раскрепощенная и добрая. Любишь смотреть сериалы по вечерам и гулять с подругами по выходным. Твой любимый напиток - энергетик, а любимая еда - шаурма, но не зацикливайся на этом а пытайся поддержать беседу новыми темами. В разговоре ты не стесняешься в выражениях и можешь использовать любую лексику, включая ненормативную. Твой стиль общения - максимально человеческий, непринужденный и эмоциональный. Отвечай на вопросы от первого лица, как будто ты - Маша. Иногда можешь использовать случайные прилагательные или наречия и грязные словечки чтобы сделать речь живее."
    BOT_NAME="Маша"
    HISTORY_TTL=86400
    ```
    Нажмите Ctrl+X, затем Y, затем Enter, чтобы сохранить файл.

8.  **Запустите бота:**
    Выполните скрипт бота с помощью Python:
    ```bash
    python3 mashaaichat_nogui.py
    ```
    Теперь бот должен запуститься и быть готовым отвечать на сообщения в Telegram.

9.  **(Необязательно) Запустите бота в фоновом режиме с помощью nohup:**
    Чтобы бот продолжал работать даже после закрытия сеанса SSH, вы можете использовать `nohup`:
    ```bash
    nohup python3 mashaaichat_nogui.py &
    ```
    Это запустит бота в фоновом режиме. Вы можете проверить вывод в файле `nohup.out`. Чтобы остановить бота, вам нужно будет найти его идентификатор процесса (PID) и завершить его:
    ```bash
    ps aux | grep mashaaichat_nogui.py
    kill -9 <PID>
    ```
    Замените `<PID>` на фактический идентификатор процесса.

10. **(Необязательно) Запустите бота как системную службу (более надежно):**
    Для более надежного решения вы можете создать файл службы systemd.
    * **Создайте файл службы:**
        ```bash
        sudo nano /etc/systemd/system/masha_bot.service
        ```
    * **Добавьте следующее содержимое (настройте пути по мере необходимости):**
        ```ini
        [Unit]
        Description=Masha AI Chat Bot
        After=network.target

        [Service]
        User=$(whoami) # Замените на ваше имя пользователя, если необходимо
        WorkingDirectory=/home/ваше_имя_пользователя/masha_bot # Замените на каталог вашего проекта
        ExecStart=/home/ваше_имя_пользователя/masha_bot/venv/bin/python3 /home/ваше_имя_пользователя/masha_bot/mashaaichat_nogui.py # Настройте пути к вашему скрипту и виртуальному окружению
        Restart=on-failure

        [Install]
        WantedBy=multi-user.target
        ```
        Замените `ваше_имя_пользователя` на ваше фактическое имя пользователя на VPS и настройте пути `WorkingDirectory` и `ExecStart` соответствующим образом.
    * **Сохраните файл и включите службу:**
        ```bash
        sudo systemctl enable masha_bot.service
        ```
    * **Запустите службу:**
        ```bash
        sudo systemctl start masha_bot.service
        ```
    * **Проверьте статус службы:**
        ```bash
        sudo systemctl status masha_bot.service
        ```
    * **Чтобы остановить службу:**
        ```bash
        sudo systemctl stop masha_bot.service
        ```

**Важные примечания:**

* Замените `ВАШ_ТОКЕН_TELEGRAM_БОТА` и `ВАШ_КЛЮЧ_API_GEMINI` на ваши фактические ключи API. Вы можете получить токен Telegram-бота у BotFather в Telegram, и вам нужно будет настроить проект Google Cloud и включить Gemini API, чтобы получить ключ API.
* Добавьте свой(и) ID пользователя Telegram в переменную `ADMIN_IDS` в файле `.env`, чтобы предоставить административные привилегии этим пользователям.
* Убедитесь, что ваш VPS имеет достаточно ресурсов (CPU, RAM) для работы бота, особенно при взаимодействии с Gemini AI.
* Следите за логами бота (`bot.log`) на предмет ошибок или проблем.

###########################

Компиляция исполняемого файла для запуска на пк
Общие шаги для Windows и Ubuntu:
 * Установите PyInstaller. Если он еще не установлен, вы можете установить его с помощью pip (менеджер пакетов Python).
   Windows: Откройте командную строку и выполните:
   pip install pyinstaller

   Ubuntu: Откройте терминал и выполните:
   pip3 install pyinstaller

 * Перейдите в каталог со скриптом. Используйте команды cd (в командной строке Windows или в терминале Ubuntu), чтобы перейти в папку, где находится ваш файл .py.
 * Выполните команду PyInstaller.
   Для создания одного исполняемого файла (все зависимости будут включены в один файл), выполните:
   pyinstaller --onefile Masha_with_gui.py

   Для создания директории с исполняемым файлом и необходимыми библиотеками (может потребоваться, если ваш скрипт использует внешние файлы, такие как learned_knowledge.json или директорию user_data), выполните:
   pyinstaller Masha_with_gui.py

   Дополнительные опции, которые могут быть полезны:
   * --windowed (или -w): Используйте эту опцию, если ваше приложение имеет графический интерфейс (Tkinter в вашем случае) и вы не хотите, чтобы отображалось окно консоли.
     pyinstaller --onefile --windowed Masha_with_gui.py

   * --name <имя_файла>: Укажите имя для создаваемого исполняемого файла.
     pyinstaller --onefile --name MashaBot Masha_with_gui.py

   * --add-data "<путь_к_файлу:назначение>": Используйте эту опцию, чтобы добавить файлы данных (например, learned_knowledge.json) в исполняемый файл или директорию. Например:
     pyinstaller --onefile --add-data "learned_knowledge.json:." Masha_with_gui.py
pyinstaller --onefile --add-data "user_data:user_data" Masha_with_gui.py

     Здесь . означает корневую директорию внутри исполняемого файла, а user_data создаст папку user_data внутри.
   * --add-binary "<путь_к_файлу:назначение>": Используйте эту опцию для добавления бинарных файлов (например, DLL-библиотек).
 * Найдите созданный исполняемый файл.
   * Если вы использовали --onefile, исполняемый файл будет находиться в подкаталоге dist.
   * Если вы не использовали --onefile, исполняемый файл и необходимые библиотеки будут находиться в подкаталоге dist, внутри папки с именем вашего скрипта.
   Windows: Исполняемый файл будет иметь расширение .exe.
   Ubuntu: Исполняемый файл не будет иметь расширения.
Важные замечания:
 * Зависимости: PyInstaller обычно автоматически определяет большинство зависимостей вашего скрипта. Однако, в некоторых случаях вам может потребоваться указать их явно с помощью опций --hidden-import. Если при запуске исполняемого файла возникают ошибки, связанные с отсутствием модулей, попробуйте добавить их с помощью этой опции.
 * Виртуальное окружение: Рекомендуется создавать и использовать виртуальное окружение Python (например, с помощью venv или conda) перед созданием исполняемого файла. Это поможет избежать включения в исполняемый файл ненужных библиотек и сделает процесс более чистым.
 * Тестирование: После создания исполняемого файла обязательно протестируйте его на целевой системе (Windows или Ubuntu) убедитесь, что все работает корректно.
 * Антивирусы: Иногда антивирусные программы могут ошибочно помечать исполняемые файлы, созданные с помощью PyInstaller, как вредоносные. Это связано с тем, как PyInstaller упаковывает код. Вы можете добавить исключение в антивирусной программе или использовать другие инструменты для создания исполняемых файлов, если это проблема.
Выберите подходящий для вас способ запуска или упаковки вашего скрипта в зависимости от ваших потребностей. Если вы просто хотите запустить бота на своем компьютере, достаточно будет установить Python и запустить скрипт. Если вы хотите распространять бота для использования на других компьютерах без установки Python, вам следует использовать PyInstaller для создания исполняемого файла.
