import sqlite3


def init_db(db_path):
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS download_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            model_id TEXT NOT NULL UNIQUE,
            model_name TEXT,
            account_id TEXT,
            download_time TEXT,
            cost TEXT,
            preview_url TEXT,
            file_path TEXT,
            preview_path TEXT,
            file_type TEXT DEFAULT 'model',
            status TEXT DEFAULT 'pending',
            error_msg TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS checkpoint (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            mode TEXT,
            current_page INTEGER DEFAULT 1,
            total_pages INTEGER,
            last_model_id TEXT,
            updated_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS cookie_store (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            cookies TEXT,
            updated_at TEXT DEFAULT (datetime('now'))
        );
    """)
    conn.commit()
    conn.close()


def insert_download_record(db_path, data):
    conn = sqlite3.connect(db_path)
    conn.execute("""
        INSERT OR IGNORE INTO download_records
            (model_id, model_name, account_id, download_time, cost)
        VALUES (?, ?, ?, ?, ?)
    """, (data["model_id"], data.get("model_name"), data.get("account_id"),
          data.get("download_time"), data.get("cost")))
    conn.commit()
    conn.close()


def get_download_record(db_path, model_id):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM download_records WHERE model_id = ?", (model_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def update_download_status(db_path, model_id, status, file_path=None, preview_path=None, error_msg=None):
    conn = sqlite3.connect(db_path)
    conn.execute("""
        UPDATE download_records
        SET status = ?, file_path = ?, preview_path = ?, error_msg = ?
        WHERE model_id = ?
    """, (status, file_path, preview_path, error_msg, model_id))
    conn.commit()
    conn.close()


def save_checkpoint(db_path, mode, current_page, total_pages, last_model_id=None):
    conn = sqlite3.connect(db_path)
    conn.execute("""
        INSERT OR REPLACE INTO checkpoint (id, mode, current_page, total_pages, last_model_id, updated_at)
        VALUES (1, ?, ?, ?, ?, datetime('now'))
    """, (mode, current_page, total_pages, last_model_id))
    conn.commit()
    conn.close()


def get_checkpoint(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM checkpoint WHERE id = 1").fetchone()
    conn.close()
    return dict(row) if row else None


def save_cookies(db_path, cookies_json):
    conn = sqlite3.connect(db_path)
    conn.execute("""
        INSERT OR REPLACE INTO cookie_store (id, cookies, updated_at)
        VALUES (1, ?, datetime('now'))
    """, (cookies_json,))
    conn.commit()
    conn.close()


def get_cookies(db_path):
    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT cookies FROM cookie_store WHERE id = 1").fetchone()
    conn.close()
    return row[0] if row else None
