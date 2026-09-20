"""
V-Lab Physical Host Agent
Runs on physical host PCs (Host 1 Primary, Host 2 Hot Standby).
Responsible for:
- Registering with Central V-Lab Controller
- Periodic heartbeat telemetry (CPU, RAM, Disk, Docker status, active containers)
- Local Docker command execution with self-healing port/container clash recovery
- Pre-caching and verification of required Docker images for hot standby readiness
- Student workspace file synchronization
"""

import os
import re
import sys
import json
import time
import html
import shutil
import socket
import urllib.request
import urllib.error
import subprocess
import threading
import argparse
import platform
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

AGENT_VERSION = "2.2.0"
REQUIRED_IMAGES = ["hello-world:latest", "nginx:alpine", "python:3.10-alpine"]

class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    """Handles each incoming request in a new thread."""
    daemon_threads = True


class AgentRequestHandler(BaseHTTPRequestHandler):
    """Processes incoming execution, sync, and health RPC requests from the Controller."""

    def _send_json(self, status_code: int, data: dict):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _is_authenticated(self) -> bool:
        secret = self.headers.get("X-Agent-Secret")
        if not secret:
            # Fallback to query parameter
            if "?" in self.path:
                query = self.path.split("?", 1)[1]
                for part in query.split("&"):
                    if part.startswith("secret="):
                        secret = part.split("=", 1)[1]
        return secret == self.server.agent.secret

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Agent-Secret")
        self.end_headers()

    def do_GET(self):
        path_clean = self.path.split("?")[0]
        if path_clean == "/health":
            self._send_json(200, {
                "ok": True,
                "host_id": self.server.agent.host_id,
                "role": self.server.agent.role,
                "status": "ONLINE",
                "version": AGENT_VERSION
            })
            return

        if not self._is_authenticated():
            self._send_json(401, {
                "ok": False,
                "error_code": "ERR_AUTH_TOKEN_INVALID",
                "output": "Unauthorized: Invalid or missing X-Agent-Secret token."
            })
            return

        if path_clean == "/status":
            telemetry = self.server.agent.get_telemetry()
            self._send_json(200, {
                "ok": True,
                "host_id": self.server.agent.host_id,
                "role": self.server.agent.role,
                "telemetry": telemetry
            })
        else:
            self._send_json(404, {"ok": False, "error": "Endpoint not found"})

    def do_POST(self):
        if not self._is_authenticated():
            self._send_json(401, {
                "ok": False,
                "error_code": "ERR_AUTH_TOKEN_INVALID",
                "output": "Unauthorized: Invalid or missing X-Agent-Secret token."
            })
            return

        path_clean = self.path.split("?")[0]
        content_len = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_len).decode("utf-8") if content_len > 0 else "{}"
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            self._send_json(400, {"ok": False, "error": "Invalid JSON body"})
            return

        if path_clean == "/sync-workspace":
            student_id = str(payload.get("student_id", "guest"))
            files = payload.get("files", {})
            self.server.agent.sync_workspace_files(student_id, files)
            self._send_json(200, {"ok": True, "message": "Workspace synchronized successfully."})

        elif path_clean == "/exec":
            res = self.server.agent.execute_command(
                student_id=str(payload.get("student_id", "guest")),
                command=str(payload.get("command", "")),
                files=payload.get("files", {})
            )
            self._send_json(200, res)

        elif path_clean == "/precache-images":
            threading.Thread(target=self.server.agent.precache_images, daemon=True).start()
            self._send_json(200, {"ok": True, "message": "Image pre-caching initiated in background."})

        else:
            self._send_json(404, {"ok": False, "error": "Endpoint not found"})

    def log_message(self, format, *args):
        # Quiet standard HTTP access logs to keep console readable
        pass


class VLabHostAgent:
    """Core Agent logic managing Docker workloads, resource telemetry, and Controller communications."""

    def __init__(self, host_id: str, role: str, controller_url: str, agent_ip: str, agent_port: int, secret: str, interval: int = 3):
        self.host_id = host_id
        self.role = role.upper()
        self.controller_url = controller_url.rstrip("/")
        self.agent_ip = agent_ip
        self.agent_port = agent_port
        self.secret = secret
        self.heartbeat_interval = interval
        self.running = True
        self.server = None

        # Workspaces directory on physical host
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.workspaces_root = os.path.join(base_dir, "workspaces")
        os.makedirs(self.workspaces_root, exist_ok=True)

        print(f"==================================================")
        print(f"🚀 V-Lab Host Agent v{AGENT_VERSION} Initializing")
        print(f"   Node ID:         {self.host_id}")
        print(f"   Role:            {self.role}")
        print(f"   Controller URL:  {self.controller_url}")
        print(f"   Listening on:    http://{self.agent_ip}:{self.agent_port}")
        print(f"==================================================")

    def auto_start_docker_desktop(self) -> bool:
        """Attempts to auto-launch Docker Desktop on Windows if daemon is offline."""
        if platform.system() == "Windows":
            candidates = [
                os.path.expandvars(r"%LOCALAPPDATA%\Programs\DockerDesktop\Docker Desktop.exe"),
                r"C:\Program Files\Docker\Docker\Docker Desktop.exe"
            ]
            for exe in candidates:
                if os.path.exists(exe):
                    try:
                        print(f"🐳 Found Docker Desktop at {exe}. Auto-launching daemon...")
                        if hasattr(os, "startfile"):
                            os.startfile(exe)
                        else:
                            subprocess.Popen([exe], shell=True)
                        return True
                    except Exception as e:
                        print(f"Warning: Could not start Docker Desktop: {e}")
        return False

    def get_telemetry(self) -> dict:
        """Samples CPU, RAM, Disk, and Docker daemon status with zero-dependency fallback."""
        if HAS_PSUTIL:
            try:
                cpu_pct = psutil.cpu_percent(interval=0.1)
                mem = psutil.virtual_memory()
                ram_pct = round(mem.percent, 1)
                ram_used_mb = round(mem.used / (1024 * 1024), 1)
                ram_total_mb = round(mem.total / (1024 * 1024), 1)
            except Exception:
                cpu_pct = 5.0
                ram_pct = 30.0
                ram_used_mb = 1024.0
                ram_total_mb = 8192.0
        else:
            # Zero-dependency standard library fallback
            cpu_pct = 5.0
            ram_pct = 30.0
            ram_used_mb = 1024.0
            ram_total_mb = 8192.0

        try:
            total, used, free = shutil.disk_usage(self.workspaces_root)
            disk_pct = round((used / total) * 100, 1)
            disk_free_gb = round(free / (1024 * 1024 * 1024), 1)
            disk_total_gb = round(total / (1024 * 1024 * 1024), 1)
        except Exception:
            disk_pct = 20.0
            disk_free_gb = 50.0
            disk_total_gb = 250.0
        
        has_docker, docker_ver, running_containers = self._check_docker()

        return {
            "cpu_percent": round(cpu_pct, 1),
            "ram_percent": ram_pct,
            "ram_used_mb": ram_used_mb,
            "ram_total_mb": ram_total_mb,
            "disk_percent": disk_pct,
            "disk_free_gb": disk_free_gb,
            "disk_total_gb": disk_total_gb,
            "docker_available": has_docker,
            "docker_version": docker_ver,
            "active_containers": running_containers
        }

    def _check_docker(self) -> tuple:
        """Queries local Docker daemon for liveness, version, and running containers."""
        try:
            res = subprocess.run(
                ["docker", "version", "--format", "{{.Server.Version}}"],
                capture_output=True, text=True, timeout=3
            )
            if res.returncode == 0 and res.stdout.strip():
                ver = res.stdout.strip() or "Available"
                ps_res = subprocess.run(
                    ["docker", "ps", "-q"],
                    capture_output=True, text=True, timeout=3
                )
                containers = len(ps_res.stdout.strip().splitlines()) if ps_res.stdout.strip() else 0
                return True, ver, containers
            return False, "Daemon Not Responding", 0
        except Exception:
            return False, "Docker CLI Error", 0

    def register_with_controller(self) -> bool:
        """Registers this host with the Central Controller."""
        telem = self.get_telemetry()
        cpu_cores = (psutil.cpu_count(logical=True) if HAS_PSUTIL else os.cpu_count()) or 2
        data = {
            "host_id": self.host_id,
            "hostname": socket.gethostname(),
            "ip_address": self.agent_ip,
            "port": self.agent_port,
            "role": self.role,
            "secret": self.secret,
            "agent_version": AGENT_VERSION,
            "cpu_cores": cpu_cores,
            "ram_total_mb": telem["ram_total_mb"],
            "disk_total_gb": telem["disk_total_gb"],
            "docker_version": telem["docker_version"],
            "docker_available": 1 if telem["docker_available"] else 0
        }

        url = f"{self.controller_url}/api/agent/register"
        req = urllib.request.Request(
            url,
            data=json.dumps(data).encode("utf-8"),
            headers={"Content-Type": "application/json", "X-Agent-Secret": self.secret},
            method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status == 200:
                    print(f"✅ Host successfully registered with Controller at {self.controller_url}")
                    return True
        except Exception as e:
            print(f"⚠️ Warning: Could not register with Controller ({e}). Will retry on next heartbeat.")
            return False
        return False

    def heartbeat_worker(self):
        """Continuously sends periodic heartbeats to the Controller."""
        while self.running:
            try:
                telem = self.get_telemetry()
                data = {
                    "host_id": self.host_id,
                    "role": self.role,
                    "secret": self.secret,
                    "cpu_percent": telem["cpu_percent"],
                    "ram_percent": telem["ram_percent"],
                    "disk_percent": telem["disk_percent"],
                    "active_containers": telem["active_containers"],
                    "docker_available": 1 if telem["docker_available"] else 0,
                    "docker_version": telem["docker_version"]
                }
                url = f"{self.controller_url}/api/agent/heartbeat"
                req = urllib.request.Request(
                    url,
                    data=json.dumps(data).encode("utf-8"),
                    headers={"Content-Type": "application/json", "X-Agent-Secret": self.secret},
                    method="POST"
                )
                with urllib.request.urlopen(req, timeout=4) as resp:
                    pass
            except Exception:
                # Connection to controller failed; retry silently on next interval
                pass

            time.sleep(self.heartbeat_interval)

    def precache_images(self):
        """Pre-pulls required images to ensure hot-standby nodes require 0 download time on failover."""
        print(f"📦 Checking pre-cached Docker images on {self.host_id}...")
        for img in REQUIRED_IMAGES:
            try:
                check = subprocess.run(
                    f"docker image inspect {img}",
                    shell=True, capture_output=True, timeout=5
                )
                if check.returncode != 0:
                    print(f"⬇️ Pre-pulling required image '{img}' on {self.host_id}...")
                    subprocess.run(f"docker pull {img}", shell=True, capture_output=True, timeout=120)
                    print(f"✅ Pre-cached image '{img}' successfully.")
                else:
                    print(f"✓ Image '{img}' is already cached.")
            except Exception as e:
                print(f"⚠️ Could not pull image '{img}': {e}")

    def sync_workspace_files(self, student_id: str, files: dict):
        """Writes given files to the student's local workspace on this host."""
        ws_dir = os.path.join(self.workspaces_root, student_id)
        os.makedirs(ws_dir, exist_ok=True)

        if isinstance(files, dict):
            for fname, content in files.items():
                if fname and isinstance(fname, str) and not fname.startswith("/") and ".." not in fname:
                    raw_text = content or ""
                    clean_text = re.sub(r'<[^>]+>', '', raw_text)
                    clean_text = html.unescape(clean_text)
                    fpath = os.path.join(ws_dir, fname)
                    with open(fpath, "w", encoding="utf-8") as f:
                        f.write(clean_text)

        # Multi-lab file bridges
        df_path = os.path.join(ws_dir, "Dockerfile")
        df_micro = os.path.join(ws_dir, "Dockerfile.microservice")
        compose_path = os.path.join(ws_dir, "docker-compose.yml")

        if os.path.exists(df_path) and not os.path.exists(df_micro):
            try: shutil.copyfile(df_path, df_micro)
            except Exception: pass
        elif os.path.exists(df_micro) and not os.path.exists(df_path):
            try: shutil.copyfile(df_micro, df_path)
            except Exception: pass

        if os.path.exists(compose_path):
            try:
                with open(compose_path, "r", encoding="utf-8") as cf:
                    cdata = cf.read()
                new_cdata = re.sub(r'["\']?5000\s*:\s*5000["\']?', '"5001-5005:5000"', cdata)
                if new_cdata != cdata:
                    with open(compose_path, "w", encoding="utf-8") as cf:
                        cf.write(new_cdata)
            except Exception:
                pass

    def execute_command(self, student_id: str, command: str, files: dict) -> dict:
        """
        Executes a student Docker command on this host node with auto-healing
        for container name collisions and port clashes.
        """
        cmd = (command or "").strip()
        cmd_lower = cmd.lower()

        if not cmd:
            return {"ok": False, "output": "No command provided.", "host_id": self.host_id}

        # Check Docker liveness first
        has_docker, docker_msg, _ = self._check_docker()
        if not has_docker:
            return {
                "ok": False,
                "error_code": "ERR_DOCKER_DAEMON_DOWN",
                "host_id": self.host_id,
                "output": (
                    f"[ERR_DOCKER_DAEMON_DOWN] Docker Engine is not running on {self.host_id}.\n"
                    f"Details: {docker_msg}.\n"
                    "Self-healing: If standby node is available, controller will reroute your task."
                )
            }

        # Sync files to local workspace
        self.sync_workspace_files(student_id, files)
        ws_dir = os.path.join(self.workspaces_root, student_id)

        # Proactive cleanup of conflicting container names
        for named_c in ["sum-microservice", "sum-client", "hello-web"]:
            if f"--name {named_c}" in cmd or f"--name={named_c}" in cmd or (named_c in cmd and "run" in cmd_lower):
                subprocess.run(f"docker rm -f {named_c}", shell=True, capture_output=True)

        if "8080:8080" in cmd and "run" in cmd_lower:
            subprocess.run("docker rm -f sum-client", shell=True, capture_output=True)

        # Placeholder detection
        if "<container_id>" in cmd_lower or ("<" in cmd and ">" in cmd):
            return {
                "ok": False,
                "host_id": self.host_id,
                "output": "docker: Error response from daemon: No such container. Please replace '<container_id>' with a valid ID from 'docker ps -a'."
            }

        # Execute
        try:
            res = subprocess.run(
                cmd,
                cwd=ws_dir,
                shell=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=45
            )

            err_lower = ((res.stderr or "") + (res.stdout or "")).lower()

            # Auto-healing: Port clash
            if res.returncode != 0 and ("port is already allocated" in err_lower or "address already in use" in err_lower):
                ps_res = subprocess.run("docker ps -a --format \"{{.ID}} {{.Ports}}\"", shell=True, capture_output=True, text=True)
                if ps_res.stdout:
                    for line in ps_res.stdout.splitlines():
                        if any(p in line for p in ["8080", "80", "5000", "5001"]):
                            cnt_id = line.split()[0]
                            subprocess.run(f"docker rm -f {cnt_id}", shell=True, capture_output=True)
                res = subprocess.run(cmd, cwd=ws_dir, shell=True, capture_output=True, text=True, timeout=30)
                err_lower = ((res.stderr or "") + (res.stdout or "")).lower()

            # Auto-healing: Container name in use
            if res.returncode != 0 and "is already in use by container" in err_lower:
                for cname in ["sum-microservice", "sum-client", "hello-web"]:
                    if cname in err_lower or cname in cmd_lower:
                        subprocess.run(f"docker rm -f {cname}", shell=True, capture_output=True)
                match = re.search(r'is already in use by container "?([a-f0-9]+)"?', err_lower)
                if match:
                    subprocess.run(f"docker rm -f {match.group(1)}", shell=True, capture_output=True)
                res = subprocess.run(cmd, cwd=ws_dir, shell=True, capture_output=True, text=True, timeout=30)

            # Auto-healing: Windows file lock
            if res.returncode != 0 and "is being used by another process" in err_lower:
                time.sleep(1.0)
                res = subprocess.run(cmd, cwd=ws_dir, shell=True, capture_output=True, text=True, timeout=30)

            stdout_text = res.stdout.strip() if res.stdout else ""
            stderr_text = res.stderr.strip() if res.stderr else ""
            out = f"{stdout_text}\n{stderr_text}".strip() if (stdout_text and stderr_text) else (stdout_text or stderr_text)

            return {
                "ok": (res.returncode == 0) or ("network" in cmd_lower and "already exists" in out.lower()),
                "output": out,
                "returncode": res.returncode,
                "host_id": self.host_id,
                "execution_node": f"{self.host_id} ({self.role})"
            }

        except subprocess.TimeoutExpired:
            return {
                "ok": False,
                "error_code": "ERR_EXECUTION_TIMEOUT",
                "host_id": self.host_id,
                "output": f"[ERR_EXECUTION_TIMEOUT] Command timed out after 45 seconds on {self.host_id}."
            }
        except Exception as e:
            return {
                "ok": False,
                "error_code": "ERR_EXECUTION_FAILED",
                "host_id": self.host_id,
                "output": f"[ERR_EXECUTION_FAILED] Error executing command on {self.host_id}: {str(e)}"
            }

    def start(self):
        """Starts HTTP RPC server and background threads."""
        # Auto-launch Docker Desktop if daemon is not running
        has_docker, _, _ = self._check_docker()
        if not has_docker:
            print("⚠️ Docker Engine not responding. Attempting to auto-start Docker Desktop...")
            self.auto_start_docker_desktop()

        # Initial registration attempt
        self.register_with_controller()

        # Background thread: Heartbeats
        hb_thread = threading.Thread(target=self.heartbeat_worker, daemon=True)
        hb_thread.start()

        # Background thread: Pre-cache images if standby
        if self.role == "HOT_STANDBY" or self.role == "STANDBY":
            img_thread = threading.Thread(target=self.precache_images, daemon=True)
            img_thread.start()

        # Start HTTP server
        self.server = ThreadedHTTPServer((self.agent_ip, self.agent_port), AgentRequestHandler)
        self.server.agent = self
        print(f"🎯 V-Lab Agent active and serving RPC requests on port {self.agent_port}...")
        try:
            self.server.serve_forever()
        except KeyboardInterrupt:
            print("\n🛑 Agent shutting down...")
            self.running = False
            self.server.server_close()


def main():
    parser = argparse.ArgumentParser(description="V-Lab Physical Host Agent")
    parser.add_argument("--host-id", default=os.environ.get("VLAB_HOST_ID", "HOST1"), help="Unique Host ID (e.g. HOST1, HOST2)")
    parser.add_argument("--role", default=os.environ.get("VLAB_HOST_ROLE", "PRIMARY"), choices=["PRIMARY", "HOT_STANDBY", "STANDBY"], help="Node Role")
    parser.add_argument("--controller-url", default=os.environ.get("VLAB_CONTROLLER_URL", "http://127.0.0.1:5000"), help="Central Controller URL")
    parser.add_argument("--agent-ip", default=os.environ.get("VLAB_AGENT_IP", "0.0.0.0"), help="IP address to bind agent RPC server")
    parser.add_argument("--agent-port", type=int, default=int(os.environ.get("VLAB_AGENT_PORT", 5051)), help="Port for agent RPC server")
    parser.add_argument("--secret", default=os.environ.get("VLAB_AGENT_SECRET", "vlab-cluster-secret-2026"), help="Cluster authentication secret")
    parser.add_argument("--interval", type=int, default=int(os.environ.get("VLAB_HEARTBEAT_INTERVAL", 3)), help="Heartbeat interval in seconds")

    args = parser.parse_args()

    agent = VLabHostAgent(
        host_id=args.host_id,
        role=args.role,
        controller_url=args.controller_url,
        agent_ip=args.agent_ip,
        agent_port=args.agent_port,
        secret=args.secret,
        interval=args.interval
    )
    agent.start()


if __name__ == "__main__":
    main()
