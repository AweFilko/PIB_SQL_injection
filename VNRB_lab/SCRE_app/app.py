import os
import re
import logging
from flask import Flask, render_template, request, redirect, url_for, session
import psycopg2
from psycopg2 import OperationalError, DatabaseError

DB_HOST = os.getenv("LABDB_HOST", "localhost")
DB_NAME = os.getenv("LABDB_NAME", "labdb")
DB_USER = os.getenv("LABDB_USER", "app_user_vul")
DB_PASS = os.getenv("LABDB_PASS", "app_user_pass")
FLASK_SECRET = os.getenv("FLASK_SECRET", "testing_secret_for_local_only")

# Users (0–5)
U_ID = 0
U_USERNAME = 1
U_PASSWORD_HASH = 2
U_EMAIL = 3
U_ROLE = 4
U_CREATED = 5

# Comments (6–9)
C_ID = 6
C_USER_ID = 7
C_CONTENT = 8
C_CREATED = 9

# Orders (10–15)
O_ID = 10
O_USER_ID = 11
O_PRODUCT_ID = 12
O_QUANTITY = 13
O_TOTAL = 14
O_ORDERED_AT = 15

# Profiles (16–20)
P_USER_ID = 16
P_FULL_NAME = 17
P_BIO = 18
P_CITY = 19
P_PHONE = 20


app = Flask(__name__)
app.secret_key = FLASK_SECRET
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=True
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

def get_db_connection():
    try:
        return psycopg2.connect(host=DB_HOST, database=DB_NAME, user=DB_USER, password=DB_PASS)
    except OperationalError:
        logger.exception("Failed to open DB connection")
        raise

USERNAME_RE = re.compile(r'^[A-Za-z0-9_.-]{1,64}$')
SEARCH_RE = re.compile(r'^[A-Za-z0-9@._+\- ]{0,128}$')

def validate_username(u: str) -> bool:
    return bool(u and USERNAME_RE.fullmatch(u))

def validate_search_q(q: str) -> bool:
    return q == "" or bool(SEARCH_RE.fullmatch(q)) and len(q) <= 128

def user_login_secure(name: str, password: str, cursor):
    sql = "SELECT id, username, password_hash, email FROM users WHERE username = %s LIMIT 1"
    logger.debug("Executing secure user lookup for username=%s", name)
    try:
        cursor.execute(sql, (name,))
        row = cursor.fetchone()
    except Exception:
        logger.exception("DB error in secure login")
        return None
    if not row:
        return None
    user_id, username, stored_password, email = row
    if stored_password == password:
        return {"id": user_id, "username": username, "email": email}
    return None
1

def get_user_joined_info_secure(username: str, cursor):
    sql = """
        SELECT
            us.id, us.username, us.password_hash, us.email, us.role, us.created_at,
            c.id, c.user_id, c.content, c.created_at,
            o.id, o.user_id, o.product_id, o.quantity, o.total, o.ordered_at,
            p.user_id, p.full_name, p.bio, p.city, p.phone
        FROM users us
        JOIN comments c ON us.id = c.user_id
        JOIN orders o   ON us.id = o.user_id
        JOIN profiles p ON us.id = p.user_id
        WHERE us.username = %s
    """
    cursor.execute(sql, (username,))
    return cursor.fetchall()

def search_users_secure(q: str, cursor, limit: int = 20):
    """
    Secure search using parameterized LIKE.
    We only expose id, username, email.
    """
    pattern = f"%{q}%"
    sql = "SELECT id, username, email FROM users WHERE username ILIKE %s OR email ILIKE %s LIMIT %s"
    logger.debug("Executing secure search q=%s", q)
    cursor.execute(sql, (pattern, pattern, limit))
    return cursor.fetchall()

@app.route("/", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        if not validate_username(username):
            logger.info("Invalid username format attempted: %s", username)
            error = "Invalid username or password."
            return render_template("index.html", error=error)

        conn = None
        try:
            conn = get_db_connection()
            with conn:
                with conn.cursor() as cur:
                    try:
                        user = user_login_secure(username, password, cur)
                    except DatabaseError:
                        # do NOT return DB error details to the user
                        logger.exception("DB error during login for user=%s", username)
                        user = None
                        error = "An internal error occurred."
            if user:
                session.clear()
                session["user_id"] = user["id"]
                session["username"] = user["username"]
                return redirect(url_for("dashboard"))
            else:
                # generic message (do not disclose whether username exists)
                error = error or "Invalid username or password."
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

@app.route("/dashboard", methods=["GET"])
def dashboard():
    if "username" not in session:
        return redirect(url_for("login"))

    q = request.args.get("q", "").strip()

    SEARCH_RE = re.compile(r'^[A-Za-z0-9@._+\- ]{0,128}$')
    if not SEARCH_RE.fullmatch(q):
        logger.info("Invalid search query from user=%s: %s", session.get("username"), q)
        q = ""  # sanitize

    profile = None
    comments = {}
    orders = {}
    search_results = []

    conn = None
    try:
        conn = get_db_connection()
        with conn:
            with conn.cursor() as cur:
                # --- Fetch joined user info securely ---
                try:
                    rows = get_user_joined_info_secure(session["username"], cur)
                except Exception:
                    logger.exception("DB error fetching joined info for user=%s", session.get("username"))
                    rows = []

                for row in rows:
                    if profile is None:
                        profile = {
                            "id": row[U_ID],
                            "username": row[U_USERNAME],
                            "email": row[U_EMAIL],
                            "role": row[U_ROLE],
                            "created_at": row[U_CREATED],
                            "full_name": row[P_FULL_NAME],
                            "bio": row[P_BIO],
                            "city": row[P_CITY],
                            "phone": row[P_PHONE],
                        }

                    comment_id = row[C_ID]
                    if comment_id is not None and comment_id not in comments:
                        comments[comment_id] = {
                            "id": comment_id,
                            "user_id": row[C_USER_ID],
                            "content": row[C_CONTENT],
                            "created_at": row[C_CREATED],
                        }

                    order_id = row[O_ID]
                    if order_id is not None and order_id not in orders:
                        orders[order_id] = {
                            "id": order_id,
                            "user_id": row[O_USER_ID],
                            "product_id": row[O_PRODUCT_ID],
                            "quantity": row[O_QUANTITY],
                            "total": row[O_TOTAL],
                            "created_at": row[O_ORDERED_AT],
                        }

                if q:
                    try:
                        pattern = f"%{q}%"
                        sql_search = """
                            SELECT id, username, email
                            FROM users
                            WHERE username ILIKE %s OR email ILIKE %s
                            LIMIT 20
                        """
                        cur.execute(sql_search, (pattern, pattern))
                        search_results = cur.fetchall()
                    except Exception:
                        logger.exception("DB error during search for user=%s", session.get("username"))
                        search_results = []

    except Exception:
        logger.exception("Unexpected DB error on dashboard for user=%s", session.get("username"))
        search_results = []
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                logger.warning("Failed to close DB connection", exc_info=True)

    comments_list = list(comments.values())
    orders_list = list(orders.values())

    return render_template(
        "dashboard.html",
        user=(session.get("user_id"), session.get("username")),
        profile=profile,
        comments=comments_list,
        orders=orders_list,
        q=q,
        results=search_results
    )

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.errorhandler(500)
def internal_error(e):
    logger.exception("Internal server error")
    return render_template("error.html", message="An internal error occurred."), 500

if __name__ == '__main__':
    app.run(debug=False, host="127.0.0.1", port=5000)
