import logging
import math
import asyncio
import sys
from typing import Dict
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InputMediaVideo,
    InputMediaPhoto,
    InputMediaDocument,
)
from telegram.ext import ContextTypes
from telegram.error import NetworkError
import db
from config import ADMIN_IDS, is_admin
from constants import MSG_NO_COLLECTIONS, active_collections, active_shared_collections

logger = logging.getLogger(__name__)

# Custom filter - only user action logs and errors
class UserActionFilter(logging.Filter):
    def filter(self, record):
        # Allow only WARNING and above or logs from our bot (__main__ or handlers)
        if record.levelno >= logging.WARNING:  # Errors always
            return True
        # Only logs from our bot packages
        return record.name == "__main__" or record.name.startswith("handlers")


def parse_callback_data(data: str, prefix: str = None) -> list[str] | None:
    """
    Parses callback data string.
    If prefix is provided, verifies it matches.
    Returns list of parts (excluding prefix) or None if invalid.
    """
    if not data:
        return None
    
    parts = data.split(":")

    if prefix:
        if not data.startswith(prefix):
            return None
        
        # If the first part matches prefix exactly, remove it
        if parts[0] == prefix:
            return parts[1:]
        
    return parts


async def validate_access_wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, collection_id: int) -> tuple[bool, object]:
    """
    Wrapper for check_collection_access that handles user feedback.
    Returns (is_allowed, collection_obj).
    """
    if hasattr(update, 'callback_query') and update.callback_query:
        user_id = update.callback_query.from_user.id
        message_func = update.callback_query.edit_message_text
    else:
        user_id = update.effective_user.id
        message_func = update.effective_chat.send_message if update.effective_chat else None

    is_allowed, error_msg, collection = check_collection_access(user_id, collection_id)
    
    if not is_allowed and message_func:
        try:
            await message_func(text=error_msg)
        except Exception:
             # Fallback if edit fails (e.g. message too old)
             if update.effective_chat:
                 await update.effective_chat.send_message(text=error_msg)
    
    return is_allowed, collection

def extract_file_info(message):
    """
    Extracts file information (content_type, file_id, file_name, file_size, text_content) from a message.
    Returns dict or None if no supported content found.
    """
    content_type = None
    file_id = None
    text_content = message.caption or message.text or ""
    f_name = None
    f_size = 0
    
    if message.photo:
        content_type = "photo"
        file_id = message.photo[-1].file_id
        f_size = message.photo[-1].file_size
        
    elif message.video:
        content_type = "video"
        file_id = message.video.file_id
        f_name = message.video.file_name
        f_size = message.video.file_size
        
    elif message.document:
        content_type = "document"
        file_id = message.document.file_id
        f_name = message.document.file_name
        f_size = message.document.file_size
        
    elif message.audio:
        content_type = "audio"
        file_id = message.audio.file_id
        f_name = message.audio.file_name
        f_size = message.audio.file_size
        
    elif message.text:
        content_type = "text"
        text_content = message.text
        
    if not content_type:
        return None
        
    return {
        "content_type": content_type,
        "file_id": file_id,
        "text_content": text_content,
        "file_name": f_name,
        "file_size": f_size
    }

def record_activity(func):
    """Decorator to track user activity and reset modes"""
    import functools
    @functools.wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user = update.effective_user
        track_and_reset_user(user, context)
        return await func(update, context, *args, **kwargs)
    return wrapper

async def send_response(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, keyboard=None, edit_message_id: int = None, parse_mode=None):
    """
    Helper function to send or edit a message.
    Reduces code duplication by handling both cases in one place.
    """
    if update.effective_chat:
        chat_id = update.effective_chat.id
    else:
        # Fallback if no effective chat (rare)
        return

    if edit_message_id:
        try:
            return await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=edit_message_id,
                text=text,
                reply_markup=keyboard,
                parse_mode=parse_mode
            )
        except Exception as e:
            logger.debug(f"Failed to edit message {edit_message_id}: {e}")
            # Fall through to send new message
    
    # Send new message
    return await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        reply_markup=keyboard,
        parse_mode=parse_mode
    )

def build_collection_keyboard(collections, callback_prefix: str, add_back_button: bool = False):
    """Build a keyboard with collection buttons"""
    keyboard = [
        [InlineKeyboardButton(text=f"📁 {name}", callback_data=f"{callback_prefix}:{col_id}")]
        for col_id, name in collections
    ]
    if add_back_button:
        keyboard.append([InlineKeyboardButton("🏠 חזור לתפריט ראשי", callback_data="back_to_main")])
    return keyboard

async def show_collections_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int, callback_prefix: str, text: str, edit_message_id: int = None, extra_buttons: list = None):
    """
    Helper to show a list of collections as a menu.
    Reduces code duplication for collection selection flows.
    """
    collections = db.get_collections(user_id)
    
    if not collections:
        await send_response(update, context, MSG_NO_COLLECTIONS, edit_message_id=edit_message_id)
        return
    
    keyboard = build_collection_keyboard(collections, callback_prefix, add_back_button=True)
    
    if extra_buttons:
        # Prepend extra buttons (like Import)
        for btn_row in reversed(extra_buttons):
            keyboard.insert(0, btn_row)
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await send_response(update, context, text, reply_markup, edit_message_id)

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    err = context.error

    # Network errors like httpx.ReadError, timeouts etc - ignore in logs
    if isinstance(err, NetworkError) or "ReadError" in str(err):
        logger.warning(f"Network issue ignored: {err}")
        return

    # All other errors - log with stacktrace
    logger.exception("Exception while handling update", exc_info=err)

def reset_user_modes(context: ContextTypes.DEFAULT_TYPE):
    """Reset all user modes when a new command is issued"""
    for key in ["delete_mode", "id_mode", "waiting_for_share_code", 
                "verify_delete_collection", "verify_send_collection",
                "import_mode", "creating_collection_mode", "temp_collection_name", "allowed_item_ids", "info_page_collection_id",
                "item_delete_mode", "delete_target_collection_id"]:
        context.user_data.pop(key, None)

def track_and_reset_user(user, context: ContextTypes.DEFAULT_TYPE):
    """Track user in DB and reset all modes"""
    reset_user_modes(context)
    if user:
        db.upsert_user(user.id, user.username, user.first_name, user.last_name)

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

def prepare_media_groups(items: list) -> tuple[list, list, list]:
    """
    Prepare media items into visual and document groups.
    """
    media_visual = []
    media_docs = []
    text_items = []
    
    for item_id, content_type, file_id, text_content, file_name, file_size, added_at in items:
        # Handle text items (no file_id)
        if content_type == "text" or (not file_id and text_content):
            text_items.append(text_content)
            continue
            
        if not file_id:
            continue
            
        if content_type == "video":
            media_visual.append(InputMediaVideo(media=file_id, caption=text_content))
        elif content_type == "photo":
            media_visual.append(InputMediaPhoto(media=file_id, caption=text_content))
        elif content_type == "document":
            media_docs.append(InputMediaDocument(media=file_id, filename=file_name, caption=text_content))
    
    return media_visual, media_docs, text_items

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
            await asyncio.sleep(5) 
            try:
                await bot.send_media_group(chat_id=chat_id, media=media, reply_to_message_id=reply_to_message_id)
            except Exception as retry_e:
                logger.error(f"Failed to send media group after retry: {retry_e}")
        else:
            logger.error(f"Error sending media group: {e}")

async def send_media_groups_in_chunks(bot, chat_id: int, media_visual: list, media_docs: list, text_items: list = None):
    """
    Send media groups in chunks of 10 to avoid flood limits.
    Also sends text messages.
    """
    if text_items:
        for text in text_items:
            try:
                await bot.send_message(chat_id=chat_id, text=text)
                await asyncio.sleep(0.5) # Small delay
            except Exception as e:
                logger.error(f"Error sending text item: {e}")

    for i in range(0, len(media_visual), 10):
        chunk = media_visual[i:i + 10]
        await safe_send_media_group(bot, chat_id=chat_id, media=chunk)
        if i + 10 < len(media_visual):
            await asyncio.sleep(4)
    
    for i in range(0, len(media_docs), 10):
        chunk = media_docs[i:i + 10]
        await safe_send_media_group(bot, chat_id=chat_id, media=chunk)
        if i + 10 < len(media_docs):
            await asyncio.sleep(4)

def get_page_header(collection_id: int, page: int, block_size: int = 100, page_prefix: str = "") -> tuple[str, int, int, int, int, list]:
    """
    Calculate pagination details and generate header text.
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

def build_main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📁 יצירת אוסף חדש", callback_data="main_menu:newcollection")],

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

async def batch_status_loop(chat_id: int, collection_id: int, collection_name: str, context: ContextTypes.DEFAULT_TYPE, user_data_status: dict):
    """Background loop to update status message every few seconds"""
    try:
        while True:
            current_count = user_data_status["count"]
            last_sent_count = user_data_status.get("last_sent_count", 0)
            
            # If count changed or message doesn't exist, send update
            if current_count != last_sent_count or not user_data_status.get("msg_id"):
                
                # Delete old message if exists
                old_msg_id = user_data_status.get("msg_id")
                if old_msg_id:
                    try:
                        await context.bot.delete_message(chat_id=chat_id, message_id=old_msg_id)
                    except Exception:
                        pass
                
                # Send new message
                text = f"✅ נוספו קבצים לאוסף \"{collection_name}\""
                keyboard = InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton("📊 מצב איסוף", callback_data=f"batch_status:{collection_id}"),
                        InlineKeyboardButton("🏠 חזרה לבית", callback_data="back_to_main")
                    ]
                ])
                
                try:
                    msg = await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=keyboard)
                    user_data_status["msg_id"] = msg.message_id
                    user_data_status["last_sent_count"] = current_count
                except Exception as e:
                    logger.error(f"Error sending batch status: {e}")
            
            # Wait 2 seconds
            await asyncio.sleep(2)
            
            if user_data_status["count"] == user_data_status["last_sent_count"]:
                # No new files in last 2 seconds, stop loop
                user_data_status["is_updating"] = False
                break
                
    except Exception as e:
        logger.error(f"Error in batch_status_loop: {e}")
        user_data_status["is_updating"] = False

async def update_batch_status(message, context: ContextTypes.DEFAULT_TYPE, collection_name: str):
    """Update file collection status message - throttled system"""
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
            "last_sent_count": 0,
            "is_updating": False
        }
    
    # Update count
    status = user_data["batch_status"][collection_id]
    status["count"] += 1
    
    # Start loop if not running
    if not status["is_updating"]:
        status["is_updating"] = True
        context.application.create_task(
            batch_status_loop(
                chat_id=message.chat_id,
                collection_id=collection_id,
                collection_name=collection_name,
                context=context,
                user_data_status=status
            )
        )

async def delete_message_after_delay(bot, chat_id: int, message_id: int, delay: int):
    """Delete a message after delay"""
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
    """Browsing menu: Select All and below numbers representing groups of items"""

    # First row: Select All
    row_select_all = [
        InlineKeyboardButton(
            "✳ בחר הכל",
            callback_data=f"browse_page_select_all:{collection_id}:{page}",
        )
    ]

    # How many groups needed in this page
    groups_count = math.ceil(items_in_block / group_size)
    groups_count = min(groups_count, 10)  # Up to 10 groups per page

    row_numbers_1: list[InlineKeyboardButton] = []
    row_numbers_2: list[InlineKeyboardButton] = []

    # Base for number display according to page
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

    # Navigation between 100-item pages
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
        [
            InlineKeyboardButton(
                text="⬅️ חזור",
                callback_data=f"browse_page:{collection_id}:{page}",
            ),
        ],
    ]

    return InlineKeyboardMarkup(keyboard)

async def show_collection_page(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    collection_id: int,
    page: int,
    edit_message_id: int = None,
    force_resend: bool = False
):
    """
    Central function to display a collection browse page.
    Handles permissions, pagination, building the menu with all buttons,
    and sending/editing the message.
    """
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    # 1. Check access
    is_allowed, error_msg, collection = check_collection_access(user_id, collection_id)
    if not is_allowed:
        if edit_message_id and not force_resend:
             try:
                 await context.bot.edit_message_text(chat_id=chat_id, message_id=edit_message_id, text=error_msg)
             except:
                 await context.bot.send_message(chat_id=chat_id, text=error_msg)
        else:
             await context.bot.send_message(chat_id=chat_id, text=error_msg)
        return

    # 2. Pagination & Header
    block_size = 100
    group_size = 10
    
    header_text, total_items, total_pages, items_in_block, page, _ = get_page_header(
        collection_id, page, block_size
    )

    if total_items == 0:
        text = "אין פריטים באוסף הזה."
        if edit_message_id and not force_resend:
             try:
                 await context.bot.edit_message_text(chat_id=chat_id, message_id=edit_message_id, text=text)
             except:
                 await context.bot.send_message(chat_id=chat_id, text=text)
        else:
             await context.bot.send_message(chat_id=chat_id, text=text)
        return

    # 3. Build Menu (Numbers buttons)
    reply_markup = build_page_menu(
        collection_id=collection_id,
        page=page,
        total_pages=total_pages,
        items_in_block=items_in_block,
        group_size=group_size,
    )

    # 4. Add Extra Buttons (Scroll, Info, Navigation)
    keyboard_list = list(reply_markup.inline_keyboard)
    
    # Row: Scroll View | Info
    keyboard_list.append([
        InlineKeyboardButton("🔄 צפייה בגלילה", callback_data=f"scroll_view:{collection_id}:0"),
        InlineKeyboardButton("ℹ️ מידע", callback_data=f"page_info:{collection_id}:{page}:0")
    ])
    
    # Back button logic
    if is_admin(user_id) and collection[2] != user_id:
        # Admin viewing someone else's collection -> return to management of that collection
        keyboard_list.append([
            InlineKeyboardButton("⬅️ חזור לניהול האוסף", callback_data=f"admin_manage_col:{collection_id}")
        ])
    else:
        # Standard user or admin viewing own collection
        keyboard_list.append([
            InlineKeyboardButton("🏠 תפריט ראשי", callback_data="back_to_main")
        ])
    
    reply_markup = InlineKeyboardMarkup(keyboard_list)

    # 5. Send/Edit Logic
    if force_resend:
        # Calculate fresh header text incase items were deleted/changed outside (though get_page_header does count)
        if edit_message_id:
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=edit_message_id)
            except:
                pass
        await context.bot.send_message(chat_id=chat_id, text=header_text, reply_markup=reply_markup)
    else:
        # Try edit
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=edit_message_id,
                text=header_text,
                reply_markup=reply_markup
            )
        except Exception:
            # Edit failed (e.g. content same, or message too old/deleted, or type diff)
            # Try delete and send new
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=edit_message_id)
            except:
                pass
            await context.bot.send_message(chat_id=chat_id, text=header_text, reply_markup=reply_markup)

async def send_info_page(
    bot,
    chat_id: int,
    user_id: int,
    context: ContextTypes.DEFAULT_TYPE,
    collection_id: int,
    page: int,
    info_page: int,
    edit_message_id: int = None
):
    """Send or edit info page message showing file details for a collection."""
    block_size = 100
    info_group_size = 10

    # Get items for current page block
    offset_block = (page - 1) * block_size
    items_block = db.get_items_by_collection(collection_id, offset=offset_block, limit=block_size)
    
    if not items_block:
        text = "אין פריטים בעמוד זה."
        if edit_message_id:
            try:
                return await bot.edit_message_text(chat_id=chat_id, message_id=edit_message_id, text=text)
            except:
                pass
        return await bot.send_message(chat_id=chat_id, text=text)

    # Calculate info page bounds
    info_start = info_page * info_group_size
    info_end = info_start + info_group_size
    items_to_show = items_block[info_start:info_end]
    
    if not items_to_show:
        # Reset to first info page if out of bounds
        info_page = 0
        info_start = 0
        info_end = info_group_size
        items_to_show = items_block[info_start:info_end]

    total_info_pages = math.ceil(len(items_block) / info_group_size)
    
    # Build info text
    content_type_map = {
        "video": "🎬 וידאו",
        "photo": "🖼 תמונה", 
        "document": "📄 קובץ",
        "audio": "🎵 אודיו",
        "text": "📝 טקסט"
    }
    
    def escape_html(text):
        if not text:
            return text
        return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    
    info_text = f"📋 <b>מידע על קבצים - עמוד {page}</b>\n"
    info_text += f"מציג {info_start + 1}-{min(info_end, len(items_block))} מתוך {len(items_block)}\n\n"
    
    for item in items_to_show:
        item_id, content_type, file_id, text_content, file_name, file_size, added_at = item
        
        type_display = content_type_map.get(content_type, "📁 קובץ")
        name_display = escape_html(file_name) if file_name else "(ללא שם קובץ)"
        
        info_text += f"סוג: {type_display}\n"
        info_text += f"שם: {name_display}\n"
        info_text += f"ID: <code>{item_id}</code>\n"
        info_text += "─────────────────\n"
    
    # Store allowed IDs for this user (security - only allow IDs shown on current page)
    context.user_data["allowed_item_ids"] = [item[0] for item in items_to_show]
    context.user_data["info_page_collection_id"] = collection_id
    context.user_data["info_page_page"] = page
    context.user_data["info_page_info_page"] = info_page
    
    info_text += f"\n💡 <i>שלח את מספר ה-ID כדי לקבל את הקובץ</i>"
    
    # Build navigation keyboard
    nav_buttons = []
    if info_page > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️ הקודם", callback_data=f"page_info:{collection_id}:{page}:{info_page - 1}"))
    if info_page < total_info_pages - 1:
        nav_buttons.append(InlineKeyboardButton("הבא ➡️", callback_data=f"page_info:{collection_id}:{page}:{info_page + 1}"))
    
    keyboard = []
    if nav_buttons:
        keyboard.append(nav_buttons)
    keyboard.append([InlineKeyboardButton("🔙 חזור לתפריט דפדוף", callback_data=f"browse_page:{collection_id}:{page}")])
    
    # Check admin status for back button
    collection = db.get_collection_by_id(collection_id)
    if is_admin(user_id) and collection and collection[2] != user_id:
        keyboard.append([InlineKeyboardButton("⬅️ חזור לניהול האוסף", callback_data=f"admin_manage_col:{collection_id}")])
    else:
        keyboard.append([InlineKeyboardButton("🏠 חזור לתפריט ראשי", callback_data="back_to_main")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Edit or send message
    if edit_message_id:
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=edit_message_id,
                text=info_text,
                reply_markup=reply_markup,
                parse_mode="HTML"
            )
            # Save the message ID for later deletion
            context.user_data["info_message_id"] = edit_message_id
            return
        except Exception as e:
            logger.warning(f"Failed to edit info page: {e}")
            # Fall through to send new message
    
    # Send new message
    try:
        msg = await bot.send_message(
            chat_id=chat_id,
            text=info_text,
            reply_markup=reply_markup,
            parse_mode="HTML"
        )
        # Save the message ID for later deletion
        context.user_data["info_message_id"] = msg.message_id
        return msg
    except Exception as e:
        logger.warning(f"Failed to send info page with HTML: {e}")
        # Fallback: remove HTML tags
        plain_text = info_text.replace("<b>", "").replace("</b>", "")
        plain_text = plain_text.replace("<code>", "").replace("</code>", "")
        plain_text = plain_text.replace("<i>", "").replace("</i>", "")
        plain_text = plain_text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
        
        msg = await bot.send_message(
            chat_id=chat_id,
            text=plain_text,
            reply_markup=reply_markup
        )
        # Save the message ID for later deletion
        context.user_data["info_message_id"] = msg.message_id
        return msg

