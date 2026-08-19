# Container-Based Application Development Virtual Labs

Welcome to the **Container-Based Application Development Virtual Labs** project. This project serves as an interactive virtual learning environment (Virtual Labs) for students to learn containerization concepts using Docker. 

This repository is organized so that different teammates can work on separate lab modules by branching off `main`. 

---

## 🛠️ Technology Stack

- **Backend**: Python 3, Flask framework (WSGI-compatible)
- **Frontend**: HTML5, Vanilla CSS (using curated custom designs, no ad-hoc Tailwind bloat), JavaScript
- **Database**: SQLite3 (persistent file-based relational database)
- **Server Telemetry / System Access**: `psutil` (for system metrics), Python `subprocess` (for executing sandboxed docker instructions)

---

## 📁 Repository Structure

```text
vlab-module1-docker/
├── .gitignore              # Configured Git tracking exclusions (pycache, env, etc.)
├── README.md               # Collaborator documentation for your teammates
├── database/
│   └── vlab.db             # SQLite local database (generated on launch)
├── backend/
│   ├── app.py              # Flask Core Backend (Routing, DB Logic, Task Engine)
│   ├── generate_cert.py    # SSL Self-Signed Certificate Generator
│   └── requirements.txt    # Production-grade Python Dependencies
├── deploy_gpo.ps1          # Active Directory GPO SSL Deployment Script
└── frontend/
    ├── static/
    │   ├── css/
    │   │   └── style.css   # Core styling & UI Theme (Light & Dark modes)
    │   └── images/
    │       └── logo.png    # Institution / Lab Branding Logo
    └── templates/          # HTML layout and dashboard templates
        ├── base.html
        ├── login.html
        ├── student_dashboard.html
        ├── faculty_dashboard.html
        ├── lab1.html       # Lab 1: Docker Desktop Installation
        ├── lab2.html       # Lab 2: Nginx Web App Containerization
        ├── lab3.html       # Lab 3: Run hello-world Container & Verify Application
        ├── lab4.html       # Lab 4: CI/CD Pipeline & Docker Registry
        └── change_password.html
```

---

## 🗄️ Database Schema (SQLite)

The database initializes automatically inside the `database/vlab.db` file. The following tables are created during initialization:

1. **`faculty`**: Handles educator credentials.
   - `id` (INTEGER, Primary Key)
   - `username` (TEXT, Unique)
   - `password_hash` (TEXT)
   - `password_changed` (INTEGER, Flag)

2. **`students`**: Stores student profiles and credentials.
   - `id` (INTEGER, Primary Key)
   - `student_id` (TEXT, Unique, formatted as `STUxxx`)
   - `name` (TEXT)
   - `email` (TEXT)
   - `default_password` (TEXT)
   - `password_hash` (TEXT)
   - `password_changed` (INTEGER, Flag)
   - `created_at` (TEXT)

3. **`lab_progress`**: Tracks progress on a per-step basis for different labs.
   - `id` (INTEGER, Primary Key)
   - `student_id` (TEXT, Foreign Key)
   - `lab_number` (INTEGER)
   - `steps_completed` (TEXT, JSON array of completed steps)
   - `verification_passed` (INTEGER, Flag)
   - `completed_at` (TEXT)
   - `updated_at` (TEXT)

4. **`issue_tickets`**: Supports student ticket submittals to faculty.
   - `id` (INTEGER, Primary Key)
   - `student_id` (TEXT)
   - `student_name` (TEXT)
   - `lab_number` (INTEGER)
   - `title` (TEXT)
   - `description` (TEXT)
   - `status` (TEXT, e.g., `OPEN` / `CLOSED`)
   - `faculty_reply` (TEXT)
   - `created_at` (TEXT)

5. **`lab_videos`**: Stores lecture video endpoints added by faculty.
   - `id` (INTEGER, Primary Key)
   - `lab_number` (INTEGER, Unique)
   - `video_title` (TEXT)
   - `video_url` (TEXT)
   - `uploaded_at` (TEXT)

---

## 🔌 API Endpoints & Routes

The application features the following routing layers:

### Page Routes
- `/login` (GET/POST): Unified login page for both faculty and students.
- `/logout` (GET): Destroy user session.
- `/change-password` (GET/POST): Redirected here if standard passwords are unchanged.
- `/dashboard` (GET): Redirects to either `/student/dashboard` or `/faculty/dashboard` depending on active session role.
- `/lab/<int:lab_number>` (GET): Dynamic loader for specific lab worksheets (e.g. Lab 1, Lab 2).

### API Endpoints
- **System Diagnostics**:
  - `GET /api/system-check`: Runs real hardware specification diagnostics on the host machine.
  - `GET /api/system-telemetry`: Retrieves live CPU usage, RAM levels, and Disk info.
- **Docker Actions & Tasks**:
  - `POST /api/verify-docker-real`: Validates local host installation state of Docker daemon.
  - `POST /api/run-command`: Initiates a background container orchestration subprocess command.
  - `GET /api/task-status/<task_id>`: Monitors active streaming log output, RAM usage, and return status of a container task.
  - `POST /api/kill-task/<task_id>`: Safely interrupts a running command/process.
- **Progress Tracking**:
  - `POST /api/save-progress`: Saves completed step IDs for a specific student and lab.
- **Resource Media**:
  - `POST /api/admin/upload-video`: Updates reference lecture links for the labs.
  - `GET /api/lab/video/<int:lab_number>`: Pulls corresponding video metadata.

---

## 🚀 Running the Project

### Prerequisites
Make sure Python 3 is installed.

### Setup Steps
1. Create and activate a virtual environment:
   ```bash
   python -m venv venv
   # On Windows:
   venv\Scripts\activate
   # On Linux/macOS:
   source venv/bin/activate
   ```
2. Install the production-grade dependencies:
   ```bash
   pip install -r backend/requirements.txt
   ```
3. Run the Flask application:
   ```bash
   python backend/app.py
   ```
   *Note: Environment variables such as `FLASK_SECRET_KEY`, `FLASK_RUN_HOST`, `FLASK_RUN_PORT`, and `FLASK_DEBUG` can be customized to change execution parameters.*

## 👥 Collaborator & Teammate Development Guide

To work on new virtual lab worksheets (e.g., Lab 3, Lab 4, etc.) smoothly without code conflicts, follow these guidelines:

### 1. Git Workflow
- Always start by updating your local `main` branch:
  ```bash
  git checkout main
  git pull origin main
  ```
- Create a feature branch named after the lab you are building:
  ```bash
  git checkout -b feature/lab3-compose
  ```
- Do not commit directly to the `main` branch. Push your branch and create a Pull Request (PR) for review.

### 2. Adding a New Lab Module
- **Frontend Templates**:
  - Add your frontend pages inside `frontend/templates/` (e.g. `frontend/templates/lab3.html`).
  - Link your stylesheets or script files under `frontend/static/`.
- **Backend Routes & Logic**:
  - Open `backend/app.py`.
  - Add your routing handler (e.g., `@app.route('/lab/3')` or mapping to `/lab/<int:lab_number>`).
- **Database Schema Alterations**:
  - If your lab module requires new tables or configurations in the database, add them directly to the `init_db()` function in `backend/app.py` inside the `conn.executescript()` section using standard `IF NOT EXISTS` queries. This ensures that the database updates automatically when your team runs the app locally.
  - Never check in the `database/vlab.db` file to Git.

### 3. Local Development Best Practices
- Run the server in Debug mode locally to see updates instantly without restarting:
  ```bash
  # On Windows PowerShell:
  $env:FLASK_DEBUG="true"
  python backend/app.py

  # On Linux/macOS:
  export FLASK_DEBUG="true"
  python backend/app.py
  ```
- If you install any new libraries (e.g., `requests`, `docker`), make sure to freeze them into `backend/requirements.txt`:
  ```bash
  pip freeze > backend/requirements.txt
  ```
