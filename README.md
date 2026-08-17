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
├── app.py                  # Flask Core Backend (Routing, DB Logic, Task Engine)
├── requirements.txt        # Production-grade Python Dependencies
├── .gitignore              # Configured Git tracking exclusions (pycache, env, etc.)
├── instance/
│   └── vlab.db             # SQLite local database (generated on launch)
├── static/
│   ├── css/
│   │   └── style.css       # Core styling & UI Theme
│   └── images/
│       └── logo.png        # Institution / Lab Branding Logo
└── templates/              # HTML layout and dashboard templates
    ├── base.html
    ├── login.html
    ├── student_dashboard.html
    ├── faculty_dashboard.html
    ├── lab1.html           # Lab 1: Docker Desktop Installation
    ├── lab2.html           # Lab 2: Nginx Web App Containerization
    ├── lab3.html           # Lab 3: Run hello-world Container & Verify Application
    └── change_password.html
```

---

## 🗄️ Database Schema (SQLite)

The database initializes automatically inside the `instance/vlab.db` file. The following tables are created during initialization:

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
   pip install -r requirements.txt
   ```
3. Run the Flask application:
   ```bash
   python app.py
   ```
   *Note: Environment variables such as `FLASK_SECRET_KEY`, `FLASK_RUN_HOST`, `FLASK_RUN_PORT`, and `FLASK_DEBUG` can be customized to change execution parameters.*

---

## 👥 Branching Guidelines for Teammates
1. Ensure your local branch is updated with the latest remote `main` branch before coding.
2. Create a clean branch indicating your lab feature:
   ```bash
   git checkout -b feature/lab3-compose
   ```
3. Add your templates inside the `templates/` folder and implement logic inside `app.py`. Ensure DB alterations are added in `init_db()` under non-destructive `IF NOT EXISTS` queries.
