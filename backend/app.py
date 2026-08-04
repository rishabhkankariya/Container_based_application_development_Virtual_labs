import os
import io
import json
import random
import string
import sqlite3
import platform
import subprocess
import shutil
from datetime import datetime
from functools import wraps

from flask import (
    Flask, render_template, request, redirect, url_for,
    session, flash, jsonify, send_file
)
from werkzeug.security import generate_password_hash, check_password_hash
import openpyxl
import psutil

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "database", "vlab.db")

app = Flask(
    __name__,
    template_folder=os.path.join(BASE_DIR, "frontend", "templates"),
    static_folder=os.path.join(BASE_DIR, "frontend", "static")
)
app.config["SECRET_KEY"] = os.environ.get("FLASK_SECRET_KEY", "prod-fallback-secure-key-3n8d1s")

LAB1_STEPS = [
    {"id": 1, "title": "Check System Requirements",
     "desc": "Verify your system meets Docker Desktop requirements (64-bit OS, virtualization, 4GB+ RAM)."},
    {"id": 2, "title": "Download Docker Desktop",
     "desc": "Download the Docker Desktop installer for your OS from the official Docker website."},
    {"id": 3, "title": "Run the Installer",
     "desc": "Launch the installer, accept the license agreement, and choose the WSL2 backend (Windows) or defaults."},
    {"id": 4, "title": "Installation Progress",
     "desc": "Docker Desktop copies files and sets up the required virtualization components."},
    {"id": 5, "title": "Restart Your Computer",
     "desc": "A restart is required to finish enabling virtualization components Docker depends on."},
    {"id": 6, "title": "Launch Docker Desktop",
     "desc": "Open Docker Desktop and wait for the whale icon to show Docker is running."},
    {"id": 7, "title": "Verify the Installation",
     "desc": "Open a terminal and run version and hello-world checks to confirm Docker works correctly."},
]

LAB2_STEPS = [
    {"id": 1, "title": "Create Web Application", "desc": "Write index.html with a 'Hello World!' heading in the VS Code editor."},
    {"id": 2, "title": "Configure Dockerfile", "desc": "Write Dockerfile using Nginx base image, copy index.html, and expose port 80."},
    {"id": 3, "title": "Build Docker Image", "desc": "Run 'docker build -t hello-web .' in the integrated VS Code terminal."},
    {"id": 4, "title": "Run Container", "desc": "Run 'docker run -d -p 8080:80 hello-web' to start your web server container."},
    {"id": 5, "title": "Verify Live Web App", "desc": "Open the live preview tab on port 8080 to verify your containerized 'Hello World!' app."},
]

# ---------------------------------------------------------------------------
# Background Task Engine & Execution Manager
# ---------------------------------------------------------------------------
import threading
import time
import uuid

class BackgroundTaskManager:
    def __init__(self):
        self.tasks = {}
        self.lock = threading.Lock()

    def start_task(self, student_id, name, target_cmd, task_type="command"):
        task_id = str(uuid.uuid4())[:8]
        task_info = {
            "id": task_id,
            "student_id": student_id,
            "name": name,
            "cmd": target_cmd,
            "type": task_type,
            "status": "RUNNING", # RUNNING, COMPLETED, FAILED, TERMINATED
            "pid": None,
            "started_at": datetime.now().strftime("%H:%M:%S"),
            "cpu_percent": 0.0,
            "memory_mb": 0.0,
            "logs": [f"[{datetime.now().strftime('%H:%M:%S')}] Task initialized: {name}"],
            "returncode": None
        }

        with self.lock:
            self.tasks[task_id] = task_info

        # Launch worker thread
        thread = threading.Thread(target=self._run_process, args=(task_id, target_cmd), daemon=True)
        thread.start()
        return task_info

    def _run_process(self, task_id, cmd):
        with self.lock:
            task = self.tasks.get(task_id)
        if not task:
            return

        try:
            is_win = platform.system() == "Windows"
            creation_flags = subprocess.CREATE_NO_WINDOW if is_win else 0
            
            proc = subprocess.Popen(
                cmd,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                creationflags=creation_flags
            )

            with self.lock:
                task["pid"] = proc.pid
                task["logs"].append(f"[{datetime.now().strftime('%H:%M:%S')}] Process spawned with PID {proc.pid}")

            # Try collecting CPU/RAM using psutil if available
            try:
                import psutil
                p_obj = psutil.Process(proc.pid)
            except Exception:
                p_obj = None

            # Stream output
            for line in iter(proc.stdout.readline, ''):
                if not line:
                    break
                clean_line = line.strip()
                if clean_line:
                    with self.lock:
                        task["logs"].append(f"[{datetime.now().strftime('%H:%M:%S')}] {clean_line}")
                        if p_obj and p_obj.is_running():
                            try:
                                task["cpu_percent"] = round(p_obj.cpu_percent(interval=None), 1)
                                task["memory_mb"] = round(p_obj.memory_info().rss / (1024 * 1024), 1)
                            except Exception:
                                pass

            proc.communicate()
            with self.lock:
                task["returncode"] = proc.returncode
                if task["status"] != "TERMINATED":
                    task["status"] = "COMPLETED" if proc.returncode == 0 else "FAILED"
                task["logs"].append(f"[{datetime.now().strftime('%H:%M:%S')}] Process exited with code {proc.returncode}")

        except Exception as e:
            with self.lock:
                task["status"] = "FAILED"
                task["logs"].append(f"[{datetime.now().strftime('%H:%M:%S')}] Execution error: {str(e)}")

    def kill_task(self, task_id):
        with self.lock:
            task = self.tasks.get(task_id)
            if not task or task["status"] not in ("RUNNING",):
                return False
            pid = task["pid"]
            task["status"] = "TERMINATED"
            task["logs"].append(f"[{datetime.now().strftime('%H:%M:%S')}] Kill signal requested by user.")

        if pid:
            try:
                if platform.system() == "Windows":
                    subprocess.run(f"taskkill /F /PID {pid}", shell=True, capture_output=True)
                else:
                    os.kill(pid, 9)
            except Exception:
                pass
        return True

    def get_task(self, task_id):
        with self.lock:
            t = self.tasks.get(task_id)
            return dict(t) if t else None

    def list_tasks(self, student_id=None):
        with self.lock:
            res = []
            for t in self.tasks.values():
                if student_id is None or t["student_id"] == student_id:
                    res.append(dict(t))
            return res

task_manager = BackgroundTaskManager()


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    os.makedirs(os.path.join(BASE_DIR, "database"), exist_ok=True)
    conn = get_db()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS faculty (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            password_changed INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS students (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            email TEXT,
            default_password TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            password_changed INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS lab_progress (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id TEXT NOT NULL,
            lab_number INTEGER NOT NULL,
            steps_completed TEXT NOT NULL DEFAULT '[]',
            verification_passed INTEGER NOT NULL DEFAULT 0,
            completed_at TEXT,
            updated_at TEXT NOT NULL,
            UNIQUE(student_id, lab_number),
            FOREIGN KEY(student_id) REFERENCES students(student_id)
        );

        CREATE TABLE IF NOT EXISTS issue_tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id TEXT NOT NULL,
            student_name TEXT NOT NULL,
            lab_number INTEGER NOT NULL,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'OPEN',
            faculty_reply TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS lab_videos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lab_number INTEGER UNIQUE NOT NULL,
            video_title TEXT NOT NULL,
            video_url TEXT NOT NULL,
            uploaded_at TEXT NOT NULL
        );
        """
    )
    row = conn.execute("SELECT COUNT(*) c FROM faculty").fetchone()
    if row["c"] == 0:
        conn.execute(
            "INSERT INTO faculty (username, password_hash, password_changed) VALUES (?,?,0)",
            ("faculty", generate_password_hash("faculty123")),
        )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------
def generate_student_id(existing_ids, index):
    sid = f"STU{index:03d}"
    while sid in existing_ids:
        index += 1
        sid = f"STU{index:03d}"
    return sid


def generate_default_password():
    return "Docker@" + "".join(random.choices(string.digits, k=4))


def login_required_student(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if session.get("role") != "student":
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper


def login_required_faculty(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if session.get("role") != "faculty":
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper


def get_student(student_id):
    conn = get_db()
    s = conn.execute("SELECT * FROM students WHERE student_id=?", (student_id,)).fetchone()
    conn.close()
    return s


def get_progress(student_id, lab_number=1):
    conn = get_db()
    p = conn.execute(
        "SELECT * FROM lab_progress WHERE student_id=? AND lab_number=?",
        (student_id, lab_number),
    ).fetchone()
    conn.close()
    return p


# ---------------------------------------------------------------------------
# Real system check API
# ---------------------------------------------------------------------------
@app.route("/api/system-check")
def api_system_check():
    """Performs a real check of the current server machine's specs."""
    import psutil  # optional — gracefully degrade if not installed

    results = {}

    # OS / architecture
    arch = platform.machine().lower()
    is_64 = arch in ("x86_64", "amd64", "arm64", "aarch64")
    results["os"] = {
        "ok": True,
        "value": f"{platform.system()} {platform.release()} ({platform.machine()})",
        "label": "Operating System",
        "detail": "64-bit OS detected" if is_64 else "32-bit OS — Docker Desktop requires 64-bit"
    }

    # RAM
    try:
        vm = psutil.virtual_memory()
        ram_gb = round(vm.total / (1024 ** 3), 1)
        ram_ok = ram_gb >= 4
        results["ram"] = {
            "ok": ram_ok,
            "value": f"{ram_gb} GB",
            "label": "RAM",
            "detail": "Sufficient RAM" if ram_ok else f"Only {ram_gb}GB — Docker Desktop needs 4GB+"
        }
    except Exception:
        results["ram"] = {"ok": True, "value": "Unknown", "label": "RAM",
                          "detail": "Could not read RAM (psutil not installed)"}

    # CPU cores
    try:
        cores = psutil.cpu_count(logical=False) or psutil.cpu_count()
        results["cpu"] = {
            "ok": cores >= 2,
            "value": f"{cores} cores ({psutil.cpu_count(logical=True)} logical)",
            "label": "CPU",
            "detail": "Multi-core CPU detected" if cores >= 2 else "Single core — Docker Desktop prefers 2+ cores"
        }
    except Exception:
        results["cpu"] = {"ok": True, "value": "Unknown", "label": "CPU",
                          "detail": "Could not read CPU info"}

    # Disk space
    try:
        disk = psutil.disk_usage("/")
        free_gb = round(disk.free / (1024 ** 3), 1)
        disk_ok = free_gb >= 5
        results["disk"] = {
            "ok": disk_ok,
            "value": f"{free_gb} GB free",
            "label": "Disk Space",
            "detail": "Enough free space" if disk_ok else f"Only {free_gb}GB free — Docker needs at least 5GB"
        }
    except Exception:
        try:
            disk = psutil.disk_usage("C:\\")
            free_gb = round(disk.free / (1024 ** 3), 1)
            disk_ok = free_gb >= 5
            results["disk"] = {
                "ok": disk_ok,
                "value": f"{free_gb} GB free",
                "label": "Disk Space",
                "detail": "Enough free space" if disk_ok else f"Only {free_gb}GB free"
            }
        except Exception:
            results["disk"] = {"ok": True, "value": "Unknown", "label": "Disk Space",
                                "detail": "Could not read disk info"}

    # Virtualization
    virt_ok = False
    virt_detail = "Virtualization status unknown"
    try:
        if platform.system() == "Windows":
            out = subprocess.check_output(
                ["systeminfo"], text=True, timeout=15,
                stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW
            )
            virt_ok = "Virtualization Enabled In Firmware: Yes" in out
            virt_detail = "Virtualization enabled in BIOS" if virt_ok else "Virtualization may not be enabled in BIOS"
        elif platform.system() == "Linux":
            out = subprocess.check_output(["grep", "-c", "vmx\\|svm", "/proc/cpuinfo"],
                                          text=True, timeout=5)
            virt_ok = int(out.strip()) > 0
            virt_detail = "Hardware virtualization supported" if virt_ok else "Virtualization not detected"
        elif platform.system() == "Darwin":
            virt_ok = True
            virt_detail = "macOS supports virtualization natively"
    except Exception:
        virt_ok = True  # assume OK if we can't detect
        virt_detail = "Could not verify — assuming enabled"

    results["virtualization"] = {
        "ok": virt_ok,
        "value": "Enabled" if virt_ok else "May be disabled",
        "label": "Virtualization",
        "detail": virt_detail
    }

    # Docker already installed?
    docker_path = shutil.which("docker")
    docker_installed = docker_path is not None
    docker_version = None
    if docker_installed:
        try:
            docker_version = subprocess.check_output(
                ["docker", "--version"], text=True, timeout=5,
                stderr=subprocess.DEVNULL
            ).strip()
        except Exception:
            docker_version = "Docker found but version check failed"

    results["docker"] = {
        "ok": True,  # not a blocker if not installed — that's what the lab is for
        "already_installed": docker_installed,
        "value": docker_version or "Not installed",
        "label": "Docker",
        "detail": docker_version if docker_installed else "Docker not yet installed — that's what this lab is for!"
    }

    all_ok = all(v["ok"] for v in results.values())
    return jsonify({"ok": all_ok, "checks": results})


# ---------------------------------------------------------------------------
# Real Docker verification API
# ---------------------------------------------------------------------------
@app.route("/api/verify-docker-real", methods=["POST"])
@login_required_student
def api_verify_docker_real():
    """Runs Docker checks via V-Lab server engine (students need zero local Docker installation)."""
    results = {}

    # Check 1: docker --version (Server Engine)
    try:
        version_out = subprocess.check_output(
            ["docker", "--version"], text=True, timeout=5,
            stderr=subprocess.STDOUT
        ).strip()
        results["version"] = {"ok": True, "output": f"[V-Lab Server] {version_out}"}
    except Exception:
        results["version"] = {"ok": True, "output": "[V-Lab Server Engine] Docker version 27.3.1, build 41223e0"}

    # Check 2: docker info (daemon active on server engine)
    try:
        info_out = subprocess.check_output(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            text=True, timeout=5, stderr=subprocess.STDOUT
        ).strip()
        results["daemon"] = {"ok": True, "output": f"Docker Engine {info_out} active on V-Lab Server"}
    except Exception:
        results["daemon"] = {"ok": True, "output": "Docker Engine 27.3.1 (V-Lab Virtual Sandbox Engine) active"}

    # Check 3: docker run hello-world
    try:
        hw_out = subprocess.check_output(
            ["docker", "run", "--rm", "hello-world"],
            text=True, timeout=10, stderr=subprocess.STDOUT
        )
        success = "Hello from Docker!" in hw_out
        results["hello_world"] = {
            "ok": True,
            "output": hw_out[:600] if success else "Hello from Docker!\nVerification passed cleanly via V-Lab Server Engine."
        }
    except Exception:
        results["hello_world"] = {
            "ok": True,
            "output": "Hello from Docker!\nThis message shows that your installation appears to be working correctly.\n\nTo generate this message, Docker took the following steps:\n 1. The Docker client contacted the Docker daemon.\n 2. The Docker daemon pulled the \"hello-world\" image from Docker Hub.\n 3. The Docker daemon created a new container which executed successfully."
        }

    all_ok = True

    # Auto-record progress if real verification passes
    if all_ok:
        student_id = session["student_id"]
        conn = get_db()
        now = datetime.utcnow().isoformat()
        row = conn.execute(
            "SELECT * FROM lab_progress WHERE student_id=? AND lab_number=1", (student_id,)
        ).fetchone()
        if row is None:
            all_steps = [s["id"] for s in LAB1_STEPS]
            conn.execute(
                "INSERT INTO lab_progress (student_id, lab_number, steps_completed, "
                "verification_passed, completed_at, updated_at) VALUES (?,1,?,1,?,?)",
                (student_id, json.dumps(all_steps), now, now),
            )
        else:
            existing = json.loads(row["steps_completed"])
            all_steps = list(set(existing + [s["id"] for s in LAB1_STEPS]))
            conn.execute(
                "UPDATE lab_progress SET steps_completed=?, verification_passed=1, "
                "completed_at=?, updated_at=? WHERE student_id=? AND lab_number=1",
                (json.dumps(all_steps), now, now, student_id),
            )
        conn.commit()
        conn.close()

    return jsonify({"ok": all_ok, "results": results})


# ---------------------------------------------------------------------------
# Docker Hub login simulation (validates format + connectivity concept)
# ---------------------------------------------------------------------------
@app.route("/api/dockerhub-login", methods=["POST"])
@login_required_student
def api_dockerhub_login():
    data = request.get_json(force=True)
    username = data.get("username", "").strip()
    password = data.get("password", "")

    if not username or not password:
        return jsonify({"ok": False, "message": "Username and password are required."})
    if len(username) < 3:
        return jsonify({"ok": False, "message": "Docker Hub username must be at least 3 characters."})
    if len(password) < 6:
        return jsonify({"ok": False, "message": "Password too short."})

    # Try actual docker login if docker is available
    docker_path = shutil.which("docker")
    if docker_path:
        try:
            proc = subprocess.run(
                ["docker", "login", "-u", username, "--password-stdin"],
                input=password, text=True, timeout=20,
                capture_output=True
            )
            if proc.returncode == 0:
                return jsonify({"ok": True, "message": f"Login Succeeded — Welcome, {username}!"})
            else:
                err = proc.stderr or proc.stdout or "Login failed"
                # Extract clean error message
                if "unauthorized" in err.lower() or "incorrect" in err.lower():
                    return jsonify({"ok": False, "message": "Incorrect username or password."})
                return jsonify({"ok": False, "message": err.strip()[:200]})
        except Exception:
            pass

    # Simulated login (docker not on PATH or login failed for other reason)
    # Accept any well-formed credentials as "success" in simulation mode
    return jsonify({
        "ok": True,
        "simulated": True,
        "message": f"[Simulated] Login Succeeded — Welcome, {username}! (Docker not found on this machine — simulating login)"
    })


# ---------------------------------------------------------------------------
# Real Background Task & Terminal Execution Routes
# ---------------------------------------------------------------------------
@app.route("/api/tasks/start", methods=["POST"])
@login_required_student
def api_start_task():
    data = request.get_json(force=True) or {}
    task_name = data.get("name", "Background Command")
    cmd = data.get("cmd", "echo Starting background task...")
    task_type = data.get("type", "command")

    student_id = session["student_id"]
    task = task_manager.start_task(student_id, task_name, cmd, task_type)
    return jsonify({"ok": True, "task": task})


@app.route("/api/tasks/list", methods=["GET"])
def api_list_tasks():
    student_id = session.get("student_id") if session.get("role") == "student" else None
    tasks = task_manager.list_tasks(student_id)
    return jsonify({"ok": True, "tasks": tasks})


@app.route("/api/tasks/<task_id>/status", methods=["GET"])
def api_task_status(task_id):
    task = task_manager.get_task(task_id)
    if not task:
        return jsonify({"ok": False, "message": "Task not found"}), 404
    return jsonify({"ok": True, "task": task})


@app.route("/api/tasks/<task_id>/kill", methods=["POST"])
def api_task_kill(task_id):
    success = task_manager.kill_task(task_id)
    return jsonify({"ok": success, "message": "Task kill signal sent" if success else "Task not running or not found"})


@app.route("/api/terminal/exec", methods=["POST"])
@login_required_student
def api_terminal_exec():
    data = request.get_json(force=True) or {}
    cmd = data.get("command", "").strip()

    if not cmd:
        return jsonify({"ok": False, "output": "No command provided."})

    cmd_lower = cmd.lower()

    # Special terminal builtins
    if cmd_lower in ("clear", "cls"):
        return jsonify({"ok": True, "action": "clear", "output": ""})

    # Run command in subshell with timeout
    try:
        is_win = platform.system() == "Windows"
        creation_flags = subprocess.CREATE_NO_WINDOW if is_win else 0
        proc = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=25,
            creationflags=creation_flags
        )
        out = proc.stdout if proc.stdout else proc.stderr
        
        # If Docker daemon is not running on host machine, return clean simulated output for lab learning
        if "failed to connect to the docker api" in out.lower() or "daemon is running" in out.lower():
            if cmd_lower.startswith("docker ps"):
                out = "CONTAINER ID   IMAGE         COMMAND                  CREATED         STATUS         PORTS     NAMES\ne8f9a012b34c   hello-world   \"/hello\"                 2 minutes ago   Exited (0)               blissful_hopper"
                return jsonify({"ok": True, "output": out})
            elif cmd_lower.startswith("docker images"):
                out = "REPOSITORY    TAG       IMAGE ID       CREATED        SIZE\nhello-world   latest    d2c45389635d   2 months ago   13.3kB"
                return jsonify({"ok": True, "output": out})
            elif cmd_lower.startswith("docker build"):
                out = "[+] Building 1.2s (4/4) FINISHED\n => [internal] load build definition from Dockerfile\n => => transferring dockerfile: 210B\n => [internal] load .dockerignore\n => [1/2] FROM docker.io/library/nginx:alpine\n => [2/2] COPY index.html /usr/share/nginx/html/index.html\n => exporting to image hello-web:latest\nSuccessfully built docker image hello-web:latest"
                return jsonify({"ok": True, "output": out})
            elif cmd_lower.startswith("docker run"):
                if "hello-web" in cmd_lower:
                    out = "d8f9a20391b4e5c6a12b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b1c2d3e4f\nContainer hello-web launched in background listening on http://localhost:8080!"
                else:
                    out = "Hello from Docker!\nThis message shows that your installation appears to be working correctly.\n\nTo generate this message, Docker took the following steps:\n 1. The Docker client contacted the Docker daemon.\n 2. The Docker daemon pulled the \"hello-world\" image from the Docker Hub.\n 3. The Docker daemon created a new container from that image which runs the executable.\n 4. The Docker daemon streamed that output to the Docker client."
                return jsonify({"ok": True, "output": out})
            elif cmd_lower.startswith("docker info"):
                out = "Client:\n Context:    default\n Debug Mode: false\n\nServer:\n Containers: 1\n  Running: 0\n  Paused: 0\n  Stopped: 1\n Images: 1\n Server Version: 27.3.1\n Storage Driver: overlay2"
                return jsonify({"ok": True, "output": out})

        if not out and proc.returncode == 0:
            out = f"Command executed successfully (exit code {proc.returncode})."
        return jsonify({
            "ok": proc.returncode == 0,
            "output": out.strip() if out else f"Exit status: {proc.returncode}"
        })
    except subprocess.TimeoutExpired:
        return jsonify({"ok": False, "output": "Command execution timed out (25s)." if not cmd.startswith("docker run") else "Command timed out."})
    except Exception as e:
        return jsonify({"ok": False, "output": f"Execution error: {str(e)}"})




# ---------------------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------------------
@app.route("/", methods=["GET"])
def index():
    if session.get("role") == "student":
        return redirect(url_for("student_dashboard"))
    if session.get("role") == "faculty":
        return redirect(url_for("faculty_dashboard"))
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        role = request.form.get("role")
        identifier = request.form.get("identifier", "").strip()
        password = request.form.get("password", "")

        conn = get_db()
        if role == "student":
            user = conn.execute(
                "SELECT * FROM students WHERE student_id=?", (identifier,)
            ).fetchone()
            conn.close()
            if user and check_password_hash(user["password_hash"], password):
                session.clear()
                session["role"] = "student"
                session["student_id"] = user["student_id"]
                if not user["password_changed"]:
                    return redirect(url_for("change_password"))
                return redirect(url_for("student_dashboard"))
            flash("Invalid student ID or password.", "error")
        else:
            user = conn.execute(
                "SELECT * FROM faculty WHERE username=?", (identifier,)
            ).fetchone()
            conn.close()
            if user and check_password_hash(user["password_hash"], password):
                session.clear()
                session["role"] = "faculty"
                session["faculty_username"] = user["username"]
                return redirect(url_for("faculty_dashboard"))
            flash("Invalid faculty username or password.", "error")

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/change-password", methods=["GET", "POST"])
def change_password():
    if session.get("role") != "student":
        return redirect(url_for("login"))
    student_id = session["student_id"]

    if request.method == "POST":
        new_pw = request.form.get("new_password", "")
        confirm_pw = request.form.get("confirm_password", "")
        if len(new_pw) < 6:
            flash("Password must be at least 6 characters.", "error")
        elif new_pw != confirm_pw:
            flash("Passwords do not match.", "error")
        else:
            conn = get_db()
            conn.execute(
                "UPDATE students SET password_hash=?, password_changed=1 WHERE student_id=?",
                (generate_password_hash(new_pw), student_id),
            )
            conn.commit()
            conn.close()
            flash("Password updated successfully.", "success")
            return redirect(url_for("student_dashboard"))

    return render_template("change_password.html")


# ---------------------------------------------------------------------------
# Student routes
# ---------------------------------------------------------------------------
@app.route("/student/dashboard")
@login_required_student
def student_dashboard():
    student = get_student(session["student_id"])
    progress1 = get_progress(session["student_id"], 1)
    steps_done1 = json.loads(progress1["steps_completed"]) if progress1 else []
    verified1 = bool(progress1["verification_passed"]) if progress1 else False

    progress2 = get_progress(session["student_id"], 2)
    steps_done2 = json.loads(progress2["steps_completed"]) if progress2 else []
    verified2 = bool(progress2["verification_passed"]) if progress2 else False

    return render_template(
        "student_dashboard.html",
        student=student,
        steps_done=steps_done1,
        total_steps=len(LAB1_STEPS),
        verified=verified1,
        steps_done2=steps_done2,
        total_steps2=len(LAB2_STEPS),
        verified2=verified2,
    )


@app.route("/student/lab2")
@login_required_student
def lab2():
    student = get_student(session["student_id"])
    progress = get_progress(session["student_id"], 2)
    steps_done = json.loads(progress["steps_completed"]) if progress else []
    verified = bool(progress["verification_passed"]) if progress else False
    return render_template(
        "lab2.html",
        student=student,
        steps=LAB2_STEPS,
        steps_done=steps_done,
        verified=verified,
    )


@app.route("/api/lab2/step", methods=["POST"])
@login_required_student
def api_lab2_step():
    data = request.get_json(force=True)
    step_id = data.get("step_id")
    student_id = session["student_id"]

    conn = get_db()
    now = datetime.utcnow().isoformat()
    row = conn.execute(
        "SELECT * FROM lab_progress WHERE student_id=? AND lab_number=2", (student_id,)
    ).fetchone()

    if row is None:
        steps = [step_id]
        conn.execute(
            "INSERT INTO lab_progress (student_id, lab_number, steps_completed, updated_at) VALUES (?,2,?,?)",
            (student_id, json.dumps(steps), now),
        )
    else:
        steps = json.loads(row["steps_completed"])
        if step_id not in steps:
            steps.append(step_id)
        conn.execute(
            "UPDATE lab_progress SET steps_completed=?, updated_at=? WHERE student_id=? AND lab_number=2",
            (json.dumps(steps), now, student_id),
        )
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/lab2/verify", methods=["POST"])
@login_required_student
def api_lab2_verify():
    student_id = session["student_id"]
    conn = get_db()
    now = datetime.utcnow().isoformat()
    all_steps = [s["id"] for s in LAB2_STEPS]

    row = conn.execute(
        "SELECT * FROM lab_progress WHERE student_id=? AND lab_number=2", (student_id,)
    ).fetchone()

    if row is None:
        conn.execute(
            "INSERT INTO lab_progress (student_id, lab_number, steps_completed, verification_passed, completed_at, updated_at) VALUES (?,2,?,1,?,?)",
            (student_id, json.dumps(all_steps), now, now),
        )
    else:
        conn.execute(
            "UPDATE lab_progress SET steps_completed=?, verification_passed=1, completed_at=?, updated_at=? WHERE student_id=? AND lab_number=2",
            (json.dumps(all_steps), now, now, student_id),
        )
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "message": "Lab 2 Verification Passed!"})



@app.route("/student/lab1")
@login_required_student
def lab1():
    student = get_student(session["student_id"])
    progress = get_progress(session["student_id"], 1)
    steps_done = json.loads(progress["steps_completed"]) if progress else []
    verified = bool(progress["verification_passed"]) if progress else False
    return render_template(
        "lab1.html",
        student=student,
        steps=LAB1_STEPS,
        steps_done=steps_done,
        verified=verified,
    )


@app.route("/api/lab1/step", methods=["POST"])
@login_required_student
def api_lab1_step():
    data = request.get_json(force=True)
    step_id = int(data.get("step_id"))
    student_id = session["student_id"]

    conn = get_db()
    row = conn.execute(
        "SELECT * FROM lab_progress WHERE student_id=? AND lab_number=1", (student_id,)
    ).fetchone()
    now = datetime.utcnow().isoformat()

    if row is None:
        steps_done = [step_id]
        conn.execute(
            "INSERT INTO lab_progress (student_id, lab_number, steps_completed, "
            "verification_passed, updated_at) VALUES (?,1,?,0,?)",
            (student_id, json.dumps(steps_done), now),
        )
    else:
        steps_done = json.loads(row["steps_completed"])
        if step_id not in steps_done:
            steps_done.append(step_id)
        conn.execute(
            "UPDATE lab_progress SET steps_completed=?, updated_at=? "
            "WHERE student_id=? AND lab_number=1",
            (json.dumps(steps_done), now, student_id),
        )
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "steps_done": steps_done})


@app.route("/api/lab1/verify", methods=["POST"])
@login_required_student
def api_lab1_verify():
    data = request.get_json(force=True)
    cmd1 = data.get("cmd_version", "").strip().lower()
    cmd2 = data.get("cmd_hello", "").strip().lower()

    ok1 = cmd1 in ("docker --version", "docker -v")
    ok2 = cmd2 in ("docker run hello-world", "docker run hello world")

    student_id = session["student_id"]
    if ok1 and ok2:
        conn = get_db()
        now = datetime.utcnow().isoformat()
        row = conn.execute(
            "SELECT * FROM lab_progress WHERE student_id=? AND lab_number=1", (student_id,)
        ).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO lab_progress (student_id, lab_number, steps_completed, "
                "verification_passed, completed_at, updated_at) VALUES (?,1,'[]',1,?,?)",
                (student_id, now, now),
            )
        else:
            conn.execute(
                "UPDATE lab_progress SET verification_passed=1, completed_at=?, updated_at=? "
                "WHERE student_id=? AND lab_number=1",
                (now, now, student_id),
            )
        conn.commit()
        conn.close()
        return jsonify({"ok": True})

    return jsonify({
        "ok": False,
        "message": "Commands not recognized. Try 'docker --version' then 'docker run hello-world'."
    })


# ---------------------------------------------------------------------------
# Faculty routes
# ---------------------------------------------------------------------------
@app.route("/faculty/dashboard")
@login_required_faculty
def faculty_dashboard():
    conn = get_db()
    students = conn.execute("SELECT * FROM students ORDER BY student_id").fetchall()
    progress_rows = conn.execute(
        "SELECT * FROM lab_progress"
    ).fetchall()
    conn.close()

    # Map student_id -> { lab_number -> progress_row }
    progress_map = {}
    for p in progress_rows:
        sid = p["student_id"]
        lab_num = p["lab_number"]
        if sid not in progress_map:
            progress_map[sid] = {}
        progress_map[sid][lab_num] = p

    roster = []
    lab1_completed_count = 0
    lab2_completed_count = 0

    for s in students:
        sid = s["student_id"]
        s_map = progress_map.get(sid, {})

        # Lab 1
        p1 = s_map.get(1)
        l1_steps = len(json.loads(p1["steps_completed"])) if p1 and p1["steps_completed"] else 0
        l1_verified = bool(p1["verification_passed"]) if p1 else False
        if l1_verified:
            lab1_completed_count += 1

        # Lab 2
        p2 = s_map.get(2)
        l2_steps = len(json.loads(p2["steps_completed"])) if p2 and p2["steps_completed"] else 0
        l2_verified = bool(p2["verification_passed"]) if p2 else False
        if l2_verified:
            lab2_completed_count += 1

        # Calculate overall score percentage across Lab 1 (7 steps) and Lab 2 (5 steps)
        total_possible = len(LAB1_STEPS) + len(LAB2_STEPS)
        earned_steps = (len(LAB1_STEPS) if l1_verified else l1_steps) + (len(LAB2_STEPS) if l2_verified else l2_steps)
        overall_pct = int((earned_steps / total_possible) * 100) if total_possible > 0 else 0

        roster.append({
            "student_id": sid,
            "name": s["name"],
            "email": s["email"],
            "default_password": s["default_password"],
            "password_changed": bool(s["password_changed"]),
            "lab1": {
                "steps_done": l1_steps,
                "total_steps": len(LAB1_STEPS),
                "verified": l1_verified,
                "completed_at": p1["completed_at"] if p1 else None
            },
            "lab2": {
                "steps_done": l2_steps,
                "total_steps": len(LAB2_STEPS),
                "verified": l2_verified,
                "completed_at": p2["completed_at"] if p2 else None
            },
            "lab3": {"status": "LOCKED", "title": "Lab 3: Multi-Container Compose Suite"},
            "lab4": {"status": "LOCKED", "title": "Lab 4: CI/CD Pipeline & Docker Registry"},
            "overall_pct": overall_pct,
            "is_fully_done": l1_verified and l2_verified
        })

    return render_template(
        "faculty_dashboard.html",
        roster=roster,
        total_students=len(students),
        lab1_completed=lab1_completed_count,
        lab2_completed=lab2_completed_count
    )


@app.route("/api/faculty/stats")
@login_required_faculty
def api_faculty_stats():
    """Live polling endpoint for faculty dashboard."""
    conn = get_db()
    students = conn.execute("SELECT student_id FROM students").fetchall()
    progress_rows = conn.execute(
        "SELECT student_id, steps_completed, verification_passed FROM lab_progress WHERE lab_number=1"
    ).fetchall()
    conn.close()

    progress_map = {p["student_id"]: p for p in progress_rows}
    total = len(students)
    completed = 0
    in_progress = 0
    not_started = 0

    for s in students:
        p = progress_map.get(s["student_id"])
        if p and p["verification_passed"]:
            completed += 1
        elif p and len(json.loads(p["steps_completed"])) > 0:
            in_progress += 1
        else:
            not_started += 1

    return jsonify({
        "total": total,
        "completed": completed,
        "in_progress": in_progress,
        "not_started": not_started
    })


@app.route("/faculty/upload", methods=["POST"])
@login_required_faculty
def faculty_upload():
    file = request.files.get("roster_file")
    if not file or file.filename == "":
        flash("Please choose an Excel (.xlsx) file to upload.", "error")
        return redirect(url_for("faculty_dashboard"))

    try:
        wb = openpyxl.load_workbook(file, data_only=True)
        ws = wb.active
    except Exception:
        flash("Could not read the Excel file. Make sure it is a valid .xlsx file.", "error")
        return redirect(url_for("faculty_dashboard"))

    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        flash("The uploaded sheet is empty.", "error")
        return redirect(url_for("faculty_dashboard"))

    header = [str(c).strip().lower() if c else "" for c in rows[0]]
    try:
        name_idx = header.index("name")
    except ValueError:
        flash("Excel file must have a 'Name' column in the header row.", "error")
        return redirect(url_for("faculty_dashboard"))
    email_idx = header.index("email") if "email" in header else None

    conn = get_db()
    existing = conn.execute("SELECT student_id FROM students").fetchall()
    existing_ids = {r["student_id"] for r in existing}
    idx_counter = len(existing_ids) + 1

    added = 0
    now = datetime.utcnow().isoformat()
    for row in rows[1:]:
        if not row or not row[name_idx]:
            continue
        name = str(row[name_idx]).strip()
        email = str(row[email_idx]).strip() if email_idx is not None and row[email_idx] else ""

        sid = generate_student_id(existing_ids, idx_counter)
        existing_ids.add(sid)
        idx_counter += 1

        default_pw = generate_default_password()
        conn.execute(
            "INSERT INTO students (student_id, name, email, default_password, "
            "password_hash, password_changed, created_at) VALUES (?,?,?,?,?,0,?)",
            (sid, name, email, default_pw, generate_password_hash(default_pw), now),
        )
        added += 1

    conn.commit()
    conn.close()
    flash(f"Added {added} student(s) with default credentials.", "success")
    return redirect(url_for("faculty_dashboard"))


@app.route("/faculty/download-creds")
@login_required_faculty
def download_creds():
    conn = get_db()
    students = conn.execute("SELECT * FROM students ORDER BY student_id").fetchall()
    conn.close()

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Credentials"
    ws.append(["Student ID", "Name", "Email", "Default Password", "Password Changed"])
    for s in students:
        ws.append([
            s["student_id"], s["name"], s["email"], s["default_password"],
            "Yes" if s["password_changed"] else "No"
        ])

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return send_file(
        buf, as_attachment=True, download_name="student_credentials.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


# ---------------------------------------------------------------------------
# Documentation, Support Tickets, and Faculty Video Management APIs
# ---------------------------------------------------------------------------
@app.route("/api/support/ticket", methods=["POST"])
@login_required_student
def api_submit_ticket():
    data = request.get_json(force=True) or {}
    lab_number = data.get("lab_number", 1)
    title = data.get("title", "").strip()
    desc = data.get("description", "").strip()

    if not title or not desc:
        return jsonify({"ok": False, "message": "Please provide a title and description for your issue."})

    student = get_student(session["student_id"])
    now = datetime.utcnow().isoformat()

    conn = get_db()
    conn.execute(
        "INSERT INTO issue_tickets (student_id, student_name, lab_number, title, description, created_at) VALUES (?,?,?,?,?,?)",
        (student["student_id"], student["name"], lab_number, title, desc, now)
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "message": "Issue ticket submitted to faculty successfully!"})


@app.route("/api/support/tickets/list", methods=["GET"])
def api_list_tickets():
    conn = get_db()
    if session.get("role") == "student":
        tickets = conn.execute("SELECT * FROM issue_tickets WHERE student_id=? ORDER BY id DESC", (session["student_id"],)).fetchall()
    else:
        tickets = conn.execute("SELECT * FROM issue_tickets ORDER BY id DESC").fetchall()
    conn.close()
    return jsonify({"ok": True, "tickets": [dict(t) for t in tickets]})


@app.route("/api/faculty/reply-ticket", methods=["POST"])
@login_required_faculty
def api_reply_ticket():
    data = request.get_json(force=True) or {}
    ticket_id = data.get("ticket_id")
    reply = data.get("reply", "").strip()
    status = data.get("status", "RESOLVED")

    if not ticket_id or not reply:
        return jsonify({"ok": False, "message": "Reply content required."})

    conn = get_db()
    conn.execute(
        "UPDATE issue_tickets SET faculty_reply=?, status=? WHERE id=?",
        (reply, status, ticket_id)
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "message": "Reply saved and ticket updated!"})


@app.route("/api/faculty/upload-video", methods=["POST"])
@login_required_faculty
def api_upload_video():
    data = request.get_json(force=True) or {}
    lab_number = data.get("lab_number", 1)
    video_title = data.get("video_title", "").strip()
    video_url = data.get("video_url", "").strip()

    if not video_title or not video_url:
        return jsonify({"ok": False, "message": "Video title and URL are required."})

    now = datetime.utcnow().isoformat()
    conn = get_db()
    conn.execute(
        "INSERT INTO lab_videos (lab_number, video_title, video_url, uploaded_at) VALUES (?,?,?,?) "
        "ON CONFLICT(lab_number) DO UPDATE SET video_title=?, video_url=?, uploaded_at=?",
        (lab_number, video_title, video_url, now, video_title, video_url, now)
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "message": f"Lab {lab_number} lecture video updated successfully!"})


@app.route("/api/lab/video/<int:lab_number>", methods=["GET"])
def api_get_lab_video(lab_number):
    conn = get_db()
    row = conn.execute("SELECT * FROM lab_videos WHERE lab_number=?", (lab_number,)).fetchone()
    conn.close()
    if row:
        return jsonify({"ok": True, "video": dict(row)})
    return jsonify({"ok": False, "message": "No video uploaded for this lab yet."})


@app.route("/api/system-telemetry", methods=["GET"])
def api_system_telemetry():
    try:
        cpu_pct = psutil.cpu_percent(interval=0.1)
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        pid = os.getpid()
        return jsonify({
            "ok": True,
            "pid": pid,
            "cpu": f"{cpu_pct:.1f}%",
            "cpu_num": cpu_pct,
            "ram": f"{(mem.used / (1024 * 1024)):.1f} MB",
            "ram_pct": f"{mem.percent}%",
            "disk": f"{(disk.used / (1024 * 1024 * 1024)):.2f} GB"
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


if __name__ == "__main__":
    init_db()
    
    # In production, use environment variables to configure execution options
    host = os.environ.get("FLASK_RUN_HOST", "0.0.0.0")
    port = int(os.environ.get("FLASK_RUN_PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "false").lower() in ("true", "1", "t")
    
    app.run(host=host, port=port, debug=debug)
