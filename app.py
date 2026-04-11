import os
import time
import json
import uuid
import copy
import secrets
import logging
import sqlite3
from datetime import timedelta

import numpy as np
import pandas as pd

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

import seaborn as sns
import plotly.express as px

from flask import (
    Flask, render_template, request, redirect, url_for,
    flash, session, Response, send_file, has_request_context, jsonify
)

from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash

from settings import DEFAULT_APP_SETTINGS, DEFAULT_USER_SETTINGS
from service.service import DataService
from datetime import timedelta


# APP
app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "dvms-dev-secret")
app.permanent_session_lifetime = timedelta(days=30)


logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


# CONFIG
DATA_PATH_DEFAULT = os.getenv("DATA_PATH", "data.xlsx")

UPLOAD_DIR = "uploads"
STORAGE_DIR = "storage"
STATIC_DIR = "static"

DB_PATH = os.path.join(STORAGE_DIR, "dvms.sqlite")
DATA_TABLE = "dvms_data"
META_TABLE = "dvms_meta"

ALLOWED_EXTENSIONS = {".csv", ".xls", ".xlsx"}

ROTATION = 40
FONT_SIZE = 7

ADMIN_USER = os.getenv("ADMIN_USER", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")


# DIR / DB
def init_dirs():
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    os.makedirs(STORAGE_DIR, exist_ok=True)
    os.makedirs(STATIC_DIR, exist_ok=True)
    os.makedirs(os.path.join(STATIC_DIR, "avatars"), exist_ok=True)


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def table_exists(name: str) -> bool:
    connection = db()
    try:
        cur = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (name,)
        )
        return cur.fetchone() is not None
    finally:
        connection.close()


def ensure_columns(conn, table_name: str, columns: dict):
    existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table_name})").fetchall()}
    for col, typ in columns.items():
        if col not in existing:
            conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {col} {typ}")


def qident(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def init_db():
    init_dirs()
    connection = db()
    try:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS users(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL
            )
        """)

        connection.execute("""
            CREATE TABLE IF NOT EXISTS presets(
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                url TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                owner TEXT NOT NULL
            )
        """)

        connection.execute(f"""
            CREATE TABLE IF NOT EXISTS {META_TABLE}(
                k TEXT PRIMARY KEY,
                v TEXT NOT NULL
            )
        """)

        connection.execute("""
            CREATE TABLE IF NOT EXISTS user_profiles(
                username TEXT PRIMARY KEY,
                name TEXT,
                first_name TEXT,
                last_name TEXT,
                email TEXT,
                phone TEXT,
                sex TEXT,
                birthdate TEXT,
                address TEXT,
                bio TEXT,
                avatar_path TEXT,
                updated_at INTEGER
            )
        """)

        # User settings
        connection.execute("""
            CREATE TABLE IF NOT EXISTS user_settings(
                username TEXT PRIMARY KEY,
                settings_json TEXT NOT NULL,
                updated_at INTEGER NOT NULL
            )
        """)

        # Password reset tokens
        connection.execute("""
            CREATE TABLE IF NOT EXISTS password_resets(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                token TEXT NOT NULL,
                expires_at INTEGER NOT NULL,
                used INTEGER NOT NULL DEFAULT 0
            )
        """)

        # Safe migrations 
        ensure_columns(connection, "user_profiles", {
            "first_name": "TEXT",
            "last_name": "TEXT",
            "sex": "TEXT",
            "birthdate": "TEXT"
        })

        connection.commit()

        # Ensure admin exists
        cur = connection.execute("SELECT username FROM users WHERE username=?", (ADMIN_USER,))
        if cur.fetchone() is None:
            connection.execute(
                "INSERT INTO users(username, password_hash, role) VALUES(?,?,?)",
                (ADMIN_USER, generate_password_hash(ADMIN_PASSWORD), "admin")
            )
            connection.commit()
            logger.info("Admin account created (change ADMIN_PASSWORD env for security).")
    finally:
        connection.close()


# META
def meta_get(key: str, default=None):
    connection = db()
    try:
        row = connection.execute(f"SELECT v FROM {META_TABLE} WHERE k=?", (key,)).fetchone()
        return row["v"] if row else default
    finally:
        connection.close()


def meta_set(key: str, value: str):
    connection = db()
    try:
        connection.execute(
            f"INSERT INTO {META_TABLE}(k,v) VALUES(?,?) "
            f"ON CONFLICT(k) DO UPDATE SET v=excluded.v",
            (key, str(value))
        )
        connection.commit()
    finally:
        connection.close()


#  SETTINGS HELPERS 
def _safe_json_load(s, default):
    try:
        return json.loads(s) if s else copy.deepcopy(default)
    except Exception:
        return copy.deepcopy(default)


def get_app_settings():
    raw = meta_get("app_settings", "")
    data = _safe_json_load(raw, DEFAULT_APP_SETTINGS)

    merged = copy.deepcopy(DEFAULT_APP_SETTINGS)
    merged.update(data or {})

    # nested merge
    if "social_links" in DEFAULT_APP_SETTINGS:
        merged["social_links"] = copy.deepcopy(DEFAULT_APP_SETTINGS["social_links"])
        merged["social_links"].update((data or {}).get("social_links", {}) or {})

    return merged


def set_app_settings(settings_dict):
    meta_set("app_settings", json.dumps(settings_dict))


def get_user_settings(username: str):
    if not username:
        return copy.deepcopy(DEFAULT_USER_SETTINGS)

    conn = db()
    try:
        row = conn.execute("SELECT settings_json FROM user_settings WHERE username=?", (username,)).fetchone()
        data = _safe_json_load(row["settings_json"] if row else "", DEFAULT_USER_SETTINGS)
    finally:
        conn.close()

    merged = copy.deepcopy(DEFAULT_USER_SETTINGS)
    merged.update(data or {})
    return merged


def set_user_settings(username: str, settings_dict):
    conn = db()
    try:
        conn.execute("""
            INSERT INTO user_settings(username, settings_json, updated_at)
            VALUES(?,?,?)
            ON CONFLICT(username) DO UPDATE SET
                settings_json=excluded.settings_json,
                updated_at=excluded.updated_at
        """, (username, json.dumps(settings_dict), int(time.time())))
        conn.commit()
    finally:
        conn.close()


# ---------- AUTH HELPERS ----------
def current_user():
    return session.get("user") if has_request_context() else None


def current_role():
    return session.get("role", "viewer") if has_request_context() else "viewer"


def login_required():
    return current_user() is not None


def admin_required():
    return login_required() and current_role() == "admin"


@app.before_request
def enforce_maintenance_mode():
    try:
        s = get_app_settings()
        if s.get("maintenance_mode") and request.endpoint not in ("login", "logout", "static"):
            if not admin_required():
                flash("System is in maintenance mode. Please try again later.", "warning")
                return redirect(url_for("login"))
    except Exception:
        pass


# DATASET HELPERS
def active_data_path():
    if has_request_context():
        return session.get("DATA_PATH_ACTIVE", meta_get("active_dataset_path", DATA_PATH_DEFAULT))
    return meta_get("active_dataset_path", DATA_PATH_DEFAULT)


def import_to_sqlite(path: str):
    ds = DataService(path)
    df = ds.load_df().copy()

    # normalize columns
    df.columns = [str(c).strip().replace(" ", "_") for c in df.columns]

    connection = db()
    try:
        df.to_sql(DATA_TABLE, connection, if_exists="replace", index=False)
        connection.commit()
    finally:
        connection.close()

    meta_set("active_dataset_path", path)
    meta_set("imported_at", str(int(time.time())))


def ensure_dataset_imported():
    init_db()
    if not table_exists(DATA_TABLE):
        import_to_sqlite(DATA_PATH_DEFAULT)


def get_columns():
    conn = db()
    try:
        rows = conn.execute(f"PRAGMA table_info({DATA_TABLE})").fetchall()
        return [r["name"] for r in rows]
    finally:
        conn.close()


def sample_df(limit=5000, where_clause="", params=None):
    conn = db()
    try:
        q = f"SELECT * FROM {DATA_TABLE}{where_clause} LIMIT {int(limit)}"
        return pd.read_sql_query(q, conn, params=params or [])
    finally:
        conn.close()


def pick_columns():
    cols = get_columns()
    df_sample = sample_df(400)
    num = df_sample.select_dtypes(include="number").columns.tolist()
    nonnum = [c for c in cols if c not in num]
    x = nonnum[0] if nonnum else None
    y = num[0] if num else None
    return x, y


def detect_date_column():
    cols = get_columns()
    for c in cols:
        lc = c.lower()
        if "date" in lc or "year" in lc:
            return c
    return None


# FILTER / SEARCH / SORT
def safe_float(s):
    try:
        return float(s)
    except:
        return None


def build_where(x_col, y_col, date_col, scatter_cols=None):
    where = []
    params = []

    category = request.args.get("category", "").strip()
    date_val = request.args.get("date", "").strip()
    mn = safe_float(request.args.get("min", "").strip()) if request.args.get("min") else None
    mx = safe_float(request.args.get("max", "").strip()) if request.args.get("max") else None
    search = request.args.get("search", "").strip().lower()

    if category and x_col:
        where.append(f"CAST({qident(x_col)} AS TEXT) = ?")
        params.append(category)

    if date_val and date_col:
        where.append(f"CAST({qident(date_col)} AS TEXT) = ?")
        params.append(date_val)

    # numeric range
    if scatter_cols and len(scatter_cols) >= 2:
        a, b = scatter_cols[0], scatter_cols[1]
        if mn is not None:
            where.append(f"({qident(a)} >= ? AND {qident(b)} >= ?)")
            params.extend([mn, mn])
        if mx is not None:
            where.append(f"({qident(a)} <= ? AND {qident(b)} <= ?)")
            params.extend([mx, mx])
    else:
        if y_col:
            if mn is not None:
                where.append(f"{qident(y_col)} >= ?")
                params.append(mn)
            if mx is not None:
                where.append(f"{qident(y_col)} <= ?")
                params.append(mx)

    # search across all columns
    if search:
        cols = get_columns()
        parts = []
        for c in cols:
            parts.append(f"LOWER(CAST({qident(c)} AS TEXT)) LIKE ?")
            params.append(f"%{search}%")
        where.append("(" + " OR ".join(parts) + ")")

    clause = (" WHERE " + " AND ".join(where)) if where else ""
    return clause, params


def build_filter_options(x_col, date_col):
    conn = db()
    try:
        categories = []
        if x_col:
            df = pd.read_sql_query(
                f"SELECT CAST({qident(x_col)} AS TEXT) AS v, COUNT(*) AS c "
                f"FROM {DATA_TABLE} WHERE {qident(x_col)} IS NOT NULL "
                f"GROUP BY v ORDER BY c DESC LIMIT 200",
                conn
            )
            categories = df["v"].tolist()

        date_values = []
        if date_col:
            df = pd.read_sql_query(
                f"SELECT CAST({qident(date_col)} AS TEXT) AS v, COUNT(*) AS c "
                f"FROM {DATA_TABLE} WHERE {qident(date_col)} IS NOT NULL "
                f"GROUP BY v ORDER BY c DESC LIMIT 200",
                conn
            )
            date_values = df["v"].tolist()

        return categories, date_values
    finally:
        conn.close()


def compute_kpis(where_clause, params, x_col, y_col):
    cols = get_columns()
    conn = db()
    try:
        total_rows = int(pd.read_sql_query(
            f"SELECT COUNT(*) AS c FROM {DATA_TABLE}{where_clause}",
            conn, params=params
        ).loc[0, "c"])

        distinct = 0
        if x_col:
            distinct = int(pd.read_sql_query(
                f"SELECT COUNT(DISTINCT CAST({qident(x_col)} AS TEXT)) AS d FROM {DATA_TABLE}{where_clause}",
                conn, params=params
            ).loc[0, "d"])

        y_min = y_mean = y_max = None
        if y_col:
            stats = pd.read_sql_query(
                f"SELECT MIN({qident(y_col)}) AS mn, AVG({qident(y_col)}) AS av, MAX({qident(y_col)}) AS mx FROM {DATA_TABLE}{where_clause}",
                conn, params=params
            )
            y_min = stats.loc[0, "mn"]
            y_mean = stats.loc[0, "av"]
            y_max = stats.loc[0, "mx"]

        df_sample = pd.read_sql_query(f"SELECT * FROM {DATA_TABLE} LIMIT 300", conn)
        numeric_cols = len(df_sample.select_dtypes(include="number").columns)
    finally:
        conn.close()

    return {
        "rows": total_rows,
        "cols": len(cols),
        "numeric_cols": numeric_cols,
        "distinct_categories": distinct,
        "y_min": y_min,
        "y_mean": y_mean,
        "y_max": y_max,
    }


# HEATMAP PNG
def save_heatmap_png(df_any, out_path, title="Correlation Heatmap", small=True):
    plt.close("all")
    num_df = df_any.select_dtypes(include="number").dropna(axis=1, how="all")

    if not num_df.empty:
        nunique = num_df.nunique(dropna=True)
        num_df = num_df.loc[:, nunique > 1]

    if num_df.shape[1] < 2:
        plt.figure(figsize=(4, 3) if small else (10, 8))
        plt.axis("off")
        plt.text(0.5, 0.5, "Not enough numeric columns\nfor correlation heatmap",
                 ha="center", va="center", fontsize=10)
        plt.tight_layout()
        plt.savefig(out_path, dpi=140, bbox_inches="tight")
        plt.close()
        return

    corr = num_df.corr()
    if corr.isna().all().all():
        plt.figure(figsize=(4, 3) if small else (10, 8))
        plt.axis("off")
        plt.text(0.5, 0.5, "Correlation could not be computed\n(check numeric data)",
                 ha="center", va="center", fontsize=10)
        plt.tight_layout()
        plt.savefig(out_path, dpi=140, bbox_inches="tight")
        plt.close()
        return

    plt.figure(figsize=(4.8, 3.2) if small else (10, 8))
    ax = sns.heatmap(corr, cmap="coolwarm", annot=False, square=True, linewidths=0.5, cbar=False)
    ax.set_title(title, fontsize=10)
    plt.xticks(rotation=ROTATION, ha="right", fontsize=FONT_SIZE)
    plt.yticks(fontsize=FONT_SIZE)
    plt.tight_layout(pad=0.3)
    plt.savefig(out_path, dpi=140, bbox_inches="tight")
    plt.close()


# CONTEXT
def base_context():
    x_col, y_col = pick_columns()
    date_col = detect_date_column()
    categories, date_values = build_filter_options(x_col, date_col)

    conn = db()
    try:
        presets = conn.execute("SELECT * FROM presets ORDER BY created_at DESC LIMIT 10").fetchall()
    finally:
        conn.close()

    # Navbar profile
    nav_profile = None
    if current_user():
        conn2 = db()
        try:
            p = conn2.execute("SELECT name, email, avatar_path FROM user_profiles WHERE username=?",
                              (current_user(),)).fetchone()
        finally:
            conn2.close()

        nav_profile = {
            "display_name": (p["name"] if p and p["name"] else current_user()),
            "email": (p["email"] if p and p["email"] else f"{current_user()}@dvms.local"),
            "avatar_url": (p["avatar_path"] if p and p["avatar_path"] else "")
        }

    return dict(
        user=current_user(),
        role=current_role(),
        nav_profile=nav_profile,
        app_settings=get_app_settings(),
        user_settings=get_user_settings(current_user() or ""),
        active_data_path=active_data_path(),
        categories=categories,
        date_values=date_values,
        presets=presets,
        filters={
            "category": request.args.get("category", ""),
            "date": request.args.get("date", ""),
            "min": request.args.get("min", ""),
            "max": request.args.get("max", ""),
            "search": request.args.get("search", ""),
            "sort": request.args.get("sort", ""),
            "order": request.args.get("order", "asc"),
        },
        share_url=request.url,
        export_url=url_for("export_csv", **request.args.to_dict(flat=True))
    )


# AUTH ROUTES
@app.route("/login", methods=["GET", "POST"])
def login():
    init_db()
    ensure_dataset_imported()

    ctx = base_context()

    ctx["open_tab"] = "login"
    ctx["prefill_username"] = request.args.get("username", "")

    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = (request.form.get("password") or "").strip()
        remember = (request.form.get("remember") == "1")

        ctx["prefill_username"] = username

        # Missing fields
        if not username or not password:
            flash("Please enter username and password.", "danger")
            return render_template("login.html", **ctx, title="Login")

        # Lookup user
        conn = db()
        try:
            user_row = conn.execute(
                "SELECT username, password_hash, role FROM users WHERE username=?",
                (username,)
            ).fetchone()
        finally:
            conn.close()

        # Account not found
        if not user_row:
            flash("Account does not exist. Please create an account.", "danger")
            ctx["open_tab"] = "register"
            return render_template("login.html", **ctx, title="Login")

        # Wrong password
        if not check_password_hash(user_row["password_hash"], password):
            flash("Incorrect password. Please try again.", "danger")
            return render_template("login.html", **ctx, title="Login")

        # Success
        session.clear()
        session["user"] = user_row["username"]
        session["role"] = user_row["role"]
        session.permanent = remember

        flash("Login successful. Welcome!", "success")
        return redirect(url_for("index"))

    # GET
    return render_template("login.html", **ctx, title="Login")


@app.route("/register", methods=["POST"])
def register():
    init_db()
    ensure_dataset_imported()

    if not get_app_settings().get("allow_registration", True):
        flash("Registration is disabled by admin.", "danger")
        return redirect(url_for("login") + "#register")

    first_name = (request.form.get("first_name") or "").strip()
    last_name = (request.form.get("last_name") or "").strip()
    email = (request.form.get("email") or "").strip()
    phone = (request.form.get("phone") or "").strip()
    sex = (request.form.get("sex") or "").strip()
    birthdate = (request.form.get("birthdate") or "").strip()

    username = (request.form.get("username") or "").strip()
    password = (request.form.get("password") or "").strip()
    confirm = (request.form.get("confirm_password") or "").strip()

    if len(first_name) < 2 or len(last_name) < 2:
        flash("Please enter first name and last name.", "danger")
        return redirect(url_for("login") + "#register")

    if "@" not in email or "." not in email:
        flash("Please enter a valid email address.", "danger")
        return redirect(url_for("login") + "#register")

    if len(username) < 3:
        flash("Username must be at least 3 characters.", "danger")
        return redirect(url_for("login") + "#register")

    reserved = {ADMIN_USER.lower(), "admin", "administrator", "root"}
    if username.lower() in reserved:
        flash("This username is reserved. Choose another username.", "danger")
        return redirect(url_for("login") + "#register")

    if len(password) < 6:
        flash("Password must be at least 6 characters.", "danger")
        return redirect(url_for("login") + "#register")

    if password != confirm:
        flash("Passwords do not match.", "danger")
        return redirect(url_for("login") + "#register")

    if sex not in ("Female", "Male", "Prefer not to say"):
        flash("Please select sex.", "danger")
        return redirect(url_for("login") + "#register")

    role = "viewer"
    full_name = (first_name + " " + last_name).strip()

    connection = db()
    try:
        exists = connection.execute("SELECT username FROM users WHERE username=?", (username,)).fetchone()
        if exists:
            flash("Username already exists. Try another.", "danger")
            return redirect(url_for("login") + "#register")

        connection.execute(
            "INSERT INTO users(username, password_hash, role) VALUES(?,?,?)",
            (username, generate_password_hash(password), role)
        )

        # Save
        connection.execute("""
            INSERT INTO user_profiles(username, name, first_name, last_name, email, phone, sex, birthdate, updated_at)
            VALUES(?,?,?,?,?,?,?,?,?)
            ON CONFLICT(username) DO UPDATE SET
                name=excluded.name,
                first_name=excluded.first_name,
                last_name=excluded.last_name,
                email=excluded.email,
                phone=excluded.phone,
                sex=excluded.sex,
                birthdate=excluded.birthdate,
                updated_at=excluded.updated_at
        """, (username, full_name, first_name, last_name, email, phone, sex, birthdate, int(time.time())))

        connection.commit()
    finally:
        connection.close()

    flash("Account created successfully! You can login now.", "success")
    return redirect(url_for("login", username=username))


@app.route("/forgot", methods=["POST"])
def forgot_password():
    init_db()
    ensure_dataset_imported()

    identifier = (request.form.get("identifier") or "").strip()
    if not identifier:
        flash("Please enter your username or email.", "danger")
        return redirect(url_for("login"))

    conn = db()
    try:
        row = conn.execute("""
            SELECT u.username
            FROM users u
            LEFT JOIN user_profiles p ON p.username = u.username
            WHERE u.username = ? OR (p.email IS NOT NULL AND p.email = ?)
        """, (identifier, identifier)).fetchone()

        if not row:
            flash("If that account exists, you will receive recovery instructions.", "info")
            return redirect(url_for("login"))

        username = row["username"]
        token = secrets.token_urlsafe(24)
        expires_at = int(time.time()) + 15 * 60

        conn.execute("""
            INSERT INTO password_resets(username, token, expires_at, used)
            VALUES(?,?,?,0)
        """, (username, token, expires_at))
        conn.commit()
    finally:
        conn.close()

    flash(f"Password reset token (demo): {token} — valid for 15 minutes.", "info")
    return redirect(url_for("login"))


@app.route("/reset", methods=["POST"])
def reset_password():
    init_db()
    ensure_dataset_imported()

    token = (request.form.get("token") or "").strip()
    new_pw = (request.form.get("new_password") or "").strip()
    confirm = (request.form.get("confirm_new_password") or "").strip()

    if len(new_pw) < 6:
        flash("New password must be at least 6 characters.", "danger")
        return redirect(url_for("login"))

    if new_pw != confirm:
        flash("New passwords do not match.", "danger")
        return redirect(url_for("login"))

    conn = db()
    try:
        row = conn.execute("""
            SELECT id, username, expires_at, used
            FROM password_resets
            WHERE token = ?
            ORDER BY id DESC
            LIMIT 1
        """, (token,)).fetchone()

        if not row:
            flash("Invalid reset token.", "danger")
            return redirect(url_for("login"))

        if row["used"] == 1:
            flash("Reset token already used.", "danger")
            return redirect(url_for("login"))

        if int(time.time()) > int(row["expires_at"]):
            flash("Reset token expired.", "danger")
            return redirect(url_for("login"))

        conn.execute("UPDATE users SET password_hash=? WHERE username=?",
                     (generate_password_hash(new_pw), row["username"]))
        conn.execute("UPDATE password_resets SET used=1 WHERE id=?", (row["id"],))
        conn.commit()
    finally:
        conn.close()

    flash("Password updated successfully. Please login.", "success")
    return redirect(url_for("login"))


@app.route("/logout")
def logout():
    session.clear()
    flash("Logged out.", "info")
    return redirect(url_for("login"))


# ACCOUNT 
@app.route("/account")
def account():
    init_db()
    ensure_dataset_imported()
    if not login_required():
        return redirect(url_for("login"))

    ctx = base_context()
    username = current_user()

    # defaults
    name = username
    email = f"{username}@dvms.local"
    phone = ""
    address = ""
    bio = "Hi, Welcome to DVMS."
    avatar_url = ""

    conn = db()
    try:
        prof = conn.execute("SELECT * FROM user_profiles WHERE username=?", (username,)).fetchone()
        if prof:
            name = prof["name"] or name
            email = prof["email"] or email
            phone = prof["phone"] or phone
            address = prof["address"] or address
            bio = prof["bio"] or bio
            avatar_url = prof["avatar_path"] or ""
    finally:
        conn.close()

    ctx.update(profile_info={
        "name": name,
        "role": current_role(),
        "email": email,
        "phone": phone,
        "address": address,
        "status": "Active",
        "bio": bio,
        "avatar_url": avatar_url
    })
    return render_template("account.html", **ctx, title="My Account")


@app.route("/account/update", methods=["POST"])
def account_update():
    init_db()
    ensure_dataset_imported()
    if not login_required():
        return redirect(url_for("login"))

    username = current_user()
    name = (request.form.get("name") or "").strip()
    email = (request.form.get("email") or "").strip()
    phone = (request.form.get("phone") or "").strip()
    address = (request.form.get("address") or "").strip()
    bio = (request.form.get("bio") or "").strip()

    conn = db()
    try:
        conn.execute("""
            INSERT INTO user_profiles(username, name, email, phone, address, bio, updated_at)
            VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(username) DO UPDATE SET
                name=excluded.name,
                email=excluded.email,
                phone=excluded.phone,
                address=excluded.address,
                bio=excluded.bio,
                updated_at=excluded.updated_at
        """, (username, name, email, phone, address, bio, int(time.time())))
        conn.commit()
    finally:
        conn.close()

    flash("Profile updated successfully.", "success")
    return redirect(url_for("account"))


@app.route("/account/password", methods=["POST"])
def account_password():
    init_db()
    ensure_dataset_imported()
    if not login_required():
        return redirect(url_for("login"))

    current_pw = request.form.get("current_password", "")
    new_pw = request.form.get("new_password", "")
    confirm_pw = request.form.get("confirm_new_password", "")

    if len(new_pw) < 6:
        flash("New password must be at least 6 characters.", "danger")
        return redirect(url_for("account") + "#security")

    if new_pw != confirm_pw:
        flash("New passwords do not match.", "danger")
        return redirect(url_for("account") + "#security")

    conn = db()
    try:
        row = conn.execute("SELECT password_hash FROM users WHERE username=?", (current_user(),)).fetchone()
        if not row or not check_password_hash(row["password_hash"], current_pw):
            flash("Current password is incorrect.", "danger")
            return redirect(url_for("account") + "#security")

        conn.execute("UPDATE users SET password_hash=? WHERE username=?",
                     (generate_password_hash(new_pw), current_user()))
        conn.commit()
    finally:
        conn.close()

    flash("Password updated successfully.", "success")
    return redirect(url_for("account") + "#security")


@app.route("/account/avatar", methods=["POST"])
def account_avatar():
    init_db()
    ensure_dataset_imported()
    if not login_required():
        return redirect(url_for("login"))

    f = request.files.get("avatar")
    if not f or f.filename.strip() == "":
        flash("No image selected.", "danger")
        return redirect(url_for("account"))

    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in [".png", ".jpg", ".jpeg"]:
        flash("Unsupported image type. Use PNG/JPG.", "danger")
        return redirect(url_for("account"))

    filename = f"{int(time.time())}_{secure_filename(f.filename)}"
    save_path = os.path.join(STATIC_DIR, "avatars", filename)
    f.save(save_path)

    public_url = url_for("static", filename=f"avatars/{filename}")

    conn = db()
    try:
        conn.execute("""
            INSERT INTO user_profiles(username, avatar_path, updated_at)
            VALUES(?,?,?)
            ON CONFLICT(username) DO UPDATE SET
                avatar_path=excluded.avatar_path,
                updated_at=excluded.updated_at
        """, (current_user(), public_url, int(time.time())))
        conn.commit()
    finally:
        conn.close()

    flash("Profile image updated.", "success")
    return redirect(url_for("account"))


@app.route("/account/delete", methods=["POST"])
def account_delete():
    init_db()
    ensure_dataset_imported()
    if not login_required():
        return redirect(url_for("login"))

    if current_role() == "admin":
        flash("Admin account cannot be deleted from UI.", "danger")
        return redirect(url_for("account") + "#danger")

    confirm = (request.form.get("confirm_text") or "").strip().upper()
    if confirm != "DELETE":
        flash("Type DELETE to confirm.", "danger")
        return redirect(url_for("account") + "#danger")

    username = current_user()
    conn = db()
    try:
        conn.execute("DELETE FROM user_profiles WHERE username=?", (username,))
        conn.execute("DELETE FROM user_settings WHERE username=?", (username,))
        conn.execute("DELETE FROM users WHERE username=?", (username,))
        conn.commit()
    finally:
        conn.close()

    session.clear()
    flash("Account deleted.", "info")
    return redirect(url_for("login"))

# SETTINGS 
@app.route("/settings", methods=["GET", "POST"])
def settings_page():
    init_db()
    ensure_dataset_imported()

    if not login_required():
        return redirect(url_for("login"))

    ctx = base_context()
    username = current_user()

    if request.method == "POST":
        scope = (request.form.get("scope") or "user").strip().lower()

        # RESET MY SETTINGS
        if scope == "reset_user":
            set_user_settings(username, copy.deepcopy(DEFAULT_USER_SETTINGS))
            flash("Your settings were reset to defaults.", "info")
            return redirect(url_for("settings_page"))

        # IMPORT MY SETTINGS
        if scope == "import_user":
            raw = (request.form.get("settings_json") or "").strip()
            if not raw:
                flash("Paste settings JSON to import.", "danger")
                return redirect(url_for("settings_page"))

            try:
                obj = json.loads(raw)
                if not isinstance(obj, dict):
                    raise ValueError("Settings JSON must be an object/dictionary.")
            except Exception:
                flash("Invalid JSON. Please paste valid settings JSON.", "danger")
                return redirect(url_for("settings_page"))

            # Merge imported -> 
            merged = copy.deepcopy(DEFAULT_USER_SETTINGS)
            merged.update(obj)

            # Validate/clamp imported values
            merged["theme"] = str(merged.get("theme", "auto")).strip()
            if merged["theme"] not in ("auto", "light", "dark"):
                merged["theme"] = "auto"

            merged["density"] = str(merged.get("density", "auto")).strip()
            if merged["density"] not in ("auto", "comfortable", "compact"):
                merged["density"] = "auto"

            try:
                merged["font_scale"] = float(merged.get("font_scale", 1.0))
            except:
                merged["font_scale"] = 1.0
            if merged["font_scale"] < 0.8:
                merged["font_scale"] = 0.8
            if merged["font_scale"] > 1.4:
                merged["font_scale"] = 1.4

            try:
                merged["table_per_page"] = int(merged.get("table_per_page", 0))
            except:
                merged["table_per_page"] = 0
            if merged["table_per_page"] < 0:
                merged["table_per_page"] = 0
            if merged["table_per_page"] > 200:
                merged["table_per_page"] = 200

            # New preference keys
            merged["font_family"] = str(merged.get("font_family", "auto")).strip()
            if merged["font_family"] not in ("auto", "system", "serif", "mono", "arial", "georgia", "courier", "custom"):
                merged["font_family"] = "auto"

            merged["custom_font_stack"] = str(merged.get("custom_font_stack", "")).strip()

            merged["start_page"] = str(merged.get("start_page", "dashboard")).strip()
            if merged["start_page"] not in ("dashboard", "data", "charts"):
                merged["start_page"] = "dashboard"

            merged["sidebar_default"] = str(merged.get("sidebar_default", "remember")).strip()
            if merged["sidebar_default"] not in ("remember", "open", "collapsed"):
                merged["sidebar_default"] = "remember"

            merged["filters_panel"] = str(merged.get("filters_panel", "remember")).strip()
            if merged["filters_panel"] not in ("remember", "open", "collapsed"):
                merged["filters_panel"] = "remember"

            merged["reduce_motion"] = str(merged.get("reduce_motion", "auto")).strip()
            if merged["reduce_motion"] not in ("auto", "off", "on"):
                merged["reduce_motion"] = "auto"

            merged["high_contrast"] = str(merged.get("high_contrast", "off")).strip()
            if merged["high_contrast"] not in ("off", "on"):
                merged["high_contrast"] = "off"

            set_user_settings(username, merged)
            flash("Settings imported successfully.", "success")
            return redirect(url_for("settings_page"))

        # SAVE MY PREFERENCES
        if scope == "user":
            theme = (request.form.get("theme") or "auto").strip()
            density = (request.form.get("density") or "auto").strip()

            # New fields
            font_family = (request.form.get("font_family") or "auto").strip()
            custom_font_stack = (request.form.get("custom_font_stack") or "").strip()
            start_page = (request.form.get("start_page") or "dashboard").strip()
            sidebar_default = (request.form.get("sidebar_default") or "remember").strip()
            filters_panel = (request.form.get("filters_panel") or "remember").strip()
            reduce_motion = (request.form.get("reduce_motion") or "auto").strip()
            high_contrast = (request.form.get("high_contrast") or "off").strip()

            # Numeric
            try:
                font_scale = float(request.form.get("font_scale") or 1.0)
            except:
                font_scale = 1.0

            try:
                table_per_page = int(request.form.get("table_per_page") or 0)
            except:
                table_per_page = 0

            # Validate/clamp
            if theme not in ("auto", "light", "dark"):
                theme = "auto"

            if density not in ("auto", "comfortable", "compact"):
                density = "auto"

            if font_scale < 0.8:
                font_scale = 0.8
            if font_scale > 1.4:
                font_scale = 1.4

            if table_per_page < 0:
                table_per_page = 0
            if table_per_page > 200:
                table_per_page = 200

            if font_family not in ("auto", "system", "serif", "mono", "arial", "georgia", "courier", "custom"):
                font_family = "auto"

            if start_page not in ("dashboard", "data", "charts"):
                start_page = "dashboard"

            if sidebar_default not in ("remember", "open", "collapsed"):
                sidebar_default = "remember"

            if filters_panel not in ("remember", "open", "collapsed"):
                filters_panel = "remember"

            if reduce_motion not in ("auto", "off", "on"):
                reduce_motion = "auto"

            if high_contrast not in ("off", "on"):
                high_contrast = "off"

            # Save
            s = get_user_settings(username)
            s.update({
                "theme": theme,
                "density": density,
                "font_scale": font_scale,
                "table_per_page": table_per_page,

                "font_family": font_family,
                "custom_font_stack": custom_font_stack,
                "start_page": start_page,
                "sidebar_default": sidebar_default,
                "filters_panel": filters_panel,
                "reduce_motion": reduce_motion,
                "high_contrast": high_contrast,
            })
            set_user_settings(username, s)

            flash("Your preferences were saved successfully.", "success")
            return redirect(url_for("settings_page"))

        flash("Unknown settings action.", "danger")
        return redirect(url_for("settings_page"))

    return render_template("settings.html", **ctx, title="Settings")

# UPLOAD
@app.route("/upload", methods=["POST"])
def upload_dataset():
    if not admin_required():
        flash("Admin only: upload datasets.", "danger")
        return redirect(url_for("index"))

    ensure_dataset_imported()

    f = request.files.get("file")
    if not f or f.filename.strip() == "":
        flash("No file selected.", "danger")
        return redirect(url_for("index"))

    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        flash("Unsupported type. Upload CSV/XLS/XLSX.", "danger")
        return redirect(url_for("index"))

    name = secure_filename(f.filename)
    new_name = f"{int(time.time())}_{name}"
    path = os.path.join(UPLOAD_DIR, new_name)
    f.save(path)

    session["DATA_PATH_ACTIVE"] = path
    import_to_sqlite(path)

    flash("Dataset uploaded + imported into SQLite.", "success")
    return redirect(url_for("index"))


@app.route("/dataset/reset")
def reset_dataset():
    if not admin_required():
        flash("Admin only: reset dataset.", "danger")
        return redirect(url_for("index"))

    session.pop("DATA_PATH_ACTIVE", None)
    import_to_sqlite(DATA_PATH_DEFAULT)
    flash("Dataset reset to default and re-imported.", "info")
    return redirect(url_for("index"))


# PRESETS
@app.route("/presets/save", methods=["POST"])
def preset_save():
    if not admin_required():
        flash("Admin only: save presets.", "danger")
        return redirect(url_for("index"))

    name = request.form.get("name", "").strip()
    url = request.form.get("url", request.referrer or url_for("index", _external=True))
    if not name:
        flash("Preset name required.", "danger")
        return redirect(url_for("index"))

    pid = str(uuid.uuid4())
    conn = db()
    try:
        conn.execute(
            "INSERT INTO presets(id,name,url,created_at,owner) VALUES(?,?,?,?,?)",
            (pid, name, url, int(time.time()), current_user() or ADMIN_USER)
        )
        conn.commit()
    finally:
        conn.close()

    flash("Preset saved.", "success")
    return redirect(url_for("index"))


@app.route("/presets/apply/<preset_id>")
def preset_apply(preset_id):
    conn = db()
    try:
        p = conn.execute("SELECT * FROM presets WHERE id=?", (preset_id,)).fetchone()
    finally:
        conn.close()

    if not p:
        flash("Preset not found.", "danger")
        return redirect(url_for("index"))
    return redirect(p["url"])


@app.route("/presets/delete/<preset_id>")
def preset_delete(preset_id):
    if not admin_required():
        flash("Admin only: delete presets.", "danger")
        return redirect(url_for("index"))

    conn = db()
    try:
        conn.execute("DELETE FROM presets WHERE id=?", (preset_id,))
        conn.commit()
    finally:
        conn.close()

    flash("Preset deleted.", "info")
    return redirect(url_for("index"))


# EXPORT CSV
@app.route("/export/data.csv")
def export_csv():
    ensure_dataset_imported()
    x_col, y_col = pick_columns()
    date_col = detect_date_column()

    where_clause, params = build_where(x_col, y_col, date_col)

    sort_col = request.args.get("sort", "").strip()
    order = request.args.get("order", "asc").strip().lower()
    cols = get_columns()

    order_clause = ""
    if sort_col and sort_col in cols:
        order_clause = f" ORDER BY {qident(sort_col)} {'DESC' if order == 'desc' else 'ASC'}"

    q = f"SELECT * FROM {DATA_TABLE}{where_clause}{order_clause}"
    conn = db()
    try:
        df = pd.read_sql_query(q, conn, params=params)
    finally:
        conn.close()

    csv_data = df.to_csv(index=False)
    filename = f"dvms_export_{int(time.time())}.csv"
    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


# EXPORT PDF 
@app.route("/export/report.pdf")
def export_report_pdf():
    ensure_dataset_imported()
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfgen import canvas
    except Exception:
        return Response("reportlab missing. Add to requirements.txt.", status=500)

    ctx = base_context()
    pdf_path = os.path.join(STATIC_DIR, f"DVMS_Report_{int(time.time())}.pdf")
    c = canvas.Canvas(pdf_path, pagesize=A4)

    c.setFont("Helvetica-Bold", 16)
    c.drawString(40, 800, "DVMS Analytics Report")

    c.setFont("Helvetica", 10)
    c.drawString(40, 780, f"Dataset: {active_data_path()}")
    c.drawString(40, 765, f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}")

    f = ctx["filters"]
    c.drawString(40, 740, f"Category: {f.get('category') or 'All'}")
    c.drawString(40, 725, f"Date: {f.get('date') or 'All'}")
    c.drawString(40, 710, f"Min: {f.get('min') or '-'}   Max: {f.get('max') or '-'}")

    c.drawString(40, 680, "Open dashboard for charts and drill-down.")
    c.setFont("Helvetica", 9)
    c.drawString(40, 30, "© 2026 DVMS")

    c.save()
    return send_file(pdf_path, as_attachment=True, download_name=os.path.basename(pdf_path))


# DRILL
@app.route("/drill")
def drill():
    category = request.args.get("category", "").strip()
    target = request.args.get("to", "data").strip().lower()

    args = request.args.to_dict(flat=True)
    args.pop("to", None)
    if category:
        args["category"] = category

    if target == "dashboard":
        return redirect(url_for("index", **args))
    return redirect(url_for("data", **args))


# CHART API 
@app.route("/api/chart/<chart_type>")
def api_chart(chart_type):
    ensure_dataset_imported()

    max_points = int(get_app_settings().get("chart_max_points", 5000))

    x_col, y_col = pick_columns()
    date_col = detect_date_column()
    where_clause, params = build_where(x_col, y_col, date_col)

    conn = db()
    try:
        df = pd.read_sql_query(
            f"SELECT * FROM {DATA_TABLE}{where_clause} LIMIT {max_points}",
            conn, params=params
        )
    finally:
        conn.close()

    if df.empty:
        return jsonify({"error": "No data"}), 404

    cols = df.columns.tolist()

    if (not x_col) or (x_col not in cols):
        non_num = df.select_dtypes(exclude="number").columns.tolist()
        x_col = non_num[0] if non_num else (cols[0] if cols else None)

    if (not y_col) or (y_col not in cols) or (not pd.api.types.is_numeric_dtype(df[y_col])):
        num_cols = df.select_dtypes(include="number").columns.tolist()
        y_col = num_cols[0] if num_cols else None

    if not x_col:
        return jsonify({"error": "No usable columns found for charting"}), 400

    try:
        if chart_type == "bar":
            if y_col:
                g = df.groupby(x_col, dropna=False)[y_col].mean().sort_values(ascending=False).head(10).reset_index()
                fig = px.bar(g, x=x_col, y=y_col, title="Bar Chart (Mean)")
            else:
                g = df[x_col].astype(str).value_counts().head(10).reset_index()
                g.columns = [x_col, "count"]
                fig = px.bar(g, x=x_col, y="count", title="Bar Chart (Count)")

        elif chart_type == "line":
            if y_col:
                g = df.groupby(x_col, dropna=False)[y_col].mean().reset_index()
                fig = px.line(g, x=x_col, y=y_col, title="Line Chart (Mean)")
            else:
                g = df[x_col].astype(str).value_counts().reset_index()
                g.columns = [x_col, "count"]
                fig = px.line(g, x=x_col, y="count", title="Line Chart (Count)")

        elif chart_type == "pie":
            g = df[x_col].astype(str).value_counts().head(10).reset_index()
            g.columns = [x_col, "count"]
            fig = px.pie(g, names=x_col, values="count", title="Pie Chart")

        elif chart_type == "scatter":
            nums = df.select_dtypes(include="number")
            if nums.shape[1] < 2:
                return jsonify({"error": "Not enough numeric cols for scatter"}), 400
            fig = px.scatter(df, x=nums.columns[0], y=nums.columns[1], title="Scatter Plot")

        elif chart_type == "heatmap":
            nums = df.select_dtypes(include="number").dropna(axis=1, how="all")
            if nums.shape[1] < 2:
                return jsonify({"error": "Not enough numeric cols"}), 400

            nunique = nums.nunique(dropna=True)
            nums = nums.loc[:, nunique > 1]
            if nums.shape[1] < 2:
                return jsonify({"error": "Numeric columns are constant; heatmap not possible"}), 400

            corr = nums.corr()
            fig = px.imshow(corr, text_auto=False, title="Correlation Heatmap")

        else:
            return jsonify({"error": "Unknown chart type"}), 400

        return Response(fig.to_json(), mimetype="application/json")

    except Exception as e:
        logger.exception("Chart API failed")
        return jsonify({"error": f"Chart generation failed: {str(e)}"}), 500


# DASHBOARD 
@app.route("/")
def index():
    init_db()
    ensure_dataset_imported()
    if not login_required():
        return redirect(url_for("login"))

    x_col, y_col = pick_columns()
    date_col = detect_date_column()
    ctx = base_context()

    where_clause, params = build_where(x_col, y_col, date_col)
    ctx["kpis"] = compute_kpis(where_clause, params, x_col, y_col)

    df_plot = sample_df(limit=8000, where_clause=where_clause, params=params)
    if df_plot.empty or not x_col or not y_col:
        ctx["error"] = "No data matches the selected filters."
        return render_template("index.html", **ctx, title="Analytics Dashboard")

    grouped = df_plot.groupby(x_col)[y_col].mean().sort_values(ascending=False).head(5)

    bar_file = "dashboard_bar.png"
    plt.figure(figsize=(4, 3))
    plt.bar(grouped.index.astype(str), grouped.values, color="#3f6ad8")
    plt.xticks(rotation=ROTATION, ha="right", fontsize=FONT_SIZE)
    plt.tight_layout()
    plt.savefig(os.path.join(STATIC_DIR, bar_file), dpi=130, bbox_inches="tight")
    plt.close()

    ctx["bar_url"] = url_for("static", filename=bar_file) + f"?v={int(time.time())}"
    return render_template("index.html", **ctx, title="Analytics Dashboard")


# DATA TABLE 
@app.route("/data")
def data():
    init_db()
    ensure_dataset_imported()
    if not login_required():
        return redirect(url_for("login"))

    ctx = base_context()

    x_col, y_col = pick_columns()
    date_col = detect_date_column()
    where_clause, params = build_where(x_col, y_col, date_col)

    sort_col = request.args.get("sort", "").strip()
    order = request.args.get("order", "asc").strip().lower()
    cols = get_columns()

    order_clause = ""
    if sort_col and sort_col in cols:
        order_clause = f" ORDER BY {qident(sort_col)} {'DESC' if order == 'desc' else 'ASC'}"

    # per-user table_per_page override
    app_s = get_app_settings()
    user_s = get_user_settings(current_user() or "")
    per_page = int(user_s.get("table_per_page") or 0) or int(app_s.get("table_per_page") or 15)
    per_page = max(5, min(200, per_page))

    page = int(request.args.get("page", 1))
    page = max(1, page)
    offset = (page - 1) * per_page

    conn = db()
    try:
        total = int(pd.read_sql_query(
            f"SELECT COUNT(*) AS c FROM {DATA_TABLE}{where_clause}",
            conn, params=params
        ).loc[0, "c"])

        df = pd.read_sql_query(
            f"SELECT * FROM {DATA_TABLE}{where_clause}{order_clause} LIMIT ? OFFSET ?",
            conn, params=params + [per_page, offset]
        )
    finally:
        conn.close()

    ctx.update(
        columns=cols,
        rows=df.values.tolist(),
        page=page,
        total=total,
        per_page=per_page
    )
    return render_template("data.html", **ctx, title="Data")


# PROFILE PAGE
@app.route("/profile")
def profile():
    init_db()
    ensure_dataset_imported()
    if not login_required():
        return redirect(url_for("login"))

    ctx = base_context()
    df = sample_df(8000)

    summary = {
        "rows": int(df.shape[0]),
        "cols": int(df.shape[1]),
        "duplicates": int(df.duplicated().sum())
    }

    missing = (df.isna().sum()).to_dict()
    dtypes = {c: str(df[c].dtype) for c in df.columns}

    # Outliers (simple IQR method)
    outliers = {}
    for c in df.select_dtypes(include="number").columns:
        s = df[c].dropna()
        if len(s) < 10:
            continue
        q1 = s.quantile(0.25)
        q3 = s.quantile(0.75)
        iqr = q3 - q1
        low = q1 - 1.5 * iqr
        high = q3 + 1.5 * iqr
        outliers[c] = int(((s < low) | (s > high)).sum())

    col = request.args.get("col")
    if not col:
        num_cols = df.select_dtypes(include="number").columns.tolist()
        col = num_cols[0] if num_cols else None

    hist_json = None
    if col and col in df.columns and pd.api.types.is_numeric_dtype(df[col]):
        fig = px.histogram(df, x=col, nbins=30, title=f"Distribution: {col}")
        hist_json = fig.to_json()

    ctx.update(
        summary=summary,
        missing=missing,
        dtypes=dtypes,
        outliers=outliers,
        hist_json=hist_json,
        profile_col=col,
        all_columns=df.columns.tolist()
    )
    return render_template("profile.html", **ctx, title="Data Profile")


# CHART PAGES 
@app.route("/bar")
def bar_chart():
    if not login_required():
        return redirect(url_for("login"))
    return render_template("chart.html", **base_context(), chart_type="bar", title="Bar Chart")


@app.route("/line")
def line_chart():
    if not login_required():
        return redirect(url_for("login"))
    return render_template("chart.html", **base_context(), chart_type="line", title="Line Chart")


@app.route("/scatter")
def scatter_chart():
    if not login_required():
        return redirect(url_for("login"))
    return render_template("chart.html", **base_context(), chart_type="scatter", title="Scatter Plot")


@app.route("/heatmap")
def heatmap_chart():
    if not login_required():
        return redirect(url_for("login"))
    return render_template("chart.html", **base_context(), chart_type="heatmap", title="Heatmap")


@app.route("/pie")
def pie_chart():
    if not login_required():
        return redirect(url_for("login"))
    return render_template("chart.html", **base_context(), chart_type="pie", title="Pie Chart")


if __name__ == "__main__":
    init_db()
    ensure_dataset_imported()
    app.run(debug=True)