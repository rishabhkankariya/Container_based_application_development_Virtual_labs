"""
Unified Database Adapter for V-Lab
Supports SQLite (default local) and PostgreSQL (via DATABASE_URL)
Provides parameter compatibility, dictionary row access, HA schema migrations,
and Notification / Email / Token management schemas.
"""

import os
import re
import json
import sqlite3
import datetime
from typing import Any, Dict, List, Optional, Tuple, Union

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
    HAS_POSTGRES = True
except ImportError:
    HAS_POSTGRES = False

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_SQLITE_PATH = os.path.join(BASE_DIR, "database", "vlab.db")


class DBConnectionWrapper:
    """Wraps connection to provide a unified interface for SQLite and PostgreSQL."""

    def __init__(self, conn, is_postgres: bool = False):
        self.conn = conn
        self.is_postgres = is_postgres

    def _convert_sql(self, sql: str) -> str:
        """Translate SQLite '?' placeholders to PostgreSQL '%s' if running on PostgreSQL."""
        if not self.is_postgres:
            return sql
        return re.sub(r'\?', '%s', sql)

    def execute(self, sql: str, params: Union[Tuple, List, Dict] = ()):
        sql = self._convert_sql(sql)
        cursor = self.conn.cursor()
        cursor.execute(sql, params)
        return cursor

    def executescript(self, sql_script: str):
        if self.is_postgres:
            cursor = self.conn.cursor()
            for statement in sql_script.split(';'):
                stmt = statement.strip()
                if stmt:
                    cursor.execute(stmt)
            return cursor
        else:
            return self.conn.executescript(sql_script)

    def commit(self):
        self.conn.commit()

    def rollback(self):
        self.conn.rollback()

    def close(self):
        self.conn.close()


def get_db() -> DBConnectionWrapper:
    """
    Returns a unified DB connection.
    If DATABASE_URL is set, connects to PostgreSQL.
    Otherwise, connects to SQLite database at database/vlab.db.
    """
    database_url = os.environ.get("DATABASE_URL", "").strip()

    if database_url:
        if not HAS_POSTGRES:
            print("⚠️ [V-Lab DB Warning] DATABASE_URL is defined, but 'psycopg2' is not installed. Falling back to SQLite.")
        else:
            if database_url.startswith("postgres://"):
                database_url = database_url.replace("postgres://", "postgresql://", 1)
            try:
                conn = psycopg2.connect(database_url, cursor_factory=RealDictCursor)
                return DBConnectionWrapper(conn, is_postgres=True)
            except Exception as e:
                print(f"❌ [V-Lab DB Error] Unable to connect to remote PostgreSQL DB VM: {e}")
                print("   Falling back to local SQLite at database/vlab.db for resilience.")

    os.makedirs(os.path.dirname(DEFAULT_SQLITE_PATH), exist_ok=True)
    conn = sqlite3.connect(DEFAULT_SQLITE_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return DBConnectionWrapper(conn, is_postgres=False)


def init_db():
    """Initializes existing V-Lab tables, HA tables, and Email/Notification tables."""
    conn = get_db()
    
    if conn.is_postgres:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS faculty (
                id SERIAL PRIMARY KEY,
                username VARCHAR(100) UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                password_changed INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS students (
                id SERIAL PRIMARY KEY,
                student_id VARCHAR(50) UNIQUE NOT NULL,
                enrollment_no VARCHAR(100),
                roll_no VARCHAR(50),
                name VARCHAR(150) NOT NULL,
                email VARCHAR(200),
                default_password TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                password_changed INTEGER NOT NULL DEFAULT 0,
                must_change_password INTEGER NOT NULL DEFAULT 1,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS lab_progress (
                id SERIAL PRIMARY KEY,
                student_id VARCHAR(50) NOT NULL,
                lab_number INTEGER NOT NULL,
                steps_completed TEXT NOT NULL DEFAULT '[]',
                verification_passed INTEGER NOT NULL DEFAULT 0,
                completed_at TIMESTAMP,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(student_id, lab_number)
            );

            CREATE TABLE IF NOT EXISTS issue_tickets (
                id SERIAL PRIMARY KEY,
                student_id VARCHAR(50) NOT NULL,
                student_name VARCHAR(150) NOT NULL,
                lab_number INTEGER NOT NULL,
                title VARCHAR(255) NOT NULL,
                description TEXT NOT NULL,
                status VARCHAR(50) NOT NULL DEFAULT 'OPEN',
                faculty_reply TEXT,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS lab_videos (
                id SERIAL PRIMARY KEY,
                lab_number INTEGER UNIQUE NOT NULL,
                video_title VARCHAR(255) NOT NULL,
                video_url TEXT NOT NULL,
                uploaded_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            -- High-Availability Cluster Entities
            CREATE TABLE IF NOT EXISTS hosts (
                host_id VARCHAR(100) PRIMARY KEY,
                hostname VARCHAR(150) NOT NULL,
                ip_address VARCHAR(100) NOT NULL,
                port INTEGER NOT NULL DEFAULT 5050,
                role VARCHAR(50) NOT NULL DEFAULT 'STANDBY',
                status VARCHAR(50) NOT NULL DEFAULT 'OFFLINE',
                cpu_cores INTEGER NOT NULL DEFAULT 1,
                ram_total_mb REAL NOT NULL DEFAULT 0,
                disk_total_gb REAL NOT NULL DEFAULT 0,
                current_cpu REAL NOT NULL DEFAULT 0,
                current_ram REAL NOT NULL DEFAULT 0,
                current_disk REAL NOT NULL DEFAULT 0,
                active_containers INTEGER NOT NULL DEFAULT 0,
                docker_available INTEGER NOT NULL DEFAULT 0,
                docker_version VARCHAR(100) DEFAULT '',
                agent_version VARCHAR(50) DEFAULT '',
                is_authorized INTEGER NOT NULL DEFAULT 1,
                authorization_note TEXT DEFAULT 'Authorized by Main Host',
                missed_heartbeats INTEGER NOT NULL DEFAULT 0,
                last_heartbeat TIMESTAMP,
                last_sync_time TIMESTAMP,
                registered_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS host_heartbeats (
                id SERIAL PRIMARY KEY,
                host_id VARCHAR(100) NOT NULL,
                timestamp TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                cpu_percent REAL NOT NULL,
                ram_percent REAL NOT NULL,
                disk_percent REAL NOT NULL,
                active_containers INTEGER NOT NULL,
                docker_status INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS failover_events (
                id SERIAL PRIMARY KEY,
                timestamp TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                from_host VARCHAR(100) NOT NULL,
                to_host VARCHAR(100) NOT NULL,
                trigger_reason TEXT NOT NULL,
                recovered_sessions INTEGER NOT NULL DEFAULT 0,
                status VARCHAR(50) NOT NULL DEFAULT 'COMPLETED'
            );

            CREATE TABLE IF NOT EXISTS student_workspaces (
                id SERIAL PRIMARY KEY,
                student_id VARCHAR(50) NOT NULL,
                filename VARCHAR(255) NOT NULL,
                content TEXT NOT NULL,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(student_id, filename)
            );

            CREATE TABLE IF NOT EXISTS ha_config (
                config_key VARCHAR(100) PRIMARY KEY,
                config_value TEXT NOT NULL,
                description TEXT
            );

            CREATE TABLE IF NOT EXISTS audit_logs (
                id SERIAL PRIMARY KEY,
                timestamp TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                event_type VARCHAR(100) NOT NULL,
                host_id VARCHAR(100),
                student_id VARCHAR(50),
                details TEXT NOT NULL
            );

            -- Email & Notification Infrastructure
            CREATE TABLE IF NOT EXISTS password_tokens (
                id SERIAL PRIMARY KEY,
                student_id VARCHAR(50) NOT NULL,
                email VARCHAR(200) NOT NULL,
                token VARCHAR(128) UNIQUE NOT NULL,
                token_type VARCHAR(50) NOT NULL,
                is_used INTEGER NOT NULL DEFAULT 0,
                expires_at TIMESTAMP NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS notifications (
                id SERIAL PRIMARY KEY,
                student_id VARCHAR(50) NOT NULL,
                type VARCHAR(50) NOT NULL,
                title VARCHAR(255) NOT NULL,
                message TEXT NOT NULL,
                severity VARCHAR(20) NOT NULL DEFAULT 'info',
                is_read INTEGER NOT NULL DEFAULT 0,
                related_lab_id INTEGER,
                related_entity_id VARCHAR(100),
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                read_at TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS email_queue (
                id SERIAL PRIMARY KEY,
                student_id VARCHAR(50),
                recipient_email VARCHAR(200) NOT NULL,
                template VARCHAR(100) NOT NULL,
                subject VARCHAR(255) NOT NULL,
                payload TEXT NOT NULL,
                status VARCHAR(50) NOT NULL DEFAULT 'PENDING',
                attempts INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                idempotency_key VARCHAR(150) UNIQUE NOT NULL,
                scheduled_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                sent_at TIMESTAMP,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS email_logs (
                id SERIAL PRIMARY KEY,
                student_id VARCHAR(50),
                recipient_email VARCHAR(200) NOT NULL,
                event_type VARCHAR(100) NOT NULL,
                subject VARCHAR(255) NOT NULL,
                status VARCHAR(50) NOT NULL,
                provider_message_id VARCHAR(100),
                sent_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                error_message TEXT
            );

            CREATE TABLE IF NOT EXISTS notification_preferences (
                student_id VARCHAR(50) PRIMARY KEY,
                email_lab_completion INTEGER NOT NULL DEFAULT 1,
                email_submission_results INTEGER NOT NULL DEFAULT 1,
                email_announcements INTEGER NOT NULL DEFAULT 1,
                email_ticket_updates INTEGER NOT NULL DEFAULT 1
            );
            """
        )
    else:
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
                enrollment_no TEXT,
                roll_no TEXT,
                name TEXT NOT NULL,
                email TEXT,
                default_password TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                password_changed INTEGER NOT NULL DEFAULT 0,
                must_change_password INTEGER NOT NULL DEFAULT 1,
                is_active INTEGER NOT NULL DEFAULT 1,
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

            -- High-Availability Cluster Entities
            CREATE TABLE IF NOT EXISTS hosts (
                host_id TEXT PRIMARY KEY,
                hostname TEXT NOT NULL,
                ip_address TEXT NOT NULL,
                port INTEGER NOT NULL DEFAULT 5050,
                role TEXT NOT NULL DEFAULT 'STANDBY',
                status TEXT NOT NULL DEFAULT 'OFFLINE',
                cpu_cores INTEGER NOT NULL DEFAULT 1,
                ram_total_mb REAL NOT NULL DEFAULT 0,
                disk_total_gb REAL NOT NULL DEFAULT 0,
                current_cpu REAL NOT NULL DEFAULT 0,
                current_ram REAL NOT NULL DEFAULT 0,
                current_disk REAL NOT NULL DEFAULT 0,
                active_containers INTEGER NOT NULL DEFAULT 0,
                docker_available INTEGER NOT NULL DEFAULT 0,
                docker_version TEXT DEFAULT '',
                agent_version TEXT DEFAULT '',
                is_authorized INTEGER NOT NULL DEFAULT 1,
                authorization_note TEXT DEFAULT 'Authorized by Main Host',
                missed_heartbeats INTEGER NOT NULL DEFAULT 0,
                last_heartbeat TEXT,
                last_sync_time TEXT,
                registered_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS host_heartbeats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                host_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                cpu_percent REAL NOT NULL,
                ram_percent REAL NOT NULL,
                disk_percent REAL NOT NULL,
                active_containers INTEGER NOT NULL,
                docker_status INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS failover_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                from_host TEXT NOT NULL,
                to_host TEXT NOT NULL,
                trigger_reason TEXT NOT NULL,
                recovered_sessions INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'COMPLETED'
            );

            CREATE TABLE IF NOT EXISTS student_workspaces (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id TEXT NOT NULL,
                filename TEXT NOT NULL,
                content TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(student_id, filename)
            );

            CREATE TABLE IF NOT EXISTS ha_config (
                config_key TEXT PRIMARY KEY,
                config_value TEXT NOT NULL,
                description TEXT
            );

            CREATE TABLE IF NOT EXISTS audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                event_type TEXT NOT NULL,
                host_id TEXT,
                student_id TEXT,
                details TEXT NOT NULL
            );

            -- Email & Notification Infrastructure
            CREATE TABLE IF NOT EXISTS password_tokens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id TEXT NOT NULL,
                email TEXT NOT NULL,
                token TEXT UNIQUE NOT NULL,
                token_type TEXT NOT NULL,
                is_used INTEGER NOT NULL DEFAULT 0,
                expires_at TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id TEXT NOT NULL,
                type TEXT NOT NULL,
                title TEXT NOT NULL,
                message TEXT NOT NULL,
                severity TEXT NOT NULL DEFAULT 'info',
                is_read INTEGER NOT NULL DEFAULT 0,
                related_lab_id INTEGER,
                related_entity_id TEXT,
                created_at TEXT NOT NULL,
                read_at TEXT
            );

            CREATE TABLE IF NOT EXISTS email_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id TEXT,
                recipient_email TEXT NOT NULL,
                template TEXT NOT NULL,
                subject TEXT NOT NULL,
                payload TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'PENDING',
                attempts INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                idempotency_key TEXT UNIQUE NOT NULL,
                scheduled_at TEXT NOT NULL,
                sent_at TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS email_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id TEXT,
                recipient_email TEXT NOT NULL,
                event_type TEXT NOT NULL,
                subject TEXT NOT NULL,
                status TEXT NOT NULL,
                provider_message_id TEXT,
                sent_at TEXT NOT NULL,
                error_message TEXT
            );

            CREATE TABLE IF NOT EXISTS notification_preferences (
                student_id TEXT PRIMARY KEY,
                email_lab_completion INTEGER NOT NULL DEFAULT 1,
                email_submission_results INTEGER NOT NULL DEFAULT 1,
                email_announcements INTEGER NOT NULL DEFAULT 1,
                email_ticket_updates INTEGER NOT NULL DEFAULT 1
            );
            """
        )

    # Dynamic Column Migration for existing databases
    if conn.is_postgres:
        try:
            conn.execute("ALTER TABLE students ADD COLUMN IF NOT EXISTS enrollment_no VARCHAR(100)")
            conn.execute("ALTER TABLE students ADD COLUMN IF NOT EXISTS roll_no VARCHAR(50)")
            conn.execute("ALTER TABLE students ADD COLUMN IF NOT EXISTS is_active INTEGER NOT NULL DEFAULT 1")
            conn.execute("ALTER TABLE students ADD COLUMN IF NOT EXISTS must_change_password INTEGER NOT NULL DEFAULT 1")
            conn.commit()
        except Exception:
            conn.rollback()
    else:
        try:
            cur = conn.execute("PRAGMA table_info(students)")
            existing_cols = {r["name"] if isinstance(r, dict) or hasattr(r, "__getitem__") else r[1] for r in cur.fetchall()}
            if "enrollment_no" not in existing_cols:
                conn.execute("ALTER TABLE students ADD COLUMN enrollment_no TEXT")
            if "roll_no" not in existing_cols:
                conn.execute("ALTER TABLE students ADD COLUMN roll_no TEXT")
            if "is_active" not in existing_cols:
                conn.execute("ALTER TABLE students ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1")
            if "must_change_password" not in existing_cols:
                conn.execute("ALTER TABLE students ADD COLUMN must_change_password INTEGER NOT NULL DEFAULT 1")
            conn.commit()
        except Exception:
            conn.rollback()

    # Insert default faculty user if not present
    from werkzeug.security import generate_password_hash
    row = conn.execute("SELECT COUNT(*) as c FROM faculty").fetchone()
    count = row["c"] if isinstance(row, dict) or hasattr(row, "__getitem__") else row[0]
    if count == 0:
        conn.execute(
            "INSERT INTO faculty (username, password_hash, password_changed) VALUES (?, ?, 0)",
            ("faculty", generate_password_hash("faculty123"))
        )

    # Seed default HA and Email configuration thresholds if not present
    default_configs = [
        ("heartbeat_interval_sec", "3", "Frequency of host agent heartbeats in seconds"),
        ("heartbeat_timeout_misses", "3", "Consecutive missed heartbeats before declaring host offline"),
        ("cpu_warn_threshold", "75", "CPU utilization warning percentage"),
        ("cpu_hard_threshold", "85", "CPU utilization hard threshold before spilling over"),
        ("ram_warn_threshold", "75", "RAM utilization warning percentage"),
        ("ram_hard_threshold", "85", "RAM utilization hard threshold before spilling over"),
        ("max_containers_per_host", "25", "Maximum concurrent student containers allowed per host"),
        ("cluster_auth_secret", "vlab-cluster-secret-2026", "Shared token for agent-to-controller communication"),
        ("smtp_host", "", "SMTP Host Server (e.g. smtp.gmail.com)"),
        ("smtp_port", "587", "SMTP Port (587 for TLS, 465 for SSL)"),
        ("smtp_user", "", "SMTP Username/Email"),
        ("smtp_password", "", "SMTP Password/App Password"),
        ("smtp_from_email", "vlab-support@college.edu", "From Email address"),
        ("smtp_from_name", "V-Lab Virtual Laboratories", "Display Name for outgoing emails"),
        ("vlab_base_url", "http://localhost:5000", "Public or LAN base URL of V-Lab platform"),
        ("global_compute_enabled", "1", "Main Host permission toggle for container execution on lab hosts")
    ]
    for key, val, desc in default_configs:
        cfg = conn.execute("SELECT config_key FROM ha_config WHERE config_key = ?", (key,)).fetchone()
        if not cfg:
            conn.execute(
                "INSERT INTO ha_config (config_key, config_value, description) VALUES (?, ?, ?)",
                (key, val, desc)
            )

    # Safe migration for hosts authorization fields
    try:
        conn.execute("ALTER TABLE hosts ADD COLUMN is_authorized INTEGER NOT NULL DEFAULT 1")
    except Exception:
        pass
    try:
        conn.execute("ALTER TABLE hosts ADD COLUMN authorization_note TEXT DEFAULT 'Authorized by Main Host'")
    except Exception:
        pass

    conn.commit()
    conn.close()


def save_workspace_file(student_id: str, filename: str, content: str):
    """Persists a file edit to central database storage for deterministic recovery."""
    conn = get_db()
    now = datetime.datetime.utcnow().isoformat()
    try:
        conn.execute(
            """
            INSERT INTO student_workspaces (student_id, filename, content, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(student_id, filename) DO UPDATE SET content=excluded.content, updated_at=excluded.updated_at
            """,
            (student_id, filename, content, now)
        )
        conn.commit()
    except Exception:
        conn.rollback()
        existing = conn.execute(
            "SELECT id FROM student_workspaces WHERE student_id = ? AND filename = ?",
            (student_id, filename)
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE student_workspaces SET content = ?, updated_at = ? WHERE student_id = ? AND filename = ?",
                (content, now, student_id, filename)
            )
        else:
            conn.execute(
                "INSERT INTO student_workspaces (student_id, filename, content, updated_at) VALUES (?, ?, ?, ?)",
                (student_id, filename, content, now)
            )
        conn.commit()
    finally:
        conn.close()


def get_workspace_files(student_id: str) -> Dict[str, str]:
    """Retrieves all saved workspace files for a student from central database storage."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT filename, content FROM student_workspaces WHERE student_id = ?",
            (student_id,)
        ).fetchall()
        return {r["filename"]: r["content"] for r in rows}
    finally:
        conn.close()


def delete_workspace_file(student_id: str, filename: str):
    """Permanently deletes a student workspace file from database storage."""
    conn = get_db()
    try:
        conn.execute(
            "DELETE FROM student_workspaces WHERE student_id = ? AND filename = ?",
            (student_id, filename)
        )
        conn.commit()
    finally:
        conn.close()


def rename_workspace_file(student_id: str, old_filename: str, new_filename: str):
    """Renames a student workspace file in database storage."""
    conn = get_db()
    try:
        conn.execute(
            "UPDATE student_workspaces SET filename = ?, updated_at = ? WHERE student_id = ? AND filename = ?",
            (new_filename, datetime.datetime.utcnow().isoformat(), student_id, old_filename)
        )
        conn.commit()
    finally:
        conn.close()

