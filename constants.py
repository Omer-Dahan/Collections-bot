from typing import Dict

# Message constants
MSG_NO_COLLECTIONS = "אין עדיין אוספים. צור אחד עם /newcollection."

# Global state
active_collections: Dict[int, int] = {}  # user_id -> collection_id
active_shared_collections: Dict[int, str] = {}  # user_id -> share_code
