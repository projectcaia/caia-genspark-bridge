#!/usr/bin/env python3
"""Rename legacy 'msg' table to 'messages'.

Run this script in deployed environments to migrate existing
SQLite databases that still use the old 'msg' table name.
"""

import os
import sqlite3

DB_PATH = os.getenv("DB_PATH", "mailbridge.sqlite3")


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='msg'")
    if cur.fetchone():
        conn.execute("ALTER TABLE msg RENAME TO messages")
        conn.execute("DROP INDEX IF EXISTS idx_msg_id")
        conn.execute("DROP INDEX IF EXISTS idx_msg_ts")
        conn.execute("DROP INDEX IF EXISTS idx_msg_hash")
        conn.commit()
        print("Renamed table 'msg' to 'messages'.")
    else:
        print("Table 'msg' not found; no changes made.")
    conn.close()


if __name__ == "__main__":
    main()
