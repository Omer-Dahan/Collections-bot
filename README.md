📁 Collections Telegram Bot

A powerful Telegram bot that helps you store, organize, browse, share, and manage collections of media and text.
Built for speed, simplicity, and handling very large collections without flooding the chat.

⭐ Features
📦 Collections

Create multiple collections

Add photos, videos, documents, audio, and text

Automatic file saving with captions

Real time batch status system

Structured browsing with pages and groups

🔗 Sharing

Generate secure share codes

View shared collections without copying data

Automatic usage log tracking

🗂 Management

File type filtering

Send all items from a page

Send entire collections with verification codes

Export collections to TXT

Delete items by ID, by file, or bulk delete

Full admin panel with user, collection, and share management

🔒 Permissions

User specific collections

Shared access mode

Blocked user system

Admin only actions where required

🚀 Getting Started
1. Clone the project
git clone https://github.com/your_username/your_bot_repo.git
cd your_bot_repo

2. Create a virtual environment
python -m venv venv

3. Activate the environment

Windows:

venv\Scripts\activate


Linux or macOS:

source venv/bin/activate

4. Install dependencies
pip install -r requirements.txt

5. Configure the bot

Create a file named config.py in the project folder:

BOT_TOKEN = "your-bot-token"
ADMIN_IDS = [123456789]
MAX_CAPTION_LENGTH = 1024


Make sure this file is included in .gitignore.

6. Run the bot
python bot.py


On first launch, the SQLite database is created automatically and migrations run if needed.

🧭 Usage Overview
➕ Creating a collection

Send:

/newcollection My Trip


Then start sending files. The bot automatically saves everything.

⭐ Selecting an active collection
/collections

📚 Browsing collections
/browse


Includes:

Page navigation

Ten item groups

Send all options

File type filters

🛠 Managing collections
/manage

🔍 Getting file IDs
/id_file

🗑 Delete mode
/remove


Delete by ID or by sending the file again.

🔗 Shared access
/access


Enter the share code you received.

🕹 Admin Panel

Admins can access:

/adminpanel


Includes:

Users list with cards and block options

Collections list with pagination

Share code dashboard

Global statistics

🧱 Project Structure
project/
│
├── bot.py              Main bot logic and handlers
├── db.py               SQLite database layer
├── admin_panel.py      Full admin panel system
├── requirements.txt    Python dependencies
├── config.py           Bot token and constants (ignored in Git)
└── bot_data.db         SQLite database (auto generated)

🤝 Contributing

Pull requests are welcome.
If you want to add features, fix bugs, or improve performance, you can open an issue or PR.

📜 License

Add your chosen license here.
MIT is recommended for open projects.