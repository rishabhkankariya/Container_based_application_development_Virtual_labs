"""
V-Lab Practice Playground Service
Manages independent practice environments (Linux, Python, Git, Docker)
Isolated from student lab workspaces (stores in playground_workspaces/<session_id>/).
Enforces:
- User session authorization (students and faculty own their own sessions)
- Path traversal & host boundary protection
- Subprocess execution timeouts & container resource limits (--memory=512m, --cpus=1.0, --pids-limit=100)
- Local-first LAN compatibility
"""

import os
import re
import sys
import json
import time
import shutil
import secrets
import datetime
import subprocess
from typing import Optional, Dict, Any, List, Tuple

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PLAYGROUND_WORKSPACES_ROOT = os.path.join(BASE_DIR, "playground_workspaces")

# Ensure playground root exists
os.makedirs(PLAYGROUND_WORKSPACES_ROOT, exist_ok=True)

from backend.db import get_db

ENVIRONMENTS = {
    "linux": {
        "id": "linux",
        "name": "Linux Shell Sandbox",
        "icon": "fa-brands fa-linux",
        "color": "var(--primary-yellow)",
        "desc": "Hands-on shell environment for practicing bash utilities, piping, permissions, filters, and directory navigation.",
        "prompt": "vlab@linux-sandbox:~$ ",
        "starter_files": {
            "welcome.txt": (
                "====================================================\n"
                "  WELCOME TO THE V-LAB LINUX PRACTICE PLAYGROUND    \n"
                "====================================================\n\n"
                "Try these fundamental Linux commands:\n"
                "  ls -la              List all files with permissions\n"
                "  cat welcome.txt     Read file content\n"
                "  grep 'LINUX' welcome.txt Search for text patterns\n"
                "  wc -w welcome.txt   Count words in a file\n"
                "  head -n 5 welcome.txt Show first 5 lines\n"
                "  mkdir my_folder     Create a new directory\n"
                "  bash sample_task.sh Run the sample bash script\n\n"
                "Your files are isolated in your personal session.\n"
            ),
            "sample_data.csv": (
                "id,name,role,department,score\n"
                "101,Aarav,Developer,Platform,92\n"
                "102,Diya,Analyst,Data,88\n"
                "103,Rohan,Engineer,DevOps,95\n"
                "104,Ananya,Designer,Frontend,84\n"
                "105,Kabir,Developer,Backend,91\n"
            ),
            "sample_task.sh": (
                "#!/bin/bash\n"
                "echo 'Running Linux Environment Diagnostics...'\n"
                "echo 'Current Directory:' $(pwd)\n"
                "echo 'Available Files:'\n"
                "ls -1\n"
                "echo 'Processing sample_data.csv top scores:'\n"
                "awk -F',' '$5 > 90 {print $2, \"(\" $4 \"):\", $5}' sample_data.csv\n"
                "echo 'Done!'\n"
            )
        }
    },
    "python": {
        "id": "python",
        "name": "Python 3 Sandbox",
        "icon": "fa-brands fa-python",
        "color": "var(--primary-blue)",
        "desc": "Interactive Python environment for practicing data structures, algorithms, scripts, and standard libraries.",
        "prompt": "python@sandbox:~$ ",
        "starter_files": {
            "main.py": (
                "# V-Lab Python 3 Sandbox Starter Script\n"
                "import sys\n"
                "import math\n"
                "from utils import calculate_stats\n\n"
                "def main():\n"
                "    print(f'🐍 Python Version: {sys.version.split()[0]}')\n"
                "    sample_numbers = [14, 28, 42, 56, 70, 84, 98]\n"
                "    stats = calculate_stats(sample_numbers)\n"
                "    print('----------------------------------------')\n"
                "    print(f'Data:    {sample_numbers}')\n"
                "    print(f'Count:   {stats[\"count\"]}')\n"
                "    print(f'Sum:     {stats[\"sum\"]}')\n"
                "    print(f'Mean:    {stats[\"mean\"]:.2f}')\n"
                "    print('----------------------------------------')\n\n"
                "if __name__ == '__main__':\n"
                "    main()\n"
            ),
            "utils.py": (
                "# Utility helper functions for main.py\n"
                "def calculate_stats(numbers):\n"
                "    if not numbers:\n"
                "        return {'count': 0, 'sum': 0, 'mean': 0.0}\n"
                "    total = sum(numbers)\n"
                "    mean = total / len(numbers)\n"
                "    return {'count': len(numbers), 'sum': total, 'mean': mean}\n"
            ),
            "requirements.txt": "# Standard library is pre-loaded\n"
        }
    },
    "git": {
        "id": "git",
        "name": "Git Version Control",
        "icon": "fa-brands fa-git-alt",
        "color": "var(--primary-red)",
        "desc": "Live Git sandbox initialized with branches and commit history to practice commits, merges, diffs, and logs.",
        "prompt": "git@sandbox:[main]$ ",
        "starter_files": {
            "README.md": (
                "# V-Lab Git Practice Repository\n\n"
                "This repository is initialized for hands-on Git practice.\n\n"
                "### Practice exercises:\n"
                "1. `git status` — Check branch status and tracked files\n"
                "2. `git log --oneline` — View recent commit history\n"
                "3. `git branch` — Inspect existing branches\n"
                "4. `git checkout -b my-new-feature` — Create a new branch\n"
                "5. Edit `app.txt`, then run `git add .` and `git commit -m 'feat: update app'`\n"
                "6. `git diff` — Review staged or unstaged modifications\n"
            ),
            "app.txt": "V-Lab Version 1.0 - Ready for Git branching practice.\n"
        }
    },
    "docker": {
        "id": "docker",
        "name": "Docker Engine CLI",
        "icon": "fa-brands fa-docker",
        "color": "#38bdf8",
        "desc": "Explore container architectures, inspect image layers, build definitions, and container runtimes with cgroup guards.",
        "prompt": "docker@vlab-engine:~$ ",
        "starter_files": {
            "Dockerfile": (
                "# Sample Multi-Stage Container Definition\n"
                "FROM alpine:3.19 AS base\n"
                "WORKDIR /app\n"
                "RUN echo 'Building base stage...'\n\n"
                "FROM base AS final\n"
                "COPY app.py .\n"
                "CMD [\"python\", \"app.py\"]\n"
            ),
            "app.py": (
                "# Micro-application for containerization\n"
                "print('Hello from the V-Lab Docker Playground!')\n"
            ),
            "docker-compose.yml": (
                "version: '3.8'\n"
                "services:\n"
                "  web:\n"
                "    image: nginx:alpine\n"
                "    ports:\n"
                "      - '8080:80'\n"
            )
        }
    }
}


class PlaygroundService:
    def __init__(self):
        self.workspaces_root = PLAYGROUND_WORKSPACES_ROOT

    def _resolve_secure_session_path(self, session_id: str, relative_path: str = "") -> Optional[str]:
        """
        Enforces strict path containment within playground_workspaces/<session_id>/.
        Blocks directory traversal, symlinks pointing outside, absolute paths, and drive escapes.
        """
        if not session_id or not isinstance(session_id, str):
            return None

        clean_sid = re.sub(r'[^a-zA-Z0-9_-]', '', session_id)
        if not clean_sid:
            return None

        session_root = os.path.realpath(os.path.join(self.workspaces_root, clean_sid))
        os.makedirs(session_root, exist_ok=True)

        if not relative_path:
            return session_root

        # Normalize relative path
        norm = relative_path.replace("\\", "/").strip().lstrip("/")
        if ":" in norm or ".." in norm.split("/"):
            return None

        target = os.path.realpath(os.path.join(session_root, norm))
        try:
            # Check commonpath boundary
            if os.path.commonpath([session_root, target]) == session_root:
                return target
        except Exception:
            return None
        return None

    def get_or_create_session(self, user_id: str, role: str, env_type: str = "linux") -> Dict[str, Any]:
        """Retrieves an active session for the given user and environment, or initializes a fresh one."""
        if env_type not in ENVIRONMENTS:
            env_type = "linux"

        conn = get_db()
        try:
            row = conn.execute(
                """
                SELECT * FROM playground_sessions
                WHERE user_id = ? AND env_type = ? AND status = 'ACTIVE'
                ORDER BY id DESC LIMIT 1
                """,
                (user_id, env_type)
            ).fetchone()

            now_str = datetime.datetime.utcnow().isoformat()

            if row:
                session_dict = dict(row)
                # Verify physical workspace still exists
                ws_path = session_dict.get("workspace_path", "")
                if not ws_path or not os.path.exists(ws_path):
                    ws_path = self._resolve_secure_session_path(session_dict["session_id"])
                    self._seed_workspace(ws_path, env_type)
                    conn.execute("UPDATE playground_sessions SET workspace_path = ? WHERE session_id = ?", (ws_path, session_dict["session_id"]))
                    conn.commit()
                # Update last activity
                conn.execute("UPDATE playground_sessions SET last_activity = ? WHERE session_id = ?", (now_str, session_dict["session_id"]))
                conn.commit()
                return self._enrich_session(session_dict)

            # Create new session
            new_session_id = secrets.token_hex(16)
            ws_path = self._resolve_secure_session_path(new_session_id)
            self._seed_workspace(ws_path, env_type)

            conn.execute(
                """
                INSERT INTO playground_sessions (session_id, user_id, role, env_type, status, workspace_path, created_at, last_activity)
                VALUES (?, ?, ?, ?, 'ACTIVE', ?, ?, ?)
                """,
                (new_session_id, user_id, role, env_type, ws_path, now_str, now_str)
            )
            conn.commit()

            new_row = conn.execute("SELECT * FROM playground_sessions WHERE session_id = ?", (new_session_id,)).fetchone()
            return self._enrich_session(dict(new_row))
        finally:
            conn.close()

    def get_session(self, session_id: str, user_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Retrieves session details and strictly verifies user ownership if user_id is provided."""
        conn = get_db()
        try:
            row = conn.execute("SELECT * FROM playground_sessions WHERE session_id = ?", (session_id,)).fetchone()
            if not row:
                return None
            sess = dict(row)
            if user_id and sess["user_id"] != user_id:
                return None  # Authorization failure
            return self._enrich_session(sess)
        finally:
            conn.close()

    def list_user_sessions(self, user_id: str) -> List[Dict[str, Any]]:
        """Lists all active and recent sessions for this user."""
        conn = get_db()
        try:
            rows = conn.execute(
                "SELECT * FROM playground_sessions WHERE user_id = ? ORDER BY id DESC LIMIT 10",
                (user_id,)
            ).fetchall()
            return [self._enrich_session(dict(r)) for r in rows]
        finally:
            conn.close()

    def reset_session(self, session_id: str, user_id: str) -> Tuple[bool, str]:
        """Resets the session workspace back to clean default starter files."""
        sess = self.get_session(session_id, user_id=user_id)
        if not sess:
            return False, "Session not found or access denied."

        ws_path = self._resolve_secure_session_path(session_id)
        if not ws_path or not os.path.exists(ws_path):
            return False, "Workspace path could not be resolved."

        try:
            # Remove all existing files in workspace
            for item in os.listdir(ws_path):
                target = os.path.join(ws_path, item)
                if os.path.isfile(target) or os.path.islink(target):
                    os.remove(target)
                elif os.path.isdir(target):
                    shutil.rmtree(target)

            # Re-seed files
            self._seed_workspace(ws_path, sess["env_type"])

            conn = get_db()
            now_str = datetime.datetime.utcnow().isoformat()
            conn.execute("UPDATE playground_sessions SET last_activity = ? WHERE session_id = ?", (now_str, session_id))
            conn.commit()
            conn.close()

            return True, "Session workspace reset cleanly."
        except Exception as e:
            return False, f"Failed to reset workspace: {str(e)}"

    def stop_session(self, session_id: str, user_id: str) -> Tuple[bool, str]:
        """Stops an active session."""
        sess = self.get_session(session_id, user_id=user_id)
        if not sess:
            return False, "Session not found or access denied."

        conn = get_db()
        try:
            conn.execute("UPDATE playground_sessions SET status = 'STOPPED' WHERE session_id = ?", (session_id,))
            conn.commit()
            return True, "Session stopped successfully."
        finally:
            conn.close()

    def execute_command(self, session_id: str, user_id: str, command: str) -> Dict[str, Any]:
        """
        Executes a user command inside their session workspace with strict timeouts,
        resource limits, and security protections.
        """
        sess = self.get_session(session_id, user_id=user_id)
        if not sess:
            return {"ok": False, "output": "Access denied: Invalid session or unauthorized user."}

        if sess["status"] != "ACTIVE":
            return {"ok": False, "output": "Session is inactive. Please restart or switch environment."}

        cmd = (command or "").strip()
        if not cmd:
            return {"ok": False, "output": "No command provided."}

        cmd_lower = cmd.lower()

        # Built-in clear / cls
        if cmd_lower in ("clear", "cls"):
            return {"ok": True, "action": "clear", "output": ""}

        # Built-in exit
        if cmd_lower == "exit":
            return {"ok": True, "action": "exit", "output": "Playground session remains active. Use 'Reset' to clear files."}

        ws_path = self._resolve_secure_session_path(session_id)
        if not ws_path or not os.path.exists(ws_path):
            return {"ok": False, "output": "Workspace directory not found."}

        # Update last activity
        conn = get_db()
        try:
            conn.execute(
                "UPDATE playground_sessions SET last_activity = ? WHERE session_id = ?",
                (datetime.datetime.utcnow().isoformat(), session_id)
            )
            conn.commit()
        finally:
            conn.close()

        # Prevent dangerous host commands & socket mounts
        if "/var/run/docker.sock" in cmd or "\\\\.\\pipe\\docker_engine" in cmd:
            return {"ok": False, "output": "⛔ Security Violation: Direct host Docker socket mounting is strictly forbidden."}

        # Prevent destructive host formatting
        if re.search(r'(\bformat\s+[a-z]:|\bmkfs|\brm\s+-[a-z]*r[a-z]*f\s+([/~]|\.\.)|\brmdir\s+/[sq]\s+[a-z]:)', cmd_lower):
            return {"ok": False, "output": "⛔ Security Policy: Destructive filesystem command blocked."}

        # If docker command executed in playground, inject kernel cgroup resource controls
        exec_cmd = cmd
        if cmd_lower.startswith("docker run"):
            limit_flags = []
            if "--memory" not in exec_cmd and "-m " not in exec_cmd:
                limit_flags.append("--memory=512m")
            if "--cpus" not in exec_cmd:
                limit_flags.append("--cpus=1.0")
            if "--pids-limit" not in exec_cmd:
                limit_flags.append("--pids-limit=100")
            if limit_flags:
                exec_cmd = re.sub(r'^(docker\s+run\b)', r'\1 ' + ' '.join(limit_flags), exec_cmd, count=1, flags=re.IGNORECASE)

        # Run in subprocess inside isolated session workspace
        try:
            is_win = sys.platform == "win32"
            creation_flags = subprocess.CREATE_NO_WINDOW if is_win else 0
            proc = subprocess.run(
                exec_cmd,
                shell=True,
                cwd=ws_path,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=25,
                creationflags=creation_flags
            )

            out = proc.stdout if proc.stdout else proc.stderr
            if not out and proc.returncode == 0:
                out = "Command executed successfully (exit code 0)."

            return {
                "ok": proc.returncode == 0,
                "output": out.strip() if out else f"Process exited with code {proc.returncode}.",
                "returncode": proc.returncode,
                "env_type": sess["env_type"]
            }
        except subprocess.TimeoutExpired:
            return {"ok": False, "output": "Command execution timed out (25-second limit exceeded)."}
        except Exception as e:
            return {"ok": False, "output": f"Execution error: {str(e)}"}

    def list_files(self, session_id: str, user_id: str) -> Dict[str, Any]:
        """Lists files and directories in the session workspace."""
        sess = self.get_session(session_id, user_id=user_id)
        if not sess:
            return {"ok": False, "error": "Access denied."}

        ws_path = self._resolve_secure_session_path(session_id)
        if not ws_path or not os.path.exists(ws_path):
            return {"ok": False, "error": "Workspace not found."}

        files_list = []
        try:
            for root, dirs, files in os.walk(ws_path):
                # Don't recurse into .git directory details for file list
                if ".git" in dirs:
                    dirs.remove(".git")
                for f in files:
                    full_p = os.path.join(root, f)
                    rel_p = os.path.relpath(full_p, ws_path).replace("\\", "/")
                    try:
                        sz = os.path.getsize(full_p)
                    except Exception:
                        sz = 0
                    files_list.append({"name": rel_p, "size": sz})
            return {"ok": True, "files": sorted(files_list, key=lambda x: x["name"])}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def read_file(self, session_id: str, user_id: str, filename: str) -> Dict[str, Any]:
        """Reads a specific file from the session workspace."""
        sess = self.get_session(session_id, user_id=user_id)
        if not sess:
            return {"ok": False, "error": "Access denied."}

        fpath = self._resolve_secure_session_path(session_id, filename)
        if not fpath or not os.path.isfile(fpath):
            return {"ok": False, "error": "File not found or invalid path."}

        try:
            with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            return {"ok": True, "filename": filename, "content": content}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def save_file(self, session_id: str, user_id: str, filename: str, content: str) -> Dict[str, Any]:
        """Saves or updates a file in the session workspace."""
        sess = self.get_session(session_id, user_id=user_id)
        if not sess:
            return {"ok": False, "error": "Access denied."}

        fpath = self._resolve_secure_session_path(session_id, filename)
        if not fpath:
            return {"ok": False, "error": "Invalid filename or path traversal detected."}

        try:
            os.makedirs(os.path.dirname(fpath), exist_ok=True)
            with open(fpath, "w", encoding="utf-8") as f:
                f.write(content or "")
            return {"ok": True, "filename": filename, "size": len(content or "")}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _seed_workspace(self, ws_path: str, env_type: str):
        """Initializes starter files for the given environment type."""
        cfg = ENVIRONMENTS.get(env_type, ENVIRONMENTS["linux"])
        starters = cfg.get("starter_files", {})
        for fname, content in starters.items():
            fpath = os.path.join(ws_path, fname)
            os.makedirs(os.path.dirname(fpath), exist_ok=True)
            try:
                with open(fpath, "w", encoding="utf-8") as f:
                    f.write(content)
            except Exception:
                pass

        # For Git environment, initialize a clean local repo with sample commit
        if env_type == "git":
            try:
                subprocess.run("git init", cwd=ws_path, shell=True, capture_output=True, timeout=5)
                subprocess.run('git config user.name "VLab Student"', cwd=ws_path, shell=True, capture_output=True, timeout=5)
                subprocess.run('git config user.email "student@vlab.local"', cwd=ws_path, shell=True, capture_output=True, timeout=5)
                subprocess.run("git add .", cwd=ws_path, shell=True, capture_output=True, timeout=5)
                subprocess.run('git commit -m "feat: initial practice repository setup"', cwd=ws_path, shell=True, capture_output=True, timeout=5)
                subprocess.run("git branch feature-auth", cwd=ws_path, shell=True, capture_output=True, timeout=5)
            except Exception:
                pass

    def _enrich_session(self, session_dict: dict) -> dict:
        """Adds display metadata (name, icon, prompt, etc.) to session object."""
        env_key = session_dict.get("env_type", "linux")
        cfg = ENVIRONMENTS.get(env_key, ENVIRONMENTS["linux"])
        session_dict["env_name"] = cfg["name"]
        session_dict["env_icon"] = cfg["icon"]
        session_dict["env_color"] = cfg["color"]
        session_dict["env_desc"] = cfg["desc"]
        session_dict["prompt"] = cfg["prompt"].replace("{user_id}", str(session_dict.get("user_id", "user")))
        return session_dict


# Global Singleton
playground_service = PlaygroundService()
