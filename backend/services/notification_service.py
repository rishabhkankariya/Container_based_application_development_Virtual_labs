"""
Centralized Notification Service & Event Bus for V-Lab
Coordinates:
- In-app notification creation, retrieval, and read state tracking
- Cryptographically secure single-use token generation for password setup & reset
- Asynchronous email event dispatching with strict idempotency / duplicate prevention
- Student notification preference filtering
"""

import os
import secrets
import datetime
from typing import Dict, Any, Optional, Tuple, List

from backend.db import get_db
from backend.services.email_service import email_service


class NotificationService:
    """Dispatches in-app notifications and queues emails with idempotency guarantees."""

    def create_in_app_notification(
        self,
        student_id: str,
        notif_type: str,
        title: str,
        message: str,
        severity: str = "info",
        related_lab_id: Optional[int] = None,
        related_entity_id: Optional[str] = None
    ) -> Optional[int]:
        """Inserts an in-app notification record for a student and returns the notification ID."""
        now_str = datetime.datetime.utcnow().isoformat()
        conn = get_db()
        try:
            if getattr(conn, "is_postgres", False):
                cur = conn.execute(
                    """
                    INSERT INTO notifications (
                        student_id, type, title, message, severity,
                        is_read, related_lab_id, related_entity_id, created_at
                    ) VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?) RETURNING id
                    """,
                    (student_id, notif_type, title, message, severity, related_lab_id, related_entity_id, now_str)
                )
                row = cur.fetchone()
                notif_id = row[0] if row else None
            else:
                cur = conn.execute(
                    """
                    INSERT INTO notifications (
                        student_id, type, title, message, severity,
                        is_read, related_lab_id, related_entity_id, created_at
                    ) VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?)
                    """,
                    (student_id, notif_type, title, message, severity, related_lab_id, related_entity_id, now_str)
                )
                notif_id = cur.lastrowid
            conn.commit()
            return notif_id
        except Exception as e:
            conn.rollback()
            print(f"⚠️ Error creating in-app notification: {e}")
            return None
        finally:
            conn.close()

    def get_student_notifications(
        self,
        student_id: str,
        limit: int = 15,
        unread_only: bool = False
    ) -> Dict[str, Any]:
        """Fetches notification history and unread count for a student."""
        conn = get_db()
        try:
            unread_row = conn.execute(
                "SELECT COUNT(*) as c FROM notifications WHERE student_id = ? AND is_read = 0",
                (student_id,)
            ).fetchone()
            unread_count = unread_row["c"] if isinstance(unread_row, dict) or hasattr(unread_row, "__getitem__") else unread_row[0]

            if unread_only:
                rows = conn.execute(
                    "SELECT * FROM notifications WHERE student_id = ? AND is_read = 0 ORDER BY id DESC LIMIT ?",
                    (student_id, limit)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM notifications WHERE student_id = ? ORDER BY id DESC LIMIT ?",
                    (student_id, limit)
                ).fetchall()

            return {
                "unread_count": int(unread_count or 0),
                "notifications": [dict(r) for r in rows]
            }
        finally:
            conn.close()

    def mark_notifications_read(self, student_id: str, notification_id: Optional[int] = None) -> bool:
        """Marks a single notification or all notifications as read."""
        now_str = datetime.datetime.utcnow().isoformat()
        conn = get_db()
        try:
            if notification_id:
                conn.execute(
                    "UPDATE notifications SET is_read = 1, read_at = ? WHERE id = ? AND student_id = ?",
                    (now_str, notification_id, student_id)
                )
            else:
                conn.execute(
                    "UPDATE notifications SET is_read = 1, read_at = ? WHERE student_id = ? AND is_read = 0",
                    (now_str, student_id)
                )
            conn.commit()
            return True
        except Exception:
            conn.rollback()
            return False
        finally:
            conn.close()

    # -----------------------------------------------------------------------
    # Cryptographic Password Tokens
    # -----------------------------------------------------------------------
    def generate_password_token(
        self,
        student_id: str,
        email: str,
        token_type: str = "SETUP",
        expiry_hours: int = 48
    ) -> str:
        """Generates a secure single-use URL token with expiration."""
        token = secrets.token_urlsafe(36)
        now = datetime.datetime.utcnow()
        expires_at = (now + datetime.timedelta(hours=expiry_hours)).isoformat()
        now_str = now.isoformat()

        conn = get_db()
        try:
            # Invalidate any previous unused tokens of this type for this student
            conn.execute(
                "UPDATE password_tokens SET is_used = 1 WHERE student_id = ? AND token_type = ? AND is_used = 0",
                (student_id, token_type)
            )
            conn.execute(
                """
                INSERT INTO password_tokens (student_id, email, token, token_type, is_used, expires_at, created_at)
                VALUES (?, ?, ?, ?, 0, ?, ?)
                """,
                (student_id, email, token, token_type, expires_at, now_str)
            )
            conn.commit()
            return token
        finally:
            conn.close()

    def _resolve_student(self, student_or_id: Any) -> Dict[str, Any]:
        """Resolves a student record whether given a dict, Row, or string student_id."""
        if isinstance(student_or_id, dict):
            return dict(student_or_id)
        if hasattr(student_or_id, "keys") and callable(student_or_id.keys):
            return dict(student_or_id)
        if isinstance(student_or_id, str):
            conn = get_db()
            try:
                row = conn.execute("SELECT * FROM students WHERE student_id = ? OR email = ?", (student_or_id, student_or_id)).fetchone()
                if row:
                    return dict(row)
            finally:
                conn.close()
            return {"student_id": student_or_id, "name": "Student", "email": ""}
        return {"student_id": str(student_or_id), "name": "Student", "email": ""}

    def create_notification(self, student_id: str, notif_type: str, title: str, message: str, severity: str = "info", related_lab_id: Optional[int] = None) -> Optional[int]:
        """Alias for create_in_app_notification."""
        return self.create_in_app_notification(student_id, notif_type, title, message, severity, related_lab_id)

    def create_password_token(self, student_id: str, email: str = "", token_type: str = "RESET", expiry_hours: int = 1) -> str:
        """Alias for generate_password_token."""
        return self.generate_password_token(student_id, email, token_type, expiry_hours)

    def verify_password_token(self, token: str, token_type: Optional[str] = None) -> Tuple[bool, Optional[Dict[str, Any]]]:
        """
        Validates token existence, expiration, and single-use status.
        Returns (is_valid, student_dict).
        """
        if not token:
            return False, None

        conn = get_db()
        try:
            if token_type:
                row = conn.execute(
                    "SELECT * FROM password_tokens WHERE token = ? AND UPPER(token_type) = UPPER(?)",
                    (token, token_type)
                ).fetchone()
            else:
                row = conn.execute("SELECT * FROM password_tokens WHERE token = ?", (token,)).fetchone()

            if not row:
                return False, None

            row_dict = dict(row)
            if row_dict.get("is_used") or row_dict.get("used"):
                return False, None

            expires_at = datetime.datetime.fromisoformat(row_dict["expires_at"].replace("Z", ""))
            if datetime.datetime.utcnow() > expires_at:
                return False, None

            # Retrieve student
            s_row = conn.execute("SELECT * FROM students WHERE student_id = ?", (row_dict["student_id"],)).fetchone()
            student_dict = dict(s_row) if s_row else {"student_id": row_dict["student_id"], "email": row_dict["email"], "name": "Student"}

            return True, student_dict
        finally:
            conn.close()

    def consume_password_token(self, token: str) -> bool:
        """Marks token as used so it can never be reused."""
        conn = get_db()
        try:
            conn.execute("UPDATE password_tokens SET is_used = 1 WHERE token = ?", (token,))
            conn.commit()
            return True
        except Exception:
            conn.rollback()
            return False
        finally:
            conn.close()

    def generate_and_send_otp(self, email: str, expiry_minutes: int = 15) -> Tuple[bool, str, Optional[str]]:
        """
        Generates a 6-digit numeric OTP, records it in password_tokens with expiration,
        and dispatches it to the student's registered email address.
        """
        email_clean = (email or "").strip().lower()
        if not email_clean:
            return False, "Please provide a valid email address.", None

        conn = get_db()
        try:
            student = conn.execute(
                "SELECT * FROM students WHERE LOWER(email) = ?",
                (email_clean,)
            ).fetchone()

            if not student:
                return False, "No student account found registered with this email address.", None

            student_dict = dict(student)
            student_id = student_dict["student_id"]

            # Generate cryptographically secure 6-digit numeric OTP
            otp_code = f"{secrets.randbelow(900000) + 100000}"
            # Suffix with random hex to ensure absolute database uniqueness
            token_val = f"{otp_code}_{secrets.token_hex(8)}"

            now = datetime.datetime.utcnow()
            expires_at = (now + datetime.timedelta(minutes=expiry_minutes)).isoformat()
            now_str = now.isoformat()

            # Invalidate any prior active OTPs for this student
            conn.execute(
                "UPDATE password_tokens SET is_used = 1 WHERE student_id = ? AND token_type = 'OTP' AND is_used = 0",
                (student_id,)
            )

            conn.execute(
                """
                INSERT INTO password_tokens (student_id, email, token, token_type, is_used, expires_at, created_at)
                VALUES (?, ?, ?, 'OTP', 0, ?, ?)
                """,
                (student_id, student_dict.get("email", email_clean), token_val, expires_at, now_str)
            )
            conn.commit()

            # Enqueue email and flush immediately
            email_service.enqueue_email(
                recipient_email=student_dict.get("email", email_clean),
                template="otp_verification.html",
                subject="Your V-Lab Password Reset Verification Code",
                payload={
                    "student_name": student_dict.get("name", "Student"),
                    "otp_code": otp_code,
                    "expiry_minutes": expiry_minutes
                },
                student_id=student_id
            )
            email_service.process_queue()

            return True, "A 6-digit verification code has been dispatched to your email.", otp_code
        except Exception as e:
            if conn:
                conn.rollback()
            return False, f"Failed to generate verification code: {str(e)}", None
        finally:
            conn.close()

    def verify_and_consume_otp(self, email: str, otp: str) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        """
        Validates the 6-digit OTP for the given student email.
        If valid and not expired, marks it as used and returns (True, message, student_dict).
        """
        email_clean = (email or "").strip().lower()
        otp_clean = (otp or "").strip()

        if not email_clean or not otp_clean:
            return False, "Email address and 6-digit OTP code are required.", None

        conn = get_db()
        try:
            student = conn.execute(
                "SELECT * FROM students WHERE LOWER(email) = ?",
                (email_clean,)
            ).fetchone()

            if not student:
                return False, "No student account found registered with this email address.", None

            student_dict = dict(student)
            student_id = student_dict["student_id"]

            row = conn.execute(
                """
                SELECT * FROM password_tokens 
                WHERE student_id = ? AND token_type = 'OTP' AND is_used = 0 
                ORDER BY id DESC LIMIT 1
                """,
                (student_id,)
            ).fetchone()

            if not row:
                return False, "No active verification code found. Please request a new OTP.", None

            row_dict = dict(row)
            expires_at = datetime.datetime.fromisoformat(row_dict["expires_at"].replace("Z", ""))
            if datetime.datetime.utcnow() > expires_at:
                conn.execute("UPDATE password_tokens SET is_used = 1 WHERE id = ?", (row_dict["id"],))
                conn.commit()
                return False, "Verification code has expired. Please request a new OTP.", None

            stored_otp = row_dict["token"].split("_")[0]
            if not secrets.compare_digest(stored_otp, otp_clean):
                return False, "Invalid verification code. Please check your email and enter the correct code.", None

            # Mark OTP consumed
            conn.execute("UPDATE password_tokens SET is_used = 1 WHERE id = ?", (row_dict["id"],))
            conn.commit()

            return True, "Verification code verified successfully.", student_dict
        except Exception as e:
            if conn:
                conn.rollback()
            return False, f"Verification failed: {str(e)}", None
        finally:
            conn.close()


    # -----------------------------------------------------------------------
    # High-Level Event Triggers & Email Notification Dispatch
    # -----------------------------------------------------------------------
    def notify_account_created(self, student: Any, custom_password: Optional[str] = None, raw_password: Optional[str] = None, force: bool = False, is_reset: bool = False):
        """Dispatches in-app notification and welcome/credentials email with set-password link."""
        s = self._resolve_student(student)
        student_id = s["student_id"]
        email = s.get("email")
        pwd = custom_password or raw_password or s.get("default_password")
        if not email:
            return

        token = self.generate_password_token(student_id, email, token_type="SETUP", expiry_hours=48)
        setup_url = f"{email_service.base_url}/reset-password?token={token}"

        # In-App Notification
        if is_reset:
            self.create_in_app_notification(
                student_id=student_id,
                notif_type="ACCOUNT",
                title="Default Password Updated 🔑",
                message="Your laboratory default password has been reset by faculty. Check your email or faculty portal for details.",
                severity="info"
            )
            subject = f"V-Lab Account: New Default Password Generated ({student_id})"
        else:
            self.create_in_app_notification(
                student_id=student_id,
                notif_type="ACCOUNT",
                title="Welcome to Container V-Lab! 👋",
                message="Your student laboratory account is ready. Begin by containerizing your first application in Lab 1 & 2.",
                severity="info"
            )
            subject = "Welcome to V-Lab: Set Your Laboratory Account Password"

        # Enqueue Welcome / Credentials Email (Uses timestamped idempotency key when forced to allow credential resends/resets)
        if force:
            ts = int(datetime.datetime.utcnow().timestamp() * 1000)
            idempotency_key = f"account_created:{student_id}:{ts}"
        else:
            idempotency_key = f"account_created:{student_id}"

        email_service.enqueue_email(
            recipient_email=email,
            template="welcome_setup.html",
            subject=subject,
            payload={
                "student_name": s.get("name", "Student"),
                "student_id": student_id,
                "enrollment_no": s.get("enrollment_no", ""),
                "roll_no": s.get("roll_no", ""),
                "email": email,
                "setup_url": setup_url,
                "default_password": pwd,
                "is_reset": is_reset,
                "base_url": email_service.base_url
            },
            student_id=student_id,
            idempotency_key=idempotency_key
        )

    def notify_password_reset(self, student: Any):
        """Dispatches 1-hour password reset link to student's email."""
        s = self._resolve_student(student)
        student_id = s["student_id"]
        email = s.get("email")
        if not email:
            return

        token = self.generate_password_token(student_id, email, token_type="RESET", expiry_hours=1)
        reset_url = f"{email_service.base_url}/reset-password?token={token}"

        # Idempotency prevents burst duplicate sends within the same minute
        now_min = datetime.datetime.utcnow().strftime("%Y%m%d%H%M")
        idempotency_key = f"password_reset:{student_id}:{now_min}"

        email_service.enqueue_email(
            recipient_email=email,
            template="password_reset.html",
            subject="V-Lab Password Reset Request",
            payload={
                "student_name": s.get("name", "Student"),
                "email": email,
                "reset_url": reset_url
            },
            student_id=student_id,
            idempotency_key=idempotency_key
        )

    def notify_password_changed(self, student: Any):
        """Security alert triggered whenever a password is updated."""
        s = self._resolve_student(student)
        student_id = s["student_id"]
        email = s.get("email")

        # In-App Notification
        self.create_in_app_notification(
            student_id=student_id,
            notif_type="SECURITY",
            title="Password Changed Successfully 🔒",
            message="Your V-Lab account password was recently updated. If you did not make this change, notify your faculty immediately.",
            severity="warning"
        )

        if email:
            now_str = datetime.datetime.utcnow().strftime("%d %b %Y, %I:%M %p UTC")
            idempotency_key = f"password_changed:{student_id}:{int(datetime.datetime.utcnow().timestamp())}"
            email_service.enqueue_email(
                recipient_email=email,
                template="security_alert.html",
                subject="Security Alert: V-Lab Password Changed",
                payload={
                    "student_name": s.get("name", "Student"),
                    "student_id": student_id,
                    "timestamp": now_str
                },
                student_id=student_id,
                idempotency_key=idempotency_key
            )

    def notify_lab_completed(self, student: Any, lab_number: int, lab_title: str, score: Optional[int] = 100):
        """Triggered upon successful lab verification pass."""
        s = self._resolve_student(student)
        student_id = s["student_id"]
        email = s.get("email")

        # In-App Notification
        self.create_in_app_notification(
            student_id=student_id,
            notif_type="LAB_COMPLETED",
            title=f"Lab {lab_number} Completed! 🎉",
            message=f"Automated verification for Lab {lab_number}: {lab_title} passed with {score}%. Verified by host compute node.",
            severity="success",
            related_lab_id=lab_number
        )

        # Idempotency prevents duplicate completion emails even during cluster failovers
        idempotency_key = f"lab_completed:{student_id}:{lab_number}"
        if email:
            now_str = datetime.datetime.utcnow().strftime("%d %b %Y, %I:%M %p UTC")
            email_service.enqueue_email(
                recipient_email=email,
                template="lab_completed.html",
                subject=f"Lab {lab_number} Passed: {lab_title}",
                payload={
                    "student_name": s.get("name", "Student"),
                    "lab_number": lab_number,
                    "lab_title": lab_title,
                    "score": score,
                    "completed_at": now_str
                },
                student_id=student_id,
                idempotency_key=idempotency_key
            )

    def notify_lab_failed(self, student: Any, lab_number: int, lab_title: str, reason: Optional[str] = None, error_message: Optional[str] = None):
        """Triggered upon lab verification failure."""
        s = self._resolve_student(student)
        student_id = s["student_id"]
        email = s.get("email")
        err = reason or error_message or "Missing required container or commands."

        # In-App Notification
        self.create_in_app_notification(
            student_id=student_id,
            notif_type="VERIFICATION",
            title=f"Lab {lab_number} Verification Failed ⚠️",
            message=f"Verification did not pass for Lab {lab_number}: {err}",
            severity="error",
            related_lab_id=lab_number
        )

        if email and err:
            idempotency_key = f"lab_failed:{student_id}:{lab_number}:{int(datetime.datetime.utcnow().timestamp() / 300)}"
            email_service.enqueue_email(
                recipient_email=email,
                template="lab_failed.html",
                subject=f"Lab {lab_number} Verification Issue",
                payload={
                    "student_name": s.get("name", "Student"),
                    "lab_number": lab_number,
                    "lab_title": lab_title,
                    "reason": err,
                    "lab_url": f"{email_service.base_url}/student/lab{lab_number}"
                },
                student_id=student_id,
                idempotency_key=idempotency_key
            )

    def notify_ticket_replied(self, student: Any, ticket_id_or_lab: Any = 1, ticket_title: Optional[str] = None, reply_text: Optional[str] = None, subject: Optional[str] = None, reply_snippet: Optional[str] = None):
        """Triggered when faculty replies to a student issue ticket."""
        s = self._resolve_student(student)
        student_id = s["student_id"]
        email = s.get("email")
        title = ticket_title or subject or "Support Request"
        reply = reply_text or reply_snippet or ""

        # In-App Notification
        self.create_in_app_notification(
            student_id=student_id,
            notif_type="TICKET",
            title="Faculty Replied to Help Ticket 💬",
            message=f"Instructor response regarding '{title}': {reply[:80]}...",
            severity="info",
            related_lab_id=int(ticket_id_or_lab) if str(ticket_id_or_lab).isdigit() else 1
        )

        if email:
            idempotency_key = f"ticket_reply:{student_id}:{hash(reply[:40])}"
            email_service.enqueue_email(
                recipient_email=email,
                template="ticket_update.html",
                subject=f"Faculty Response: {title}",
                payload={
                    "student_name": s.get("name", "Student"),
                    "lab_number": ticket_id_or_lab,
                    "ticket_title": title,
                    "reply_text": reply
                },
                student_id=student_id,
                idempotency_key=idempotency_key
            )


# Global Singleton instance
notification_service = NotificationService()
