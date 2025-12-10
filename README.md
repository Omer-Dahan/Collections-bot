# 📁 Collections Telegram Bot

A powerful Telegram bot that helps you store, organize, browse, share, and manage collections of media and text.

## ⭐ Features

### 📦 Collections
- Create multiple collections
- Add photos, videos, documents, audio, and text
- Automatic saving with captions
- Real time batch status

### 🔗 Sharing
- Secure share codes
- View shared collections
- Usage analytics

---
## 🚀 Installation

### 1️⃣ Clone the project
```bash
git clone https://github.com/Omer-Dahan/Collections-bot
cd your_repo
```

### 2️⃣ Create virtual environment
Copy code
```bash
python -m venv venv
```

### 3️⃣ Activate it
Windows:

```bash
venv\Scripts\activate
```
Linux or macOS:

```bash
source venv/bin/activate
```

### 4️⃣ Install dependencies
Copy code
```bash
pip install -r requirements.txt
```

### 5️⃣ Run the bot
Copy code
```bash
python bot.py
```
### 🧱 Project Structure

📁 project/
├── 🧠 bot.py
├── 🛠 admin_panel.py
├── 🗄 db.py
├── 📦 requirements.txt
└── 📘 README.md

⚙️ Configuration

The bot uses a simple configuration file named config.py containing all runtime settings.
This file is not included in the repository for security reasons and must be created manually.

Example config.py
BOT_TOKEN = "your_bot_token_here"

# Admin list for advanced operations and visibility
ADMIN_IDS = [123456789, 987654321]

# Maximum caption length allowed by the bot logic
MAX_CAPTION_LENGTH = 800

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

What configuration controls

Telegram authentication

Admin permissions

Caption size limits

Access control logic

DB migrations that rely on admin IDs 

db

🗄 Database Architecture

The bot uses SQLite and creates all necessary tables automatically on startup.
All migrations run at runtime so deployment is simple and fully self contained.

Main tables

collections: stores user collections and ownership

items: stores all media entries

users: stores user info, first seen date and block status

shared_collections: manages share codes

shared_collection_access_log: tracks usage stats

Indexes are created for fast pagination and browsing performance.
Reference: initialization is handled inside init_db() and migrate_db() 

db

🔄 Bot Architecture and Flow Coordination

The bot is fully asynchronous and uses python telegram bot v21 with AIORateLimiter to safely manage flood control while sending heavy media batches.

Key internal coordinators

active_collections: tracks which collection each user is currently writing to

active_shared_collections: tracks temporary access for invited users

user_data modes:

delete_mode

id_mode

waiting_for_share_code

pending verification codes
These modes allow the bot to switch between features smoothly without overlapping states.

Automatic state resets

Whenever a user triggers a new command or a main menu button, the bot clears all modes through reset_user_modes() preventing stuck states or conflicting behaviors.
Reference implementation inside bot.py 

bot

⏱ Rate Limiting and Flood Protection

Sending large collections or many media groups requires careful control.
The bot includes:

Built in AIORateLimiter from python telegram bot

Custom safe_send_media_group wrapper that retries after flood errors

Automatic chunking into groups of 10 items

Delays between chunks to avoid Telegram limits

This makes sending hundreds or thousands of files stable and safe.

📥 Smart Batch Collection Status

When users upload many files in a row, the bot shows a dynamic status message:

Updates only if enough time passed

Sends a fresh message every 30 files

Tracks count separately for each collection

Prevents mixing counts between collections
Reference logic in update_batch_status() 

bot

🛡 Access Control and Validation

Every sensitive action is protected:

Ownership checks for managing collections

Admins bypass restrictions

Share code permission checks

Verification codes for destructive actions like deleting or sending an entire collection

The helper check_collection_access() centralizes all permission logic.
Reference inside bot.py 

bot

📊 Logging System

The bot uses a dual logging pipeline:

Console: all logs

bot.log: only important user actions and errors

A custom filter ensures internal spam messages are never written to file.
Reference: UserActionFilter and logging config in bot setup 

bot

🔧 Admin Panel Integration

Admins can use a separate panel (admin_panel.py) for:

Viewing users

Managing shares

Checking statistics

Blocking users

The admin panel communicates with the database layer and reuses all core logic.

