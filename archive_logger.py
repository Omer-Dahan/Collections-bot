# archive_logger.py
"""
Archive and Activity Logging Module

This module handles:
1. Forwarding/copying files to dual archive channels
2. Sending structured activity logs to admin channel
3. Rate limiting and error handling for channel operations

Uses a queue-based approach to prevent Telegram rate limiting.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional, Literal, TypedDict
from collections import deque
from telegram import Bot, InlineKeyboardMarkup
from telegram.error import TelegramError, Forbidden, RetryAfter

# Channel IDs - configured by admin
ARCHIVE_CHANNEL_1 = -1003386031529  # ערוץ גיבוי תוכן 1
ARCHIVE_CHANNEL_2 = -1003470142704  # ערוץ גיבוי תוכן 2
ADMIN_ACTIVITY_CHANNEL = -1003542497376  # ערוץ מידע - בוט אוספים

# Feature toggle
ENABLE_ARCHIVING = True

# Rate limiting - more conservative to avoid flood control
ARCHIVE_DELAY = 5.0  # seconds between archive file sends (per channel)
ACTIVITY_LOG_DELAY = 2.0  # seconds between activity logs
RETRY_EXTRA_DELAY = 5.0  # extra delay after a retry

logger = logging.getLogger(__name__)

class ArchiveQueueItem(TypedDict):
    item_id: int
    file_id: Optional[str]
    content_type: str
    user_id: int
    user_name: str
    username: Optional[str]
    collection_id: int
    collection_name: Optional[str]
    file_name: Optional[str]
    original_caption: Optional[str]
    bot: Bot

# Global queue and lock for serializing archive operations
_archive_queue: deque[ArchiveQueueItem] = deque()
_archive_lock = asyncio.Lock()
_queue_processor_running = False

# Action types for activity logging
ActionType = Literal[
    "FILE_SAVED",
    "FILE_ARCHIVED",
    "ARCHIVE_FAILED",
    "FILES_SENT",
    "SHARE_CREATED",
    "SHARE_ACCESSED",
    "SHARE_REVOKED"
]

# Hebrew action names
ACTION_NAMES_HE = {
    "FILE_SAVED": "קובץ נשמר",
    "FILE_ARCHIVED": "קובץ התווסף לאוסף",
    "ARCHIVE_FAILED": "גיבוי נכשל",
    "FILES_SENT": "קבצים נשלחו",
    "SHARE_CREATED": "שיתוף נוצר",
    "SHARE_ACCESSED": "גישה לשיתוף",
    "SHARE_REVOKED": "שיתוף בוטל"
}


def format_archive_caption(
    item_id: int,
    file_id: str,
    user_id: int,
    archive_msg_id: Optional[int] = None,
    original_caption: Optional[str] = None,
    user_name: str = "Unknown",
    username: Optional[str] = None
) -> str:
    """
    Format metadata caption for archived files.
    """
    # Format: Name @Username, or ID if no username
    if username:
        user_display = f"{user_name} @{username}"
    else:
        user_display = str(user_id)

    lines = [
        f"📦 מזהה פריט: {item_id}",
        f"📁 מזהה קובץ: {file_id[:50]}..." if len(file_id) > 50 else f"📁 מזהה קובץ: {file_id}",
        f"👤 שולח: {user_display}",
    ]
    
    if archive_msg_id:
        lines.insert(2, f"💬 הודעת ארכיון: {archive_msg_id}")
    
    if original_caption:
        lines.append("")
        lines.append(f"📝 מקורי: {original_caption[:200]}")
    
    return "\n".join(lines)


def format_activity_log(
    action: ActionType,
    user_id: int,
    success: bool = True,
    collection_id: Optional[int] = None,
    collection_name: Optional[str] = None,
    item_id: Optional[int] = None,
    extra: Optional[dict] = None,
    user_name: str = "Unknown",
    username: Optional[str] = None
) -> str:
    """
    Format structured log message for admin activity channel.
    """
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    status = "✅ נשמר בהצלחה" if success else "❌ נכשל"
    
    # Custom formatting for "FILE_ARCHIVED" to include Name/ID in the action line
    if action == "FILE_ARCHIVED":
        if collection_name:
             action_name = f"קובץ התווסף לאוסף \"{collection_name}\""
        elif collection_id:
             action_name = f"קובץ התווסף לאוסף {collection_id}"
        else:
             action_name = ACTION_NAMES_HE.get(action, action)
    else:
        action_name = ACTION_NAMES_HE.get(action, action)
    
    # Format: Name @Username, or ID if no username
    if username:
        user_display = f"{user_name} @{username}"
    else:
        user_display = str(user_id)
    
    lines = [
        f"🕐 {timestamp}",
        f"📌 פעולה: {action_name}",
        f"👤 משתמש: {user_display}",
    ]
    
    if item_id is not None:
        lines.append(f"📦 פריט: {item_id}")
    
    lines.append(f"מצב: {status}")
    
    if extra:
        for key, value in extra.items():
            lines.append(f"  └ {key}: {value}")
    
    return "\n".join(lines)


def get_message_link(channel_id: int, message_id: int) -> str:
    """
    Generate a direct link to a message in a private channel.
    Handles the -100 prefix removal.
    """
    channel_id_str = str(channel_id)
    if channel_id_str.startswith("-100"):
        link_channel_id = channel_id_str[4:]
    else:
        link_channel_id = channel_id_str.lstrip("-")
    
    return f"https://t.me/c/{link_channel_id}/{message_id}"


async def _send_with_retry(
    bot: Bot,
    send_func,
    max_retries: int = 3
) -> Optional[int]:
    """
    Execute a send function with retry logic for rate limits.
    Returns message_id on success, None on failure.
    """
    for attempt in range(max_retries):
        try:
            msg = await send_func()
            return msg.message_id
        except RetryAfter as e:
            wait_time = e.retry_after + RETRY_EXTRA_DELAY
            logger.warning(f"Rate limited (attempt {attempt+1}), waiting {wait_time}s")
            await asyncio.sleep(wait_time)
        except Forbidden:
            logger.error("Bot not authorized in channel")
            return None
        except (TelegramError, Exception) as e:
            logger.warning(f"Send error (attempt {attempt+1}): {e}")
            if attempt < max_retries - 1:
                await asyncio.sleep(RETRY_EXTRA_DELAY)
            else:
                logger.error(f"Failed to send after {max_retries} attempts: {e}")
                return None
    return None


async def safe_send_to_channel(
    bot: Bot,
    channel_id: int,
    text: str,
    parse_mode: str = "HTML"
) -> Optional[int]:
    """
    Safely send a text message to a channel with error handling.
    Returns message_id on success, None on failure.
    """
async def safe_copy_file_to_channel(
    bot: Bot,
    channel_id: int,
    file_id: Optional[str],
    content_type: str,
    caption: Optional[str] = None,
    file_name: Optional[str] = None,
    reply_markup: Optional[InlineKeyboardMarkup] = None
) -> Optional[int]:
    """
    Copy a file or send a message to a channel.
    Unified function for all content types.
    Returns message_id on success, None on failure.
    """
    async def send():
        method_map = {
            "photo": "send_photo",
            "video": "send_video",
            "document": "send_document",
            "audio": "send_audio",
            "text": "send_message"
        }
        
        if content_type not in method_map:
            raise ValueError(f"Unknown content type: {content_type}")
            
        method = getattr(bot, method_map[content_type])
        kwargs = {"chat_id": channel_id}
        
        if content_type == "text":
            kwargs["text"] = caption or "[Empty text item]"
        else:
            # For non-text items, file_id is required
            if not file_id:
                 raise ValueError(f"file_id required for {content_type}")
            kwargs[content_type] = file_id
            kwargs["caption"] = caption
            if content_type == "document":
                kwargs["filename"] = file_name
        
        # Add reply_markup if provided
        if reply_markup:
            kwargs["reply_markup"] = reply_markup
                
        return await method(**kwargs)
    
    try:
        return await _send_with_retry(bot, send)
    except ValueError as e:
        logger.warning(str(e))
        return None


async def log_activity(
    bot: Bot,
    action: ActionType,
    user_id: int,
    success: bool = True,
    collection_id: Optional[int] = None,
    collection_name: Optional[str] = None,
    item_id: Optional[int] = None,
    extra: Optional[dict] = None,
    user_name: str = "Unknown",
    username: Optional[str] = None,
    reply_markup: Optional[InlineKeyboardMarkup] = None
) -> None:
    """
    Send activity log to admin channel.
    This is fire-and-forget - failures are logged but don't propagate.
    """
    if not ENABLE_ARCHIVING:
        return
    
    async with _archive_lock:
        try:
            log_text = format_activity_log(
                action, user_id, success, 
                collection_id, collection_name,
                item_id, extra,
                user_name, username
            )
            # Use unified send function with content_type="text"
            await safe_copy_file_to_channel(
                bot=bot,
                channel_id=ADMIN_ACTIVITY_CHANNEL,
                file_id=None,
                content_type="text",
                caption=log_text,
                reply_markup=reply_markup
            )
            await asyncio.sleep(ACTIVITY_LOG_DELAY)
        except Exception as e:
            # Never let activity logging crash the main flow
            logger.error(f"Activity log failed: {e}")

async def _do_archive_file(
    bot: Bot,
    item_id: int,
    file_id: Optional[str],
    content_type: str,
    user_id: int,
    collection_id: int,
    collection_name: Optional[str] = None,
    file_name: Optional[str] = None,
    original_caption: Optional[str] = None,
    user_name: str = "Unknown",
    username: Optional[str] = None
) -> bool:
    """
    Actually perform the archiving.
    MODIFIED: Only logs activity with "View File" deep link button.
    Does NOT send to archive channels anymore.
    """
    from telegram import InlineKeyboardMarkup, InlineKeyboardButton
    
    # Generate Deep Link URL
    try:
        bot_username = bot.username
        deep_link = f"https://t.me/{bot_username}?start=view_{item_id}"
        
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("👀 צפיה בקובץ (מנהלים בלבד)", url=deep_link)]
        ])
    except Exception as e:
        logger.error(f"Failed to generate deep link: {e}")
        keyboard = None
    
    # Log activity with the button
    await log_activity(
        bot, "FILE_ARCHIVED", user_id,
        success=True,
        collection_id=collection_id,
        collection_name=collection_name,
        item_id=item_id,
        user_name=user_name,
        username=username,
        reply_markup=keyboard
    )
    
    return True


    
    return success_count > 0


async def archive_file_to_channels(
    bot: Bot,
    item_id: int,
    file_id: Optional[str],
    content_type: str,
    user_id: int,
    collection_id: int,
    collection_name: Optional[str] = None,
    file_name: Optional[str] = None,
    original_caption: Optional[str] = None,
    user_name: str = "Unknown",
    username: Optional[str] = None
) -> bool:
    """
    Queue a file for logging to the activity channel (Archive channels disabled).
    Returns immediately after queueing.
    """
    global _queue_processor_running
    
    if not ENABLE_ARCHIVING:
        return True
    
    # Use lock to prevent race condition when checking/setting processor status
    async with _archive_lock:
        # Add to queue
        _archive_queue.append({
            "item_id": item_id,
            "file_id": file_id,
            "content_type": content_type,
            "user_id": user_id,
            "collection_id": collection_id,
            "collection_name": collection_name,
            "file_name": file_name,
            "original_caption": original_caption,
            "user_name": user_name,
            "username": username,
            "bot": bot  # Store bot reference for queue processor
        })
        
        # Start queue processor if not already running
        if not _queue_processor_running:
            _queue_processor_running = True
            asyncio.create_task(_process_archive_queue_safe())
    
    return True


async def _process_archive_queue_safe():
    """
    Wrapper for queue processing with proper cleanup on exit.
    """
    global _queue_processor_running
    
    try:
        while True:
            # Get next item under lock
            async with _archive_lock:
                if not _archive_queue:
                    _queue_processor_running = False
                    return
                item = _archive_queue.popleft()
            
            bot = item.pop("bot")  # Extract bot from item
            
            try:
                await _do_archive_file(bot=bot, **item)
            except Exception as e:
                logger.error(f"Error processing archive queue item: {e}")
            
            # Wait before processing next item
            await asyncio.sleep(ARCHIVE_DELAY)
    except Exception as e:
        logger.error(f"Queue processor crashed: {e}")
        async with _archive_lock:
            _queue_processor_running = False
