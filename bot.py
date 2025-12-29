import logging
import db
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    AIORateLimiter,
)
from telegram.request import HTTPXRequest
from config import BOT_TOKEN
from admin_panel import admin_panel, handle_admin_callback
from utils import error_handler, UserActionFilter, logger

# Import all handlers from our new package
from handlers import (
    start, new_collection, list_collections, manage_collections, browse, 
    remove, id_file, access_shared,
    handle_select_collection_callback, handle_browse_page_callback,
    handle_scroll_view_callback, handle_page_info_callback,
    handle_browse_group_or_select_all_callback, handle_page_file_send_choice_callback,
    handle_batch_status_callback, handle_collection_send_all_callback,
    handle_stop_collect_callback, handle_delete_select_collection_callback,
    handle_main_menu_button, handle_back_to_main_callback,
    handle_manage_collection_callback, handle_share_collection_callback,
    handle_share_stats_callback, handle_regenerate_share_callback,
    handle_revoke_share_callback, handle_export_collection_callback,
    handle_delete_collection_callback, handle_back_to_manage_callback,
    handle_exit_shared_collection_callback, handle_cancel_share_access_callback,
    handle_exit_delete_mode_callback, handle_import_collection_mode_callback,
    handle_select_item_delete_col_callback,
    handle_message
)

def setup_logging():
    # File handler - only user actions and errors
    file_handler = logging.FileHandler("bot.log", encoding="utf-8")
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s]: %(message)s"))
    file_handler.addFilter(UserActionFilter())

    # Console handler - everything
    import sys
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))

    # Define root logger
    logging.basicConfig(
        level=logging.INFO,
        handlers=[file_handler, console_handler]
    )

def main():
    setup_logging()
    
    # Initialize DB
    db.init_db()
    
    logger.info("Bot starting...")

    request = HTTPXRequest(connection_pool_size=8, read_timeout=30.0, write_timeout=30.0, connect_timeout=30.0, pool_timeout=30.0)
    app = ApplicationBuilder().token(BOT_TOKEN).rate_limiter(AIORateLimiter()).request(request).build()

    # --- Error Handler ---
    app.add_error_handler(error_handler)

    # --- Commands ---
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("newcollection", new_collection))
    app.add_handler(CommandHandler("collections", list_collections))
    app.add_handler(CommandHandler("manage", manage_collections))
    app.add_handler(CommandHandler("browse", browse))
    app.add_handler(CommandHandler("remove", remove))
    app.add_handler(CommandHandler("id_file", id_file))
    app.add_handler(CommandHandler("access", access_shared))
    
    app.add_handler(CommandHandler("admin", admin_panel)) # External module
    app.add_handler(CommandHandler("adminpanel", admin_panel))

    # --- Callback Handlers ---
    
    # Main Menu
    app.add_handler(CallbackQueryHandler(handle_main_menu_button, pattern="^main_menu:"))
    app.add_handler(CallbackQueryHandler(handle_back_to_main_callback, pattern="^back_to_main$"))
    
    # Collection Selection & Creation
    app.add_handler(CallbackQueryHandler(handle_select_collection_callback, pattern="^select_collection:"))
    app.add_handler(CallbackQueryHandler(handle_import_collection_mode_callback, pattern="^import_collection_mode$"))
    app.add_handler(CallbackQueryHandler(handle_stop_collect_callback, pattern="^stop_collect$"))
    
    # Browsing & Viewing
    app.add_handler(CallbackQueryHandler(handle_browse_page_callback, pattern="^browse_page:"))
    app.add_handler(CallbackQueryHandler(handle_scroll_view_callback, pattern="^scroll_view:"))
    app.add_handler(CallbackQueryHandler(handle_page_info_callback, pattern="^page_info:"))
    app.add_handler(CallbackQueryHandler(handle_browse_group_or_select_all_callback, pattern="^(browse_group|browse_page_select_all):"))
    app.add_handler(CallbackQueryHandler(handle_page_file_send_choice_callback, pattern="^page_files_"))
    app.add_handler(CallbackQueryHandler(handle_collection_send_all_callback, pattern="^collection_send_all:"))
    app.add_handler(CallbackQueryHandler(handle_batch_status_callback, pattern="^batch_status:"))

    # Management
    app.add_handler(CallbackQueryHandler(handle_manage_collection_callback, pattern="^manage_collection:"))
    app.add_handler(CallbackQueryHandler(handle_share_collection_callback, pattern="^share_collection:"))
    app.add_handler(CallbackQueryHandler(handle_share_stats_callback, pattern="^share_stats:"))
    app.add_handler(CallbackQueryHandler(handle_regenerate_share_callback, pattern="^regenerate_share:"))
    app.add_handler(CallbackQueryHandler(handle_revoke_share_callback, pattern="^revoke_share:"))
    app.add_handler(CallbackQueryHandler(handle_export_collection_callback, pattern="^export_collection:"))
    app.add_handler(CallbackQueryHandler(handle_delete_collection_callback, pattern="^delete_collection:"))
    app.add_handler(CallbackQueryHandler(handle_back_to_manage_callback, pattern="^back_to_manage$"))
    
    # Deletion & Sharing Access
    app.add_handler(CallbackQueryHandler(handle_select_item_delete_col_callback, pattern="^select_item_del_col:"))
    app.add_handler(CallbackQueryHandler(handle_exit_delete_mode_callback, pattern="^exit_delete_mode$"))
    app.add_handler(CallbackQueryHandler(handle_exit_shared_collection_callback, pattern="^exit_shared_collection$"))
    app.add_handler(CallbackQueryHandler(handle_cancel_share_access_callback, pattern="^cancel_share_access$"))
    
    # External Admin Handlers
    app.add_handler(CallbackQueryHandler(handle_admin_callback, pattern="^(admin_|user_stats|system_stats|broadcast|backup_db)"))

    # Everything else (Text, Photo, Video, Document, etc.)
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_message))

    logger.info("Polling...")
    app.run_polling()

if __name__ == "__main__":
    main()