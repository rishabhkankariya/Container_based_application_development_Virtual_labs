"""
V-Lab Practice Playground Blueprint
Separate, additive routes for the interactive multi-technology practice environment.
Leaves all existing lab routes, controllers, and worksheets completely untouched.
"""

from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for
from functools import wraps
from backend.services.playground_service import playground_service, ENVIRONMENTS

playground_bp = Blueprint("playground", __name__, url_prefix="")


def playground_auth_required(f):
    """
    Ensures the user is an authenticated Student or Faculty member.
    Rejects unauthenticated requests with 401 for APIs or redirect for pages.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        student_id = session.get("student_id")
        faculty_user = session.get("faculty_username")

        if not student_id and not faculty_user:
            if request.path.startswith("/api/"):
                return jsonify({"ok": False, "error": "Unauthorized: Please log in to access the Practice Playground."}), 401
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated_function


def _get_current_user_and_role():
    """Extracts (user_id, role, display_name) from current session."""
    student_id = session.get("student_id")
    faculty_user = session.get("faculty_username")

    if student_id:
        return str(student_id), "student", f"Student {student_id}"
    elif faculty_user:
        return str(faculty_user), "faculty", f"Faculty ({faculty_user})"
    return "guest", "guest", "Guest"


@playground_bp.route("/playground")
@playground_auth_required
def playground_view():
    """Renders the standalone multi-technology Practice Playground interface."""
    user_id, role, display_name = _get_current_user_and_role()
    requested_env = request.args.get("env", "linux")
    if requested_env not in ENVIRONMENTS:
        requested_env = "linux"

    # Get or create session for requested environment
    active_session = playground_service.get_or_create_session(user_id, role, requested_env)
    user_sessions = playground_service.list_user_sessions(user_id)

    return render_template(
        "playground.html",
        user_id=user_id,
        role=role,
        display_name=display_name,
        current_env=requested_env,
        active_session=active_session,
        environments=ENVIRONMENTS,
        recent_sessions=user_sessions
    )


@playground_bp.route("/api/playground/session/start", methods=["POST"])
@playground_auth_required
def api_session_start():
    """Starts or switches to an active session for the selected environment."""
    user_id, role, _ = _get_current_user_and_role()
    data = request.get_json(force=True, silent=True) or {}
    env_type = data.get("env_type", "linux")

    session_info = playground_service.get_or_create_session(user_id, role, env_type)
    return jsonify({"ok": True, "session": session_info})


@playground_bp.route("/api/playground/session/reset", methods=["POST"])
@playground_auth_required
def api_session_reset():
    """Resets the workspace files of a user's session back to clean defaults."""
    user_id, _, _ = _get_current_user_and_role()
    data = request.get_json(force=True, silent=True) or {}
    session_id = data.get("session_id", "")

    ok, msg = playground_service.reset_session(session_id, user_id)
    return jsonify({"ok": ok, "message": msg}), (200 if ok else 400)


@playground_bp.route("/api/playground/session/stop", methods=["POST"])
@playground_auth_required
def api_session_stop():
    """Stops an active session."""
    user_id, _, _ = _get_current_user_and_role()
    data = request.get_json(force=True, silent=True) or {}
    session_id = data.get("session_id", "")

    ok, msg = playground_service.stop_session(session_id, user_id)
    return jsonify({"ok": ok, "message": msg}), (200 if ok else 400)


@playground_bp.route("/api/playground/exec", methods=["POST"])
@playground_auth_required
def api_exec_command():
    """Executes a command inside the user's isolated session workspace."""
    user_id, _, _ = _get_current_user_and_role()
    data = request.get_json(force=True, silent=True) or {}
    session_id = data.get("session_id", "")
    cmd = data.get("command", "")

    res = playground_service.execute_command(session_id, user_id, cmd)
    return jsonify(res)


@playground_bp.route("/api/playground/files", methods=["GET"])
@playground_auth_required
def api_list_files():
    """Lists files in the user's session workspace."""
    user_id, _, _ = _get_current_user_and_role()
    session_id = request.args.get("session_id", "")

    res = playground_service.list_files(session_id, user_id)
    return jsonify(res), (200 if res.get("ok") else 403)


@playground_bp.route("/api/playground/file/read", methods=["GET"])
@playground_auth_required
def api_read_file():
    """Reads a file from the user's session workspace."""
    user_id, _, _ = _get_current_user_and_role()
    session_id = request.args.get("session_id", "")
    filename = request.args.get("filename", "")

    res = playground_service.read_file(session_id, user_id, filename)
    return jsonify(res), (200 if res.get("ok") else 403)


@playground_bp.route("/api/playground/file/save", methods=["POST"])
@playground_auth_required
def api_save_file():
    """Saves a file in the user's session workspace."""
    user_id, _, _ = _get_current_user_and_role()
    data = request.get_json(force=True, silent=True) or {}
    session_id = data.get("session_id", "")
    filename = data.get("filename", "")
    content = data.get("content", "")

    res = playground_service.save_file(session_id, user_id, filename, content)
    return jsonify(res), (200 if res.get("ok") else 403)
