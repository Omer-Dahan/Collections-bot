# db.py
import sqlite3
from datetime import datetime
from config import ADMIN_IDS

DB_PATH = "bot_data.db"


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def migrate_db():
    """Add user_id column to collections if it doesn't exist."""
    conn = get_connection()
    cur = conn.cursor()
    
    # Check if user_id column exists
    cur.execute("PRAGMA table_info(collections)")
    columns = [info[1] for info in cur.fetchall()]
    
    if "user_id" not in columns:
        print("Migrating database: Adding user_id to collections...")
        # Add the column
        cur.execute("ALTER TABLE collections ADD COLUMN user_id INTEGER")
        
        # Assign existing collections to the first admin
        default_admin_id = ADMIN_IDS[0] if ADMIN_IDS else 0
        cur.execute("UPDATE collections SET user_id = ?", (default_admin_id,))
        conn.commit()
        print(f"Migration complete. All existing collections assigned to {default_admin_id}")
    
    # Check if blocked column exists in users table
    cur.execute("PRAGMA table_info(users)")
    user_columns = [info[1] for info in cur.fetchall()]
    
    if "blocked" not in user_columns:
        print("Migrating database: Adding blocked column to users...")
        cur.execute("ALTER TABLE users ADD COLUMN blocked INTEGER DEFAULT 0")
        conn.commit()
        print("Migration complete. Added blocked column to users table.")
    
    conn.close()


def init_db():
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS collections (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        user_id INTEGER NOT NULL,
        UNIQUE(name, user_id)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        collection_id INTEGER NOT NULL,
        content_type TEXT NOT NULL,  -- "video", "photo", "document", "text"
        file_id TEXT,
        text_content TEXT,
        file_name TEXT,
        file_size INTEGER,
        added_at TEXT NOT NULL,
        FOREIGN KEY (collection_id) REFERENCES collections(id)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        last_name TEXT,
        first_seen TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS shared_collections (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        collection_id INTEGER NOT NULL,
        share_code TEXT NOT NULL UNIQUE,
        created_at TEXT NOT NULL,
        created_by INTEGER NOT NULL,
        is_active INTEGER DEFAULT 1,
        FOREIGN KEY (collection_id) REFERENCES collections(id) ON DELETE CASCADE
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS shared_collection_access_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        share_code TEXT NOT NULL,
        user_id INTEGER NOT NULL,
        accessed_at TEXT NOT NULL,
        FOREIGN KEY (share_code) REFERENCES shared_collections(share_code)
    )
    """)
    
    # Add indices for better performance
    cur.execute("CREATE INDEX IF NOT EXISTS idx_items_collection ON items(collection_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_items_file_id ON items(file_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_collections_user ON collections(user_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_shared_collections_code ON shared_collections(share_code)")
    
    conn.commit()
    conn.close()
    
    # Run migration to ensure existing DBs are updated
    migrate_db()


def create_collection(name: str, user_id: int) -> int:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("INSERT INTO collections (name, user_id) VALUES (?, ?)", (name, user_id))
    conn.commit()
    collection_id = cur.lastrowid
    conn.close()
    return collection_id


def get_collections(user_id: int) -> list:
    """Get collections for a specific user."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, name FROM collections WHERE user_id = ? ORDER BY id", (user_id,))
    rows = cur.fetchall()
    conn.close()
    return rows


def get_collection_by_id(collection_id: int) -> tuple | None:
    """Get collection details including user_id."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, name, user_id FROM collections WHERE id = ?", (collection_id,))
    row = cur.fetchone()
    conn.close()
    return row


def add_item(
    collection_id: int,
    content_type: str,
    file_id: str | None = None,
    text_content: str | None = None,
    file_name: str | None = None,
    file_size: int | None = None,
):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO items (collection_id, content_type, file_id, text_content, file_name, file_size, added_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            collection_id,
            content_type,
            file_id,
            text_content,
            file_name,
            file_size,
            datetime.utcnow().isoformat(),
        )
    )
    conn.commit()
    conn.close()


def is_duplicate_file(collection_id: int, file_id: str, file_size: int | None) -> bool:
    """
    Check if a file with the same file_id and file_size already exists in the collection.
    
    Args:
        collection_id: Collection ID to check within
        file_id: Telegram file_id
        file_size: File size in bytes
        
    Returns:
        True if duplicate found, False otherwise
    """
    conn = get_connection()
    cur = conn.cursor()
    
    # If file_size is None, only check file_id
    if file_size is None:
        cur.execute(
            """
            SELECT COUNT(*) FROM items 
            WHERE collection_id = ? AND file_id = ? AND file_size IS NULL
            """,
            (collection_id, file_id)
        )
    else:
        cur.execute(
            """
            SELECT COUNT(*) FROM items 
            WHERE collection_id = ? AND file_id = ? AND file_size = ?
            """,
            (collection_id, file_id, file_size)
        )
    
    count = cur.fetchone()[0]
    conn.close()
    
    return count > 0


def get_items_by_collection(collection_id: int, offset: int = 0, limit: int = 10) -> list:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, content_type, file_id, text_content, file_name, file_size, added_at
        FROM items
        WHERE collection_id = ?
        ORDER BY id
        LIMIT ? OFFSET ?
        """,
        (collection_id, limit, offset)
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def count_items_in_collection(collection_id: int) -> int:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(*) FROM items WHERE collection_id = ?",
        (collection_id,)
    )
    (count,) = cur.fetchone()
    conn.close()
    return count


def delete_item_by_id(item_id: int, user_id: int) -> int:
    conn = get_connection()
    cur = conn.cursor()
    # Only delete if item belongs to a collection owned by user
    cur.execute("""
        DELETE FROM items 
        WHERE id = ? 
        AND collection_id IN (SELECT id FROM collections WHERE user_id = ?)
    """, (item_id, user_id))
    deleted = cur.rowcount
    conn.commit()
    conn.close()
    return deleted


def delete_items_by_file_id(file_id: str, user_id: int) -> int:
    conn = get_connection()
    cur = conn.cursor()
    # Only delete if item belongs to a collection owned by user
    cur.execute("""
        DELETE FROM items 
        WHERE file_id = ? 
        AND collection_id IN (SELECT id FROM collections WHERE user_id = ?)
    """, (file_id, user_id))
    deleted = cur.rowcount
    conn.commit()
    conn.close()
    return deleted


def delete_all_items_in_collection(collection_id: int) -> int:
    """מחיקת כל הפריטים באוסף"""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM items WHERE collection_id = ?", (collection_id,))
    deleted = cur.rowcount
    conn.commit()
    conn.close()
    return deleted


def delete_collection(collection_id: int) -> bool:
    """מחיקת אוסף (לאחר מחיקת כל הפריטים)"""
    import logging
    logger = logging.getLogger(__name__)
    
    conn = get_connection()
    cur = conn.cursor()
    try:
        # Step 1: Delete access logs for share codes of this collection
        cur.execute("""
            DELETE FROM shared_collection_access_log 
            WHERE share_code IN (
                SELECT share_code FROM shared_collections 
                WHERE collection_id = ?
            )
        """, (collection_id,))
        deleted_logs = cur.rowcount
        if deleted_logs > 0:
            logger.info(f"Deleted {deleted_logs} access log(s) for collection {collection_id}")
        
        # Step 2: Delete any shared_collections records
        cur.execute("DELETE FROM shared_collections WHERE collection_id = ?", (collection_id,))
        deleted_shares = cur.rowcount
        if deleted_shares > 0:
            logger.info(f"Deleted {deleted_shares} share record(s) for collection {collection_id}")
        
        # Step 3: Delete the collection itself
        cur.execute("DELETE FROM collections WHERE id = ?", (collection_id,))
        conn.commit()
        success = cur.rowcount > 0
        
        if success:
            logger.info(f"Successfully deleted collection {collection_id}")
        else:
            logger.warning(f"No collection found with id {collection_id}")
            
    except Exception as e:
        logger.error(f"Error deleting collection {collection_id}: {type(e).__name__}: {e}")
        success = False
    finally:
        conn.close()
    return success


def get_all_collections_paginated(offset: int = 0, limit: int = 12) -> list:
    """
    Get all collections with user details, paginated.
    Returns list of tuples: (col_id, col_name, user_id, username, first_name)
    """
    conn = get_connection()
    cur = conn.cursor()
    
    cur.execute("""
        SELECT c.id, c.name, c.user_id, u.username, u.first_name
        FROM collections c
        LEFT JOIN users u ON c.user_id = u.user_id
        ORDER BY c.id DESC
        LIMIT ? OFFSET ?
    """, (limit, offset))
    
    rows = cur.fetchall()
    conn.close()
    return rows


def count_all_collections() -> int:
    """Count total number of collections in the system."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM collections")
    count = cur.fetchone()[0]
    conn.close()
    return count


# --- Admin / Global Functions ---

def get_all_users_with_collections() -> list:
    """Get list of distinct user_ids that have collections."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT user_id FROM collections")
    rows = [row[0] for row in cur.fetchall()]
    conn.close()
    return rows


def transfer_collection_ownership(collection_id: int, new_user_id: int) -> bool:
    """Transfer a collection to another user."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("UPDATE collections SET user_id = ? WHERE id = ?", (new_user_id, collection_id))
        conn.commit()
        success = cur.rowcount > 0
    except Exception:
        success = False
    finally:
        conn.close()
    return success


def clone_collection_for_user(source_collection_id: int, target_user_id: int) -> int:
    """
    Create a copy of an existing collection and all its items
    for another user (for example: the admin).

    Returns the new collection id, or 0 on failure.
    """
    conn = get_connection()
    cur = conn.cursor()
    try:
        # Get collection name
        cur.execute("SELECT name FROM collections WHERE id = ?", (source_collection_id,))
        row = cur.fetchone()
        if not row:
            return 0

        original_name = row[0]

        # Create new collection for target user
        cur.execute(
            "INSERT INTO collections (name, user_id) VALUES (?, ?)",
            (original_name, target_user_id),
        )
        new_collection_id = cur.lastrowid

        # Copy all items from source collection to new collection
        cur.execute(
            """
            INSERT INTO items (collection_id, content_type, file_id, text_content, file_name, file_size, added_at)
            SELECT ?, content_type, file_id, text_content, file_name, file_size, added_at
            FROM items
            WHERE collection_id = ?
            """,
            (new_collection_id, source_collection_id),
        )

        conn.commit()
        return new_collection_id
    except Exception:
        conn.rollback()
        return 0
    finally:
        conn.close()


def get_global_stats() -> dict:
    """Get global statistics."""
    conn = get_connection()
    cur = conn.cursor()
    
    cur.execute("SELECT COUNT(*) FROM collections")
    total_collections = cur.fetchone()[0]
    
    cur.execute("SELECT COUNT(*) FROM items")
    total_items = cur.fetchone()[0]
    
    cur.execute("SELECT COUNT(DISTINCT user_id) FROM collections")
    total_users = cur.fetchone()[0]
    
    conn.close()
    
    return {
        "collections": total_collections,
        "items": total_items,
        "users": total_users
    }


def upsert_user(user_id: int, username: str | None, first_name: str | None, last_name: str | None):
    """Insert or update user details."""
    conn = get_connection()
    cur = conn.cursor()
    
    # Check if user exists
    cur.execute("SELECT first_seen FROM users WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    
    if row:
        # Update existing
        cur.execute("""
            UPDATE users 
            SET username = ?, first_name = ?, last_name = ?
            WHERE user_id = ?
        """, (username, first_name, last_name, user_id))
    else:
        # Insert new
        first_seen = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cur.execute("""
            INSERT INTO users (user_id, username, first_name, last_name, first_seen)
            VALUES (?, ?, ?, ?, ?)
        """, (user_id, username, first_name, last_name, first_seen))
    
    conn.commit()
    conn.close()


def get_user(user_id: int):
    """Get user information including blocked status."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT user_id, username, first_name, last_name, first_seen, blocked FROM users WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()
    
    if not row:
        return None
    
    # Return a simple object-like dict
    class UserInfo:
        def __init__(self, user_id, username, first_name, last_name, first_seen, blocked):
            self.user_id = user_id
            self.username = username
            self.first_name = first_name
            self.last_name = last_name
            self.first_seen = first_seen
            self.blocked = blocked
    
    return UserInfo(row[0], row[1], row[2], row[3], row[4], row[5] if len(row) > 5 else 0)


def block_user(user_id: int) -> bool:
    """Block a user from using the bot."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("UPDATE users SET blocked = 1 WHERE user_id = ?", (user_id,))
        conn.commit()
        return cur.rowcount > 0
    except:
        return False
    finally:
        conn.close()


def get_user_details(user_id: int) -> dict | None:
    """Get detailed user info for admin panel."""
    conn = get_connection()
    cur = conn.cursor()
    
    cur.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    user_row = cur.fetchone()
    
    if not user_row:
        conn.close()
        return None
        
    # user_row: (user_id, username, first_name, last_name, first_seen, blocked)
    
    # Get stats
    cur.execute("SELECT COUNT(*) FROM collections WHERE user_id = ?", (user_id,))
    collections_count = cur.fetchone()[0]
    
    cur.execute("""
        SELECT COUNT(*) FROM items 
        JOIN collections ON items.collection_id = collections.id 
        WHERE collections.user_id = ?
    """, (user_id,))
    items_count = cur.fetchone()[0]
    
    # Get first collection date
    cur.execute("SELECT id FROM collections WHERE user_id = ? ORDER BY id ASC LIMIT 1", (user_id,))
    first_col = cur.fetchone()
    
    conn.close()
    
    return {
        "user_id": user_row[0],
        "username": user_row[1],
        "first_name": user_row[2],
        "last_name": user_row[3],
        "first_seen": user_row[4],
        "blocked": user_row[5] if len(user_row) > 5 else 0,
        "collections_count": collections_count,
        "items_count": items_count
    }


# --- Collection Sharing Functions ---

def generate_share_code() -> str:
    """
    Generate a unique random share code using [A-Za-z0-9] characters.
    Length: 15-20 characters.
    Ensures uniqueness by checking against existing codes.
    """
    import random
    import string
    
    chars = string.ascii_letters + string.digits  # A-Za-z0-9
    conn = get_connection()
    cur = conn.cursor()
    
    max_attempts = 100
    for _ in range(max_attempts):
        # Generate random length between 15-20
        length = random.randint(15, 20)
        code = ''.join(random.choice(chars) for _ in range(length))
        
        # Check if code already exists
        cur.execute("SELECT COUNT(*) FROM shared_collections WHERE share_code = ?", (code,))
        count = cur.fetchone()[0]
        
        if count == 0:
            conn.close()
            return code
    
    conn.close()
    raise Exception("Failed to generate unique share code after maximum attempts")


def create_share_link(collection_id: int, user_id: int) -> str:
    """
    Create a share code for a collection.
    If a share code already exists for this collection, return it.
    Otherwise, generate a new one.
    """
    conn = get_connection()
    cur = conn.cursor()
    
    # Check if active share code already exists
    cur.execute("""
        SELECT share_code FROM shared_collections 
        WHERE collection_id = ? AND is_active = 1
    """, (collection_id,))
    row = cur.fetchone()
    
    if row:
        conn.close()
        return row[0]
    
    # Generate new share code
    share_code = generate_share_code()
    created_at = datetime.now().isoformat()
    
    cur.execute("""
        INSERT INTO shared_collections (collection_id, share_code, created_at, created_by, is_active)
        VALUES (?, ?, ?, ?, 1)
    """, (collection_id, share_code, created_at, user_id))
    
    conn.commit()
    conn.close()
    return share_code


def get_collection_by_share_code(share_code: str) -> tuple | None:
    """
    Get collection details by share code.
    Returns (collection_id, collection_name, owner_user_id) if valid and active.
    Returns None if code is invalid or inactive.
    """
    conn = get_connection()
    cur = conn.cursor()
    
    cur.execute("""
        SELECT c.id, c.name, c.user_id
        FROM collections c
        JOIN shared_collections sc ON c.id = sc.collection_id
        WHERE sc.share_code = ? AND sc.is_active = 1
    """, (share_code,))
    
    row = cur.fetchone()
    conn.close()
    return row


def revoke_share_code(collection_id: int, user_id: int) -> bool:
    """
    Revoke (deactivate) the share code for a collection.
    Only the owner can revoke.
    """
    conn = get_connection()
    cur = conn.cursor()
    
    # Verify ownership
    cur.execute("SELECT user_id FROM collections WHERE id = ?", (collection_id,))
    row = cur.fetchone()
    
    if not row or row[0] != user_id:
        conn.close()
        return False
    
    # Deactivate share code
    cur.execute("""
        UPDATE shared_collections 
        SET is_active = 0 
        WHERE collection_id = ?
    """, (collection_id,))
    
    conn.commit()
    success = cur.rowcount > 0
    conn.close()
    return success


def regenerate_share_code(collection_id: int, user_id: int) -> str:
    """
    Regenerate a new share code for a collection.
    Deactivates the old code and creates a new one.
    """
    # Revoke old code
    revoke_share_code(collection_id, user_id)
    
    # Create new code
    return create_share_link(collection_id, user_id)


def get_share_code_for_collection(collection_id: int) -> str | None:
    """
    Get the active share code for a collection.
    Returns None if no active share code exists.
    """
    conn = get_connection()
    cur = conn.cursor()
    
    cur.execute("""
        SELECT share_code FROM shared_collections 
        WHERE collection_id = ? AND is_active = 1
    """, (collection_id,))
    
    row = cur.fetchone()
    conn.close()
    return row[0] if row else None


def log_share_access(share_code: str, user_id: int):
    """
    Log when a user accesses a shared collection.
    """
    conn = get_connection()
    cur = conn.cursor()
    
    accessed_at = datetime.now().isoformat()
    cur.execute("""
        INSERT INTO shared_collection_access_log (share_code, user_id, accessed_at)
        VALUES (?, ?, ?)
    """, (share_code, user_id, accessed_at))
    
    conn.commit()
    conn.close()


def get_share_access_logs(collection_id: int) -> list:
    """
    Get access logs for a shared collection.
    Returns list of (user_id, username, first_name, accessed_at) tuples.
    """
    conn = get_connection()
    cur = conn.cursor()
    
    cur.execute("""
        SELECT sal.user_id, u.username, u.first_name, sal.accessed_at
        FROM shared_collection_access_log sal
        JOIN shared_collections sc ON sal.share_code = sc.share_code
        LEFT JOIN users u ON sal.user_id = u.user_id
        WHERE sc.collection_id = ? AND sc.is_active = 1
        ORDER BY sal.accessed_at DESC
    """, (collection_id,))
    
    rows = cur.fetchall()
    conn.close()
    return rows


# --- Admin Shares Management Functions ---

def get_all_active_shares() -> list:
    """
    Get all active share codes with collection and creator info.
    Returns list of tuples: (share_id, share_code, collection_id, collection_name, 
                             created_by, creator_username, created_at, access_count)
    """
    conn = get_connection()
    cur = conn.cursor()
    
    cur.execute("""
        SELECT 
            sc.id,
            sc.share_code,
            sc.collection_id,
            c.name,
            sc.created_by,
            u.username,
            sc.created_at,
            COUNT(DISTINCT sal.user_id) as access_count
        FROM shared_collections sc
        JOIN collections c ON sc.collection_id = c.id
        LEFT JOIN users u ON sc.created_by = u.user_id
        LEFT JOIN shared_collection_access_log sal ON sc.share_code = sal.share_code
        WHERE sc.is_active = 1
        GROUP BY sc.id, sc.share_code, sc.collection_id, c.name, sc.created_by, u.username, sc.created_at
        ORDER BY sc.created_at DESC
    """)
    
    rows = cur.fetchall()
    conn.close()
    return rows


def get_share_stats(share_code: str) -> dict:
    """
    Get statistics for a specific share code.
    Returns dict with: unique_users, total_accesses, last_access
    """
    conn = get_connection()
    cur = conn.cursor()
    
    # Count unique users
    cur.execute("""
        SELECT COUNT(DISTINCT user_id)
        FROM shared_collection_access_log
        WHERE share_code = ?
    """, (share_code,))
    unique_users = cur.fetchone()[0]
    
    # Count total accesses
    cur.execute("""
        SELECT COUNT(*)
        FROM shared_collection_access_log
        WHERE share_code = ?
    """, (share_code,))
    total_accesses = cur.fetchone()[0]
    
    # Get last access time
    cur.execute("""
        SELECT MAX(accessed_at)
        FROM shared_collection_access_log
        WHERE share_code = ?
    """, (share_code,))
    last_access_row = cur.fetchone()
    last_access = last_access_row[0] if last_access_row and last_access_row[0] else None
    
    conn.close()
    
    return {
        "unique_users": unique_users,
        "total_accesses": total_accesses,
        "last_access": last_access
    }


def get_detailed_access_log(share_code: str, offset: int = 0, limit: int = 50) -> list:
    """
    Get detailed access log for a share code with pagination.
    Returns list of tuples: (user_id, username, first_name, accessed_at)
    """
    conn = get_connection()
    cur = conn.cursor()
    
    cur.execute("""
        SELECT sal.user_id, u.username, u.first_name, sal.accessed_at
        FROM shared_collection_access_log sal
        LEFT JOIN users u ON sal.user_id = u.user_id
        WHERE sal.share_code = ?
        ORDER BY sal.accessed_at DESC
        LIMIT ? OFFSET ?
    """, (share_code, limit, offset))
    
    rows = cur.fetchall()
    conn.close()
    return rows


def get_share_by_collection(collection_id: int) -> tuple | None:
    """
    Get active share info for a collection.
    Returns (id, share_code, created_at, created_by) or None
    """
    conn = get_connection()
    cur = conn.cursor()
    
    cur.execute("""
        SELECT id, share_code, created_at, created_by
        FROM shared_collections
        WHERE collection_id = ? AND is_active = 1
    """, (collection_id,))
    
    row = cur.fetchone()
    conn.close()
    return row

