"""
Automated Test Suite for VLab Email, Student Notifications & Account Management System
Tests:
1. Database Schema & Student Enrollment Attributes (Enrollment No, Roll No, Active Status)
2. Multi-Identifier Student Authentication (Login via ID, Email, or Enrollment No)
3. Email-First Forgot Password Recovery (Reset request with email only)
4. Token Verification, Password Reset Execution & Token Invalidation
5. In-App Student Notifications API (Fetch, Mark Read, Mark All Read)
6. Lab Completion Event Notifications & Failover Idempotency (Zero duplicate emails)
7. Faculty Email Audit Logs & Resend Credentials Endpoint
8. Offline/LAN Mock Mode & Email Worker Queue Processing
"""

import os
import sys
import json
import unittest
from datetime import datetime, timedelta

# Ensure project root is on sys.path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from backend.db import init_db, get_db
from backend.app import app
from backend.services.email_service import email_service
from backend.services.notification_service import notification_service
from werkzeug.security import generate_password_hash, check_password_hash


class TestEmailAndNotificationSystem(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        init_db()
        app.config["TESTING"] = True
        app.config["SECRET_KEY"] = "test-secret-key-123"
        cls.client = app.test_client()

    def setUp(self):
        """Prepare clean test database fixtures before each test."""
        conn = get_db()
        # Clean test tables
        conn.execute("DELETE FROM email_logs")
        conn.execute("DELETE FROM email_queue")
        conn.execute("DELETE FROM notifications")
        conn.execute("DELETE FROM password_tokens")
        conn.execute("DELETE FROM students WHERE student_id LIKE 'TEST%'")
        conn.commit()

        # Insert reference student with enrollment and roll number
        now = datetime.utcnow().isoformat()
        conn.execute(
            """INSERT INTO students (
                student_id, name, email, enrollment_no, roll_no,
                default_password, password_hash, password_changed,
                is_active, must_change_password, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, 1, 1, ?)""",
            (
                "TEST001",
                "Aryan Sharma",
                "aryan.test@college.edu",
                "ADT25SOCBD129",
                "129",
                "Docker@1234",
                generate_password_hash("Docker@1234"),
                now
            )
        )
        conn.commit()
        conn.close()

    def test_01_schema_and_student_enrollment_fields(self):
        """Verifies enrollment_no and roll_no format support (e.g. ADT25SOCBD129)."""
        conn = get_db()
        row = conn.execute("SELECT * FROM students WHERE student_id='TEST001'").fetchone()
        conn.close()

        self.assertIsNotNone(row)
        self.assertEqual(row["enrollment_no"], "ADT25SOCBD129")
        self.assertEqual(str(row["roll_no"]), "129")
        self.assertEqual(row["is_active"], 1)
        self.assertEqual(row["must_change_password"], 1)

    def test_02_multi_identifier_login(self):
        """Verifies student can authenticate using Student ID, Email, or Enrollment No."""
        # 1. Login via Student ID
        res_id = self.client.post("/login", data={
            "role": "student",
            "identifier": "TEST001",
            "password": "Docker@1234"
        }, follow_redirects=False)
        self.assertIn(res_id.status_code, (302, 303))

        # 2. Login via Registered Email
        res_email = self.client.post("/login", data={
            "role": "student",
            "identifier": "aryan.test@college.edu",
            "password": "Docker@1234"
        }, follow_redirects=False)
        self.assertIn(res_email.status_code, (302, 303))

        # 3. Login via College Enrollment Number (ADT25SOCBD129)
        res_enr = self.client.post("/login", data={
            "role": "student",
            "identifier": "ADT25SOCBD129",
            "password": "Docker@1234"
        }, follow_redirects=False)
        self.assertIn(res_enr.status_code, (302, 303))

        # 4. Invalid credentials should fail
        res_bad = self.client.post("/login", data={
            "role": "student",
            "identifier": "ADT25SOCBD129",
            "password": "wrong_password"
        }, follow_redirects=True)
        self.assertIn(b"Invalid", res_bad.data)

    def test_03_forgot_password_email_only(self):
        """Verifies email-only forgot password flow generates token and queues reset email."""
        # Submit forgot password form with only student's registered email
        res = self.client.post("/forgot-password", data={
            "email": "aryan.test@college.edu"
        }, follow_redirects=True)

        self.assertEqual(res.status_code, 200)
        self.assertIn(b"password reset link has been dispatched", res.data)

        # Verify token created in database
        conn = get_db()
        token_row = conn.execute(
            "SELECT * FROM password_tokens WHERE student_id='TEST001' AND is_used=0"
        ).fetchone()
        self.assertIsNotNone(token_row)
        self.assertTrue(len(token_row["token"]) >= 32)

        # Verify email was queued
        queue_row = conn.execute(
            "SELECT * FROM email_queue WHERE student_id='TEST001' AND template LIKE '%password_reset%'"
        ).fetchone()
        self.assertIsNotNone(queue_row)
        self.assertEqual(queue_row["recipient_email"], "aryan.test@college.edu")
        conn.close()

    def test_04_reset_password_execution_and_token_invalidation(self):
        """Verifies reset link validation, password updating, token consumption and security email."""
        # First trigger reset token
        token = notification_service.create_password_token("TEST001", token_type="reset")
        self.assertIsNotNone(token)

        # GET /reset-password?token=...
        res_get = self.client.get(f"/reset-password?token={token}")
        self.assertEqual(res_get.status_code, 200)
        self.assertIn(b"Aryan Sharma", res_get.data)

        # POST /reset-password with new password
        res_post = self.client.post("/reset-password", data={
            "token": token,
            "new_password": "NewSecurePass@2026",
            "confirm_password": "NewSecurePass@2026"
        }, follow_redirects=True)

        self.assertEqual(res_post.status_code, 200)
        self.assertIn(b"Password reset successfully", res_post.data)

        # Verify DB: password hash updated and must_change_password cleared
        conn = get_db()
        student = conn.execute("SELECT * FROM students WHERE student_id='TEST001'").fetchone()
        self.assertTrue(check_password_hash(student["password_hash"], "NewSecurePass@2026"))
        self.assertEqual(student["must_change_password"], 0)

        # Verify token consumed (is_used=1)
        token_row = conn.execute("SELECT * FROM password_tokens WHERE token=?", (token,)).fetchone()
        self.assertEqual(token_row["is_used"], 1)

        # Verify security alert email was queued
        sec_email = conn.execute(
            "SELECT * FROM email_queue WHERE student_id='TEST001' AND template LIKE '%security_alert%'"
        ).fetchone()
        self.assertIsNotNone(sec_email)
        conn.close()

        # Verify reused token is rejected
        res_reused = self.client.post("/reset-password", data={
            "token": token,
            "new_password": "AnotherPassword@999",
            "confirm_password": "AnotherPassword@999"
        }, follow_redirects=True)
        self.assertIn(b"Invalid or expired", res_reused.data)

    def test_05_in_app_notifications_api(self):
        """Tests fetching in-app notifications, unread count, and mark-as-read endpoints."""
        # Create 2 notifications
        n1 = notification_service.create_notification(
            "TEST001", "lab_complete", "Lab 1 Passed", "Great job on Lab 1!"
        )
        n2 = notification_service.create_notification(
            "TEST001", "ticket_reply", "Faculty Replied", "Check your ticket update."
        )

        with self.client.session_transaction() as sess:
            sess["role"] = "student"
            sess["student_id"] = "TEST001"

        # Fetch notifications
        res = self.client.get("/api/notifications")
        data = json.loads(res.data)
        self.assertTrue(data["ok"])
        self.assertEqual(data["unread_count"], 2)
        self.assertEqual(len(data["notifications"]), 2)

        # Mark first notification as read
        res_read = self.client.post(f"/api/notifications/{n1}/read")
        self.assertTrue(json.loads(res_read.data)["ok"])

        # Check unread count decreased to 1
        res_after = self.client.get("/api/notifications")
        data_after = json.loads(res_after.data)
        self.assertEqual(data_after["unread_count"], 1)

        # Mark all as read
        res_all = self.client.post("/api/notifications/read-all")
        self.assertTrue(json.loads(res_all.data)["ok"])

        res_final = self.client.get("/api/notifications")
        data_final = json.loads(res_final.data)
        self.assertEqual(data_final["unread_count"], 0)

    def test_06_lab_completion_and_failover_idempotency(self):
        """Verifies that duplicate lab completion events (e.g. during Host failover) do NOT trigger duplicate emails."""
        # First verification event
        notification_service.notify_lab_completed(
            "TEST001", lab_number=1, lab_title="Introduction to Docker"
        )

        conn = get_db()
        queue_count_1 = conn.execute(
            "SELECT COUNT(*) as c FROM email_queue WHERE student_id='TEST001' AND template LIKE '%lab_completed%'"
        ).fetchone()["c"]
        self.assertEqual(queue_count_1, 1)

        # Simulated duplicate trigger (e.g. student verifies again or Host 2 syncs progress)
        notification_service.notify_lab_completed(
            "TEST001", lab_number=1, lab_title="Introduction to Docker"
        )

        queue_count_2 = conn.execute(
            "SELECT COUNT(*) as c FROM email_queue WHERE student_id='TEST001' AND template LIKE '%lab_completed%'"
        ).fetchone()["c"]
        conn.close()

        # Idempotency check: count MUST still be 1
        self.assertEqual(queue_count_2, 1)

    def test_07_faculty_resend_credentials_and_email_logs(self):
        """Verifies faculty can inspect email logs and trigger credential resend."""
        with self.client.session_transaction() as sess:
            sess["role"] = "faculty"
            sess["faculty_username"] = "faculty"

        # Resend credentials endpoint
        res = self.client.post("/api/faculty/resend-credentials/TEST001")
        data = json.loads(res.data)
        self.assertTrue(data["ok"])
        self.assertIn("aryan.test@college.edu", data["message"])

        # Check faculty email logs API
        res_logs = self.client.get("/api/faculty/email-logs")
        logs_data = json.loads(res_logs.data)
        self.assertTrue(logs_data["ok"])
        self.assertTrue("logs" in logs_data)
        self.assertTrue("queue" in logs_data)

    def test_08_email_queue_worker_processing(self):
        """Verifies that the email queue processor delivers items and records audit logs."""
        # Enqueue test item
        email_service.queue_email(
            recipient="aryan.test@college.edu",
            subject="Test System Verification",
            template="welcome_setup",
            context={"student_name": "Aryan", "student_id": "TEST001", "default_password": "Pass", "setup_url": "http://localhost:5000/reset-password?token=abc"},
            student_id="TEST001",
            idempotency_key="manual_test_verification_08"
        )

        # Run one processing iteration
        processed = email_service.process_queue()
        self.assertGreaterEqual(processed, 1)

        # Verify delivery recorded in email_logs table
        conn = get_db()
        log_row = conn.execute(
            "SELECT * FROM email_logs WHERE student_id='TEST001' AND recipient_email='aryan.test@college.edu' ORDER BY sent_at DESC"
        ).fetchone()
        conn.close()

        self.assertIsNotNone(log_row)
        self.assertIn(log_row["status"], ("sent", "mock_sent"))

    def test_09_separate_login_pages_and_host_permission_api(self):
        """Verifies separate dedicated student/faculty login pages and faculty host permission API."""
        # 1. Verify GET /login is dedicated Student Portal
        res_stu = self.client.get("/login")
        self.assertEqual(res_stu.status_code, 200)
        self.assertIn(b"STUDENT PORTAL", res_stu.data)
        self.assertIn(b"SWITCH TO FACULTY PORTAL", res_stu.data)
        self.assertNotIn(b'name="role" value="faculty"', res_stu.data)

        # 2. Verify GET /faculty/login is dedicated Faculty Console
        res_fac = self.client.get("/faculty/login")
        self.assertEqual(res_fac.status_code, 200)
        self.assertIn(b"FACULTY CONSOLE", res_fac.data)
        self.assertIn(b"SWITCH TO STUDENT PORTAL", res_fac.data)

        # 3. Verify POST /faculty/login authenticates faculty
        res_fac_post = self.client.post("/faculty/login", data={
            "username": "faculty",
            "password": "faculty123"
        }, follow_redirects=False)
        self.assertIn(res_fac_post.status_code, (302, 303))
        self.assertIn("/faculty/dashboard", res_fac_post.headers.get("Location", ""))

        # 4. Verify Faculty Host Permission API
        with self.client.session_transaction() as sess:
            sess["role"] = "faculty"
            sess["faculty_username"] = "faculty"

        # Toggle global compute access
        res_glob = self.client.post(
            "/api/ha/global-permission",
            data=json.dumps({"enabled": False}),
            content_type="application/json"
        )
        self.assertEqual(res_glob.status_code, 200)
        self.assertFalse(json.loads(res_glob.data)["global_compute_enabled"])

        # Re-enable global compute access
        res_glob_on = self.client.post(
            "/api/ha/global-permission",
            data=json.dumps({"enabled": True}),
            content_type="application/json"
        )
        self.assertTrue(json.loads(res_glob_on.data)["global_compute_enabled"])

        # Toggle host permission
        res_host = self.client.post(
            "/api/ha/host/HOST1/permission",
            data=json.dumps({"authorized": False, "note": "Maintenance"}),
            content_type="application/json"
        )
        self.assertEqual(res_host.status_code, 200)
        self.assertEqual(json.loads(res_host.data)["is_authorized"], 0)

    def test_10_default_password_email_dispatch_on_create_and_reset(self):
        """Verifies email dispatch with default password on student creation and password reset."""
        with self.client.session_transaction() as sess:
            sess["role"] = "faculty"
            sess["faculty_username"] = "faculty"

        # 1. Faculty adds a new student manually
        res_add = self.client.post(
            "/faculty/add-student",
            data=json.dumps({
                "name": "Rohan Verma",
                "email": "rohan.test@college.edu",
                "student_id": "TEST002",
                "enrollment_no": "ADT25SOCBD130",
                "roll_no": "130"
            }),
            content_type="application/json"
        )
        self.assertEqual(res_add.status_code, 200)
        data_add = json.loads(res_add.data)
        self.assertTrue(data_add["ok"])
        self.assertEqual(data_add["student_id"], "TEST002")
        initial_pw = data_add["default_password"]
        self.assertTrue(initial_pw.startswith("Docker@"))

        # Verify email was immediately dispatched and logged
        conn = get_db()
        log_create = conn.execute(
            "SELECT * FROM email_logs WHERE student_id='TEST002' ORDER BY id DESC"
        ).fetchone()
        self.assertIsNotNone(log_create)
        self.assertEqual(log_create["recipient_email"], "rohan.test@college.edu")
        self.assertIn(log_create["status"], ("sent", "mock_sent"))

        # 2. Faculty triggers reset of student's default password
        res_reset = self.client.post("/api/faculty/reset-student-password/TEST002")
        self.assertEqual(res_reset.status_code, 200)
        data_reset = json.loads(res_reset.data)
        self.assertTrue(data_reset["ok"])
        new_pw = data_reset["default_password"]
        self.assertTrue(new_pw.startswith("Docker@"))
        self.assertIn("rohan.test@college.edu", data_reset["message"])

        # Verify DB student record was updated with new password and flags
        student_row = conn.execute("SELECT * FROM students WHERE student_id='TEST002'").fetchone()
        self.assertEqual(student_row["default_password"], new_pw)
        self.assertEqual(student_row["password_changed"], 0)
        self.assertEqual(student_row["must_change_password"], 1)

        # Verify a new reset email log was recorded
        logs_all = conn.execute(
            "SELECT * FROM email_logs WHERE student_id='TEST002' ORDER BY id DESC"
        ).fetchall()
        conn.close()
        self.assertGreaterEqual(len(logs_all), 2)
        latest_log = logs_all[0]
        self.assertIn("Default Password", latest_log["subject"])
        self.assertIn(latest_log["status"], ("sent", "mock_sent"))

    def test_10_otp_generation_and_email_delivery(self):
        """Verifies 6-digit OTP generation, database record, and email delivery."""
        ok, msg, otp = notification_service.generate_and_send_otp("aryan.test@college.edu")
        self.assertTrue(ok)
        self.assertIsNotNone(otp)
        self.assertEqual(len(otp), 6)
        self.assertTrue(otp.isdigit())

        # Verify DB entry in password_tokens
        conn = get_db()
        token_row = conn.execute(
            "SELECT * FROM password_tokens WHERE student_id='TEST001' AND token_type='OTP' AND is_used=0 ORDER BY id DESC LIMIT 1"
        ).fetchone()
        self.assertIsNotNone(token_row)
        self.assertTrue(token_row["token"].startswith(f"{otp}_"))

        # Verify email was queued and sent
        email_row = conn.execute(
            "SELECT * FROM email_logs WHERE student_id='TEST001' AND subject LIKE '%Verification Code%' ORDER BY id DESC"
        ).fetchone()
        self.assertIsNotNone(email_row)
        self.assertEqual(email_row["recipient_email"], "aryan.test@college.edu")
        conn.close()

    def test_11_otp_verification_edge_cases(self):
        """Verifies OTP validation against invalid codes, non-existent accounts, and expired tokens."""
        # 1. Non-existent email
        ok, msg, _ = notification_service.verify_and_consume_otp("nonexistent@college.edu", "123456")
        self.assertFalse(ok)
        self.assertIn("no student", msg.lower())

        # 2. Generate a valid OTP
        ok, _, otp = notification_service.generate_and_send_otp("aryan.test@college.edu")
        self.assertTrue(ok)

        # 3. Wrong OTP code
        ok_wrong, msg_wrong, _ = notification_service.verify_and_consume_otp("aryan.test@college.edu", "000000")
        self.assertFalse(ok_wrong)
        self.assertIn("invalid", msg_wrong.lower())

        # 4. Expired OTP simulation
        conn = get_db()
        past_time = (datetime.utcnow() - timedelta(minutes=30)).isoformat()
        conn.execute(
            "UPDATE password_tokens SET expires_at=? WHERE student_id='TEST001' AND token_type='OTP' AND is_used=0",
            (past_time,)
        )
        conn.commit()
        conn.close()

        ok_exp, msg_exp, _ = notification_service.verify_and_consume_otp("aryan.test@college.edu", otp)
        self.assertFalse(ok_exp)
        self.assertIn("expired", msg_exp.lower())

        # 5. Generate fresh OTP and successfully consume
        ok_fresh, _, fresh_otp = notification_service.generate_and_send_otp("aryan.test@college.edu")
        self.assertTrue(ok_fresh)
        ok_valid, msg_valid, student = notification_service.verify_and_consume_otp("aryan.test@college.edu", fresh_otp)
        self.assertTrue(ok_valid)
        self.assertEqual(student["student_id"], "TEST001")

        # 6. Reusing consumed OTP must fail
        ok_reused, msg_reused, _ = notification_service.verify_and_consume_otp("aryan.test@college.edu", fresh_otp)
        self.assertFalse(ok_reused)

    def test_12_change_password_endpoint_with_otp(self):
        """Tests the full API flow of sending OTP and updating password."""
        # 1. Request OTP via API
        res_send = self.client.post("/api/auth/send-otp", json={"email": "aryan.test@college.edu"})
        self.assertEqual(res_send.status_code, 200)
        data_send = json.loads(res_send.data)
        self.assertTrue(data_send["ok"])

        # Fetch generated OTP from DB
        conn = get_db()
        token_row = conn.execute(
            "SELECT token FROM password_tokens WHERE student_id='TEST001' AND token_type='OTP' AND is_used=0 ORDER BY id DESC LIMIT 1"
        ).fetchone()
        otp = token_row["token"].split("_")[0]
        conn.close()

        # 2. Submit password change with correct OTP
        res_change = self.client.post("/change-password", json={
            "email": "aryan.test@college.edu",
            "otp": otp,
            "new_password": "NewOtpVerifiedPassword@2026",
            "confirm_password": "NewOtpVerifiedPassword@2026"
        })
        self.assertEqual(res_change.status_code, 200)
        data_change = json.loads(res_change.data)
        self.assertTrue(data_change["ok"])

        # 3. Verify in DB
        conn = get_db()
        st = conn.execute("SELECT * FROM students WHERE student_id='TEST001'").fetchone()
        self.assertTrue(check_password_hash(st["password_hash"], "NewOtpVerifiedPassword@2026"))
        self.assertEqual(st["must_change_password"], 0)
        self.assertEqual(st["password_changed"], 1)
        conn.close()

    def test_13_faculty_password_change_forbidden(self):
        """Verifies that faculty role password change attempts are strictly rejected."""
        res = self.client.post("/change-password", json={
            "role": "faculty",
            "email": "faculty@college.edu",
            "otp": "123456",
            "new_password": "NewFacultyPassword@999",
            "confirm_password": "NewFacultyPassword@999"
        })
        self.assertEqual(res.status_code, 403)
        data = json.loads(res.data)
        self.assertFalse(data["ok"])
        self.assertIn("faculty passwords cannot be modified", data["message"].lower())


if __name__ == "__main__":
    unittest.main()

