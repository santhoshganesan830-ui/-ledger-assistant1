"""
database.py
SQLite persistence for the Ledger Assistant app - per-user accounts.
Everything lives in one file (ledger.db) next to this module, so there's
no external database server to install.
"""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "ledger.db"

CATEGORY_COLORS = {
    "Food": "#F5A623",
    "Transport": "#4A90D9",
    "Shopping": "#9B59B6",
    "Bills": "#2ECC71",
    "Entertainment": "#E84393",
    "Others": "#95A5A6",
}


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    """Create tables if they don't exist yet. Safe to call on every startup."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            salt TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS sessions (
            token TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            created_at TEXT NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            date TEXT NOT NULL,
            description TEXT NOT NULL,
            category TEXT NOT NULL,
            type TEXT NOT NULL,
            amount REAL NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS budgets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            category TEXT NOT NULL,
            monthly_limit REAL NOT NULL,
            UNIQUE(user_id, category)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS goals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            target_amount REAL NOT NULL,
            saved_amount REAL NOT NULL DEFAULT 0,
            target_date TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS bills (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            amount REAL NOT NULL,
            category TEXT NOT NULL,
            due_day INTEGER NOT NULL,
            last_paid_date TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()
