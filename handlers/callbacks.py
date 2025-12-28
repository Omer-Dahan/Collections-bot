import math
import asyncio
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes
import db
from config import is_admin
from constants import active_collections
from utils import (
    reset_user_modes, send_response, check_collection_access, 
    get_page_header, build_page_menu, show_collection_page,
    build_page_file_type_menu, logger, prepare_media_groups, 
    send_media_groups_in_chunks, verify_user_code,
    create_verification_code, update_batch_status, format_size,
    get_main_menu_text, build_main_menu_keyboard
)
from handlers.commands import (
    new_collection_flow, list_collections_flow, manage_collections_flow, 
    remove_flow, id_file_flow, show_browse_menu
)

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

    # Reset batch status for this collection to ensure fresh counter
    if "batch_status" in context.user_data and collection_id in context.user_data["batch_status"]:
        del context.user_data["batch_status"][collection_id]

    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton(text="🛑 הפסק הוספה", callback_data="stop_collect")]]
    )

    await query.edit_message_text(
        text=f"אוסף פעיל הוגדר: {collection[1]}\nעכשיו שלח תוכן כדי לשמור.",
        reply_markup=keyboard,
    )

async def handle_select_item_delete_col_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """בחירת אוסף למחיקת פריטים (מצב מחיקה)"""
    query = update.callback_query
    await query.answer()

    user = query.from_user
    data = query.data  # format: select_item_del_col:<id>
    if not data.startswith("select_item_del_col:"):
        return

    try:
        _, col_id_str = data.split(":")
        collection_id = int(col_id_str)
    except ValueError:
        return

    is_allowed, error_msg, collection = check_collection_access(user.id, collection_id)
    if not is_allowed:
        await query.edit_message_text(error_msg)
        return

    # Activate Item Deletion Mode
    context.user_data["item_delete_mode"] = True
    context.user_data["delete_target_collection_id"] = collection_id
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🏁 סיום מחיקה", callback_data="back_to_main")]
    ])

    text = (
        f"🗑 **מצב מחיקת פריטים הופעל עבור: {collection[1]}**\n\n"
        "שלח לי כעת תמונה, וידאו או קובץ שקיים באוסף זה, ואני אמחק אותו עבורך.\n"
        "תוכל למחוק מספר פריטים ברצף."
    )

    await query.edit_message_text(text=text, reply_markup=keyboard, parse_mode="Markdown")

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

    # Use centralized function
    await show_collection_page(
        update=update,
        context=context,
        collection_id=collection_id,
        page=page,
        edit_message_id=query.message.message_id
    )

async def handle_scroll_view_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """צפייה בפריט יחיד עם כפתורי הבא/הקודם - מחיקת הודעה ושליחה חדשה"""
    query = update.callback_query
    await query.answer()

    user = query.from_user
    data = query.data  # format: scroll_view:<collection_id>:<item_index>
    
    if not data.startswith("scroll_view:"):
        return

    try:
        _, col_id_str, index_str = data.split(":")
        collection_id = int(col_id_str)
        item_index = int(index_str)
    except ValueError:
        return

    is_allowed, error_msg, collection = check_collection_access(user.id, collection_id)
    if not is_allowed:
        await query.edit_message_text(error_msg)
        return

    # Get total items count
    total_items = db.count_items_in_collection(collection_id)
    
    if total_items == 0:
        await query.edit_message_text("אין פריטים באוסף הזה.")
        return

    # Ensure index is within bounds
    if item_index < 0:
        item_index = 0
    elif item_index >= total_items:
        item_index = total_items - 1

    # Get single item at the current index
    items = db.get_items_by_collection(collection_id, offset=item_index, limit=1)
    if not items:
        await query.edit_message_text("פריט לא נמצא.")
        return

    item = items[0]
    # item structure: (id, content_type, file_id, text_content, file_name, file_size, added_at)
    item_id, content_type, file_id, text_content, file_name, file_size, added_at = item

    chat_id = query.message.chat_id
    
    # Build navigation keyboard
    nav_buttons = []
    if item_index > 0:
        nav_buttons.append(InlineKeyboardButton("⬅ הקודם", callback_data=f"scroll_view:{collection_id}:{item_index - 1}"))
    if item_index < total_items - 1:
        nav_buttons.append(InlineKeyboardButton("הבא ➡", callback_data=f"scroll_view:{collection_id}:{item_index + 1}"))
    
    keyboard = []
    if nav_buttons:
        keyboard.append(nav_buttons)
    keyboard.append([InlineKeyboardButton("🔙 חזור לתפריט דפדוף", callback_data=f"browse_page:{collection_id}:1")])
    reply_markup = InlineKeyboardMarkup(keyboard)

    # Build header text
    header_text = f"📄 פריט {item_index + 1} מתוך {total_items}"
    if text_content:
        header_text += f"\n\n{text_content}"

    # Delete the old message first
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=query.message.message_id)
    except Exception:
        pass

    # Send item based on content type
    try:
        if content_type == "text" or not file_id:
            # Text-only item
            await context.bot.send_message(
                chat_id=chat_id,
                text=header_text,
                reply_markup=reply_markup
            )
        elif content_type == "photo":
            await context.bot.send_photo(
                chat_id=chat_id,
                photo=file_id,
                caption=header_text,
                reply_markup=reply_markup
            )
        elif content_type == "video":
            await context.bot.send_video(
                chat_id=chat_id,
                video=file_id,
                caption=header_text,
                reply_markup=reply_markup
            )
        elif content_type == "document":
            await context.bot.send_document(
                chat_id=chat_id,
                document=file_id,
                caption=header_text,
                reply_markup=reply_markup
            )
        else:
            # Fallback for unknown content types
            await context.bot.send_message(
                chat_id=chat_id,
                text=header_text,
                reply_markup=reply_markup
            )
    except Exception as e:
        logger.error(f"Error sending scroll item: {e}")
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"שגיאה בטעינת הפריט.\n\n{header_text}",
            reply_markup=reply_markup
        )

async def handle_page_info_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """תצוגת מידע מפורט על קבצים בדף - 10 קבצים בכל פעם"""
    query = update.callback_query
    await query.answer()

    user = query.from_user
    data = query.data  # format: page_info:<collection_id>:<page>:<info_page>
    
    if not data.startswith("page_info:"):
        return

    try:
        _, col_id_str, page_str, info_page_str = data.split(":")
        collection_id = int(col_id_str)
        page = int(page_str)
        info_page = int(info_page_str)
    except ValueError:
        return

    is_allowed, error_msg, collection = check_collection_access(user.id, collection_id)
    if not is_allowed:
        await query.edit_message_text(error_msg)
        return

    block_size = 100
    info_group_size = 10

    # Get items for current page block
    offset_block = (page - 1) * block_size
    items_block = db.get_items_by_collection(collection_id, offset=offset_block, limit=block_size)
    
    if not items_block:
        await query.edit_message_text("אין פריטים בעמוד זה.")
        return

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
        """Escape HTML special characters"""
        if not text:
            return text
        return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    
    info_text = f"📋 <b>מידע על קבצים - עמוד {page}</b>\n"
    info_text += f"מציג {info_start + 1}-{min(info_end, len(items_block))} מתוך {len(items_block)}\n\n"
    
    for item in items_to_show:
        item_id, content_type, file_id, text_content, file_name, file_size, added_at = item
        
        type_display = content_type_map.get(content_type, "📁 קובץ")
        # Escape HTML special characters
        name_display = escape_html(file_name) if file_name else "(ללא שם קובץ)"
        
        info_text += f"סוג: {type_display}\n"
        info_text += f"שם: {name_display}\n"
        info_text += f"ID: <code>{item_id}</code>\n"
        info_text += "─────────────────\n"
    
    # Store allowed IDs for this user (security - only allow IDs shown on current page)
    context.user_data["allowed_item_ids"] = [item[0] for item in items_to_show]
    context.user_data["info_page_collection_id"] = collection_id
    
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
    keyboard.append([InlineKeyboardButton("🏠 חזור לתפריט ראשי", callback_data="back_to_main")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    try:
        await query.edit_message_text(
            text=info_text,
            reply_markup=reply_markup,
            parse_mode="HTML"
        )
    except Exception as e:
        logger.warning(f"Failed to send with HTML, trying plain text: {e}")
        # Fallback: remove HTML tags and try without parse_mode
        plain_text = info_text.replace("<b>", "").replace("</b>", "")
        plain_text = plain_text.replace("<code>", "").replace("</code>", "")
        plain_text = plain_text.replace("<i>", "").replace("</i>", "")
        plain_text = plain_text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
        
        try:
            await query.edit_message_text(
                text=plain_text,
                reply_markup=reply_markup
            )
        except Exception:
            chat_id = query.message.chat_id
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=query.message.message_id)
            except Exception:
                pass
            await context.bot.send_message(
                chat_id=chat_id,
                text=plain_text,
                reply_markup=reply_markup
            )

async def handle_browse_group_or_select_all_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """כפתורי המספרים (קבוצות) ו'בחר הכל' בעמוד דפדוף"""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    user_id = query.from_user.id
    
    is_select_all = False
    idx = 0
    
    # Detect type
    if data.startswith("browse_group:"):
        _, col_id_str, page_str, idx_str = data.split(":")
        collection_id = int(col_id_str)
        page = int(page_str)
        idx = int(idx_str)
    elif data.startswith("browse_page_select_all:"):
        is_select_all = True
        _, col_id_str, page_str = data.split(":")
        collection_id = int(col_id_str)
        page = int(page_str)
    else:
        return

    # Permissions
    is_allowed, error_msg, collection = check_collection_access(user_id, collection_id)
    if not is_allowed:
        await query.edit_message_text(error_msg)
        return

    if is_select_all:
        header_text = f"בחרת את כל הפריטים בעמוד {page}.\nמה לעשות?"
    else:
        # Calculate range for group
        block_size = 100
        group_size = 10
        offset_block = (page - 1) * block_size
        
        # Determine group range
        start_offset = offset_block + (idx - 1) * group_size
        end_offset = start_offset + group_size
        
        header_text = f"בחרת את קבוצה {idx} (פריטים {start_offset+1}-{end_offset}).\nמה לעשות?"

    # Show options keyboard (Send Videos, Send Images, Send Files, Send All Main)
    # We call db to check counts to show nice numbers on buttons
    # Note: This checks counts for the WHOLE PAGE for select_all, or specific group
    
    # For simplicity, we just pass the range/page params to the next menu.
    # The build_page_file_type_menu logic might need adjustment if we want specific counts.
    # Currently it seems built for page-level, but we can reuse or adapt.
    
    # Let's count totals for this scope to show on buttons
    block_size = 100
    offset_block = (page - 1) * block_size
    items_block = db.get_items_by_collection(collection_id, offset=offset_block, limit=block_size)
    
    if not is_select_all:
        # filter only group items
        group_size = 10
        g_start = (idx - 1) * group_size
        g_end = g_start + group_size
        items_scope = items_block[g_start:g_end] if g_start < len(items_block) else []
    else:
        items_scope = items_block

    # If it's a specific group (not select all), we send immediately as requested
    if not is_select_all:
        if not items_scope:
            await query.answer("אין פריטים בקבוצה זו.", show_alert=True)
            return

        # Prepare and send immediately
        media_visual, media_docs, text_items = prepare_media_groups(items_scope)
        
        await context.bot.send_message(
            chat_id=query.message.chat_id, 
            text=f"🚀 שולח {len(items_scope)} פריטים מקבוצה {idx}..."
        )
        
        await send_media_groups_in_chunks(context.bot, query.message.chat_id, media_visual, media_docs, text_items)
        
        # After sending, we resend the collection page so it appears at the bottom
        await show_collection_page(
            update=update,
            context=context,
            collection_id=collection_id,
            page=page,
            force_resend=True
        )
        return

    # For Select All, we keep the menu logic
    video_count = sum(1 for x in items_scope if x[1] == 'video')
    image_count = sum(1 for x in items_scope if x[1] == 'photo')
    doc_count = sum(1 for x in items_scope if x[1] == 'document')
    
    # Store the scope in user_data so the next step knows what to send
    context.user_data[f"send_scope_{user_id}"] = {
        "collection_id": collection_id,
        "page": page,
        "is_select_all": is_select_all,
        "group_idx": idx,
        "items_ids": [x[0] for x in items_scope]  # Store IDs to send
    }

    reply_markup = build_page_file_type_menu(
        collection_id, page, video_count, image_count, doc_count
    )

    await query.edit_message_text(
        text=header_text,
        reply_markup=reply_markup
    )

async def handle_page_file_send_choice_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """שליחת תכנים לפי סוג מתוך עמוד דפדוף, אחרי 'בחר הכל'"""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    user_id = query.from_user.id
    
    # data format: page_files_<type>:<collection_id>:<page>
    # types: videos, images, document, queue_all
    
    try:
        action, col_id_str, page_str = data.split(":")
        collection_id = int(col_id_str)
        page = int(page_str)
    except ValueError:
        return

    # Check permission again
    is_allowed, _, collection = check_collection_access(user_id, collection_id)
    if not is_allowed:
        return

    # Retrieve scope
    scope_key = f"send_scope_{user_id}"
    scope = context.user_data.get(scope_key)
    
    # If scope is missing or for wrong collection/page, we fallback to page-level or error
    if not scope or scope["collection_id"] != collection_id or scope["page"] != page:
        # Fallback: Just fetch all items in page
        block_size = 100
        offset_block = (page - 1) * block_size
        items = db.get_items_by_collection(collection_id, offset=offset_block, limit=block_size)
    else:
        # Fetch actual items by stored IDs
        # To avoid massive DB query with "IN (...)", we can just fetch the page and filter in python,
        # since we know the page anyway.
        block_size = 100
        offset_block = (page - 1) * block_size
        items_block = db.get_items_by_collection(collection_id, offset=offset_block, limit=block_size)
        target_ids = set(scope["items_ids"])
        items = [x for x in items_block if x[0] in target_ids]

    # Filter by type requested
    final_items = []
    if action == "page_files_videos":
        final_items = [x for x in items if x[1] == 'video']
    elif action == "page_files_images":
        final_items = [x for x in items if x[1] == 'photo']
    elif action == "page_files_document":
        final_items = [x for x in items if x[1] == 'document']
    elif action == "page_files_queue_all":
        final_items = items
    
    if not final_items:
        await query.edit_message_text("לא נמצאו פריטים מהסוג שנבחר בקבוצה זו.")
        # Restoration logic omitted for brevity, user can click back
        return

    # Send items
    chat_id = query.message.chat_id
    await context.bot.send_message(chat_id=chat_id, text=f"🚀 שולח {len(final_items)} פריטים...")
    
    media_visual, media_docs, text_items = prepare_media_groups(final_items)
    await send_media_groups_in_chunks(context.bot, chat_id, media_visual, media_docs, text_items)
    
    if media_visual or media_docs or text_items:
        # Show the collection page again (fresh message at bottom)
        await show_collection_page(
            update=update,
            context=context,
            collection_id=collection_id,
            page=page,
            edit_message_id=query.message.message_id,
            force_resend=True
        )
    else:
        await context.bot.send_message(
            chat_id=chat_id,
            text="חלה שגיאה בעיבוד הפריטים.",
        )

async def handle_batch_status_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """הצגת התראה קופצת עם מספר הקבצים שנוספו"""
    query = update.callback_query
    data = query.data  # batch_status:<collection_id>
    
    if not data.startswith("batch_status:"):
        return
        
    try:
        _, col_id_str = data.split(":")
        collection_id = int(col_id_str)
        
        user_data = context.user_data
        count = 0
        if "batch_status" in user_data and collection_id in user_data["batch_status"]:
            count = user_data["batch_status"][collection_id]["count"]
            
        await query.answer(f"עד כה נוספו {count} קבצים בסשן הנוכחי", show_alert=True)
        
    except Exception:
        await query.answer()

async def handle_collection_send_all_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Request confirmation with verification code before sending all items in a collection."""
    query = update.callback_query
    await query.answer()
    
    data = query.data  # collection_send_all:<collection_id>
    if not data.startswith("collection_send_all:"):
        return
        
    try:
        _, col_id_str = data.split(":")
        collection_id = int(col_id_str)
    except ValueError:
        return
        
    user = query.from_user
    is_allowed, error_msg, collection = check_collection_access(user.id, collection_id)
    if not is_allowed:
        await query.edit_message_text(error_msg)
        return
        
    total_items = db.count_items_in_collection(collection_id)
    if total_items == 0:
        await query.answer("האוסף ריק", show_alert=True)
        return

    # Generate code
    code = create_verification_code(
        context, 
        "send_collection", 
        {
            "collection_id": collection_id,
            "msg_id": query.message.message_id
        }
    )
    
    text = (
        f"⚠️ **אישור שליחת אוסף מלא**\n\n"
        f"אתה עומד לשלוח את כל האוסף: {collection[1]} ({total_items} פריטים).\n"
        f"זה עשוי לקחת זמן וליצור עומס.\n\n"
        f"כדי לאשר, שלח את הקוד הבא לבוט:\n"
        f"`{code}`"
    )
    
    # We set a state to expect text input
    context.user_data["verify_send_collection_mode"] = True
    
    await query.edit_message_text(
        text=text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ ביטול", callback_data=f"browse_page:{collection_id}:1")]
        ])
    )

async def handle_stop_collect_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("מצב איסוף נעצר")
    
    user_id = query.from_user.id
    
    # Remove from active collections
    if user_id in active_collections:
        del active_collections[user_id]
    
    # Clear batch status
    if "batch_status" in context.user_data:
        # Clean specific user collection status would be better but simple clear is ok
        pass

    try:
        await query.edit_message_text(
            "🛑 מצב איסוף נעצר.\nתוכל לחזור ולהוסיף קבצים דרך התפריט הראשי.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🏠 תפריט ראשי", callback_data="back_to_main")]
            ])
        )
    except Exception:
        pass

async def handle_delete_select_collection_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle collection selection for delete mode"""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    # format: delete_collection:<id>
    
    try:
        _, col_id_str = data.split(":")
        collection_id = int(col_id_str)
    except ValueError:
        return
        
    user_id = query.from_user.id
    is_allowed, error_msg, collection = check_collection_access(user_id, collection_id)
    
    if not is_allowed:
        await query.edit_message_text(error_msg)
        return
        
    # Generate verification code
    code = create_verification_code(
        context, 
        "delete_collection", 
        {"collection_id": collection_id}
    )
    
    item_count = db.count_items_in_collection(collection_id)
    
    text = (
        f"⚠️ **בטוח שאתה רוצה למחוק את האוסף?**\n\n"
        f"📌 שם האוסף: **{collection[1]}**\n"
        f"📦 מספר פריטים: **{item_count}**\n\n"
        f"כדי לאשר מחיקה, שלח את הקוד הבא:\n"
        f"`{code}`"
    )
    
    context.user_data["verify_delete_collection_mode"] = True
    
    await query.edit_message_text(
        text=text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
             [InlineKeyboardButton("❌ ביטול", callback_data="exit_delete_mode")]
        ])
    )

async def handle_main_menu_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data = query.data
    # main_menu:<action>
    
    action = data.split(":")[1]
    
    if action == "newcollection":
        # Create dummy message wrapper to reuse flow function
        await new_collection_flow(query.message, query.from_user, context, [], edit_message_id=query.message.message_id)
        
    elif action == "browse":
        await show_browse_menu(query.message.chat_id, query.from_user.id, context, edit_message_id=query.message.message_id)
        
    elif action == "collections":
        await list_collections_flow(update, context, edit_message_id=query.message.message_id)
        
    elif action == "manage":
        await manage_collections_flow(update, context, edit_message_id=query.message.message_id)
        
    elif action == "remove":
        await remove_flow(query.message, query.from_user, context, [], edit_message_id=query.message.message_id)
        
    elif action == "id_file":
        await id_file_flow(query.message, query.from_user, context, edit_message_id=query.message.message_id)

    elif action == "enter_code":
        # Reset modes before entering
        from utils import reset_user_modes
        reset_user_modes(context)
        await access_shared_flow(query.message, query.from_user, context, args=[], edit_message_id=query.message.message_id)
    
    elif action == "new_collection": # From no active collection error menu
        await new_collection_flow(query.message, query.from_user, context, [], edit_message_id=query.message.message_id)

    elif action == "select_collection": # From no active collection error menu
         await list_collections_flow(update, context, edit_message_id=query.message.message_id)

async def handle_back_to_main_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    # Remove any lingering Message IDs from user_data that might confuse flows
    # (But keep main_menu_msg_id if we want to reuse it)
    
    try:
        await query.edit_message_text(
            text=get_main_menu_text(),
            reply_markup=build_main_menu_keyboard()
        )
    except Exception:
        # If edit fails, send new
        await query.message.reply_text(
            text=get_main_menu_text(),
            reply_markup=build_main_menu_keyboard()
        )

async def handle_manage_collection_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show management options for a specific collection"""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    # manage_collection:<id>
    
    try:
        _, col_id_str = data.split(":")
        collection_id = int(col_id_str)
    except ValueError:
        return
        
    is_allowed, error_msg, collection = check_collection_access(query.from_user.id, collection_id)
    if not is_allowed:
        await query.edit_message_text(error_msg)
        return

    # Check if admin is viewing
    is_admin_view = is_admin(query.from_user.id) and collection[2] != query.from_user.id
    
    keyboard = []
    
    if not is_admin_view:
        # Standard owner options
        keyboard = [
            [InlineKeyboardButton("📤 ייצוא לקובץ (גיבוי)", callback_data=f"export_collection:{collection_id}")],
            [InlineKeyboardButton("🔗 יצירת קישור שיתוף", callback_data=f"share_collection:{collection_id}")],
            [InlineKeyboardButton("🗑 מחיקת אוסף", callback_data=f"delete_collection:{collection_id}")],
            [InlineKeyboardButton("🔙 חזור לרשימה", callback_data="back_to_manage")],
        ]
    else:
        # Admin options
        keyboard = [
            [InlineKeyboardButton("📂 צפייה בתוכן (Admin)", callback_data=f"browse_page:{collection_id}:1")],
            [InlineKeyboardButton("🗑 מחיקת אוסף (Admin)", callback_data=f"delete_collection:{collection_id}")],
            [InlineKeyboardButton("🔙 חזור לרשימה", callback_data="back_to_manage")],
        ]
    
    await query.edit_message_text(
        f"ניהול אוסף: **{collection[1]}**",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

async def handle_share_collection_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generate or display share code for a collection"""
    query = update.callback_query
    await query.answer()
    user = query.from_user
    
    try:
        _, col_id_str = query.data.split(":")
        collection_id = int(col_id_str)
    except ValueError:
        return

    is_allowed, error_msg, collection = check_collection_access(query.from_user.id, collection_id)
    if not is_allowed:
        return
        
    collection_name = collection[1]

    # Get or create share code (logic from OLD.py)
    share_code = db.create_share_link(collection_id, user.id)
    
    # Get access logs
    logs = db.get_share_access_logs(collection_id)
    access_count = len(logs)
    
    args = [str(collection_name), str(share_code), str(access_count)]
    text = (
        "🔗 קוד שיתוף לאוסף: {}\n\n"
        "📋 קוד: `{}`\n\n"
        "👥 מספר גישות: {}\n\n"
        "💡 שלח את הקוד הזה למשתמשים אחרים.\n"
        "הם יוכלו לגשת לאוסף באמצעות הפקודה /access."
    ).format(*args)

    keyboard = [
        [InlineKeyboardButton("📊 סטטיסטיקות גישה", callback_data=f"share_stats:{collection_id}")],
        [InlineKeyboardButton("🔄 חידוש קוד", callback_data=f"regenerate_share:{collection_id}")],
        [InlineKeyboardButton("❌ ביטול שיתוף", callback_data=f"revoke_share:{collection_id}")],
        [InlineKeyboardButton("⬅️ חזור", callback_data=f"manage_collection:{collection_id}")]
    ]

    await query.edit_message_text(
        text=text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

async def handle_share_stats_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show access statistics for a shared collection"""
    query = update.callback_query
    await query.answer()
    
    try:
        _, col_id_str = query.data.split(":")
        collection_id = int(col_id_str)
    except ValueError:
        return

    # Check ownership
    is_allowed, _, _ = check_collection_access(query.from_user.id, collection_id)
    if not is_allowed:
        return
        
    logs = db.get_share_access_logs(collection_id)
    
    if not logs:
        text = "📊 אין עדיין צפיות באוסף המשותף הזה."
    else:
        text = f"📊 **סטטיסטיקות צפייה ({len(logs)} משתמשים):**\n\n"
        for user_id, username, first_name, accessed_at in logs:
            name = f"{first_name} " + (f"(@{username})" if username else "")
            # accessed_at format from SQLite is typically "YYYY-MM-DD HH:MM:SS..."
            # We want "YYYY-MM-DD HH:MM"
            try:
                date_str = accessed_at[:16].replace("T", " ")
            except Exception:
                date_str = accessed_at 
                
            text += f"👤 {name} - {date_str}\n"

    keyboard = [[InlineKeyboardButton("🔙 חזור", callback_data=f"share_collection:{collection_id}")]]
    
    await query.edit_message_text(
        text=text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

async def handle_regenerate_share_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Regenerate share code for a collection"""
    query = update.callback_query
    await query.answer("קוד שיתוף הוחלף")
    
    try:
        _, col_id_str = query.data.split(":")
        collection_id = int(col_id_str)
    except ValueError:
        return
        
    new_code = db.regenerate_share_code(collection_id, query.from_user.id)
    
    if new_code:
        # Provide same view as initial share creation but updated
        text = (
            f"🔄 **הקוד הוחלף בהצלחה!**\n\n"
            f"הקוד החדש:\n`{new_code}`\n\n"
            f"הקוד הישן מבוטל ולא יעבוד יותר."
        )
        keyboard = [
             [InlineKeyboardButton("🔙 חזור", callback_data=f"manage_collection:{collection_id}")]
        ]
        await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def handle_revoke_share_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Revoke share code for a collection"""
    query = update.callback_query
    await query.answer("שיתוף בוטל")
    
    try:
        _, col_id_str = query.data.split(":")
        collection_id = int(col_id_str)
    except ValueError:
        return
        
    db.revoke_share_code(collection_id, query.from_user.id)
    
    await query.edit_message_text(
        "🚫 השיתוף בוטל בהצלחה.\nאף אחד לא יוכל לגשת לאוסף יותר דרך קוד.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 חזור לניהול", callback_data=f"manage_collection:{collection_id}")]
        ])
    )

async def handle_export_collection_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("מכין קובץ גיבוי...")
    
    try:
        _, col_id_str = query.data.split(":")
        collection_id = int(col_id_str)
    except ValueError:
        return
        
    # Get items
    items = db.get_items_by_collection(collection_id, limit=100000) # Get all
    if not items:
        await query.edit_message_text("האוסף ריק, אין מה לייצא.")
        return
        
    is_allowed, _, collection = check_collection_access(query.from_user.id, collection_id)
    if not is_allowed:
        return
        
    # Generate TXT content
    # Format: CONTENT_TYPE|FILE_ID|TEXT|FILENAME|SIZE
    lines = []
    lines.append(f"# COLLECTION EXPORT: {collection[1]}")
    lines.append(f"# DATE: {db.datetime.now()}")
    lines.append("# DO NOT EDIT THIS FILE")
    lines.append("")
    
    for item in items:
        # item: id, content_type, file_id, text_content, file_name, file_size, added_at
        c_type = item[1]
        f_id = item[2]
        text = item[3] or ""
        text = text.replace("|", "<PIPE>") # Escape pipe
        text = text.replace("\n", "<NL>") # Escape newline
        f_name = item[4] or ""
        f_size = str(item[5]) if item[5] else "0"
        
        line = f"{c_type}|{f_id}|{text}|{f_name}|{f_size}"
        lines.append(line)
        
    content = "\n".join(lines)
    
    # Send as document
    from io import BytesIO
    bio = BytesIO(content.encode('utf-8'))
    bio.name = f"Build_Collection_{collection_id}_backup.txt"
    
    await context.bot.send_document(
        chat_id=query.message.chat_id,
        document=bio,
        caption=f"📦 גיבוי מלא לאוסף: {collection[1]}",
        filename=bio.name
    )

async def handle_delete_collection_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Delete a collection with confirmation"""
    # Simply redirects to shared handle_delete_select_collection_callback logic or similar
    # But since we have the ID already, we can reuse logic.
    await handle_delete_select_collection_callback(update, context)

async def handle_back_to_manage_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Go back to manage collections list"""
    query = update.callback_query
    await query.answer()
    
    await manage_collections_flow(update, context, edit_message_id=query.message.message_id)

async def handle_exit_shared_collection_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Exit from viewing a shared collection"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    if user_id in active_shared_collections:
        del active_shared_collections[user_id]
        
    await query.edit_message_text(
        "יצאת מהאוסף המשותף.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 תפריט ראשי", callback_data="back_to_main")]])
    )

async def handle_cancel_share_access_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel share code input"""
    query = update.callback_query
    await query.answer()
    
    if "waiting_for_share_code" in context.user_data:
        del context.user_data["waiting_for_share_code"]
        
    await query.edit_message_text(
        "ביטלת את הכניסה לאוסף.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 תפריט ראשי", callback_data="back_to_main")]])
    )

async def handle_exit_delete_mode_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Exit from delete mode"""
    query = update.callback_query
    await query.answer()
    
    reset_user_modes(context)
    
    await query.edit_message_text(
        "יצאת ממצב מחיקה.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 תפריט ראשי", callback_data="back_to_main")]])
    )
