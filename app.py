import os
import time
import seaborn as sns
import logging
import numpy as np
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


# ---------------- HELPERS ----------------
def create_static():
    os.makedirs("static", exist_ok=True)


def formats(v):
    if v is None:
        return "-"
    try:
        i = int(v)
        if abs(v - i) < 1e-9:
            return f"{i:,}"
        return f"{v:,.2f}"
    except:
        return str(v)


def cache_bust(filename):
    return url_for("static", filename=filename) + f"?v={int(time.time())}"


def pick_columns(df, x_override, y_override):
    num = df.select_dtypes(include="number").columns.tolist()
    nonnum = [c for c in df.columns if c not in num]

    y = y_override if y_override in df.columns else (num[0] if num else None)

    if x_override in df.columns:
        x = x_override
    else:
        x = nonnum[0] if nonnum else None

    return df, x, y

# ---------------- DASHBOARD ----------------
@app.route("/")
def index():
    try:
        df = service.load_df()
        create_static()

        # ---------------- BAR ----------------
        bar_file = "dashboard_bar.png"
        plot.figure(figsize=(4, 3))
        (
            df.groupby(df.columns[0])[df.columns[1]]
            .mean()
            .sort_values(ascending=False)
            .head(5)
            .plot(kind="bar", color="#3f6ad8")
        )
        plot.tight_layout()
        plot.savefig(f"static/{bar_file}", dpi=120)
        plot.close()

        # ---------------- LINE ----------------
        line_file = "dashboard_line.png"
        plot.figure(figsize=(4, 3))
        (
            df.groupby(df.columns[0])[df.columns[1]]
            .mean()
            .sort_values(ascending=False)
            .head(5)
            .plot(marker="o", linewidth=2, color="#28a745")
        )
        plot.tight_layout()
        plot.savefig(f"static/{line_file}", dpi=120)
        plot.close()

        # ---------------- PIE ----------------
        pie_file = "dashboard_pie.png"
        plot.figure(figsize=(4, 3))
        (
            df[df.columns[0]]
            .value_counts()
            .head(5)
            .plot(kind="pie", autopct="%1.1f%%")
        )
        plot.ylabel("")
        plot.tight_layout()
        plot.savefig(f"static/{pie_file}", dpi=120)
        plot.close()

        # ---------------- SCATTER ----------------
        scatter_file = "dashboard_scatter.png"
        num_cols = df.select_dtypes(include="number").columns.tolist()
        plot.figure(figsize=(4, 3))
        plot.scatter(
            df[num_cols[0]],
            df[num_cols[1]],
            alpha=0.6,
            color="#17a2b8"
        )
        plot.xlabel(num_cols[0])
        plot.ylabel(num_cols[1])
        plot.tight_layout()
        plot.savefig(f"static/{scatter_file}", dpi=120)
        plot.close()

        # ---------------- HEATMAP ----------------
        heatmap_file = "dashboard_heatmap.png"
        plot.figure(figsize=(4, 3))
        sns.heatmap(
            df.select_dtypes(include="number").corr(),
            cmap="coolwarm",
            annot=False
        )
        plot.tight_layout()
        plot.savefig(f"static/{heatmap_file}", dpi=120)
        plot.close()

        # ---------------- RENDER ----------------
        return render_template(
            "index.html",
            bar_url=cache_bust(bar_file),
            line_url=cache_bust(line_file),
            pie_url=cache_bust(pie_file),
            scatter_url=cache_bust(scatter_file),
            heatmap_url=cache_bust(heatmap_file),
            title="Analytics Dashboard"
        )

    except Exception as e:
        logger.exception("Dashboard error")
        return render_template("index.html", error=str(e))
    
@app.route("/data")
def data():
    try:
        df = service.load_df()
        return render_template(
            "data.html",
            columns=list(df.columns),
            rows=df.values.tolist(),
            title="Data"
        )
    except Exception as e:
        logger.exception("Data route failed")
        return render_template("data.html", error=str(e), rows=[], columns=[])


# ---------------- BAR ----------------
@app.route("/bar")
def bar_chart():
    try:
        df = service.load_df()
        df, x, y = pick_columns(df, None, None)

        create_static()
        plot.figure(figsize=(10, 5))

        grouped = (
            df.groupby(x)[y]
            .mean()
            .sort_values(ascending=False)
            .head(10)
        )

        plot.bar(grouped.index, grouped.values, color="#1f77b4")
        plot.xticks(rotation=30, ha="right")
        plot.tight_layout()

        file = "bar.png"
        plot.savefig(f"static/{file}", dpi=130)
        plot.close()

        return render_template(
            "chart.html",
            chart_url=cache_bust(file),
            title="Bar Chart"
        )

    except Exception as e:
        logger.exception("Bar chart error")
        return render_template("chart.html", error=str(e))


# ---------------- LINE ----------------
@app.route("/line")
def line_chart():
    try:
        df = service.load_df()
        df, x, y = pick_columns(df, None, None)

        create_static()
        plot.figure(figsize=(10, 5))

        grouped = (
            df.groupby(x)[y]
            .mean()
            .sort_values(ascending=False)
            .head(10)
        )

        plot.plot(grouped.index, grouped.values, marker="o")
        plot.xticks(rotation=30, ha="right")
        plot.tight_layout()

        file = "line.png"
        plot.savefig(f"static/{file}", dpi=130)
        plot.close()

        return render_template(
            "chart.html",
            chart_url=cache_bust(file),
            title="Line Chart"
        )

    except Exception as e:
        logger.exception("Line chart error")
        return render_template("chart.html", error=str(e))


# ---------------- SCATTER ----------------
@app.route("/scatter")
def scatter_chart():
    try:
        df = service.load_df()
        num_cols = df.select_dtypes(include="number").columns.tolist()

        if len(num_cols) < 2:
            raise ValueError("Not enough numeric columns for scatter plot")

        create_static()
        plot.figure(figsize=(8, 6))
        plot.scatter(df[num_cols[0]], df[num_cols[1]], alpha=0.7)
        plot.xlabel(num_cols[0])
        plot.ylabel(num_cols[1])
        plot.tight_layout()

        file = "scatter.png"
        plot.savefig(f"static/{file}", dpi=130)
        plot.close()

        return render_template(
            "chart.html",
            chart_url=cache_bust(file),
            title="Scatter Chart"
        )

    except Exception as e:
        logger.exception("Scatter chart error")
        return render_template("chart.html", error=str(e))


# ---------------- HEATMAP ----------------
@app.route("/heatmap")
def heatmap_chart():
    try:
        df = service.load_df()
        num_df = df.select_dtypes(include="number")

        if num_df.empty:
            raise ValueError("No numeric data for heatmap")

        create_static()
        plot.figure(figsize=(10, 8))
        sns.heatmap(num_df.corr(), annot=True, cmap="coolwarm", fmt=".2f")
        plot.tight_layout()

        file = "heatmap.png"
        plot.savefig(f"static/{file}", dpi=130)
        plot.close()

        return render_template(
            "chart.html",
            chart_url=cache_bust(file),
            title="Heatmap"
        )

    except Exception as e:
        logger.exception("Heatmap error")
        return render_template("chart.html", error=str(e))


# ---------------- PIE ----------------
@app.route("/pie")
def pie():
    try:
        df = service.load_df()
        col = df.columns[0]

        create_static()
        plot.figure(figsize=(7, 7))
        data = df[col].value_counts().head(10)
        plot.pie(data.values, labels=data.index, autopct="%1.1f%%")
        plot.tight_layout()

        file = "pie.png"
        plot.savefig(f"static/{file}", dpi=130)
        plot.close()

        return render_template(
            "pie.html",
            pie_url=cache_bust(file),
            title="Pie Chart"
        )

    except Exception as e:
        logger.exception("Pie chart error")
        return render_template("pie.html", error=str(e))


if __name__ == "__main__":
    app.run(debug=True)