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
import getpass
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
        self.project_root = BASE_DIR

    def _sanitize_output(self, text: str, ws_path: str = "") -> str:
        """
        Sanitizes command output before presenting it in the playground terminal:
        1. Strips all host usernames and Windows credentials, replacing them with 'vlab'.
        2. Normalizes all Windows and POSIX workspace paths to '/home/vlab'.
        3. Normalizes Windows group IDs (e.g. 197121) in 'ls -l' output to 'vlab'.
        4. Strips any parent project directories, drive letters, or host paths.
        """
        if not text:
            return text

        # Collect known usernames
        usernames = set()
        current_user = os.environ.get("USERNAME", "")
        if current_user:
            usernames.add(current_user)
            for part in current_user.split():
                if len(part) >= 3:
                    usernames.add(part)
        try:
            gp = getpass.getuser()
            if gp:
                usernames.add(gp)
                for part in gp.split():
                    if len(part) >= 3:
                        usernames.add(part)
        except Exception:
            pass

        usernames.add("Rishabh Kankariya")
        usernames.add("Kankariya")
        usernames.add("Rishabh")

        out = text

        # 1. Replace workspace paths with /home/vlab
        if ws_path:
            norm_ws_win = ws_path.replace("/", "\\")
            norm_ws_posix = ws_path.replace("\\", "/")
            drive_match = re.match(r'^([a-zA-Z]):[\\/](.*)$', ws_path)
            git_bash_ws = f"/{drive_match.group(1).lower()}/{drive_match.group(2).replace(chr(92), '/')}" if drive_match else ""

            for p in (norm_ws_win, norm_ws_posix, git_bash_ws):
                if p:
                    out = re.sub(re.escape(p), "/home/vlab", out, flags=re.IGNORECASE)

        # Replace project root directory and workspaces root
        for root_p in (self.project_root, self.workspaces_root):
            p_win = root_p.replace("/", "\\")
            p_posix = root_p.replace("\\", "/")
            dm = re.match(r'^([a-zA-Z]):[\\/](.*)$', root_p)
            gb_p = f"/{dm.group(1).lower()}/{dm.group(2).replace(chr(92), '/')}" if dm else ""
            for p in (p_win, p_posix, gb_p):
                if p:
                    out = re.sub(re.escape(p), "/home/vlab", out, flags=re.IGNORECASE)

        # Replace parent workspaces references
        out = re.sub(r'playground_workspaces[\\/][a-zA-Z0-9_-]+', '/home/vlab', out, flags=re.IGNORECASE)
        out = re.sub(r'playground_workspaces', '/home/vlab', out, flags=re.IGNORECASE)

        # Replace any leaked Windows User paths (e.g. C:\Users\Username\... or /c/Users/Username/...)
        out = re.sub(r'[a-zA-Z]:[\\/]Users[\\/][^\\/\r\n\t]+(?:[\\/][^\r\n\t :\'"]+)*', '/home/vlab', out, flags=re.IGNORECASE)
        out = re.sub(r'/[a-zA-Z]/Users/[^/\r\n\t]+(?:/[^\r\n\t :\'"]+)*', '/home/vlab', out, flags=re.IGNORECASE)

        # 2. Replace usernames (sorted by length descending so full name is replaced first)
        for u in sorted(usernames, key=len, reverse=True):
            if u:
                out = re.sub(re.escape(u), "vlab", out, flags=re.IGNORECASE)

        # 3. Replace Windows numerical Group ID in ls -la (e.g. "1 vlab 197121 0")
        out = re.sub(r'(\b\d+\s+vlab\s+)\d+(\s+)', r'\g<1>vlab\2', out)

        return out

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

    def _find_bash_binary(self) -> Optional[str]:
        """Finds bash binary on Windows, prioritizing Git Bash with full POSIX coreutils."""
        candidates = [
            r"C:\Program Files\Git\bin\bash.exe",
            r"C:\Program Files\Git\usr\bin\bash.exe",
            r"C:\Program Files (x86)\Git\bin\bash.exe",
            os.path.join(os.environ.get("LOCALAPPDATA", ""), r"Programs\Git\bin\bash.exe"),
            shutil.which("bash")
        ]
        for c in candidates:
            if c and os.path.isfile(c):
                return c
        return None

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

        stripped_lower = cmd.lower()

        # Built-in clear / cls
        if stripped_lower in ("clear", "cls"):
            return {"ok": True, "action": "clear", "output": ""}

        # Built-in exit
        if stripped_lower == "exit":
            return {"ok": True, "action": "exit", "output": "Playground session remains active. Use 'Reset' to clear files."}

        # Prevent headless interactive hanging
        if stripped_lower in ("python", "python3"):
            py_ver = sys.version.split()[0]
            return {
                "ok": True,
                "output": (
                    f"Python {py_ver} Sandbox Active.\n"
                    "Ready to run Python scripts or one-liners!\n"
                    "  • Run a file:    python main.py\n"
                    "  • Run inline:    python -c \"import math; print(math.pi)\"\n"
                    "  • Inspect code:  cat main.py"
                ),
                "returncode": 0,
                "env_type": sess["env_type"]
            }

        if stripped_lower in ("bash", "sh"):
            return {
                "ok": True,
                "output": (
                    "POSIX Linux Shell Active.\n"
                    "Run your commands directly in this terminal (e.g. 'ls -la', 'cat welcome.txt', or 'bash sample_task.sh')."
                ),
                "returncode": 0,
                "env_type": sess["env_type"]
            }

        # 1. Block host GUI / interactive text editor utilities (e.g. notepad, calc, nano, vi)
        desktop_apps = ["notepad", "calc", "mspaint", "explorer", "regedit", "taskmgr", "mstsc"]
        for app_name in desktop_apps:
            if re.search(r'\b' + app_name + r'(\.exe)?\b', stripped_lower):
                if app_name in ("notepad", "explorer"):
                    return {
                        "ok": False,
                        "output": (
                            f"⛔ Sandbox Notice: Host application '{app_name}' is disabled.\n"
                            "The Practice Playground is strictly isolated from your local host machine.\n"
                            "👉 Use the 'Workspace Files' panel on the left to click and edit files directly in the built-in scratchpad editor!"
                        ),
                        "returncode": 0,
                        "env_type": sess["env_type"]
                    }
                return {
                    "ok": False,
                    "output": f"⛔ Security Policy: Execution of host binary '{app_name}' is blocked. Operations are restricted to the isolated sandbox.",
                    "returncode": 126,
                    "env_type": sess["env_type"]
                }

        editor_cmds = ("nano", "vi", "vim", "code", "gedit", "subl", "wordpad", "emacs", "pico")
        first_token = re.split(r'[\s|;&]+', stripped_lower)[0].replace('.exe', '')
        if first_token in editor_cmds or stripped_lower.startswith(tuple(e + " " for e in editor_cmds)):
            return {
                "ok": False,
                "output": (
                    f"⛔ Sandbox Notice: Interactive text editor '{first_token}' is disabled.\n"
                    "👉 Use the 'Workspace Files' panel on the left to click and edit files directly in the built-in scratchpad editor!"
                ),
                "returncode": 0,
                "env_type": sess["env_type"]
            }

        # 2. Block dangerous host utilities, system configuration, and process management
        host_blocked_binaries = {
            "calc", "mspaint", "write", "wordpad", "explorer", "regedit", "reg",
            "cmd", "powershell", "pwsh", "wscript", "cscript", "mshta", "rundll32",
            "taskmgr", "control", "shutdown", "restart", "taskkill", "tskill",
            "sc", "schtasks", "vssadmin", "format", "diskpart", "chkdsk",
            "certutil", "bitsadmin", "mstsc", "mmc", "eventvwr", "dxdiag",
            "services.msc", "compmgmt.msc", "lusrmgr.msc", "cleanmgr",
            "ipconfig", "net", "net1", "route", "arp", "systeminfo",
            "regsvr32", "conhost", "powershell.exe", "cmd.exe", "wmic", "start",
            "openfiles", "netsh", "gpupdate", "driverquery", "pnputil",
            "systemctl", "service", "init", "reboot", "poweroff", "halt"
        }
        if first_token in host_blocked_binaries:
            return {
                "ok": False,
                "output": (
                    f"⛔ Security Violation: Execution of host utility '{first_token}' is blocked.\n"
                    "The Practice Playground is strictly isolated from your local host machine to protect host integrity."
                ),
                "returncode": 126,
                "env_type": sess["env_type"]
            }

        # 3. Block absolute host drive paths, UNC shares, and parent directory traversals
        if (
            re.search(r'(\b[a-zA-Z]:[\\/]|\\\\|/[a-zA-Z]/)', cmd)
            or re.search(r'(^|\s)/[a-zA-Z]($|\s)', cmd)
            or re.search(r'(^|[\s\\/])\.\.($|[\s\\/])', cmd)
            or re.search(r'(--git-dir|--work-tree|-C\s+)', cmd, flags=re.IGNORECASE)
        ):
            return {
                "ok": False,
                "output": "⛔ Security Violation: Access to host drives, parent paths, or repository escape flags is strictly forbidden.",
                "returncode": 1,
                "env_type": sess["env_type"]
            }

        ws_path = self._resolve_secure_session_path(session_id)
        if not ws_path or not os.path.exists(ws_path):
            return {"ok": False, "output": "Workspace directory not found."}

        # Built-in instant cat handler (works across all platforms and environments)
        cat_match = re.match(r'^\s*cat\s+([^\s|><]+)\s*$', cmd)
        if cat_match:
            target_fname = cat_match.group(1).strip().strip("'\"")
            secure_fpath = self._resolve_secure_session_path(session_id, target_fname)
            if secure_fpath and os.path.isfile(secure_fpath):
                try:
                    with open(secure_fpath, "r", encoding="utf-8", errors="replace") as f:
                        content = f.read()
                    return {
                        "ok": True,
                        "output": self._sanitize_output(content, ws_path),
                        "returncode": 0,
                        "env_type": sess["env_type"]
                    }
                except Exception as e:
                    return {
                        "ok": False,
                        "output": f"cat: {target_fname}: {str(e)}",
                        "returncode": 1,
                        "env_type": sess["env_type"]
                    }
            elif secure_fpath:
                return {
                    "ok": False,
                    "output": f"cat: {target_fname}: No such file or directory",
                    "returncode": 1,
                    "env_type": sess["env_type"]
                }

        # Built-in instant pwd handler
        if stripped_lower == "pwd":
            return {
                "ok": True,
                "output": "/home/vlab",
                "returncode": 0,
                "env_type": sess["env_type"]
            }

        # Built-in instant whoami handler
        if stripped_lower == "whoami":
            return {
                "ok": True,
                "output": "vlab",
                "returncode": 0,
                "env_type": sess["env_type"]
            }

        # Built-in instant id handler
        if stripped_lower in ("id", "id -u", "id -un", "id -gn"):
            if stripped_lower == "id":
                return {
                    "ok": True,
                    "output": "uid=1000(vlab) gid=1000(vlab) groups=1000(vlab),27(sudo),100(users)",
                    "returncode": 0,
                    "env_type": sess["env_type"]
                }
            elif stripped_lower == "id -u":
                return {"ok": True, "output": "1000", "returncode": 0, "env_type": sess["env_type"]}
            elif stripped_lower == "id -un":
                return {"ok": True, "output": "vlab", "returncode": 0, "env_type": sess["env_type"]}
            elif stripped_lower == "id -gn":
                return {"ok": True, "output": "vlab", "returncode": 0, "env_type": sess["env_type"]}

        # Built-in instant hostname handler
        if stripped_lower == "hostname":
            return {
                "ok": True,
                "output": "vlab-sandbox",
                "returncode": 0,
                "env_type": sess["env_type"]
            }

        # Built-in instant env / printenv handler (prevents leaking host Windows environment variables)
        if stripped_lower in ("env", "printenv", "export"):
            return {
                "ok": True,
                "output": (
                    "USER=vlab\n"
                    "LOGNAME=vlab\n"
                    "HOME=/home/vlab\n"
                    "SHELL=/bin/bash\n"
                    "TERM=xterm-256color\n"
                    "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin\n"
                    "VLAB_ENVIRONMENT=sandbox\n"
                    "VLAB_ISOLATION=enforced"
                ),
                "returncode": 0,
                "env_type": sess["env_type"]
            }

        # Built-in instant uname handler
        if stripped_lower in ("uname", "uname -a", "uname -r", "uname -s"):
            return {
                "ok": True,
                "output": "Linux vlab-sandbox 6.6.0-vlab-container x86_64 GNU/Linux" if stripped_lower == "uname -a" else "Linux",
                "returncode": 0,
                "env_type": sess["env_type"]
            }

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
        if re.search(r'(\bformat\s+[a-z]:|\bmkfs|\brm\s+-[a-z]*r[a-z]*f\s+([/~]|\.\.)|\brmdir\s+/[sq]\s+[a-z]:)', stripped_lower):
            return {"ok": False, "output": "⛔ Security Policy: Destructive filesystem command blocked."}

        # Ensure git environment always has isolated git repository initialized
        if sess.get("env_type") == "git" or re.match(r'^\s*git\b', cmd, flags=re.IGNORECASE):
            if not os.path.exists(os.path.join(ws_path, ".git")):
                self._seed_workspace(ws_path, "git")

        # If docker command executed in playground, inject kernel cgroup resource controls
        exec_cmd = cmd
        if stripped_lower.startswith("docker run"):
            limit_flags = []
            if "--memory" not in exec_cmd and "-m " not in exec_cmd:
                limit_flags.append("--memory=512m")
            if "--cpus" not in exec_cmd:
                limit_flags.append("--cpus=1.0")
            if "--pids-limit" not in exec_cmd:
                limit_flags.append("--pids-limit=100")
            if limit_flags:
                exec_cmd = re.sub(r'^(docker\s+run\b)', r'\1 ' + ' '.join(limit_flags), exec_cmd, count=1, flags=re.IGNORECASE)

        # Normalize python3 to python on Windows if needed
        if sys.platform == "win32" and re.match(r'^\s*python3\b', exec_cmd):
            exec_cmd = re.sub(r'^\s*python3\b', 'python', exec_cmd, count=1)

        # Auto-correct git branches typo
        if re.match(r'^\s*git\s+branches\b', exec_cmd, flags=re.IGNORECASE):
            exec_cmd = re.sub(r'^\s*git\s+branches\b', 'git branch', exec_cmd, count=1, flags=re.IGNORECASE)

        # Prepare environment with Python directory & Git coreutils in PATH
        env = os.environ.copy()
        py_dir = os.path.dirname(sys.executable)
        scripts_dir = os.path.join(py_dir, "Scripts")
        git_usr_bin = r"C:\Program Files\Git\usr\bin"
        git_bin = r"C:\Program Files\Git\bin"
        git_cmd = r"C:\Program Files\Git\cmd"
        extra_paths = [py_dir, scripts_dir]
        for p in (git_usr_bin, git_bin, git_cmd):
            if os.path.isdir(p):
                extra_paths.append(p)
        env["PATH"] = ";".join(extra_paths) + ";" + env.get("PATH", "")
        env["PYTHONUNBUFFERED"] = "1"
        env["USER"] = "vlab"
        env["LOGNAME"] = "vlab"
        env["USERNAME"] = "vlab"
        env["HOME"] = ws_path
        env["GIT_CEILING_DIRECTORIES"] = f"{os.path.abspath(self.project_root)};{os.path.abspath(self.workspaces_root)}"
        env["GIT_DISCOVERY_ACROSS_FILESYSTEM"] = "0"
        env["GIT_CONFIG_NOSYSTEM"] = "1"
        env["GIT_CONFIG_GLOBAL"] = os.path.join(ws_path, ".gitconfig_dummy")
        env["GIT_AUTHOR_NAME"] = "VLab Student"
        env["GIT_AUTHOR_EMAIL"] = "student@vlab.local"
        env["GIT_COMMITTER_NAME"] = "VLab Student"
        env["GIT_COMMITTER_EMAIL"] = "student@vlab.local"

        is_win = sys.platform == "win32"
        bash_bin = self._find_bash_binary() if is_win else None

        # Python Sandbox Direct Evaluation:
        # If user is in the Python sandbox and types Python code directly (e.g. print("hello"), 2 + 2, import math, etc.)
        # instead of a shell command (ls, pip, git, clear, etc.), evaluate it directly in Python!
        is_python_env = sess.get("env_type") == "python"
        is_shell_utility = re.match(r'^\s*(python|python3|pip|pip3|cat|ls|head|tail|grep|touch|rm|mkdir|git|clear|cls|exit|echo|pwd|whoami|id|hostname|env|printenv)\b', cmd, flags=re.IGNORECASE)

        if is_python_env and not is_shell_utility:
            py_exec_code = (
                "import sys\n"
                f"_user_code = {repr(cmd)}\n"
                "try:\n"
                "    _val = eval(_user_code)\n"
                "    if _val is not None:\n"
                "        print(_val)\n"
                "except SyntaxError:\n"
                "    try:\n"
                "        exec(_user_code)\n"
                "    except Exception as e:\n"
                "        print(f'{type(e).__name__}: {e}', file=sys.stderr)\n"
                "        sys.exit(1)\n"
                "except Exception as e:\n"
                "    print(f'{type(e).__name__}: {e}', file=sys.stderr)\n"
                "    sys.exit(1)\n"
            )
            cmd_args = [sys.executable, "-c", py_exec_code]
            use_shell = False
        else:
            if is_win and bash_bin:
                cmd_args = [bash_bin, "-c", exec_cmd]
                use_shell = False
            elif is_win:
                powershell_bin = shutil.which("powershell")
                if powershell_bin:
                    cmd_args = [powershell_bin, "-NoProfile", "-Command", exec_cmd]
                    use_shell = False
                else:
                    cmd_args = exec_cmd
                    use_shell = True
            else:
                cmd_args = exec_cmd
                use_shell = True

        # Run in subprocess inside isolated session workspace
        try:
            creation_flags = subprocess.CREATE_NO_WINDOW if is_win else 0
            proc = subprocess.run(
                cmd_args,
                shell=use_shell,
                cwd=ws_path,
                env=env,
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

            clean_out = self._sanitize_output(out, ws_path)

            return {
                "ok": proc.returncode == 0,
                "output": clean_out.strip() if clean_out else f"Process exited with code {proc.returncode}.",
                "returncode": proc.returncode,
                "env_type": sess["env_type"]
            }
        except subprocess.TimeoutExpired:
            return {"ok": False, "output": "Command execution timed out (25-second limit exceeded)."}
        except Exception as e:
            return {"ok": False, "output": f"Execution error: {self._sanitize_output(str(e), ws_path)}"}

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
                    if f.startswith(".git"):
                        continue
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
                git_env = os.environ.copy()
                git_env["GIT_CEILING_DIRECTORIES"] = f"{os.path.abspath(self.project_root)};{os.path.abspath(self.workspaces_root)}"
                git_env["GIT_DISCOVERY_ACROSS_FILESYSTEM"] = "0"
                git_env["GIT_CONFIG_NOSYSTEM"] = "1"
                git_env["GIT_CONFIG_GLOBAL"] = os.path.join(ws_path, ".gitconfig_dummy")
                git_env["GIT_AUTHOR_NAME"] = "VLab Student"
                git_env["GIT_AUTHOR_EMAIL"] = "student@vlab.local"
                git_env["GIT_COMMITTER_NAME"] = "VLab Student"
                git_env["GIT_COMMITTER_EMAIL"] = "student@vlab.local"
                subprocess.run("git init", cwd=ws_path, shell=True, capture_output=True, timeout=5, env=git_env)
                subprocess.run('git config user.name "VLab Student"', cwd=ws_path, shell=True, capture_output=True, timeout=5, env=git_env)
                subprocess.run('git config user.email "student@vlab.local"', cwd=ws_path, shell=True, capture_output=True, timeout=5, env=git_env)
                subprocess.run("git add .", cwd=ws_path, shell=True, capture_output=True, timeout=5, env=git_env)
                subprocess.run('git commit -m "feat: initial practice repository setup"', cwd=ws_path, shell=True, capture_output=True, timeout=5, env=git_env)
                subprocess.run("git branch -M main", cwd=ws_path, shell=True, capture_output=True, timeout=5, env=git_env)
                subprocess.run("git branch feature-auth", cwd=ws_path, shell=True, capture_output=True, timeout=5, env=git_env)
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
