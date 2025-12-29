from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes
import db
from config import is_admin
from constants import active_collections, active_shared_collections, MSG_NO_COLLECTIONS
from utils import (
    track_and_reset_user, verify_user_code, update_batch_status, # set_user_mode is implicit via direct context
    send_response, show_collection_page, format_size, logger,
    check_collection_access, prepare_media_groups, send_media_groups_in_chunks,
    extract_file_info
)

async def handle_new_collection_name_input(message, context: ContextTypes.DEFAULT_TYPE):
    """Handle text input for new collection name"""
    name = message.text.strip()
    user = message.from_user
    
    if len(name) < 2:
        await message.reply_text("השם קצר מדי, נסה שוב:")
        return

    try:
        collection_id = db.create_collection(name, user.id)
        active_collections[user.id] = collection_id
        
        # Clear creating mode
        if "creating_collection_mode" in context.user_data:
            del context.user_data["creating_collection_mode"]
        
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton(text="🛑 הפסק הוספה", callback_data="stop_collect")]]
        )
        
        await message.reply_text(
            f"✅ אוסף חדש נוצר: {name}\n\n"
            f"🔄 מתחיל מצב איסוף...\n"
            f"העלה עכשיו קבצים והם יתווספו לאוסף.",
            reply_markup=keyboard
        )

    except Exception as e:
        logger.error(f"Error creating collection: {e}")
        if "UNIQUE constraint failed" in str(e):
             # Ask for retry
             context.user_data["temp_collection_name"] = name 
             keyboard = InlineKeyboardMarkup([
                 [InlineKeyboardButton("♻️ נסה שם אחר", callback_data="retry_create_collection")],
                 [InlineKeyboardButton("❌ ביטול", callback_data="back_to_main")]
             ])
             await message.reply_text(f"❌ כבר יש לך אוסף בשם '{name}'.", reply_markup=keyboard)
        else:
            await message.reply_text("אירעה שגיאה ביצירת האוסף/.")

async def handle_import_collection_mode_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the import mode activation"""
    query = update.callback_query
    await query.answer()
    
    context.user_data["import_mode"] = True
    context.user_data.pop("creating_collection_mode", None)
    
    await query.edit_message_text(
        "📂 **מצב יבוא אוסף**\n\n"
        "שלח לי עכשיו את קובץ הגיבוי (.txt) שקיבלת מהבוט.\n"
        "אני אצור אוסף חדש מהתוכן שלו.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 ביטול", callback_data="back_to_main")]])
    )

async def process_imported_collection(message, context: ContextTypes.DEFAULT_TYPE):
    """Process uploaded TXT file for collection import"""
    doc = message.document
    
    if not doc.file_name.endswith(".txt"):
        await message.reply_text("❌ זה לא קובץ טקסט. אנא שלח קובץ גיבוי (.txt) תקני.")
        return

    status_msg = await message.reply_text("⏳ מוריד ומעבד את קובץ הגיבוי...")
    
    try:
        file_obj = await doc.get_file()
        from io import BytesIO
        data = BytesIO()
        await file_obj.download_to_memory(data)
        content = data.getvalue().decode('utf-8')
        
        lines = content.splitlines()
        
        # Verify header
        header = lines[0] if lines else ""
        if not header.startswith("# COLLECTION EXPORT:"):
            await status_msg.edit_text("❌ הקובץ לא נראה כמו גיבוי תקין של הבוט.")
            return
            
        # Extract name from header or filename
        col_name = header.replace("# COLLECTION EXPORT:", "").strip()
        if not col_name:
            col_name = doc.file_name.replace(".txt", "").replace("_backup", "")
            
        original_name = col_name
        counter = 1
        
        # Try to create collection, append number if exists
        user_id = message.from_user.id
        collection_id = None
        
        while True:
            try:
                collection_id = db.create_collection(col_name, user_id)
                break
            except Exception as e:
                if "UNIQUE constraint failed" in str(e):
                    col_name = f"{original_name} ({counter})"
                    counter += 1
                else:
                    raise e
                    
        # Parse items
        imported_count = 0
        errors = 0
        
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
                
            parts = line.split("|")
            if len(parts) < 5:
                continue
                
            # c_type|f_id|text|f_name|f_size
            c_type = parts[0]
            f_id = parts[1]
            text = parts[2].replace("<PIPE>", "|").replace("<NL>", "\n")
            # If text is empty string, make it None
            text = text if text else None
            
            f_name = parts[3]
            f_name = f_name if f_name else None
            
            f_size = 0
            try:
                f_size = int(parts[4])
            except:
                pass
                
            # Insert
            try:
                db.add_item(
                    collection_id, c_type, f_id, text,
                    f_name, f_size
                )
                imported_count += 1
            except:
                errors += 1
                
        # Finish
        context.user_data.pop("import_mode", None)
        active_collections[user_id] = collection_id
        
        await status_msg.edit_text(
            f"✅ **היבוא הושלם בהצלחה!**\n\n"
            f"📁 שם האוסף: {col_name}\n"
            f"📦 פריטים שיובאו: {imported_count}\n" +
            (f"⚠️ שגיאות: {errors}\n" if errors > 0 else "") + 
            f"\nהאוסף הוגדר כפעיל. ניתן להוסיף לו עוד פריטים כעת.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🛑 הפסק הוספה", callback_data="stop_collect")],
                [InlineKeyboardButton("📂 צפה באוסף", callback_data=f"browse_page:{collection_id}:1")]
            ])
        )
        
    except Exception as e:
        logger.exception("Import failed")
        await status_msg.edit_text(f"❌ שגיאה ביבוא הקובץ:\n{str(e)}")

async def handle_send_collection_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handle send collection confirmation logic.
    Returns True if the message was handled (confirmation process), False otherwise.
    """
    if "verify_send_collection" not in context.user_data:
        return False
        
    message = update.message
    
    # Try to verify code
    is_valid, data = verify_user_code(message, context, "send_collection")
    
    if is_valid:
        collection_id = data["collection_id"]
        
        # Double check access
        is_allowed, _, collection = check_collection_access(message.from_user.id, collection_id)
        if not is_allowed:
             await message.reply_text("❌ שגיאת הרשאה.")
             return True
             
        status_msg = await message.reply_text(f"🚀 מאמת קוד... מתחיל שליחה של אוסף '{collection[1]}'.")
        
        # Start sending
        # Get all items
        items = db.get_items_by_collection(collection_id, limit=10000)
        
        media_visual, media_docs, text_items = prepare_media_groups(items)
        
        await status_msg.edit_text(f"📦 שולח {len(items)} פריטים...\n(זה ייקח קצת זמן)")
        
        await send_media_groups_in_chunks(context.bot, message.chat_id, media_visual, media_docs, text_items)
        
        await message.reply_text("✅ כל הפריטים נשלחו בהצלחה.")
        
        msg_id_to_delete = data.get("msg_id") 
        
        # Restore the collection page so user can continue browsing
        await show_collection_page(
             update=update, 
             context=context, 
             collection_id=collection_id, 
             page=1, 
             edit_message_id=msg_id_to_delete, # Verify function uses this to delete if force_resend is True
             force_resend=True
        )
        return True
        
    elif "verify_send_collection" in context.user_data: # Incorrect code but mode active
        await message.reply_text("❌ קוד שגוי. נסה שוב או לחץ על ביטול למעלה.")
        return True
        
    return False

async def handle_delete_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle collection deletion confirmation. Returns True if handled."""
    if "verify_delete_collection" not in context.user_data:
        return False
        
    message = update.message
    is_valid, data = verify_user_code(message, context, "delete_collection")
    
    if is_valid:
        collection_id = data["collection_id"]
        
        # Verify ownership/access again
        is_allowed, _, collection = check_collection_access(message.from_user.id, collection_id)
        if is_allowed:
            db.delete_collection(collection_id)
            # Remove from active if needed
            if active_collections.get(message.from_user.id) == collection_id:
                del active_collections[message.from_user.id]
                
            await message.reply_text(
                f"🗑 האוסף '{collection[1]}' נמחק בהצלחה.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 תפריט ראשי", callback_data="back_to_main")]])
            )
            # Exit delete mode state
            context.user_data.pop("verify_delete_collection_mode", None)
            context.user_data.pop("delete_mode", None)
        else:
            await message.reply_text("❌ שגיאה במחיקת האוסף.")
            
        return True
        
    else:
        # Check if mode is explicitly active (user typed something else)
        if context.user_data.get("verify_delete_collection_mode"):
             await message.reply_text("❌ קוד שגוי. נסה שוב או לחץ ביטול.")
             return True
             
    return False

async def handle_share_code_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle share code input from user. Returns True if handled."""
    if not context.user_data.get("waiting_for_share_code"):
        return False
        
    code = update.message.text.strip()
    await activate_shared_collection(update, context, code)
    return True

async def activate_shared_collection(update: Update, context: ContextTypes.DEFAULT_TYPE, share_code: str):
    """
    Helper to activate shared collection access.
    Consolidates logic for both /access command and interactive flow.
    """
    collection = db.get_collection_by_share_code(share_code)
    user = update.effective_user
    
    if not collection:
        await send_response(update, context, "❌ קוד שיתוף לא תקין או פג תוקף.")
        # Don't reset mode immediately so they can try again if interactive
        return

    # Success
    collection_id = collection[0]
    col_name = collection[1]
    
    # Store access
    active_shared_collections[user.id] = share_code
    db.log_share_access(collection_id, user.id)
    
    # Clear waiting mode
    if "waiting_for_share_code" in context.user_data:
        del context.user_data["waiting_for_share_code"]
        
    await send_response(
        update, context, 
        f"✅ **גישה אושרה!**\nאתה צופה באוסף המשותף: **{col_name}**",
        parse_mode="Markdown"
    )
    
    # Immediately show the collection
    await show_collection_page(
        update=update,
        context=context,
        collection_id=collection_id,
        page=1
    )

async def handle_id_file_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    
    # Only if file
    file_info = extract_file_info(message)
    file_id = file_info["file_id"] if file_info else None
        
    if not file_id:
         # Check if it is a text message with an ID we need to send
         # This part was requested to be added to support sending files by ID
         pass
         return

    # Reply with code
    await message.reply_text(
        f"✅ **File ID Detected:**\n`{file_id}`",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 תפריט ראשי", callback_data="back_to_main")]])
    )

async def handle_item_delete_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle messages when in Item Deletion Mode"""
    message = update.message
    user = update.effective_user
    
    # Extract file ID
    file_info = extract_file_info(message)
    file_id = file_info["file_id"] if file_info else None
        
    if not file_id:
        await message.reply_text("❌ לא זוהה קובץ. אנא שלח תמונה, וידאו או מסמך למחיקה.")
        return
        
    collection_id = context.user_data.get("delete_target_collection_id")
    if not collection_id:
        # Fallback if somehow missing
        collection_id = active_collections.get(user.id)

    if not collection_id:
        await message.reply_text("⚠️ לא נבחר אוסף למחיקה.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 תפריט ראשי", callback_data="back_to_main")]]))
        del context.user_data["item_delete_mode"]
        return

    # Delete from DB
    success = db.delete_item(collection_id, file_id)
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🏁 סיום מחיקה", callback_data="back_to_main")]
    ])
    
    if success:
        await message.reply_text(
            "✅ **הפריט נמחק בהצלחה.**\nשלח פריט נוסף למחיקה או לחץ על סיום.",
            reply_markup=keyboard,
            parse_mode="Markdown"
        )
    else:
        await message.reply_text(
            "⚠️ **הפריט לא נמצא באוסף הפעיל.**\nודא שאתה שולח את הקובץ הנכון מאותו האוסף.",
            reply_markup=keyboard,
            parse_mode="Markdown"
        )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    # track_and_reset_user clears modes, which breaks flows like creating_collection_mode.
    # We only want to upsert the user here.
    if user:
        db.upsert_user(user.id, user.username, user.first_name, user.last_name)
    message = update.message
    
    # 1. Handle flows that intercept messages
    if await handle_send_collection_confirmation(update, context):
        return
        
    if await handle_delete_confirmation(update, context):
        return

    if await handle_share_code_input(update, context):
        return

    # 2. Check for Mode Flags
    if context.user_data.get("creating_collection_mode"):
        await handle_new_collection_name_input(message, context)
        return

    if context.user_data.get("id_mode"):
        await handle_id_file_message(update, context)
        return

    if context.user_data.get("item_delete_mode"):
        await handle_item_delete_message(update, context)
        return

    if context.user_data.get("import_mode") and message.document:
        await process_imported_collection(message, context)
        return

    # 3. Handle Collection Item Addition (Default behavior)
    # Check if user has active collection
    collection_id = active_collections.get(user.id)
    
    # Check if user sent a number (Requesting file by ID from info page)
    if message.text and message.text.isdigit():
        target_id = int(message.text)
        allowed_ids = context.user_data.get("allowed_item_ids", [])
        info_col_id = context.user_data.get("info_page_collection_id")
        
        if info_col_id and target_id in allowed_ids:
             # Fetch item by ID
             try:
                 if hasattr(db, 'get_item_by_id'):
                    item = db.get_item_by_id(target_id)
                    if item:
                        # item has 8 elements (includes collection_id at index 1), prepare_media_groups expects 7
                        # Convert: (id, col_id, type, ...) -> (id, type, ...)
                        item_for_utils = (item[0],) + item[2:]
                        
                        # Send it
                        media_visual, media_docs, text_items = prepare_media_groups([item_for_utils])
                        await send_media_groups_in_chunks(context.bot, message.chat_id, media_visual, media_docs, text_items)
                        return
                 else:
                    await message.reply_text("מצטער, הפונקציה לשליפה לפי ID חסרה בבסיס הנתונים כרגע.")
                    return
             except Exception as e:
                 logger.error(f"Failed to fetch item by ID: {e}")
                 return

    if not collection_id:
        # No active collection
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📁 צור אוסף חדש", callback_data="main_menu:new_collection")],
            [InlineKeyboardButton("📂 בחר אוסף קיים", callback_data="main_menu:select_collection")],
            [InlineKeyboardButton("🏠 תפריט ראשי", callback_data="back_to_main")]
        ])
        await message.reply_text(
            "⚠️ **אין אוסף פעיל.**\n\n"
            "כדי לשמור דברים, צריך לבחור לאיזה אוסף להוסיף אותם,\n"
            "או ליצור אוסף חדש.",
            reply_markup=keyboard,
            parse_mode="Markdown"
        )
        return
        
    # Check content
    file_info = extract_file_info(message)
    
    if not file_info:
        await message.reply_text("סוג תוכן לא נתמך.")
        return

    content_type = file_info["content_type"]
    file_id = file_info["file_id"]
    text_content = file_info["text_content"]
    f_name = file_info["file_name"]
    f_size = file_info["file_size"]

    # Add to DB
    try:
        db.add_item(
            collection_id, content_type, file_id, text_content, f_name, f_size
        )
        # Verify collection name available
        col_data = db.get_collection_by_id(collection_id)
        col_name = col_data[1] if col_data else "Unknown"
        
        # Trigger batch notification
        await update_batch_status(message, context, col_name)
        
        # Try to delete user message to keep chat clean (optional, user preference)
        # await message.delete() 
        
    except Exception as e:
        logger.error(f"Error adding item: {e}")
        await message.reply_text("שגיאה בשמירת הפריט.")

async def handle_delete_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # This handler monitors deletions
    pass
