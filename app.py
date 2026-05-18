from flask import Flask, render_template, request, redirect, url_for, abort, g
import sqlite3
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent / "helpdesk.db"

app = Flask(__name__)
app.config["SECRET_KEY"] = "dev-change-me-before-prod"

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
            author = (request.form.get("author") or "").strip() or "staff"
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


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=5000, debug=False)
