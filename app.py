import os
import time
import json
import uuid
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
    flash, session, Response, send_file, has_request_context
)
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash

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


# DIR / DB 
def init_dirs():
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    os.makedirs(STORAGE_DIR, exist_ok=True)
    os.makedirs(STATIC_DIR, exist_ok=True)


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def table_exists(name: str) -> bool:
    conn = db()
    try:
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (name,)
        )
        return cur.fetchone() is not None
    finally:
        conn.close()


def init_db():
    init_dirs()
    conn = db()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS presets(
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                url TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                owner TEXT NOT NULL
            )
        """)
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {META_TABLE}(
                k TEXT PRIMARY KEY,
                v TEXT NOT NULL
            )
        """)
        conn.commit()

        # Ensure admin exists
        cur = conn.execute("SELECT username FROM users WHERE username=?", (ADMIN_USER,))
        if cur.fetchone() is None:
            conn.execute(
                "INSERT INTO users(username, password_hash, role) VALUES(?,?,?)",
                (ADMIN_USER, generate_password_hash(ADMIN_PASSWORD), "admin")
            )
            conn.commit()
            logger.info("Admin account created (change ADMIN_PASSWORD env for security).")
    finally:
        conn.close()


# META 
def meta_get(key: str, default=None):
    conn = db()
    try:
        row = conn.execute(f"SELECT v FROM {META_TABLE} WHERE k=?", (key,)).fetchone()
        return row["v"] if row else default
    finally:
        conn.close()


def meta_set(key: str, value: str):
    conn = db()
    try:
        conn.execute(
            f"INSERT INTO {META_TABLE}(k,v) VALUES(?,?) "
            f"ON CONFLICT(k) DO UPDATE SET v=excluded.v",
            (key, str(value))
        )
        conn.commit()
    finally:
        conn.close()


# AUTH HELPERS
def current_user():
    return session.get("user") if has_request_context() else None


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

    conn = db()
    try:
        df.to_sql(DATA_TABLE, conn, if_exists="replace", index=False)
        conn.commit()
    finally:
        conn.close()

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

    # search across all columns
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

        # estimate numeric cols from sample
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


# FIXED HEATMAP PNG 
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


@app.route("/logout")
def logout():
    session.clear()
    flash("Logged out.", "info")
    return redirect(url_for("login"))


# UPLOAD (ADMIN)
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


# EXPORT PDF REPORT
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


# INTERACTIVE CHART API
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
        return Response(json.dumps({"error": "No data"}), mimetype="application/json")

    if chart_type == "bar":
        g = df.groupby(x_col)[y_col].mean().sort_values(ascending=False).head(10).reset_index()
        fig = px.bar(g, x=x_col, y=y_col, title="Bar Chart")

    elif chart_type == "line":
        g = df.groupby(x_col)[y_col].mean().sort_values(ascending=False).head(10).reset_index()
        fig = px.line(g, x=x_col, y=y_col, title="Line Chart")

    elif chart_type == "pie":
        g = df[x_col].astype(str).value_counts().head(10).reset_index()
        g.columns = [x_col, "count"]
        fig = px.pie(g, names=x_col, values="count", title="Pie Chart")

    elif chart_type == "scatter":
        nums = df.select_dtypes(include="number")
        if nums.shape[1] < 2:
            return Response(json.dumps({"error": "Not enough numeric cols"}), mimetype="application/json")
        fig = px.scatter(df, x=nums.columns[0], y=nums.columns[1], title="Scatter Plot")

    elif chart_type == "heatmap":
        nums = df.select_dtypes(include="number").dropna(axis=1, how="all")
        if nums.shape[1] < 2:
            return Response(json.dumps({"error": "Not enough numeric cols"}), mimetype="application/json")
        nunique = nums.nunique(dropna=True)
        nums = nums.loc[:, nunique > 1]
        if nums.shape[1] < 2:
            return Response(json.dumps({"error": "Numeric columns are constant; heatmap not possible"}), mimetype="application/json")
        corr = nums.corr()
        if corr.isna().all().all():
            return Response(json.dumps({"error": "Correlation could not be computed"}), mimetype="application/json")
        fig = px.imshow(corr, text_auto=False, title="Correlation Heatmap")

    else:
        return Response(json.dumps({"error": "Unknown chart type"}), mimetype="application/json")

    return Response(fig.to_json(), mimetype="application/json")


# DASHBOARD (PNG charts) 
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

    # Get data subset for plotting
    df_plot = sample_df(limit=8000, where_clause=where_clause, params=params)
    if df_plot.empty or not x_col or not y_col:
        ctx["error"] = "No data matches the selected filters."
        return render_template("index.html", **ctx, title="Analytics Dashboard")

    # top categories for drilldown
    ctx["top_categories"] = (
        df_plot[x_col].astype(str).value_counts().head(5).index.tolist()
        if x_col in df_plot.columns else []
    )

    # Build grouped
    grouped = df_plot.groupby(x_col)[y_col].mean().sort_values(ascending=False).head(5)

    # BAR
    bar_file = "dashboard_bar.png"
    plt.figure(figsize=(4, 3))
    plt.bar(grouped.index.astype(str), grouped.values, color="#3f6ad8")
    plt.xticks(rotation=ROTATION, ha="right", fontsize=FONT_SIZE)
    plt.tight_layout()
    plt.savefig(os.path.join(STATIC_DIR, bar_file), dpi=130, bbox_inches="tight")
    plt.close()

    # LINE
    line_file = "dashboard_line.png"
    plt.figure(figsize=(4, 3))
    plt.plot(grouped.index.astype(str), grouped.values, linewidth=2, color="#3f6ad8")
    plt.xticks(rotation=ROTATION, ha="right", fontsize=FONT_SIZE)
    plt.tight_layout()
    plt.savefig(os.path.join(STATIC_DIR, line_file), dpi=130, bbox_inches="tight")
    plt.close()

    # PIE (top 5 counts)
    pie_file = "dashboard_pie.png"
    top = df_plot[x_col].astype(str).value_counts().head(5)
    plt.figure(figsize=(4, 3))
    plt.pie(top.values, labels=top.index.tolist(), autopct="%1.1f%%")
    plt.tight_layout()
    plt.savefig(os.path.join(STATIC_DIR, pie_file), dpi=130, bbox_inches="tight")
    plt.close()

    # SCATTER (first two numeric columns)
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

    # HEATMAP (FIXED)
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

    # histogram column
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


# DETAIL CHART PAGES (PNG)
@app.route("/bar")
def bar_chart():
    if not login_required():
        return redirect(url_for("login"))

    x_col, y_col = pick_columns()
    date_col = detect_date_column()
    ctx = base_context()

    where_clause, params = build_where(x_col, y_col, date_col)
    df_plot = sample_df(limit=10000, where_clause=where_clause, params=params)

    if df_plot.empty or not x_col or not y_col:
        ctx["error"] = "No data matches the selected filters."
        return render_template("chart.html", **ctx, title="Bar Chart")

    grouped = df_plot.groupby(x_col)[y_col].mean().sort_values(ascending=False).head(10)
    out_file = "bar.png"

    plt.figure(figsize=(10, 5))
    plt.bar(grouped.index.astype(str), grouped.values, color="#3f6ad8")
    plt.xlabel(x_col)
    plt.ylabel(f"{y_col} (Mean)")
    plt.xticks(rotation=ROTATION, ha="right")
    plt.tight_layout()
    plt.savefig(os.path.join(STATIC_DIR, out_file), dpi=140, bbox_inches="tight")
    plt.close()

    ctx.update(
        chart_url=url_for("static", filename=out_file) + f"?v={int(time.time())}",
        download_url=url_for("static", filename=out_file),
        download_name=out_file,
        top_categories=grouped.index.astype(str).tolist()
    )
    return render_template("chart.html", **ctx, title="Bar Chart")


@app.route("/line")
def line_chart():
    if not login_required():
        return redirect(url_for("login"))

    x_col, y_col = pick_columns()
    date_col = detect_date_column()
    ctx = base_context()

    where_clause, params = build_where(x_col, y_col, date_col)
    df_plot = sample_df(limit=10000, where_clause=where_clause, params=params)

    if df_plot.empty or not x_col or not y_col:
        ctx["error"] = "No data matches the selected filters."
        return render_template("chart.html", **ctx, title="Line Chart")

    grouped = df_plot.groupby(x_col)[y_col].mean().sort_values(ascending=False).head(10)
    out_file = "line.png"

    plt.figure(figsize=(10, 5))
    plt.plot(grouped.index.astype(str), grouped.values, linewidth=2, color="#3f6ad8")
    plt.xlabel(x_col)
    plt.ylabel(y_col)
    plt.xticks(rotation=ROTATION, ha="right")
    plt.tight_layout()
    plt.savefig(os.path.join(STATIC_DIR, out_file), dpi=140, bbox_inches="tight")
    plt.close()

    ctx.update(
        chart_url=url_for("static", filename=out_file) + f"?v={int(time.time())}",
        download_url=url_for("static", filename=out_file),
        download_name=out_file,
    )
    return render_template("chart.html", **ctx, title="Line Chart")


@app.route("/scatter")
def scatter_chart():
    if not login_required():
        return redirect(url_for("login"))

    x_col, y_col = pick_columns()
    date_col = detect_date_column()
    ctx = base_context()

    where_clause, params = build_where(x_col, y_col, date_col)
    df_plot = sample_df(limit=8000, where_clause=where_clause, params=params)

    nums = df_plot.select_dtypes(include="number")
    out_file = "scatter.png"

    plt.figure(figsize=(8, 6))
    if nums.shape[1] >= 2:
        plt.scatter(nums.iloc[:, 0], nums.iloc[:, 1], alpha=0.7, color="#00a8a8")
        plt.xlabel(nums.columns[0])
        plt.ylabel(nums.columns[1])
    else:
        plt.axis("off")
        plt.text(0.5, 0.5, "Not enough numeric columns", ha="center", va="center")
    plt.tight_layout()
    plt.savefig(os.path.join(STATIC_DIR, out_file), dpi=140, bbox_inches="tight")
    plt.close()

    ctx.update(
        chart_url=url_for("static", filename=out_file) + f"?v={int(time.time())}",
        download_url=url_for("static", filename=out_file),
        download_name=out_file,
    )
    return render_template("chart.html", **ctx, title="Scatter Plot")


@app.route("/heatmap")
def heatmap_chart():
    if not login_required():
        return redirect(url_for("login"))

    x_col, y_col = pick_columns()
    date_col = detect_date_column()
    ctx = base_context()

    where_clause, params = build_where(x_col, y_col, date_col)
    df_plot = sample_df(limit=10000, where_clause=where_clause, params=params)

    out_file = "heatmap.png"
    save_heatmap_png(df_plot, os.path.join(STATIC_DIR, out_file), title="Correlation Heatmap", small=False)

    ctx.update(
        chart_url=url_for("static", filename=out_file) + f"?v={int(time.time())}",
        download_url=url_for("static", filename=out_file),
        download_name=out_file
    )
    return render_template("chart.html", **ctx, title="Heatmap")


@app.route("/pie")
def pie_chart():
    if not login_required():
        return redirect(url_for("login"))

    x_col, y_col = pick_columns()
    date_col = detect_date_column()
    ctx = base_context()

    where_clause, params = build_where(x_col, y_col, date_col)
    df_plot = sample_df(limit=10000, where_clause=where_clause, params=params)

    if df_plot.empty or not x_col or x_col not in df_plot.columns:
        ctx["error"] = "No data matches the selected filters."
        return render_template("pie.html", **ctx, title="Pie Chart")

    top = df_plot[x_col].astype(str).value_counts().head(10)
    out_file = "pie.png"

    plt.figure(figsize=(7, 7))
    plt.pie(top.values, labels=top.index.tolist(), autopct="%1.1f%%")
    plt.tight_layout()
    plt.savefig(os.path.join(STATIC_DIR, out_file), dpi=140, bbox_inches="tight")
    plt.close()

    ctx.update(
        pie_url=url_for("static", filename=out_file) + f"?v={int(time.time())}",
        download_url=url_for("static", filename=out_file),
        download_name=out_file
    )
    return render_template("pie.html", **ctx, title="Pie Chart")


if __name__ == "__main__":
    init_db()
    ensure_dataset_imported()
    app.run(debug=True)