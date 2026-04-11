# settings.py

DEFAULT_APP_SETTINGS = {
    # Branding
    "app_name": "DVMS",
    "app_title": "Data Visualization & Management System",
    "about_text": "DVMS is a lightweight analytics dashboard for exploring and exporting datasets.",

    # Social links
    "developer_name": "Fadil Ahmed",
    "social_links": {
        "linkedin": "https://www.linkedin.com/in/fadilahmed123",
        "github": "https://github.com/ALFINC1",
        "telegram": "https://t.me/ALFINC1",
    },

    # Appearance defaults
    "default_theme": "light",
    "accent_color": "#3f6ad8",
    "font_family": "system",
    "custom_font_stack": "",
    "base_font_size_px": 14,
    "density": "comfortable",

    # Charts
    "plotly_template": "plotly",
    "chart_max_points": 5000,

    # Data / Table defaults
    "table_per_page": 15,
    "max_categories": 200,
    "max_dates": 200,

    # Security / System
    "allow_registration": True,
    "maintenance_mode": False,
    "session_timeout_minutes": 0,

    # Locale
    "timezone": "Africa/Addis_Ababa",
    "date_format": "YYYY-MM-DD",
    "language": "en",
}

DEFAULT_USER_SETTINGS = {
    "theme": "auto",
    "font_scale": 1.0,
    "density": "auto",
    "table_per_page": 0,
}