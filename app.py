from flask import Flask, render_template, request, redirect, url_for, abort, g, session, flash
import sqlite3
import os
import sys
import secrets
import getpass
from functools import wraps
from datetime import datetime
from pathlib import Path

import bcrypt

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "helpdesk.db"
SECRET_KEY_FILE = BASE_DIR / ".flask_secret"


def _load_secret_key():
    """Load Flask SECRET_KEY from env, or from a local file (generated on first run)."""
    env_key = os.environ.get("HELPDESK_SECRET_KEY")
    if env_key:
        return env_key
    if SECRET_KEY_FILE.exists():
        return SECRET_KEY_FILE.read_text(encoding="utf-8").strip()
    key = secrets.token_hex(32)
    SECRET_KEY_FILE.write_text(key, encoding="utf-8")
    return key


app = Flask(__name__)
app.config["SECRET_KEY"] = _load_secret_key()
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

STATUSES = ["open", "in_progress", "waiting", "resolved", "closed"]
PRIORITIES = ["urgent", "high", "normal", "low"]
PRIORITY_ORDER = {"urgent": 0, "high": 1, "normal": 2, "low": 3}


def get_db():
    if "db" not in g:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        g.db = conn
    return g.db


@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE COLLATE NOCASE,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            submitter_name TEXT NOT NULL,
            submitter_email TEXT NOT NULL,
            subject TEXT NOT NULL,
            description TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open',
            priority TEXT NOT NULL DEFAULT 'normal',
            assigned_to TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_id INTEGER NOT NULL,
            author TEXT NOT NULL,
            body TEXT NOT NULL,
            internal INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            FOREIGN KEY (ticket_id) REFERENCES tickets(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(status);
        CREATE INDEX IF NOT EXISTS idx_comments_ticket ON comments(ticket_id);
        """
    )
    conn.commit()
    conn.close()


def now_iso():
    return datetime.utcnow().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        return False


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.get("current_user") is None:
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


@app.before_request
def load_current_user():
    g.current_user = None
    user_id = session.get("user_id")
    if user_id is None:
        return
    row = get_db().execute(
        "SELECT id, username FROM users WHERE id = ?", (user_id,)
    ).fetchone()
    if row is None:
        # stale session (user was deleted)
        session.clear()
        return
    g.current_user = row


@app.route("/login", methods=["GET", "POST"])
def login():
    if g.get("current_user") is not None:
        return redirect(url_for("staff_dashboard"))

    error = None
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        row = get_db().execute(
            "SELECT id, username, password_hash FROM users WHERE username = ?",
            (username,),
        ).fetchone()
        if row is not None and verify_password(password, row["password_hash"]):
            session.clear()
            session["user_id"] = row["id"]
            session.permanent = False
            next_url = request.args.get("next") or request.form.get("next") or url_for("staff_dashboard")
            # only allow relative redirects
            if not next_url.startswith("/"):
                next_url = url_for("staff_dashboard")
            return redirect(next_url)
        error = "Invalid username or password."
        return render_template("login.html", error=error, username=username), 401

    return render_template("login.html", error=None, username="")


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------------------------------------------------------------------------
# Public + staff routes
# ---------------------------------------------------------------------------


@app.route("/", methods=["GET", "POST"])
def submit():
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        email = (request.form.get("email") or "").strip()
        subject = (request.form.get("subject") or "").strip()
        description = (request.form.get("description") or "").strip()
        priority = (request.form.get("priority") or "normal").strip()

        errors = []
        if not name:
            errors.append("Name is required.")
        if not email or "@" not in email:
            errors.append("A valid email is required.")
        if not subject:
            errors.append("Subject is required.")
        if not description:
            errors.append("Description is required.")
        if priority not in PRIORITIES:
            errors.append("Invalid priority.")

        if errors:
            return (
                render_template(
                    "submit.html",
                    errors=errors,
                    form=request.form,
                    priorities=PRIORITIES,
                ),
                400,
            )

        db = get_db()
        now = now_iso()
        cur = db.execute(
            """
            INSERT INTO tickets (submitter_name, submitter_email, subject, description,
                                 status, priority, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'open', ?, ?, ?)
            """,
            (name, email, subject, description, priority, now, now),
        )
        db.commit()
        return render_template("submitted.html", ticket_id=cur.lastrowid)

    return render_template(
        "submit.html", errors=None, form={}, priorities=PRIORITIES
    )


@app.route("/staff")
@login_required
def staff_dashboard():
    status_filter = request.args.get("status", "active")
    db = get_db()
    if status_filter == "all":
        rows = db.execute("SELECT * FROM tickets").fetchall()
    elif status_filter == "active":
        rows = db.execute(
            "SELECT * FROM tickets WHERE status NOT IN ('resolved', 'closed')"
        ).fetchall()
    elif status_filter in STATUSES:
        rows = db.execute(
            "SELECT * FROM tickets WHERE status = ?", (status_filter,)
        ).fetchall()
    else:
        rows = []

    tickets = sorted(
        rows,
        key=lambda r: (
            PRIORITY_ORDER.get(r["priority"], 99),
            -datetime.fromisoformat(r["updated_at"]).timestamp(),
        ),
    )
    return render_template(
        "staff.html",
        tickets=tickets,
        status_filter=status_filter,
        statuses=STATUSES,
    )


@app.route("/staff/ticket/<int:ticket_id>", methods=["GET", "POST"])
@login_required
def staff_ticket(ticket_id):
    db = get_db()
    ticket = db.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
    if ticket is None:
        abort(404)

    if request.method == "POST":
        action = request.form.get("action")
        if action == "update":
            status = request.form.get("status", ticket["status"])
            priority = request.form.get("priority", ticket["priority"])
            assigned_to = (request.form.get("assigned_to") or "").strip() or None
            if status not in STATUSES or priority not in PRIORITIES:
                abort(400)
            db.execute(
                """
                UPDATE tickets SET status=?, priority=?, assigned_to=?, updated_at=?
                WHERE id=?
                """,
                (status, priority, assigned_to, now_iso(), ticket_id),
            )
            db.commit()
        elif action == "comment":
            # Comment author is always the logged-in staff user (no spoofing via form).
            author = g.current_user["username"]
            body = (request.form.get("body") or "").strip()
            internal = 1 if request.form.get("internal") == "1" else 0
            if not body:
                abort(400)
            now = now_iso()
            db.execute(
                """
                INSERT INTO comments (ticket_id, author, body, internal, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (ticket_id, author, body, internal, now),
            )
            db.execute(
                "UPDATE tickets SET updated_at=? WHERE id=?", (now, ticket_id)
            )
            db.commit()
        else:
            abort(400)
        return redirect(url_for("staff_ticket", ticket_id=ticket_id))

    comments = db.execute(
        "SELECT * FROM comments WHERE ticket_id = ? ORDER BY created_at ASC",
        (ticket_id,),
    ).fetchall()
    return render_template(
        "ticket.html",
        ticket=ticket,
        comments=comments,
        statuses=STATUSES,
        priorities=PRIORITIES,
    )


@app.errorhandler(404)
def not_found(e):
    return render_template("error.html", code=404, message="Not found."), 404


@app.errorhandler(400)
def bad_request(e):
    return render_template("error.html", code=400, message="Bad request."), 400


# ---------------------------------------------------------------------------
# CLI commands (user management)
# ---------------------------------------------------------------------------


def cli_create_user(argv):
    username = argv[0].strip() if argv else input("Username: ").strip()
    if not username:
        print("Username is required.", file=sys.stderr)
        return 2
    pw1 = getpass.getpass("Password: ")
    pw2 = getpass.getpass("Confirm password: ")
    if pw1 != pw2:
        print("Passwords do not match.", file=sys.stderr)
        return 2
    if len(pw1) < 8:
        print("Password must be at least 8 characters.", file=sys.stderr)
        return 2

    init_db()
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(
            "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
            (username, hash_password(pw1), now_iso()),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        print(f"User '{username}' already exists.", file=sys.stderr)
        return 1
    finally:
        conn.close()
    print(f"Created user '{username}'.")
    return 0


def cli_list_users(argv):
    init_db()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, username, created_at FROM users ORDER BY id"
    ).fetchall()
    conn.close()
    if not rows:
        print("(no users yet — run: python app.py create-user <name>)")
        return 0
    print(f"{'ID':>3}  {'USERNAME':<20}  CREATED")
    for r in rows:
        print(f"{r['id']:>3}  {r['username']:<20}  {r['created_at']}")
    return 0


def cli_delete_user(argv):
    if not argv:
        print("Usage: python app.py delete-user <username>", file=sys.stderr)
        return 2
    username = argv[0]
    init_db()
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute("DELETE FROM users WHERE username = ?", (username,))
    conn.commit()
    conn.close()
    if cur.rowcount == 0:
        print(f"No user named '{username}'.", file=sys.stderr)
        return 1
    print(f"Deleted user '{username}'.")
    return 0


def cli_help(argv):
    print(
        "Usage:\n"
        "  python app.py                    Start the web server\n"
        "  python app.py create-user [name] Create a new staff user (prompts for password)\n"
        "  python app.py list-users         List staff users\n"
        "  python app.py delete-user <name> Delete a staff user\n"
    )
    return 0


COMMANDS = {
    "create-user": cli_create_user,
    "list-users": cli_list_users,
    "delete-user": cli_delete_user,
    "help": cli_help,
    "--help": cli_help,
    "-h": cli_help,
}


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in COMMANDS:
        sys.exit(COMMANDS[sys.argv[1]](sys.argv[2:]))
    init_db()
    app.run(host="0.0.0.0", port=5000, debug=False)
