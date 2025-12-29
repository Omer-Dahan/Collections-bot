from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes
import db
from config import is_admin
from constants import active_collections
from utils import (
    record_activity, get_user_keyboard, get_main_menu_text, 
    build_main_menu_keyboard, send_response, show_collections_menu, 
    show_collection_page, check_collection_access, logger
)

@record_activity
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

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
    # Create a temporary Update object for send_response
    temp_update = type('obj', (object,), {'effective_chat': message.chat, 'effective_user': user})()
    
    if not args:
        context.user_data["creating_collection_mode"] = True
        
        text = "מתחיל ביצירת אוסף חדש 🗂\nאיך לקרוא לאוסף?"
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📥 יבוא אוסף מקובץ", callback_data="import_collection_mode")],
            [InlineKeyboardButton("🏠 חזור לתפריט ראשי", callback_data="back_to_main")]
        ])
        await send_response(temp_update, context, text, keyboard, edit_message_id)
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

@record_activity
async def new_collection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    await new_collection_flow(update.message, user, context, context.args)

async def list_collections_flow(update: Update, context: ContextTypes.DEFAULT_TYPE, edit_message_id: int = None):
    user = update.effective_user
    await show_collections_menu(update, context, user.id, "select_collection", "בחר אוסף פעיל:", edit_message_id)

@record_activity
async def list_collections(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    
    await list_collections_flow(update, context)

async def manage_collections_flow(update: Update, context: ContextTypes.DEFAULT_TYPE, edit_message_id: int = None):
    user = update.effective_user
    
    await show_collections_menu(
        update, context, user.id, "manage_collection", 
        "בחר אוסף לניהול:", edit_message_id
    )

@record_activity
async def manage_collections(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ניהול אוספים - ייצוא ומחיקה"""
    user = update.effective_user
    
    await manage_collections_flow(update.message, user, context)

async def show_browse_menu(chat_id: int, user_id: int, context: ContextTypes.DEFAULT_TYPE, edit_message_id: int = None):
    """
    Shared function to display browse menu.
    Can be used from both command handlers and callback queries.
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
    keyboard.append([InlineKeyboardButton("🏠 חזור לתפריט ראשי", callback_data="back_to_main")])

    text = "בחר אוסף לדפדוף:"
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Create temp Update object for send_response
    temp_update = type('obj', (object,), {'effective_chat': type('obj', (object,), {'id': chat_id})()})()
    await send_response(temp_update, context, text, reply_markup, edit_message_id)

@record_activity
async def browse(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """פקודת /browse - בחירת אוסף לדפדוף"""
    user = update.effective_user

    await show_browse_menu(
        chat_id=update.message.chat_id,
        user_id=user.id,
        context=context
    )

async def remove_flow(message, user, context, args: list[str], edit_message_id: int = None):
    # If a specific collection is provided in args, we initiate delete for it
    if args:
        # Note: Handling by name is tricky if names aren't unique or have spaces. 
        # Better to keep it interactive.
        pass

    # Activate Item Deletion Mode by asking for collection first
    
    # Create temp update for helper
    temp_update = type('obj', (object,), {'effective_chat': message.chat, 'effective_user': user})()
    
    await show_collections_menu(
        temp_update, context, user.id, "select_item_del_col", 
        "🗑 בחר אוסף למחיקת פריטים:", edit_message_id
    )

@record_activity
async def remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    await remove_flow(update.message, user, context, context.args)

async def id_file_flow(message, user, context, edit_message_id: int = None):
    context.user_data["id_mode"] = True
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🏠 חזור לתפריט ראשי", callback_data="back_to_main")]
    ])
    
    # Use temp update object if passing a message object
    temp_update = type('obj', (object,), {'effective_chat': message.chat})()
    await send_response(
        temp_update, context, 
        "🔍 מצב זיהוי הופעל.\nשלח לי כל קובץ, ואחזיר לך את ה-file_id שלו.", 
        keyboard, edit_message_id
    )

@record_activity
async def id_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    await id_file_flow(update.message, user, context)

@record_activity
async def access_shared(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Command to access a shared collection via code"""
    user = update.effective_user
    
    # Check if code provided in args
    if context.args:
        # direct access
        from handlers.messages import activate_shared_collection # Implicit circular import prevention
        await activate_shared_collection(update, context, context.args[0])
        return

    # Interactive mode
    context.user_data["waiting_for_share_code"] = True
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ ביטול", callback_data="cancel_share_access")]
    ])
    await update.message.reply_text(
        "אנא שלח את קוד השיתוף שקיבלת:",
        reply_markup=keyboard
    )
