import os
import logging
from flask import Flask, render_template, request, redirect, url_for, session
import psycopg2
from psycopg2 import OperationalError, DatabaseError

# --- Configuration ---
DB_HOST = os.getenv("LABDB_HOST", "localhost")
DB_NAME = os.getenv("LABDB_NAME", "labdb")
DB_USER = os.getenv("LABDB_USER", "app_user_vul")#sec_app_user
DB_PASS = os.getenv("LABDB_PASS", "app_user_pass")#123
FLASK_SECRET = os.getenv("FLASK_SECRET", "testing_secret_for_local_only")

app = Flask(__name__)
app.secret_key = FLASK_SECRET
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")

logging.basicConfig(level=logging.DEBUG, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

def get_db_connection():
    try:
        return psycopg2.connect(host=DB_HOST, database=DB_NAME, user=DB_USER, password=DB_PASS)
    except OperationalError:
        logger.exception("Failed to open DB connection")
        raise

def user_login(name: str, password: str, cursor):
    query = f"SELECT * FROM users WHERE username = '{name}' AND password_hash = '{password}'"
    logger.debug("Executing user lookup (vulnerable) for username=%s", name)
    cursor.execute(query)
    return cursor.fetchone()

# ' or SELECT CASE WHEN EXISTS (SELECT 1 FROM users WHERE username = 'admin') THEN pg_sleep(5) END;--
#' OR (SELECT CASE WHEN EXISTS (SELECT 1 FROM users WHERE username='admin') THEN pg_sleep(5) END)=0--

def get_user_joined_info_vulnerable(username: str, cursor):
    sql = (
        "SELECT * FROM users us "
        "JOIN comments c on us.id = c.user_id "
        "JOIN orders o on c.id = o.user_id "
        "JOIN profiles p on us.id = p.user_id "
        f"WHERE us.username = '{username}'"
    )
    logger.debug("Executing joined user info (vulnerable) for username=%s", username)
    cursor.execute(sql)
    return cursor.fetchall()

# Index mapping constants (adjust if your schema has different column counts/order)
# Based on example CSV you provided:
U_ID = 0
U_USERNAME = 1
U_PASSWORD = 2
U_EMAIL = 3
U_ROLE = 4
U_CREATED = 5

# After users' 6 fields, comments start at index 6:
C_ID = 6
C_USER_ID = 7
C_CONTENT = 8
C_CREATED = 9

# After comments (4 fields), orders start at index 10:
O_ID = 10
O_USER_REF = 11
O_SOMETHING = 12
O_QUANTITY = 13
O_PRICE = 14
O_CREATED = 15

# After orders (6 fields), profiles start at index 16:
P_ID = 16
P_FULL_NAME = 17
P_BIO = 18
P_CITY = 19
P_PHONE = 20

@app.route("/", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")

        logger.info("Login attempt for username=%s", username)
        conn = None
        try:
            conn = get_db_connection()
            with conn:
                with conn.cursor() as cur:
                    try:
                        user = user_login(username, password, cur)
                    except DatabaseError:
                        logger.exception("DB error during login")
                        user = None
                        error = "An internal error occurred."
            if user:
                # minimal defensive extraction
                if isinstance(user, (list, tuple)):
                    user_id = user[0] if len(user) > 0 else None
                    user_name = user[1] if len(user) > 1 else username
                else:
                    user_id = getattr(user, "id", None)
                    user_name = getattr(user, "username", username)
                session.clear()
                session["user_id"] = user_id
                session["username"] = user_name
                return redirect(url_for("dashboard"))
            else:
                error = error or "Invalid username or password"
        except Exception:
            logger.exception("Unexpected error during login")
            error = "An unexpected error occurred."
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    logger.warning("Failed to close DB connection", exc_info=True)
    return render_template("index.html", error=error)

@app.route("/dashboard", methods=["GET", "POST"])
def dashboard():
    if "username" not in session:
        return redirect(url_for("login"))

    q = request.args.get("q", "").strip()
    profile = None
    comments = []
    orders = []

    conn = None
    try:
        conn = get_db_connection()
        with conn:
            with conn.cursor() as cur:
                try:
                    rows = get_user_joined_info_vulnerable(session["username"], cur)
                except DatabaseError:
                    logger.exception("DB error while fetching joined info")
                    rows = []

                # Parse joined rows into structured data
                for row in rows:
                    # defensive length checks to avoid IndexError
                    if len(row) < 21:
                        logger.warning("Joined row length unexpected: %s", len(row))
                        continue

                    # Build profile dict (they are the same across rows; keep first)
                    if profile is None:
                        profile = {
                            "id": row[P_ID],
                            "full_name": row[P_FULL_NAME],
                            "bio": row[P_BIO],
                            "city": row[P_CITY],
                            "phone": row[P_PHONE],
                            "email": row[U_EMAIL],
                            "username": row[U_USERNAME],
                        }

                    # Append comment
                    comments.append({
                        "id": row[C_ID],
                        "user_id": row[C_USER_ID],
                        "content": row[C_CONTENT],
                        "created_at": row[C_CREATED],
                    })

                    # Append order
                    orders.append({
                        "id": row[O_ID],
                        "ref": row[O_USER_REF],
                        "something": row[O_SOMETHING],
                        "quantity": row[O_QUANTITY],
                        "price": row[O_PRICE],
                        "created_at": row[O_CREATED],
                    })

                # If a search query was provided, perform vulnerable search (reuse existing vulnerable function)
                if q:
                    try:
                        sql = f"SELECT id, username, email FROM users WHERE username = '{q}' OR email '{q}' LIMIT 20"
                        logger.debug("Executing search (vulnerable) q=%s", q)
                        cur.execute(sql)
                        search_results = cur.fetchall()
                    except DatabaseError:
                        logger.exception("DB error during search")
                        search_results = []
                else:
                    search_results = []

    except Exception:
        logger.exception("Unexpected DB error on dashboard")
        search_results = []
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                logger.warning("Failed to close DB connection", exc_info=True)

    return render_template(
        "dashboard.html",
        user=(session.get("user_id"), session.get("username")),
        profile=profile,
        comments=comments,
        orders=orders,
        q=q,
        results=search_results
    )

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

if __name__ == '__main__':
    app.run(debug=True, host="127.0.0.1", port=5000)


