# Container-Based Application Development Virtual Labs (V-Lab)
## Full System Architecture & Engineering Specification

**Active Branch:** `Rishabh's-Branch`<br>
**System Classification:** Local-First, Offline-Capable Distributed Virtual Lab with Asynchronous Cloud Synchronization & High-Availability Clustering.

---

## 1. Executive Summary & Core Architectural Principle

The **Container-Based Application Development Virtual Labs (V-Lab)** platform enables students to perform hands-on containerization and Docker development exercises within an interactive web-based IDE (featuring file exploration, code editing, terminal execution, step-by-step lab worksheets, and automated verification).

### The Primary Operating Axiom:
> **Local LAN Autonomy First:** The virtual lab platform must function with 100% feature parity inside a college physical laboratory environment over a local LAN switch/Wi-Fi router, even when the internet, external cloud databases, or wide-area networks are completely disconnected.

Docker containers run on physical host machines possessing real virtualization hardware (Docker Engine / WSL2 / Linux Kernel), **never inside the student's browser**. The cloud serves strictly as an **asynchronous synchronization target, persistent audit repository, and disaster-recovery backup**, not as a runtime dependency.

---

## 2. High-Level System Topology

```text
 ┌─────────────────────────────────────────────────────────────────────────────────┐
 │                            STUDENT & FACULTY CLIENTS                            │
 │  • Standard Web Browser (Chrome, Edge, Firefox)                                 │
 │  • Client-Side Connectivity Engine (Heartbeat polling, exponential backoff)     │
 │  • Interactive Workspace UI (Monaco/VS Code editor, xterm terminal emulator)    │
 └────────────────────────────────────────┬────────────────────────────────────────┘
                                          │
                  College LAN / HTTP(S)   │ (e.g., http://192.168.1.101:5000)
                                          ▼
 ┌─────────────────────────────────────────────────────────────────────────────────┐
 │                       LOCAL V-LAB HIGH-AVAILABILITY CLUSTER                     │
 │                                                                                 │
 │   ┌───────────────────────────────────┐     ┌────────────────────────────────┐  │
 │   │      HOST 1 — PRIMARY (ACTIVE)    │     │    HOST 2 — HOT STANDBY NODE   │  │
 │   │  • Flask Controller & REST APIs   │     │  • Node Compute Agent          │  │
 │   │  • Local SQLite (database/vlab.db)│◄───►│  • Docker Engine               │  │
 │   │  • Docker Engine (Local Daemon)   │ HA  │  • Replicated Workspaces       │  │
 │   │  • Student Workspaces (Disk)      │Sync │  • Pre-warmed Lab Docker Images│  │
 │   │  • Transactional Sync Outbox      │     │  • Heartbeat Reporter          │  │
 │   └─────────────────┬─────────────────┘     └────────────────────────────────┘  │
 └─────────────────────┼───────────────────────────────────────────────────────────┘
                       │
       Asynchronous    │ When Internet / Cloud Reachable
       Transactional   │ (Non-blocking background sync worker)
       Outbox Stream   ▼
 ┌─────────────────────────────────────────────────────────────────────────────────┐
 │                          OPTIONAL CLOUD SERVICES PLATFORM                       │
 │  • Managed PostgreSQL (Neon / Supabase / AWS RDS): Centralized student records, │
 │    idempotent sync event ingestion, and aggregated faculty analytics            │
 │  • Cloud Object Storage: Compressed workspace archives & database snapshots     │
 │  • Container Registry (Docker Hub): Versioned base images (nginx:alpine, etc.)  │
 └─────────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Component Architecture & Repository Organization

| Component Layer | Primary Path / Module | Core Responsibilities |
| :--- | :--- | :--- |
| **Presentation Layer** | `frontend/templates/`, `frontend/static/` | UI views for student/faculty dashboards, worksheets, code editor, terminal, live connectivity badge. |
| **Client Engine** | `frontend/static/js/connectivity.js` | Heartbeat health monitoring (`/api/health`), retry with exponential backoff & jitter, status pill. |
| **Web Application** | `backend/app.py` | Flask application server, session management, access control, lab worksheets, task dispatch. |
| **Data Access & Storage** | `backend/db.py` | Unified SQLite/PostgreSQL adapter, local schema migration, transactional outbox queue. |
| **Cloud Sync Worker** | `backend/services/sync_service.py` | Background daemon worker, strict non-fallback PostgreSQL connection, idempotent merge engine. |
| **HA Cluster Controller** | `backend/ha_manager.py` | Node registration, heartbeat tracking, spillover scheduling, failover orchestrator. |
| **Compute Node Agent** | `agent/vlab_agent.py` | Host daemon reporting system telemetry and executing local Docker operations safely. |
| **Workspace Storage** | `backend/workspaces/<student_id>/` | Physical source files (`index.html`, `Dockerfile`, `app.py`) bind-mounted into student containers. |

---

## 4. Multi-Tier Data Architecture & Ownership

To prevent split-brain data corruption across distributed nodes, each data domain has a strictly defined operational owner:

| Data Entity | Authoritative Operational Owner | Replication & Synchronization Strategy |
| :--- | :--- | :--- |
| **Student Credentials & Profiles** | Active Host Database (`vlab.db`) | Hashed passwords, encrypted session cookies, synced to cloud PostgreSQL. |
| **Lab Progress (`lab_progress`)** | Active Host Database (`vlab.db`) | Atomically queued into `sync_outbox`; asynchronously synced via monotonic set union. |
| **Lab Verification Status** | Active Host Backend Engine | Awarded **only** when backend verification passes; never inferred from step unions. |
| **Student Workspace Code** | Active Host Physical Disk | Dual-layer persistence: physical disk write + `student_workspaces` DB snapshot. |
| **Cluster Telemetry & Health** | HA Manager Memory + `hosts` table | Time-series heartbeats (`host_heartbeats`), failover audit logs (`failover_events`). |
| **Outbox Queue (`sync_outbox`)** | Active Host Database (`vlab.db`) | Transactionally tied to local mutations; deleted/marked `SYNCED` only after cloud ack. |
| **Docker Images & Containers** | Local Host Docker Daemon | Pre-pulled image cache; ephemeral containers tied to student workspaces. |

---

## 5. The Transactional Outbox Pattern & Cloud Sync Engine

A central flaw in naive dual-database systems is the "dual-write hazard": saving locally and making an asynchronous network call can fail midway, creating silent divergence between SQLite and PostgreSQL.

### 5.1 Atomic Local Dual-Write
Every mutation to student progress in `backend/app.py` writes to `lab_progress` **and** inserts an outbox event into `sync_outbox` in the **exact same SQLite transaction**:

```text
[HTTP POST /api/lab1/step]
          │
          ▼
   BEGIN TRANSACTION
     ├── 1. UPDATE lab_progress SET steps_completed = ...
     └── 2. INSERT INTO sync_outbox (operation_id, entity_type, payload, status='PENDING')
   COMMIT TRANSACTION
```
*If either write fails, the entire transaction rolls back.*

### 5.2 Strict Cloud Connection (`get_cloud_db()`)
* **Local Web Requests (`get_db()`):** Uses SQLite (`database/vlab.db`) locally.
* **Cloud Sync Worker (`get_cloud_db()`):** Connects **strictly** to the remote PostgreSQL instance using `DATABASE_URL`. If the cloud database is unreachable, it returns `None` or raises an error—**it never silently falls back to SQLite**.

### 5.3 Idempotency & Monotonic Merge Rules in PostgreSQL
1. **Deduplication:** Cloud PostgreSQL maintains a `processed_sync_events` table indexed by `operation_id`. If a network interruption drops the server's acknowledgement and the client retries, PostgreSQL detects the duplicate `operation_id` and ignores the mutation.
2. **Monotonic Set Union:** Step lists are merged using set union:
   $$\text{Final Steps} = \text{Steps}_{\text{Cloud}} \cup \text{Steps}_{\text{Local}}$$
   Offline work cannot accidentally delete or uncheck previously completed steps.
3. **Verification Protection:** `verification_passed` is merged with logical OR ($\max(v_{\text{cloud}}, v_{\text{local}})$). Step completion never implies verified completion.

---

## 6. High-Availability (HA) Two-Node Clustering

The cluster architecture consists of **Host 1 (Primary)** and **Host 2 (Hot Standby)** running via `start.bat`:

1. **Heartbeat Protocol:** Host 2 sends heartbeats to Host 1 every 3 seconds.
2. **Failure Detection Threshold:** Host 1 is declared unresponsive only after **3 consecutive missed heartbeats (9–15 seconds)**.
3. **Docker Pre-Warming:** Host 2 regularly pulls required base images (`nginx:alpine`, `hello-world`, `python:3.10-alpine`) so failover execution incurs zero image-pull latency.
4. **Workspace Snapshot Restoration:** In the event of failover, Host 2 materializes student code from the latest database snapshot in `student_workspaces`.
5. **Split-Brain & Non-Preemption Guard:** When Host 1 recovers from a network partition, it rejoins as `HOT_STANDBY`. It **never** preemptively takes back the Primary role until the faculty administrator explicitly clicks **"Reset Roles"** in the Faculty Dashboard.

---

## 7. Client Connectivity State Machine & Multi-Worker Architecture

The student navigation bar provides continuous, transparent feedback regarding cluster connectivity across 4 distinct states:

```text
   ┌─────────────────────────────────────────────────────────────────────────────────┐
   │ 🟢 Online & Synced     │ Local host connected, cloud reachable, 0 pending      │
   │ 🟡 Syncing (N pending) │ Changes queued in outbox, actively uploading to cloud  │
   │ 🔵 Local LAN Mode      │ Connected to college Wi-Fi/LAN, cloud offline (SQLite) │
   │ 🔴 Host Unreachable    │ Wi-Fi/LAN connection to physical lab server dropped    │
   └─────────────────────────────────────────────────────────────────────────────────┘
```

* **Heartbeat Interval:** `/api/health` queried with exponential backoff (5s–30s) and jitter.
* **Manual Override:** A persistent **"🔄 Sync Now"** button allows authenticated users to trigger synchronization on demand (protected by session auth and a 5-second cooldown rate limit).
* **Multi-Worker Server Support:**
  * **Cooperative File Lease (`ProcessWorkerLock`):** Built-in non-blocking file lease lock (`database/.sync_worker.lock`) guarantees that only a single worker process runs the polling loop under multi-worker WSGI servers (e.g. `gunicorn -w 4`), completely eliminating SQLite contention.
  * **Dedicated Daemon Mode:** Operators can set `ENABLE_SYNC_WORKER=false` on web nodes and run `python -m backend.services.sync_service` as a dedicated background service.

---

## 8. Implementation Status & Feature Roadmap

| Feature / Module | Status | Notes |
| :--- | :---: | :--- |
| **Host 1 Controller (`app.py`, `start.bat`)** | ✅ **Implemented** | Stable local web server, student roster, lab worksheets. |
| **Unified DB Adapter (`backend/db.py`)** | ✅ **Implemented** | SQLite default with parameter translation and migrations. |
| **Local Physical Workspaces (`workspaces/`)** | ✅ **Implemented** | Disk persistence + DB backup sync with realpath boundary protection. |
| **Transactional Outbox (`sync_outbox`)** | ✅ **Implemented** | Atomic dual-write wired across all 13 lab progress endpoints. |
| **Strict Cloud Connection (`get_cloud_db`)** | ✅ **Implemented** | Dedicated non-fallback PostgreSQL connector with 5s timeout. |
| **Cloud Sync Worker (`sync_service.py`)** | ✅ **Implemented** | Multi-worker leader lease lock, monotonic set union & deduplication. |
| **Health API & Navbar Badge (`/api/health`)** | ✅ **Implemented** | Real-time diagnostic telemetry, 4-state indicator & manual sync. |
| **Security Hardening (`_get_or_create_flask_secret`)** | ✅ **Implemented** | Persistent random token, prod fail-closed guard, container limits. |
| **HA Heartbeat Monitoring & Failover** | ✅ **Implemented** | 3-miss failure threshold, resource scheduling, and standby promotion. |
| **Browser IndexedDB Offline Queue** | ⏳ **Phase 5** | Client-side service worker cache for offline editor drafts. |
