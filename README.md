# DVMS  

## Data Visualization and Management System

DVMS (Data Visualization and Management System) is a modern, extensible analytics dashboard built with **Flask**, **Pandas**, **Matplotlib**, and **Seaborn**.  
It ingests Excel or CSV datasets, provides a clean analytics dashboard, and supports multiple professional‑grade visualizations following enterprise BI design principles.

---

## Overview

DVMS bridges the gap between raw data and actionable insight.  
It emphasizes **clarity, hierarchy, and usability**, ensuring that users can explore datasets effectively without visual clutter.

The system separates:

- **Dashboard view** → high‑level overview  
- **Detail views** → focused analytical exploration  

---

## Key Features

### Data Ingestion & Management

- Load datasets from:
  - `.xlsx` (Excel)
  - `.xls` (Legacy Excel)
  - `.csv` (Comma‑separated values)
- Environment‑based dataset configuration using `DATA_PATH`
- Fully **data‑agnostic** (no fixed schema required)
- Automatic detection of numeric and non‑numeric columns
- Centralized data loading via a dedicated `DataService` layer

---

### Analytics Dashboard

- Unified dashboard showing:
  - Bar Chart
  - Line Chart
  - Pie Chart
  - Scatter Plot
  - Heatmap
- Optimized for clarity:
  - Top‑5 values by default
  - Reduced visual noise
  - Rotated x‑axis labels (40°)
  - Smaller font sizes for dense labels
  - Clean line charts without clutter
- Responsive, card‑based layout using Bootstrap

> The dashboard answers *what is happening*, while detail pages explain *why*.

---

### Visualization Capabilities

Each visualization is available both on the dashboard and as an individual analytical page.

- **Bar Chart** → Aggregated numeric values (mean)  
- **Line Chart** → Trend visualization across categories  
- **Pie Chart** → Distribution and frequency analysis  
- **Scatter Plot** → Correlation between numeric variables  
- **Heatmap** → Correlation matrix of numeric columns  

All charts:

- Rendered **server‑side**
- Saved as **PNG files**
- Cache‑busted automatically for freshness

---

### Data Table View

- Clean, professional table design
- Styled with:
  - Blue header
  - Blue cell borders
  - Hover highlighting
- Pagination enabled (15 rows per page)
- Usable even with large datasets

---

### Navigation & UI

- Vertical sidebar navigation:
  - Dashboard
  - Bar Chart
  - Line Chart
  - Scatter Plot
  - Heatmap
  - Pie Chart
  - Data Table
- Sidebar toggle button (collapse/expand)
- Active navigation highlighting
- Unified brand color system
- Clean typography and spacing

---

## Project Structure

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
    ├── dashboard_bar.png
    ├── dashboard_line.png
    ├── dashboard_pie.png
    ├── dashboard_scatter.png
    ├── dashboard_heatmap.png
    ├── bar.png
    ├── line.png
    ├── scatter.png
    ├── heatmap.png
    └── pie.png

---

## Architecture & Design

DVMS follows a clear **separation of concerns**:

- **Flask (Routing & Controllers)** → Handles HTTP requests and rendering  
- **DataService (Data Layer)** → Loads Excel/CSV into Pandas DataFrames  
- **Pandas (Processing)** → Aggregation, grouping, filtering, pagination  
- **Matplotlib / Seaborn (Visualization)** → Chart generation  
- **Jinja2 Templates (UI Layer)** → Consistent, reusable frontend layout  

### Flow Diagram

Browser  
↓ HTTP Request  
Flask (app.py)  
↓  
DataService → Pandas DataFrame  
↓  
Matplotlib / Seaborn  
↓  
Static PNG Images  
↓  
Jinja2 Templates  
↓  
Rendered Analytics Dashboard  

---

## Design Principles

- **Dashboard ≠ Detailed Analysis** → Overview first, details on demand  
- **Reduced Visual Noise** → Fewer labels, consistent colors, clean axes  
- **Semantic Visualization** → Explicit X and Y axis labels  
- **Consistency & Reusability** → Shared base template across all pages  
- **Scalability Ready** → Extendable to:
  - Databases
  - REST APIs
  - Interactive chart libraries (Chart.js / Plotly)
  - Drill‑down analytics

---

## Running the Project

Install dependencies:
pip install -r requirements.txt

Run the application:
python app.py

Open in browser:
http://127.0.0.1:5000/
