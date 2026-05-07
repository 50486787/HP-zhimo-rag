import os
import pytest
import sqlite3
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import init_db, insert_download_record, get_download_record, save_checkpoint, get_checkpoint


@pytest.fixture
def test_db():
    db_path = os.path.join(os.path.dirname(__file__), "test.db")
    init_db(db_path)
    yield db_path
    # Close any lingering connections before cleanup
    try:
        os.remove(db_path)
    except PermissionError:
        pass
    for suffix in ("-wal", "-shm"):
        wal_path = db_path + suffix
        if os.path.exists(wal_path):
            try:
                os.remove(wal_path)
            except PermissionError:
                pass


class TestInitDB:
    def test_creates_all_tables(self, test_db):
        conn = sqlite3.connect(test_db)
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        table_names = [t[0] for t in tables]
        conn.close()
        assert "download_records" in table_names
        assert "checkpoint" in table_names
        assert "cookie_store" in table_names


class TestDownloadRecords:
    def test_insert_and_get(self, test_db):
        insert_download_record(test_db, {
            "model_id": "1195169820",
            "model_name": "现代简约客厅",
            "account_id": "21810008241125",
            "download_time": "2026-04-28 10:58:39",
            "cost": "13知币",
        })
        record = get_download_record(test_db, "1195169820")
        assert record["model_name"] == "现代简约客厅"
        assert record["status"] == "pending"

    def test_insert_duplicate_ignored(self, test_db):
        data = {"model_id": "1195169820", "model_name": "test"}
        insert_download_record(test_db, data)
        insert_download_record(test_db, data)
        record = get_download_record(test_db, "1195169820")
        assert record is not None

    def test_update_status(self, test_db):
        from db import update_download_status
        insert_download_record(test_db, {
            "model_id": "1195169820",
            "model_name": "test",
            "account_id": "1",
            "download_time": "2026-04-28",
            "cost": "1知币",
        })
        update_download_status(test_db, "1195169820", "done",
            file_path="/tmp/test.zip", preview_path="/tmp/test.jpg")
        record = get_download_record(test_db, "1195169820")
        assert record["status"] == "done"
        assert record["file_path"] == "/tmp/test.zip"


class TestCheckpoint:
    def test_save_and_get(self, test_db):
        save_checkpoint(test_db, mode="full", current_page=42, total_pages=1313)
        cp = get_checkpoint(test_db)
        assert cp["mode"] == "full"
        assert cp["current_page"] == 42
        assert cp["total_pages"] == 1313
