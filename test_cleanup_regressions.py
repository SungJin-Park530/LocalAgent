import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from config.categories import FILE_CATEGORIES
from config.settings import PROMPTS_DIR
from prompt_loader import assemble_system_prompt
from tools import files


class CleanupRegressionTests(unittest.TestCase):
    def test_selected_prompt_context_keeps_persona_tool_and_workflow(self):
        prompt = assemble_system_prompt("shiki", ["files"])
        prompt_root = Path(PROMPTS_DIR)
        selected_prompt_paths = (
            "persona/00_persona_general.md",
            "persona/01_persona_shiki.md",
            "tools/01_files.md",
            "workflow/01_files_workflow.md",
        )

        for relative_path in selected_prompt_paths:
            with self.subTest(prompt=relative_path):
                prompt_text = (prompt_root / relative_path).read_text(encoding="utf-8").strip()
                self.assertIn(prompt_text, prompt)

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