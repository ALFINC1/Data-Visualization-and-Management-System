import sqlite3
from werkzeug.security import generate_password_hash

DB = "storage/dvms.sqlite"

def create_user(username, password, role):
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    cur.execute("""
        INSERT OR REPLACE INTO users(username, password_hash, role)
        VALUES (?, ?, ?)
    """, (username, generate_password_hash(password), role))
    conn.commit()
    conn.close()

create_user("admin", "admin123", "admin")

create_user("user1", "user123", "user")

print("✅ Users created successfully")