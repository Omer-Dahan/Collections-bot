import logging
import math
import random
from typing import Dict
import asyncio
from io import BytesIO

import sys
from datetime import datetime

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaVideo,
    InputMediaPhoto,
    InputMediaDocument,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
    AIORateLimiter,
)
from telegram.error import NetworkError
from config import BOT_TOKEN, ADMIN_IDS, MAX_CAPTION_LENGTH, is_admin
import db
from admin_panel import admin_panel, handle_admin_callback


# פילטר מותאם אישית - רק לוגים של פעולות משתמשים ושגיאות
class UserActionFilter(logging.Filter):
    def filter(self, record):
        # אפשר רק לוגים מהבוט שלנו או שגיאות
        if record.levelno >= logging.WARNING:  # שגיאות תמיד
            return True
        # רק לוגים מ-__main__ (הבוט שלנו)
        return record.name == "__main__"

# Handler לקובץ - רק פעולות משתמשים ושגיאות
file_handler = logging.FileHandler("bot.log", encoding="utf-8")
file_handler.setLevel(logging.INFO)
file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s]: %(message)s"))
file_handler.addFilter(UserActionFilter())

# Handler לקונסול - הכל
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))

# הגדרת root logger
logging.basicConfig(
    level=logging.INFO,
    handlers=[file_handler, console_handler]
)

logger = logging.getLogger(__name__)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    err = context.error

    # שגיאות רשת כמו httpx.ReadError, timeouts וכו - לא מעניינים אותנו בלוג
    if isinstance(err, NetworkError) or "ReadError" in str(err):
        logger.warning(f"Network issue ignored: {err}")
        return

    # כל שאר השגיאות - כן נרצה לראות עם סטאקטרייס
    logger.exception("Exception while handling update", exc_info=err)


active_collections: Dict[int, int] = {}  # user_id -> collection_id
active_shared_collections: Dict[int, str] = {}  # user_id -> share_code


def reset_user_modes(context: ContextTypes.DEFAULT_TYPE):
    """Reset all user modes when a new command is issued"""
    for key in ["delete_mode", "id_mode", "waiting_for_share_code", 
                "verify_delete_collection", "verify_send_collection"]:
        context.user_data.pop(key, None)


def track_and_reset_user(user, context: ContextTypes.DEFAULT_TYPE):
    """Track user in DB and reset all modes"""
    reset_user_modes(context)
    db.upsert_user(user.id, user.username, user.first_name, user.last_name)


# Message constants
MSG_NO_COLLECTIONS = "אין עדיין אוספים. צור אחד עם /newcollection."


def build_collection_keyboard(collections, callback_prefix: str, add_back_button: bool = False):
    """Build a keyboard with collection buttons"""
    keyboard = [
        [InlineKeyboardButton(text=f"📁 {name}", callback_data=f"{callback_prefix}:{col_id}")]
        for col_id, name in collections
    ]
    if add_back_button:
        keyboard.append([InlineKeyboardButton("⬅ חזור לתפריט ראשי", callback_data="back_to_main")])
    return keyboard


def get_user_keyboard():
    """בניית מקלדת קבועה עם כפתור התחל בלבד"""
    return ReplyKeyboardMarkup(
        [[KeyboardButton("/start")]],
        resize_keyboard=True,
        one_time_keyboard=False,
        is_persistent=True,
    )


def check_collection_access(user_id: int, collection_id: int) -> tuple[bool, str, tuple | None]:
    """
    Check if user has access to the collection.
    Supports both owned collections and shared collections.
    Returns: (is_allowed, error_message, collection_object)
    """
    collection = db.get_collection_by_id(collection_id)
    if not collection:
        return False, "האוסף לא נמצא (אולי נמחק?).", None
    
    # collection structure: (id, name, user_id)
    owner_id = collection[2]
    
    # Check if user owns the collection or is admin
    if owner_id == user_id or is_admin(user_id):
        return True, "", collection
    
    # Check if user has shared access
    if user_id in active_shared_collections:
        share_code = active_shared_collections[user_id]
        shared_collection = db.get_collection_by_share_code(share_code)
        if shared_collection and shared_collection[0] == collection_id:
            return True, "", collection
    
    return False, "אין לך גישה לאוסף הזה.", None



def create_verification_code(context: ContextTypes.DEFAULT_TYPE, action_type: str, data: dict) -> int:
    """
    Create a 4-digit verification code and store it in user_data.
    
    Args:
        context: Telegram context
        action_type: Type of action (e.g., "delete_collection", "send_collection")
        data: Dictionary with action-specific data to store
    
    Returns:
        The generated 4-digit code
    """
    import random
    code = random.randint(1000, 9999)
    
    context.user_data[f"verify_{action_type}"] = {
        "code": code,
        **data
    }
    
    return code


def verify_user_code(message, context: ContextTypes.DEFAULT_TYPE, action_type: str) -> tuple[bool, dict | None]:
    """
    Verify user's input code against stored verification.
    
    Args:
        message: Telegram message with user's code
        context: Telegram context
        action_type: Type of action to verify
    
    Returns:
        (is_valid, stored_data or None)
    """
    key = f"verify_{action_type}"
    
    if key not in context.user_data or not message.text:
        return False, None
    
    try:
        user_code = int(message.text.strip())
        stored = context.user_data[key]
        
        if user_code == stored["code"]:
            context.user_data.pop(key)
            return True, stored
        else:
            context.user_data.pop(key)
            return False, None
    except ValueError:
        return False, None


def prepare_media_groups(items: list) -> tuple[list, list]:
    """
    Prepare media items into visual and document groups.
    
    Args:
        items: List of item tuples from DB (item_id, content_type, file_id, text_content, file_name, file_size, added_at)
    
    Returns:
        (media_visual, media_docs) - Two lists of InputMedia objects
    """
    media_visual = []
    media_docs = []
    
    for item_id, content_type, file_id, text_content, file_name, file_size, added_at in items:
        if not file_id:
            continue
            
        if content_type == "video":
            media_visual.append(InputMediaVideo(media=file_id, caption=text_content))
        elif content_type == "photo":
            media_visual.append(InputMediaPhoto(media=file_id, caption=text_content))
        elif content_type == "document":
            media_docs.append(InputMediaDocument(media=file_id, filename=file_name, caption=text_content))
    
    return media_visual, media_docs


async def send_media_groups_in_chunks(bot, chat_id: int, media_visual: list, media_docs: list):
    """
    Send media groups in chunks of 10 to avoid flood limits.
    
    Args:
        bot: Telegram bot instance
        chat_id: Chat ID to send to
        media_visual: List of InputMediaVideo and InputMediaPhoto objects
        media_docs: List of InputMediaDocument objects
    """
    # Send visual media (photos/videos) in chunks of 10
    for i in range(0, len(media_visual), 10):
        chunk = media_visual[i:i + 10]
        await safe_send_media_group(bot, chat_id=chat_id, media=chunk)
        if i + 10 < len(media_visual):
            await asyncio.sleep(4)  # Increased from 1s to 2s to prevent flood control
    
    # Send documents in chunks of 10
    for i in range(0, len(media_docs), 10):
        chunk = media_docs[i:i + 10]
        await safe_send_media_group(bot, chat_id=chat_id, media=chunk)
        if i + 10 < len(media_docs):
            await asyncio.sleep(4)  # Increased from 1s to 2s to prevent flood control


def get_page_header(collection_id: int, page: int, block_size: int = 100, page_prefix: str = "") -> tuple[str, int, int, int, int, list]:
    """
    Calculate pagination details and generate header text.
    
    Args:
        collection_id: Collection ID
        page: Current page number
        block_size: Number of items per page
        page_prefix: Optional prefix for the header text
        
    Returns:
        (header_text, total_items, total_pages, items_in_block, page, items_block)
        Note: returns updated page (in case it was out of bounds)
    """
    total_items = db.count_items_in_collection(collection_id)
    total_pages = max(1, math.ceil(total_items / block_size))
    
    if page < 1:
        page = 1
    elif page > total_pages:
        page = total_pages
        
    offset = (page - 1) * block_size
    items_block = db.get_items_by_collection(collection_id, offset=offset, limit=block_size)
    items_in_block = len(items_block)

    first_index = offset + 1
    last_index = offset + items_in_block

    if total_items == 0:
         first_index = 0

    header_text = (
        f"{page_prefix}"
        f"✅ עמוד {page} מתוך {total_pages}\n"
        f"📦 מציג פריטים {first_index}-{last_index} מתוך {total_items}"
    )
    
    return header_text, total_items, total_pages, items_in_block, page, items_block

async def safe_send_media_group(bot, chat_id, media, reply_to_message_id=None):
    """
    Safe wrapper for send_media_group that handles RetryAfter errors.
    """
    try:
        await bot.send_media_group(chat_id=chat_id, media=media, reply_to_message_id=reply_to_message_id)
    except Exception as e:
        if "RetryAfter" in str(e):
            # Extract retry time if possible, or default to a safe wait
            logger.warning(f"Flood control triggered. Waiting... Error: {e}")
            # The AIORateLimiter should handle most of this, but if we still hit it:
            await asyncio.sleep(5) 
            try:
                await bot.send_media_group(chat_id=chat_id, media=media, reply_to_message_id=reply_to_message_id)
            except Exception as retry_e:
                logger.error(f"Failed to send media group after retry: {retry_e}")
        else:
            logger.error(f"Error sending media group: {e}")


def build_main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📁 יצירת אוסף חדש", callback_data="main_menu:newcollection")],
        [InlineKeyboardButton("⭐ בחירת אוסף פעיל", callback_data="main_menu:collections")],
        [InlineKeyboardButton("📚 דפדוף וצפייה בתוכן", callback_data="main_menu:browse")],
        [InlineKeyboardButton("➕ הוסף תוכן לאוסף", callback_data="main_menu:collections")],
        [InlineKeyboardButton("🛠 ניהול אוספים", callback_data="main_menu:manage")],
        [InlineKeyboardButton("🗑 מצב מחיקה", callback_data="main_menu:remove")],
        [InlineKeyboardButton("🔍 זיהוי file_id", callback_data="main_menu:id_file")],
    ])


def get_main_menu_text() -> str:
    """Get the main menu welcome text"""
    return (
        "היי, ברוך הבא לבוט שמירת האוספים שלך.\n"
        "כאן אפשר לאסוף, לארגן ולמצוא כל תמונה, וידאו, מסמך או טקסט בצורה פשוטה ומהירה.\n"
        "בחר פעולה מהתפריט למטה:"
    )


async def send_main_menu(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    text = get_main_menu_text()
    keyboard = build_main_menu_keyboard()

    msg_id = context.user_data.get("main_menu_msg_id")
    if msg_id:
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=msg_id,
                text=text,
                reply_markup=keyboard,
            )
            return
        except Exception:
            # If edit fails (e.g. message deleted), fall through to send new
            pass

    msg = await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        reply_markup=keyboard,
    )
    context.user_data["main_menu_msg_id"] = msg.message_id

def format_size(size: int | None) -> str:
    if not size:
        return ""
    units = ["B", "KB", "MB", "GB", "TB"]
    s = float(size)
    idx = 0
    while s >= 1024 and idx < len(units) - 1:
        s /= 1024.0
        idx += 1
    if idx == 0:
        return f"{int(s)} {units[idx]}"
    return f"{s:.1f} {units[idx]}"


async def update_batch_status(message, context: ContextTypes.DEFAULT_TYPE, collection_name: str):
    """עדכון הודעת סטטוס איסוף קבצים - מערכת phase-based"""
    user_id = message.from_user.id
    
    # Get active collection for this user
    if user_id not in active_collections:
        return
    
    collection_id = active_collections[user_id]
    user_data = context.user_data
    
    # Initialize batch_status dict if not present
    if "batch_status" not in user_data:
        user_data["batch_status"] = {}
    
    # Initialize this collection's status if not present
    if collection_id not in user_data["batch_status"]:
        user_data["batch_status"][collection_id] = {
            "count": 0,
            "msg_id": None,
            "last_update": 0.0,
            "last_fresh_message_time": 0.0,
            "phase": 0
        }
    
    # Get collection-specific status
    status = user_data["batch_status"][collection_id]
    status["count"] += 1
    count = status["count"]
    msg_id = status["msg_id"]
    last_update = status["last_update"]
    phase = status.get("phase", 0)
    last_fresh_message_time = status.get("last_fresh_message_time", 0.0)
    
    chat_id = message.chat_id
    current_time = datetime.now().timestamp()
    
    # PHASE 1: First file - send initial message without count
    if phase == 0:
        initial_text = f'תהליך קליטת הקבצים החל לאוסף "{collection_name}"...'
        
        status_msg = await message.reply_text(initial_text)
        status["msg_id"] = status_msg.message_id
        status["phase"] = 1
        status["last_update"] = current_time
        status["last_fresh_message_time"] = current_time
        return
    
    # PHASE 2: After 5 seconds - show count for the first time
    if phase == 1 and (current_time - last_fresh_message_time) >= 1:
        text = f'נאספו עד עכשיו {count} קבצים לאוסף "{collection_name}"'
        
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("📊 סטטוס", callback_data=f"batch_status:{count}"),
                InlineKeyboardButton("🛑 הפסק הוספה", callback_data="stop_collect")
            ]
        ])
        
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=msg_id,
                text=text,
                reply_markup=keyboard,
            )
            status["phase"] = 2
            status["last_update"] = current_time
        except Exception:
            try:
                status_msg = await message.reply_text(text, reply_markup=keyboard)
                status["msg_id"] = status_msg.message_id
                status["phase"] = 2
                status["last_update"] = current_time
                status["last_fresh_message_time"] = current_time
            except Exception:
                pass
        return
    
    # PHASE 3: Periodic updates - only if count changed
    if phase == 2:
        time_since_last_update = current_time - last_update
        
        should_send_fresh = (
            count % 30 == 0 and 
            count > 1 and 
            (current_time - last_fresh_message_time) >= 15
        )
        
        if should_send_fresh:
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
            except Exception:
                pass
            
            text = f'נאספו עד עכשיו {count} קבצים לאוסף "{collection_name}"'
            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("📊 סטטוס", callback_data=f"batch_status:{count}"),
                    InlineKeyboardButton("🛑 הפסק הוספה", callback_data="stop_collect")
                ]
            ])
            
            try:
                status_msg = await message.reply_text(text, reply_markup=keyboard)
                status["msg_id"] = status_msg.message_id
                status["last_update"] = current_time
                status["last_fresh_message_time"] = current_time
            except Exception:
                pass
        
        elif time_since_last_update >= 5:
            text = f'נאספו עד עכשיו {count} קבצים לאוסף "{collection_name}"'
            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("📊 סטטוס", callback_data=f"batch_status:{count}"),
                    InlineKeyboardButton("🛑 הפסק הוספה", callback_data="stop_collect")
                ]
            ])
            
            try:
                await context.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=msg_id,
                    text=text,
                    reply_markup=keyboard,
                )
                status["last_update"] = current_time
            except Exception:
                try:
                    status_msg = await message.reply_text(text, reply_markup=keyboard)
                    status["msg_id"] = status_msg.message_id
                    status["last_update"] = current_time
                    status["last_fresh_message_time"] = current_time
                except Exception:
                    pass


async def delete_message_after_delay(bot, chat_id: int, message_id: int, delay: int):
    """מחיקת הודעה אחרי השהייה"""
    await asyncio.sleep(delay)
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        pass


def build_page_menu(
    collection_id: int,
    page: int,
    total_pages: int,
    items_in_block: int,
    group_size: int = 10,
) -> InlineKeyboardMarkup:
    """תפריט לדפדוף: בחר הכל ומתחת מספרים שכל אחד מייצג קבוצה של פריטים"""

    # שורה ראשונה: בחר הכל
    row_select_all = [
        InlineKeyboardButton(
            "✳ בחר הכל",
            callback_data=f"browse_page_select_all:{collection_id}:{page}",
        )
    ]

    # כמה קבוצות צריך בעמוד הזה
    groups_count = math.ceil(items_in_block / group_size)
    groups_count = min(groups_count, 10)  # עד 10 קבוצות לעמוד

    row_numbers_1: list[InlineKeyboardButton] = []
    row_numbers_2: list[InlineKeyboardButton] = []

    # בסיס לתצוגה של המספרים בהתאם לעמוד
    display_base = (page - 1) * 10

    for idx in range(1, groups_count + 1):
        display_number = display_base + idx
        btn = InlineKeyboardButton(
            str(display_number),
            callback_data=f"browse_group:{collection_id}:{page}:{idx}",
        )
        if idx <= 5:
            row_numbers_1.append(btn)
        else:
            row_numbers_2.append(btn)

    keyboard: list[list[InlineKeyboardButton]] = [row_select_all]
    if row_numbers_1:
        keyboard.append(row_numbers_1)
    if row_numbers_2:
        keyboard.append(row_numbers_2)

    # ניווט בין עמודי ה 100
    nav_row: list[InlineKeyboardButton] = []
    if page > 1:
        nav_row.append(
            InlineKeyboardButton(
                "⬅ עמוד קודם",
                callback_data=f"browse_page:{collection_id}:{page - 1}",
            )
        )
    if page < total_pages:
        nav_row.append(
            InlineKeyboardButton(
                "עמוד הבא ➡",
                callback_data=f"browse_page:{collection_id}:{page + 1}",
            )
        )
    if nav_row:
        keyboard.append(nav_row)

    return InlineKeyboardMarkup(keyboard)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    track_and_reset_user(user, context)

    chat = update.effective_chat
    if not chat:
        return
    
    logger.info("Saved item from user_id=%s username=%s", user.id, user.username)

    # Update the reply keyboard to show only /start
    # We do this with a temporary message that gets deleted
    temp_msg = await update.message.reply_text(
        "🔄",
        reply_markup=get_user_keyboard(),
    )
    
    # Delete the temporary message
    try:
        await context.bot.delete_message(chat_id=chat.id, message_id=temp_msg.message_id)
    except Exception:
        pass
    
    # Send the main menu message with inline keyboard
    msg = await context.bot.send_message(
        chat_id=chat.id,
        text=get_main_menu_text(),
        reply_markup=build_main_menu_keyboard(),
    )
    
    # Store the message ID for future edits
    context.user_data["main_menu_msg_id"] = msg.message_id


async def new_collection_flow(message, user, context, args: list[str], edit_message_id: int = None):
    if not args:
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("⬅ חזור לתפריט ראשי", callback_data="back_to_main")]
        ])
        text = "כתוב שם לאוסף. לדוגמה:\n/newcollection טיול בולגריה"
        
        if edit_message_id:
            await context.bot.edit_message_text(
                chat_id=message.chat_id,
                message_id=edit_message_id,
                text=text,
                reply_markup=keyboard
            )
        else:
            await message.reply_text(text, reply_markup=keyboard)
        return

    name = " ".join(args)
    try:
        collection_id = db.create_collection(name, user.id)
        active_collections[user.id] = collection_id
        
        # Auto-activate collection mode
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton(text="🛑 הפסק הוספה", callback_data="stop_collect")]]
        )
        
        await message.reply_text(
            f"✅ אוסף חדש נוצר: {name}\n\n"
            f"🔄 מתחיל מצב איסוף...\n"
            f"העלה עכשיו קבצים (תמונות, סרטונים, מסמכים) והם יתווספו לאוסף.",
            reply_markup=keyboard
        )
    except Exception as e:
        logger.exception("Error creating collection")
        # Check if it's a duplicate name error
        if "UNIQUE constraint failed" in str(e):
            await message.reply_text(f"❌ כבר יש לך אוסף בשם '{name}'.\nבחר שם אחר.")
        else:
            await message.reply_text(f"שגיאה ביצירת אוסף: {e}")


async def new_collection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    track_and_reset_user(user, context)

    await new_collection_flow(update.message, user, context, context.args)


async def list_collections_flow(message, user, context, edit_message_id: int = None):
    collections = db.get_collections(user.id)
    if not collections:
        text = MSG_NO_COLLECTIONS
        if edit_message_id:
            await context.bot.edit_message_text(
                chat_id=message.chat_id,
                message_id=edit_message_id,
                text=text
            )
        else:
            await message.reply_text(text)
        return

    keyboard = build_collection_keyboard(collections, "select_collection", add_back_button=True)
    text = "בחר אוסף פעיל:"
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if edit_message_id:
        await context.bot.edit_message_text(
            chat_id=message.chat_id,
            message_id=edit_message_id,
            text=text,
            reply_markup=reply_markup
        )
    else:
        await message.reply_text(text, reply_markup=reply_markup)


async def list_collections(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    track_and_reset_user(user, context)
    
    await list_collections_flow(update.message, user, context)


async def manage_collections_flow(message, user, context, edit_message_id: int = None):
    collections = db.get_collections(user.id)
    if not collections:
        text = MSG_NO_COLLECTIONS
        if edit_message_id:
            await context.bot.edit_message_text(
                chat_id=message.chat_id,
                message_id=edit_message_id,
                text=text
            )
        else:
            await message.reply_text(text)
        return

    keyboard = build_collection_keyboard(collections, "manage_collection", add_back_button=True)
    text = "בחר אוסף לניהול:"
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if edit_message_id:
        await context.bot.edit_message_text(
            chat_id=message.chat_id,
            message_id=edit_message_id,
            text=text,
            reply_markup=reply_markup
        )
    else:
        await message.reply_text(text, reply_markup=reply_markup)


async def manage_collections(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ניהול אוספים - ייצוא ומחיקה"""
    user = update.effective_user
    track_and_reset_user(user, context)
    
    await manage_collections_flow(update.message, user, context)

async def handle_select_collection_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """בחירת אוסף פעיל לשמירה (לא קשור לדפדוף)"""
    query = update.callback_query
    await query.answer()

    user = query.from_user
    data = query.data  # format: select_collection:<id>
    if not data.startswith("select_collection:"):
        return

    _, col_id_str = data.split(":")
    collection_id = int(col_id_str)

    is_allowed, error_msg, collection = check_collection_access(user.id, collection_id)
    if not is_allowed:
        await query.edit_message_text(error_msg)
        return


    active_collections[user.id] = collection_id

    # Keep batch_status for all collections - don't reset when switching

    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton(text="🛑 הפסק הוספה", callback_data="stop_collect")]]
    )

    await query.edit_message_text(
        text=f"אוסף פעיל הוגדר: {collection[1]}\nעכשיו שלח תוכן כדי לשמור.",
        reply_markup=keyboard,
    )


def build_page_file_type_menu(
    collection_id: int,
    page: int,
    video_count: int,
    image_count: int,
    doc_count: int,
) -> InlineKeyboardMarkup:
    """תפריט סוגי קבצים עבור עמוד דפדוף מסוים"""

    keyboard = [
        [
            InlineKeyboardButton(
                text=f"🎬 סרטונים ({video_count})",
                callback_data=f"page_files_videos:{collection_id}:{page}",
            )
        ],
        [
            InlineKeyboardButton(
                text=f"🖼 תמונות ({image_count})",
                callback_data=f"page_files_images:{collection_id}:{page}",
            )
        ],
        [
            InlineKeyboardButton(
                text=f"💿 קבצים ({doc_count})",
                callback_data=f"page_files_document:{collection_id}:{page}",
            )
        ],
        [
            InlineKeyboardButton(
                text="📨 שלח את כל התוכן בעמוד",
                callback_data=f"page_files_queue_all:{collection_id}:{page}",
            ),
        ],
        [
            InlineKeyboardButton(
                text="📦 שלח את כל האוסף",
                callback_data=f"collection_send_all:{collection_id}",
            ),
        ],
    ]

    return InlineKeyboardMarkup(keyboard)


async def show_browse_menu(chat_id: int, user_id: int, context: ContextTypes.DEFAULT_TYPE, edit_message_id: int = None):
    """
    Shared function to display browse menu.
    Can be used from both command handlers and callback queries.
    
    Args:
        chat_id: The chat ID to send the message to
        user_id: The user ID to get collections for
        context: The context object
        edit_message_id: If provided, edit this message instead of sending a new one
    """
    collections = db.get_collections(user_id)
    if not collections:
        text = "אין אוספים לדפדוף."
        if edit_message_id:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=edit_message_id,
                text=text
            )
        else:
            await context.bot.send_message(chat_id=chat_id, text=text)
        return

    # Browse needs special callback format with page number, so build manually
    keyboard = [
        [InlineKeyboardButton(text=f"📁 {name}", callback_data=f"browse_page:{col_id}:1")]
        for col_id, name in collections
    ]
    
    # Add Back button
    keyboard.append([InlineKeyboardButton("⬅️ חזור לתפריט ראשי", callback_data="back_to_main")])

    text = "בחר אוסף לדפדוף:"
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if edit_message_id:
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=edit_message_id,
            text=text,
            reply_markup=reply_markup
        )
    else:
        await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=reply_markup
        )


async def browse(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """פקודת /browse - בחירת אוסף לדפדוף"""
    user = update.effective_user
    track_and_reset_user(user, context)

    await show_browse_menu(
        chat_id=update.message.chat_id,
        user_id=user.id,
        context=context
    )



async def handle_browse_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """תצוגת עמוד דפדוף: כל עמוד עד 100 פריטים, מחולק לקבוצות של 10"""
    query = update.callback_query
    await query.answer()

    user = query.from_user
    data = query.data  # format: browse_page:<collection_id>:<page>
    if not data.startswith("browse_page:"):
        return

    _, col_id_str, page_str = data.split(":")
    collection_id = int(col_id_str)
    page = int(page_str)

    is_allowed, error_msg, collection = check_collection_access(user.id, collection_id)
    if not is_allowed:
        await query.edit_message_text(error_msg)
        return

    block_size = 100
    group_size = 10

    # Use helper for pagination
    header_text, total_items, total_pages, items_in_block, page, _ = get_page_header(
        collection_id, page, block_size
    )

    if total_items == 0:
        await query.edit_message_text("אין פריטים באוסף הזה.")
        return

    reply_markup = build_page_menu(
        collection_id=collection_id,
        page=page,
        total_pages=total_pages,
        items_in_block=items_in_block,
        group_size=group_size,
    )
    
    # Add Back button with context awareness
    back_text = "⬅️ חזור לרשימת האוספים"
    back_data = "back_to_browse"
    
    # Check if admin is viewing someone else's collection
    if is_admin(user.id) and collection[2] != user.id:
        back_text = "⬅️ חזור לניהול האוסף"
        back_data = f"admin_manage_col:{collection_id}"
    
    keyboard_list = list(reply_markup.inline_keyboard)
    keyboard_list.append([InlineKeyboardButton(back_text, callback_data=back_data)])
    reply_markup = InlineKeyboardMarkup(keyboard_list)

    await query.edit_message_text(
        text=header_text,
        reply_markup=reply_markup,
    )


async def handle_browse_group_or_select_all_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """כפתורי המספרים (קבוצות) ו'בחר הכל' בעמוד דפדוף"""
    query = update.callback_query
    await query.answer()

    user = query.from_user
    data = query.data
    chat_id = query.message.chat_id

    block_size = 100
    group_size = 10

    if data.startswith("browse_group:"):
        _, col_id_str, page_str, group_str = data.split(":")
        collection_id = int(col_id_str)
        page = int(page_str)
        group_index = int(group_str)

        is_allowed, error_msg, collection = check_collection_access(user.id, collection_id)
        if not is_allowed:
            await query.answer(error_msg, show_alert=True)
            return

        offset_block = (page - 1) * block_size
        items_block = db.get_items_by_collection(collection_id, offset=offset_block, limit=block_size)

        start_in_block = (group_index - 1) * group_size
        end_in_block = start_in_block + group_size

        if start_in_block >= len(items_block):
            await query.answer("אין קבוצה כזו בעמוד הזה.", show_alert=True)
            return

        group_items = items_block[start_in_block:end_in_block]

        logger.info(
            "Sending group: user_id=%s collection_id=%s page=%s group=%s items_count=%s",
            user.id,
            collection_id,
            page,
            group_index,
            len(group_items),
        )

        # Use helper functions to prepare and send media
        media_visual, media_docs = prepare_media_groups(group_items)
        await send_media_groups_in_chunks(context.bot, chat_id, media_visual, media_docs)
        
        if media_visual or media_docs:
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=query.message.message_id)
            except Exception:
                pass

            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=query.message.message_id)
            except Exception:
                pass

            # Use helper for pagination
            header_text, total_items, total_pages, items_in_block, page, _ = get_page_header(
                collection_id, page, block_size
            )

            reply_markup = build_page_menu(
                collection_id=collection_id,
                page=page,
                total_pages=total_pages,
                items_in_block=items_in_block,
                group_size=group_size,
            )
            
            # Add Back button with context awareness
            back_text = "⬅️ חזור לרשימת האוספים"
            back_data = "back_to_browse"
            
            # Check if admin is viewing someone else's collection
            if is_admin(user.id) and collection[2] != user.id:
                back_text = "⬅️ חזור לניהול האוסף"
                back_data = f"admin_manage_col:{collection_id}"

            keyboard_list = list(reply_markup.inline_keyboard)
            keyboard_list.append([InlineKeyboardButton(back_text, callback_data=back_data)])
            reply_markup = InlineKeyboardMarkup(keyboard_list)
            
            await context.bot.send_message(
                chat_id=chat_id,
                text=header_text,
                reply_markup=reply_markup,
            )
        else:
            await context.bot.send_message(
                chat_id=chat_id,
                text="ℹ אין בקבוצה הזו קבצים לשליחה.",
            )

    if data.startswith("browse_page_select_all:"):
        _, col_id_str, page_str = data.split(":")
        collection_id = int(col_id_str)
        page = int(page_str)

        is_allowed, error_msg, collection = check_collection_access(user.id, collection_id)
        if not is_allowed:
            await query.answer(error_msg, show_alert=True)
            return

        offset_block = (page - 1) * block_size
        items_block = db.get_items_by_collection(collection_id, offset=offset_block, limit=block_size)

        video_count = 0
        image_count = 0
        doc_count = 0
        for item_id, content_type, file_id, text_content, file_name, file_size, added_at in items_block:
            if content_type == "video":
                video_count += 1
            elif content_type == "photo":
                image_count += 1
            elif content_type == "document":
                doc_count += 1

        total_files = video_count + image_count + doc_count
        if total_files == 0:
            await context.bot.send_message(
                chat_id=chat_id,
                text="ℹ אין בעמוד הזה תוכן."
            )
            return

        reply_markup = build_page_file_type_menu(
            collection_id=collection_id,
            page=page,
            video_count=video_count,
            image_count=image_count,
            doc_count=doc_count,
        )

        await context.bot.send_message(
            chat_id=chat_id,
            text="בחר את סוג הקבצים שברצונך לקבל מהעמוד הזה:",
            reply_markup=reply_markup,
        )
        return


async def handle_page_file_send_choice_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """שליחת תכנים לפי סוג מתוך עמוד דפדוף, אחרי 'בחר הכל'"""
    query = update.callback_query
    await query.answer()

    user = query.from_user
    data = query.data  # למשל: page_files_videos:<collection_id>:<page>
    if not data.startswith("page_files_"):
        return

    try:
        choice, col_id_str, page_str = data.split(":")
    except ValueError:
        return

    collection_id = int(col_id_str)
    page = int(page_str)

    is_allowed, error_msg, collection = check_collection_access(user.id, collection_id)
    if not is_allowed:
        await query.answer(error_msg, show_alert=True)
        return

    chat_id = query.message.chat_id
    block_size = 100

    offset_block = (page - 1) * block_size
    items_block = db.get_items_by_collection(collection_id, offset=offset_block, limit=block_size)

    if choice == "page_files_videos":
        filtered = [item for item in items_block if item[1] == "video"]
    elif choice == "page_files_images":
        filtered = [item for item in items_block if item[1] == "photo"]
    elif choice == "page_files_document":
        filtered = [item for item in items_block if item[1] == "document"]
    elif choice == "page_files_queue_all":
        filtered = [item for item in items_block if item[1] in ("video", "photo", "document")]
    else:
        return

    if not filtered:
        await context.bot.send_message(
            chat_id=chat_id,
            text="ℹ אין בעמוד הזה קבצים מהסוג שבחרת."
        )
        return

    logger.info(
        "Sending page items: user_id=%s collection_id=%s page=%s choice=%s count=%s",
        user.id,
        collection_id,
        page,
        choice,
        len(filtered),
    )

    # Use helper functions to prepare and send media
    media_visual, media_docs = prepare_media_groups(filtered)
    await send_media_groups_in_chunks(context.bot, chat_id, media_visual, media_docs)

    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=query.message.message_id)
    except Exception:
        pass

    # Send back the browse page menu so user can continue browsing
    block_size = 100
    group_size = 10
    
    total_items = db.count_items_in_collection(collection_id)
    total_pages = max(1, math.ceil(total_items / block_size))
    
    if page < 1:
        page = 1
    elif page > total_pages:
        page = total_pages
    
    offset_block = (page - 1) * block_size
    items_block = db.get_items_by_collection(collection_id, offset=offset_block, limit=block_size)
    items_in_block = len(items_block)
    
    first_index = offset_block + 1
    last_index = offset_block + items_in_block
    
    header_text = (
        f"✅ עמוד {page} מתוך {total_pages}\n"
        f"📦 מציג פריטים {first_index}-{last_index} מתוך {total_items}"
    )
    
    reply_markup = build_page_menu(
        collection_id=collection_id,
        page=page,
        total_pages=total_pages,
        items_in_block=items_in_block,
        group_size=group_size,
    )
    
    # Add Back to Collections button
    keyboard_list = list(reply_markup.inline_keyboard)
    keyboard_list.append([InlineKeyboardButton("⬅️ חזור לרשימת האוספים", callback_data="back_to_browse")])
    reply_markup = InlineKeyboardMarkup(keyboard_list)
    
    await context.bot.send_message(
        chat_id=chat_id,
        text=header_text,
        reply_markup=reply_markup,
    )


async def handle_batch_status_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """הצגת התראה קופצת עם מספר הקבצים שנוספו"""
    query = update.callback_query
    
    user = query.from_user
    

    data = query.data  # format: batch_status:<count>
    try:
        _, count_str = data.split(":")
        count = int(count_str)
        await query.answer(f"✅ נוספו {count} קבצים לאוסף!", show_alert=True)
    except Exception as e:
        logger.error(f"Error in batch status: {e}")
        await query.answer("שגיאה בקבלת סטטוס", show_alert=True)


async def handle_collection_send_all_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Request confirmation with verification code before sending all items in a collection."""
    query = update.callback_query
    user = query.from_user

    data = query.data  # format: collection_send_all:<collection_id>
    if not data.startswith("collection_send_all:"):
        return

    try:
        _, col_id_str = data.split(":")
        collection_id = int(col_id_str)
    except ValueError:
        await query.answer("שגיאה בנתוני האוסף.", show_alert=True)
        return

    # Permission check (same helper used elsewhere)
    is_allowed, error_msg, collection = check_collection_access(user.id, collection_id)
    if not is_allowed:
        await query.answer(error_msg, show_alert=True)
        return

    collection_name = collection[1]

    # Count total items in collection
    total_items = db.count_items_in_collection(collection_id)
    if total_items == 0:
        await query.answer("ℹ אין קבצים באוסף הזה.", show_alert=True)
        return

    # Generate verification code using helper function
    confirmation_code = create_verification_code(
        context,
        "send_collection",
        {
            "collection_id": collection_id,
            "collection_name": collection_name,
            "total_items": total_items,
        }
    )

    # Show popup with the code
    await query.answer(
        f"בכדי לשלוח את האוסף\nשלח לבוט: {confirmation_code}",
        show_alert=True,
    )

    logger.info(
        "Send collection confirmation requested: user_id=%s collection_id=%s total_items=%s code=%s",
        user.id,
        collection_id,
        total_items,
        confirmation_code,
    )


async def handle_stop_collect_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user = query.from_user
    context.user_data.pop("batch_count", None)
    context.user_data.pop("batch_msg_id", None)

    if user.id in active_collections:
        del active_collections[user.id]

    # Show main menu directly instead of separate message
    await query.edit_message_text(
        get_main_menu_text(),
        reply_markup=build_main_menu_keyboard()
    )
    context.user_data["main_menu_msg_id"] = query.message.message_id


async def remove_flow(message, user, context, args: list[str], edit_message_id: int = None):
    if args:
        arg = args[0]
        try:
            item_id = int(arg)
        except ValueError:
            await message.reply_text("ה id ששלחת לא מספר תקין.")
            return

        deleted = db.delete_item_by_id(item_id, user.id)
        if deleted > 0:
            await message.reply_text(f"נמחק פריט אחד עם id {item_id}.")
        else:
            await message.reply_text("לא נמצא פריט עם id הזה.")
        return

    # Check if user has any collections/items
    collections = db.get_collections(user.id)
    if not collections:
        await message.reply_text("אין לך עדיין אוספים למחיקה. צור אוסף חדש כדי להתחיל.")
        return
        
    # Check if user has any items at all (optional, but good UX)
    total_items = 0
    for col_id, _ in collections:
        total_items += db.count_items_in_collection(col_id)
    
    if total_items == 0:
        await message.reply_text("האוספים שלך ריקים. אין מה למחוק.")
        return

    context.user_data["delete_mode"] = True
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📂 בחר אוסף לדפדוף ומחיקה", callback_data="delete_choose_collection")],
        [InlineKeyboardButton("🚪 צא ממצב מחיקה", callback_data="exit_delete_mode")]
    ])
    
    text = (
        "מצב מחיקה פעיל.\n"
        "שלח עכשיו וידאו, תמונה, מסמך שברצונך למחוק מהמאגר\n"
        "או שלח הודעת טקסט שמכילה רק מספר id פנימי למחיקה לפי id."
    )
    
    if edit_message_id:
        await context.bot.edit_message_text(
            chat_id=message.chat_id,
            message_id=edit_message_id,
            text=text,
            reply_markup=keyboard
        )
    else:
        await message.reply_text(text, reply_markup=keyboard)


async def remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    reset_user_modes(context)
    
    await remove_flow(update.message, user, context, context.args)


async def id_file_flow(message, user, context, edit_message_id: int = None):
    context.user_data["id_mode"] = True
    text = (
        "מצב זיהוי קבצים הופעל.\n"
        "שלח עכשיו וידאו, תמונה או מסמך כדי לקבל את ה file_id שלו,\n"
        "או שלח לי טקסט שהוא file_id ואני אנסה לשלוח את הקובץ חזרה.\n"
    )
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅ חזור לתפריט ראשי", callback_data="back_to_main")]
    ])
    
    if edit_message_id:
        await context.bot.edit_message_text(
            chat_id=message.chat_id,
            message_id=edit_message_id,
            text=text,
            reply_markup=keyboard
        )
    else:
        await message.reply_text(text, reply_markup=keyboard)


async def id_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    reset_user_modes(context)
    await id_file_flow(update.message, user, context)


async def handle_main_menu_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user = query.from_user
    action = query.data.split(":")[1]
    message_id = query.message.message_id
    
    reset_user_modes(context)
    
    if action == "newcollection":
        await new_collection_flow(query.message, user, context, args=[], edit_message_id=message_id)
    elif action == "collections":
        await list_collections_flow(query.message, user, context, edit_message_id=message_id)
    elif action == "browse":
        await show_browse_menu(query.message.chat_id, user.id, context, edit_message_id=message_id)
    elif action == "manage":
        await manage_collections_flow(query.message, user, context, edit_message_id=message_id)
    elif action == "remove":
        await remove_flow(query.message, user, context, args=[], edit_message_id=message_id)
    elif action == "id_file":
        await id_file_flow(query.message, user, context, edit_message_id=message_id)


async def handle_send_collection_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    Handle send collection confirmation logic.
    Returns True if the message was handled (confirmation process), False otherwise.
    """
    message = update.message
    user = update.effective_user
    
    # Use helper function to verify code
    is_valid, data = verify_user_code(message, context, "send_collection")
    
    if data is None:
        return False
    
    if is_valid:
        # Code is correct - send entire collection
        collection_id = data["collection_id"]
        collection_name = data["collection_name"]
        total_items = data["total_items"]
        
        chat_id = message.chat_id

        logger.info(
            "Sending entire collection after confirmation: user_id=%s collection_id=%s total_items=%s",
            user.id,
            collection_id,
            total_items,
        )

        await message.reply_text(
            f'✅ קוד אומת!\n'
            f'📦 שולח {total_items} קבצים מהאוסף "{collection_name}"...'
        )

        # Now send everything in chunks, reusing page-send logic
        block_size = 100
        offset = 0

        while offset < total_items:
            items_block = db.get_items_by_collection(collection_id, offset=offset, limit=block_size)

            # Use helper functions to prepare and send media
            media_visual, media_docs = prepare_media_groups(items_block)
            await send_media_groups_in_chunks(context.bot, chat_id, media_visual, media_docs)

            offset += block_size
            
            # Add delay between blocks to avoid flood control when sending large collections
            if offset < total_items:
                await asyncio.sleep(3)

        await message.reply_text(
            f'✅ סיימתי לשלוח את כל הקבצים מהאוסף "{collection_name}"!'
        )
        
        return True
    else:
        await message.reply_text("❌ הקוד שגוי. השליחה בוטלה.")
        return True


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if not message:
        return

    user = update.effective_user
    db.upsert_user(user.id, user.username, user.first_name, user.last_name)
    
    # Check if user is blocked
    user_info = db.get_user(user.id)
    if user_info and user_info.blocked == 1:
        return
    
    # בדיקה אם יש קוד שיתוף ממתין
    if await handle_share_code_input(update, context):
        return

    # בדיקה אם יש אישור מחיקת אוסף ממתין
    if await handle_delete_confirmation(update, context):
        return

    # בדיקה אם יש אישור שליחת אוסף ממתין
    if await handle_send_collection_confirmation(update, context):
        return


    if context.user_data.get("delete_mode"):
        await handle_delete_message(update, context)
        return

    if context.user_data.get("id_mode"):
        await handle_id_file_message(update, context)
        return

    if user.id not in active_collections:
        await message.reply_text("אין אוסף פעיל. השתמש ב /collections או /newcollection קודם.")
        return

    collection_id = active_collections[user.id]
    collection = db.get_collection_by_id(collection_id)
    collection_name = collection[1] if collection else "ללא שם"
    saved = False

    # Extract caption if present and validate length
    caption = message.caption if message.caption else None
    if caption and len(caption) > MAX_CAPTION_LENGTH:
        await message.reply_text(
            f"❌ הכיתוב ארוך מדי. מקסימום {MAX_CAPTION_LENGTH} תווים מותרים.\n"
            f"הכיתוב שלך: {len(caption)} תווים."
        )
        return

    if message.video:
        video = message.video
        
        # Check for duplicates
        if db.is_duplicate_file(collection_id, video.file_id, video.file_size):
            logger.info(
                "Duplicate file skipped: user_id=%s collection_id=%s type=video file_id=%s file_size=%s",
                user.id, collection_id, video.file_id, video.file_size
            )
            return  # Skip silently
        
        db.add_item(
            collection_id,
            content_type="video",
            file_id=video.file_id,
            text_content=caption,
            file_name=video.file_name,
            file_size=video.file_size,
        )
        saved = True
    elif message.photo:
        photo_size = message.photo[-1]
        
        # Check for duplicates
        if db.is_duplicate_file(collection_id, photo_size.file_id, photo_size.file_size):
            logger.info(
                "Duplicate file skipped: user_id=%s collection_id=%s type=photo file_id=%s file_size=%s",
                user.id, collection_id, photo_size.file_id, photo_size.file_size
            )
            return  # Skip silently
        
        db.add_item(
            collection_id,
            content_type="photo",
            file_id=photo_size.file_id,
            text_content=caption,
            file_name=None,
            file_size=photo_size.file_size,
        )
        saved = True
    elif message.document:
        doc = message.document
        
        # Check for duplicates
        if db.is_duplicate_file(collection_id, doc.file_id, doc.file_size):
            logger.info(
                "Duplicate file skipped: user_id=%s collection_id=%s type=document file_id=%s file_size=%s",
                user.id, collection_id, doc.file_id, doc.file_size
            )
            return  # Skip silently
        
        db.add_item(
            collection_id,
            content_type="document",
            file_id=doc.file_id,
            text_content=caption,
            file_name=doc.file_name,
            file_size=doc.file_size,
        )
        saved = True
    elif message.audio:
        audio = message.audio
        
        # Check for duplicates
        if db.is_duplicate_file(collection_id, audio.file_id, audio.file_size):
            logger.info(
                "Duplicate file skipped: user_id=%s collection_id=%s type=audio file_id=%s file_size=%s",
                user.id, collection_id, audio.file_id, audio.file_size
            )
            return  # Skip silently
        
        db.add_item(
            collection_id,
            content_type="audio",
            file_id=audio.file_id,
            text_content=caption,
            file_name=audio.file_name,
            file_size=audio.file_size,
        )
        saved = True
    elif message.text and not message.text.startswith("/"):
        text_content = message.text
        db.add_item(
            collection_id,
            content_type="text",
            text_content=text_content,
        )
        saved = True
    else:
        await message.reply_text("הודעה זו אינה סוג נתמך לשמירה.")

    if saved:
        logger.info(
            "Saved item: user_id=%s username=%s collection_id=%s type=%s file_id=%s file_name=%s file_size=%s text_len=%s",
            user.id,
            getattr(user, "username", None),
            collection_id,
            (
                "video" if message.video else
                "photo" if message.photo else
                "document" if message.document else
                "text" if message.text and not message.text.startswith("/") else
                "unknown"
            ),
            (
                message.video.file_id if message.video else
                message.photo[-1].file_id if message.photo else
                message.document.file_id if message.document else
                None
            ),
            (
                message.video.file_name if message.video else
                message.document.file_name if message.document else
                None
            ),
            (
                message.video.file_size if message.video else
                message.photo[-1].file_size if message.photo else
                message.document.file_size if message.document else
                None
            ),
            len(message.text) if message.text and not message.text.startswith("/") else None,
        )

        await update_batch_status(message, context, collection_name)


async def handle_delete_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    
    # מצב המחיקה נשאר פעיל - רק כפתור היציאה מסגר אותו
    
    delete_mode_keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🚪 צא ממצב מחיקה", callback_data="exit_delete_mode")],
        [InlineKeyboardButton("📂 בחר אוסף אחר למחיקה", callback_data="delete_choose_collection")]
    ])

    if message.text and not message.text.startswith("/"):
        text = message.text.strip()
        if text.isdigit():
            item_id = int(text)
            deleted = db.delete_item_by_id(item_id, update.effective_user.id)
            if deleted > 0:
                await message.reply_text(
                    f"✅ נמחק פריט אחד עם id {item_id}.",
                    reply_markup=delete_mode_keyboard
                )
            else:
                await message.reply_text(
                    "לא נמצא פריט עם id הזה.",
                    reply_markup=delete_mode_keyboard
                )
        else:
            await message.reply_text(
                "הטקסט לא מספר תקין ולכן לא בוצעה מחיקה.",
                reply_markup=delete_mode_keyboard
            )
        return

    file_id = None
    if message.video:
        file_id = message.video.file_id
    elif message.photo:
        photo_size = message.photo[-1]
        file_id = photo_size.file_id
    elif message.document:
        file_id = message.document.file_id

    if not file_id:
        await message.reply_text(
            "לא זיהיתי קובץ למחיקה.",
            reply_markup=delete_mode_keyboard
        )
        return

    deleted = db.delete_items_by_file_id(file_id, update.effective_user.id)
    if deleted > 0:
        await message.reply_text(
            f"✅ נמחקו {deleted} פריטים עם אותו קובץ.",
            reply_markup=delete_mode_keyboard
        )
    else:
        await message.reply_text(
            "לא נמצא קובץ תואם במאגר.",
            reply_markup=delete_mode_keyboard
        )


async def handle_id_file_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if not message:
        return

    back_keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    text="⬅ חזרה לתפריט הראשי",
                    callback_data="back_to_main",
                )
            ]
        ]
    )

    if message.video:
        await message.reply_text(
            f"🎬 video file_id:\n{message.video.file_id}",
            reply_markup=back_keyboard,
        )
        return

    if message.photo:
        photo = message.photo[-1]
        await message.reply_text(
            f"🖼 photo file_id:\n{photo.file_id}",
            reply_markup=back_keyboard,
        )
        return

    if message.document:
        await message.reply_text(
            f"📄 document file_id:\n{message.document.file_id}",
            reply_markup=back_keyboard,
        )
        return

    if message.text:
        file_id = message.text.strip()
        chat_id = message.chat_id

        async def try_send():
            for sender in (
                lambda: context.bot.send_video(chat_id=chat_id, video=file_id),
                lambda: context.bot.send_photo(chat_id=chat_id, photo=file_id),
                lambda: context.bot.send_document(chat_id=chat_id, document=file_id),
            ):
                try:
                    await sender()
                    return True
                except Exception:
                    pass
            return False

        success = await try_send()
        if not success:
            await message.reply_text(
                "לא הצלחתי לשלוח קובץ מה file_id הזה.",
                reply_markup=back_keyboard,
            )
        else:
            await message.reply_text(
                "✅ הקובץ נשלח בהצלחה מה file_id.",
                reply_markup=back_keyboard,
            )
        return

    await message.reply_text(
        "לא זיהיתי קובץ או file_id בהודעה הזאת.",
        reply_markup=back_keyboard,
    )


async def handle_back_to_main_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user = query.from_user
    context.user_data["id_mode"] = False

    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass

    await send_main_menu(query.message.chat_id, context)


# Collection Management Handlers

async def handle_manage_collection_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show management options for a specific collection"""
    query = update.callback_query
    await query.answer()
    
    user = query.from_user
    data = query.data  # format: manage_collection:<collection_id>
    
    _, col_id_str = data.split(":")
    collection_id = int(col_id_str)
    
    is_allowed, error_msg, collection = check_collection_access(user.id, collection_id)
    if not is_allowed:
        await query.edit_message_text(error_msg)
        return
    
    collection_name = collection[1]
    
    # Check if share code exists
    share_code = db.get_share_code_for_collection(collection_id)
    share_text = f"🔗 {share_code}" if share_code else "🔗 שיתוף אוסף"
    
    keyboard = [
        [InlineKeyboardButton(share_text, callback_data=f"share_collection:{collection_id}")],
        [InlineKeyboardButton("📤 ייצוא לקובץ TXT", callback_data=f"export_collection:{collection_id}")],
        [InlineKeyboardButton("🗑 מחיקת אוסף", callback_data=f"delete_collection:{collection_id}")],
        [InlineKeyboardButton("⬅️ חזור", callback_data="back_to_manage")]
    ]
    
    await query.edit_message_text(
        f"ניהול אוסף: {collection_name}\n\nבחר פעולה:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def handle_share_collection_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generate or display share code for a collection"""
    query = update.callback_query
    await query.answer()
    
    user = query.from_user
    data = query.data  # format: share_collection:<collection_id>
    
    _, col_id_str = data.split(":")
    collection_id = int(col_id_str)
    
    is_allowed, error_msg, collection = check_collection_access(user.id, collection_id)
    if not is_allowed:
        await query.edit_message_text(error_msg)
        return
    
    # Only owner can share
    if collection[2] != user.id:
        await query.answer("רק בעל האוסף יכול לשתף אותו", show_alert=True)
        return
    
    collection_name = collection[1]
    
    # Get or create share code
    share_code = db.create_share_link(collection_id, user.id)
    
    # Get access logs
    logs = db.get_share_access_logs(collection_id)
    access_count = len(logs)
    
    message_text = (
        f"🔗 קוד שיתוף לאוסף: {collection_name}\n\n"
        f"📋 קוד: `{share_code}`\n\n"
        f"👥 מספר גישות: {access_count}\n\n"
        f"💡 שלח את הקוד הזה למשתמשים אחרים.\n"
        f"הם יוכלו לגשת לאוסף באמצעות הכפתור /access."
    )
    
    keyboard = [
        [InlineKeyboardButton("📊 סטטיסטיקות גישה", callback_data=f"share_stats:{collection_id}")],
        [InlineKeyboardButton("🔄 חידוש קוד", callback_data=f"regenerate_share:{collection_id}")],
        [InlineKeyboardButton("❌ ביטול שיתוף", callback_data=f"revoke_share:{collection_id}")],
        [InlineKeyboardButton("⬅️ חזור", callback_data=f"manage_collection:{collection_id}")]
    ]
    
    await query.edit_message_text(
        message_text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )


async def handle_share_stats_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show access statistics for a shared collection"""
    query = update.callback_query
    await query.answer()
    
    user = query.from_user
    data = query.data  # format: share_stats:<collection_id>
    
    _, col_id_str = data.split(":")
    collection_id = int(col_id_str)
    
    is_allowed, error_msg, collection = check_collection_access(user.id, collection_id)
    if not is_allowed:
        await query.edit_message_text(error_msg)
        return
    
    # Only owner can view stats
    if collection[2] != user.id:
        await query.answer("רק בעל האוסף יכול לצפות בסטטיסטיקות", show_alert=True)
        return
    
    collection_name = collection[1]
    logs = db.get_share_access_logs(collection_id)
    
    if not logs:
        message_text = f"📊 סטטיסטיקות גישה - {collection_name}\n\nאף אחד עדיין לא גישה לאוסף המשותף."
    else:
        message_text = f"📊 סטטיסטיקות גישה - {collection_name}\n\n"
        for user_id, username, first_name, accessed_at in logs[:10]:  # Show last 10
            user_display = first_name or username or f"User {user_id}"
            date_str = accessed_at[:16].replace("T", " ")  # Format: YYYY-MM-DD HH:MM
            message_text += f"• {user_display} - {date_str}\n"
        
        if len(logs) > 10:
            message_text += f"\n... ועוד {len(logs) - 10} גישות"
    
    keyboard = [[InlineKeyboardButton("⬅️ חזור", callback_data=f"share_collection:{collection_id}")]]
    
    await query.edit_message_text(
        message_text,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def handle_regenerate_share_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Regenerate share code for a collection"""
    query = update.callback_query
    await query.answer()
    
    user = query.from_user
    data = query.data  # format: regenerate_share:<collection_id>
    
    _, col_id_str = data.split(":")
    collection_id = int(col_id_str)
    
    is_allowed, error_msg, collection = check_collection_access(user.id, collection_id)
    if not is_allowed:
        await query.edit_message_text(error_msg)
        return
    
    # Only owner can regenerate
    if collection[2] != user.id:
        await query.answer("רק בעל האוסף יכול לחדש את הקוד", show_alert=True)
        return
    
    new_code = db.regenerate_share_code(collection_id, user.id)
    
    await query.answer("✅ קוד חדש נוצר!", show_alert=True)
    
    # Show the new code
    await handle_share_collection_callback(update, context)


async def handle_revoke_share_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Revoke share code for a collection"""
    query = update.callback_query
    await query.answer()
    
    user = query.from_user
    data = query.data  # format: revoke_share:<collection_id>
    
    _, col_id_str = data.split(":")
    collection_id = int(col_id_str)
    
    is_allowed, error_msg, collection = check_collection_access(user.id, collection_id)
    if not is_allowed:
        await query.edit_message_text(error_msg)
        return
    
    # Only owner can revoke
    if collection[2] != user.id:
        await query.answer("רק בעל האוסף יכול לבטל את השיתוף", show_alert=True)
        return
    
    success = db.revoke_share_code(collection_id, user.id)
    
    if success:
        await query.answer("✅ השיתוף בוטל!", show_alert=True)
        # Go back to manage menu
        context.user_data["temp_manage_collection"] = collection_id
        await handle_manage_collection_callback(update, context)
    else:
        await query.answer("❌ לא היה קוד שיתוף פעיל", show_alert=True)


async def handle_export_collection_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Export collection to TXT file"""
    query = update.callback_query
    await query.answer()
    
    user = query.from_user
    data = query.data  # format: export_collection:<collection_id>
    
    _, col_id_str = data.split(":")
    collection_id = int(col_id_str)
    
    is_allowed, error_msg, collection = check_collection_access(user.id, collection_id)
    if not is_allowed:
        await query.edit_message_text(error_msg)
        return
    
    collection_name = collection[1]
    
    # Get all items
    all_items = []
    offset = 0
    while True:
        items = db.get_items_by_collection(collection_id, offset=offset, limit=100)
        if not items:
            break
        all_items.extend(items)
        offset += 100
    
    if not all_items:
        await query.answer("האוסף ריק", show_alert=True)
        return
    
    # Create TXT content
    txt_content = f"Collection: {collection_name}\n"
    txt_content += f"Total items: {len(all_items)}\n"
    txt_content += "=" * 50 + "\n\n"
    
    for item_id, content_type, file_id, text_content, file_name, file_size, added_at in all_items:
        txt_content += f"ID: {item_id}\n"
        txt_content += f"Type: {content_type}\n"
        if file_id:
            txt_content += f"File ID: {file_id}\n"
        if file_name:
            txt_content += f"Filename: {file_name}\n"
        if file_size:
            txt_content += f"Size: {format_size(file_size)}\n"
        if text_content:
            txt_content += f"Caption: {text_content}\n"
        txt_content += f"Added: {added_at}\n"
        txt_content += "-" * 50 + "\n\n"
    
    # Send as document
    from io import BytesIO
    file_bytes = BytesIO(txt_content.encode('utf-8'))
    file_bytes.name = f"{collection_name}_export.txt"
    
    await context.bot.send_document(
        chat_id=query.message.chat_id,
        document=file_bytes,
        filename=f"{collection_name}_export.txt",
        caption=f"ייצוא אוסף: {collection_name}"
    )
    
    await query.answer("✅ הקובץ נשלח!", show_alert=True)


async def handle_delete_collection_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Delete a collection with confirmation"""
    query = update.callback_query
    await query.answer()
    
    user = query.from_user
    data = query.data  # format: delete_collection:<collection_id>
    
    _, col_id_str = data.split(":")
    collection_id = int(col_id_str)
    
    is_allowed, error_msg, collection = check_collection_access(user.id, collection_id)
    if not is_allowed:
        await query.edit_message_text(error_msg)
        return
    
    # Only owner can delete
    if collection[2] != user.id:
        await query.answer("רק בעל האוסף יכול למחוק אותו", show_alert=True)
        return
    
    collection_name = collection[1]
    item_count = db.count_items_in_collection(collection_id)
    
    # Use unified verification code mechanism
    data = {"collection_id": collection_id}
    verification_code = create_verification_code(context, "delete_collection", data)
    
    message_text = (
        f"⚠️ אזהרה: מחיקת אוסף\n\n"
        f"אוסף: {collection_name}\n"
        f"פריטים: {item_count}\n\n"
        f"🔢 קוד אימות: `{verification_code}`\n\n"
        f"שלח את הקוד הזה כדי לאשר את המחיקה."
    )
    
    keyboard = [[InlineKeyboardButton("❌ ביטול", callback_data=f"manage_collection:{collection_id}")]]
    
    await query.edit_message_text(
        message_text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )


async def handle_back_to_manage_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Go back to manage collections list"""
    query = update.callback_query
    await query.answer()
    
    user = query.from_user
    db.upsert_user(user.id, user.username, user.first_name, user.last_name)
    
    collections = db.get_collections(user.id)
    if not collections:
        await query.edit_message_text(MSG_NO_COLLECTIONS)
        return
    
    keyboard = build_collection_keyboard(collections, "manage_collection", add_back_button=True)
    
    await query.edit_message_text(
        "בחר אוסף לניהול:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# Shared Collection Access Handlers

async def access_shared(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Command to access a shared collection via code"""
    user = update.effective_user
    track_and_reset_user(user, context)
    
    # Set mode to wait for share code
    context.user_data["waiting_for_share_code"] = True
    
    keyboard = [[InlineKeyboardButton("❌ ביטול", callback_data="cancel_share_access")]]
    
    await update.message.reply_text(
        "🔑 גישה לאוסף משותף\n\n"
        "שלח את קוד השיתוף שקיבלת:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def handle_share_code_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Handle share code input from user. Returns True if handled."""
    if not context.user_data.get("waiting_for_share_code"):
        return False
    
    message = update.message
    if not message or not message.text:
        return False
    
    user = message.from_user
    share_code = message.text.strip()
    
    # Clear waiting state
    context.user_data.pop("waiting_for_share_code", None)
    
    # Validate share code
    collection_info = db.get_collection_by_share_code(share_code)
    
    if not collection_info:
        await message.reply_text(
            "❌ קוד שיתוף לא תקין או שפג תוקפו.\n\n"
            "ודא שהקוד נכון ושהשיתוף עדיין פעיל."
        )
        return True
    
    collection_id, collection_name, owner_id = collection_info
    
    # Log access
    db.log_share_access(share_code, user.id)
    
    # Save shared collection state
    active_shared_collections[user.id] = share_code
    
    # Open browse page directly
    await message.reply_text(
        f"✅ גישה לאוסף משותף: 🔗 {collection_name}\n\n"
        f"אתה יכול כעת לצפות ולשלוח קבצים מהאוסף.\n"
        f"לא ניתן להוסיף קבצים חדשים לאוסף משותף."
    )
    
    # Simulate browse_page callback to show the collection
    from telegram import CallbackQuery
    
    # Create a fake query to reuse browse_page logic
    block_size = 100
    page = 1
    
    # Use helper for pagination
    prefix = f"🔗 אוסף משותף: {collection_name}\n\n"
    header_text, total_items, total_pages, items_in_block, page, _ = get_page_header(
        collection_id, page, block_size, page_prefix=prefix
    )

    if total_items == 0:
        await message.reply_text("האוסף המשותף ריק כרגע.")
        return True
    
    reply_markup = build_page_menu(
        collection_id=collection_id,
        page=page,
        total_pages=total_pages,
        items_in_block=items_in_block,
        group_size=10,
    )
    
    # Add exit shared collection button
    keyboard_list = list(reply_markup.inline_keyboard)
    keyboard_list.append([InlineKeyboardButton("❌ יציאה מאוסף משותף", callback_data="exit_shared_collection")])
    reply_markup = InlineKeyboardMarkup(keyboard_list)
    
    await message.reply_text(
        text=header_text,
        reply_markup=reply_markup
    )
    
    return True


async def handle_exit_shared_collection_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Exit from viewing a shared collection"""
    query = update.callback_query
    await query.answer()
    
    user = query.from_user
    
    # Clear shared collection state
    if user.id in active_shared_collections:
        active_shared_collections.pop(user.id)
    
    # Edit existing message to main menu instead of sending new message
    text = get_main_menu_text()
    keyboard = build_main_menu_keyboard()
    
    await query.edit_message_text(
        text=text,
        reply_markup=keyboard
    )
    
    # Update main_menu_msg_id to reference this message for future edits
    context.user_data["main_menu_msg_id"] = query.message.message_id


async def handle_cancel_share_access_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel share code input"""
    query = update.callback_query
    await query.answer()
    
    context.user_data.pop("waiting_for_share_code", None)
    
    await query.edit_message_text("❌ בוטל.")


async def handle_delete_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Handle collection deletion confirmation. Returns True if handled."""
    # Use unified verification check
    is_valid, data = verify_user_code(update.message, context, "delete_collection")
    
    if is_valid and data:
        collection_id = data["collection_id"]
        
        logger.info(f"User {update.message.from_user.id} confirmed deletion of collection {collection_id}")
        
        # Delete all items first
        items_deleted = db.delete_all_items_in_collection(collection_id)
        logger.info(f"Deleted {items_deleted} items from collection {collection_id}")
        
        # Delete collection
        success = db.delete_collection(collection_id)
        
        if success:
            logger.info(f"Collection {collection_id} successfully deleted by user {update.message.from_user.id}")
            await update.message.reply_text("✅ האוסף נמחק בהצלחה!")
            await send_main_menu(update.message.chat_id, context)
        else:
            logger.error(f"Failed to delete collection {collection_id} - check db.py logs for details")
            await update.message.reply_text("❌ שגיאה במחיקת האוסף. נא לנסות שוב או ליצור קשר עם התמיכה.")
        
        return True
    
    # Check if we were supposed to be verifying but failed
    if "verify_delete_collection" in context.user_data:
        await update.message.reply_text("❌ הקוד שגוי. המחיקה בוטלה.")
        context.user_data.pop("verify_delete_collection", None)
        return True
        
    return False

async def handle_exit_delete_mode_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Exit from delete mode"""
    query = update.callback_query
    await query.answer()
    
    context.user_data.pop("delete_mode", None)
    
    # Show main menu directly instead of separate message
    await query.edit_message_text(
        get_main_menu_text(),
        reply_markup=build_main_menu_keyboard()
    )
    context.user_data["main_menu_msg_id"] = query.message.message_id


async def handle_delete_choose_collection_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show collections list to choose for deletion"""
    query = update.callback_query
    await query.answer()
    
    user = query.from_user
    collections = db.get_collections(user.id)
    
    if not collections:
        await query.edit_message_text("אין לך אוספים.")
        return
    
    keyboard = [
        [InlineKeyboardButton(text=f"📁 {name}", callback_data=f"browse_page:{col_id}:1")]
        for col_id, name in collections
    ]
    keyboard.append([InlineKeyboardButton("🚪 צא ממצב מחיקה", callback_data="exit_delete_mode")])
    
    await query.edit_message_text(
        "📂 בחר אוסף לדפדוף ומחיקה:\n\n"
        "(מצב המחיקה עדיין פעיל - שלח קובץ למחיקה)",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

def main():

    db.init_db()

    app = ApplicationBuilder().token(BOT_TOKEN).rate_limiter(AIORateLimiter()).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("newcollection", new_collection))
    app.add_handler(CommandHandler("collections", list_collections))
    app.add_handler(CommandHandler("browse", browse))
    app.add_handler(CommandHandler("remove", remove))
    app.add_handler(CommandHandler("id_file", id_file))
    app.add_handler(CommandHandler("manage", manage_collections))
    app.add_handler(CommandHandler("adminpanel", admin_panel))

    app.add_handler(CallbackQueryHandler(handle_admin_callback, pattern=r"^admin_"))
    
    app.add_handler(CallbackQueryHandler(handle_select_collection_callback, pattern=r"^select_collection:"))
    app.add_handler(CallbackQueryHandler(handle_browse_page_callback, pattern=r"^browse_page:"))
    app.add_handler(
        CallbackQueryHandler(
            handle_browse_group_or_select_all_callback,
            pattern=r"^(browse_group:|browse_page_select_all:)",
        )
    )
    app.add_handler(CallbackQueryHandler(handle_page_file_send_choice_callback, pattern=r"^page_files_"))
    app.add_handler(CallbackQueryHandler(handle_collection_send_all_callback, pattern=r"^collection_send_all:"))
    app.add_handler(CallbackQueryHandler(handle_batch_status_callback, pattern=r"^batch_status:"))
    app.add_handler(CallbackQueryHandler(handle_stop_collect_callback, pattern=r"^stop_collect$"))
    app.add_handler(CallbackQueryHandler(handle_back_to_main_callback, pattern=r"^back_to_main$"))
    app.add_handler(CallbackQueryHandler(handle_main_menu_button, pattern=r"^main_menu:"))
    
    async def handle_back_to_browse(update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        
        user = query.from_user
        db.upsert_user(user.id, user.username, user.first_name, user.last_name)
        
        await show_browse_menu(
            chat_id=query.message.chat_id,
            user_id=user.id,
            context=context,
            edit_message_id=query.message.message_id
        )
        
    app.add_handler(CallbackQueryHandler(handle_back_to_browse, pattern=r"^back_to_browse$"))
    

    app.add_handler(CallbackQueryHandler(handle_manage_collection_callback, pattern=r"^manage_collection:"))
    app.add_handler(CallbackQueryHandler(handle_export_collection_callback, pattern=r"^export_collection:"))
    app.add_handler(CallbackQueryHandler(handle_delete_collection_callback, pattern=r"^delete_collection:"))
    app.add_handler(CallbackQueryHandler(handle_back_to_manage_callback, pattern=r"^back_to_manage$"))
    

    app.add_handler(CallbackQueryHandler(handle_exit_delete_mode_callback, pattern=r"^exit_delete_mode$"))
    app.add_handler(CallbackQueryHandler(handle_delete_choose_collection_callback, pattern=r"^delete_choose_collection$"))
    

    app.add_handler(CommandHandler("access", access_shared))
    app.add_handler(CallbackQueryHandler(handle_share_collection_callback, pattern=r"^share_collection:"))
    app.add_handler(CallbackQueryHandler(handle_share_stats_callback, pattern=r"^share_stats:"))
    app.add_handler(CallbackQueryHandler(handle_regenerate_share_callback, pattern=r"^regenerate_share:"))
    app.add_handler(CallbackQueryHandler(handle_revoke_share_callback, pattern=r"^revoke_share:"))
    app.add_handler(CallbackQueryHandler(handle_exit_shared_collection_callback, pattern=r"^exit_shared_collection$"))
    app.add_handler(CallbackQueryHandler(handle_cancel_share_access_callback, pattern=r"^cancel_share_access$"))

    app.add_handler(MessageHandler(filters.ALL & (~filters.COMMAND), handle_message))

    app.add_error_handler(error_handler)

    try:
        app.run_polling()
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Error occurred: {e}", exc_info=True)

if __name__ == "__main__":
    main()