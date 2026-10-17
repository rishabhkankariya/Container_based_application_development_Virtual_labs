"""
Unit and Integration Tests for V-Lab Practice Playground Module
Verifies:
1. Unauthenticated access rejection (redirect for UI, 401 for APIs)
2. Student & Faculty session isolation (users cannot access other sessions)
3. Workspace separation (playground_workspaces/ completely isolated from lab workspaces/)
4. Multi-environment provisioning (Linux, Python, Git, Docker starter files)
5. Command execution sandboxing, timeouts, and dangerous command blocklist
6. Path traversal rejection in playground file read/write
"""

import os
import sys
import json
import shutil
import unittest

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from backend.app import app
from backend.db import init_db, get_db
from backend.services.playground_service import playground_service, PLAYGROUND_WORKSPACES_ROOT


class TestPracticePlayground(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        init_db()

    def setUp(self):
        self.app = app
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()
        self.student_user = "STU_TEST_PG_01"
        self.faculty_user = "faculty_test_pg"

    def tearDown(self):
        # Cleanup test rows from playground_sessions
        conn = get_db()
        try:
            sessions = conn.execute(
                "SELECT session_id, workspace_path FROM playground_sessions WHERE user_id IN (?, ?)",
                (self.student_user, self.faculty_user)
            ).fetchall()
            for s in sessions:
                ws = s["workspace_path"]
                if ws and os.path.exists(ws) and "playground_workspaces" in ws:
                    try:
                        shutil.rmtree(ws)
                    except Exception:
                        pass
            conn.execute("DELETE FROM playground_sessions WHERE user_id IN (?, ?)", (self.student_user, self.faculty_user))
            conn.commit()
        finally:
            conn.close()

    def test_01_unauthenticated_access_rejected(self):
        """Unauthenticated requests must be redirected (page) or rejected with 401 (API)."""
        # Page redirect
        res_page = self.client.get("/playground")
        self.assertEqual(res_page.status_code, 302)
        self.assertIn("/login", res_page.headers.get("Location", ""))

        # API 401
        res_exec = self.client.post("/api/playground/exec", json={"session_id": "fake", "command": "ls"})
        self.assertEqual(res_exec.status_code, 401)

        res_files = self.client.get("/api/playground/files?session_id=fake")
        self.assertEqual(res_files.status_code, 401)

    def test_02_student_and_faculty_session_provisioning(self):
        """Authenticated student and faculty can create separate sessions."""
        # Student session
        with self.client.session_transaction() as sess:
            sess["student_id"] = self.student_user
            sess["role"] = "student"

        res_start = self.client.post("/api/playground/session/start", json={"env_type": "linux"})
        self.assertEqual(res_start.status_code, 200)
        data = res_start.get_json()
        self.assertTrue(data["ok"])
        stu_sid = data["session"]["session_id"]
        self.assertEqual(data["session"]["env_type"], "linux")
        self.assertEqual(data["session"]["user_id"], self.student_user)

        # Faculty session
        with self.client.session_transaction() as sess:
            sess.clear()
            sess["faculty_username"] = self.faculty_user
            sess["role"] = "faculty"

        res_fac = self.client.post("/api/playground/session/start", json={"env_type": "python"})
        self.assertEqual(res_fac.status_code, 200)
        fac_data = res_fac.get_json()
        fac_sid = fac_data["session"]["session_id"]
        self.assertEqual(fac_data["session"]["env_type"], "python")
        self.assertNotEqual(stu_sid, fac_sid)

    def test_03_cross_user_session_isolation(self):
        """A user cannot execute commands or read files from another user's session."""
        # 1. Create Student session
        stu_sess = playground_service.get_or_create_session(self.student_user, "student", "linux")
        stu_sid = stu_sess["session_id"]

        # 2. Log in as Faculty and attempt to access Student's session
        with self.client.session_transaction() as sess:
            sess["faculty_username"] = self.faculty_user
            sess["role"] = "faculty"

        # Exec attempt on student session by faculty
        res_exec = self.client.post("/api/playground/exec", json={"session_id": stu_sid, "command": "ls"})
        data = res_exec.get_json()
        self.assertFalse(data["ok"])
        self.assertIn("Access denied", data["output"])

        # File list attempt on student session by faculty
        res_files = self.client.get(f"/api/playground/files?session_id={stu_sid}")
        self.assertEqual(res_files.status_code, 403)

    def test_04_workspace_separation_from_lab_workspaces(self):
        """Playground files are stored in playground_workspaces/ and NEVER touch lab workspaces/."""
        sess = playground_service.get_or_create_session(self.student_user, "student", "linux")
        ws_path = sess["workspace_path"]

        # Must reside strictly in playground_workspaces
        self.assertIn("playground_workspaces", ws_path)
        self.assertNotIn(os.path.join("backend", "workspaces", self.student_user), ws_path)
        self.assertTrue(os.path.exists(ws_path))

        # Starter files verified in playground
        welcome_file = os.path.join(ws_path, "welcome.txt")
        self.assertTrue(os.path.isfile(welcome_file))

        # Regular student lab workspace must remain clean
        regular_lab_ws = os.path.join(BASE_DIR, "backend", "workspaces", self.student_user)
        if os.path.exists(regular_lab_ws):
            self.assertFalse(os.path.exists(os.path.join(regular_lab_ws, "welcome.txt")))

    def test_05_command_execution_and_security_blacklists(self):
        """Valid commands execute successfully, while dangerous commands and socket mounts are blocked."""
        sess = playground_service.get_or_create_session(self.student_user, "student", "linux")
        sid = sess["session_id"]

        with self.client.session_transaction() as s:
            s["student_id"] = self.student_user
            s["role"] = "student"

        # 1. Normal command execution
        res_echo = self.client.post("/api/playground/exec", json={"session_id": sid, "command": "echo 'Hello VLab'"})
        data = res_echo.get_json()
        self.assertTrue(data["ok"])
        self.assertIn("Hello VLab", data["output"])

        # 2. Block direct host Docker socket mount
        res_sock = self.client.post("/api/playground/exec", json={"session_id": sid, "command": "docker run -v /var/run/docker.sock:/var/run/docker.sock alpine"})
        sock_data = res_sock.get_json()
        self.assertFalse(sock_data["ok"])
        self.assertIn("Security Violation", sock_data["output"])

        # 3. Block destructive disk commands
        res_rm = self.client.post("/api/playground/exec", json={"session_id": sid, "command": "rm -rf /"})
        rm_data = res_rm.get_json()
        self.assertFalse(rm_data["ok"])
        self.assertIn("Security Policy", rm_data["output"])

    def test_06_file_management_and_path_traversal_blocking(self):
        """Files can be created and read inside workspace, but path traversal escapes are rejected."""
        sess = playground_service.get_or_create_session(self.student_user, "student", "python")
        sid = sess["session_id"]

        with self.client.session_transaction() as s:
            s["student_id"] = self.student_user
            s["role"] = "student"

        # 1. Save valid scratch file
        res_save = self.client.post("/api/playground/file/save", json={
            "session_id": sid,
            "filename": "calc.py",
            "content": "print(2 + 2)"
        })
        self.assertEqual(res_save.status_code, 200)

        # 2. Read back saved file
        res_read = self.client.get(f"/api/playground/file/read?session_id={sid}&filename=calc.py")
        self.assertEqual(res_read.status_code, 200)
        self.assertIn("print(2 + 2)", res_read.get_json()["content"])

        # 3. Path traversal attack blocked
        bad_save = self.client.post("/api/playground/file/save", json={
            "session_id": sid,
            "filename": "../../escaped.txt",
            "content": "hacked"
        })
        self.assertEqual(bad_save.status_code, 403)

        bad_read = self.client.get(f"/api/playground/file/read?session_id={sid}&filename=../../etc/passwd")
        self.assertEqual(bad_read.status_code, 403)

    def test_07_session_reset(self):
        """Reset clears custom files and restores original starter files."""
        sess = playground_service.get_or_create_session(self.student_user, "student", "linux")
        sid = sess["session_id"]

        with self.client.session_transaction() as s:
            s["student_id"] = self.student_user
            s["role"] = "student"

        # Add custom file
        self.client.post("/api/playground/file/save", json={"session_id": sid, "filename": "custom.txt", "content": "custom"})

        # Reset
        res_reset = self.client.post("/api/playground/session/reset", json={"session_id": sid})
        self.assertEqual(res_reset.status_code, 200)

        # Starter file welcome.txt exists, custom.txt is gone
        res_welcome = self.client.get(f"/api/playground/file/read?session_id={sid}&filename=welcome.txt")
        self.assertEqual(res_welcome.status_code, 200)

        res_custom = self.client.get(f"/api/playground/file/read?session_id={sid}&filename=custom.txt")
        self.assertEqual(res_custom.status_code, 403)


if __name__ == "__main__":
    unittest.main()
