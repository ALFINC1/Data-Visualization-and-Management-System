import os
import time
import json
import uuid
import logging
import numpy as np
import pandas as pd
import seaborn as sns
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plot

from flask import (
    Flask, render_template, url_for, request,
    Response, redirect, flash, session, send_file
)
from werkzeug.utils import secure_filename
from service.service import DataService

# APP SETUP
app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "dvms-dev-secret")

logging.basicConfig(level=logging.INFO, format="%(levelname)s:  %(message)s")
logger = logging.getLogger(__name__)

DATA_PATH = os.getenv("DATA_PATH", "data.xlsx")

ROTATION = 40
FONT_SIZE = 6

UPLOAD_DIR = "uploads"
PRESETS_DIR = "storage"
PRESETS_FILE = os.path.join(PRESETS_DIR, "presets.json")

ALLOWED_EXTENSIONS = {".csv", ".xls", ".xlsx"}


# HELPERS
def ensure_dirs():
    os.makedirs("static", exist_ok=True)
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    os.makedirs(PRESETS_DIR, exist_ok=True)
    if not os.path.exists(PRESETS_FILE):
        with open(PRESETS_FILE, "w", encoding="utf-8") as f:
            json.dump([], f)


def get_data_path():
    return session.get("DATA_PATH_ACTIVE", DATA_PATH)


def get_service():
    return DataService(get_data_path())


def cache_bust(filename):
    return url_for("static", filename=filename) + f"?v={int(time.time())}"


def pick_columns(df, x_override=None, y_override=None):
    num = df.select_dtypes(include="number").columns.tolist()
    nonnum = [c for c in df.columns if c not in num]

    y = y_override if (y_override and y_override in df.columns) else (num[0] if num else None)
    x = x_override if (x_override and x_override in df.columns) else (nonnum[0] if nonnum else None)
    return x, y


def detect_date_column(df):
    for c in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[c]):
            return c
    for c in df.columns:
        if "date" in str(c).lower():
            return c
    for c in df.columns:
        if "year" in str(c).lower():
            return c
    return None


def build_filter_options(df, x_col, y_col, date_col):
    categories = []
    if x_col and x_col in df.columns:
        categories = df[x_col].dropna().astype(str).value_counts().index.tolist()

    date_values = []
    if date_col and date_col in df.columns:
        s = df[date_col].dropna()
        if pd.api.types.is_datetime64_any_dtype(s):
            date_values = (
                pd.to_datetime(s, errors="coerce").dropna()
                .dt.strftime("%Y-%m-%d").value_counts().index.tolist()
            )
        else:
            date_values = s.astype(str).value_counts().index.tolist()

    y_min, y_max = None, None
    if y_col and y_col in df.columns and pd.api.types.is_numeric_dtype(df[y_col]):
        s = df[y_col].dropna()
        if not s.empty:
            y_min = float(s.min())
            y_max = float(s.max())

    return categories, date_values, y_min, y_max


def current_filters():
    return {
        "category": request.args.get("category", "").strip(),
        "date": request.args.get("date", "").strip(),
        "min": request.args.get("min", "").strip(),
        "max": request.args.get("max", "").strip(),
        "search": request.args.get("search", "").strip(),
        "sort": request.args.get("sort", "").strip(),
        "order": request.args.get("order", "asc").strip().lower(),
    }


def _safe_float(s):
    try:
        return float(s)
    except:
        return None


def apply_filters(df, x_col, y_col, date_col, scatter_cols=None):
    category = request.args.get("category", "").strip()
    date_val = request.args.get("date", "").strip()
    min_val = request.args.get("min", "").strip()
    max_val = request.args.get("max", "").strip()

    if category and x_col and x_col in df.columns:
        df = df[df[x_col].astype(str) == category]

    if date_val and date_col and date_col in df.columns:
        series = df[date_col]
        if pd.api.types.is_datetime64_any_dtype(series):
            dt = pd.to_datetime(series, errors="coerce")
            df = df[dt.dt.strftime("%Y-%m-%d") == date_val]
        else:
            df = df[series.astype(str) == date_val]

    mn = _safe_float(min_val) if min_val else None
    mx = _safe_float(max_val) if max_val else None

    if scatter_cols and len(scatter_cols) >= 2:
        x_num, y_num = scatter_cols[0], scatter_cols[1]
        if mn is not None:
            df = df[(df[x_num] >= mn) & (df[y_num] >= mn)]
        if mx is not None:
            df = df[(df[x_num] <= mx) & (df[y_num] <= mx)]
        return df

    if y_col and y_col in df.columns and pd.api.types.is_numeric_dtype(df[y_col]):
        if mn is not None:
            df = df[df[y_col] >= mn]
        if mx is not None:
            df = df[df[y_col] <= mx]

    return df


def apply_search_sort(df):
    search = request.args.get("search", "").strip().lower()
    sort_col = request.args.get("sort", "").strip()
    order = request.args.get("order", "asc").strip().lower()

    if search:
        df = df[df.apply(lambda r: r.astype(str).str.lower().str.contains(search).any(), axis=1)]

    if sort_col and sort_col in df.columns:
        df = df.sort_values(by=sort_col, ascending=(order != "desc"))

    return df


def compute_kpis(df_filtered, x_col, y_col):
    kpis = {
        "rows": int(df_filtered.shape[0]),
        "cols": int(df_filtered.shape[1]),
        "numeric_cols": int(len(df_filtered.select_dtypes(include="number").columns)),
        "distinct_categories": int(df_filtered[x_col].nunique()) if x_col and x_col in df_filtered.columns else 0,
        "y_min": None,
        "y_mean": None,
        "y_max": None,
    }
    if y_col and y_col in df_filtered.columns and pd.api.types.is_numeric_dtype(df_filtered[y_col]):
        s = df_filtered[y_col].dropna()
        if not s.empty:
            kpis["y_min"] = float(s.min())
            kpis["y_mean"] = float(s.mean())
            kpis["y_max"] = float(s.max())
    return kpis


def load_presets():
    ensure_dirs()
    with open(PRESETS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_presets(items):
    ensure_dirs()
    with open(PRESETS_FILE, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)


def base_context(df, x_col, y_col, date_col):
    categories, date_values, y_min, y_max = build_filter_options(df, x_col, y_col, date_col)
    return {
        "categories": categories,
        "date_values": date_values,
        "y_min": y_min,
        "y_max": y_max,
        "filters": current_filters(),
        "share_url": request.url,
        "export_url": url_for("export_csv", **request.args.to_dict(flat=True)),
        "active_data_path": get_data_path(),
        "presets": load_presets(),
    }


def _style_xticks():
    plot.xticks(rotation=ROTATION, ha="right", fontsize=FONT_SIZE)


# A) UPLOAD DATASET 
@app.route("/upload", methods=["POST"])
def upload_dataset():
    ensure_dirs()
    f = request.files.get("file")
    if not f or f.filename.strip() == "":
        flash("No file selected.", "danger")
        return redirect(request.referrer or url_for("index"))

    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        flash("Unsupported file type. Upload CSV, XLS, or XLSX.", "danger")
        return redirect(request.referrer or url_for("index"))

    name = secure_filename(f.filename)
    new_name = f"{int(time.time())}_{name}"
    path = os.path.join(UPLOAD_DIR, new_name)
    f.save(path)

    session["DATA_PATH_ACTIVE"] = path
    flash(f"Dataset uploaded and activated: {new_name}", "success")
    return redirect(url_for("index"))


@app.route("/dataset/reset")
def reset_dataset():
    session.pop("DATA_PATH_ACTIVE", None)
    flash("Dataset reset to default.", "info")
    return redirect(url_for("index"))


# B) SAVED VIEWS / PRESETS 
@app.route("/presets/save", methods=["POST"])
def save_preset():
    ensure_dirs()
    name = request.form.get("name", "").strip()
    if not name:
        flash("Preset name is required.", "danger")
        return redirect(request.referrer or url_for("index"))

    preset = {
        "id": str(uuid.uuid4()),
        "name": name,
        "url": request.form.get("url", request.referrer or url_for("index", _external=True)),
        "created_at": int(time.time())
    }

    items = load_presets()
    items.insert(0, preset)
    save_presets(items)

    flash("Preset saved.", "success")
    return redirect(request.referrer or url_for("index"))


@app.route("/presets/apply/<preset_id>")
def apply_preset(preset_id):
    items = load_presets()
    p = next((x for x in items if x["id"] == preset_id), None)
    if not p:
        flash("Preset not found.", "danger")
        return redirect(url_for("index"))
    return redirect(p["url"])


@app.route("/presets/delete/<preset_id>")
def delete_preset(preset_id):
    items = [x for x in load_presets() if x["id"] != preset_id]
    save_presets(items)
    flash("Preset deleted.", "info")
    return redirect(request.referrer or url_for("index"))


# C) EXPORT CSV 
@app.route("/export/data.csv")
def export_csv():
    df = get_service().load_df()
    x_col, y_col = pick_columns(df, request.args.get("x"), request.args.get("y"))
    date_col = detect_date_column(df)

    df_f = apply_filters(df, x_col, y_col, date_col)
    df_f = apply_search_sort(df_f)

    csv_data = df_f.to_csv(index=False)
    filename = f"dvms_export_{int(time.time())}.csv"

    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


# C) EXPORT PDF REPORT 
@app.route("/export/report.pdf")
def export_report_pdf():
    ensure_dirs()
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfgen import canvas
        from reportlab.lib.utils import ImageReader
    except Exception:
        return Response("reportlab not installed. Add 'reportlab' to requirements.txt and install.", status=500)

    df = get_service().load_df()
    x_col, y_col = pick_columns(df)
    date_col = detect_date_column(df)
    ctx = base_context(df, x_col, y_col, date_col)
    df_filtered = apply_filters(df, x_col, y_col, date_col)

    pdf_path = os.path.join("static", f"DVMS_Report_{int(time.time())}.pdf")
    c = canvas.Canvas(pdf_path, pagesize=A4)
    width, height = A4

    y = height - 40
    c.setFont("Helvetica-Bold", 16)
    c.drawString(40, y, "DVMS Analytics Report")
    y -= 22

    c.setFont("Helvetica", 10)
    c.drawString(40, y, f"Dataset: {get_data_path()}")
    y -= 14
    c.drawString(40, y, f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    y -= 18

    c.setFont("Helvetica-Bold", 11)
    c.drawString(40, y, "Filters:")
    y -= 14
    c.setFont("Helvetica", 10)
    f = ctx["filters"]
    c.drawString(60, y, f"Category: {f.get('category') or 'All'}"); y -= 12
    c.drawString(60, y, f"Date: {f.get('date') or 'All'}"); y -= 12
    c.drawString(60, y, f"Min: {f.get('min') or '-'}  Max: {f.get('max') or '-'}"); y -= 18

    if not df_filtered.empty and x_col and y_col:
        kpis = compute_kpis(df_filtered, x_col, y_col)
        c.setFont("Helvetica-Bold", 11)
        c.drawString(40, y, "KPIs (filtered):")
        y -= 14
        c.setFont("Helvetica", 10)
        c.drawString(60, y, f"Rows: {kpis['rows']}   Distinct: {kpis['distinct_categories']}"); y -= 12
        c.drawString(60, y, f"Min: {kpis['y_min']}  Mean: {kpis['y_mean']}  Max: {kpis['y_max']}"); y -= 18

    c.setFont("Helvetica", 9)
    c.drawString(40, 30, "© 2026 Fadil Ahmed — DNA Technology")
    c.save()

    return send_file(pdf_path, as_attachment=True, download_name=os.path.basename(pdf_path))


# D) DRILL DOWN 
@app.route("/drill")
def drill():
    category = request.args.get("category", "").strip()
    to = request.args.get("to", "data").strip().lower()

    args = request.args.to_dict(flat=True)
    args.pop("to", None)

    if category:
        args["category"] = category

    if to == "dashboard":
        return redirect(url_for("index", **args))
    return redirect(url_for("data", **args))


# DASHBOARD
@app.route("/")
def index():
    ensure_dirs()
    try:
        df = get_service().load_df()
        x_col, y_col = pick_columns(df, request.args.get("x"), request.args.get("y"))
        date_col = detect_date_column(df)

        ctx = base_context(df, x_col, y_col, date_col)

        df_filtered = apply_filters(df, x_col, y_col, date_col)
        ctx["kpis"] = compute_kpis(df_filtered, x_col, y_col)

        if df_filtered.empty or x_col is None or y_col is None:
            ctx.update(error="No data matches the selected filters.", title="Analytics Dashboard")
            return render_template("index.html", **ctx)

        ctx["top_categories"] = df_filtered[x_col].astype(str).value_counts().head(5).index.tolist()

        # BAR
        bar_file = "dashboard_bar.png"
        plot.figure(figsize=(4, 3))
        grouped = df_filtered.groupby(x_col)[y_col].mean().sort_values(ascending=False).head(5)
        plot.bar(grouped.index.astype(str), grouped.values, color="#3f6ad8")
        _style_xticks()
        plot.tight_layout()
        plot.savefig(f"static/{bar_file}", dpi=120, bbox_inches="tight")
        plot.close()

        # LINE
        line_file = "dashboard_line.png"
        plot.figure(figsize=(4, 3))
        plot.plot(grouped.index.astype(str), grouped.values, linewidth=2, color="#3f6ad8")
        _style_xticks()
        plot.tight_layout()
        plot.savefig(f"static/{line_file}", dpi=120, bbox_inches="tight")
        plot.close()

        # PIE
        pie_file = "dashboard_pie.png"
        plot.figure(figsize=(4, 3))
        df_filtered[x_col].astype(str).value_counts().head(5).plot(kind="pie", autopct="%1.1f%%")
        plot.ylabel("")
        plot.tight_layout()
        plot.savefig(f"static/{pie_file}", dpi=120, bbox_inches="tight")
        plot.close()

        # SCATTER
        scatter_file = "dashboard_scatter.png"
        num_cols = df_filtered.select_dtypes(include="number").columns.tolist()
        plot.figure(figsize=(4, 3))
        if len(num_cols) >= 2:
            plot.scatter(df_filtered[num_cols[0]], df_filtered[num_cols[1]], alpha=0.6, color="#00a8a8")
            plot.xlabel(num_cols[0], fontsize=FONT_SIZE)
            plot.ylabel(num_cols[1], fontsize=FONT_SIZE)
        plot.tight_layout()
        plot.savefig(f"static/{scatter_file}", dpi=120, bbox_inches="tight")
        plot.close()

        # HEATMAP
        heatmap_file = "dashboard_heatmap.png"
        plot.figure(figsize=(4, 3))
        num_df = df_filtered.select_dtypes(include="number")
        if not num_df.empty:
            sns.heatmap(num_df.corr(), cmap="coolwarm", annot=False)
            _style_xticks()
        plot.tight_layout()
        plot.savefig(f"static/{heatmap_file}", dpi=120, bbox_inches="tight")
        plot.close()

        ctx.update(
            bar_url=cache_bust(bar_file),
            line_url=cache_bust(line_file),
            pie_url=cache_bust(pie_file),
            scatter_url=cache_bust(scatter_file),
            heatmap_url=cache_bust(heatmap_file),
            title="Analytics Dashboard",
        )
        return render_template("index.html", **ctx)

    except Exception as e:
        logger.exception("Dashboard error")
        return render_template("index.html", error=str(e), title="Analytics Dashboard")


# DATA TABLE 
@app.route("/data")
def data():
    ensure_dirs()
    try:
        df = get_service().load_df()
        x_col, y_col = pick_columns(df, request.args.get("x"), request.args.get("y"))
        date_col = detect_date_column(df)

        ctx = base_context(df, x_col, y_col, date_col)

        df_filtered = apply_filters(df, x_col, y_col, date_col)
        df_filtered = apply_search_sort(df_filtered)

        page = int(request.args.get("page", 1))
        per_page = 15
        total = len(df_filtered)

        start = (page - 1) * per_page
        end = start + per_page

        ctx.update(
            columns=list(df_filtered.columns),
            rows=df_filtered.iloc[start:end].values.tolist(),
            page=page,
            total=total,
            per_page=per_page,
            title="Data",
        )
        return render_template("data.html", **ctx)

    except Exception as e:
        logger.exception("Data route failed")
        return render_template("data.html", error=str(e), columns=[], rows=[], page=1, total=0, per_page=15, title="Data")


# ROUTES 
@app.route("/bar")
def bar_chart():
    df = get_service().load_df()
    x_col, y_col = pick_columns(df, request.args.get("x"), request.args.get("y"))
    date_col = detect_date_column(df)
    ctx = base_context(df, x_col, y_col, date_col)

    df_filtered = apply_filters(df, x_col, y_col, date_col)
    if df_filtered.empty or not x_col or not y_col:
        ctx.update(error="No data matches filters.", title="Bar Chart", chart_url=None)
        return render_template("chart.html", **ctx)

    grouped = df_filtered.groupby(x_col)[y_col].mean().sort_values(ascending=False).head(10)

    plot.figure(figsize=(10, 5))
    plot.bar(grouped.index.astype(str), grouped.values, color="#3f6ad8")
    plot.xlabel(x_col)
    plot.ylabel(f"{y_col} (Mean)")
    _style_xticks()
    plot.tight_layout()
    plot.savefig("static/bar.png", dpi=130, bbox_inches="tight")
    plot.close()

    ctx.update(
        title="Bar Chart",
        chart_url=cache_bust("bar.png"),
        download_url=url_for("static", filename="bar.png"),
        download_name="bar.png",
        top_categories=grouped.index.astype(str).tolist(),
    )
    return render_template("chart.html", **ctx)


@app.route("/line")
def line_chart():
    df = get_service().load_df()
    x_col, y_col = pick_columns(df, request.args.get("x"), request.args.get("y"))
    date_col = detect_date_column(df)
    ctx = base_context(df, x_col, y_col, date_col)

    df_filtered = apply_filters(df, x_col, y_col, date_col)
    if df_filtered.empty or not x_col or not y_col:
        ctx.update(error="No data matches filters.", title="Line Chart", chart_url=None)
        return render_template("chart.html", **ctx)

    grouped = df_filtered.groupby(x_col)[y_col].mean().sort_values(ascending=False).head(10)

    plot.figure(figsize=(10, 5))
    plot.plot(grouped.index.astype(str), grouped.values, linewidth=2, color="#3f6ad8")
    plot.xlabel(x_col)
    plot.ylabel(y_col)
    _style_xticks()
    plot.tight_layout()
    plot.savefig("static/line.png", dpi=130, bbox_inches="tight")
    plot.close()

    ctx.update(
        title="Line Chart",
        chart_url=cache_bust("line.png"),
        download_url=url_for("static", filename="line.png"),
        download_name="line.png",
    )
    return render_template("chart.html", **ctx)


@app.route("/scatter")
def scatter_chart():
    df = get_service().load_df()
    date_col = detect_date_column(df)

    num_cols = df.select_dtypes(include="number").columns.tolist()
    ctx = base_context(df, None, None, date_col)

    if len(num_cols) < 2:
        ctx.update(error="Not enough numeric columns for scatter plot.", title="Scatter Chart", chart_url=None)
        return render_template("chart.html", **ctx)

    df_filtered = apply_filters(df, None, None, date_col, scatter_cols=num_cols)

    plot.figure(figsize=(8, 6))
    plot.scatter(df_filtered[num_cols[0]], df_filtered[num_cols[1]], alpha=0.7, color="#00a8a8")
    plot.xlabel(num_cols[0])
    plot.ylabel(num_cols[1])
    plot.tight_layout()
    plot.savefig("static/scatter.png", dpi=130, bbox_inches="tight")
    plot.close()

    ctx.update(
        title="Scatter Chart",
        chart_url=cache_bust("scatter.png"),
        download_url=url_for("static", filename="scatter.png"),
        download_name="scatter.png",
    )
    return render_template("chart.html", **ctx)


@app.route("/heatmap")
def heatmap_chart():
    df = get_service().load_df()
    date_col = detect_date_column(df)
    ctx = base_context(df, None, None, date_col)

    df_filtered = apply_filters(df, None, None, date_col)
    num_df = df_filtered.select_dtypes(include="number")
    if num_df.empty:
        ctx.update(error="No numeric data for heatmap.", title="Heatmap", chart_url=None)
        return render_template("chart.html", **ctx)

    plot.figure(figsize=(10, 8))
    sns.heatmap(num_df.corr(), cmap="coolwarm", annot=False)
    _style_xticks()
    plot.tight_layout()
    plot.savefig("static/heatmap.png", dpi=130, bbox_inches="tight")
    plot.close()

    ctx.update(
        title="Heatmap",
        chart_url=cache_bust("heatmap.png"),
        download_url=url_for("static", filename="heatmap.png"),
        download_name="heatmap.png",
    )
    return render_template("chart.html", **ctx)


@app.route("/pie")
def pie():
    df = get_service().load_df()
    x_col, _ = pick_columns(df, request.args.get("x"), request.args.get("y"))
    date_col = detect_date_column(df)
    ctx = base_context(df, x_col, None, date_col)

    df_filtered = apply_filters(df, x_col, None, date_col)
    if df_filtered.empty or not x_col:
        ctx.update(error="No data matches filters.", title="Pie Chart", pie_url=None)
        return render_template("pie.html", **ctx)

    plot.figure(figsize=(7, 7))
    df_filtered[x_col].astype(str).value_counts().head(10).plot(kind="pie", autopct="%1.1f%%")
    plot.ylabel("")
    plot.tight_layout()
    plot.savefig("static/pie.png", dpi=130, bbox_inches="tight")
    plot.close()

    ctx.update(
        title="Pie Chart",
        pie_url=cache_bust("pie.png"),
        download_url=url_for("static", filename="pie.png"),
        download_name="pie.png",
    )
    return render_template("pie.html", **ctx)


if __name__ == "__main__":
    ensure_dirs()
    app.run(debug=True)