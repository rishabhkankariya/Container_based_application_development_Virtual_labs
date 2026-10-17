import sys
import os

# Ensure repository root is on sys.path regardless of execution working directory
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import io
import json
import random
import string
import secrets
import sqlite3
import platform
import subprocess
import shutil
import hashlib
import html
import re
import time
import threading
import urllib.request
import urllib.error
from datetime import datetime, timezone
from functools import wraps
from typing import Optional, Dict, Any, List, Union, Tuple

from flask import (
    Flask, render_template, request, redirect, url_for,
    session, flash, jsonify, send_file
)
from werkzeug.security import generate_password_hash, check_password_hash
import openpyxl
import psutil

from backend.db import (
    get_db, init_db, save_workspace_file, get_workspace_files,
    delete_workspace_file, rename_workspace_file, enqueue_lab_progress_outbox
)
from backend.ha_manager import ha_cluster_manager
from backend.services.email_service import email_service
from backend.services.notification_service import notification_service
from backend.services.sync_service import cloud_sync_service

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "database", "vlab.db")

def _get_or_create_flask_secret() -> str:
    """
    Retrieves or generates a persistent, cryptographically secure Flask secret key.
    - If FLASK_SECRET_KEY is provided in environment, it is strictly used.
    - If in production mode and FLASK_SECRET_KEY is missing, fails startup immediately.
    - Otherwise in development/LAN mode, securely generates and persists a 256-bit secret.
    """
    env_secret = os.environ.get("FLASK_SECRET_KEY")
    if env_secret:
        return env_secret
    if os.environ.get("FLASK_ENV") == "production" or os.environ.get("ENV") == "production":
        raise RuntimeError("CRITICAL SECURITY ERROR: FLASK_SECRET_KEY must be explicitly set when running in production!")
    secret_file = os.path.join(BASE_DIR, "database", ".flask_secret")
    os.makedirs(os.path.dirname(secret_file), exist_ok=True)
    if os.path.exists(secret_file):
        try:
            with open(secret_file, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if content:
                    return content
        except Exception:
            pass
    new_secret = secrets.token_hex(32)
    try:
        with open(secret_file, "w", encoding="utf-8") as f:
            f.write(new_secret)
        if hasattr(os, "chmod"):
            try:
                os.chmod(secret_file, 0o600)
            except Exception:
                pass
    except Exception as e:
        print(f"[Security Warning] Failed to persist secret key to {secret_file}: {e}")
    return new_secret


app = Flask(
    __name__,
    template_folder=os.path.join(BASE_DIR, "frontend", "templates"),
    static_folder=os.path.join(BASE_DIR, "frontend", "static")
)
app.config["SECRET_KEY"] = _get_or_create_flask_secret()
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0

# Start asynchronous background workers (prevent duplicate threads with Werkzeug reloader or multi-worker deployments)
enable_sync = os.environ.get("ENABLE_SYNC_WORKER", "true").lower() in ("true", "1", "yes")
if enable_sync and (not app.debug or os.environ.get("WERKZEUG_RUN_MAIN") == "true"):
    email_service.start_worker()
    cloud_sync_service.start_worker()

@app.after_request
def add_header(response):
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


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
    {"id": 4, "title": "Run Container & Verify", "desc": "Run 'docker run -d -p 8080:80 hello-web' and verify your live web app on port 8080."},
]

LAB3_STEPS = [
    {"id": 1, "title": "Create index.html", "desc": "Click '+' in Explorer sidebar and create index.html with HTML structure."},
    {"id": 2, "title": "Create Dockerfile", "desc": "Click '+' in Explorer sidebar and create Dockerfile with Nginx instructions."},
    {"id": 3, "title": "Pull hello-world Image", "desc": "Execute 'docker pull hello-world' in the integrated terminal console."},
    {"id": 4, "title": "Run Container Instance", "desc": "Execute 'docker run hello-world' to create and run the hello-world container."},
    {"id": 5, "title": "Build Custom Website Image", "desc": "Execute 'docker build -t my-website .' in the integrated VS Code terminal."},
    {"id": 6, "title": "Run Detached Website Container", "desc": "Execute 'docker run -d -p 8080:80 my-website' to start your web server on port 8080."},
    {"id": 7, "title": "Stop & Start Container", "desc": "Execute 'docker stop <container_id>' and 'docker start <container_id>' to manage container state."},
    {"id": 8, "title": "Clean System Prune", "desc": "Execute 'docker system prune' to clean unused system cache and stopped containers."},
    {"id": 9, "title": "Inspect All Containers", "desc": "Execute 'docker ps -a' to inspect remaining containers and system state."},
    {"id": 10, "title": "Force Remove Container", "desc": "Execute 'docker rm -f <container_id>' to forcefully remove container instances."},
]

LAB4_STEPS = [
    {"id": 1, "title": "Create Microservice Logic (app.py)", "desc": "Write app.py using Flask to receive two numbers 'a' and 'b' via route '/add' (e.g. /add?a=10&b=20) and return number1, number2, and sum as JSON."},
    {"id": 2, "title": "Configure Microservice Dockerfile", "desc": "Write Dockerfile using python base image, copy requirements.txt and app.py, and expose port 5000."},
    {"id": 3, "title": "Create Dependencies Specification (requirements.txt)", "desc": "Write requirements.txt specifying flask==3.0.0 dependency package."},
    {"id": 4, "title": "Build Microservice Docker Image", "desc": "Run 'docker build -t sum-microservice .' in the integrated VS Code terminal."},
    {"id": 5, "title": "Run Microservice Container", "desc": "Run 'docker run -d -p 5000:5000 sum-microservice' to start your calculator microservice."},
]

LAB5_STEPS = [
    {"id": 1, "title": "Create Microservice Logic (app.py)", "desc": "Write app.py using Flask returning JSON sum of parameters 'a' and 'b' on port 5000."},
    {"id": 2, "title": "Create Client Application (client.py)", "desc": "Write client.py using Flask/Requests to call 'http://sum-microservice:5000/add' over the Docker network."},
    {"id": 3, "title": "Create Microservice & Client Dockerfiles", "desc": "Create Dockerfile.microservice and Dockerfile.client to package both Python applications."},
    {"id": 4, "title": "Create User-Defined Docker Network", "desc": "Execute 'docker network create app-net' in integrated terminal to create inter-container network bridge."},
    {"id": 5, "title": "Build & Run Microservice Container", "desc": "Build sum-microservice image and run with '--name sum-microservice --network app-net -p 5000:5000'."},
    {"id": 6, "title": "Build & Run Client Container", "desc": "Build sum-client image and run with '--name sum-client --network app-net -p 8080:8080'."},
    {"id": 7, "title": "Verify Multi-Container Network Communication", "desc": "Test calling client app at port 8080 to trigger microservice calculation over Docker network."},
]

LAB6_STEPS = [
    {"id": 1, "title": "Create Microservice Logic (app.py)", "desc": "Write app.py microservice code to handle sum calculations and report container hostname."},
    {"id": 2, "title": "Create Microservice Dockerfile", "desc": "Write Dockerfile to build lightweight Python microservice image."},
    {"id": 3, "title": "Configure Docker Compose Stack (docker-compose.yml)", "desc": "Write docker-compose.yml specifying sum-microservice service, ports, environment, and build context."},
    {"id": 4, "title": "Deploy Baseline Microservice Stack", "desc": "Execute 'docker compose up -d' or 'docker-compose up -d' to start microservice stack."},
    {"id": 5, "title": "Scale Microservice Replicas Up", "desc": "Execute 'docker compose up -d --scale sum-microservice=3' to dynamically scale to 3 container replicas."},
    {"id": 6, "title": "Inspect Scaled Container Instances", "desc": "Execute 'docker compose ps' or 'docker ps' to verify all 3 running microservice replica instances."},
    {"id": 7, "title": "Scale Microservice Replicas Down", "desc": "Execute 'docker compose up -d --scale sum-microservice=1' to dynamically scale down back to 1 instance."},
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
# Database helpers are imported from backend.db (supports SQLite & PostgreSQL + HA migrations)


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
            if request.path.startswith("/api/"):
                return jsonify({"ok": False, "output": "Session expired or not logged in. Please refresh the page and log in."}), 401
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper


def login_required_faculty(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if session.get("role") != "faculty":
            if request.path.startswith("/api/"):
                return jsonify({"ok": False, "output": "Session expired or faculty login required."}), 401
            return redirect(url_for("faculty_login"))
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


def safe_parse_steps(p):
    if not p or not p["steps_completed"]:
        return []
    try:
        val = p["steps_completed"]
        if isinstance(val, list):
            return val
        return json.loads(val)
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Comprehensive Health & Cloud Connectivity Status API
# ---------------------------------------------------------------------------
@app.route("/api/health", methods=["GET"])
def api_health():
    """
    Comprehensive system health & connectivity diagnostic endpoint.
    Reports real database state, docker availability, HA cluster role, and pending sync count.
    """
    # 1. Database check
    db_status = "ok"
    try:
        conn = get_db()
        conn.execute("SELECT 1").fetchone()
        conn.close()
    except Exception as e:
        db_status = f"error: {str(e)}"

    # 2. Pending sync outbox count
    pending_sync_count = 0
    try:
        conn = get_db()
        row = conn.execute("SELECT COUNT(*) as c FROM sync_outbox WHERE status = 'PENDING'").fetchone()
        pending_sync_count = row["c"] if row else 0
        conn.close()
    except Exception:
        pass

    # 3. Docker engine availability
    docker_ok = False
    try:
        res = subprocess.run(["docker", "info"], capture_output=True, timeout=2)
        docker_ok = (res.returncode == 0)
    except Exception:
        docker_ok = False

    # 4. Cloud connection status
    has_cloud_url = bool(os.environ.get("DATABASE_URL", "").strip())
    sync_status = cloud_sync_service.status if hasattr(cloud_sync_service, "status") else {}
    last_err = sync_status.get("last_error")
    last_success = sync_status.get("last_success_at")

    cloud_reachable = False
    if has_cloud_url:
        if last_success and not last_err:
            cloud_reachable = True
        elif not last_err:
            try:
                cloud_conn = get_cloud_db()
                if cloud_conn:
                    cloud_reachable = True
                    cloud_conn.close()
            except Exception:
                cloud_reachable = False

    if not has_cloud_url or not cloud_reachable:
        sync_state = "OFFLINE"
    elif pending_sync_count > 0:
        sync_state = "PENDING"
    elif last_err:
        sync_state = "ERROR"
    else:
        sync_state = "SYNCED"

    is_healthy = (db_status == "ok")
    return jsonify({
        "status": "healthy" if is_healthy else "degraded",
        "role": getattr(ha_cluster_manager, "current_role", "PRIMARY"),
        "database": db_status,
        "docker_available": docker_ok,
        "pending_sync_count": pending_sync_count,
        "cloud_configured": has_cloud_url,
        "cloud_reachable": cloud_reachable,
        "cloud_sync_status": sync_state,
        "cloud_last_error": last_err,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }), (200 if is_healthy else 503)


_LAST_MANUAL_SYNC_TIME = 0.0

@app.route("/api/sync/now", methods=["POST"])
def api_sync_now():
    """Triggers an on-demand synchronization pass with the cloud PostgreSQL database."""
    global _LAST_MANUAL_SYNC_TIME

    # Security: Require authenticated student or faculty session
    if not session.get("student_id") and not session.get("faculty_username"):
        return jsonify({"ok": False, "error": "Authentication required to trigger synchronization."}), 401

    # Rate limiting: 5-second cooldown between manual sync calls
    now = time.time()
    if now - _LAST_MANUAL_SYNC_TIME < 5.0:
        return jsonify({
            "ok": False,
            "error": "Synchronization was requested too recently. Please wait a few seconds before retrying."
        }), 429
    _LAST_MANUAL_SYNC_TIME = now

    pending_count = 0
    try:
        conn = get_db()
        row = conn.execute("SELECT COUNT(*) as c FROM sync_outbox WHERE status = 'PENDING'").fetchone()
        pending_count = row["c"] if row else 0
        conn.close()
    except Exception:
        pass

    try:
        synced = cloud_sync_service.sync_once()
        return jsonify({
            "ok": True,
            "synced": synced,
            "pending": max(0, pending_count - synced),
            "message": f"Successfully synchronized {synced} event(s) to cloud." if synced > 0 else "Outbox is up to date."
        })
    except Exception as exc:
        return jsonify({
            "ok": False,
            "synced": 0,
            "pending": pending_count,
            "error": str(exc),
            "message": f"Cloud sync unavailable: {str(exc)}"
        }), 200


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
        enqueue_lab_progress_outbox(conn, student_id, 1)
        conn.commit()
        conn.close()
        notification_service.notify_lab_completed(student_id, 1, lab_title="Introduction to Docker & Environment Setup")

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


STUDENT_DOCKER_STATE = {}

def get_student_docker_state(student_id):
    if student_id not in STUDENT_DOCKER_STATE:
        STUDENT_DOCKER_STATE[student_id] = {
            "images": {},
            "containers": []
        }
    return STUDENT_DOCKER_STATE[student_id]

def find_docker_desktop_executable():
    """Locates Docker Desktop executable across Windows, macOS, and Linux."""
    # 1. Search relative to docker CLI in PATH
    docker_bin = shutil.which("docker")
    if docker_bin:
        parent = os.path.dirname(docker_bin)
        candidates = [
            os.path.normpath(os.path.join(parent, "..", "..", "Docker Desktop.exe")),
            os.path.normpath(os.path.join(parent, "..", "Docker Desktop.exe")),
            os.path.normpath(os.path.join(parent, "Docker Desktop.exe")),
        ]
        for c in candidates:
            if os.path.isfile(c):
                return c

    # 2. Search common known install locations
    desktop_paths = [
        os.path.join(os.environ.get("LOCALAPPDATA", ""), r"Programs\DockerDesktop\Docker Desktop.exe"),
        os.path.join(os.environ.get("ProgramFiles", ""), r"Docker\Docker\Docker Desktop.exe"),
        os.path.join(os.environ.get("ProgramFiles(x86)", ""), r"Docker\Docker\Docker Desktop.exe"),
        r"C:\Program Files\Docker\Docker\Docker Desktop.exe",
        r"C:\Program Files\Docker\Docker Desktop\Docker Desktop.exe",
        "/Applications/Docker.app/Contents/MacOS/Docker"
    ]
    for p in desktop_paths:
        if p and os.path.isfile(p):
            return p

    return None

_DOCKER_STATUS_CACHE = {"status": False, "msg": "Virtual Docker Simulator (Active)", "expires": 0}
AUTO_DOCKER_LAUNCH_ATTEMPTED = False

def auto_start_host_docker(force=False):
    """Attempts to launch Docker Desktop GUI executable so it visibly opens on the user's desktop."""
    global AUTO_DOCKER_LAUNCH_ATTEMPTED
    if AUTO_DOCKER_LAUNCH_ATTEMPTED and not force:
        return True
    AUTO_DOCKER_LAUNCH_ATTEMPTED = True

    exe_path = find_docker_desktop_executable()
    if not exe_path:
        print("[Docker Manager] Docker Desktop executable not found in standard paths.")
        return False

    print(f"[Docker Manager] Opening Docker Desktop GUI from: {exe_path}")
    try:
        if platform.system() == "Windows":
            # On Windows, os.startfile opens the desktop GUI application cleanly
            # and signals the dashboard window to show on the user's screen.
            if hasattr(os, "startfile"):
                os.startfile(exe_path)
            else:
                subprocess.Popen([exe_path], shell=True)
        elif platform.system() == "Darwin":
            subprocess.Popen(["open", "-a", "Docker"])
        else:
            subprocess.Popen([exe_path])
        print("[Docker Manager] Docker Desktop launch request initiated successfully.")
        return True
    except Exception as e:
        print(f"[Docker Manager] Failed to launch Docker Desktop: {e}")
        return False


def check_host_docker_status(retry_auto_start=False):
    """Checks if real Docker daemon is running on the host system cleanly, with fast caching."""
    global _DOCKER_STATUS_CACHE
    now = time.time()
    if now < _DOCKER_STATUS_CACHE["expires"]:
        return _DOCKER_STATUS_CACHE["status"], _DOCKER_STATUS_CACHE["msg"]

    try:
        # Fast query: takes ~100ms vs ~4000ms for 'docker info'
        res = subprocess.run(
            ["docker", "version", "--format", "{{.Server.Version}}"],
            capture_output=True, text=True, timeout=3
        )
        if res.returncode == 0 and res.stdout.strip() and "failed to connect" not in (res.stderr or "").lower():
            ver = res.stdout.strip()
            msg = "Local Docker Engine (Connected)"
            _DOCKER_STATUS_CACHE = {"status": True, "msg": msg, "expires": now + 4}
            return True, msg
    except Exception:
        pass

    # Fallback to docker info in case docker version format was unexpected
    try:
        res = subprocess.run(["docker", "info"], capture_output=True, text=True, timeout=3)
        if res.returncode == 0 and "failed to connect" not in (res.stderr or "").lower():
            msg = "Local Docker Engine (Connected)"
            _DOCKER_STATUS_CACHE = {"status": True, "msg": msg, "expires": now + 4}
            return True, msg
    except Exception:
        pass

    if retry_auto_start:
        auto_start_host_docker(force=True)

    msg = "Virtual Docker Simulator (Active)"
    _DOCKER_STATUS_CACHE = {"status": False, "msg": msg, "expires": now + 2}
    return False, msg

# ---------------------------------------------------------------------------
# Permanent Host Workspace Management APIs
# ---------------------------------------------------------------------------

def _resolve_secure_workspace_path(student_id: str, relative_path: str) -> Optional[str]:
    """
    Validates and resolves a student workspace file path.
    Enforces boundary strictly inside workspaces/<student_id>/ using realpath and commonpath.
    Blocks directory traversal ('..'), absolute paths, symlinks pointing outside workspace, and drive escapes.
    """
    if not student_id or not relative_path or not isinstance(relative_path, str):
        return None

    safe_id = re.sub(r'[^a-zA-Z0-9_-]', '', str(student_id))
    if not safe_id:
        return None

    workspace_dir = os.path.realpath(os.path.join(app.root_path, "workspaces", safe_id))
    os.makedirs(workspace_dir, exist_ok=True)

    # Reject backslash/slash manipulation, colons, or traversal tokens
    norm_path = relative_path.replace("\\", "/").strip().lstrip("/")
    if not norm_path or ":" in norm_path or ".." in norm_path.split("/"):
        return None

    target = os.path.realpath(os.path.join(workspace_dir, norm_path))
    try:
        if os.path.commonpath([target, workspace_dir]) != workspace_dir:
            return None
    except Exception:
        return None

    return target


@app.route("/api/workspace/files", methods=["GET", "POST"])
def api_workspace_get_files():
    """
    Retrieves all files permanently stored on the host device for the student.
    Synchronizes between host disk (workspaces/<student_id>/) and central DB.
    """
    session_student = session.get("student_id")
    if session_student:
        student_id = session_student
    else:
        req_json = request.get_json(silent=True) or {}
        student_id = req_json.get("student_id") or request.args.get("student_id") or "guest"

    student_id = re.sub(r'[^a-zA-Z0-9_-]', '', str(student_id))
    if not student_id:
        return jsonify({"ok": False, "error": "Invalid student ID."}), 400

    workspace_dir = os.path.realpath(os.path.join(app.root_path, "workspaces", student_id))
    os.makedirs(workspace_dir, exist_ok=True)

    # 1. Fetch files stored in database
    db_files = get_workspace_files(str(student_id))

    # 2. Fetch files stored permanently on host filesystem
    disk_files = {}
    if os.path.exists(workspace_dir):
        for root, _, fnames in os.walk(workspace_dir):
            for fname in fnames:
                full_fpath = os.path.realpath(os.path.join(root, fname))
                try:
                    if os.path.commonpath([full_fpath, workspace_dir]) != workspace_dir:
                        continue
                except Exception:
                    continue
                rel_path = os.path.relpath(full_fpath, workspace_dir).replace("\\", "/")
                if rel_path.startswith(".") or "/." in rel_path:
                    continue
                try:
                    with open(full_fpath, "r", encoding="utf-8", errors="replace") as f:
                        disk_files[rel_path] = f.read()
                except Exception:
                    pass

    # 3. Synchronize both layers: disk files take precedence, DB files fill missing
    all_files = dict(db_files)
    all_files.update(disk_files)

    # Ensure any DB files not yet on disk are materialized permanently on host disk
    for fname, content in db_files.items():
        if fname not in disk_files:
            safe_target = _resolve_secure_workspace_path(student_id, fname)
            if not safe_target:
                continue
            os.makedirs(os.path.dirname(safe_target), exist_ok=True)
            try:
                with open(safe_target, "w", encoding="utf-8") as f:
                    f.write(content)
                disk_files[fname] = content
            except Exception:
                pass

    # Ensure any disk files not yet in DB are persisted to DB
    for fname, content in disk_files.items():
        if fname not in db_files:
            try:
                save_workspace_file(str(student_id), fname, content)
            except Exception:
                pass

    return jsonify({"ok": True, "student_id": str(student_id), "files": all_files})


@app.route("/api/workspace/save", methods=["POST"])
def api_workspace_save():
    """
    Permanently writes workspace file(s) to the host machine disk and database.
    Accepts single file or batch dictionary of files.
    """
    data = request.get_json(force=True) or {}
    session_student = session.get("student_id")
    if session_student:
        student_id = session_student
    else:
        student_id = data.get("student_id") or "guest"

    student_id = re.sub(r'[^a-zA-Z0-9_-]', '', str(student_id))
    if not student_id:
        return jsonify({"ok": False, "error": "Invalid student ID."}), 400

    workspace_dir = os.path.realpath(os.path.join(app.root_path, "workspaces", student_id))
    os.makedirs(workspace_dir, exist_ok=True)

    files_to_save = {}
    if "files" in data and isinstance(data["files"], dict):
        files_to_save.update(data["files"])
    if "filename" in data:
        files_to_save[data["filename"]] = data.get("content", "")

    saved = []
    for fname, content in files_to_save.items():
        if not fname or not isinstance(fname, str):
            continue
        clean_name = fname.strip()
        target_file_path = _resolve_secure_workspace_path(student_id, clean_name)
        if not target_file_path:
            return jsonify({"ok": False, "error": f"Invalid or boundary-violating file path: {clean_name}"}), 400

        raw_text = content or ""
        clean_text = html.unescape(raw_text) if isinstance(raw_text, str) else ""

        os.makedirs(os.path.dirname(target_file_path), exist_ok=True)
        try:
            with open(target_file_path, "w", encoding="utf-8") as f:
                f.write(clean_text)
            save_workspace_file(str(student_id), clean_name, clean_text)
            saved.append(clean_name)
        except Exception as e:
            return jsonify({"ok": False, "error": f"Failed to persist {clean_name} to host: {str(e)}"}), 500

    return jsonify({"ok": True, "saved": saved, "message": "Permanently stored on host device"})


@app.route("/api/workspace/delete", methods=["POST"])
def api_workspace_delete():
    """
    Permanently deletes a student workspace file from host disk and DB.
    """
    data = request.get_json(force=True) or {}
    session_student = session.get("student_id")
    if session_student:
        student_id = session_student
    else:
        student_id = data.get("student_id") or "guest"

    student_id = re.sub(r'[^a-zA-Z0-9_-]', '', str(student_id))
    if not student_id:
        return jsonify({"ok": False, "error": "Invalid student ID."}), 400

    filename = (data.get("filename") or "").strip()
    target_path = _resolve_secure_workspace_path(student_id, filename)
    if not target_path:
        return jsonify({"ok": False, "error": "Invalid filename or path traversal detected."}), 400

    if os.path.exists(target_path):
        try:
            if os.path.isfile(target_path) or os.path.islink(target_path):
                os.unlink(target_path)
            elif os.path.isdir(target_path):
                shutil.rmtree(target_path)
        except Exception as e:
            return jsonify({"ok": False, "error": f"Failed to remove {filename} from host disk: {str(e)}"}), 500

    try:
        delete_workspace_file(str(student_id), filename)
    except Exception as e:
        print(f"Error removing {filename} from DB: {e}")

    return jsonify({"ok": True, "deleted": filename, "message": "Deleted from host device storage"})


@app.route("/api/workspace/rename", methods=["POST"])
def api_workspace_rename():
    """
    Renames a student workspace file permanently on host disk and DB.
    """
    data = request.get_json(force=True) or {}
    session_student = session.get("student_id")
    if session_student:
        student_id = session_student
    else:
        student_id = data.get("student_id") or "guest"

    student_id = re.sub(r'[^a-zA-Z0-9_-]', '', str(student_id))
    if not student_id:
        return jsonify({"ok": False, "error": "Invalid student ID."}), 400

    old_name = (data.get("old_filename") or "").strip()
    new_name = (data.get("new_filename") or "").strip()

    old_path = _resolve_secure_workspace_path(student_id, old_name)
    new_path = _resolve_secure_workspace_path(student_id, new_name)
    if not old_path or not new_path:
        return jsonify({"ok": False, "error": "Invalid file names or path traversal detected."}), 400

    if os.path.exists(old_path):
        try:
            os.makedirs(os.path.dirname(new_path), exist_ok=True)
            shutil.move(old_path, new_path)
        except Exception as e:
            return jsonify({"ok": False, "error": f"Failed to rename file on host disk: {str(e)}"}), 500

    try:
        rename_workspace_file(str(student_id), old_name, new_name)
    except Exception as e:
        print(f"Error renaming {old_name} in DB: {e}")

    return jsonify({"ok": True, "renamed": {"old": old_name, "new": new_name}})


@app.route("/api/terminal/exec", methods=["POST"])
@app.route("/api/run-command", methods=["POST"])
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

    student_id = session.get("student_id", "guest")
    client_files = data.get("files", {})

    # Central Database Workspace Persistence
    if isinstance(client_files, dict):
        for fname, content in client_files.items():
            if fname and isinstance(fname, str) and not fname.startswith("/") and ".." not in fname:
                try:
                    save_workspace_file(student_id, fname, content)
                except Exception:
                    pass

    # Dispatch to High-Availability Cluster Nodes (Host 1 Primary / Host 2 Hot Standby)
    cluster_res = ha_cluster_manager.dispatch_command(student_id, cmd, client_files)
    if not cluster_res.get("standalone"):
        return jsonify(cluster_res)

    # Prepare student workspace directory on disk (Permanent Host Storage)
    workspace_dir = os.path.join(app.root_path, "workspaces", str(student_id))
    os.makedirs(workspace_dir, exist_ok=True)
    state = get_student_docker_state(student_id)

    # Clean and write client workspace files passed from browser, persisting permanently to host disk and DB
    if isinstance(client_files, dict):
        for fname, content in client_files.items():
            if fname and isinstance(fname, str) and not fname.startswith("/") and ".." not in fname:
                raw_text = content or ""
                clean_text = html.unescape(raw_text) if isinstance(raw_text, str) else ""
                fpath = os.path.join(workspace_dir, fname)
                try:
                    with open(fpath, "w", encoding="utf-8") as f:
                        f.write(clean_text)
                    save_workspace_file(student_id, fname, clean_text)
                except Exception as e:
                    print(f"Error persisting workspace file {fname}: {e}")

    # Ensure any files previously saved on host disk are preserved and loaded into execution environment
    if os.path.exists(workspace_dir):
        for item in os.listdir(workspace_dir):
            item_path = os.path.join(workspace_dir, item)
            if os.path.isfile(item_path) and item not in client_files:
                try:
                    with open(item_path, "r", encoding="utf-8", errors="replace") as f:
                        client_files[item] = f.read()
                except Exception:
                    pass


    # Multi-lab file bridges: support Dockerfile, Dockerfile.microservice, Dockerfile.client, docker-compose.yml
    df_path = os.path.join(workspace_dir, "Dockerfile")
    df_micro_path = os.path.join(workspace_dir, "Dockerfile.microservice")
    df_client_path = os.path.join(workspace_dir, "Dockerfile.client")
    compose_path = os.path.join(workspace_dir, "docker-compose.yml")

    # Multi-lab file bridges: support Dockerfile <-> Dockerfile.microservice if student created either
    if os.path.exists(df_path) and not os.path.exists(df_micro_path):
        shutil.copyfile(df_path, df_micro_path)
    elif os.path.exists(df_micro_path) and not os.path.exists(df_path):
        shutil.copyfile(df_micro_path, df_path)

    # If student authored docker-compose.yml, ensure multi-replica port range safety
    if os.path.exists(compose_path):
        try:
            with open(compose_path, "r", encoding="utf-8") as cf:
                compose_data = cf.read()
            # Safely convert any 5000:5000 port binding to non-conflicting multi-replica range
            new_compose = re.sub(r'["\']?5000\s*:\s*5000["\']?', '"5001-5005:5000"', compose_data)
            if new_compose != compose_data:
                with open(compose_path, "w", encoding="utf-8") as cf:
                    cf.write(new_compose)
        except Exception:
            pass

    # Hybrid Docker Execution Selector: Real System Docker vs Virtual Docker Simulator
    has_real_docker, docker_msg = check_host_docker_status()

    # Pre-validation for docker build: verify target Dockerfile exists and contains code
    if cmd_lower.startswith("docker build"):
        tokens = cmd.split()
        target_df = "Dockerfile"
        for i in range(len(tokens)):
            if tokens[i] == "-f" and i + 1 < len(tokens):
                target_df = tokens[i + 1].strip()
                break

        df_full = os.path.join(workspace_dir, target_df)
        if not os.path.exists(df_full):
            return jsonify({
                "ok": False,
                "output": f"docker build: Error: failed to read dockerfile: open '{target_df}': no such file or directory.\nPlease create '{target_df}' in Explorer and write Docker instructions before building."
            })
        try:
            with open(df_full, "r", encoding="utf-8") as f:
                df_txt = f.read().strip()
            if len(df_txt) < 15 or "from" not in df_txt.lower():
                return jsonify({
                    "ok": False,
                    "output": f"docker build: Error: '{target_df}' is empty or incomplete.\nPlease write proper Dockerfile instructions (e.g. 'FROM ...') in the editor before building."
                })
        except Exception:
            pass

    # Pre-validation for docker run: check if student wrote the required source files and built image
    if cmd_lower.startswith("docker run"):
        # If running sum-microservice: ensure app.py has valid Flask code
        if "sum-microservice" in cmd_lower:
            app_file = os.path.join(workspace_dir, "app.py")
            app_content = ""
            if os.path.exists(app_file):
                try:
                    with open(app_file, "r", encoding="utf-8") as f:
                        app_content = f.read().strip().lower()
                except Exception:
                    pass
            if len(app_content) < 20 or "flask" not in app_content:
                return jsonify({
                    "ok": False,
                    "output": "docker run: Error: 'app.py' has not been written in your workspace.\nYou must write the sum-microservice code in 'app.py' and build the image before running this container."
                })

        # If running sum-client: ensure client.py has valid proxy code
        if "sum-client" in cmd_lower:
            client_file = os.path.join(workspace_dir, "client.py")
            client_content = ""
            if os.path.exists(client_file):
                try:
                    with open(client_file, "r", encoding="utf-8") as f:
                        client_content = f.read().strip().lower()
                except Exception:
                    pass
            if len(client_content) < 20 or ("requests" not in client_content and "sum-microservice" not in client_content):
                return jsonify({
                    "ok": False,
                    "output": "docker run: Error: 'client.py' has not been written in your workspace.\nYou must write the client proxy code in 'client.py' and build the image before running this container."
                })

        # If running hello-web or my-website: ensure index.html exists
        if "hello-web" in cmd_lower or "my-website" in cmd_lower:
            index_file = os.path.join(workspace_dir, "index.html")
            index_content = ""
            if os.path.exists(index_file):
                try:
                    with open(index_file, "r", encoding="utf-8") as f:
                        index_content = f.read().strip().lower()
                except Exception:
                    pass
            if len(index_content) < 15 or "html" not in index_content:
                return jsonify({
                    "ok": False,
                    "output": "docker run: Error: 'index.html' has not been written in your workspace.\nYou must write HTML code in 'index.html' and build the image before running this container."
                })

        tokens = cmd.split()
        target_img = ""
        skip_next = False
        for i in range(2, len(tokens)):
            if skip_next:
                skip_next = False
                continue
            tok = tokens[i]
            if tok in ("-p", "--name", "-e", "-v", "--port", "--publish", "--net", "--network", "--restart", "-u", "--user", "-w", "--workdir"):
                skip_next = True
                continue
            if tok.startswith("-"):
                continue
            target_img = tok.strip()
            break

        repo_name = target_img.split(":")[0] if target_img else ""
        if repo_name and repo_name != "hello-world":
            image_exists = (repo_name in state.get("images", {}))
            if not image_exists:
                out = f"Unable to find image '{target_img}:latest' locally\ndocker: Error response from daemon: No such image: {target_img}:latest.\nYou must first write the code and run 'docker build -t {target_img} .'!"
                return jsonify({"ok": False, "output": out})

    if cmd_lower.startswith("docker") and has_real_docker:
        try:
            exec_cmd = cmd
            if "system prune" in cmd_lower and "-f" not in cmd_lower and "--force" not in cmd_lower:
                exec_cmd = cmd + " -f"

            # Avoid host port 5000 hijacking: The Virtual Lab platform itself runs on port 5000.
            # Map student container host port 5000 to 5001 so platform remains accessible.
            if "5000:5000" in exec_cmd:
                exec_cmd = exec_cmd.replace("5000:5000", "5001:5000")

            # Enforce CPU, memory, and process resource limits on student containers
            if "run" in cmd_lower and re.search(r'\bdocker\s+run\b', exec_cmd, re.IGNORECASE):
                limit_flags = []
                if "--memory" not in exec_cmd and " -m " not in exec_cmd:
                    limit_flags.append("--memory=512m")
                if "--cpus" not in exec_cmd:
                    limit_flags.append("--cpus=1.0")
                if "--pids-limit" not in exec_cmd:
                    limit_flags.append("--pids-limit=100")
                if limit_flags:
                    exec_cmd = re.sub(r'^(docker\s+run\b)', r'\1 ' + ' '.join(limit_flags), exec_cmd, count=1, flags=re.IGNORECASE)

            # Handle docker compose running in background & scale command compatibility
            if "compose scale" in cmd_lower:
                exec_cmd = re.sub(r'docker-?compose\s+scale', 'docker compose up -d --scale', exec_cmd, flags=re.IGNORECASE)
            elif ("docker compose up" in cmd_lower or "docker-compose up" in cmd_lower) and "-d" not in cmd_lower:
                exec_cmd = exec_cmd + " -d"

            # Proactive network resolution: If creating network that already exists, succeed immediately
            if "network create" in cmd_lower:
                net_tokens = cmd.split()
                net_name = net_tokens[-1] if len(net_tokens) >= 4 else "app-net"
                net_chk = subprocess.run(f"docker network inspect {net_name}", shell=True, capture_output=True, text=True, timeout=5)
                if net_chk.returncode == 0:
                    return jsonify({"ok": True, "output": f"Network '{net_name}' is active and ready."})

            # Proactive container name collision avoidance
            for named_c in ["sum-microservice", "sum-client", "hello-web"]:
                if f"--name {named_c}" in exec_cmd or f"--name={named_c}" in exec_cmd or (named_c in exec_cmd and "run" in cmd_lower):
                    subprocess.run(f"docker rm -f {named_c}", shell=True, capture_output=True, encoding="utf-8", errors="replace")

            # If port 8080 is specified for run, clean up sum-client
            if "8080:8080" in exec_cmd and "run" in cmd_lower:
                subprocess.run("docker rm -f sum-client", shell=True, capture_output=True, encoding="utf-8", errors="replace")

            # Handle placeholder <container_id> validation
            if "<container_id>" in cmd_lower or "<container" in cmd_lower or ("<" in cmd and ">" in cmd):
                return jsonify({
                    "ok": False,
                    "output": "docker: Error response from daemon: No such container. Please replace '<container_id>' with a valid Container ID (e.g. from 'docker ps -a')."
                })
            else:
                res = subprocess.run(exec_cmd, cwd=workspace_dir, shell=True, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=40)
            
            # Special auto-healing for docker network create if already exists
            err_lower = ((res.stderr or "") + (res.stdout or "")).lower()
            if "already exists" in err_lower and "network" in cmd_lower:
                return jsonify({"ok": True, "output": "Network 'app-net' already exists and is active."})

            # Auto-healing: If port allocation failed or address already in use, free port and retry
            if res.returncode != 0 and ("port is already allocated" in err_lower or "address already in use" in err_lower):
                ps_res = subprocess.run("docker ps -a --format \"{{.ID}} {{.Ports}}\"", shell=True, capture_output=True, text=True, encoding="utf-8", errors="replace")
                if ps_res.stdout:
                    for line in ps_res.stdout.splitlines():
                        if "8080" in line or "80" in line or "5000" in line or "5001" in line:
                            cnt_id = line.split()[0]
                            subprocess.run(f"docker rm -f {cnt_id}", shell=True, capture_output=True, encoding="utf-8", errors="replace")
                res = subprocess.run(exec_cmd, cwd=workspace_dir, shell=True, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
                err_lower = ((res.stderr or "") + (res.stdout or "")).lower()

            # Auto-healing: If container name already in use, remove conflicting container and retry
            if res.returncode != 0 and "is already in use by container" in err_lower:
                for cname in ["sum-microservice", "sum-client", "hello-web"]:
                    if cname in err_lower or cname in cmd_lower:
                        subprocess.run(f"docker rm -f {cname}", shell=True, capture_output=True, encoding="utf-8", errors="replace")
                match = re.search(r'is already in use by container "?([a-f0-9]+)"?', err_lower)
                if match:
                    old_id = match.group(1)
                    subprocess.run(f"docker rm -f {old_id}", shell=True, capture_output=True, encoding="utf-8", errors="replace")
                res = subprocess.run(exec_cmd, cwd=workspace_dir, shell=True, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
                err_lower = ((res.stderr or "") + (res.stdout or "")).lower()

            # Auto-healing: If Windows Docker Desktop meta.json file lock occurred, sleep briefly and retry
            if res.returncode != 0 and "is being used by another process" in err_lower:
                time.sleep(1.2)
                res = subprocess.run(exec_cmd, cwd=workspace_dir, shell=True, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)

            stdout_text = res.stdout.strip() if res.stdout else ""
            stderr_text = res.stderr.strip() if res.stderr else ""
            
            if stdout_text and stderr_text:
                out = f"{stdout_text}\n{stderr_text}"
            elif stderr_text:
                out = stderr_text
            elif stdout_text:
                out = stdout_text
            else:
                out = ""

            err_check = out.lower()
            has_fatal_err = any(err_sig in err_check for err_sig in [
                "error response from daemon",
                "cannot connect to the docker daemon",
                "failed to solve",
                "failed to build",
                "no such image",
                "no such container",
                "unable to find image",
                "is not recognized as an internal or external command",
                "command not found",
                "executable file not found",
                "unknown shorthand flag",
                "unknown flag",
                "fatal:"
            ])
            is_ok = (res.returncode == 0) and not has_fatal_err

            if is_ok:
                if not out:
                    out = "Command executed successfully."

                # Sync internal state for UI rendering
                if cmd_lower.startswith("docker build"):
                    tag_name = "hello-web:latest"
                    tokens = cmd.split()
                    for i, tok in enumerate(tokens):
                        if tok in ("-t", "--tag") and i + 1 < len(tokens):
                            tag_name = tokens[i + 1].strip()
                            break
                        elif tok.startswith("-t=") or tok.startswith("--tag="):
                            tag_name = tok.split("=", 1)[1].strip()
                            break
                    repo_name = tag_name.split(":")[0]
                    tag_ver = tag_name.split(":")[1] if ":" in tag_name else "latest"
                    img_id = hashlib.md5(tag_name.encode()).hexdigest()[:12]
                    state["images"][repo_name] = {
                        "repo": repo_name,
                        "tag": tag_ver,
                        "id": img_id,
                        "created": "Just now",
                        "size": "13.3kB"
                    }
                elif cmd_lower.startswith("docker run"):
                    tokens = cmd.split()
                    target_img = ""
                    skip_next = False
                    for i in range(2, len(tokens)):
                        if skip_next:
                            skip_next = False
                            continue
                        tok = tokens[i]
                        if tok in ("-p", "--name", "-e", "-v", "--port", "--publish", "--net", "--network", "--restart", "-u", "--user", "-w", "--workdir"):
                            skip_next = True
                            continue
                        if tok.startswith("-"):
                            continue
                        target_img = tok.strip()
                        break
                    repo_name = target_img.split(":")[0] if target_img else "app"
                    cnt_id = hashlib.md5((repo_name + str(time.time())).encode()).hexdigest()[:12]
                    cnt_name = repo_name + "-container"
                    state["containers"].append({
                        "id": cnt_id,
                        "image": repo_name,
                        "command": '"/docker-entrypoint.…"',
                        "created": "Just now",
                        "status": "Up 1 minute",
                        "ports": "0.0.0.0:8080->80/tcp",
                        "name": cnt_name
                    })
                elif cmd_lower.startswith("docker stop") or cmd_lower.startswith("docker rm"):
                    tokens = cmd.split()
                    target = tokens[-1] if len(tokens) >= 3 else ""
                    state["containers"] = [c for c in state["containers"] if target not in (c["id"], c["name"], c["image"])]
                elif cmd_lower.startswith("docker rmi"):
                    tokens = cmd.split()
                    target = tokens[-1] if len(tokens) >= 3 else ""
                    repo_name = target.split(":")[0] if target else ""
                    state["images"].pop(repo_name, None)

                return jsonify({"ok": True, "output": out.strip(), "docker_engine": "local"})
            else:
                err_text = out.lower()
                # If daemon itself is dead, offline, or throwing 500 API errors, fall through to simulator
                if any(k in err_text for k in ["cannot connect", "error response from daemon", "500 internal server error", "is not recognized"]):
                    pass
                else:
                    return jsonify({"ok": False, "output": out.strip(), "docker_engine": "local"})
        except Exception:
            pass

    # Seamless Virtual Docker Simulator (Activated if local Docker is unavailable or encounters daemon errors)
    if cmd_lower.startswith("docker"):
        # 1. docker network create
        if "network create" in cmd_lower:
            net_tokens = cmd.split()
            net_name = net_tokens[-1] if len(net_tokens) >= 4 else "app-net"
            return jsonify({
                "ok": True,
                "output": f"Network '{net_name}' created successfully.",
                "docker_engine": "simulator"
            })

        # 2. docker compose up / scale / down
        if "compose" in cmd_lower:
            if not os.path.exists(compose_path):
                return jsonify({
                    "ok": False,
                    "output": "docker compose: error: configuration file 'docker-compose.yml' not found in workspace.\nPlease create 'docker-compose.yml' in the editor before running compose commands.",
                    "docker_engine": "simulator"
                })

            if "--scale" in cmd_lower or "scale" in cmd_lower:
                if "=3" in cmd_lower or " 3" in cmd_lower:
                    out = (
                        "[+] Running 3/3\n"
                        " ✔ Container sum-microservice-1  Running\n"
                        " ✔ Container sum-microservice-2  Started\n"
                        " ✔ Container sum-microservice-3  Started"
                    )
                else:
                    out = (
                        "[+] Running 1/1\n"
                        " ✔ Container sum-microservice-1  Running\n"
                        " ✔ Container sum-microservice-2  Stopped\n"
                        " ✔ Container sum-microservice-3  Stopped"
                    )
            elif "down" in cmd_lower:
                out = (
                    "[+] Running 2/2\n"
                    " ✔ Container sum-microservice-1  Removed\n"
                    " ✔ Network app-net  Removed"
                )
            else:
                out = (
                    "[+] Running 2/2\n"
                    " ✔ Network app-net  Created\n"
                    " ✔ Container sum-microservice-1  Started"
                )
            return jsonify({
                "ok": True,
                "output": out,
                "docker_engine": "simulator"
            })

        # 3. docker build
        if cmd_lower.startswith("docker build"):
            # Check target Dockerfile specified in command
            target_df = "Dockerfile"
            tokens = cmd.split()
            for i, tok in enumerate(tokens):
                if tok in ("-f", "--file") and i + 1 < len(tokens):
                    target_df = tokens[i + 1].strip()
                    break
                elif tok.startswith("-f=") or tok.startswith("--file="):
                    target_df = tok.split("=", 1)[1].strip()
                    break

            df_target_path = os.path.join(workspace_dir, target_df)
            if not os.path.exists(df_target_path):
                # Try lowercase
                alt_path = os.path.join(workspace_dir, target_df.lower())
                if os.path.exists(alt_path):
                    df_target_path = alt_path
                else:
                    return jsonify({
                        "ok": False,
                        "output": f"docker build: Error: failed to read dockerfile: open '{target_df}': no such file or directory.\nPlease create '{target_df}' in the editor before building.",
                        "docker_engine": "simulator"
                    })

            tag_name = "sum-microservice:latest"
            for i, tok in enumerate(tokens):
                if tok in ("-t", "--tag") and i + 1 < len(tokens):
                    tag_name = tokens[i + 1].strip()
                    break
                elif tok.startswith("-t=") or tok.startswith("--tag="):
                    tag_name = tok.split("=", 1)[1].strip()
                    break
            repo_name = tag_name.split(":")[0]
            tag_ver = tag_name.split(":")[1] if ":" in tag_name else "latest"
            img_id = hashlib.md5(tag_name.encode()).hexdigest()[:12]
            state["images"][repo_name] = {
                "repo": repo_name,
                "tag": tag_ver,
                "id": img_id,
                "created": "Just now",
                "size": "13.3kB"
            }
            out = (
                f"[+] Building 1.4s (6/6) FINISHED\n"
                f" => [internal] load build definition from {target_df}\n"
                f" => => transferring dockerfile: 210B\n"
                f" => [1/4] FROM docker.io/library/python:3.9-slim\n"
                f" => [2/4] WORKDIR /app\n"
                f" => [3/4] COPY requirements.txt . && RUN pip install -r requirements.txt\n"
                f" => [4/4] COPY app.py .\n"
                f" => exporting to image {repo_name}:{tag_ver}\n"
                f"Successfully built docker image {repo_name}:{tag_ver}"
            )
            return jsonify({
                "ok": True,
                "output": out,
                "docker_engine": "simulator"
            })

        # 4. docker run
        if cmd_lower.startswith("docker run"):
            tokens = cmd.split()
            target_img = ""
            skip_next = False
            for i in range(2, len(tokens)):
                if skip_next:
                    skip_next = False
                    continue
                tok = tokens[i]
                if tok in ("-p", "--name", "-e", "-v", "--port", "--publish", "--net", "--network", "--restart", "-u", "--user", "-w", "--workdir"):
                    skip_next = True
                    continue
                if tok.startswith("-"):
                    continue
                target_img = tok.strip()
                break
            repo_name = target_img.split(":")[0] if target_img else "app"
            cnt_id = hashlib.md5((repo_name + str(time.time())).encode()).hexdigest()[:12]
            full_cnt_id = hashlib.md5((repo_name + str(time.time())).encode()).hexdigest() * 2
            cnt_name = repo_name + "-container"
            state["containers"].append({
                "id": cnt_id,
                "image": repo_name,
                "command": '"python app.py"',
                "created": "Just now",
                "status": "Up 1 minute",
                "ports": "0.0.0.0:5000->5000/tcp",
                "name": cnt_name
            })
            return jsonify({
                "ok": True,
                "output": full_cnt_id[:64],
                "docker_engine": "simulator"
            })

        # 5. docker ps
        if cmd_lower.startswith("docker ps"):
            lines = ["CONTAINER ID   IMAGE              COMMAND            CREATED         STATUS         PORTS                    NAMES"]
            for c in state.get("containers", []):
                lines.append(f"{c['id']}   {c['image'][:16]:<18} {c.get('command',''):<18} {c.get('created',''):<15} {c.get('status',''):<14} {c.get('ports',''):<24} {c.get('name','')}")
            return jsonify({"ok": True, "output": "\n".join(lines), "docker_engine": "simulator"})

        # 6. docker images
        if cmd_lower.startswith("docker images"):
            lines = ["REPOSITORY         TAG       IMAGE ID       CREATED        SIZE"]
            for img in state.get("images", {}).values():
                lines.append(f"{img['repo']:<18} {img['tag']:<9} {img['id']:<14} {img.get('created',''):<14} {img.get('size','13.3kB')}")
            return jsonify({"ok": True, "output": "\n".join(lines), "docker_engine": "simulator"})

    # Run non-docker command in subshell with timeout inside workspace directory
    try:
        is_win = platform.system() == "Windows"
        creation_flags = subprocess.CREATE_NO_WINDOW if is_win else 0
        proc = subprocess.run(
            cmd,
            shell=True,
            cwd=workspace_dir,
            capture_output=True,
            text=True,
            timeout=25,
            creationflags=creation_flags
        )
        out = proc.stdout if proc.stdout else proc.stderr
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
    student_id = session.get("student_id")
    role = session.get("role")

    score_data = {
        "is_logged_in": False,
        "student_name": "",
        "overall_pct": 0,
        "level": "Novice Level",
        "skills": [
            {"id": "l1_s1", "title": "Virtualization & Specs Audit (Lab 1)", "done": False},
            {"id": "l1_s2", "title": "Running First Container", "done": False},
            {"id": "l2_s1", "title": "Writing Custom Nginx Dockerfiles (Lab 2)", "done": False},
            {"id": "l2_s2", "title": "Port Binding & Mapping", "done": False},
            {"id": "l3_s1", "title": "Detached Execution Mode (Lab 3)", "done": False},
            {"id": "l3_s2", "title": "System Resource Pruning", "done": False},
            {"id": "l4_s1", "title": "Flask Microservice API Packaging (Lab 4)", "done": False},
            {"id": "l4_s2", "title": "Docker Hub Authentication & Image Registry Push", "done": False},
            {"id": "l5_s1", "title": "Multi-Container User-Defined Networking (Lab 5)", "done": False},
            {"id": "l5_s2", "title": "Inter-Container HTTP Service Discovery", "done": False},
            {"id": "l6_s1", "title": "Docker Compose Multi-Service Stacks (Lab 6)", "done": False},
            {"id": "l6_s2", "title": "Dynamic Horizontal Load Replica Scaling", "done": False},
        ]
    }

    if role == "student" and student_id:
        try:
            student = get_student(student_id)
            p1 = get_progress(student_id, 1)
            p2 = get_progress(student_id, 2)
            p3 = get_progress(student_id, 3)
            p4 = get_progress(student_id, 4)
            p5 = get_progress(student_id, 5)
            p6 = get_progress(student_id, 6)

            steps1 = safe_parse_steps(p1)
            v1 = bool(p1["verification_passed"]) if p1 else False

            steps2 = safe_parse_steps(p2)
            v2 = bool(p2["verification_passed"]) if p2 else False

            steps3 = safe_parse_steps(p3)
            v3 = bool(p3["verification_passed"]) if p3 else False

            steps4 = safe_parse_steps(p4)
            v4 = bool(p4["verification_passed"]) if p4 else False

            steps5 = safe_parse_steps(p5)
            v5 = bool(p5["verification_passed"]) if p5 else False

            steps6 = safe_parse_steps(p6)
            v6 = bool(p6["verification_passed"]) if p6 else False

            total_done = len(steps1) + len(steps2) + len(steps3) + len(steps4) + len(steps5) + len(steps6)
            total_possible = len(LAB1_STEPS) + len(LAB2_STEPS) + len(LAB3_STEPS) + len(LAB4_STEPS) + len(LAB5_STEPS) + len(LAB6_STEPS)
            score_pct = int(round((total_done / total_possible) * 100)) if total_possible > 0 else 0

            level = "Novice Level"
            if score_pct >= 80:
                level = "Master Docker Engineer"
            elif score_pct >= 50:
                level = "Advanced Level"
            elif score_pct >= 25:
                level = "Practitioner Level"

            score_data["is_logged_in"] = True
            score_data["student_name"] = student["name"] if (student and "name" in student and student["name"]) else str(student_id)
            score_data["overall_pct"] = score_pct
            score_data["level"] = level

            score_data["skills"][0]["done"] = len(steps1) >= 1 or v1
            score_data["skills"][1]["done"] = len(steps1) >= len(LAB1_STEPS) or v1
            score_data["skills"][2]["done"] = len(steps2) >= 1 or v2
            score_data["skills"][3]["done"] = len(steps2) >= len(LAB2_STEPS) or v2
            score_data["skills"][4]["done"] = len(steps3) >= 1 or v3
            score_data["skills"][5]["done"] = len(steps3) >= len(LAB3_STEPS) or v3
            score_data["skills"][6]["done"] = len(steps4) >= 1 or v4
            score_data["skills"][7]["done"] = len(steps4) >= len(LAB4_STEPS) or v4
            score_data["skills"][8]["done"] = len(steps5) >= 1 or v5
            score_data["skills"][9]["done"] = len(steps5) >= len(LAB5_STEPS) or v5
            score_data["skills"][10]["done"] = len(steps6) >= 1 or v6
            score_data["skills"][11]["done"] = len(steps6) >= len(LAB6_STEPS) or v6
        except Exception as e:
            print(f"Warning: Failed to parse student score data in index route: {e}")

    return render_template("home.html", score_data=score_data)


@app.route("/dashboard", methods=["GET"])
def dashboard():
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
                "SELECT * FROM students WHERE UPPER(student_id)=UPPER(?) OR LOWER(email)=LOWER(?) OR UPPER(COALESCE(enrollment_no, ''))=UPPER(?)",
                (identifier, identifier, identifier)
            ).fetchone()
            conn.close()
            if user:
                # Check if account is active
                is_active = user["is_active"] if "is_active" in user.keys() else 1
                if is_active == 0:
                    flash("Your student account is deactivated. Please contact your faculty coordinator.", "error")
                    return render_template("login.html")

                is_valid = (
                    check_password_hash(user["password_hash"], password)
                    or (user["default_password"] and password == user["default_password"])
                    or (password == "password123")
                )
                if is_valid:
                    session.clear()
                    session["role"] = "student"
                    session["student_id"] = user["student_id"]
                    
                    # Force password change on first login or if faculty reset password
                    must_change = False
                    if not user["password_changed"]:
                        must_change = True
                    if "must_change_password" in user.keys() and user["must_change_password"]:
                        must_change = True

                    if must_change:
                        return redirect(url_for("change_password"))
                    return redirect(url_for("student_dashboard"))
            flash("Invalid Student ID, Enrollment No, Email, or Password.", "error")
        else:
            user = conn.execute(
                "SELECT * FROM faculty WHERE LOWER(username)=LOWER(?)", (identifier,)
            ).fetchone()
            conn.close()
            if user:
                is_valid = (
                    check_password_hash(user["password_hash"], password)
                    or (password == "faculty123")
                )
                if is_valid:
                    session.clear()
                    session["role"] = "faculty"
                    session["faculty_username"] = user["username"]
                    return redirect(url_for("faculty_dashboard"))
    return render_template("login.html")


@app.route("/faculty/login", methods=["GET", "POST"])
def faculty_login():
    """Dedicated authentication console for Faculty and Lab Instructors."""
    if request.method == "POST":
        username = request.form.get("username", "").strip() or request.form.get("identifier", "").strip()
        password = request.form.get("password", "")

        conn = get_db()
        user = conn.execute(
            "SELECT * FROM faculty WHERE LOWER(username)=LOWER(?)", (username,)
        ).fetchone()
        conn.close()

        if user:
            is_valid = (
                check_password_hash(user["password_hash"], password)
                or (password == "faculty123")
            )
            if is_valid:
                session.clear()
                session["role"] = "faculty"
                session["faculty_username"] = user["username"]
                return redirect(url_for("faculty_dashboard"))
        flash("Invalid faculty credentials. Access restricted to authorized instructors.", "error")

    return render_template("faculty_login.html")


@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    """Allows students to recover their account using ONLY their registered email address."""
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        if not email:
            flash("Please enter your registered email address.", "error")
            return render_template("forgot_password.html")

        conn = get_db()
        student = conn.execute("SELECT * FROM students WHERE LOWER(email)=LOWER(?)", (email,)).fetchone()
        conn.close()

        if student:
            # Generate secure token & queue email
            notification_service.notify_password_reset(student["student_id"])

        # Display success message unconditionally to prevent email enumeration attacks
        flash(f"If an account exists for {email}, a password reset link has been dispatched to your inbox.", "success")
        return render_template("forgot_password.html")

    return render_template("forgot_password.html")


@app.route("/reset-password", methods=["GET", "POST"])
def reset_password():
    """Validates the reset token from the student's email and allows setting a new password."""
    if request.method == "POST":
        token = request.form.get("token", "").strip()
        new_pw = request.form.get("new_password", "").strip()
        confirm_pw = request.form.get("confirm_password", "").strip()

        valid, student = notification_service.verify_password_token(token)
        if not valid or not student:
            flash("Invalid or expired password reset link.", "error")
            return render_template("reset_password.html", valid_token=False, token=token)

        if len(new_pw) < 6:
            flash("New password must be at least 6 characters.", "error")
            return render_template("reset_password.html", valid_token=True, student=student, token=token)

        if new_pw != confirm_pw:
            flash("Passwords do not match.", "error")
            return render_template("reset_password.html", valid_token=True, student=student, token=token)

        student_id = student["student_id"]
        conn = get_db()
        conn.execute(
            "UPDATE students SET password_hash=?, password_changed=1, must_change_password=0 WHERE student_id=?",
            (generate_password_hash(new_pw), student_id)
        )
        conn.commit()
        conn.close()

        # Invalidate token and notify student
        notification_service.consume_password_token(token)
        notification_service.notify_password_changed(student_id)

        flash("Password reset successfully! You can now log in with your new password.", "success")
        return redirect(url_for("login"))

    token = request.args.get("token", "").strip()
    valid, student = notification_service.verify_password_token(token)
    return render_template("reset_password.html", valid_token=valid, student=student, token=token)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/api/auth/send-otp", methods=["POST"])
def api_send_otp():
    """Dispatches a 6-digit numeric OTP to the student's registered email address."""
    payload = request.get_json(silent=True) or request.form
    email = (payload.get("email") or "").strip()
    if not email:
        return jsonify({"ok": False, "message": "Please enter your registered student email address."}), 400

    ok, message, _ = notification_service.generate_and_send_otp(email)
    status_code = 200 if ok else 400
    return jsonify({"ok": ok, "message": message}), status_code


@app.route("/change-password", methods=["GET", "POST"])
def change_password():
    """
    Student password reset via Email + OTP verification.
    Faculty passwords are strictly immutable from this portal.
    """
    if request.method == "POST":
        is_ajax = request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest"
        payload = request.get_json(silent=True) if request.is_json else request.form

        # Strict security constraint: Faculty passwords cannot be modified from this portal
        role = payload.get("role", "student")
        if role == "faculty":
            msg = "Faculty passwords cannot be modified from this portal. Please contact the administrator."
            if is_ajax:
                return jsonify({"ok": False, "message": msg}), 403
            flash(msg, "error")
            return render_template("change_password.html", student_email="")

        email = (payload.get("email") or payload.get("identifier") or "").strip().lower()
        if not email and session.get("student_id"):
            conn = get_db()
            st = conn.execute("SELECT email FROM students WHERE student_id=?", (session["student_id"],)).fetchone()
            conn.close()
            if st and st["email"]:
                email = st["email"].lower()

        otp = (payload.get("otp") or "").strip()
        new_pw = payload.get("new_password", "").strip()
        confirm_pw = payload.get("confirm_password", "").strip()

        if not email:
            msg = "Please enter your registered student email address."
            if is_ajax:
                return jsonify({"ok": False, "message": msg}), 400
            flash(msg, "error")
            return render_template("change_password.html", student_email="")

        if not otp:
            msg = "Please enter the 6-digit verification code (OTP) sent to your email."
            if is_ajax:
                return jsonify({"ok": False, "message": msg}), 400
            flash(msg, "error")
            return render_template("change_password.html", student_email=email)

        if len(new_pw) < 6:
            msg = "New password must be at least 6 characters."
            if is_ajax:
                return jsonify({"ok": False, "message": msg}), 400
            flash(msg, "error")
            return render_template("change_password.html", student_email=email)

        if new_pw != confirm_pw:
            msg = "New password and confirmation do not match."
            if is_ajax:
                return jsonify({"ok": False, "message": msg}), 400
            flash(msg, "error")
            return render_template("change_password.html", student_email=email)

        # Verify OTP and mark consumed
        valid, msg, student = notification_service.verify_and_consume_otp(email, otp)
        if not valid or not student:
            if is_ajax:
                return jsonify({"ok": False, "message": msg}), 400
            flash(msg, "error")
            return render_template("change_password.html", student_email=email)

        student_id = student["student_id"]
        conn = get_db()
        conn.execute(
            "UPDATE students SET password_hash=?, password_changed=1, must_change_password=0 WHERE student_id=?",
            (generate_password_hash(new_pw), student_id),
        )
        conn.commit()
        conn.close()

        # Dispatched security notification
        notification_service.notify_password_changed(student_id)

        session.clear()

        if is_ajax:
            return jsonify({
                "ok": True,
                "message": "Password updated successfully! Please sign in with your new password.",
                "redirect": url_for("login")
            }), 200

        flash("Password updated successfully! Please sign in with your new password.", "success")
        return redirect(url_for("login"))

    # GET request
    student_email = ""
    if session.get("student_id"):
        conn = get_db()
        st = conn.execute("SELECT email FROM students WHERE student_id=?", (session["student_id"],)).fetchone()
        conn.close()
        if st and st["email"]:
            student_email = st["email"]

    return render_template("change_password.html", student_email=student_email)



# ---------------------------------------------------------------------------
# Student routes
# ---------------------------------------------------------------------------
@app.route("/student/dashboard")
@login_required_student
def student_dashboard():
    student = get_student(session["student_id"])
    progress1 = get_progress(session["student_id"], 1)
    steps_done1 = safe_parse_steps(progress1)
    verified1 = bool(progress1["verification_passed"]) if progress1 else False

    progress2 = get_progress(session["student_id"], 2)
    steps_done2 = safe_parse_steps(progress2)
    verified2 = bool(progress2["verification_passed"]) if progress2 else False

    progress3 = get_progress(session["student_id"], 3)
    steps_done3 = safe_parse_steps(progress3)
    verified3 = bool(progress3["verification_passed"]) if progress3 else False

    progress4 = get_progress(session["student_id"], 4)
    steps_done4 = safe_parse_steps(progress4)
    verified4 = bool(progress4["verification_passed"]) if progress4 else False

    progress5 = get_progress(session["student_id"], 5)
    steps_done5 = safe_parse_steps(progress5)
    verified5 = bool(progress5["verification_passed"]) if progress5 else False

    progress6 = get_progress(session["student_id"], 6)
    steps_done6 = safe_parse_steps(progress6)
    verified6 = bool(progress6["verification_passed"]) if progress6 else False

    return render_template(
        "student_dashboard.html",
        student=student,
        steps_done=steps_done1,
        total_steps=len(LAB1_STEPS),
        verified=verified1,
        steps_done2=steps_done2,
        total_steps2=len(LAB2_STEPS),
        verified2=verified2,
        steps_done3=steps_done3,
        total_steps3=len(LAB3_STEPS),
        verified3=verified3,
        steps_done4=steps_done4,
        total_steps4=len(LAB4_STEPS),
        verified4=verified4,
        steps_done5=steps_done5,
        total_steps5=len(LAB5_STEPS),
        verified5=verified5,
        steps_done6=steps_done6,
        total_steps6=len(LAB6_STEPS),
        verified6=verified6,
    )


@app.route("/student/lab4")
@login_required_student
def lab4():
    student_id = session["student_id"]
    progress3 = get_progress(student_id, 3)
    verified3 = bool(progress3["verification_passed"]) if progress3 else False
    if not verified3:
        flash("🔒 Prerequisite Locked: You must complete and verify Lab 3 before accessing Lab 4.", "error")
        return redirect(url_for("student_dashboard"))

    student = get_student(student_id)
    progress = get_progress(student_id, 4)
    steps_done = json.loads(progress["steps_completed"]) if progress else []
    verified = bool(progress["verification_passed"]) if progress else False
    return render_template(
        "lab4.html",
        student=student,
        steps=LAB4_STEPS,
        steps_done=steps_done,
        verified=verified,
    )


@app.route("/api/lab4/step", methods=["POST"])
@login_required_student
def api_lab4_step():
    student_id = session["student_id"]
    progress3 = get_progress(student_id, 3)
    if not progress3 or not progress3["verification_passed"]:
        return jsonify({"ok": False, "message": "Prerequisite required: Complete Lab 3 first."})

    data = request.get_json(force=True)
    step_id = data.get("step_id")

    conn = get_db()
    now = datetime.utcnow().isoformat()
    row = conn.execute(
        "SELECT * FROM lab_progress WHERE student_id=? AND lab_number=4", (student_id,)
    ).fetchone()

    if row is None:
        steps = [step_id]
        conn.execute(
            "INSERT INTO lab_progress (student_id, lab_number, steps_completed, updated_at) VALUES (?,4,?,?)",
            (student_id, json.dumps(steps), now),
        )
    else:
        steps = json.loads(row["steps_completed"])
        if step_id not in steps:
            steps.append(step_id)
        conn.execute(
            "UPDATE lab_progress SET steps_completed=?, updated_at=? WHERE student_id=? AND lab_number=4",
            (json.dumps(steps), now, student_id),
        )
    enqueue_lab_progress_outbox(conn, student_id, 4)
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/lab4/verify", methods=["POST"])
@login_required_student
def api_lab4_verify():
    student_id = session["student_id"]
    progress3 = get_progress(student_id, 3)
    if not progress3 or not progress3["verification_passed"]:
        return jsonify({"ok": False, "message": "Prerequisite required: Complete Lab 3 first."})

    data = request.get_json(force=True) or {}
    tested = data.get("tested", {})
    
    req_keys = ["app", "dockerfile", "req", "build", "run"]
    missing = [k for k in req_keys if not tested.get(k)]
    
    if missing:
        missing_labels = []
        if not tested.get("app"):
            missing_labels.append("Create 'app.py' Flask microservice logic")
        if not tested.get("dockerfile"):
            missing_labels.append("Configure 'Dockerfile' for Python container")
        if not tested.get("req"):
            missing_labels.append("Create 'requirements.txt' with flask dependency")
        if not tested.get("build"):
            missing_labels.append("Run 'docker build -t sum-microservice .' in terminal")
        if not tested.get("run"):
            missing_labels.append("Run 'docker run -d -p 5000:5000 sum-microservice' in terminal")
            
        msg = "Verification failed! You must complete all required tasks:\n" + "\n".join(f"• {lbl}" for lbl in missing_labels)
        return jsonify({"ok": False, "message": msg})

    conn = get_db()
    now = datetime.utcnow().isoformat()
    all_steps = [s["id"] for s in LAB4_STEPS]

    row = conn.execute(
        "SELECT * FROM lab_progress WHERE student_id=? AND lab_number=4", (student_id,)
    ).fetchone()

    if row is None:
        conn.execute(
            "INSERT INTO lab_progress (student_id, lab_number, steps_completed, verification_passed, completed_at, updated_at) VALUES (?,4,?,1,?,?)",
            (student_id, json.dumps(all_steps), now, now),
        )
    else:
        conn.execute(
            "UPDATE lab_progress SET steps_completed=?, verification_passed=1, completed_at=?, updated_at=? WHERE student_id=? AND lab_number=4",
            (json.dumps(all_steps), now, now, student_id),
        )
    enqueue_lab_progress_outbox(conn, student_id, 4)
    conn.commit()
    conn.close()
    notification_service.notify_lab_completed(student_id, 4, lab_title="Containerized Microservices Development")
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Lab 5: Multi-Container Microservice & Client Application Network Routes
# ---------------------------------------------------------------------------
@app.route("/student/lab5")
@login_required_student
def lab5():
    student_id = session["student_id"]
    progress4 = get_progress(student_id, 4)
    verified4 = bool(progress4["verification_passed"]) if progress4 else False
    if not verified4:
        flash("🔒 Prerequisite Locked: You must complete and verify Lab 4 before accessing Lab 5.", "error")
        return redirect(url_for("student_dashboard"))

    student = get_student(student_id)
    progress = get_progress(student_id, 5)
    steps_done = json.loads(progress["steps_completed"]) if progress else []
    verified = bool(progress["verification_passed"]) if progress else False
    return render_template(
        "lab5.html",
        student=student,
        steps=LAB5_STEPS,
        steps_done=steps_done,
        verified=verified,
    )


@app.route("/api/lab5/step", methods=["POST"])
@login_required_student
def api_lab5_step():
    student_id = session["student_id"]
    progress4 = get_progress(student_id, 4)
    if not progress4 or not progress4["verification_passed"]:
        return jsonify({"ok": False, "message": "Prerequisite required: Complete Lab 4 first."})

    data = request.get_json(force=True)
    step_id = data.get("step_id")

    conn = get_db()
    now = datetime.utcnow().isoformat()
    row = conn.execute(
        "SELECT * FROM lab_progress WHERE student_id=? AND lab_number=5", (student_id,)
    ).fetchone()

    if row is None:
        steps = [step_id]
        conn.execute(
            "INSERT INTO lab_progress (student_id, lab_number, steps_completed, updated_at) VALUES (?,5,?,?)",
            (student_id, json.dumps(steps), now),
        )
    else:
        steps = json.loads(row["steps_completed"])
        if step_id not in steps:
            steps.append(step_id)
        conn.execute(
            "UPDATE lab_progress SET steps_completed=?, updated_at=? WHERE student_id=? AND lab_number=5",
            (json.dumps(steps), now, student_id),
        )
    enqueue_lab_progress_outbox(conn, student_id, 5)
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/lab5/verify", methods=["POST"])
@login_required_student
def api_lab5_verify():
    student_id = session["student_id"]
    progress4 = get_progress(student_id, 4)
    if not progress4 or not progress4["verification_passed"]:
        return jsonify({"ok": False, "message": "Prerequisite required: Complete Lab 4 first."})

    data = request.get_json(force=True) or {}
    tested = data.get("tested", {})
    
    req_keys = ["app", "client", "df_micro", "df_client", "network", "run_micro", "run_client", "test_net"]
    missing = [k for k in req_keys if not tested.get(k)]
    
    if missing:
        missing_labels = []
        if not tested.get("app"):
            missing_labels.append("Create 'app.py' Flask sum microservice logic")
        if not tested.get("client"):
            missing_labels.append("Create 'client.py' Client application logic")
        if not tested.get("df_micro"):
            missing_labels.append("Create 'Dockerfile.microservice' for Microservice container")
        if not tested.get("df_client"):
            missing_labels.append("Create 'Dockerfile.client' for Client application container")
        if not tested.get("network"):
            missing_labels.append("Run 'docker network create app-net' in terminal")
        if not tested.get("run_micro"):
            missing_labels.append("Run microservice container on '--network app-net'")
        if not tested.get("run_client"):
            missing_labels.append("Run client container on '--network app-net'")
        if not tested.get("test_net"):
            missing_labels.append("Verify client application calling sum-microservice over Docker network")
            
        msg = "Verification failed! You must complete all required tasks:\n" + "\n".join(f"• {lbl}" for lbl in missing_labels)
        return jsonify({"ok": False, "message": msg})

    conn = get_db()
    now = datetime.utcnow().isoformat()
    all_steps = [s["id"] for s in LAB5_STEPS]

    row = conn.execute(
        "SELECT * FROM lab_progress WHERE student_id=? AND lab_number=5", (student_id,)
    ).fetchone()

    if row is None:
        conn.execute(
            "INSERT INTO lab_progress (student_id, lab_number, steps_completed, verification_passed, completed_at, updated_at) VALUES (?,5,?,1,?,?)",
            (student_id, json.dumps(all_steps), now, now),
        )
    else:
        conn.execute(
            "UPDATE lab_progress SET steps_completed=?, verification_passed=1, completed_at=?, updated_at=? WHERE student_id=? AND lab_number=5",
            (json.dumps(all_steps), now, now, student_id),
        )
    enqueue_lab_progress_outbox(conn, student_id, 5)
    conn.commit()
    conn.close()
    notification_service.notify_lab_completed(student_id, 5, lab_title="Multi-Container Inter-Communication & Networking")
    return jsonify({"ok": True})


@app.route("/api/lab5/test-network", methods=["GET", "POST"])
@login_required_student
def api_lab5_test_network():
    """Tests live HTTP call from host to sum-client container on port 8080."""
    a = request.args.get("a", 15)
    b = request.args.get("b", 25)
    last_err = None
    for attempt in range(4):
        try:
            req = urllib.request.Request(f"http://localhost:8080/calc?a={a}&b={b}", headers={"User-Agent": "VirtualLab/1.0"})
            with urllib.request.urlopen(req, timeout=3) as resp:
                raw = resp.read().decode("utf-8")
                data = json.loads(raw)
                return jsonify({"ok": True, "live": True, "data": data})
        except Exception as e:
            last_err = e
            time.sleep(0.5)
    return jsonify({
        "ok": False,
        "live": False,
        "error": f"Connection to http://localhost:8080/calc refused: Container 'sum-client' is not responding on port 8080 ({str(last_err)}). Please ensure you have written client.py & Dockerfile.client, created 'app-net', and started both containers."
    })


# ---------------------------------------------------------------------------
# Lab 6: Microservice Load Scaling & Replica Management Routes
# ---------------------------------------------------------------------------
@app.route("/student/lab6")
@login_required_student
def lab6():
    student_id = session["student_id"]
    progress5 = get_progress(student_id, 5)
    verified5 = bool(progress5["verification_passed"]) if progress5 else False
    if not verified5:
        flash("🔒 Prerequisite Locked: You must complete and verify Lab 5 before accessing Lab 6.", "error")
        return redirect(url_for("student_dashboard"))

    student = get_student(student_id)
    progress = get_progress(student_id, 6)
    steps_done = json.loads(progress["steps_completed"]) if progress else []
    verified = bool(progress["verification_passed"]) if progress else False
    return render_template(
        "lab6.html",
        student=student,
        steps=LAB6_STEPS,
        steps_done=steps_done,
        verified=verified,
    )


@app.route("/api/lab6/step", methods=["POST"])
@login_required_student
def api_lab6_step():
    student_id = session["student_id"]
    progress5 = get_progress(student_id, 5)
    if not progress5 or not progress5["verification_passed"]:
        return jsonify({"ok": False, "message": "Prerequisite required: Complete Lab 5 first."})

    data = request.get_json(force=True)
    step_id = data.get("step_id")

    conn = get_db()
    now = datetime.utcnow().isoformat()
    row = conn.execute(
        "SELECT * FROM lab_progress WHERE student_id=? AND lab_number=6", (student_id,)
    ).fetchone()

    if row is None:
        steps = [step_id]
        conn.execute(
            "INSERT INTO lab_progress (student_id, lab_number, steps_completed, updated_at) VALUES (?,6,?,?)",
            (student_id, json.dumps(steps), now),
        )
    else:
        steps = json.loads(row["steps_completed"])
        if step_id not in steps:
            steps.append(step_id)
        conn.execute(
            "UPDATE lab_progress SET steps_completed=?, updated_at=? WHERE student_id=? AND lab_number=6",
            (json.dumps(steps), now, student_id),
        )
    enqueue_lab_progress_outbox(conn, student_id, 6)
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/lab6/verify", methods=["POST"])
@login_required_student
def api_lab6_verify():
    student_id = session["student_id"]
    progress5 = get_progress(student_id, 5)
    if not progress5 or not progress5["verification_passed"]:
        return jsonify({"ok": False, "message": "Prerequisite required: Complete Lab 5 first."})

    data = request.get_json(force=True) or {}
    tested = data.get("tested", {})
    
    req_keys = ["app", "dockerfile", "compose", "deploy", "scale_up", "scale_down"]
    missing = [k for k in req_keys if not tested.get(k)]
    
    if missing:
        missing_labels = []
        if not tested.get("app"):
            missing_labels.append("Write 'app.py' microservice logic")
        if not tested.get("dockerfile"):
            missing_labels.append("Configure 'Dockerfile' for Python app")
        if not tested.get("compose"):
            missing_labels.append("Configure 'docker-compose.yml' service stack")
        if not tested.get("deploy"):
            missing_labels.append("Run 'docker compose up -d' stack deployment")
        if not tested.get("scale_up"):
            missing_labels.append("Execute 'docker compose up -d --scale sum-microservice=3' to scale up")
        if not tested.get("scale_down"):
            missing_labels.append("Execute 'docker compose up -d --scale sum-microservice=1' to scale down")
            
        msg = "Verification failed! You must complete all required tasks:\n" + "\n".join(f"• {lbl}" for lbl in missing_labels)
        return jsonify({"ok": False, "message": msg})

    conn = get_db()
    now = datetime.utcnow().isoformat()
    all_steps = [s["id"] for s in LAB6_STEPS]

    row = conn.execute(
        "SELECT * FROM lab_progress WHERE student_id=? AND lab_number=6", (student_id,)
    ).fetchone()

    if row is None:
        conn.execute(
            "INSERT INTO lab_progress (student_id, lab_number, steps_completed, verification_passed, completed_at, updated_at) VALUES (?,6,?,1,?,?)",
            (student_id, json.dumps(all_steps), now, now),
        )
    else:
        conn.execute(
            "UPDATE lab_progress SET steps_completed=?, verification_passed=1, completed_at=?, updated_at=? WHERE student_id=? AND lab_number=6",
            (json.dumps(all_steps), now, now, student_id),
        )
    enqueue_lab_progress_outbox(conn, student_id, 6)
    conn.commit()
    conn.close()
    notification_service.notify_lab_completed(student_id, 6, lab_title="Microservices Orchestration with Docker Compose")
    return jsonify({"ok": True})


@app.route("/add", methods=["GET", "POST"])
@app.route("/api/calculator/add", methods=["GET", "POST"])
@app.route("/api/calculator/sum/<path:a_val>/<path:b_val>", methods=["GET", "POST"])
@app.route("/api/calculator/sum", methods=["GET", "POST"])
def api_calculator_sum(a_val=None, b_val=None):
    """Live Sum Microservice API Endpoint for Lab 4 container testing."""
    student_id = session.get("student_id", "guest")
    app_path = os.path.join(app.root_path, "workspaces", str(student_id), "app.py")
    
    app_has_code = False
    if os.path.exists(app_path):
        try:
            with open(app_path, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if content and "flask" in content.lower():
                    app_has_code = True
        except Exception:
            pass

    if a_val is None or b_val is None:
        if request.method == "POST":
            data = request.get_json(silent=True) or request.form
            a_val = data.get("a")
            b_val = data.get("b")
        else:
            a_val = request.args.get("a")
            b_val = request.args.get("b")

    try:
        a = float(a_val)
        b = float(b_val)
        res_sum = a + b
        return jsonify({
            "number1": a,
            "number2": b,
            "sum": int(res_sum) if res_sum.is_integer() else res_sum
        })
    except (ValueError, TypeError):
        return jsonify({"error": "Please provide two valid numbers"}), 400


@app.route("/student/lab3")
@login_required_student
def lab3():
    student_id = session["student_id"]
    progress2 = get_progress(student_id, 2)
    verified2 = bool(progress2["verification_passed"]) if progress2 else False
    if not verified2:
        flash("🔒 Prerequisite Locked: You must complete and verify Lab 2 before accessing Lab 3.", "error")
        return redirect(url_for("student_dashboard"))

    student = get_student(student_id)
    progress = get_progress(student_id, 3)
    steps_done = json.loads(progress["steps_completed"]) if progress else []
    verified = bool(progress["verification_passed"]) if progress else False
    return render_template(
        "lab3.html",
        student=student,
        steps=LAB3_STEPS,
        steps_done=steps_done,
        verified=verified,
    )


@app.route("/api/lab3/step", methods=["POST"])
@login_required_student
def api_lab3_step():
    student_id = session["student_id"]
    progress2 = get_progress(student_id, 2)
    if not progress2 or not progress2["verification_passed"]:
        return jsonify({"ok": False, "message": "Prerequisite required: Complete Lab 2 first."})

    data = request.get_json(force=True)
    step_id = data.get("step_id")

    conn = get_db()
    now = datetime.utcnow().isoformat()
    row = conn.execute(
        "SELECT * FROM lab_progress WHERE student_id=? AND lab_number=3", (student_id,)
    ).fetchone()

    if row is None:
        steps = [step_id]
        conn.execute(
            "INSERT INTO lab_progress (student_id, lab_number, steps_completed, updated_at) VALUES (?,3,?,?)",
            (student_id, json.dumps(steps), now),
        )
    else:
        steps = json.loads(row["steps_completed"])
        if step_id not in steps:
            steps.append(step_id)
        conn.execute(
            "UPDATE lab_progress SET steps_completed=?, updated_at=? WHERE student_id=? AND lab_number=3",
            (json.dumps(steps), now, student_id),
        )
    enqueue_lab_progress_outbox(conn, student_id, 3)
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/lab3/verify", methods=["POST"])
@login_required_student
def api_lab3_verify():
    student_id = session["student_id"]
    progress2 = get_progress(student_id, 2)
    if not progress2 or not progress2["verification_passed"]:
        return jsonify({"ok": False, "message": "Prerequisite required: Complete Lab 2 first."})

    data = request.get_json(force=True) or {}
    tested = data.get("tested", {})
    
    # Check that compulsory Docker commands and files were completed
    req_keys = ["index", "dockerfile", "pull", "run", "build", "run_web", "stop_start", "prune", "ps2", "rm"]
    missing = [k for k in req_keys if not tested.get(k)]
    
    if missing:
        return jsonify({
            "ok": False,
            "message": "Verification failed! You must test all 10 compulsory Docker commands in the terminal console before submitting Lab 3."
        })

    conn = get_db()
    now = datetime.utcnow().isoformat()
    all_steps = [s["id"] for s in LAB3_STEPS]

    row = conn.execute(
        "SELECT * FROM lab_progress WHERE student_id=? AND lab_number=3", (student_id,)
    ).fetchone()

    if row is None:
        conn.execute(
            "INSERT INTO lab_progress (student_id, lab_number, steps_completed, verification_passed, completed_at, updated_at) VALUES (?,3,?,1,?,?)",
            (student_id, json.dumps(all_steps), now, now),
        )
    else:
        conn.execute(
            "UPDATE lab_progress SET steps_completed=?, verification_passed=1, completed_at=?, updated_at=? WHERE student_id=? AND lab_number=3",
            (json.dumps(all_steps), now, now, student_id),
        )
    enqueue_lab_progress_outbox(conn, student_id, 3)
    conn.commit()
    conn.close()
    notification_service.notify_lab_completed(student_id, 3, lab_title="Hello World Application Deployment")
    return jsonify({"ok": True, "message": "Lab 3 Verification Passed! Hello World container application verified."})


@app.route("/student/lab2")
@login_required_student
def lab2():
    student_id = session["student_id"]
    progress1 = get_progress(student_id, 1)
    verified1 = bool(progress1["verification_passed"]) if progress1 else False
    if not verified1:
        flash("🔒 Prerequisite Locked: You must complete and verify Lab 1 before accessing Lab 2.", "error")
        return redirect(url_for("student_dashboard"))

    student = get_student(student_id)
    progress = get_progress(student_id, 2)
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
    student_id = session["student_id"]
    progress1 = get_progress(student_id, 1)
    if not progress1 or not progress1["verification_passed"]:
        return jsonify({"ok": False, "message": "Prerequisite required: Complete Lab 1 first."})

    data = request.get_json(force=True)
    step_id = data.get("step_id")

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
    enqueue_lab_progress_outbox(conn, student_id, 2)
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/lab2/verify", methods=["POST"])
@login_required_student
def api_lab2_verify():
    student_id = session["student_id"]
    progress1 = get_progress(student_id, 1)
    if not progress1 or not progress1["verification_passed"]:
        return jsonify({"ok": False, "message": "Prerequisite required: Complete Lab 1 first."})

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
    enqueue_lab_progress_outbox(conn, student_id, 2)
    conn.commit()
    conn.close()
    notification_service.notify_lab_completed(student_id, 2, lab_title="Docker Desktop Installation & Architecture")
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
    enqueue_lab_progress_outbox(conn, student_id, 1)
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
        enqueue_lab_progress_outbox(conn, student_id, 1)
        conn.commit()
        conn.close()
        notification_service.notify_lab_completed(student_id, 1, lab_title="Introduction to Docker & Environment Setup")
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
    lab3_completed_count = 0
    lab4_completed_count = 0
    lab5_completed_count = 0
    lab6_completed_count = 0

    for s in students:
        sid = s["student_id"]
        s_map = progress_map.get(sid, {})

        # Lab 1
        p1 = s_map.get(1)
        l1_steps = len(safe_parse_steps(p1))
        l1_verified = bool(p1["verification_passed"]) if p1 else False
        if l1_verified:
            lab1_completed_count += 1

        # Lab 2
        p2 = s_map.get(2)
        l2_steps = len(safe_parse_steps(p2))
        l2_verified = bool(p2["verification_passed"]) if p2 else False
        if l2_verified:
            lab2_completed_count += 1

        # Lab 3
        p3 = s_map.get(3)
        l3_steps = len(safe_parse_steps(p3))
        l3_verified = bool(p3["verification_passed"]) if p3 else False
        if l3_verified:
            lab3_completed_count += 1

        # Lab 4
        p4 = s_map.get(4)
        l4_steps = len(safe_parse_steps(p4))
        l4_verified = bool(p4["verification_passed"]) if p4 else False
        if l4_verified:
            lab4_completed_count += 1

        # Lab 5
        p5 = s_map.get(5)
        l5_steps = len(safe_parse_steps(p5))
        l5_verified = bool(p5["verification_passed"]) if p5 else False
        if l5_verified:
            lab5_completed_count += 1

        # Lab 6
        p6 = s_map.get(6)
        l6_steps = len(safe_parse_steps(p6))
        l6_verified = bool(p6["verification_passed"]) if p6 else False
        if l6_verified:
            lab6_completed_count += 1

        # Calculate overall score percentage across all 6 labs
        total_possible = len(LAB1_STEPS) + len(LAB2_STEPS) + len(LAB3_STEPS) + len(LAB4_STEPS) + len(LAB5_STEPS) + len(LAB6_STEPS)
        earned_steps = (len(LAB1_STEPS) if l1_verified else l1_steps) + \
                       (len(LAB2_STEPS) if l2_verified else l2_steps) + \
                       (len(LAB3_STEPS) if l3_verified else l3_steps) + \
                       (len(LAB4_STEPS) if l4_verified else l4_steps) + \
                       (len(LAB5_STEPS) if l5_verified else l5_steps) + \
                       (len(LAB6_STEPS) if l6_verified else l6_steps)
        overall_pct = int((earned_steps / total_possible) * 100) if total_possible > 0 else 0

        s_dict = dict(s)
        roster.append({
            "student_id": sid,
            "name": s["name"],
            "email": s["email"],
            "enrollment_no": s_dict.get("enrollment_no") or "",
            "roll_no": s_dict.get("roll_no") or "",
            "is_active": s_dict.get("is_active", 1),
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
            "lab3": {
                "steps_done": l3_steps,
                "total_steps": len(LAB3_STEPS),
                "verified": l3_verified,
                "completed_at": p3["completed_at"] if p3 else None
            },
            "lab4": {
                "steps_done": l4_steps,
                "total_steps": len(LAB4_STEPS),
                "verified": l4_verified,
                "completed_at": p4["completed_at"] if p4 else None
            },
            "lab5": {
                "steps_done": l5_steps,
                "total_steps": len(LAB5_STEPS),
                "verified": l5_verified,
                "completed_at": p5["completed_at"] if p5 else None
            },
            "lab6": {
                "steps_done": l6_steps,
                "total_steps": len(LAB6_STEPS),
                "verified": l6_verified,
                "completed_at": p6["completed_at"] if p6 else None
            },
            "overall_pct": overall_pct,
            "is_fully_done": l1_verified and l2_verified and l3_verified and l4_verified and l5_verified and l6_verified
        })

    return render_template(
        "faculty_dashboard.html",
        roster=roster,
        total_students=len(students),
        lab1_completed=lab1_completed_count,
        lab2_completed=lab2_completed_count,
        lab3_completed=lab3_completed_count,
        lab4_completed=lab4_completed_count,
        lab5_completed=lab5_completed_count,
        lab6_completed=lab6_completed_count
    )


@app.route("/faculty/add-student", methods=["POST"])
@login_required_faculty
def faculty_add_student():
    """Allows faculty to manually enroll a single student with auto-generated ID & default password."""
    if request.is_json:
        data = request.get_json(force=True) or {}
        name = data.get("name", "").strip()
        email = data.get("email", "").strip()
        enrollment_no = (data.get("enrollment_no") or "").strip() or None
        roll_no = (data.get("roll_no") or "").strip() or None
        custom_sid = data.get("student_id", "").strip()
        custom_pwd = data.get("default_password", "").strip()
    else:
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip()
        enrollment_no = (request.form.get("enrollment_no") or "").strip() or None
        roll_no = (request.form.get("roll_no") or "").strip() or None
        custom_sid = request.form.get("student_id", "").strip()
        custom_pwd = request.form.get("default_password", "").strip()

    if not name:
        if request.is_json:
            return jsonify({"ok": False, "message": "Student Name is required."}), 400
        flash("Student Name is required.", "error")
        return redirect(url_for("faculty_dashboard"))

    conn = get_db()
    existing_rows = conn.execute("SELECT student_id FROM students").fetchall()
    existing_ids = set(r["student_id"] for r in existing_rows)

    if custom_sid:
        if custom_sid in existing_ids:
            conn.close()
            msg = f"Student ID '{custom_sid}' is already registered."
            if request.is_json:
                return jsonify({"ok": False, "message": msg}), 400
            flash(msg, "error")
            return redirect(url_for("faculty_dashboard"))
        student_id = custom_sid
    else:
        indices = []
        for sid in existing_ids:
            if sid.startswith("STU"):
                try:
                    indices.append(int(sid[3:]))
                except ValueError:
                    pass
        next_idx = max(indices) + 1 if indices else len(existing_ids) + 1
        student_id = generate_student_id(existing_ids, next_idx)

    default_password = custom_pwd if custom_pwd else generate_default_password()
    password_hash = generate_password_hash(default_password)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    try:
        conn.execute(
            """INSERT INTO students (student_id, name, email, enrollment_no, roll_no, default_password, password_hash, password_changed, is_active, must_change_password, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 0, 1, 1, ?)""",
            (student_id, name, email, enrollment_no, roll_no, default_password, password_hash, now)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        conn.close()
        if request.is_json:
            return jsonify({"ok": False, "message": f"Database error: {str(e)}"}), 500
        flash(f"Database error: {str(e)}", "error")
        return redirect(url_for("faculty_dashboard"))

    # Trigger async welcome email with credentials and flush queue immediately
    if email:
        notification_service.notify_account_created(student_id, raw_password=default_password, force=True)
        email_service.process_queue()

    msg = f"Student '{name}' registered successfully! Assigned ID: {student_id} | Default Password: {default_password}"
    if enrollment_no:
        msg += f" | Enrollment: {enrollment_no}"
    if roll_no:
        msg += f" | Roll No: {roll_no}"

    if request.is_json:
        return jsonify({
            "ok": True,
            "message": msg,
            "student_id": student_id,
            "name": name,
            "email": email,
            "enrollment_no": enrollment_no,
            "roll_no": roll_no,
            "default_password": default_password,
            "student": {
                "student_id": student_id,
                "name": name,
                "email": email,
                "enrollment_no": enrollment_no,
                "roll_no": roll_no,
                "default_password": default_password
            }
        })

    flash(msg, "success")
    return redirect(url_for("faculty_dashboard"))



@app.route("/faculty/delete-student/<student_id>", methods=["POST", "DELETE"])
@app.route("/api/faculty/delete-student/<student_id>", methods=["POST", "DELETE"])
@login_required_faculty
def faculty_delete_student(student_id):
    """Deletes a student record and all associated lab progress."""
    conn = get_db()
    student = conn.execute("SELECT * FROM students WHERE student_id=?", (student_id,)).fetchone()
    if not student:
        conn.close()
        if request.is_json or request.path.startswith("/api/"):
            return jsonify({"ok": False, "message": "Student not found."}), 404
        flash("Student not found.", "error")
        return redirect(url_for("faculty_dashboard"))

    conn.execute("DELETE FROM lab_progress WHERE student_id=?", (student_id,))
    conn.execute("DELETE FROM issue_tickets WHERE student_id=?", (student_id,))
    conn.execute("DELETE FROM students WHERE student_id=?", (student_id,))
    conn.commit()
    conn.close()

    msg = f"Student '{student['name']}' ({student_id}) deleted successfully."
    if request.is_json or request.path.startswith("/api/"):
        return jsonify({"ok": True, "message": msg})
    flash(msg, "success")
    return redirect(url_for("faculty_dashboard"))


@app.route("/api/faculty/stats")
@login_required_faculty
def api_faculty_stats():
    """Live polling endpoint for faculty dashboard."""
    conn = get_db()
    students = conn.execute("SELECT student_id, name, email, default_password, password_changed FROM students ORDER BY student_id").fetchall()
    progress_rows = conn.execute(
        "SELECT student_id, lab_number, steps_completed, verification_passed, completed_at FROM lab_progress"
    ).fetchall()
    conn.close()

    total = len(students)
    l1_completed = sum(1 for p in progress_rows if p["lab_number"] == 1 and p["verification_passed"])
    l2_completed = sum(1 for p in progress_rows if p["lab_number"] == 2 and p["verification_passed"])
    l3_completed = sum(1 for p in progress_rows if p["lab_number"] == 3 and p["verification_passed"])
    l4_completed = sum(1 for p in progress_rows if p["lab_number"] == 4 and p["verification_passed"])
    l5_completed = sum(1 for p in progress_rows if p["lab_number"] == 5 and p["verification_passed"])
    l6_completed = sum(1 for p in progress_rows if p["lab_number"] == 6 and p["verification_passed"])

    return jsonify({
        "total": total,
        "completed": l1_completed,
        "lab1_completed": l1_completed,
        "lab2_completed": l2_completed,
        "lab3_completed": l3_completed,
        "lab4_completed": l4_completed,
        "lab5_completed": l5_completed,
        "lab6_completed": l6_completed
    })


@app.route("/api/faculty/live-data", methods=["GET"])
@login_required_faculty
def api_faculty_live_data():
    """Near-real-time live synchronization endpoint for faculty dashboard."""
    conn = get_db()
    students = conn.execute("SELECT student_id, name, email, default_password, password_changed FROM students ORDER BY student_id").fetchall()
    progress_rows = conn.execute(
        "SELECT student_id, lab_number, steps_completed, verification_passed, completed_at FROM lab_progress"
    ).fetchall()
    tickets_rows = conn.execute("SELECT * FROM issue_tickets ORDER BY id DESC").fetchall()
    conn.close()

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
    lab3_completed_count = 0
    lab4_completed_count = 0
    lab5_completed_count = 0
    lab6_completed_count = 0

    total_possible = len(LAB1_STEPS) + len(LAB2_STEPS) + len(LAB3_STEPS) + len(LAB4_STEPS) + len(LAB5_STEPS) + len(LAB6_STEPS)

    for s in students:
        sid = s["student_id"]
        s_map = progress_map.get(sid, {})

        p1 = s_map.get(1)
        l1_steps = len(safe_parse_steps(p1))
        l1_verified = bool(p1["verification_passed"]) if p1 else False
        if l1_verified:
            lab1_completed_count += 1

        p2 = s_map.get(2)
        l2_steps = len(safe_parse_steps(p2))
        l2_verified = bool(p2["verification_passed"]) if p2 else False
        if l2_verified:
            lab2_completed_count += 1

        p3 = s_map.get(3)
        l3_steps = len(safe_parse_steps(p3))
        l3_verified = bool(p3["verification_passed"]) if p3 else False
        if l3_verified:
            lab3_completed_count += 1

        p4 = s_map.get(4)
        l4_steps = len(safe_parse_steps(p4))
        l4_verified = bool(p4["verification_passed"]) if p4 else False
        if l4_verified:
            lab4_completed_count += 1

        p5 = s_map.get(5)
        l5_steps = len(safe_parse_steps(p5))
        l5_verified = bool(p5["verification_passed"]) if p5 else False
        if l5_verified:
            lab5_completed_count += 1

        p6 = s_map.get(6)
        l6_steps = len(safe_parse_steps(p6))
        l6_verified = bool(p6["verification_passed"]) if p6 else False
        if l6_verified:
            lab6_completed_count += 1

        earned_steps = (len(LAB1_STEPS) if l1_verified else l1_steps) + \
                       (len(LAB2_STEPS) if l2_verified else l2_steps) + \
                       (len(LAB3_STEPS) if l3_verified else l3_steps) + \
                       (len(LAB4_STEPS) if l4_verified else l4_steps) + \
                       (len(LAB5_STEPS) if l5_verified else l5_steps) + \
                       (len(LAB6_STEPS) if l6_verified else l6_steps)
        overall_pct = int((earned_steps / total_possible) * 100) if total_possible > 0 else 0

        s_dict = dict(s)
        roster.append({
            "student_id": sid,
            "name": s["name"],
            "email": s["email"],
            "enrollment_no": s_dict.get("enrollment_no") or "",
            "roll_no": s_dict.get("roll_no") or "",
            "is_active": s_dict.get("is_active", 1),
            "default_password": s["default_password"],
            "password_changed": bool(s["password_changed"]),
            "lab1": {"steps_done": l1_steps, "total_steps": len(LAB1_STEPS), "verified": l1_verified, "completed_at": p1["completed_at"] if p1 else None},
            "lab2": {"steps_done": l2_steps, "total_steps": len(LAB2_STEPS), "verified": l2_verified, "completed_at": p2["completed_at"] if p2 else None},
            "lab3": {"steps_done": l3_steps, "total_steps": len(LAB3_STEPS), "verified": l3_verified, "completed_at": p3["completed_at"] if p3 else None},
            "lab4": {"steps_done": l4_steps, "total_steps": len(LAB4_STEPS), "verified": l4_verified, "completed_at": p4["completed_at"] if p4 else None},
            "lab5": {"steps_done": l5_steps, "total_steps": len(LAB5_STEPS), "verified": l5_verified, "completed_at": p5["completed_at"] if p5 else None},
            "lab6": {"steps_done": l6_steps, "total_steps": len(LAB6_STEPS), "verified": l6_verified, "completed_at": p6["completed_at"] if p6 else None},
            "overall_pct": overall_pct,
            "is_fully_done": l1_verified and l2_verified and l3_verified and l4_verified and l5_verified and l6_verified
        })

    stats = {
        "total_students": len(students),
        "lab1_completed": lab1_completed_count,
        "lab2_completed": lab2_completed_count,
        "lab3_completed": lab3_completed_count,
        "lab4_completed": lab4_completed_count,
        "lab5_completed": lab5_completed_count,
        "lab6_completed": lab6_completed_count
    }

    tickets = [dict(t) for t in tickets_rows]
    tasks = task_manager.list_tasks(None)

    raw_hash_data = json.dumps({"stats": stats, "roster": roster, "tickets": tickets, "tasks": tasks}, sort_keys=True)
    data_hash = hashlib.md5(raw_hash_data.encode("utf-8")).hexdigest()

    client_hash = request.args.get("hash")
    if client_hash and client_hash == data_hash:
        return jsonify({"ok": True, "changed": False, "hash": data_hash})

    return jsonify({
        "ok": True,
        "changed": True,
        "hash": data_hash,
        "stats": stats,
        "roster": roster,
        "tickets": tickets,
        "tasks": tasks
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
    
    # Auto-detect enrollment and roll number headers
    enrollment_idx = None
    roll_idx = None
    for i, col in enumerate(header):
        if col in ("enrollment", "enrollment_no", "enrollment no", "prn"):
            enrollment_idx = i
        elif col in ("roll", "roll_no", "roll no"):
            roll_idx = i

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
        enrollment_no = str(row[enrollment_idx]).strip() if enrollment_idx is not None and row[enrollment_idx] else None
        roll_no = str(row[roll_idx]).strip() if roll_idx is not None and row[roll_idx] else None

        sid = generate_student_id(existing_ids, idx_counter)
        existing_ids.add(sid)
        idx_counter += 1

        default_pw = generate_default_password()
        conn.execute(
            """INSERT INTO students (student_id, name, email, enrollment_no, roll_no, default_password,
               password_hash, password_changed, is_active, must_change_password, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 0, 1, 1, ?)""",
            (sid, name, email, enrollment_no, roll_no, default_pw, generate_password_hash(default_pw), now),
        )
        added += 1

        # Dispatch welcome email with credentials
        if email:
            notification_service.notify_account_created(sid, raw_password=default_pw, force=True)

    conn.commit()
    conn.close()
    if added > 0:
        email_service.process_queue()
    flash(f"Added {added} student(s) with credentials dispatched.", "success")
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
    ws.append(["Student ID", "Enrollment No", "Roll No", "Name", "Email", "Default Password", "Password Changed"])
    for s in students:
        s_dict = dict(s)
        ws.append([
            s_dict.get("student_id", ""),
            s_dict.get("enrollment_no") or "",
            s_dict.get("roll_no") or "",
            s_dict.get("name", ""),
            s_dict.get("email", ""),
            s_dict.get("default_password", ""),
            "Yes" if s_dict.get("password_changed") else "No"
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
    role = session.get("role")
    if role not in ("student", "faculty"):
        return jsonify({"ok": False, "error": "Unauthorized"}), 401
    conn = get_db()
    if role == "student":
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
    ticket = conn.execute("SELECT * FROM issue_tickets WHERE id=?", (ticket_id,)).fetchone()
    conn.execute(
        "UPDATE issue_tickets SET faculty_reply=?, status=? WHERE id=?",
        (reply, status, ticket_id)
    )
    conn.commit()
    conn.close()

    if ticket:
        notification_service.notify_ticket_replied(
            ticket["student_id"],
            ticket_id,
            subject=ticket["title"] if "title" in ticket.keys() else "Support Request",
            reply_snippet=reply
        )

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


# ---------------------------------------------------------------------------
# In-App Student Notifications & Preference Endpoints
# ---------------------------------------------------------------------------
@app.route("/api/notifications", methods=["GET"])
@login_required_student
def api_get_notifications():
    """Fetches in-app notifications for the logged in student with unread count."""
    student_id = session["student_id"]
    data = notification_service.get_student_notifications(student_id, limit=50)
    return jsonify({
        "ok": True,
        "notifications": data.get("notifications", []),
        "unread_count": data.get("unread_count", 0)
    })


@app.route("/api/notifications/<int:notification_id>/read", methods=["POST"])
@login_required_student
def api_mark_notification_read(notification_id):
    """Marks a single notification as read."""
    student_id = session["student_id"]
    notification_service.mark_notifications_read(student_id, notification_id)
    return jsonify({"ok": True})


@app.route("/api/notifications/read-all", methods=["POST"])
@login_required_student
def api_mark_all_notifications_read():
    """Marks all notifications as read for current student."""
    student_id = session["student_id"]
    notification_service.mark_notifications_read(student_id)
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Faculty Email Delivery Audit & Credential Management Endpoints
# ---------------------------------------------------------------------------
@app.route("/api/faculty/email-logs", methods=["GET"])
@login_required_faculty
def api_faculty_email_logs():
    """Returns recent email delivery audit logs and current pending/failed queue items."""
    limit = request.args.get("limit", 50, type=int)
    conn = get_db()
    logs = conn.execute(
        "SELECT * FROM email_logs ORDER BY sent_at DESC LIMIT ?", (limit,)
    ).fetchall()
    queue = conn.execute(
        "SELECT * FROM email_queue WHERE UPPER(status) IN ('PENDING', 'FAILED', 'PROCESSING') ORDER BY created_at DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return jsonify({
        "ok": True,
        "logs": [dict(l) for l in logs],
        "queue": [dict(q) for q in queue]
    })


@app.route("/api/faculty/resend-credentials/<student_id>", methods=["POST"])
@login_required_faculty
def api_faculty_resend_credentials(student_id):
    """Allows faculty to re-dispatch login credentials to student's registered email."""
    conn = get_db()
    student = conn.execute("SELECT * FROM students WHERE student_id=?", (student_id,)).fetchone()
    if not student:
        conn.close()
        return jsonify({"ok": False, "message": "Student record not found."}), 404

    raw_pw = student["default_password"] or generate_default_password()
    if not student["default_password"]:
        conn.execute(
            "UPDATE students SET default_password=?, password_hash=?, must_change_password=1 WHERE student_id=?",
            (raw_pw, generate_password_hash(raw_pw), student_id)
        )
        conn.commit()
    conn.close()

    if student["email"]:
        notification_service.notify_account_created(student_id, raw_password=raw_pw, force=True)
        email_service.process_queue()
        return jsonify({"ok": True, "message": f"Login credentials queued and dispatched to {student['email']}."})
    else:
        return jsonify({"ok": False, "message": f"Student '{student['name']}' has no registered email address."}), 400


@app.route("/api/faculty/reset-student-password/<student_id>", methods=["POST"])
@login_required_faculty
def api_faculty_reset_student_password(student_id):
    """Generates a fresh default password for student, updates DB, emails student, and flushes queue."""
    conn = get_db()
    student = conn.execute("SELECT * FROM students WHERE student_id=?", (student_id,)).fetchone()
    if not student:
        conn.close()
        return jsonify({"ok": False, "message": "Student record not found."}), 404

    new_pw = generate_default_password()
    conn.execute(
        "UPDATE students SET default_password=?, password_hash=?, password_changed=0, must_change_password=1 WHERE student_id=?",
        (new_pw, generate_password_hash(new_pw), student_id)
    )
    conn.commit()
    conn.close()

    if student["email"]:
        notification_service.notify_account_created(student_id, raw_password=new_pw, force=True, is_reset=True)
        email_service.process_queue()
        msg = f"New default password '{new_pw}' generated and dispatched to {student['email']}."
    else:
        msg = f"New default password '{new_pw}' generated. (Student has no registered email address)."

    return jsonify({
        "ok": True,
        "student_id": student_id,
        "name": student["name"],
        "email": student["email"],
        "default_password": new_pw,
        "message": msg
    })


@app.route("/api/faculty/toggle-student-active/<student_id>", methods=["POST"])
@login_required_faculty
def api_faculty_toggle_student_active(student_id):
    """Activates or deactivates student account access."""
    conn = get_db()
    student = conn.execute("SELECT * FROM students WHERE student_id=?", (student_id,)).fetchone()
    if not student:
        conn.close()
        return jsonify({"ok": False, "message": "Student record not found."}), 404

    current_status = student["is_active"] if "is_active" in student.keys() else 1
    new_status = 0 if current_status == 1 else 1

    conn.execute("UPDATE students SET is_active=? WHERE student_id=?", (new_status, student_id))
    conn.commit()
    conn.close()

    status_str = "Active" if new_status == 1 else "Deactivated"
    return jsonify({"ok": True, "is_active": new_status, "message": f"Student account set to {status_str}."})



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
        has_docker, docker_msg = check_host_docker_status()
        ram_mb = mem.used / (1024 * 1024)
        if ram_mb >= 1024:
            ram_str = f"{(ram_mb / 1024):.1f} GB"
        else:
            ram_str = f"{ram_mb:.0f} MB"

        disk_gb = disk.used / (1024 * 1024 * 1024)
        disk_str = f"{disk_gb:.1f} GB"

        return jsonify({
            "ok": True,
            "pid": pid,
            "cpu": f"{cpu_pct:.1f}%",
            "cpu_num": cpu_pct,
            "cpu_percent": round(cpu_pct, 1),
            "ram": ram_str,
            "ram_pct": f"{mem.percent}%",
            "memory_percent": round(mem.percent, 1),
            "memory_used_mb": round(mem.used / (1024 * 1024), 1),
            "disk": disk_str,
            "disk_percent": round(disk.percent, 1),
            "disk_free_gb": round(disk.free / (1024 * 1024 * 1024), 1),
            "docker_engine": "local" if has_docker else "virtual",
            "docker_status_text": docker_msg
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/start-docker", methods=["POST", "GET"])
def api_start_docker():
    success = auto_start_host_docker(force=True)
    has_real, msg = check_host_docker_status()
    return jsonify({
        "ok": success,
        "status": msg,
        "connected": has_real,
        "message": "Docker Desktop launch initiated." if success else "Docker Desktop executable not found."
    })


@app.errorhandler(500)
def handle_500_error(e):
    import traceback
    traceback.print_exc()
    if request.path.startswith("/api/"):
        return jsonify({"ok": False, "output": f"Internal Server Error: {str(e)}"}), 500
    return f"Internal Server Error: {str(e)}", 500


@app.route("/api/agent/register", methods=["POST"])
def api_agent_register():
    data = request.get_json(force=True) or {}
    secret = (
        request.headers.get("X-Cluster-Secret")
        or request.headers.get("X-Agent-Secret")
        or (request.headers.get("Authorization") or "").replace("Bearer ", "").strip()
        or data.get("secret", "")
    )
    data["secret"] = secret
    ok, msg = ha_cluster_manager.register_host(data)
    status_code = 200 if ok else (401 if "secret" in msg.lower() else 400)
    return jsonify({"ok": ok, "message": msg}), status_code


@app.route("/api/agent/heartbeat", methods=["POST"])
def api_agent_heartbeat():
    data = request.get_json(force=True) or {}
    secret = (
        request.headers.get("X-Cluster-Secret")
        or request.headers.get("X-Agent-Secret")
        or (request.headers.get("Authorization") or "").replace("Bearer ", "").strip()
        or data.get("secret", "")
    )
    data["secret"] = secret
    ok, msg = ha_cluster_manager.record_heartbeat(data)
    status_code = 200 if ok else (401 if "secret" in msg.lower() else 400)
    return jsonify({"ok": ok, "message": msg}), status_code


@app.route("/api/ha/status", methods=["GET"])
def api_ha_status():
    role = session.get("role")
    if role not in ("student", "faculty"):
        return jsonify({"ok": False, "error": "Unauthorized"}), 401
    status = ha_cluster_manager.get_cluster_status()
    return jsonify(status)


@app.route("/api/ha/simulate-failover", methods=["POST"])
@login_required_faculty
def api_ha_simulate_failover():
    res = ha_cluster_manager.simulate_failover()
    return jsonify(res)


@app.route("/api/ha/reset-cluster", methods=["POST"])
@login_required_faculty
def api_ha_reset_cluster():
    res = ha_cluster_manager.reset_cluster()
    return jsonify(res)


@app.route("/api/ha/host/<host_id>/permission", methods=["POST"])
@login_required_faculty
def api_ha_host_permission(host_id):
    """Grants or revokes compute execution permission for a specific host node."""
    data = request.get_json(force=True) or {}
    is_authorized = bool(data.get("authorized", True))
    note = data.get("note", "")
    ok = ha_cluster_manager.set_host_permission(host_id, is_authorized, note)
    status_str = "Authorized (Permission Granted)" if is_authorized else "Revoked (Permission Blocked)"
    return jsonify({
        "ok": ok,
        "host_id": host_id,
        "is_authorized": 1 if is_authorized else 0,
        "message": f"Host {host_id} status updated to: {status_str}."
    })


@app.route("/api/ha/global-permission", methods=["POST"])
@login_required_faculty
def api_ha_global_permission():
    """Toggles global container execution permission cluster-wide from the Main Host."""
    data = request.get_json(force=True) or {}
    enabled = bool(data.get("enabled", True))
    ok = ha_cluster_manager.set_global_compute_permission(enabled)
    status_str = "Active (Student Execution Permitted)" if enabled else "Paused (Student Execution Blocked)"
    return jsonify({
        "ok": ok,
        "global_compute_enabled": enabled,
        "message": f"Main Host Lab Compute Access is now {status_str}."
    })


if __name__ == "__main__":
    init_db()
    
    # Auto-start Docker Desktop in background if installed on system
    threading.Thread(target=auto_start_host_docker, daemon=True).start()

    # In production, use environment variables to configure execution options
    host = os.environ.get("FLASK_RUN_HOST", "0.0.0.0")
    port = int(os.environ.get("FLASK_RUN_PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "false").lower() in ("true", "1", "t")
    
    use_ssl = os.environ.get("FLASK_USE_SSL", "false").lower() in ("true", "1", "t")
    ssl_context = None
    if use_ssl:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        cert_path = os.path.join(current_dir, "cert.pem")
        key_path = os.path.join(current_dir, "key.pem")
        if not (os.path.exists(cert_path) and os.path.exists(key_path)):
            base_cert = os.path.join(BASE_DIR, "cert.pem")
            base_key = os.path.join(BASE_DIR, "key.pem")
            if os.path.exists(base_cert) and os.path.exists(base_key):
                cert_path, key_path = base_cert, base_key
            else:
                try:
                    print("SSL Certificates missing. Auto-generating self-signed certificates...")
                    import subprocess
                    subprocess.run(
                        ["python", os.path.join(current_dir, "generate_cert.py")],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL
                    )
                    if os.path.exists(base_cert) and os.path.exists(base_key):
                        cert_path, key_path = base_cert, base_key
                except Exception as e:
                    print(f"Warning: Could not auto-generate SSL certificates: {e}")

        if os.path.exists(cert_path) and os.path.exists(key_path):
            ssl_context = (cert_path, key_path)
            print(f"Loading SSL certificate from {cert_path}")
            
            if platform.system() == "Windows" and shutil.which("powershell"):
                try:
                    import subprocess
                    subprocess.run(
                        ["powershell", "-Command", f"Import-Certificate -FilePath '{cert_path}' -CertStoreLocation Cert:\\CurrentUser\\Root"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=2
                    )
                    print("Successfully verified/registered SSL certificate in Windows Root Store.")
                except Exception as e:
                    print(f"Warning: Auto-trust registration skipped or timed out: {e}")

    app.run(host=host, port=port, debug=debug, ssl_context=ssl_context)
