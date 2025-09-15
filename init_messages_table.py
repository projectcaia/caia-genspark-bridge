"""Initialize or migrate the messages table schema.

Run this script to create the `messages` table and ensure required
columns (`deleted`, `replied`, `priority`, `auto_reply`, etc.) exist.
"""
from app import init_db

if __name__ == "__main__":
    init_db()
    print("messages table initialized")
