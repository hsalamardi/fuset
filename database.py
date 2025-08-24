# database.py
import sqlite3
import pandas as pd
from contextlib import closing
from datetime import datetime
import config # Import config to use DB_PATH and USERS

def init_db():
    """Initializes the database and creates tables if they don't exist."""
    with closing(sqlite3.connect(config.DB_PATH)) as conn:
        c = conn.cursor()
        c.execute("CREATE TABLE IF NOT EXISTS users (username TEXT PRIMARY KEY, role TEXT NOT NULL)")
        c.execute("""
        CREATE TABLE IF NOT EXISTS facilities (
            id INTEGER PRIMARY KEY AUTOINCREMENT, type TEXT NOT NULL, description TEXT NOT NULL,
            governorate TEXT, district TEXT, city_or_village TEXT,
            lat REAL, lon REAL, external_image BLOB, vision_labels TEXT
        )""")
        c.execute("""
        CREATE TABLE IF NOT EXISTS work_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT, serial TEXT UNIQUE NOT NULL, technician TEXT NOT NULL,
            facility_id INTEGER, maintenance_type TEXT NOT NULL, before_image BLOB, after_image BLOB,
            status TEXT NOT NULL, created_at TEXT NOT NULL, last_saved_at TEXT NOT NULL, editable_until TEXT NOT NULL,
            FOREIGN KEY(facility_id) REFERENCES facilities(id)
        )""")
        c.execute("""
        CREATE TABLE IF NOT EXISTS edit_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT, work_order_id INTEGER NOT NULL, field_name TEXT NOT NULL,
            proposed_value TEXT, reason TEXT, status TEXT NOT NULL, created_at TEXT NOT NULL,
            reviewed_by TEXT, reviewed_at TEXT, FOREIGN KEY(work_order_id) REFERENCES work_orders(id)
        )""")
        for uname, meta in config.USERS.items():
            c.execute("INSERT OR IGNORE INTO users(username, role) VALUES(?,?)", (uname, meta["role"]))
        conn.commit()

def now_iso():
    """Returns the current UTC time in ISO format."""
    return datetime.utcnow().isoformat()

def normalize_blob(blob):
    """Converts various database BLOB types to standard bytes."""
    if blob is None: return None
    if isinstance(blob, (bytes, bytearray)): return bytes(blob)
    try: return bytes(blob)
    except Exception: return blob.tobytes() if hasattr(blob, "tobytes") else None

def store_facility(conn, data):
    """Stores facility data and returns the new facility ID."""
    c = conn.cursor()
    c.execute("""INSERT INTO facilities(type, description, governorate, district, city_or_village, lat, lon, external_image, vision_labels)
                 VALUES(?,?,?,?,?,?,?,?,?)""",
              (data["type"], data["description"], data["governorate"], data["district"], data["city"],
               data["lat"], data["lon"], data["external_image"], data["vision_labels"]))
    conn.commit()
    return c.lastrowid

def store_work_order(conn, data):
    """Stores work order data and returns the new work order ID."""
    c = conn.cursor()
    c.execute("""INSERT INTO work_orders(serial, technician, facility_id, maintenance_type, before_image, after_image, status, created_at, last_saved_at, editable_until)
                 VALUES(?,?,?,?,?,?,?,?,?,?)""",
              (data["serial"], data["technician"], data["facility_id"], data["maintenance_type"],
               data["before_image"], data["after_image"], data["status"], data["created_at"],
               data["last_saved_at"], data["editable_until"]))
    conn.commit()
    return c.lastrowid

def update_work_order(conn, wo_id, updates: dict):
    """Updates a work order with the given dictionary of changes."""
    sets = [f"{k}=?" for k in updates.keys()]
    vals = list(updates.values()) + [wo_id]
    with conn:
        conn.execute(f"UPDATE work_orders SET {', '.join(sets)} WHERE id=?", vals)

def fetch_work_orders(conn, role, username):
    """Fetches work orders for a given role and username, returns a DataFrame."""
    if role == "technician":
        query = "SELECT w.id, w.serial, w.technician, w.maintenance_type, w.status, w.created_at, w.last_saved_at, w.editable_until, f.type, f.description, f.governorate, f.district, f.city_or_village, f.lat, f.lon FROM work_orders w LEFT JOIN facilities f ON w.facility_id=f.id WHERE w.technician=? ORDER BY w.created_at DESC"
        params = (username,)
    else: # Admin or other roles
        query = "SELECT w.id, w.serial, w.technician, w.maintenance_type, w.status, w.created_at, w.last_saved_at, w.editable_until, f.type, f.description, f.governorate, f.district, f.city_or_village, f.lat, f.lon FROM work_orders w LEFT JOIN facilities f ON w.facility_id=f.id ORDER BY w.created_at DESC"
        params = ()

    rows = conn.execute(query, params).fetchall()
    cols = ["id","serial","technician","maintenance_type","status","created_at","last_saved_at","editable_until",
            "facility_type","facility_desc","governorate","district","city_or_village","lat","lon"]
    return pd.DataFrame(rows, columns=cols)

def create_edit_request(conn, work_order_id, field_name, proposed_value, reason):
    """Creates a new edit request for a work order."""
    with conn:
        conn.execute("INSERT INTO edit_requests(work_order_id, field_name, proposed_value, reason, status, created_at) VALUES(?,?,?,?,?,?)",
                     (work_order_id, field_name, proposed_value, reason, "Pending", now_iso()))

def fetch_edit_requests(conn, status_filter="Pending"):
    """Fetches edit requests, returns a DataFrame."""
    q = "SELECT e.id AS request_id, e.work_order_id, e.field_name, e.proposed_value, e.reason, e.status AS request_status, e.created_at AS request_created_at, e.reviewed_by, e.reviewed_at, w.serial, w.technician, w.status AS work_order_status FROM edit_requests e JOIN work_orders w ON e.work_order_id = w.id"
    params = ()
    if status_filter:
        q += " WHERE e.status=?"
        params = (status_filter,)
    return pd.read_sql_query(q, conn, params=params)

def approve_edit_request(conn, req_id, admin_username):
    """Approves an edit request and applies the change."""
    with conn:
        r = conn.execute("SELECT work_order_id, field_name, proposed_value FROM edit_requests WHERE id=?", (req_id,)).fetchone()
        if not r: return "Request not found."
        work_order_id, field_name, proposed_value = r
        if field_name in {"maintenance_type", "status"}:
            conn.execute(f"UPDATE work_orders SET {field_name}=? WHERE id=?", (proposed_value, work_order_id))
        conn.execute("UPDATE edit_requests SET status='Approved', reviewed_by=?, reviewed_at=? WHERE id=?",
                     (admin_username, now_iso(), req_id))
    return "Approved."

def reject_edit_request(conn, req_id, admin_username):
    """Rejects an edit request."""
    with conn:
        conn.execute("UPDATE edit_requests SET status='Rejected', reviewed_by=?, reviewed_at=? WHERE id=?",
                     (admin_username, now_iso(), req_id))
    return "Rejected."