"""
Production multi-user web application for ApplyJob AI.

Features:
  - User registration & login (Flask-Login + bcrypt)
  - Per-user profile, credentials, and settings management
  - Per-user job automation with start/stop controls
  - Real-time log streaming
  - Application history with stats
  - Admin overview
"""
import os
import sys
import json
from datetime import datetime

from dotenv import load_dotenv
from flask import Flask, render_template, request, redirect, url_for, jsonify, flash, session
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user

from app.database import (
    init_db,
    create_user,
    verify_user,
    get_user_by_id,
    get_all_users,
    get_profile,
    save_profile,
    get_credentials,
    save_credentials_bulk,
    get_settings,
    save_settings,
    get_applications,
    get_application_stats,
    get_run_history,
)
from app.task_manager import start_run, stop_run, get_user_status

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "applyjob-super-secret-key-change-in-prod")

# ═══════════════════════════════════════════════════════════════════════════════
# Flask-Login setup
# ═══════════════════════════════════════════════════════════════════════════════

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"
login_manager.login_message_category = "info"


class User(UserMixin):
    def __init__(self, user_data: dict):
        self.id = user_data["id"]
        self.email = user_data["email"]
        self.full_name = user_data.get("full_name", "")
        self.is_admin = bool(user_data.get("is_admin", 0))


@login_manager.user_loader
def load_user(user_id):
    data = get_user_by_id(int(user_id))
    if data:
        return User(data)
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# Auth routes
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")

        user_data = verify_user(email, password)
        if user_data:
            user = User(user_data)
            login_user(user, remember=True)
            next_page = request.args.get("next")
            return redirect(next_page or url_for("dashboard"))
        else:
            flash("Invalid email or password.", "error")

    return render_template("login.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")
        full_name = request.form.get("full_name", "").strip()

        if not email or not password:
            flash("Email and password are required.", "error")
        elif password != confirm:
            flash("Passwords do not match.", "error")
        elif len(password) < 6:
            flash("Password must be at least 6 characters.", "error")
        else:
            user_id = create_user(email, password, full_name)
            if user_id:
                flash("Account created! Please log in.", "success")
                return redirect(url_for("login"))
            else:
                flash("An account with this email already exists.", "error")

    return render_template("register.html")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))


# ═══════════════════════════════════════════════════════════════════════════════
# Dashboard
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/")
@login_required
def dashboard():
    stats = get_application_stats(current_user.id)
    settings = get_settings(current_user.id)
    status = get_user_status(current_user.id)
    recent_apps = get_applications(current_user.id, limit=5)
    return render_template(
        "dashboard.html",
        stats=stats,
        settings=settings,
        status=status,
        recent_apps=recent_apps,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Profile & Credentials
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile_editor():
    if request.method == "POST":
        # Save profile fields
        profile_fields = {}
        creds = {
            "linkedin": {},
            "naukri": {},
            "foundit": {},
            "monster": {},
        }
        settings_data = {}

        for key, value in request.form.items():
            # Credential fields: portal__key format
            if "__" in key:
                portal, cred_key = key.split("__", 1)
                if portal in creds:
                    creds[portal][cred_key] = value
                continue

            # Settings fields
            if key.startswith("setting_"):
                settings_data[key.replace("setting_", "")] = value
                continue

            # Everything else is profile data
            profile_fields[key] = value

        save_profile(current_user.id, profile_fields)
        save_credentials_bulk(current_user.id, creds)
        if settings_data:
            save_settings(current_user.id, settings_data)

        flash("Profile and settings saved!", "success")
        return redirect(url_for("profile_editor"))

    profile = get_profile(current_user.id)
    credentials = get_credentials(current_user.id)
    settings = get_settings(current_user.id)
    return render_template(
        "user_profile.html",
        profile=profile,
        credentials=credentials,
        settings=settings,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Application History
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/history")
@login_required
def history():
    apps = get_applications(current_user.id, limit=200)
    stats = get_application_stats(current_user.id)
    return render_template("user_history.html", apps=apps, stats=stats)


# ═══════════════════════════════════════════════════════════════════════════════
# Run controls (AJAX)
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/api/run", methods=["POST"])
@login_required
def api_run():
    data = request.get_json(silent=True) or {}
    portals = data.get("portals", ["linkedin", "naukri", "foundit", "monster"])
    result = start_run(current_user.id, portals)
    return jsonify(result)


@app.route("/api/stop", methods=["POST"])
@login_required
def api_stop():
    data = request.get_json(silent=True) or {}
    run_id = data.get("run_id")
    result = stop_run(current_user.id, run_id)
    return jsonify(result)


@app.route("/api/status")
@login_required
def api_status():
    return jsonify(get_user_status(current_user.id))


# ═══════════════════════════════════════════════════════════════════════════════
# Admin (optional — first registered user is admin)
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/admin")
@login_required
def admin_panel():
    if not current_user.is_admin:
        flash("Access denied.", "error")
        return redirect(url_for("dashboard"))

    users = get_all_users()
    user_statuses = {}
    for u in users:
        user_statuses[u["id"]] = get_user_status(u["id"])

    return render_template("admin.html", users=users, user_statuses=user_statuses)


# ═══════════════════════════════════════════════════════════════════════════════
# Startup
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    init_db()
    print("✅ Database initialized.")
    print("🚀 Starting ApplyJob AI — Multi-User Production Server")
    app.run(host="0.0.0.0", port=5001, debug=True, threaded=True)
