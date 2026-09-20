# 🐳 V-Lab Two-Node High-Availability Deployment & Demo Guide (Tuesday Lab)

This guide provides step-by-step instructions for deploying and demonstrating the **Two-Node High-Availability (HA) Container Execution System** in your college physical computer lab.

---

## 1. 🎯 Architecture & Separation of Concerns

```text
                    CLOUD / DOCKER HUB
              ┌─────────────────────────────┐
              │  PostgreSQL   │  Docker Hub │
              │  (Metadata)   │  (Images)   │
              └───────────────┬─────────────┘
                              │
                         College LAN
                              │
              ┌───────────────┴───────────────┐
              ▼                               ▼
       ┌──────────────┐                ┌──────────────┐
       │    HOST 1    │◄──────────────►│    HOST 2    │
       │   PRIMARY    │   Workspace    │ HOT STANDBY  │
       │              │  Replication   │              │
       │ Docker       │                │ Docker       │
       │ Containers   │                │ Pre-cached   │
       │ Workspace    │                │ Workspace    │
       └──────────────┘                └──────────────┘
```

### The 4 Core Architectural Roles:
1. **Docker Hub = "What environment should I run?"**:
   * Pre-builds and distributes immutable lab environments (`nginx:alpine`, `python:3.10-alpine`, `hello-world`).
   * Both Host 1 and Host 2 pull the same image definitions from Docker Hub.
   * Student code is **never** pushed to Docker Hub.
2. **Host Filesystem (`workspaces/<student_id>`) = "What has the student actually created?"**:
   * Student files (`index.html`, `app.py`, `Dockerfile`) are stored on the host filesystem and mounted/built locally.
   * Workspaces are continuously synced between Host 1 and Host 2 so failover is instantaneous.
3. **Containers are Ephemeral / Disposable**:
   * You don't preserve the container itself — you preserve the **environment definition** (Docker Hub) + **persistent student data** (Workspace replica).
4. **PostgreSQL / Central Database = "What does the VLab platform know?"**:
   * Student rosters, credentials, lab progression, issue tickets, and cluster health telemetry.

---

## 2. ⚡ One Single Point of Access (`start.bat`)

To ensure **zero setup friction, zero confusion, and no scattered scripts**:
* You only need **one single file** on both machines: [`start.bat`](file:///c:/Users/Rishabh%20Kankariya/Documents/Container_based_application_development_Virtual_labs/start.bat)
* The launcher handles everything automatically:
  1. Auto-detects Python (or `venv` with zero prompts).
  2. Auto-detects and launches **Docker Desktop** if not already running.
  3. Displays your physical machine's **Local LAN IP** on screen automatically.
  4. Provides a clean 1-click menu:
     - `[1]` Run as **Host 1 (Primary)** -> Starts Web Controller + Host 1 Compute Agent automatically.
     - `[2]` Run as **Host 2 (Hot Standby)** -> Connects to Host 1 and warms up standby images.
     - `[3]` Run **Local Simulation / Demo** -> Starts Controller + Host 1 + Host 2 all on one PC.
  5. The Python Agent itself contains standard-library fallbacks so it **never crashes even if third-party packages are missing**.

---

## 3. 🚀 Step-by-Step Lab Setup on Tuesday Morning

### Scenario A: Two Physical Lab PCs (LAN Setup)

1. **On Host 1 (Primary Node / Main Lab PC):**
   - Double-click [`start.bat`](file:///c:/Users/Rishabh%20Kankariya/Documents/Container_based_application_development_Virtual_labs/start.bat).
   - Press **`1`** (or press Enter to accept the default).
   - Both the Central Controller (Port 5000) and Host 1 Compute Agent (Port 5051) start up!
   - Note the **LAN IP** printed on the screen (e.g. `http://192.168.1.101:5000`).

2. **On Host 2 (Hot Standby Node / Second Lab PC):**
   - Copy the project folder to Host 2.
   - Double-click [`start.bat`](file:///c:/Users/Rishabh%20Kankariya/Documents/Container_based_application_development_Virtual_labs/start.bat).
   - Press **`2`** (Host 2 Hot Standby).
   - Enter Host 1's URL: `http://192.168.1.101:5000` (it will save this URL, so subsequent clicks are 100% instant!).
   - Host 2 connects, registers as `HOT_STANDBY`, and pre-pulls Docker images in the background!

3. **Verify on Faculty Dashboard:**
   - Open browser: `http://localhost:5000/faculty/dashboard` (or `http://192.168.1.101:5000/faculty/dashboard`).
   - Confirm both Host 1 and Host 2 display green `ONLINE` status.

---

### Scenario B: Single-Machine Local Testing (Simulation Mode)

You can also test the entire two-node cluster and failover on a single PC/laptop right now before Tuesday:

1. Double-click [`start.bat`](file:///c:/Users/Rishabh%20Kankariya/Documents/Container_based_application_development_Virtual_labs/start.bat).
2. Press **`3`** (Local Simulation / Demo).
3. Host 1 Agent, Host 2 Agent, and Controller all launch automatically!
4. Open `http://localhost:5000/faculty/dashboard` to see both nodes running side-by-side.

---

## 3. 🧪 Step-by-Step Live Demonstration Script for Faculty / "Sir"

### Demo 1: Normal Container Execution on Host 1 (Primary)
1. Log in as student (e.g. `STU001` or create student from faculty dashboard).
2. Open **Lab 2 (Web App Containerization)**.
3. In the terminal, run: `docker build -t hello-web .` and `docker run -d -p 8080:80 hello-web`.
4. Switch to the Faculty Dashboard:
   - Notice **Host 1** active container count increases.
   - Host 1 CPU & RAM meters reflect the live build.

---

### Demo 2: Simulating Physical Host 1 Failure (Automatic Failover)
1. While the student is active, simulate Host 1 failure:
   - **Method 1 (UI button):** Click the red **"SIMULATE HOST 1 FAILOVER"** button on the Faculty Dashboard.
   - **Method 2 (Physical reality):** Close the Host 1 Agent terminal window (or unplug the Ethernet cable).
2. **Watch the Controller & Dashboard:**
   - Within 3 missed heartbeats (9 seconds), Controller detects Host 1 is unresponsive.
   - Host 1 status turns 🔴 `OFFLINE`.
   - Host 2 status instantly turns 🟢 `PRIMARY (ACTIVE)`.
   - Faculty Dashboard displays a prominent Yellow Failover Banner.
3. **Student Perspective (Zero Data Loss & SweetAlert):**
   - In the student terminal, type: `docker ps`.
   - SweetAlert2 pops up:
     > **🔄 Node Failover Active**
     > *Host 1 became unreachable. Your session and workspace files have been safely restored on Host 2 (Hot Standby).*
   - The command completes immediately on Host 2!
   - The student did **not** have to re-enter code or re-upload files.

---

### Demo 3: Resource-Aware Scheduling (Spillover Load Balancing)
1. When Host 1 CPU reaches above 85% (or container count exceeds limit):
2. The Controller scheduler detects the threshold breach.
3. New student command sessions are automatically routed to **Host 2 (Standby)** to prevent Host 1 from crashing.
4. The student receives an informative toast:
   > **⚖️ Load Balancing Active**
   > *Primary host is near capacity. Workload scheduled on Host 2.*

---

### Demo 4: Recovery of Host 1
1. Restart the Host 1 Agent terminal: `run_agent_host1.bat`.
2. Host 1 reconnects and registers as `HOT_STANDBY`.
3. To reset default roles, click **"RESET ROLES"** on the Faculty Dashboard.

---

## 4. 🛡️ Error Diagnostics & Network Failure Handling

The system provides descriptive error codes and SweetAlert modals for all possible issues:

| Error Code | Root Cause | Self-Healing / Action Taken |
| :--- | :--- | :--- |
| `[ERR_HOST_UNREACHABLE]` | LAN cable disconnected or Host Agent closed. | Automatically promoted Hot Standby node. Workspace restored from central database snapshot. |
| `[ERR_DOCKER_DAEMON_DOWN]` | Docker Desktop stopped on the physical host. | Alert notifies operator; scheduler shifts tasks to node with active Docker engine. |
| `[AUTO_HEAL_PORT_CLASH]` | Port 8080/5000 was occupied by an old container. | Backend auto-detected clash, forcefully removed the stale container, and successfully retried execution. |
| `[WARN_CAPACITY_LIMIT]` | Node CPU/RAM exceeded 85%. | Scheduler automatically spilled subsequent workloads to Hot Standby node. |

---

## 5. 🗄️ Database: Local SQLite vs. Cloud PostgreSQL

* **Default (Local Lab):** Uses `database/vlab.db` (SQLite) with 0 configuration.
* **Cloud Access (Anywhere):** Set the environment variable before starting the controller:
  ```powershell
  $env:DATABASE_URL="postgresql://user:password@ep-xyz.neon.tech/vlab?sslmode=require"
  .\run_controller.bat
  ```
  The database layer automatically detects PostgreSQL, runs schema migrations, and syncs data to the cloud.
