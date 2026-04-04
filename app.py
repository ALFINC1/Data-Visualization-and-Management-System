import os
import time
import logging
import numpy as np
import pandas as pd
import seaborn as sns
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plot
import matplotlib.ticker as ticker

from flask import Flask, render_template, url_for, request
from service.service import DataService

app = Flask(__name__)

logging.basicConfig(level=logging.INFO, format="%(levelname)s:  %(message)s")
logger = logging.getLogger(__name__)

DATA_PATH = os.getenv("DATA_PATH", "data.xlsx")
service = DataService(DATA_PATH)

ROTATION = 40
FONT_SIZE = 6


# ---------------- HELPERS ----------------
def create_static():
    os.makedirs("static", exist_ok=True)


def formats(v):
    if v is None:
        return "-"
    try:
        i = int(v)
        if abs(v - i) < 1e-9:   # ✅ fixed (<) operator
            return f"{i:,}"
        return f"{v:,.2f}"
    except:
        return str(v)


def cache_bust(filename):
    return url_for("static", filename=filename) + f"?v={int(time.time())}"


def pick_columns(df, x_override, y_override):
    num = df.select_dtypes(include="number").columns.tolist()
    nonnum = [c for c in df.columns if c not in num]

    y = y_override if (y_override and y_override in df.columns) else (num[0] if num else None)
    x = x_override if (x_override and x_override in df.columns) else (nonnum[0] if nonnum else None)

    return df, x, y


def detect_date_column(df):
    """
    Try to detect a date-like column (for Date dropdown filter).
    Priority:
      1) datetime dtype columns
      2) columns containing 'date' in name
      3) columns containing 'year' in name
    """
    # 1) datetime dtype columns
    for c in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[c]):
            return c

    # 2) name contains 'date'
    for c in df.columns:
        if "date" in str(c).lower():
            return c

    # 3) name contains 'year'
    for c in df.columns:
        if "year" in str(c).lower():
            return c

    return None


def build_filter_options(df, x_col, y_col, date_col):
    """
    Build lists/ranges for UI dropdowns and numeric inputs.
    These are passed to templates, so UI can show filter controls.
    """
    categories = []
    if x_col and x_col in df.columns:
        categories = (
            df[x_col]
            .dropna()
            .astype(str)
            .value_counts()
            .index
            .tolist()
        )

    date_values = []
    if date_col and date_col in df.columns:
        s = df[date_col].dropna()

        # If datetime, convert to yyyy-mm-dd strings
        if pd.api.types.is_datetime64_any_dtype(s):
            date_values = (
                pd.to_datetime(s, errors="coerce")
                .dropna()
                .dt.strftime("%Y-%m-%d")
                .value_counts()
                .index
                .tolist()
            )
        else:
            # Keep unique values as strings
            date_values = (
                s.astype(str)
                .value_counts()
                .index
                .tolist()
            )

    y_min, y_max = None, None
    if y_col and y_col in df.columns and pd.api.types.is_numeric_dtype(df[y_col]):
        s = df[y_col].dropna()
        if not s.empty:
            y_min = float(s.min())
            y_max = float(s.max())

    return categories, date_values, y_min, y_max


def apply_filters(df, x_col, y_col, date_col):
    """
    Apply Category, Date, and Numeric Range filters to the DataFrame.
    Filters come from URL query parameters:
      - category=<value>
      - date=<value>
      - min=<number>
      - max=<number>
    """
    # ---- Category filter (dropdown) ----
    category = request.args.get("category", "").strip()
    if category and x_col and x_col in df.columns:
        df = df[df[x_col].astype(str) == category]

    # ---- Date filter (dropdown) ----
    date_val = request.args.get("date", "").strip()
    if date_val and date_col and date_col in df.columns:
        series = df[date_col]

        if pd.api.types.is_datetime64_any_dtype(series):
            dt = pd.to_datetime(series, errors="coerce")
            df = df[dt.dt.strftime("%Y-%m-%d") == date_val]
        else:
            # Treat as string match
            df = df[series.astype(str) == date_val]

    # ---- Numeric range filter (min/max) ----
    min_val = request.args.get("min", "").strip()
    max_val = request.args.get("max", "").strip()

    if y_col and y_col in df.columns and pd.api.types.is_numeric_dtype(df[y_col]):
        if min_val:
            try:
                df = df[df[y_col] >= float(min_val)]
            except:
                pass
        if max_val:
            try:
                df = df[df[y_col] <= float(max_val)]
            except:
                pass

    return df


def current_filters():
    """
    Convenience: pass current filter values back to templates
    so the UI can keep controls selected.
    """
    return {
        "category": request.args.get("category", "").strip(),
        "date": request.args.get("date", "").strip(),
        "min": request.args.get("min", "").strip(),
        "max": request.args.get("max", "").strip(),
    }


# ================= DASHBOARD =================
@app.route("/")
def index():
    try:
        df = service.load_df()
        create_static()

        # Detect default columns
        df, x_col, y_col = pick_columns(df, None, None)
        date_col = detect_date_column(df)

        # Apply filters
        df_filtered = apply_filters(df, x_col, y_col, date_col)

        # Filter options for UI dropdowns
        categories, date_values, y_min, y_max = build_filter_options(df, x_col, y_col, date_col)
        filters = current_filters()

        # Safety: if filtered data becomes empty, render error-friendly dashboard
        if df_filtered.empty:
            return render_template(
                "index.html",
                error="No data matches the selected filters.",
                title="Analytics Dashboard",
                categories=categories,
                date_values=date_values,
                y_min=y_min,
                y_max=y_max,
                filters=filters
            )

        # BAR (Top 5)
        bar_file = "dashboard_bar.png"
        plot.figure(figsize=(4, 3))
        grouped = (
            df_filtered.groupby(x_col)[y_col]
            .mean()
            .sort_values(ascending=False)
            .head(5)
        )
        plot.bar(grouped.index.astype(str), grouped.values, color="#3f6ad8")
        plot.xlabel(x_col, fontsize=FONT_SIZE)
        plot.ylabel(f"{y_col} (Mean)", fontsize=FONT_SIZE)
        plot.xticks(rotation=ROTATION, ha="right", fontsize=FONT_SIZE)
        plot.yticks(fontsize=FONT_SIZE)
        plot.tight_layout()
        plot.savefig(f"static/{bar_file}", dpi=120)
        plot.close()

        # LINE (Top 5)
        line_file = "dashboard_line.png"
        plot.figure(figsize=(4, 3))
        plot.plot(grouped.index.astype(str), grouped.values, linewidth=2, color="#3f6ad8")
        plot.xlabel(x_col, fontsize=FONT_SIZE)
        plot.ylabel(f"{y_col} (Mean)", fontsize=FONT_SIZE)
        plot.xticks(rotation=ROTATION, ha="right", fontsize=FONT_SIZE)
        plot.yticks(fontsize=FONT_SIZE)
        plot.tight_layout()
        plot.savefig(f"static/{line_file}", dpi=120)
        plot.close()

        # PIE (Top 5)
        pie_file = "dashboard_pie.png"
        plot.figure(figsize=(4, 3))
        df_filtered[x_col].astype(str).value_counts().head(5).plot(kind="pie", autopct="%1.1f%%")
        plot.ylabel("")
        plot.tight_layout()
        plot.savefig(f"static/{pie_file}", dpi=120)
        plot.close()

        # SCATTER (first two numeric cols, filtered)
        scatter_file = "dashboard_scatter.png"
        num_cols = df_filtered.select_dtypes(include="number").columns.tolist()
        plot.figure(figsize=(4, 3))
        if len(num_cols) >= 2:
            plot.scatter(df_filtered[num_cols[0]], df_filtered[num_cols[1]], alpha=0.6, color="#00a8a8")
            plot.xlabel(num_cols[0], fontsize=FONT_SIZE)
            plot.ylabel(num_cols[1], fontsize=FONT_SIZE)
            plot.xticks(fontsize=FONT_SIZE)
            plot.yticks(fontsize=FONT_SIZE)
        else:
            plot.text(0.5, 0.5, "Not enough numeric columns for scatter", ha="center", va="center")
            plot.axis("off")
        plot.tight_layout()
        plot.savefig(f"static/{scatter_file}", dpi=120)
        plot.close()

        # HEATMAP (numeric correlation, filtered)
        heatmap_file = "dashboard_heatmap.png"
        plot.figure(figsize=(4, 3))
        num_df = df_filtered.select_dtypes(include="number")
        if not num_df.empty:
            sns.heatmap(num_df.corr(), cmap="coolwarm", annot=False)
            plot.xlabel("Variables", fontsize=FONT_SIZE)
            plot.ylabel("Variables", fontsize=FONT_SIZE)
            plot.xticks(rotation=ROTATION, ha="right", fontsize=FONT_SIZE)
            plot.yticks(fontsize=FONT_SIZE)
        else:
            plot.text(0.5, 0.5, "No numeric columns for heatmap", ha="center", va="center")
            plot.axis("off")
        plot.tight_layout()
        plot.savefig(f"static/{heatmap_file}", dpi=120)
        plot.close()

        return render_template(
            "index.html",
            bar_url=cache_bust(bar_file),
            line_url=cache_bust(line_file),
            pie_url=cache_bust(pie_file),
            scatter_url=cache_bust(scatter_file),
            heatmap_url=cache_bust(heatmap_file),
            title="Analytics Dashboard",

            # ✅ NEW: filter controls data for UI
            categories=categories,
            date_values=date_values,
            y_min=y_min,
            y_max=y_max,
            filters=filters,
        )

    except Exception as e:
        logger.exception("Dashboard error")
        return render_template("index.html", error=str(e), title="Analytics Dashboard")


# ================= DATA TABLE =================
@app.route("/data")
def data():
    try:
        df = service.load_df()

        df, x_col, y_col = pick_columns(df, None, None)
        date_col = detect_date_column(df)

        df_filtered = apply_filters(df, x_col, y_col, date_col)

        # Filter options for UI
        categories, date_values, y_min, y_max = build_filter_options(df, x_col, y_col, date_col)
        filters = current_filters()

        page = int(request.args.get("page", 1))
        per_page = 18
        total = len(df_filtered)

        start = (page - 1) * per_page
        end = start + per_page

        rows = df_filtered.iloc[start:end].values.tolist()

        return render_template(
            "data.html",
            columns=list(df_filtered.columns),
            rows=rows,
            page=page,
            total=total,
            per_page=per_page,
            title="Data",

            # ✅ NEW: filter controls data for UI
            categories=categories,
            date_values=date_values,
            y_min=y_min,
            y_max=y_max,
            filters=filters,
        )

    except Exception as e:
        logger.exception("Data route failed")
        return render_template(
            "data.html",
            error=str(e),
            columns=[],
            rows=[],
            page=1,
            total=0,
            per_page=18,
            title="Data",
            categories=[],
            date_values=[],
            y_min=None,
            y_max=None,
            filters=current_filters(),
        )


# ================= DETAIL: BAR =================
@app.route("/bar")
def bar_chart():
    try:
        df = service.load_df()
        x_override = request.args.get("x")
        y_override = request.args.get("y")
        df, x, y = pick_columns(df, x_override, y_override)
        date_col = detect_date_column(df)

        df_filtered = apply_filters(df, x, y, date_col)

        if df_filtered.empty:
            return render_template("chart.html", error="No data matches the selected filters.", title="Bar Chart", meta=None)

        grouped = df_filtered.groupby(x)[y].mean().sort_values(ascending=False).head(10)

        create_static()
        plot.figure(figsize=(10, 5))
        plot.bar(grouped.index.astype(str), grouped.values, color="#3f6ad8")
        plot.xlabel(x)
        plot.ylabel(f"{y} (Mean)")
        plot.xticks(rotation=ROTATION, ha="right")
        plot.tight_layout()
        plot.savefig("static/bar.png", dpi=130)
        plot.close()

        return render_template("chart.html", chart_url=cache_bust("bar.png"), title="Bar Chart", meta=None)

    except Exception as e:
        logger.exception("Bar chart error")
        return render_template("chart.html", error=str(e), title="Bar Chart", meta=None)


# ================= DETAIL: LINE =================
@app.route("/line")
def line_chart():
    try:
        df = service.load_df()
        x_override = request.args.get("x")
        y_override = request.args.get("y")
        df, x, y = pick_columns(df, x_override, y_override)
        date_col = detect_date_column(df)

        df_filtered = apply_filters(df, x, y, date_col)

        if df_filtered.empty:
            return render_template("chart.html", error="No data matches the selected filters.", title="Line Chart", meta=None)

        grouped = df_filtered.groupby(x)[y].mean().sort_values(ascending=False).head(10)

        create_static()
        plot.figure(figsize=(10, 5))
        plot.plot(grouped.index.astype(str), grouped.values, marker="o")
        plot.xlabel(x)
        plot.ylabel(y)
        plot.xticks(rotation=ROTATION, ha="right")
        plot.tight_layout()
        plot.savefig("static/line.png", dpi=130)
        plot.close()

        return render_template("chart.html", chart_url=cache_bust("line.png"), title="Line Chart", meta=None)

    except Exception as e:
        logger.exception("Line chart error")
        return render_template("chart.html", error=str(e), title="Line Chart", meta=None)


# ================= DETAIL: SCATTER =================
@app.route("/scatter")
def scatter_chart():
    try:
        df = service.load_df()

        # detect columns and apply filters (use first non-numeric as category filter)
        df, x, y = pick_columns(df, request.args.get("x"), request.args.get("y"))
        date_col = detect_date_column(df)
        df_filtered = apply_filters(df, x, y, date_col)

        cols = df_filtered.select_dtypes(include="number").columns.tolist()
        if len(cols) < 2:
            raise ValueError("Not enough numeric columns for scatter plot")

        plot.figure(figsize=(8, 6))
        plot.scatter(df_filtered[cols[0]], df_filtered[cols[1]], alpha=0.7)
        plot.xlabel(cols[0])
        plot.ylabel(cols[1])
        plot.xticks(rotation=ROTATION, ha="right")
        plot.tight_layout()
        plot.savefig("static/scatter.png", dpi=130)
        plot.close()

        return render_template("chart.html", chart_url=cache_bust("scatter.png"), title="Scatter Chart", meta=None)

    except Exception as e:
        logger.exception("Scatter chart error")
        return render_template("chart.html", error=str(e), title="Scatter Chart", meta=None)


# ================= DETAIL: HEATMAP =================
@app.route("/heatmap")
def heatmap_chart():
    try:
        df = service.load_df()

        df, x, y = pick_columns(df, request.args.get("x"), request.args.get("y"))
        date_col = detect_date_column(df)
        df_filtered = apply_filters(df, x, y, date_col)

        num_df = df_filtered.select_dtypes(include="number")
        if num_df.empty:
            raise ValueError("No numeric data for heatmap")

        plot.figure(figsize=(10, 8))
        sns.heatmap(num_df.corr(), annot=True, cmap="coolwarm", fmt=".2f")
        plot.xlabel("Variables")
        plot.ylabel("Variables")
        plot.xticks(rotation=ROTATION, ha="right")
        plot.tight_layout()
        plot.savefig("static/heatmap.png", dpi=130)
        plot.close()

        return render_template("chart.html", chart_url=cache_bust("heatmap.png"), title="Heatmap", meta=None)

    except Exception as e:
        logger.exception("Heatmap error")
        return render_template("chart.html", error=str(e), title="Heatmap", meta=None)


# ================= DETAIL: PIE =================
@app.route("/pie")
def pie():
    try:
        df = service.load_df()

        df, x, y = pick_columns(df, request.args.get("x"), request.args.get("y"))
        date_col = detect_date_column(df)
        df_filtered = apply_filters(df, x, y, date_col)

        if df_filtered.empty:
            return render_template("pie.html", error="No data matches the selected filters.", title="Pie Chart")

        col = x if x else df_filtered.columns[0]

        plot.figure(figsize=(7, 7))
        df_filtered[col].astype(str).value_counts().head(10).plot(kind="pie", autopct="%1.1f%%")
        plot.tight_layout()
        plot.savefig("static/pie.png", dpi=130)
        plot.close()

        return render_template("pie.html", pie_url=cache_bust("pie.png"), title="Pie Chart")

    except Exception as e:
        logger.exception("Pie chart error")
        return render_template("pie.html", error=str(e), title="Pie Chart")


if __name__ == "__main__":
    app.run(debug=True)