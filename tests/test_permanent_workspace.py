"""
Unit tests for Permanent Host Workspace Storage & Synchronization APIs
Verifies:
1. Saving files permanently to host disk (workspaces/<student_id>/) and DB
2. Retrieving files from permanent host storage
3. Renaming and deleting files permanently on host device
4. Non-destructive command execution (workspaces are preserved on host disk)
"""

import os
import shutil
import unittest
from backend.app import app
from backend.db import init_db, get_db, save_workspace_file, get_workspace_files, delete_workspace_file, rename_workspace_file


class TestPermanentWorkspace(unittest.TestCase):
    def setUp(self):
        self.app = app
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()
        self.test_student = "TEST_STU_PERM_99"
        self.workspace_dir = os.path.join(self.app.root_path, "workspaces", self.test_student)
        init_db()

    def tearDown(self):
        # Cleanup test directory and db rows
        if os.path.exists(self.workspace_dir):
            try:
                shutil.rmtree(self.workspace_dir)
            except Exception:
                pass
        conn = get_db()
        try:
            conn.execute("DELETE FROM student_workspaces WHERE student_id = ?", (self.test_student,))
            conn.commit()
        finally:
            conn.close()

    def test_save_and_fetch_permanent_workspace_files(self):
        """Verify files are permanently saved to host disk and DB, then fetched."""
        test_payload = {
            "student_id": self.test_student,
            "files": {
                "index.html": "<h1>Permanent Host Test</h1>",
                "Dockerfile": "FROM nginx:alpine\nCOPY . /usr/share/nginx/html"
            }
        }
        res = self.client.post("/api/workspace/save", json=test_payload)
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["ok"])

        # Check physical existence on host disk
        index_path = os.path.join(self.workspace_dir, "index.html")
        df_path = os.path.join(self.workspace_dir, "Dockerfile")
        self.assertTrue(os.path.isfile(index_path), "index.html must exist on physical host disk")
        self.assertTrue(os.path.isfile(df_path), "Dockerfile must exist on physical host disk")

        with open(index_path, "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), "<h1>Permanent Host Test</h1>")

        # Fetch files via API
        fetch_res = self.client.get(f"/api/workspace/files?student_id={self.test_student}")
        self.assertEqual(fetch_res.status_code, 200)
        fetch_data = fetch_res.get_json()
        self.assertTrue(fetch_data["ok"])
        self.assertIn("index.html", fetch_data["files"])
        self.assertIn("Dockerfile", fetch_data["files"])
        self.assertEqual(fetch_data["files"]["index.html"], "<h1>Permanent Host Test</h1>")

    def test_rename_permanent_file(self):
        """Verify file renaming works on physical host disk and DB."""
        # Save initial file
        self.client.post("/api/workspace/save", json={
            "student_id": self.test_student,
            "filename": "old_script.py",
            "content": "print('hello host')"
        })
        old_path = os.path.join(self.workspace_dir, "old_script.py")
        new_path = os.path.join(self.workspace_dir, "new_script.py")
        self.assertTrue(os.path.isfile(old_path))

        # Rename
        res = self.client.post("/api/workspace/rename", json={
            "student_id": self.test_student,
            "old_filename": "old_script.py",
            "new_filename": "new_script.py"
        })
        self.assertEqual(res.status_code, 200)
        self.assertFalse(os.path.exists(old_path))
        self.assertTrue(os.path.isfile(new_path))

        # Check via fetch
        fetch_res = self.client.get(f"/api/workspace/files?student_id={self.test_student}")
        files = fetch_res.get_json()["files"]
        self.assertNotIn("old_script.py", files)
        self.assertIn("new_script.py", files)

    def test_delete_permanent_file(self):
        """Verify file deletion permanently removes from host disk and DB."""
        self.client.post("/api/workspace/save", json={
            "student_id": self.test_student,
            "filename": "temp_note.txt",
            "content": "to be deleted"
        })
        file_path = os.path.join(self.workspace_dir, "temp_note.txt")
        self.assertTrue(os.path.isfile(file_path))

        res = self.client.post("/api/workspace/delete", json={
            "student_id": self.test_student,
            "filename": "temp_note.txt"
        })
        self.assertEqual(res.status_code, 200)
        self.assertFalse(os.path.exists(file_path))

        fetch_res = self.client.get(f"/api/workspace/files?student_id={self.test_student}")
        files = fetch_res.get_json()["files"]
        self.assertNotIn("temp_note.txt", files)

    def test_workspace_not_wiped_on_exec(self):
        """Verify running a command does NOT wipe other permanent files on host disk."""
        # Create a persistent background file on host disk
        os.makedirs(self.workspace_dir, exist_ok=True)
        extra_file = os.path.join(self.workspace_dir, "custom_config.json")
        with open(extra_file, "w", encoding="utf-8") as f:
            f.write('{"permanent": true}')

        # Simulate student session
        with self.client.session_transaction() as sess:
            sess["student_id"] = self.test_student
            sess["role"] = "student"

        # Execute a command without providing custom_config.json in the browser payload
        res = self.client.post("/api/terminal/exec", json={
            "command": "cls",
            "files": {
                "index.html": "<h1>Live Edit</h1>"
            }
        })
        self.assertEqual(res.status_code, 200)

        # Confirm custom_config.json was NOT wiped or unlinked
        self.assertTrue(os.path.isfile(extra_file), "Existing host files must remain permanently on disk")
        with open(extra_file, "r", encoding="utf-8") as f:
            self.assertIn('"permanent": true', f.read())


if __name__ == "__main__":
    unittest.main()
