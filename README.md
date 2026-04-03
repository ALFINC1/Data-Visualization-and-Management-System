# DNA_DVMS (DNA Data Visualization and Management System)

A simple data visualization and management system built with Flask.  
The system reads data from Excel or CSV files, displays the dataset in a clean table view,  
and generates both bar charts and pie charts based on the structure of the data.

## Features

- Load data from Excel (.xlsx/.xls) or CSV (.csv)
- Automatically detect numeric and non‑numeric columns
- Present data in a Bootstrap-styled table
- Create bar charts:
  - If numeric data exists → aggregate (mean / sum / count)
  - If no numeric column → frequency chart
- Create pie charts (numeric or frequency)
- Always show the newest chart using cache‑busted images
- No fixed schema required (data‑agnostic)

## Supported Data Formats

- `.xlsx` (Excel)
- `.xls` (Legacy Excel)
- `.csv` (Comma‑Separated Values)

To use CSV directly:

```
EXCEL_PATH=data.csv
```

or simply place a file named:

```

data.csv
```

in the project folder.

### Running the Project

First install dependencies:

```bash
pip install -r requirements.txt
```

Then start the application using:

```bash
python app.py
```

> This runs the Flask app directly using the code inside `app.py`  
> and does not require setting any environment variables.

You can also start Flask using the CLI:

```bash
flask run
```

Then open:

```

http://127.0.0.1:5000
```

## Project (Directory) Structure

```

DNA-DVMS/
│  app.py
│  data.xlsx / data.csv
│  README.md
│  .gitignore
│  requirements.txt
│
├── service/
│     ├── __init__.py
│     └── service.py
│
├── templates/
│     ├── base.html
│     ├── chart.html
│     ├── data.html
│     ├── index.html
│     └── pie.html
│
└── static/
      ├── chart.png
      └── pie.png
```

## Architecture Diagram

```           ┌───────────────────────┐
              │      User Browser     │
              └───────────▲───────────┘
                          │
                          │ HTTP Request
                          │
                 ┌────────┴─────────┐
                 │  Flask (app.py)  │
                 └────────▲─────────┘
                          │
                          │ Calls service layer
                          │
               ┌──────────┴──────────┐
               │ DataService (service.py)
               │ Loads Excel/CSV → DataFrame
               └──────────▲──────────┘
                          │
                          │
               ┌──────────┴──────────┐
               │       Pandas        | 
               │   DataFrame object  | 
               └──────────▲──────────┘
                          │
                          │ Chart data
                          │
                          |
             ┌────────────┴────────────┐
             │      Matplotlib         |
             │ Generates bar/pie charts|
             └────────────▲────────────┘
                          │
                          │ Saved to static/
                          │
              ┌───────────┴────────────┐
              │ Static Files (PNG)     |
              └───────────▲────────────┘
                          │
                          │ Referenced in templates
                          │
             ┌────────────┴────────────┐
             │     Jinja2 Templates    │
             │   HTML for UI rendering │
             └────────────▲────────────┘
                          │
                          │ Rendered page
                          │
                   ┌──────┴───────┐
                   │   Browser    │
                   └──────────────┘
```

## Design Notes

- **Separation of concerns**  
  Routes, data logic, and templates are separated for clarity.

- **Data‑agnostic**  
  The system works with most Excel/CSV formats without requiring a specific schema.

- **Graceful fallback**  
  If numeric columns don’t exist, the system automatically uses frequency-based charts.

- **Easy to extend**
  The data layer is isolated so it can switch to a database or API later.
