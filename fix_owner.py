from db import transfer_collection_ownership

# הפרטים שאתה רוצה להחזיר
COLLECTION_ID = 15
TARGET_USER_ID = 988596956

if __name__ == "__main__":
    result = transfer_collection_ownership(COLLECTION_ID, TARGET_USER_ID)
    if result:
        print("Ownership updated successfully.")
    else:
        print("Failed to update ownership.")
