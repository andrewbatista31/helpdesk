from flask import Flask, render_template, request, redirect, url_for, abort, g, session, flash
import sqlite3
import os
import sys
import secrets
import getpass
from functools import wraps
from datetime import datetime, date, timedelta
import calendar as cal_mod
import threading
import time
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
AGING_THRESHOLD_DAYS = 3


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

        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_date TEXT NOT NULL,    -- 'YYYY-MM-DD'
            event_time TEXT,             -- 'HH:MM' or NULL
            title TEXT NOT NULL,
            notes TEXT,
            created_by TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_events_date ON events(event_date);

        CREATE TABLE IF NOT EXISTS recurring_tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subject TEXT NOT NULL,
            description TEXT NOT NULL,
            priority TEXT NOT NULL DEFAULT 'normal',
            assigned_to TEXT,
            interval_value INTEGER NOT NULL,
            interval_unit TEXT NOT NULL CHECK(interval_unit IN ('day','week','month')),
            start_date TEXT NOT NULL,
            next_run_date TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1,
            created_by TEXT NOT NULL,
            created_at TEXT NOT NULL,
            last_generated_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_recurring_next ON recurring_tickets(next_run_date) WHERE active = 1;

        CREATE TABLE IF NOT EXISTS licenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            vendor TEXT,
            seats_total INTEGER NOT NULL DEFAULT 1,
            seats_used INTEGER NOT NULL DEFAULT 0,
            renewal_date TEXT,        -- 'YYYY-MM-DD' or NULL
            purchase_date TEXT,       -- 'YYYY-MM-DD' or NULL
            annual_cost REAL,         -- optional currency amount
            license_key TEXT,         -- sensitive; masked in list views
            notes TEXT,
            created_by TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_licenses_renewal ON licenses(renewal_date);
        """
    )

    # --- Lightweight migrations for existing DBs --------------------------
    # Each statement is wrapped so it's idempotent: it succeeds on the first
    # run and is a no-op afterwards. Using sqlite3.OperationalError catches
    # both "column already exists" and "no such column" cases.
    for stmt in (
        "ALTER TABLE licenses DROP COLUMN assigned_to",
        "ALTER TABLE tickets ADD COLUMN created_by_staff TEXT",
        "ALTER TABLE tickets ADD COLUMN resolved_at TEXT",
        "ALTER TABLE tickets ADD COLUMN resolved_by TEXT",
    ):
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError:
            pass
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


@app.context_processor
def inject_header_globals():
    """Make open_ticket_count available to base.html on every staff page."""
    if g.get("current_user") is None:
        return {}
    row = get_db().execute(
        "SELECT COUNT(*) AS n FROM tickets WHERE status NOT IN ('resolved', 'closed')"
    ).fetchone()
    return {"open_ticket_count": row["n"] if row else 0}


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
    # Lazy catch-up: generate any due recurring tickets before counting.
    # (The background thread does this on a 60-second tick too; this is the
    # belt-and-suspenders path for when the dashboard is hit cold.)
    try:
        generate_due_tickets()
    except Exception:
        app.logger.exception("recurring catch-up at dashboard load failed")

    db = get_db()
    me = g.current_user["username"]

    def scalar(sql, params=()):
        return db.execute(sql, params).fetchone()[0]

    open_total = scalar(
        "SELECT COUNT(*) FROM tickets WHERE status NOT IN ('resolved','closed')"
    )
    unassigned = scalar(
        "SELECT COUNT(*) FROM tickets "
        "WHERE status NOT IN ('resolved','closed') "
        "AND (assigned_to IS NULL OR assigned_to = '')"
    )
    mine_count = scalar(
        "SELECT COUNT(*) FROM tickets "
        "WHERE status NOT IN ('resolved','closed') "
        "AND LOWER(assigned_to) = LOWER(?)",
        (me,),
    )
    in_progress = scalar(
        "SELECT COUNT(*) FROM tickets WHERE status = 'in_progress'"
    )

    rows = db.execute(
        "SELECT priority, COUNT(*) AS n FROM tickets "
        "WHERE status NOT IN ('resolved','closed') GROUP BY priority"
    ).fetchall()
    priority_counts = {p: 0 for p in PRIORITIES}
    for r in rows:
        priority_counts[r["priority"]] = r["n"]

    aging = db.execute(
        """
        SELECT id, subject, priority, status, assigned_to, created_at,
               CAST(julianday('now') - julianday(created_at) AS INTEGER) AS days_open
        FROM tickets
        WHERE status NOT IN ('resolved','closed')
          AND julianday('now') - julianday(created_at) >= ?
        ORDER BY julianday('now') - julianday(created_at) DESC, id ASC
        """,
        (AGING_THRESHOLD_DAYS,),
    ).fetchall()

    my_rows = db.execute(
        "SELECT * FROM tickets "
        "WHERE status NOT IN ('resolved','closed') "
        "AND LOWER(assigned_to) = LOWER(?)",
        (me,),
    ).fetchall()
    my_tickets = sorted(
        my_rows,
        key=lambda r: (
            PRIORITY_ORDER.get(r["priority"], 99),
            -datetime.fromisoformat(r["updated_at"]).timestamp(),
        ),
    )

    ctx = dict(
        open_total=open_total,
        unassigned=unassigned,
        mine_count=mine_count,
        in_progress=in_progress,
        priority_counts=priority_counts,
        priorities=PRIORITIES,
        aging=aging,
        aging_threshold=AGING_THRESHOLD_DAYS,
        my_tickets=my_tickets,
    )
    ctx.update(_calendar_context(request.args.get("month")))
    return render_template("dashboard.html", **ctx)


@app.route("/staff/tickets")
@login_required
def staff_queue():
    status_filter = request.args.get("status", "active")
    filter_name = request.args.get("filter")  # None | 'unassigned' | 'mine'
    priority_filter = request.args.get("priority")
    me = g.current_user["username"]
    db = get_db()

    query = "SELECT * FROM tickets WHERE 1=1"
    params = []

    if status_filter == "active":
        query += " AND status NOT IN ('resolved','closed')"
    elif status_filter in STATUSES:
        query += " AND status = ?"
        params.append(status_filter)
    # status_filter == "all" adds no clause

    if filter_name == "unassigned":
        query += " AND (assigned_to IS NULL OR assigned_to = '')"
    elif filter_name == "mine":
        query += " AND LOWER(assigned_to) = LOWER(?)"
        params.append(me)

    if priority_filter in PRIORITIES:
        query += " AND priority = ?"
        params.append(priority_filter)

    rows = db.execute(query, params).fetchall()
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
        filter_name=filter_name,
        priority_filter=priority_filter,
        statuses=STATUSES,
        priorities=PRIORITIES,
    )


# ---------------------------------------------------------------------------
# Recurring tickets (schedule helpers + generator + background thread)
# ---------------------------------------------------------------------------


def _add_months(d, n):
    total = d.month - 1 + n
    year = d.year + total // 12
    month = total % 12 + 1
    day = min(d.day, cal_mod.monthrange(year, month)[1])
    return date(year, month, day)


def _advance_schedule(d, value, unit):
    if unit == "day":
        return d + timedelta(days=value)
    if unit == "week":
        return d + timedelta(weeks=value)
    if unit == "month":
        return _add_months(d, value)
    raise ValueError(f"unknown interval_unit: {unit!r}")


def _schedule_description(value, unit):
    if value == 1:
        return f"every {unit}"
    plural = {"day": "days", "week": "weeks", "month": "months"}
    return f"every {value} {plural.get(unit, unit + 's')}"


def _parse_recurring_form(form):
    subject = (form.get("subject") or "").strip()
    description = (form.get("description") or "").strip()
    priority = (form.get("priority") or "normal").strip()
    assigned_to = (form.get("assigned_to") or "").strip() or None
    interval_value_raw = (form.get("interval_value") or "").strip()
    interval_unit = (form.get("interval_unit") or "").strip()
    start_date = (form.get("start_date") or "").strip()
    active = 1 if form.get("active") == "1" else 0
    errors = []
    if not subject:
        errors.append("Subject is required.")
    if len(subject) > 200:
        errors.append("Subject must be 200 characters or fewer.")
    if not description:
        errors.append("Description is required.")
    if priority not in PRIORITIES:
        errors.append("Invalid priority.")
    try:
        interval_value = int(interval_value_raw)
        if interval_value < 1 or interval_value > 365:
            raise ValueError
    except ValueError:
        interval_value = 0
        errors.append("Interval must be a whole number between 1 and 365.")
    if interval_unit not in ("day", "week", "month"):
        errors.append("Interval unit must be day, week, or month.")
    try:
        date.fromisoformat(start_date)
    except ValueError:
        errors.append("Start date is required.")
    return (
        {
            "subject": subject,
            "description": description,
            "priority": priority,
            "assigned_to": assigned_to,
            "interval_value": interval_value,
            "interval_unit": interval_unit,
            "start_date": start_date,
            "active": active,
        },
        errors,
    )


def generate_due_tickets():
    """Create one catch-up ticket per active, due schedule and advance next_run_date.

    Catch-up policy: if a schedule's next_run_date is in the past (e.g., the
    server was offline), generate exactly ONE ticket and skip next_run_date
    forward to the next future date. This avoids ticket-storms after outages.
    """
    today = date.today()
    db = get_db()
    rows = db.execute(
        "SELECT * FROM recurring_tickets WHERE active = 1 AND next_run_date <= ?",
        (today.isoformat(),),
    ).fetchall()
    if not rows:
        return 0
    now = now_iso()
    generated = 0
    for r in rows:
        db.execute(
            """
            INSERT INTO tickets (submitter_name, submitter_email, subject, description,
                                 status, priority, assigned_to, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'open', ?, ?, ?, ?)
            """,
            (
                "[Recurring]",
                "noreply@helpdesk.local",
                r["subject"],
                r["description"],
                r["priority"],
                r["assigned_to"],
                now,
                now,
            ),
        )
        generated += 1
        next_run = date.fromisoformat(r["next_run_date"])
        while next_run <= today:
            next_run = _advance_schedule(
                next_run, r["interval_value"], r["interval_unit"]
            )
        db.execute(
            "UPDATE recurring_tickets SET next_run_date = ?, last_generated_at = ? "
            "WHERE id = ?",
            (next_run.isoformat(), now, r["id"]),
        )
    db.commit()
    return generated


def _projected_recurring_runs(first_day, last_day):
    """Return [{date, recurring_id, title}, ...] for each active schedule that
    will fire inside [first_day, last_day]. Only projects forward from
    next_run_date (past dates have already been materialized into tickets)."""
    out = []
    rows = get_db().execute(
        "SELECT id, subject, interval_value, interval_unit, next_run_date "
        "FROM recurring_tickets WHERE active = 1"
    ).fetchall()
    for r in rows:
        try:
            next_run = date.fromisoformat(r["next_run_date"])
        except ValueError:
            continue
        safety = 200  # guard against pathological intervals
        while next_run <= last_day and safety > 0:
            if next_run >= first_day:
                out.append(
                    {
                        "date": next_run.isoformat(),
                        "recurring_id": r["id"],
                        "title": r["subject"],
                    }
                )
            next_run = _advance_schedule(
                next_run, r["interval_value"], r["interval_unit"]
            )
            safety -= 1
    return out


# --- Background scheduler -------------------------------------------------

_scheduler_started = False
_scheduler_lock = threading.Lock()


def start_scheduler(interval_seconds=60):
    """Start a daemon thread that periodically generates due recurring tickets.

    Idempotent — safe to call multiple times. The thread exits when the
    process exits because it's a daemon.
    """
    global _scheduler_started
    with _scheduler_lock:
        if _scheduler_started:
            return
        _scheduler_started = True

    def loop():
        time.sleep(5)  # let app finish starting
        while True:
            try:
                with app.app_context():
                    generate_due_tickets()
            except Exception:
                app.logger.exception("recurring scheduler tick failed")
            time.sleep(interval_seconds)

    t = threading.Thread(target=loop, daemon=True, name="recurring-scheduler")
    t.start()


# ---------------------------------------------------------------------------
# Calendar + events
# ---------------------------------------------------------------------------


def _parse_month_arg(s):
    """Parse a ?month=YYYY-MM string; fall back to current month on bad input."""
    if s:
        try:
            y, m = s.split("-")
            y, m = int(y), int(m)
            if 1900 <= y <= 2100 and 1 <= m <= 12:
                return y, m
        except (ValueError, AttributeError):
            pass
    today = date.today()
    return today.year, today.month


def _shift_month(year, month, delta):
    idx = year * 12 + (month - 1) + delta
    return idx // 12, (idx % 12) + 1


def _month_str(year, month):
    return f"{year:04d}-{month:02d}"


def _parse_event_form(form):
    event_date = (form.get("event_date") or "").strip()
    event_time = (form.get("event_time") or "").strip()
    title = (form.get("title") or "").strip()
    notes = (form.get("notes") or "").strip() or None
    errors = []
    try:
        date.fromisoformat(event_date)
    except ValueError:
        errors.append("Date is required (YYYY-MM-DD).")
    if event_time:
        try:
            datetime.strptime(event_time, "%H:%M")
        except ValueError:
            errors.append("Time must be HH:MM (24-hour).")
    else:
        event_time = None
    if not title:
        errors.append("Title is required.")
    if len(title) > 200:
        errors.append("Title must be 200 characters or fewer.")
    return event_date, event_time, title, notes, errors


def _calendar_context(month_arg):
    """Build the variables the embedded month-grid template needs."""
    year, month = _parse_month_arg(month_arg)
    today = date.today()

    cal = cal_mod.Calendar(firstweekday=6)  # Sunday-first
    weeks = cal.monthdatescalendar(year, month)
    first_day = weeks[0][0]
    last_day = weeks[-1][-1]

    rows = get_db().execute(
        """
        SELECT * FROM events
        WHERE event_date BETWEEN ? AND ?
        ORDER BY event_date,
                 CASE WHEN event_time IS NULL THEN 1 ELSE 0 END,
                 event_time,
                 id
        """,
        (first_day.isoformat(), last_day.isoformat()),
    ).fetchall()

    events_by_date = {}
    for r in rows:
        events_by_date.setdefault(r["event_date"], []).append(r)

    recurring_by_date = {}
    for run in _projected_recurring_runs(first_day, last_day):
        recurring_by_date.setdefault(run["date"], []).append(run)

    prev_y, prev_m = _shift_month(year, month, -1)
    next_y, next_m = _shift_month(year, month, 1)

    return {
        "weeks": weeks,
        "cal_year": year,
        "cal_month": month,
        "cal_month_name": cal_mod.month_name[month],
        "events_by_date": events_by_date,
        "recurring_by_date": recurring_by_date,
        "today": today,
        "current_month": _month_str(year, month),
        "prev_month": _month_str(prev_y, prev_m),
        "next_month": _month_str(next_y, next_m),
        "day_labels": ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"],
    }


@app.route("/staff/calendar")
@login_required
def staff_calendar():
    """Legacy alias — calendar is now embedded in the /staff dashboard."""
    month = request.args.get("month", "")
    url = url_for("staff_dashboard")
    if month:
        url += f"?month={month}"
    return redirect(url + "#calendar")


@app.route("/staff/calendar/event/new", methods=["GET", "POST"])
@login_required
def staff_event_new():
    if request.method == "POST":
        event_date, event_time, title, notes, errors = _parse_event_form(request.form)
        if errors:
            back_month = _back_month_from(request.form.get("event_date"))
            return (
                render_template(
                    "event_form.html",
                    event=None,
                    form=request.form,
                    errors=errors,
                    back_month=back_month,
                ),
                400,
            )
        db = get_db()
        db.execute(
            """
            INSERT INTO events (event_date, event_time, title, notes, created_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                event_date,
                event_time,
                title,
                notes,
                g.current_user["username"],
                now_iso(),
            ),
        )
        db.commit()
        y, m, _ = event_date.split("-")
        return redirect(url_for("staff_dashboard", month=f"{y}-{m}") + "#calendar")

    prefill_date = request.args.get("date") or date.today().isoformat()
    try:
        date.fromisoformat(prefill_date)
    except ValueError:
        prefill_date = date.today().isoformat()
    form = {"event_date": prefill_date}
    back_month = _back_month_from(prefill_date)
    return render_template(
        "event_form.html",
        event=None,
        form=form,
        errors=[],
        back_month=back_month,
    )


@app.route("/staff/calendar/event/<int:event_id>", methods=["GET", "POST"])
@login_required
def staff_event_edit(event_id):
    db = get_db()
    event = db.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
    if event is None:
        abort(404)

    if request.method == "POST":
        event_date, event_time, title, notes, errors = _parse_event_form(request.form)
        if errors:
            back_month = _back_month_from(event["event_date"])
            return (
                render_template(
                    "event_form.html",
                    event=event,
                    form=request.form,
                    errors=errors,
                    back_month=back_month,
                ),
                400,
            )
        db.execute(
            """
            UPDATE events
            SET event_date = ?, event_time = ?, title = ?, notes = ?
            WHERE id = ?
            """,
            (event_date, event_time, title, notes, event_id),
        )
        db.commit()
        y, m, _ = event_date.split("-")
        return redirect(url_for("staff_dashboard", month=f"{y}-{m}") + "#calendar")

    form = {
        "event_date": event["event_date"],
        "event_time": event["event_time"] or "",
        "title": event["title"],
        "notes": event["notes"] or "",
    }
    back_month = _back_month_from(event["event_date"])
    return render_template(
        "event_form.html",
        event=event,
        form=form,
        errors=[],
        back_month=back_month,
    )


@app.route("/staff/calendar/event/<int:event_id>/delete", methods=["POST"])
@login_required
def staff_event_delete(event_id):
    db = get_db()
    event = db.execute(
        "SELECT event_date FROM events WHERE id = ?", (event_id,)
    ).fetchone()
    if event is None:
        abort(404)
    db.execute("DELETE FROM events WHERE id = ?", (event_id,))
    db.commit()
    y, m, _ = event["event_date"].split("-")
    return redirect(url_for("staff_dashboard", month=f"{y}-{m}") + "#calendar")


def _back_month_from(date_str):
    """Best-effort: derive a YYYY-MM string to send users back to."""
    try:
        d = date.fromisoformat(date_str)
        return _month_str(d.year, d.month)
    except (ValueError, TypeError, AttributeError):
        today = date.today()
        return _month_str(today.year, today.month)


# ---------------------------------------------------------------------------
# Recurring schedule management routes
# ---------------------------------------------------------------------------


@app.route("/staff/recurring")
@login_required
def staff_recurring_list():
    rows = get_db().execute(
        "SELECT * FROM recurring_tickets "
        "ORDER BY active DESC, next_run_date ASC, id ASC"
    ).fetchall()
    today = date.today()
    schedules = []
    for r in rows:
        d = dict(r)
        d["schedule_text"] = _schedule_description(
            r["interval_value"], r["interval_unit"]
        )
        try:
            next_run = date.fromisoformat(r["next_run_date"])
            d["days_until_next"] = (next_run - today).days
        except ValueError:
            d["days_until_next"] = None
        schedules.append(d)
    return render_template("recurring_list.html", schedules=schedules)


@app.route("/staff/recurring/new", methods=["GET", "POST"])
@login_required
def staff_recurring_new():
    if request.method == "POST":
        data, errors = _parse_recurring_form(request.form)
        if errors:
            return (
                render_template(
                    "recurring_form.html",
                    schedule=None,
                    form=request.form,
                    errors=errors,
                    priorities=PRIORITIES,
                ),
                400,
            )
        db = get_db()
        db.execute(
            """
            INSERT INTO recurring_tickets (
                subject, description, priority, assigned_to,
                interval_value, interval_unit, start_date, next_run_date,
                active, created_by, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["subject"],
                data["description"],
                data["priority"],
                data["assigned_to"],
                data["interval_value"],
                data["interval_unit"],
                data["start_date"],
                data["start_date"],  # first next_run_date == start_date
                1,
                g.current_user["username"],
                now_iso(),
            ),
        )
        db.commit()
        return redirect(url_for("staff_recurring_list"))

    today_iso = date.today().isoformat()
    form = {
        "interval_value": "1",
        "interval_unit": "week",
        "start_date": today_iso,
        "priority": "normal",
    }
    return render_template(
        "recurring_form.html",
        schedule=None,
        form=form,
        errors=[],
        priorities=PRIORITIES,
    )


@app.route("/staff/recurring/<int:recurring_id>", methods=["GET", "POST"])
@login_required
def staff_recurring_edit(recurring_id):
    db = get_db()
    schedule = db.execute(
        "SELECT * FROM recurring_tickets WHERE id = ?", (recurring_id,)
    ).fetchone()
    if schedule is None:
        abort(404)

    if request.method == "POST":
        data, errors = _parse_recurring_form(request.form)
        if errors:
            return (
                render_template(
                    "recurring_form.html",
                    schedule=schedule,
                    form=request.form,
                    errors=errors,
                    priorities=PRIORITIES,
                ),
                400,
            )
        # Reset next_run_date to new start_date if start_date changed.
        if data["start_date"] != schedule["start_date"]:
            new_next_run = data["start_date"]
        else:
            new_next_run = schedule["next_run_date"]
        db.execute(
            """
            UPDATE recurring_tickets
            SET subject=?, description=?, priority=?, assigned_to=?,
                interval_value=?, interval_unit=?, start_date=?, next_run_date=?,
                active=?
            WHERE id=?
            """,
            (
                data["subject"],
                data["description"],
                data["priority"],
                data["assigned_to"],
                data["interval_value"],
                data["interval_unit"],
                data["start_date"],
                new_next_run,
                data["active"],
                recurring_id,
            ),
        )
        db.commit()
        return redirect(url_for("staff_recurring_list"))

    form = {
        "subject": schedule["subject"],
        "description": schedule["description"],
        "priority": schedule["priority"],
        "assigned_to": schedule["assigned_to"] or "",
        "interval_value": schedule["interval_value"],
        "interval_unit": schedule["interval_unit"],
        "start_date": schedule["start_date"],
        "active": schedule["active"],
    }
    return render_template(
        "recurring_form.html",
        schedule=schedule,
        form=form,
        errors=[],
        priorities=PRIORITIES,
    )


@app.route("/staff/recurring/<int:recurring_id>/toggle", methods=["POST"])
@login_required
def staff_recurring_toggle(recurring_id):
    db = get_db()
    row = db.execute(
        "SELECT active FROM recurring_tickets WHERE id = ?", (recurring_id,)
    ).fetchone()
    if row is None:
        abort(404)
    db.execute(
        "UPDATE recurring_tickets SET active = ? WHERE id = ?",
        (0 if row["active"] else 1, recurring_id),
    )
    db.commit()
    return redirect(url_for("staff_recurring_list"))


@app.route("/staff/recurring/<int:recurring_id>/delete", methods=["POST"])
@login_required
def staff_recurring_delete(recurring_id):
    db = get_db()
    db.execute("DELETE FROM recurring_tickets WHERE id = ?", (recurring_id,))
    db.commit()
    return redirect(url_for("staff_recurring_list"))


@app.route("/staff/recurring/<int:recurring_id>/run", methods=["POST"])
@login_required
def staff_recurring_run_now(recurring_id):
    """Generate a ticket from this schedule immediately (out-of-band)."""
    db = get_db()
    schedule = db.execute(
        "SELECT * FROM recurring_tickets WHERE id = ?", (recurring_id,)
    ).fetchone()
    if schedule is None:
        abort(404)
    now = now_iso()
    db.execute(
        """
        INSERT INTO tickets (submitter_name, submitter_email, subject, description,
                             status, priority, assigned_to, created_at, updated_at)
        VALUES (?, ?, ?, ?, 'open', ?, ?, ?, ?)
        """,
        (
            "[Recurring - manual]",
            "noreply@helpdesk.local",
            schedule["subject"],
            schedule["description"],
            schedule["priority"],
            schedule["assigned_to"],
            now,
            now,
        ),
    )
    db.execute(
        "UPDATE recurring_tickets SET last_generated_at = ? WHERE id = ?",
        (now, recurring_id),
    )
    db.commit()
    return redirect(url_for("staff_recurring_list"))


# ---------------------------------------------------------------------------
# Software licenses
# ---------------------------------------------------------------------------

LICENSE_EXPIRING_DAYS = 30


def _license_status(renewal_date_str):
    """Return 'expired' | 'expiring' | 'active' | None (no renewal set)."""
    if not renewal_date_str:
        return None
    try:
        d = date.fromisoformat(renewal_date_str)
    except ValueError:
        return None
    today = date.today()
    if d < today:
        return "expired"
    if (d - today).days <= LICENSE_EXPIRING_DAYS:
        return "expiring"
    return "active"


def _mask_license_key(key):
    """Show only the last 4 characters; the rest becomes dots."""
    if not key:
        return ""
    if len(key) <= 4:
        return "•" * len(key)
    return "•" * (len(key) - 4) + key[-4:]


def _parse_license_form(form):
    name = (form.get("name") or "").strip()
    vendor = (form.get("vendor") or "").strip() or None
    seats_total_raw = (form.get("seats_total") or "").strip()
    seats_used_raw = (form.get("seats_used") or "").strip()
    renewal_date = (form.get("renewal_date") or "").strip()
    purchase_date = (form.get("purchase_date") or "").strip()
    annual_cost_raw = (form.get("annual_cost") or "").strip()
    license_key = (form.get("license_key") or "").strip() or None
    notes = (form.get("notes") or "").strip() or None

    errors = []
    if not name:
        errors.append("Name is required.")
    if len(name) > 200:
        errors.append("Name must be 200 characters or fewer.")

    try:
        seats_total = int(seats_total_raw)
        if seats_total < 1:
            raise ValueError
    except ValueError:
        seats_total = 1
        errors.append("Seats total must be a whole number of 1 or greater.")

    if seats_used_raw == "":
        seats_used = 0
    else:
        try:
            seats_used = int(seats_used_raw)
            if seats_used < 0:
                raise ValueError
        except ValueError:
            seats_used = 0
            errors.append("Seats used must be zero or a positive whole number.")

    if renewal_date:
        try:
            date.fromisoformat(renewal_date)
        except ValueError:
            errors.append("Renewal date must be a valid date.")
    else:
        renewal_date = None

    if purchase_date:
        try:
            date.fromisoformat(purchase_date)
        except ValueError:
            errors.append("Purchase date must be a valid date.")
    else:
        purchase_date = None

    if annual_cost_raw:
        try:
            annual_cost = float(annual_cost_raw)
            if annual_cost < 0:
                raise ValueError
        except ValueError:
            annual_cost = None
            errors.append("Annual cost must be a positive number.")
    else:
        annual_cost = None

    return (
        {
            "name": name,
            "vendor": vendor,
            "seats_total": seats_total,
            "seats_used": seats_used,
            "renewal_date": renewal_date,
            "purchase_date": purchase_date,
            "annual_cost": annual_cost,
            "license_key": license_key,
            "notes": notes,
        },
        errors,
    )


@app.route("/staff/licenses")
@login_required
def staff_licenses_list():
    rows = get_db().execute(
        """
        SELECT * FROM licenses
        ORDER BY
          CASE WHEN renewal_date IS NULL THEN 1 ELSE 0 END,
          renewal_date ASC,
          name ASC
        """
    ).fetchall()
    licenses = []
    for r in rows:
        d = dict(r)
        d["status"] = _license_status(r["renewal_date"])
        d["license_key_masked"] = _mask_license_key(r["license_key"])
        licenses.append(d)
    return render_template("licenses_list.html", licenses=licenses)


@app.route("/staff/licenses/new", methods=["GET", "POST"])
@login_required
def staff_license_new():
    if request.method == "POST":
        data, errors = _parse_license_form(request.form)
        if errors:
            return (
                render_template(
                    "license_form.html",
                    license=None,
                    form=request.form,
                    errors=errors,
                ),
                400,
            )
        now = now_iso()
        get_db().execute(
            """
            INSERT INTO licenses (
                name, vendor, seats_total, seats_used,
                renewal_date, purchase_date, annual_cost,
                license_key, notes,
                created_by, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["name"],
                data["vendor"],
                data["seats_total"],
                data["seats_used"],
                data["renewal_date"],
                data["purchase_date"],
                data["annual_cost"],
                data["license_key"],
                data["notes"],
                g.current_user["username"],
                now,
                now,
            ),
        )
        get_db().commit()
        return redirect(url_for("staff_licenses_list"))

    form = {"seats_total": "1", "seats_used": "0"}
    return render_template(
        "license_form.html", license=None, form=form, errors=[]
    )


@app.route("/staff/licenses/<int:license_id>", methods=["GET", "POST"])
@login_required
def staff_license_edit(license_id):
    db = get_db()
    lic = db.execute(
        "SELECT * FROM licenses WHERE id = ?", (license_id,)
    ).fetchone()
    if lic is None:
        abort(404)

    if request.method == "POST":
        data, errors = _parse_license_form(request.form)
        if errors:
            return (
                render_template(
                    "license_form.html",
                    license=lic,
                    form=request.form,
                    errors=errors,
                ),
                400,
            )
        db.execute(
            """
            UPDATE licenses
            SET name=?, vendor=?, seats_total=?, seats_used=?,
                renewal_date=?, purchase_date=?, annual_cost=?,
                license_key=?, notes=?, updated_at=?
            WHERE id=?
            """,
            (
                data["name"],
                data["vendor"],
                data["seats_total"],
                data["seats_used"],
                data["renewal_date"],
                data["purchase_date"],
                data["annual_cost"],
                data["license_key"],
                data["notes"],
                now_iso(),
                license_id,
            ),
        )
        db.commit()
        return redirect(url_for("staff_licenses_list"))

    form = {
        "name": lic["name"],
        "vendor": lic["vendor"] or "",
        "seats_total": lic["seats_total"],
        "seats_used": lic["seats_used"],
        "renewal_date": lic["renewal_date"] or "",
        "purchase_date": lic["purchase_date"] or "",
        "annual_cost": "" if lic["annual_cost"] is None else lic["annual_cost"],
        "license_key": lic["license_key"] or "",
        "notes": lic["notes"] or "",
    }
    return render_template(
        "license_form.html", license=lic, form=form, errors=[]
    )


@app.route("/staff/licenses/<int:license_id>/delete", methods=["POST"])
@login_required
def staff_license_delete(license_id):
    db = get_db()
    db.execute("DELETE FROM licenses WHERE id = ?", (license_id,))
    db.commit()
    return redirect(url_for("staff_licenses_list"))


# ---------------------------------------------------------------------------
# Staff quick-create ticket (work log)
# ---------------------------------------------------------------------------


@app.route("/staff/ticket/new", methods=["GET", "POST"])
@login_required
def staff_ticket_new():
    """Streamlined ticket creation for staff logging their own work."""
    me = g.current_user["username"]

    if request.method == "POST":
        subject = (request.form.get("subject") or "").strip()
        description = (request.form.get("description") or "").strip()
        priority = (request.form.get("priority") or "normal").strip()
        status = (request.form.get("status") or "in_progress").strip()
        assigned_to = (request.form.get("assigned_to") or me).strip() or me

        errors = []
        if not subject:
            errors.append("Subject is required.")
        if len(subject) > 200:
            errors.append("Subject must be 200 characters or fewer.")
        if not description:
            errors.append("Description is required.")
        if priority not in PRIORITIES:
            errors.append("Invalid priority.")
        if status not in STATUSES:
            errors.append("Invalid status.")

        if errors:
            return (
                render_template(
                    "staff_ticket_new.html",
                    errors=errors,
                    form=request.form,
                    priorities=PRIORITIES,
                    statuses=STATUSES,
                    me=me,
                ),
                400,
            )

        now = now_iso()
        cur = get_db().execute(
            """
            INSERT INTO tickets (
                submitter_name, submitter_email, subject, description,
                status, priority, assigned_to, created_at, updated_at,
                created_by_staff
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                me,                            # submitter is the staff user
                "internal@helpdesk.local",     # placeholder; not a real address
                subject,
                description,
                status,
                priority,
                assigned_to,
                now,
                now,
                me,                            # flag this as staff-created
            ),
        )
        get_db().commit()
        return redirect(url_for("staff_ticket", ticket_id=cur.lastrowid))

    return render_template(
        "staff_ticket_new.html",
        errors=[],
        form={"priority": "normal", "status": "in_progress", "assigned_to": me},
        priorities=PRIORITIES,
        statuses=STATUSES,
        me=me,
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
            now = now_iso()
            # Only set resolved_at / resolved_by when the status transitions
            # INTO 'resolved'. Leaving 'resolved' does not clear them.
            if status == "resolved" and ticket["status"] != "resolved":
                db.execute(
                    """
                    UPDATE tickets
                       SET status=?, priority=?, assigned_to=?, updated_at=?,
                           resolved_at=?, resolved_by=?
                     WHERE id=?
                    """,
                    (status, priority, assigned_to, now, now,
                     g.current_user["username"], ticket_id),
                )
            else:
                db.execute(
                    """
                    UPDATE tickets SET status=?, priority=?, assigned_to=?, updated_at=?
                    WHERE id=?
                    """,
                    (status, priority, assigned_to, now, ticket_id),
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


def cli_run_scheduler(argv):
    """Manually generate any due recurring tickets (hook for Task Scheduler)."""
    init_db()
    with app.app_context():
        count = generate_due_tickets()
    print(f"Generated {count} ticket(s).")
    return 0


def cli_help(argv):
    print(
        "Usage:\n"
        "  python app.py                    Start the web server\n"
        "  python app.py create-user [name] Create a new staff user (prompts for password)\n"
        "  python app.py list-users         List staff users\n"
        "  python app.py delete-user <name> Delete a staff user\n"
        "  python app.py run-scheduler      Generate any due recurring tickets and exit\n"
    )
    return 0


COMMANDS = {
    "create-user": cli_create_user,
    "list-users": cli_list_users,
    "delete-user": cli_delete_user,
    "run-scheduler": cli_run_scheduler,
    "help": cli_help,
    "--help": cli_help,
    "-h": cli_help,
}


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in COMMANDS:
        sys.exit(COMMANDS[sys.argv[1]](sys.argv[2:]))
    init_db()
    start_scheduler()
    app.run(host="0.0.0.0", port=5000, debug=False)
