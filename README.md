# 📁 Collections Telegram Bot

A powerful Telegram bot that helps you store, organize, browse, share, and manage collections of media and text.

## ⭐ Features

### 📦 *Collections*
- Create multiple collections
- Add photos, videos, documents, audio, and text
- Automatic saving with captions
- Real time batch status

### 🔗 *Sharing*
- Secure share codes
- View shared collections
- Usage analytics

---

## 🚀 *Installation*

### 1️⃣ *Clone the project*
```
git clone https://github.com/Omer-Dahan/Collections-bot
cd Collections-bot
```

### 2️⃣ *Create virtual environment*
```
python -m venv venv
```

### 3️⃣ *Activate it*
Windows:
```
venv\Scripts\activate
```
Linux or macOS:
```
source venv/bin/activate
```

### 4️⃣ *Install dependencies*
```
pip install -r requirements.txt
```

### 5️⃣ *Run the bot*
```
python bot.py
```

---

## 🧱 *Project Structure*
```
📁 Collections-bot/
├── 🧠 bot.py
├── 🛠 admin_panel.py
├── 📝 db.py
├── ⚙️ config.py
├── 📦 requirements.txt
└── 📘 README.md
```

---

## ⚙️ *Configuration*

The bot uses a simple configuration file named `config.py` containing all runtime settings.  
This file is not included in the repository for security reasons and must be created manually.

### *Example config.py*
```
BOT_TOKEN = "your_bot_token_here"

ADMIN_IDS = [123456789, 987654321]

MAX_CAPTION_LENGTH = 800

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS
```

### *Configuration controls*
- Telegram authentication  
- Admin permissions  
- Caption limits  
- Access control logic  
- DB migrations depending on admin IDs  

---

## 🗄 *Database Architecture*

The bot uses SQLite and automatically creates all necessary tables at startup.  
Migrations run on launch for self contained deployment.

### *Main tables*
- `collections`  
- `items`  
- `users`  
- `shared_collections`
- `shared_collection_access_log`

Indexes are included for fast browsing and pagination.

---

## 🔄 *Bot Architecture and Flow Coordination*

The bot is asynchronous and uses python telegram bot v21 along with `AIORateLimiter`.

### *Core coordinators*
- `active_collections`
- `active_shared_collections`
- user_data modes: `delete_mode`, `id_mode`, `waiting_for_share_code`, verification codes

Modes ensure flows never mix or conflict.

### *Automatic state resets*
All states clear when the user triggers a new command via `reset_user_modes()`.

---

## ⏱ *Rate Limiting and Flood Protection*
- AIORateLimiter  
- Custom `safe_send_media_group` retry system  
- Chunking media into groups of 10  
- Delays between batches  

---

## 📥 *Smart Batch Collection Status*
- Updates only after cooldown  
- Fresh message every 30 files  
- Per collection counters  
- Prevents mixing counts

---

## 🛡 *Access Control and Validation*
- Ownership verification  
- Admin bypass  
- Share code access  
- Verification codes for destructive operations  

---

## 📊 *Logging System*
- Console logs everything  
- `bot.log` stores only filtered important actions  
- Custom filter removes internal noise  

---

## 🔧 *Admin Panel Integration*
The admin panel (`admin_panel.py`) provides:
- User management  
- Share code management  
- Platform statistics  
- User blocking  

