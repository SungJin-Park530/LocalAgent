import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from typing import TypedDict
from unittest.mock import patch

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from config.categories import FILE_CATEGORIES
from config.settings import PROMPTS_DIR
from engine import delete_room_checkpoint
from prompt_loader import assemble_system_prompt
from room_manager import get_default_rooms
from tools import browser
from tools import files


class CleanupRegressionTests(unittest.TestCase):
    def test_browser_history_snapshot_reads_during_rollback_journal_write(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            history_path = Path(temp_dir) / "History"
            writer = sqlite3.connect(history_path)
            writer.execute("PRAGMA journal_mode=DELETE")
            writer.execute(
                "CREATE TABLE urls (title TEXT, url TEXT, visit_count INTEGER, last_visit_time INTEGER)"
            )
            writer.execute(
                "INSERT INTO urls VALUES (?, ?, ?, ?)",
                (
                    "Committed visit",
                    "https://example.test/committed",
                    1,
                    browser._to_webkit_timestamp(datetime.now()),
                ),
            )
            writer.commit()
            try:
                writer.execute("BEGIN EXCLUSIVE")
                writer.execute("UPDATE urls SET title = ?", ("Uncommitted visit",))

                journal_path = Path(f"{history_path}-journal")
                self.assertTrue(journal_path.exists())
                self.assertGreater(journal_path.stat().st_size, 0)
                with patch.object(browser, "_get_chrome_history_path", return_value=str(history_path)):
                    result = browser._fetch_browser_history(days=1)
            finally:
                if writer.in_transaction:
                    writer.rollback()
                writer.close()

            self.assertIn("Committed visit", result)

    def test_delete_room_checkpoint_removes_all_thread_state(self):
        class TestState(TypedDict):
            value: str

        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "checkpoints.sqlite"
            conn = sqlite3.connect(db_path, check_same_thread=False)
            saver = SqliteSaver(conn)
            builder = StateGraph(TestState)
            builder.add_node("persist", lambda state: {"value": state["value"]})
            builder.add_edge(START, "persist")
            builder.add_edge("persist", END)
            graph = builder.compile(checkpointer=saver)
            config = {"configurable": {"thread_id": "room-to-clear"}}
            graph.invoke({"value": "old conversation"}, config=config)
            self.assertIsNotNone(saver.get_tuple(config))
            conn.close()

            delete_room_checkpoint("room-to-clear", str(db_path))

            conn = sqlite3.connect(db_path, check_same_thread=False)
            try:
                self.assertIsNone(SqliteSaver(conn).get_tuple(config))
            finally:
                conn.close()

    def test_browser_history_query_includes_uncheckpointed_wal_records(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            history_path = Path(temp_dir) / "History"
            source = sqlite3.connect(history_path)
            source.execute("PRAGMA journal_mode=WAL")
            source.execute("PRAGMA wal_autocheckpoint=0")
            source.execute(
                "CREATE TABLE urls (title TEXT, url TEXT, visit_count INTEGER, last_visit_time INTEGER)"
            )
            source.execute(
                "INSERT INTO urls VALUES (?, ?, ?, ?)",
                (
                    "WAL-only visit",
                    "https://example.test/recent",
                    1,
                    browser._to_webkit_timestamp(datetime.now()),
                ),
            )
            source.commit()

            try:
                self.assertTrue(Path(f"{history_path}-wal").exists())
                with patch.object(browser, "_get_chrome_history_path", return_value=str(history_path)):
                    result = browser._fetch_browser_history(days=1)
            finally:
                source.close()

            self.assertIn("WAL-only visit", result)

    def test_browser_history_skips_localhost_before_applying_limit(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            history_path = Path(temp_dir) / "History"
            source = sqlite3.connect(history_path)
            source.execute(
                "CREATE TABLE urls (title TEXT, url TEXT, visit_count INTEGER, last_visit_time INTEGER)"
            )
            now = datetime.now()
            source.executemany(
                "INSERT INTO urls VALUES (?, ?, ?, ?)",
                [
                    (
                        "Local app",
                        "http://localhost:8501/",
                        1,
                        browser._to_webkit_timestamp(now),
                    ),
                    (
                        "External visit",
                        "https://example.test/",
                        1,
                        browser._to_webkit_timestamp(now - timedelta(seconds=1)),
                    ),
                ],
            )
            source.commit()
            source.close()

            with patch.object(browser, "_get_chrome_history_path", return_value=str(history_path)):
                result = browser._fetch_browser_history(days=1, limit=1)

            self.assertIn("External visit", result)
            self.assertNotIn("Local app", result)

    def test_default_rooms_build_tool_schemas(self):
        rooms = get_default_rooms()

        self.assertIn("room_chat", rooms)
        self.assertIn("room_agent", rooms)
        self.assertTrue(rooms["room_chat"]["tools"])
        self.assertTrue(rooms["room_agent"]["tools"])

    def test_selected_prompt_context_keeps_persona_tool_and_workflow(self):
        prompt = assemble_system_prompt("shiki", ["files", "browser"])
        prompt_root = Path(PROMPTS_DIR)
        selected_prompt_paths = (
            "persona/00_persona_general.md",
            "persona/01_persona_shiki.md",
            "tools/01_files.md",
            "tools/02_browser.md",
            "workflow/01_files_workflow.md",
        )

        for relative_path in selected_prompt_paths:
            with self.subTest(prompt=relative_path):
                prompt_text = (prompt_root / relative_path).read_text(encoding="utf-8").strip()
                self.assertIn(prompt_text, prompt)

        browser_schema = next(
            schema
            for schema in browser.BROWSER_SCHEMAS
            if schema["function"]["name"] == "search_browser_history"
        )
        self.assertNotIn("get_current_time", json.dumps(browser_schema, ensure_ascii=False))

    def test_search_schema_categories_match_file_categories(self):
        search_schema = next(
            schema
            for schema in files.FILES_SCHEMAS
            if schema["function"]["name"] == "search_files"
        )

        self.assertEqual(
            search_schema["function"]["parameters"]["properties"]["category"]["enum"],
            list(FILE_CATEGORIES),
        )

    def test_export_uses_default_filename_without_default_path_setting(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / "cache.json"
            export_dir = Path(temp_dir) / "exports"
            export_dir.mkdir()
            cache_path.write_text(
                json.dumps([{"name": "report.txt", "size_mb": 1, "path": "C:/report.txt"}]),
                encoding="utf-8",
            )

            with (
                patch.object(files, "CACHE_FILE", str(cache_path)),
                patch.object(files, "EXPORT_DIR", str(export_dir)),
            ):
                result = files._export_search_results_to_file()

            self.assertTrue(result["success"], result)
            self.assertEqual(result["filename"], "search_result.txt")
            self.assertTrue(Path(result["saved_path"]).is_file())


if __name__ == "__main__":
    unittest.main()