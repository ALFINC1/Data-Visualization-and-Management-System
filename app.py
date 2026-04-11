import os
import time

import json
import uuid
import copy
import logging
import sqlite3
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


# APP
app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "dvms-dev-secret")

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


# DIR
def init_dirs():
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    os.makedirs(STORAGE_DIR, exist_ok=True)
    os.makedirs(STATIC_DIR, exist_ok=True)


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

        # user_profiles table
        connection.execute("""
            CREATE TABLE IF NOT EXISTS user_profiles(
                username TEXT PRIMARY KEY,
                name TEXT,
                email TEXT,
                phone TEXT,
                address TEXT,
                bio TEXT,
                avatar_path TEXT,
                updated_at INTEGER
            )
        """)

        connection.execute("""
            CREATE TABLE IF NOT EXISTS user_settings(
                username TEXT PRIMARY KEY,
                settings_json TEXT NOT NULL,
                updated_at INTEGER NOT NULL
            )
        """)
        connection.commit()

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


# AUTH HELPERS
def current_user():
    return session.get("user") if has_request_context() else None

def _safe_json_load(s, default):
    try:
        return json.loads(s) if s else copy.deepcopy(default)
    except Exception:
        return copy.deepcopy(default)


def get_app_settings():
    # stored as JSON in META_TABLE under key "app_settings"
    raw = meta_get("app_settings", "")
    data = _safe_json_load(raw, DEFAULT_APP_SETTINGS)
    # merge defaults -> stored (stored overrides defaults)
    merged = copy.deepcopy(DEFAULT_APP_SETTINGS)
    merged.update(data or {})
    # nested merge for social links
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

@app.before_request
def enforce_maintenance_mode():
    try:
        s = get_app_settings()
        if s.get("maintenance_mode") and request.endpoint not in ("login", "logout", "static"):
            # allow admins through
            if not (login_required() and current_role() == "admin"):
                flash("System is in maintenance mode. Please try again later.", "warning")
                return redirect(url_for("login"))
    except Exception:
        # fail open (don't block app if settings broken)
        pass

def current_role():
    return session.get("role", "viewer") if has_request_context() else "viewer"


def login_required():
    return current_user() is not None


def admin_required():
    return (current_user() is not None) and (current_role() == "admin")


# DATASET HELPERS
def active_data_path():
    if has_request_context():
        return session.get("DATA_PATH_ACTIVE", meta_get("active_dataset_path", DATA_PATH_DEFAULT))
    return meta_get("active_dataset_path", DATA_PATH_DEFAULT)


def import_to_sqlite(path: str):
    ds = DataService(path)
    df = ds.load_df().copy()
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
        where.append(f"CAST({x_col} AS TEXT) = ?")
        params.append(category)

    if date_val and date_col:
        where.append(f"CAST({date_col} AS TEXT) = ?")
        params.append(date_val)

    # numeric range
    if scatter_cols and len(scatter_cols) >= 2:
        a, b = scatter_cols[0], scatter_cols[1]
        if mn is not None:
            where.append(f"({a} >= ? AND {b} >= ?)")
            params.extend([mn, mn])
        if mx is not None:
            where.append(f"({a} <= ? AND {b} <= ?)")
            params.extend([mx, mx])
    else:
        if y_col:
            if mn is not None:
                where.append(f"{y_col} >= ?")
                params.append(mn)
            if mx is not None:
                where.append(f"{y_col} <= ?")
                params.append(mx)

    # search
    if search:
        cols = get_columns()
        parts = []
        for c in cols:
            parts.append(f"LOWER(CAST({c} AS TEXT)) LIKE ?")
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
                f"SELECT CAST({x_col} AS TEXT) AS v, COUNT(*) AS c "
                f"FROM {DATA_TABLE} WHERE {x_col} IS NOT NULL "
                f"GROUP BY v ORDER BY c DESC LIMIT 200",
                conn
            )
            categories = df["v"].tolist()

        date_values = []
        if date_col:
            df = pd.read_sql_query(
                f"SELECT CAST({date_col} AS TEXT) AS v, COUNT(*) AS c "
                f"FROM {DATA_TABLE} WHERE {date_col} IS NOT NULL "
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
                f"SELECT COUNT(DISTINCT CAST({x_col} AS TEXT)) AS d FROM {DATA_TABLE}{where_clause}",
                conn, params=params
            ).loc[0, "d"])

        y_min = y_mean = y_max = None
        if y_col:
            stats = pd.read_sql_query(
                f"SELECT MIN({y_col}) AS mn, AVG({y_col}) AS av, MAX({y_col}) AS mx FROM {DATA_TABLE}{where_clause}",
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
        plt.text(
            0.5, 0.5,
            "Not enough numeric columns\nfor correlation heatmap",
            ha="center", va="center", fontsize=10
        )
        plt.tight_layout()
        plt.savefig(out_path, dpi=140, bbox_inches="tight")
        plt.close()
        return

    corr = num_df.corr()
    if corr.isna().all().all():
        plt.figure(figsize=(4, 3) if small else (10, 8))
        plt.axis("off")
        plt.text(
            0.5, 0.5,
            "Correlation could not be computed\n(check numeric data)",
            ha="center", va="center", fontsize=10
        )
        plt.tight_layout()
        plt.savefig(out_path, dpi=140, bbox_inches="tight")
        plt.close()
        return

    plt.figure(figsize=(4.8, 3.2) if small else (10, 8))
    ax = sns.heatmap(
        corr, cmap="coolwarm", annot=False,
        square=True, linewidths=0.5, cbar=False
    )
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

    return dict(
        user=current_user(),
        role=current_role(),
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

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()

        conn = db()
        try:
            user = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        finally:
            conn.close()

        if not user or not check_password_hash(user["password_hash"], password):
            flash("Invalid username or password.", "danger")
            return redirect(url_for("login"))

        session["user"] = user["username"]
        session["role"] = user["role"]
        flash(f"Welcome {user['username']}!", "success")
        return redirect(url_for("index"))

    return render_template("login.html", **base_context(), title="Login")

# ACCOUNT PAGE
@app.route("/account")
def account():
    init_db()
    ensure_dataset_imported()

    if not login_required():
        return redirect(url_for("login"))

    ctx = base_context()

    username = current_user() or "Guest"
    role = current_role() or "viewer"

    # Defaults
    name = username
    email = f"{username}@gmail.com"
    phone = ""
    address = ""
    bio = "Hi, Welcome to the DVMS."
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

    ctx.update(
        profile_info={
            "name": name,
            "role": role,
            "email": email,
            "phone": phone,
            "address": address,
            "status": "Active",
            "bio": bio,
            "avatar_url": avatar_url
        }
    )

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

        conn.execute(
            "UPDATE users SET password_hash=? WHERE username=?",
            (generate_password_hash(new_pw), current_user())
        )
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

    avatar_dir = os.path.join(STATIC_DIR, "avatars")
    os.makedirs(avatar_dir, exist_ok=True)

    filename = f"{int(time.time())}_{secure_filename(f.filename)}"
    save_path = os.path.join(avatar_dir, filename)
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
        conn.execute("DELETE FROM users WHERE username=?", (username,))
        conn.commit()
    finally:
        conn.close()

    session.clear()
    flash("Account deleted.", "info")
    return redirect(url_for("login"))

# REGISTER ROUTE
@app.route("/register", methods=["POST"])
def register():
    init_db()
    ensure_dataset_imported()

    username = (request.form.get("username") or "").strip()
    password = (request.form.get("password") or "").strip()
    confirm = (request.form.get("confirm_password") or "").strip()

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

    role = "viewer"

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
        connection.commit()
    finally:
        connection.close()

    flash("Account created successfully! You can login now.", "success")
    return redirect(url_for("login", username=username))


@app.route("/logout")
def logout():
    session.clear()
    flash("Logged out.", "info")
    return redirect(url_for("login"))


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
        order_clause = f" ORDER BY {sort_col} {'DESC' if order == 'desc' else 'ASC'}"

    q = f"SELECT * FROM {DATA_TABLE}{where_clause}{order_clause}"
    conn = db()
    try:
        df = pd.read_sql_query(q, conn, params=params)
    finally:
        conn.close()

    csv_data = df.to_csv(index=False)
    filename = f"dvms_export_{int(time.time())}.csv"
    return Response(csv_data, mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename={filename}"})


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
    c.drawString(40, 30, "© 2026 Fadil Ahmed — DNA Technology")

    c.save()
    return send_file(pdf_path, as_attachment=True, download_name=os.path.basename(pdf_path))


# DRILL DOWN
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

@app.route("/settings", methods=["GET", "POST"])
def settings_page():
    init_db()
    ensure_dataset_imported()

    if not login_required():
        return redirect(url_for("login"))

    ctx = base_context()
    username = current_user()
    is_admin = (current_role() == "admin")

    # which tab to show
    tab = (request.args.get("tab") or "user").strip().lower()
    if tab not in ("user", "system"):
        tab = "user"
    if tab == "system" and not is_admin:
        tab = "user"

    if request.method == "POST":
        scope = (request.form.get("scope") or "user").strip().lower()

        # USER SETTINGS
        if scope == "user":
            theme = (request.form.get("theme") or "auto").strip()
            density = (request.form.get("density") or "auto").strip()
            font_scale = float(request.form.get("font_scale") or 1.0)
            table_per_page = int(request.form.get("table_per_page") or 0)

            # clamp values
            if font_scale < 0.8: font_scale = 0.8
            if font_scale > 1.4: font_scale = 1.4
            if table_per_page < 0: table_per_page = 0

            new_user_settings = get_user_settings(username)
            new_user_settings.update({
                "theme": theme,
                "density": density,
                "font_scale": font_scale,
                "table_per_page": table_per_page
            })
            set_user_settings(username, new_user_settings)

            flash("Your settings saved.", "success")
            return redirect(url_for("settings_page", tab="user"))

        # SYSTEM SETTINGS
        if scope == "system":
            if not is_admin:
                flash("Admin only: system settings.", "danger")
                return redirect(url_for("settings_page", tab="user"))

            s = get_app_settings()

            s["app_name"] = (request.form.get("app_name") or s["app_name"]).strip()
            s["app_title"] = (request.form.get("app_title") or s["app_title"]).strip()
            s["about_text"] = (request.form.get("about_text") or s["about_text"]).strip()

            s["default_theme"] = (request.form.get("default_theme") or s["default_theme"]).strip()
            s["accent_color"] = (request.form.get("accent_color") or s["accent_color"]).strip()

            s["font_family"] = (request.form.get("font_family") or s["font_family"]).strip()
            s["custom_font_stack"] = (request.form.get("custom_font_stack") or s["custom_font_stack"]).strip()
            s["base_font_size_px"] = int(request.form.get("base_font_size_px") or s["base_font_size_px"])

            s["density"] = (request.form.get("density_system") or s["density"]).strip()
            s["plotly_template"] = (request.form.get("plotly_template") or s["plotly_template"]).strip()

            s["allow_registration"] = True if request.form.get("allow_registration") == "1" else False
            s["maintenance_mode"] = True if request.form.get("maintenance_mode") == "1" else False

            s["table_per_page"] = int(request.form.get("table_per_page_system") or s["table_per_page"])
            s["chart_max_points"] = int(request.form.get("chart_max_points") or s["chart_max_points"])

            s["timezone"] = (request.form.get("timezone") or s["timezone"]).strip()
            s["language"] = (request.form.get("language") or s["language"]).strip()

            # socials
            s["social_links"]["linkedin"] = (request.form.get("linkedin") or s["social_links"]["linkedin"]).strip()
            s["social_links"]["github"] = (request.form.get("github") or s["social_links"]["github"]).strip()
            s["social_links"]["telegram"] = (request.form.get("telegram") or s["social_links"]["telegram"]).strip()

            set_app_settings(s)

            flash("System settings saved.", "success")
            return redirect(url_for("settings_page", tab="system"))

        # EXPORT/IMPORT/RESET
        if scope == "export_user":
            data = get_user_settings(username)
            return Response(json.dumps(data, indent=2), mimetype="application/json",
                            headers={"Content-Disposition": "attachment; filename=user_settings.json"})

        if scope == "export_system":
            if not is_admin:
                flash("Admin only: export system settings.", "danger")
                return redirect(url_for("settings_page", tab="user"))
            data = get_app_settings()
            return Response(json.dumps(data, indent=2), mimetype="application/json",
                            headers={"Content-Disposition": "attachment; filename=system_settings.json"})

        if scope == "reset_user":
            set_user_settings(username, copy.deepcopy(DEFAULT_USER_SETTINGS))
            flash("User settings reset to defaults.", "info")
            return redirect(url_for("settings_page", tab="user"))

        if scope == "reset_system":
            if not is_admin:
                flash("Admin only: reset system settings.", "danger")
                return redirect(url_for("settings_page", tab="user"))
            set_app_settings(copy.deepcopy(DEFAULT_APP_SETTINGS))
            flash("System settings reset to defaults.", "info")
            return redirect(url_for("settings_page", tab="system"))

        flash("Unknown settings action.", "danger")
        return redirect(url_for("settings_page", tab=tab))

    return render_template("settings.html", **ctx, title="Settings", tab=tab, is_admin=is_admin)


@app.route("/api/chart/<chart_type>")
def api_chart(chart_type):
    ensure_dataset_imported()

    x_col, y_col = pick_columns()
    date_col = detect_date_column()
    where_clause, params = build_where(x_col, y_col, date_col)

    conn = db()
    try:
        df = pd.read_sql_query(
            f"SELECT * FROM {DATA_TABLE}{where_clause} LIMIT 5000",
            conn, params=params
        )
    finally:
        conn.close()

    if df.empty:
        return jsonify({"error": "No data"}), 404

    cols = df.columns.tolist()

    # pick  x column
    if (not x_col) or (x_col not in cols):
        non_num = df.select_dtypes(exclude="number").columns.tolist()
        x_col = non_num[0] if non_num else (cols[0] if cols else None)

    # pick y column (numeric)
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
            if corr.isna().all().all():
                return jsonify({"error": "Correlation could not be computed"}), 400

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

    ctx["top_categories"] = (
        df_plot[x_col].astype(str).value_counts().head(5).index.tolist()
        if x_col in df_plot.columns else []
    )

    grouped = df_plot.groupby(x_col)[y_col].mean().sort_values(ascending=False).head(5)

    bar_file = "dashboard_bar.png"
    plt.figure(figsize=(4, 3))
    plt.bar(grouped.index.astype(str), grouped.values, color="#3f6ad8")
    plt.xticks(rotation=ROTATION, ha="right", fontsize=FONT_SIZE)
    plt.tight_layout()
    plt.savefig(os.path.join(STATIC_DIR, bar_file), dpi=130, bbox_inches="tight")
    plt.close()

    line_file = "dashboard_line.png"
    plt.figure(figsize=(4, 3))
    plt.plot(grouped.index.astype(str), grouped.values, linewidth=2, color="#3f6ad8")
    plt.xticks(rotation=ROTATION, ha="right", fontsize=FONT_SIZE)
    plt.tight_layout()
    plt.savefig(os.path.join(STATIC_DIR, line_file), dpi=130, bbox_inches="tight")
    plt.close()

    pie_file = "dashboard_pie.png"
    top = df_plot[x_col].astype(str).value_counts().head(5)
    plt.figure(figsize=(4, 3))
    plt.pie(top.values, labels=top.index.tolist(), autopct="%1.1f%%")
    plt.tight_layout()
    plt.savefig(os.path.join(STATIC_DIR, pie_file), dpi=130, bbox_inches="tight")
    plt.close()

    scatter_file = "dashboard_scatter.png"
    nums = df_plot.select_dtypes(include="number")
    plt.figure(figsize=(4, 3))
    if nums.shape[1] >= 2:
        plt.scatter(nums.iloc[:, 0], nums.iloc[:, 1], alpha=0.6, color="#00a8a8")
        plt.xlabel(nums.columns[0], fontsize=FONT_SIZE)
        plt.ylabel(nums.columns[1], fontsize=FONT_SIZE)
        plt.tight_layout()
    else:
        plt.axis("off")
        plt.text(0.5, 0.5, "Not enough numeric columns", ha="center", va="center")
    plt.savefig(os.path.join(STATIC_DIR, scatter_file), dpi=130, bbox_inches="tight")
    plt.close()

    heatmap_file = "dashboard_heatmap.png"
    save_heatmap_png(df_plot, os.path.join(STATIC_DIR, heatmap_file), title="Correlation Heatmap", small=True)

    ctx.update(
        bar_url=url_for("static", filename=bar_file) + f"?v={int(time.time())}",
        line_url=url_for("static", filename=line_file) + f"?v={int(time.time())}",
        pie_url=url_for("static", filename=pie_file) + f"?v={int(time.time())}",
        scatter_url=url_for("static", filename=scatter_file) + f"?v={int(time.time())}",
        heatmap_url=url_for("static", filename=heatmap_file) + f"?v={int(time.time())}",
    )

    return render_template("index.html", **ctx, title="Analytics Dashboard")


# DATA TABLE
@app.route("/data")
def data():
    init_db()
    ensure_dataset_imported()

    if not login_required():
        return redirect(url_for("login"))

    x_col, y_col = pick_columns()
    date_col = detect_date_column()
    ctx = base_context()

    where_clause, params = build_where(x_col, y_col, date_col)

    sort_col = request.args.get("sort", "").strip()
    order = request.args.get("order", "asc").strip().lower()
    cols = get_columns()

    order_clause = ""
    if sort_col and sort_col in cols:
        order_clause = f" ORDER BY {sort_col} {'DESC' if order == 'desc' else 'ASC'}"

    page = int(request.args.get("page", 1))
    per_page = 15
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