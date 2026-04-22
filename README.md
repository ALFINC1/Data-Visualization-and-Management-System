# DVMS

## Data Visualization and Management System

DVMS (Data Visualization and Management System) is a modern, extensible analytics dashboard built with **Flask**, **Pandas**, **Matplotlib**, and **Seaborn**.  
It ingests Excel or CSV datasets, provides a clean analytics dashboard, and supports professional‑grade visualizations and analytics features following enterprise BI design principles.

---

## Overview

DVMS bridges the gap between raw data and actionable insight.  
It emphasizes **clarity, hierarchy, and usability**, ensuring users can explore datasets effectively without visual clutter.

The system separates:

- **Dashboard view** → high‑level overview (KPIs + summary charts)  
- **Detail views** → focused analytical exploration (individual charts)  
- **Data view** → searchable, sortable, paginated table  

---

## Key Features

### 1) Data Ingestion & Management

- Load datasets from:
  - `.xlsx` (Excel)
  - `.xls` (Legacy Excel)
  - `.csv` (Comma‑separated values)
- Environment‑based dataset configuration using `DATA_PATH`
- Fully data‑agnostic (no fixed schema required)
- Automatic detection of numeric and non‑numeric columns
- Centralized data loading via a dedicated `DataService` layer
- Upload dataset from the UI (CSV/XLS/XLSX) and instantly activate it
- Reset dataset to default data source

---

### 2) Filters & Controls (Applied Everywhere)

Filters are implemented using URL query parameters and apply consistently to:

✅ Dashboard  
✅ Detail charts  
✅ Data table

Supported filters:

- **Category filter** (dropdown)
- **Date filter** (auto‑detects a date‑like column if present)
- **Numeric range filter** (`min` / `max`)

The current filter state is always shareable through the URL.

---

### 3) Analytics Dashboard (Overview)

Unified dashboard showing:

- Bar Chart
- Line Chart
- Pie Chart
- Scatter Plot
- Heatmap

Dashboard optimized for clarity:

- Top‑5 values by default
- Reduced visual noise
- Rotated x‑axis labels (40°)
- Smaller font sizes for dense labels
- Clean line charts without clutter
- Tight layout to avoid clipping / overlap
- **KPI cards** computed from the filtered dataset:
  - Rows, Columns, Numeric columns
  - Distinct categories
  - Min/Mean/Max for the selected numeric metric
- **Quick Drill‑Down** (Top Categories → open filtered Data view)

> The dashboard answers *what is happening*, while detail pages explain *why*.

---

### 4) Visualization Capabilities

Each visualization is available both on the dashboard and as an individual analytical page.

- **Bar Chart** → Aggregated numeric values (mean)  
- **Line Chart** → Trend visualization across categories  
- **Pie Chart** → Distribution and frequency analysis  
- **Scatter Plot** → Correlation between numeric variables  
- **Heatmap** → Correlation matrix of numeric columns (Seaborn‑powered)

All charts:

- Rendered **server‑side**
- Saved as **PNG files**
- Cache‑busted automatically for freshness
- **Download chart as PNG** from detail pages
- Improved readability: axis labeling and rotation

---

### 5) Data Table View (Professional Data Explorer)

- Clean, professional table design
- Styled with:
  - Blue header
  - Blue cell borders
  - Hover highlighting
- Pagination enabled (default 15 rows per page)
- **Search** across all table values
- **Sort** by any column with **asc/desc**
- **Export filtered data to CSV**

---

### 6) Navigation & UI

- Vertical sidebar navigation:
  - Dashboard
  - Bar Chart
  - Line Chart
  - Scatter Plot
  - Heatmap
  - Pie Chart
  - Data Table
- Filter bar integrated into UI
- Copy link / shareable dashboard state
- Export controls (CSV + PDF)
- Upload dataset controls
- Saved views (presets) interface

---

### 7) Sharing, Presets, Reports (Enterprise Features)
- **Copy Link** button: share the current dashboard/table/chart state (filters included)
- **Saved Views / Presets (Bookmarks)**:
  - Save a named view
  - Apply a saved view
  - Delete saved views
- **Export PDF Report**:
  - Generates a PDF report that includes:
    - dataset info
    - active filters
    - KPI summary
    - (optional) dashboard snapshot charts

---

## Endpoints Summary

Core pages:

- `/` → Dashboard
- `/data` → Data table (search/sort/pagination)
- `/bar` → Bar chart detail
- `/line` → Line chart detail
- `/scatter` → Scatter plot detail
- `/heatmap` → Heatmap detail
- `/pie` → Pie chart detail

Exports:

- `/export/data.csv` → Export filtered data table to CSV
- `/export/report.pdf` → Export analytics report to PDF

Upload / dataset:

- `/upload` (POST) → Upload dataset and activate it
- `/dataset/reset` → Reset dataset to default

Presets:

- `/presets/save` (POST) → Save current view
- `/presets/apply/<preset_id>` → Apply saved view
- `/presets/delete/<preset_id>` → Delete saved view

Drill‑down:

- `/drill?category=<value>&to=data` → Opens filtered data view
- `/drill?category=<value>&to=dashboard` → Opens filtered dashboard view

---

## Project Structure

```text
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
├── static/
│   ├── dashboard_bar.png
│   ├── dashboard_line.png
│   ├── dashboard_pie.png
│   ├── dashboard_scatter.png
│   ├── dashboard_heatmap.png
│   ├── bar.png
│   ├── line.png
│   ├── scatter.png
│   ├── heatmap.png
│   └── pie.png
│
├── uploads/      
│   └── uploaded files saved here
│
└── storage/ 
    └── presets.json   saved views / bookmarks
  
``
