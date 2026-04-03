# DVMS

**Data Visualization and Management System**

DNA‑DVMS is a modern, extensible data visualization and analytics dashboard built with **Flask**, **Pandas**, and **Matplotlib**.  
The system ingests Excel or CSV datasets, provides an interactive dashboard overview, and supports multiple analytical visualizations with a professional UI inspired by enterprise BI tools.

---

## Key Features

### Data Ingestion & Management

- Load data from **Excel (.xlsx / .xls)** or **CSV (.csv)** files
- Environment‑based dataset configuration via `DATA_PATH`
- Data‑agnostic (no fixed schema required)
- Automatic detection of numeric and non‑numeric columns
- Clean, Bootstrap‑styled data table view

---

### Visualization Capabilities

DNA‑DVMS supports **multiple chart types**, each available both on the dashboard and as individual analytical pages:

- **Bar Chart**
  - Aggregated numeric values (mean)
  - Frequency fallback when numeric data is unavailable
- **Line Chart**
  - Trend visualization over grouped categories
- **Pie Chart**
  - Distribution or frequency‑based visualization
- **Scatter Plot**
  - Correlation view between numeric variables
- **Heatmap**
  - Correlation matrix of numeric features (Seaborn‑powered)

> All charts are rendered server‑side and saved as cache‑busted images to ensure the latest data is always displayed.

---

### Interactive Analytics Dashboard

- Unified **Analytics Dashboard** displaying:
  - Bar, Line, Pie, Scatter, and Heatmap charts together
- Card‑based responsive layout (Bootstrap grid)
- Light gray analytics background with white chart cards
- Professional color palette inspired by enterprise dashboards

---

### Navigation & UI

- **Vertical sidebar navigation** with:
  - Dashboard
  - Bar Chart
  - Line Chart
  - Scatter Plot
  - Heatmap
  - Pie Chart
  - Data Table
- **Sidebar toggle button**:
  - Collapse / expand navigation for focused analysis
  - Improves usability on smaller screens
- Active link highlighting
- Consistent layout using a shared base template

---

### Project Structure

DVMS/
│  app.py
│  data.xlsx / data.csv
│  README.md
│  requirements.txt
│
├── service/
│   ├── __init__.py
│   └── service.py
│
├── templates/
│   ├── base.html
│   ├── index.html
│   ├── chart.html
│   ├── data.html
│   └── pie.html
│
└── static/
    ├── bar.png
    ├── line.png
    ├── scatter.png
    ├── heatmap.png
    ├── pie.png

### Architecture & Design

- Clear **separation of concerns**:
  - Routing (Flask)
  - Data loading (DataService)
  - Visualization (Matplotlib / Seaborn)
  - UI rendering (Jinja2 + Bootstrap)
- Reusable chart rendering template
- Graceful fallback when required data types are missing
- Easily extensible for:
  - Database integration
  - REST APIs
  - Interactive charts (Chart.js / Plotly)
  - Drill‑down analytics

┌──────────────────────────┐
│        Web Browser       │
└────────────▲─────────────┘
             │ HTTP Requests
┌────────────┴─────────────┐
│        Flask App         │
│         (app.py)         │
└────────────▲─────────────┘
             │
┌────────────┴─────────────┐
│      DataService         │
│  Load Excel / CSV files  │
└────────────▲─────────────┘
             │
┌────────────┴─────────────┐
│        Pandas            │
│       DataFrame          │
└────────────▲─────────────┘
             │
┌────────────┴─────────────┐
│   Matplotlib / Seaborn   │
│  Chart image generation  │
└────────────▲─────────────┘
             │
┌────────────┴─────────────┐
│      Static PNG Files    │
└────────────▲─────────────┘
             │
┌────────────┴─────────────┐
│     Jinja2 Templates     │
│  Dashboard & UI Rendering│
└────────────▲─────────────┘
             │
        Rendered HTML Page


---

## Supported Data Formats

- `.xlsx` (Excel)
- `.xls` (Legacy Excel)
- `.csv` (Comma‑Separated Values)

To use a CSV or custom file, set:

```bash
DATA_PATH=data.csv