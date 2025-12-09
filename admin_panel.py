# admin_panel.py
# ממשק ניהול מלא - פיצול מ-bot.py

import db
from config import ADMIN_IDS, is_admin
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from datetime import datetime
import html


def build_admin_main_menu():
    """Build the admin main menu message and keyboard"""
    stats = db.get_global_stats()
    
    message_text = (
        "🔧 <b>ממשק ניהול מערכת</b>\n\n"
        f"📊 סטטיסטיקות כלליות:\n"
        f"• משתמשים: {stats['users']}\n"
        f"• אוספים: {stats['collections']}\n"
        f"• פריטים: {stats['items']}\n\n"
        "בחר אפשרות:"
    )
    
    keyboard = [
        [InlineKeyboardButton("👥 משתמשים", callback_data="admin_users")],
        [InlineKeyboardButton("📦 אוספים", callback_data="admin_collections")],
        [InlineKeyboardButton("🔗 שיתופים", callback_data="admin_shares")],
        [InlineKeyboardButton("📊 סטטיסטיקות", callback_data="admin_stats")],
    ]
    
    return message_text, InlineKeyboardMarkup(keyboard)


async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """פקודת /adminpanel - תפריט ניהול ראשי"""
    user = update.effective_user
    
    if not is_admin(user.id):
        await update.message.reply_text("⛔ אין לך הרשאות גישה לממשק הניהול.")
        return
    
    message_text, reply_markup = build_admin_main_menu()
    
    await update.message.reply_text(
        message_text,
        reply_markup=reply_markup,
        parse_mode="HTML"
    )


async def handle_admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """טיפול בכל ה-callbacks של admin"""
    query = update.callback_query
    await query.answer()
    
    user = query.from_user
    
    if not is_admin(user.id):
        await query.edit_message_text("⛔ אין לך הרשאות גישה.")
        return
    
    data = query.data
    
    # Main menu callbacks
    if data == "admin_users":
        await show_users_list(query, context)
    elif data == "admin_collections":
        await show_collections_list(query, context, page=1)
    elif data.startswith("admin_collections_page:"):
        page = int(data.split(":")[1])
        await show_collections_list(query, context, page=page)
    elif data == "admin_shares":
        await show_shares_dashboard(query, context, page=1)
    elif data.startswith("admin_shares_page:"):
        page = int(data.split(":")[1])
        await show_shares_dashboard(query, context, page=page)
    elif data == "admin_stats":
        await show_global_stats(query, context)
    elif data == "admin_back_to_main":
        await show_main_menu(query, context)
    
    # User-related callbacks
    elif data.startswith("admin_user_card:"):
        user_id = int(data.split(":")[1])
        await show_user_card(query, context, user_id)
    elif data.startswith("admin_block_user:"):
        user_id = int(data.split(":")[1])
        await block_user_action(query, context, user_id)
    
    # Collection-related callbacks
    elif data.startswith("admin_collection_card:"):
        collection_id = int(data.split(":")[1])
        await show_collection_card(query, context, collection_id)
    elif data.startswith("admin_clone_collection:"):
        collection_id = int(data.split(":")[1])
        await clone_collection_action(query, context, collection_id)
    
    # Shares-related callbacks
    elif data.startswith("admin_share_card:"):
        share_id = int(data.split(":")[1])
        await show_share_card(query, context, share_id)
    elif data.startswith("admin_share_disable:"):
        share_code = data.split(":")[1]
        await disable_share_action(query, context, share_code)
    elif data.startswith("admin_share_create:"):
        collection_id = int(data.split(":")[1])
        await create_new_share_action(query, context, collection_id)
    elif data.startswith("admin_share_logs:"):
        share_code = data.split(":")[1]
        await show_share_access_log(query, context, share_code)
    
    # Additional callbacks
    elif data == "admin_close":
        await query.delete_message()
    elif data.startswith("admin_user_cols:"):
        user_id = int(data.split(":")[1])
        await show_user_collections(query, context, user_id)
    elif data.startswith("admin_manage_col:"):
        collection_id = int(data.split(":")[1])
        await show_admin_collection_management(query, context, collection_id)
    elif data.startswith("admin_delete_col:"):
        collection_id = int(data.split(":")[1])
        await confirm_delete_collection(query, context, collection_id)
    elif data.startswith("admin_confirm_delete:"):
        collection_id = int(data.split(":")[1])
        await delete_collection_action(query, context, collection_id)


# === Main Menu Functions ===

async def show_main_menu(query, context: ContextTypes.DEFAULT_TYPE):
    """תצוגת תפריט ראשי"""
    message_text, reply_markup = build_admin_main_menu()
    
    await query.edit_message_text(
        message_text,
        reply_markup=reply_markup,
        parse_mode="HTML"
    )


async def show_global_stats(query, context: ContextTypes.DEFAULT_TYPE):
    """תצוגת סטטיסטיקות כלליות"""
    stats = db.get_global_stats()
    
    message_text = (
        "📊 <b>סטטיסטיקות מערכת</b>\n\n"
        f"👥 משתמשים: {stats['users']}\n"
        f"📦 אוספים: {stats['collections']}\n"
        f"📄 פריטים: {stats['items']}\n"
    )
    
    keyboard = [[InlineKeyboardButton("⬅️ חזור", callback_data="admin_back_to_main")]]
    
    await query.edit_message_text(
        message_text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )


# === Users Functions ===

async def show_users_list(query, context: ContextTypes.DEFAULT_TYPE):
    """תצוגת רשימת משתמשים"""
    user_ids = db.get_all_users_with_collections()
    
    if not user_ids:
        await query.edit_message_text(
            "אין משתמשים עם אוספים במערכת.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ חזור", callback_data="admin_back_to_main")]])
        )
        return
    
    message_text = "👥 <b>רשימת משתמשים</b>\n\nבחר משתמש לצפייה:"
    keyboard = []
    
    for uid in user_ids:
        user_info = db.get_user_details(uid)
        if user_info:
            username = user_info['username'] or ""
            first_name = user_info['first_name'] or f"User_{uid}"
            display_name = f"{first_name} (@{username})" if username else first_name
            keyboard.append([InlineKeyboardButton(display_name, callback_data=f"admin_user_card:{uid}")])
    
    keyboard.append([InlineKeyboardButton("⬅️ חזור", callback_data="admin_back_to_main")])
    
    await query.edit_message_text(
        message_text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )


async def show_user_card(query, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    """כרטיס משתמש בודד"""
    user_info = db.get_user_details(user_id)
    
    if not user_info:
        await query.answer("משתמש לא נמצא", show_alert=True)
        return
    
    username = user_info['username'] or "ללא"
    first_name = user_info['first_name'] or "ללא"
    
    # Safe HTML
    safe_username = html.escape(username)
    safe_first_name = html.escape(first_name)
    
    blocked_status = "🚫 חסום" if user_info.get('blocked', 0) == 1 else "✅ פעיל"
    
    # Create clickable user link
    user_link = f'<a href="tg://user?id={user_id}">{safe_first_name}</a>'
    
    message_text = (
        f"👤 <b>כרטיס משתמש</b>\n\n"
        f"📛 שם: {user_link}\n"
        f"🆔 משתמש: @{safe_username}\n"
        f"🔢 ID: <code>{user_id}</code>\n"
        f"📅 הצטרף: {user_info['first_seen']}\n"
        f"📦 אוספים: {user_info['collections_count']}\n"
        f"📄 פריטים: {user_info['items_count']}\n"
        f"🔒 סטטוס: {blocked_status}\n"
    )
    
    keyboard = [
        [InlineKeyboardButton("📁 צפייה באוספים", callback_data=f"admin_user_cols:{user_id}")],
        [InlineKeyboardButton("🚫 חסום משתמש", callback_data=f"admin_block_user:{user_id}")],
        [InlineKeyboardButton("⬅️ חזור לרשימה", callback_data="admin_users")]
    ]
    
    await query.edit_message_text(
        message_text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )


async def block_user_action(query, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    """חסימת משתמש"""
    success = db.block_user(user_id)
    
    if success:
        await query.answer("✅ המשתמש נחסם בהצלחה!", show_alert=True)
        await show_user_card(query, context, user_id)
    else:
        await query.answer("❌ שגיאה בחסימת המשתמש", show_alert=True)


# === Collections Functions ===

async def show_collections_list(query, context: ContextTypes.DEFAULT_TYPE, page: int = 1):
    """תצוגת רשימת אוספים עם pagination"""
    items_per_page = 12
    total_items = db.count_all_collections()
    
    if total_items == 0:
        await query.edit_message_text(
            "אין אוספים במערכת.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ חזור", callback_data="admin_back_to_main")]])
        )
        return

    total_pages = (total_items + items_per_page - 1) // items_per_page
    page = max(1, min(page, total_pages))
    offset = (page - 1) * items_per_page
    
    collections = db.get_all_collections_paginated(offset=offset, limit=items_per_page)
    
    message_text = f"📦 <b>רשימת אוספים</b> (עמוד {page}/{total_pages})\n\n"
    keyboard = []
    
    for col_id, col_name, user_id, username, first_name in collections:
        display_name = username or first_name or f"User_{user_id}"
        # Truncate names if too long
        if len(col_name) > 25: 
            col_name = col_name[:23] + ".."
            
        safe_col_name = html.escape(col_name)
        safe_display_name = html.escape(display_name)
            
        keyboard.append([
            InlineKeyboardButton(
                f"{safe_col_name} ({safe_display_name})",
                callback_data=f"admin_collection_card:{col_id}"
            )
        ])
    
    # Navigation buttons
    nav_buttons = []
    if page > 1:
        nav_buttons.append(InlineKeyboardButton("◀️ הקודם", callback_data=f"admin_collections_page:{page-1}"))
    if page < total_pages:
        nav_buttons.append(InlineKeyboardButton("הבא ▶️", callback_data=f"admin_collections_page:{page+1}"))
    
    if nav_buttons:
        keyboard.append(nav_buttons)
        
    keyboard.append([InlineKeyboardButton("⬅️ חזור", callback_data="admin_back_to_main")])
    
    await query.edit_message_text(
        message_text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )


async def show_collection_card(query, context: ContextTypes.DEFAULT_TYPE, collection_id: int):
    """כרטיס אוסף בודד"""
    collection = db.get_collection_by_id(collection_id)
    
    if not collection:
        await query.answer("אוסף לא נמצא", show_alert=True)
        return
    
    col_id, col_name, owner_id = collection
    item_count = db.count_items_in_collection(collection_id)
    
    owner_info = db.get_user(owner_id)
    owner_display = owner_info.username if owner_info and owner_info.username else f"User_{owner_id}"
    
    # Escape HTML characters in collection name and owner
    safe_col_name = html.escape(col_name)
    safe_owner_display = html.escape(owner_display)
    
    message_text = (
        f"📦 <b>כרטיס אוסף</b>\n\n"
        f"📛 שם: {safe_col_name}\n"
        f"🆔 ID: <code>{col_id}</code>\n"
        f"👤 בעלים: @{safe_owner_display}\n"
        f"📄 פריטים: {item_count}\n"
    )
    
    keyboard = [
        [InlineKeyboardButton("👁️ דפדף בתוכן", callback_data=f"browse_page:{collection_id}:1")],
        [InlineKeyboardButton("📋 שכפל לאדמין", callback_data=f"admin_clone_collection:{collection_id}")],
        [InlineKeyboardButton("⬅️ חזור לרשימה", callback_data="admin_collections")]
    ]
    
    await query.edit_message_text(
        message_text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )


async def clone_collection_action(query, context: ContextTypes.DEFAULT_TYPE, collection_id: int):
    """שכפול אוסף לאדמין"""
    admin_id = query.from_user.id
    new_collection_id = db.clone_collection_for_user(collection_id, admin_id)
    
    if new_collection_id > 0:
        await query.answer("✅ האוסף שוכפל בהצלחה!", show_alert=True)
        await show_collection_card(query, context, collection_id)
    else:
        await query.answer("❌ שגיאה בשכפול האוסף", show_alert=True)


# === Shares Functions ===

async def show_shares_dashboard(query, context: ContextTypes.DEFAULT_TYPE, page: int = 1):
    """מסך ראשי שיתופים - תצוגת טקסט עם pagination"""
    shares = db.get_all_active_shares()
    
    if not shares:
        await query.edit_message_text(
            "🔗 **שיתופים**\n\nאין קודי שיתוף פעילים במערכת.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ חזור", callback_data="admin_back_to_main")]]),
            parse_mode="Markdown"
        )
        return
    
    # Pagination settings
    items_per_page = 20
    total_shares = len(shares)
    total_pages = (total_shares + items_per_page - 1) // items_per_page
    
    # Ensure page is in valid range
    page = max(1, min(page, total_pages))
    
    start_idx = (page - 1) * items_per_page
    end_idx = min(start_idx + items_per_page, total_shares)
    page_shares = shares[start_idx:end_idx]
    
    message_text = f"🔗 <b>שיתופים פעילים</b> (עמוד {page}/{total_pages})\n\n"
    
    for idx, (share_id, share_code, collection_id, collection_name, created_by, creator_username, created_at, access_count) in enumerate(page_shares, start=start_idx + 1):
        # Format date
        try:
            date_obj = datetime.fromisoformat(created_at)
            date_str = date_obj.strftime("%d/%m/%Y")
        except:
            date_str = created_at[:10]
        
        creator_display = creator_username if creator_username else f"User_{created_by}"
        safe_creator_display = html.escape(creator_display)
        
        # Create clickable user link
        user_link = f'<a href="tg://user?id={created_by}">{safe_creator_display}</a>'
        
        # Escape HTML characters in collection name
        safe_collection_name = html.escape(collection_name)
        
        message_text += (
            f"📦 {idx}. {safe_collection_name}\n"
            f"   🔑 קוד: <code>{share_code}</code>\n"
            f"   👤 יוצר: {user_link}\n"
            f"   📅 תאריך: {date_str}\n"
            f"   👥 גישות: {access_count}\n\n"
        )
    
    # Build keyboard with pagination
    keyboard = []
    
    # Pagination buttons
    if total_pages > 1:
        nav_buttons = []
        if page > 1:
            nav_buttons.append(InlineKeyboardButton("◀️ הקודם", callback_data=f"admin_shares_page:{page-1}"))
        if page < total_pages:
            nav_buttons.append(InlineKeyboardButton("הבא ▶️", callback_data=f"admin_shares_page:{page+1}"))
        if nav_buttons:
            keyboard.append(nav_buttons)
    
    # Back button
    keyboard.append([InlineKeyboardButton("⬅️ חזור", callback_data="admin_back_to_main")])
    
    await query.edit_message_text(
        message_text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )


async def show_share_card(query, context: ContextTypes.DEFAULT_TYPE, share_id: int):
    """כרטיס שיתוף בודד - עם כפתורי פעולה"""
    # Get all shares to find the one we need
    all_shares = db.get_all_active_shares()
    share_info = None
    
    for share in all_shares:
        if share[0] == share_id:
            share_info = share
            break
    
    if not share_info:
        await query.answer("שיתוף לא נמצא", show_alert=True)
        return
    
    share_id, share_code, collection_id, collection_name, created_by, creator_username, created_at, access_count = share_info
    
    # Get detailed stats
    stats = db.get_share_stats(share_code)
    item_count = db.count_items_in_collection(collection_id)
    
    # Get recent users (up to 10)
    recent_logs = db.get_detailed_access_log(share_code, limit=10)
    
    # Format date
    try:
        date_obj = datetime.fromisoformat(created_at)
        date_str = date_obj.strftime("%d/%m/%Y %H:%M")
    except:
        date_str = created_at[:16]
    
    creator_display = creator_username if creator_username else f"User_{created_by}"
    safe_creator_display = html.escape(creator_display)
    creator_link = f'<a href="tg://user?id={created_by}">{safe_creator_display}</a>'
    
    # Escape HTML
    safe_collection_name = html.escape(collection_name)
    
    message_text = (
        f"🔗 <b>כרטיס שיתוף</b>\n\n"
        f"📦 אוסף: {safe_collection_name}\n"
        f"🆔 קוד: <code>{share_code}</code>\n"
        f"👤 יוצר: {creator_link}\n"
        f"📅 נוצר: {date_str}\n"
        f"📁 קבצים: {item_count}\n"
        f"👥 גישות ייחודיות: {stats['unique_users']}\n"
        f"📊 גישות כולל: {stats['total_accesses']}\n\n"
    )
    
    if recent_logs:
        message_text += "<b>משתמשים אחרונים:</b>\n"
        for log_user_id, username, first_name, accessed_at in recent_logs[:5]:
            user_display = first_name or username or f"User_{log_user_id}"
            safe_user_display = html.escape(user_display)
            user_link = f'<a href="tg://user?id={log_user_id}">{safe_user_display}</a>'
            message_text += f"• {user_link}\n"
    
    keyboard = [
        [InlineKeyboardButton("🚫 השבת קוד", callback_data=f"admin_share_disable:{share_code}")],
        [InlineKeyboardButton("🔄 צור קוד חדש", callback_data=f"admin_share_create:{collection_id}")],
        [InlineKeyboardButton("📊 לוג מלא", callback_data=f"admin_share_logs:{share_code}")],
        [InlineKeyboardButton("⬅️ חזור", callback_data="admin_shares")]
    ]
    
    await query.edit_message_text(
        message_text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )


async def disable_share_action(query, context: ContextTypes.DEFAULT_TYPE, share_code: str):
    """השבתת קוד שיתוף"""
    # Get collection id from share code
    collection_info = db.get_collection_by_share_code(share_code)
    
    if not collection_info:
        await query.answer("❌ קוד שיתוף לא נמצא", show_alert=True)
        return
    
    collection_id, _, owner_id = collection_info
    
    success = db.revoke_share_code(collection_id, owner_id)
    
    if success:
        await query.answer("✅ קוד השיתוף הושבת!", show_alert=True)
        await show_shares_dashboard(query, context)
    else:
        await query.answer("❌ שגיאה בהשבתת הקוד", show_alert=True)


async def create_new_share_action(query, context: ContextTypes.DEFAULT_TYPE, collection_id: int):
    """יצירת קוד שיתוף חדש"""
    collection = db.get_collection_by_id(collection_id)
    
    if not collection:
        await query.answer("❌ אוסף לא נמצא", show_alert=True)
        return
    
    _, _, owner_id = collection
    admin_id = query.from_user.id
    
    # Create new share code
    new_code = db.create_share_link(collection_id, admin_id)
    
    await query.answer(f"✅ קוד חדש נוצר: {new_code}", show_alert=True)
    await show_shares_dashboard(query, context)


async def show_share_access_log(query, context: ContextTypes.DEFAULT_TYPE, share_code: str, offset: int = 0):
    """לוג גישות מפורט"""
    logs = db.get_detailed_access_log(share_code, offset=offset, limit=20)
    
    if not logs:
        await query.edit_message_text(
            "📊 **לוג גישות**\n\nאין גישות רשומות.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ חזור", callback_data=f"admin_shares")]]),
            parse_mode="Markdown"
        )
        return
    
    # Get share info for title
    collection_info = db.get_collection_by_share_code(share_code)
    collection_name = collection_info[1] if collection_info else "Unknown"
    safe_collection_name = html.escape(collection_name)
    
    message_text = f"📊 <b>לוג גישות - {safe_collection_name}</b>\n\n"
    
    for user_id, username, first_name, accessed_at in logs:
        user_display = first_name or username or f"User_{user_id}"
        safe_user_display = html.escape(user_display)
        user_link = f'<a href="tg://user?id={user_id}">{safe_user_display}</a>'
        
        try:
            date_obj = datetime.fromisoformat(accessed_at)
            date_str = date_obj.strftime("%d/%m %H:%M")
        except:
            date_str = accessed_at[:16]
        
        message_text += f"• {user_link} - {date_str}\n"
    
    keyboard = [[InlineKeyboardButton("⬅️ חזור", callback_data=f"admin_shares")]]
    
    await query.edit_message_text(
        message_text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )


# === Additional Admin Functions ===

async def show_user_collections(query, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    """תצוגת אוספים של משתמש ספציפי"""
    collections = db.get_collections(user_id)
    
    if not collections:
        await query.edit_message_text(
            f"למשתמש {user_id} אין אוספים.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ חזור", callback_data=f"admin_user_card:{user_id}")]])
        )
        return
    
    keyboard = []
    for col_id, name in collections:
        keyboard.append([InlineKeyboardButton(name, callback_data=f"admin_manage_col:{col_id}")])
    
    keyboard.append([InlineKeyboardButton("⬅️ חזור", callback_data=f"admin_user_card:{user_id}")])
    
    await query.edit_message_text(
        f"📦 **אוספים של משתמש {user_id}:**",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )


async def show_admin_collection_management(query, context: ContextTypes.DEFAULT_TYPE, collection_id: int):
    """ניהול אוסף ספציפי מתוך ממשק הניהול"""
    collection = db.get_collection_by_id(collection_id)
    
    if not collection:
        await query.edit_message_text(
            "האוסף לא נמצא.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ חזור", callback_data="admin_collections")]])
        )
        return
    
    col_id, col_name, owner_id = collection
    item_count = db.count_items_in_collection(collection_id)
    
    # Escape HTML characters in collection name
    safe_col_name = html.escape(col_name)
    
    keyboard = [
        [InlineKeyboardButton("👁️ צפה בתוכן", callback_data=f"browse_page:{collection_id}:1")],
        [InlineKeyboardButton("🗑️ מחק אוסף", callback_data=f"admin_delete_col:{collection_id}")],
        [InlineKeyboardButton("📋 שכפל אוסף אלי", callback_data=f"admin_clone_collection:{collection_id}")],
        [InlineKeyboardButton("⬅️ חזור לאוספי המשתמש", callback_data=f"admin_user_cols:{owner_id}")]
    ]
    
    await query.edit_message_text(
        f"📦 <b>ניהול אוסף: {safe_col_name}</b>\n\n"
        f"👤 בעלים: {owner_id}\n"
        f"📄 פריטים: {item_count}",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )


async def confirm_delete_collection(query, context: ContextTypes.DEFAULT_TYPE, collection_id: int):
    """אישור מחיקת אוסף"""
    collection = db.get_collection_by_id(collection_id)
    
    if not collection:
        await query.answer("האוסף לא נמצא", show_alert=True)
        return
    
    col_name = collection[1]
    # Escape HTML characters in collection name
    safe_col_name = html.escape(col_name)
    
    keyboard = [
        [InlineKeyboardButton("✅ כן, מחק", callback_data=f"admin_confirm_delete:{collection_id}")],
        [InlineKeyboardButton("❌ ביטול", callback_data=f"admin_manage_col:{collection_id}")]
    ]
    
    await query.edit_message_text(
        f"⚠️ <b>אזהרה!</b>\n\n"
        f"האם אתה בטוח שברצונך למחוק את האוסף <b>{safe_col_name}</b>?\n"
        f"פעולה זו תמחק גם את כל הפריטים באוסף.",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )


async def delete_collection_action(query, context: ContextTypes.DEFAULT_TYPE, collection_id: int):
    """מחיקת אוסף בפועל"""
    # Delete all items first
    db.delete_all_items_in_collection(collection_id)
    # Delete collection
    success = db.delete_collection(collection_id)
    
    if success:
        await query.edit_message_text(
            "✅ האוסף נמחק בהצלחה!",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ חזור לתפריט ראשי", callback_data="admin_back_to_main")]])
        )
    else:
        await query.edit_message_text(
            "❌ שגיאה במחיקת האוסף.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ חזור", callback_data="admin_back_to_main")]])
        )
