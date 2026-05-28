# UI refresh + Team activity stats — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add per-staff "Team activity" stats to the dashboard, finish the in-flight `staff_ticket_new` route, and refresh the chrome (two-tier header, prominent New ticket CTA, rename Queue → Tickets with open-count badge, CSS polish pass).

**Architecture:** Stays a single-file Flask app (`app.py`) + Jinja templates + one CSS file. No new runtime deps. Schema changes go through the existing idempotent `ALTER TABLE` block in `init_db()`. Per-staff query is one SQL statement with three correlated sub-queries per row. Time-window state lives in `flask.session`. No JS framework, no migrations tool, no ORM.

**Tech Stack:** Python 3.11+, Flask 3, bcrypt, SQLite, vanilla HTML5 `<dialog>` + ~30 lines of JS.

**Testing approach:** This project has no test infrastructure (no `tests/` dir, pytest not in `requirements.txt`). Each task's verification is a **manual browser walk-through** against a local server, using the seeded `adminandrew` / `Temp174480` login. Adding a test framework is out of scope for this plan.

**Spec:** `docs/superpowers/specs/2026-05-27-ui-refresh-and-team-stats-design.md`

---

## File map

**Create:**
- `templates/staff_ticket_new.html` — the staff quick-entry form template (route already exists).

**Modify:**
- `app.py` — schema additions, resolve-tracking in ticket POST, queue filter extensions, dashboard team-activity query + window logic, open-ticket-count context processor.
- `templates/base.html` — two-tier header with `+ New ticket` CTA, "Tickets" label with badge, `request.endpoint`-driven active underline.
- `templates/dashboard.html` — new Team activity panel between stat cards and aging row.
- `templates/staff.html` — render chips for new `assignee` / `created_by` / `resolved_by` filters.
- `static/style.css` — header second-tier styles, badge, team-activity panel, pill group, polish pass on forms/tables/buttons.

**No new dependencies.** `requirements.txt` stays Flask + bcrypt.

---

## Task 1: Land the in-flight WIP as a clean baseline

The working tree has two unrelated WIP threads. Ship the complete one as its own commit, then commit the partial `staff_ticket_new` scaffolding so subsequent tasks build on a clean tree.

**Files:**
- Modify (already modified, just commit): `app.py`, `templates/license_form.html`, `templates/licenses_list.html`

- [ ] **Step 1: Inspect the current diff**

```bash
cd /c/Users/apbat/Projects/helpdesk
git status
git diff --stat
```

Expected: three modified files; `app.py`, `templates/license_form.html`, `templates/licenses_list.html`.

- [ ] **Step 2: Stage and commit the licenses `assigned_to` removal**

The licenses cleanup touches `license_form.html`, `licenses_list.html`, and the `_parse_license_form` / INSERT / UPDATE paths in `app.py`. The `app.py` diff *also* contains the `staff_ticket_new` scaffolding. To split cleanly, stage the two template files (fully removed), then use `git add -p` for just the license-related hunks in `app.py`.

```bash
git add templates/license_form.html templates/licenses_list.html
git add -p app.py
```

In the interactive prompt, accept (`y`) hunks that remove `assigned_to` from:
- `init_db()` schema CREATE TABLE
- the `ALTER TABLE licenses DROP COLUMN assigned_to` migration entry (keep `created_by_staff` for the next commit)
- `_parse_license_form()` (parsing, errors, return dict)
- `staff_license_new` INSERT
- `staff_license_edit` UPDATE and form-state dict

Reject (`n`) hunks that:
- Add the `staff_ticket_new` route block
- Add `created_by_staff` to the migration list

If `git add -p` is too fiddly, the simpler alternative: stage everything, commit, then revert just the staff_ticket_new block in a follow-up commit. The first ordering is preferred because it leaves the tree in a state where each commit is self-contained.

```bash
git commit -m "Remove unused 'assigned_to' field from software licenses"
```

- [ ] **Step 3: Commit the remaining `staff_ticket_new` scaffolding + `created_by_staff` migration**

```bash
git add app.py
git commit -m "Scaffold staff_ticket_new route and created_by_staff column"
```

The route currently 500s when hit (template missing) — Task 5 fixes that.

- [ ] **Step 4: Confirm clean tree**

```bash
git status
```

Expected: `nothing to commit, working tree clean`.

---

## Task 2: Add `resolved_at` and `resolved_by` columns

The team-activity "Resolved" column needs to know who resolved a ticket and when.

**Files:**
- Modify: `app.py:60-154` (function `init_db`)

- [ ] **Step 1: Extend the migration block**

In `app.py`, locate the migration list inside `init_db()`:

```python
    for stmt in (
        "ALTER TABLE licenses DROP COLUMN assigned_to",
        "ALTER TABLE tickets ADD COLUMN created_by_staff TEXT",
    ):
```

Replace with:

```python
    for stmt in (
        "ALTER TABLE licenses DROP COLUMN assigned_to",
        "ALTER TABLE tickets ADD COLUMN created_by_staff TEXT",
        "ALTER TABLE tickets ADD COLUMN resolved_at TEXT",
        "ALTER TABLE tickets ADD COLUMN resolved_by TEXT",
    ):
```

- [ ] **Step 2: Verify the migration runs cleanly**

```bash
venv\Scripts\activate.bat
python app.py
```

In another shell, in the project directory:

```bash
venv\Scripts\python.exe -c "import sqlite3; c = sqlite3.connect('helpdesk.db'); print(c.execute(\"PRAGMA table_info(tickets)\").fetchall())"
```

Expected: the printed column list includes `resolved_at` (TEXT) and `resolved_by` (TEXT). Stop the server with Ctrl+C.

- [ ] **Step 3: Commit**

```bash
git add app.py
git commit -m "Add resolved_at and resolved_by columns to tickets"
```

---

## Task 3: Set `resolved_at` / `resolved_by` on status transitions to 'resolved'

When a staff user marks a ticket resolved on the ticket detail page, capture who and when.

**Files:**
- Modify: `app.py:1400-1445` (function `staff_ticket`, the `action == "update"` branch)

- [ ] **Step 1: Patch the update branch**

Locate this block in `staff_ticket()`:

```python
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
```

Replace with:

```python
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
```

- [ ] **Step 2: Manually verify**

```bash
venv\Scripts\python.exe app.py
```

In a browser:

1. Sign in as `adminandrew` / `Temp174480`.
2. Go to `/staff/tickets`, open any non-resolved ticket.
3. Change Status to "resolved", click Update.

Then in another shell:

```bash
venv\Scripts\python.exe -c "import sqlite3; c = sqlite3.connect('helpdesk.db'); c.row_factory = sqlite3.Row; r = c.execute('SELECT id, status, resolved_at, resolved_by FROM tickets WHERE status = \"resolved\" ORDER BY id DESC LIMIT 1').fetchone(); print(dict(r))"
```

Expected: the most recently resolved ticket has non-null `resolved_at` (recent ISO timestamp) and `resolved_by = 'adminandrew'`.

Bonus check: change the same ticket back to `in_progress`, then `resolved` again. Confirm `resolved_at` updates to the new timestamp (re-resolution overwrites — that's the accepted spec behavior).

Stop the server with Ctrl+C.

- [ ] **Step 3: Commit**

```bash
git add app.py
git commit -m "Track resolver and resolution time on ticket status change"
```

---

## Task 4: Two-tier header in base.html + open-ticket-count context processor + active-nav

This task lands all the structural header changes in one commit (HTML structure, the Python context processor that feeds the badge, and the active-nav helper). CSS for the new pieces follows in Task 6.

**Files:**
- Modify: `app.py` — add `@app.context_processor`
- Modify: `templates/base.html` — replace header

- [ ] **Step 1: Add the context processor in `app.py`**

In `app.py`, near the `load_current_user` function (around line 187), add this *after* the `load_current_user` `@app.before_request` definition:

```python
@app.context_processor
def inject_header_globals():
    """Make open_ticket_count available to base.html on every staff page."""
    if g.get("current_user") is None:
        return {}
    row = get_db().execute(
        "SELECT COUNT(*) AS n FROM tickets WHERE status NOT IN ('resolved', 'closed')"
    ).fetchone()
    return {"open_ticket_count": row["n"] if row else 0}
```

- [ ] **Step 2: Replace `templates/base.html`**

Overwrite the entire file with:

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{% block title %}Helpdesk{% endblock %}</title>
  <link rel="stylesheet" href="{{ url_for('static', filename='style.css') }}">
</head>
<body>
  <header class="site-header">
    <div class="hdr-top">
      <a class="hdr-brand" href="{{ url_for('staff_dashboard') if g.current_user else url_for('submit') }}">Helpdesk</a>
      <span class="hdr-spacer"></span>
      {% if g.current_user %}
        <a class="hdr-cta" href="{{ url_for('staff_ticket_new') }}">+ New ticket</a>
        <span class="hdr-who">Signed in as <strong>{{ g.current_user.username }}</strong></span>
        <form method="post" action="{{ url_for('logout') }}" class="hdr-logout">
          <button type="submit">Sign out</button>
        </form>
      {% else %}
        <a class="hdr-link" href="{{ url_for('submit') }}">Submit ticket</a>
        <a class="hdr-link" href="{{ url_for('login') }}">Staff sign in</a>
      {% endif %}
    </div>
    {% if g.current_user %}
    {% set ep = request.endpoint %}
    <nav class="hdr-nav">
      <a href="{{ url_for('staff_dashboard') }}"
         class="{% if ep == 'staff_dashboard' %}active{% endif %}">Dashboard</a>
      <a href="{{ url_for('staff_queue') }}"
         class="{% if ep in ('staff_queue', 'staff_ticket', 'staff_ticket_new') %}active{% endif %}">
        Tickets
        {% if open_ticket_count %}<span class="hdr-badge">{{ open_ticket_count }}</span>{% endif %}
      </a>
      <a href="{{ url_for('staff_licenses_list') }}"
         class="{% if ep and ep.startswith('staff_license') %}active{% endif %}">Licenses</a>
      <a href="{{ url_for('staff_recurring_list') }}"
         class="{% if ep and ep.startswith('staff_recurring') %}active{% endif %}">Recurring</a>
    </nav>
    {% endif %}
  </header>
  <main>{% block content %}{% endblock %}</main>

  <script>
    // (No dropdown to close anymore — keeping this empty hook for future use.)
  </script>
</body>
</html>
```

Notes:
- The hamburger dropdown is gone; all four nav items live on the second tier.
- The `+ New ticket` link target uses `url_for('staff_ticket_new')` — the route already exists, the template will be created in Task 5.
- The Tickets link is "active" when on the queue, a ticket detail page, or the new-ticket form.
- Public submitters see only the top tier with a "Submit ticket" link and a "Staff sign in" link — no second tier.

- [ ] **Step 3: Verify the page loads (CSS will be ugly until Task 6)**

```bash
venv\Scripts\python.exe app.py
```

Sign in as `adminandrew`. Visit `/staff`. Confirm:
- The header shows brand + "+ New ticket" link + user widget on one row, and Dashboard / Tickets (with count) / Licenses / Recurring on a row below.
- Clicking "+ New ticket" 500s for now (template missing — Task 5). That's expected.
- Clicking Dashboard / Tickets / Licenses / Recurring all work; the active link is the one for the current page (no underline yet — just a class attribute on the matching `<a>`).
- Sign out, visit `/`. Confirm only the top tier renders.

Stop the server.

- [ ] **Step 4: Commit**

```bash
git add app.py templates/base.html
git commit -m "Two-tier header with New ticket CTA and open-count badge"
```

---

## Task 5: Create `staff_ticket_new.html` template

Finishes the in-flight route — the `+ New ticket` CTA stops 500ing.

**Files:**
- Create: `templates/staff_ticket_new.html`

- [ ] **Step 1: Write the template**

Create `templates/staff_ticket_new.html` with:

```html
{% extends "base.html" %}
{% block title %}New ticket{% endblock %}
{% block content %}
<h1>New ticket</h1>
<p class="back"><a href="{{ url_for('staff_queue') }}">&larr; All tickets</a></p>

{% if errors %}
<div class="form-errors">
  <strong>Could not create the ticket:</strong>
  <ul>{% for e in errors %}<li>{{ e }}</li>{% endfor %}</ul>
</div>
{% endif %}

<form method="post" class="stacked ticket-new-form">
  <label>Subject
    <input type="text" name="subject" maxlength="200" required
           autofocus
           value="{{ form.subject or '' }}"
           placeholder="Short description (e.g., printer offline in Acct)">
  </label>

  <label>Description
    <textarea name="description" rows="6" required
              placeholder="What happened, what you did, what's next…">{{ form.description or '' }}</textarea>
  </label>

  <p class="defaults-summary">
    Will be saved as
    <span class="chip">priority: <strong>{{ form.priority or 'normal' }}</strong></span>
    <span class="chip">status: <strong>{{ form.status or 'in_progress' }}</strong></span>
    <span class="chip">assigned: <strong>{% if form.assigned_to and form.assigned_to != me %}{{ form.assigned_to }}{% else %}you{% endif %}</strong></span>
  </p>

  <details class="more-options">
    <summary>&#9881; Change priority, status, or assignee</summary>
    <div class="more-grid">
      <label>Priority
        <select name="priority">
          {% for p in priorities %}
          <option value="{{ p }}" {% if (form.priority or 'normal') == p %}selected{% endif %}>{{ p }}</option>
          {% endfor %}
        </select>
      </label>
      <label>Status
        <select name="status">
          {% for s in statuses %}
          <option value="{{ s }}" {% if (form.status or 'in_progress') == s %}selected{% endif %}>{{ s }}</option>
          {% endfor %}
        </select>
      </label>
      <label>Assigned to
        <input type="text" name="assigned_to" maxlength="100"
               value="{{ form.assigned_to or me }}"
               placeholder="username or blank for unassigned">
      </label>
    </div>
  </details>

  <div class="form-actions">
    <button type="submit">Create ticket</button>
    <a class="btn-secondary" href="{{ url_for('staff_queue') }}">Cancel</a>
  </div>
</form>
{% endblock %}
```

Note: `assigned_to` is a text input (not a select). The schema stores arbitrary strings there — there's no relational constraint to `users`. This matches the existing `ticket.html` form's behavior. Defaulting to the current user covers the common case.

- [ ] **Step 2: Manually verify**

```bash
venv\Scripts\python.exe app.py
```

Sign in. Click "+ New ticket" in the header.
- Confirm the page renders with Subject + Description + the chip summary + a closed `<details>` block.
- Click the disclosure — the three controls appear.
- Type a subject and description, leave defaults. Submit.
- Confirm you land on `/staff/ticket/<new id>` and the ticket has priority=normal, status=in_progress, assigned_to=adminandrew.
- Hit Back, click "+ New ticket" again, open the disclosure, set priority=high and status=new, change assignee to a different name. Submit. Confirm the new ticket reflects those values.
- Submit a third time with an empty subject — confirm errors render at the top and the form keeps your typed description.

In another shell:

```bash
venv\Scripts\python.exe -c "import sqlite3; c = sqlite3.connect('helpdesk.db'); c.row_factory = sqlite3.Row; rows = c.execute('SELECT id, subject, status, priority, created_by_staff FROM tickets ORDER BY id DESC LIMIT 3').fetchall(); [print(dict(r)) for r in rows]"
```

Expected: each of the three new tickets has `created_by_staff = 'adminandrew'`.

Stop the server.

- [ ] **Step 3: Commit**

```bash
git add templates/staff_ticket_new.html
git commit -m "Add staff quick-entry ticket template"
```

---

## Task 6: CSS for the new header + form + polish pass

One CSS file, one commit. Covers everything from sections 1, 3, and 6 of the spec.

**Files:**
- Modify: `static/style.css`

- [ ] **Step 1: Replace the existing header block**

In `static/style.css`, find the `header { ... }` rule (around line 11) and the related rules through the end of the `header details.nav-menu .menu-content a:hover` rule (around line 76). **Delete all of them** — the new header uses different class names so the old selectors are dead.

Insert this block in their place:

```css
/* ===== Site header (two-tier) ===== */
.site-header { background: #1f2937; }
.hdr-top {
  color: #fff;
  padding: .65rem 1.5rem;
  display: flex;
  gap: 1.25rem;
  align-items: center;
}
.hdr-brand {
  color: #fff;
  text-decoration: none;
  font-weight: 700;
  letter-spacing: .02em;
}
.hdr-brand:hover { text-decoration: none; opacity: .85; }
.hdr-spacer { flex: 1; }
.hdr-link { color: #fff; text-decoration: none; font-weight: 600; font-size: .9rem; }
.hdr-link:hover { text-decoration: underline; }
.hdr-cta {
  background: #2563eb;
  color: #fff;
  text-decoration: none;
  padding: .35rem .8rem;
  border-radius: 4px;
  font-weight: 600;
  font-size: .85rem;
}
.hdr-cta:hover { background: #1d4ed8; text-decoration: none; }
.hdr-who { color: #cbd5e1; font-size: .85rem; }
.hdr-who strong { color: #fff; }
.hdr-logout { margin: 0; }
.hdr-logout button {
  background: transparent;
  color: #fff;
  border: 1px solid #4b5563;
  padding: .25rem .65rem;
  font-size: .85rem;
  border-radius: 4px;
  cursor: pointer;
}
.hdr-logout button:hover { background: #374151; }

.hdr-nav {
  background: #fff;
  border-bottom: 1px solid #e5e7eb;
  padding: 0 1.5rem;
  display: flex;
  gap: 1.5rem;
}
.hdr-nav a {
  color: #1f2937;
  text-decoration: none;
  font-weight: 600;
  font-size: .9rem;
  padding: .65rem 0;
  border-bottom: 2px solid transparent;
  display: inline-flex;
  align-items: center;
  gap: .35rem;
}
.hdr-nav a:hover { color: #2563eb; }
.hdr-nav a.active { color: #2563eb; border-bottom-color: #2563eb; }
.hdr-badge {
  background: #ef4444;
  color: #fff;
  font-size: .68rem;
  font-weight: 700;
  padding: .05rem .4rem;
  border-radius: 999px;
  letter-spacing: .02em;
}
```

- [ ] **Step 2: Append the new-ticket form styles + team-activity styles + polish-pass styles**

At the very end of `static/style.css`, append:

```css
/* ===== New ticket form ===== */
.ticket-new-form { max-width: 720px; }
.ticket-new-form .defaults-summary {
  background: #f9fafb;
  border: 1px solid #e5e7eb;
  border-radius: 4px;
  padding: .5rem .7rem;
  font-size: .82rem;
  color: #4b5563;
  margin: 0 0 .75rem;
}
.ticket-new-form .defaults-summary .chip {
  display: inline-block;
  background: #fff;
  border: 1px solid #d1d5db;
  border-radius: 999px;
  padding: .1rem .55rem;
  margin-right: .35rem;
  font-size: .75rem;
  color: #1f2937;
}
.ticket-new-form .defaults-summary .chip strong { font-weight: 600; }
.ticket-new-form .more-options summary {
  cursor: pointer;
  color: #2563eb;
  font-size: .85rem;
  font-weight: 600;
  padding: .4rem 0;
  list-style: none;
}
.ticket-new-form .more-options summary::-webkit-details-marker { display: none; }
.ticket-new-form .more-options summary::marker { content: ""; }
.ticket-new-form .more-grid {
  display: grid;
  grid-template-columns: 1fr 1fr 1fr;
  gap: .65rem;
  margin-top: .5rem;
}
.ticket-new-form .more-grid label { font-size: .8rem; }
.ticket-new-form .form-actions {
  display: flex;
  gap: .5rem;
  align-items: center;
  margin-top: 1rem;
}
.ticket-new-form .form-actions a.btn-secondary {
  color: #4b5563;
  background: transparent;
  border: 1px solid #d1d5db;
  padding: .5rem 1rem;
  border-radius: 4px;
  font-weight: 600;
  text-decoration: none;
  font-size: .9rem;
}
.ticket-new-form .form-actions a.btn-secondary:hover { background: #f3f4f6; }
.form-errors {
  background: #fef2f2;
  border: 1px solid #fecaca;
  border-radius: 4px;
  padding: .65rem .85rem;
  margin-bottom: 1rem;
  color: #991b1b;
  font-size: .85rem;
}
.form-errors ul { margin: .35rem 0 0 1.2rem; }

/* ===== Team activity panel ===== */
.team-activity { background: #fff; border: 1px solid #e5e7eb; border-radius: 6px; padding: .85rem 1rem; margin: 1rem 0; }
.team-head { display: flex; align-items: center; gap: .75rem; margin-bottom: .55rem; }
.team-head h2 { margin: 0; font-size: .95rem; }
.team-head .ta-spacer { flex: 1; }
.window-pills { display: inline-flex; border: 1px solid #d1d5db; border-radius: 999px; overflow: hidden; background: #f9fafb; }
.window-pills a {
  padding: .22rem .6rem;
  font-size: .72rem;
  font-weight: 600;
  color: #4b5563;
  text-decoration: none;
  border-right: 1px solid #d1d5db;
}
.window-pills a:last-child { border-right: 0; }
.window-pills a:hover { background: #f3f4f6; }
.window-pills a.active { background: #2563eb; color: #fff; }

table.team-table { width: 100%; border-collapse: collapse; font-size: .85rem; }
table.team-table th, table.team-table td { padding: .4rem .55rem; }
table.team-table th { text-align: left; font-weight: 700; color: #6b7280; font-size: .7rem; text-transform: uppercase; letter-spacing: .04em; border-bottom: 1px solid #e5e7eb; }
table.team-table th.num, table.team-table td.num { text-align: right; font-variant-numeric: tabular-nums; }
table.team-table td { border-bottom: 1px solid #f3f4f6; }
table.team-table tr:last-child td { border-bottom: 0; }
table.team-table td.name { font-weight: 600; color: #1f2937; }
table.team-table tr.me td { background: #eff6ff; }
table.team-table .me-tag { font-size: .62rem; background: #2563eb; color: #fff; padding: .05rem .35rem; border-radius: 999px; margin-left: .35rem; vertical-align: middle; letter-spacing: .04em; }
table.team-table td a { color: inherit; text-decoration: none; }
table.team-table td a:hover { text-decoration: underline; }

/* ===== Polish pass — tables, buttons (no layout changes) ===== */
table tbody tr:hover { background: #f9fafb; }
table th { text-transform: uppercase; font-size: .7rem; letter-spacing: .04em; color: #6b7280; }
table td.num, table th.num { font-variant-numeric: tabular-nums; }
button, form button { background: #2563eb; }
button:hover { background: #1d4ed8; }
```

- [ ] **Step 3: Verify the visual result**

```bash
venv\Scripts\python.exe app.py
```

Sign in. Walk through every page and confirm:
- **Header:** two tiers everywhere, dark top + white nav. Active page underlined in blue.
- **Tickets** label in nav shows a red pill with the open count.
- **"+ New ticket"** button visible on every staff page.
- **New ticket form:** chip summary visible, disclosure works, blue submit button, secondary Cancel link.
- **Dashboard, Tickets queue, Licenses, Recurring:** all still readable; table headers are uppercase tracked-out gray; row hover is subtle.

Stop the server.

- [ ] **Step 4: Commit**

```bash
git add static/style.css
git commit -m "Style two-tier header, new ticket form, and polish pass"
```

---

## Task 7: Extend `/staff/tickets` with `assignee`, `created_by`, `resolved_by` filters

The dashboard click-throughs in the next task assume these filters work.

**Files:**
- Modify: `app.py:375-420` (function `staff_queue`)
- Modify: `templates/staff.html` (filter chips)

- [ ] **Step 1: Extend the route handler**

Replace the `staff_queue` function body with:

```python
@app.route("/staff/tickets")
@login_required
def staff_queue():
    status_filter = request.args.get("status", "active")
    filter_name = request.args.get("filter")  # None | 'unassigned' | 'mine'
    priority_filter = request.args.get("priority")
    assignee_filter = (request.args.get("assignee") or "").strip() or None
    created_by_filter = (request.args.get("created_by") or "").strip() or None
    resolved_by_filter = (request.args.get("resolved_by") or "").strip() or None
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

    if assignee_filter:
        query += " AND LOWER(assigned_to) = LOWER(?)"
        params.append(assignee_filter)

    if created_by_filter:
        query += " AND LOWER(created_by_staff) = LOWER(?)"
        params.append(created_by_filter)

    if resolved_by_filter:
        query += " AND LOWER(resolved_by) = LOWER(?)"
        params.append(resolved_by_filter)

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
        assignee_filter=assignee_filter,
        created_by_filter=created_by_filter,
        resolved_by_filter=resolved_by_filter,
        statuses=STATUSES,
        priorities=PRIORITIES,
    )
```

- [ ] **Step 2: Rewrite the filter section in `templates/staff.html`**

The existing template builds chip-removal URLs by hand with nested `{% if %}` query-string concatenation. With six filters that gets unreadable, so the cleanest fix is to replace the existing filter-bar + chips block with a uniform pattern. Also: rename the `<h1>` from "Queue" to "Tickets".

Find lines 4-25 of `templates/staff.html` — from `<h1>Queue</h1>` through the closing `</div>` of `.active-filters`. **Replace that entire block** with:

```html
<h1>Tickets</h1>

{# Centralize current filter state so chips and status nav can build URLs uniformly. #}
{% set current = {
  'filter': filter_name,
  'priority': priority_filter,
  'assignee': assignee_filter,
  'created_by': created_by_filter,
  'resolved_by': resolved_by_filter,
} %}

{% macro queue_url(overrides={}) -%}
  {%- set merged = dict(current, **overrides) -%}
  {{- url_for('staff_queue',
       status=overrides.get('status', status_filter),
       **{k: v for k, v in merged.items() if v}) -}}
{%- endmacro %}

<nav class="filters">
  <a href="{{ queue_url({'status': 'active'}) }}" class="{% if status_filter == 'active' %}active{% endif %}">Active</a>
  {% for s in statuses %}
    <a href="{{ queue_url({'status': s}) }}" class="{% if status_filter == s %}active{% endif %}">{{ s }}</a>
  {% endfor %}
  <a href="{{ queue_url({'status': 'all'}) }}" class="{% if status_filter == 'all' %}active{% endif %}">All</a>
</nav>

{% if filter_name or priority_filter or assignee_filter or created_by_filter or resolved_by_filter %}
<div class="active-filters">
  {% if filter_name == 'mine' %}
    <span class="chip">Assigned to me <a href="{{ queue_url({'filter': None}) }}">&times;</a></span>
  {% endif %}
  {% if filter_name == 'unassigned' %}
    <span class="chip">Unassigned <a href="{{ queue_url({'filter': None}) }}">&times;</a></span>
  {% endif %}
  {% if priority_filter %}
    <span class="chip">Priority: <span class="prio prio-{{ priority_filter }}">{{ priority_filter }}</span> <a href="{{ queue_url({'priority': None}) }}">&times;</a></span>
  {% endif %}
  {% if assignee_filter %}
    <span class="chip">Assignee: {{ assignee_filter }} <a href="{{ queue_url({'assignee': None}) }}">&times;</a></span>
  {% endif %}
  {% if created_by_filter %}
    <span class="chip">Created by: {{ created_by_filter }} <a href="{{ queue_url({'created_by': None}) }}">&times;</a></span>
  {% endif %}
  {% if resolved_by_filter %}
    <span class="chip">Resolved by: {{ resolved_by_filter }} <a href="{{ queue_url({'resolved_by': None}) }}">&times;</a></span>
  {% endif %}
</div>
{% endif %}
```

How the macro works: `current` captures every active filter. `queue_url(overrides)` merges your override on top, then keeps only non-empty values when building the query string. Passing `{'priority': None}` removes priority from the URL while keeping every other current filter intact.

- [ ] **Step 3: Manually verify**

```bash
venv\Scripts\python.exe app.py
```

Sign in. Visit:
- `/staff/tickets?assignee=adminandrew` — confirm only tickets assigned to adminandrew show, and an "assignee: adminandrew" chip appears.
- `/staff/tickets?created_by=adminandrew` — confirm only staff-created tickets show, with a "created by: adminandrew" chip.
- `/staff/tickets?status=resolved&resolved_by=adminandrew` — confirm only resolved-by-you tickets, with both chips visible.
- Click the × on the assignee chip — confirm assignee filter clears but other filters (if any) stay.

Stop the server.

- [ ] **Step 4: Commit**

```bash
git add app.py templates/staff.html
git commit -m "Add assignee, created_by, resolved_by filters to the queue"
```

---

## Task 8: Team activity widget — backend query + dashboard template + window selector

The marquee feature. Backend computes per-staff counts for the chosen window; template renders the panel and pill group.

**Files:**
- Modify: `app.py` (the `staff_dashboard` function, around line 293, and a new helper `_team_activity_window_start`)
- Modify: `templates/dashboard.html` (new panel between stat cards and the existing `<div class="dash-grid">`)

- [ ] **Step 1: Add the window-start helper**

In `app.py`, after the `now_iso()` function (around line 159), add:

```python
TEAM_WINDOWS = ("7d", "30d", "mtd", "qtd", "all")


def _team_activity_window_start(window):
    """Return an ISO-8601 lower bound for the window, or None for 'all'."""
    from datetime import datetime, timedelta
    now = datetime.utcnow()
    if window == "7d":
        return (now - timedelta(days=7)).isoformat(timespec="seconds")
    if window == "30d":
        return (now - timedelta(days=30)).isoformat(timespec="seconds")
    if window == "mtd":
        return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat(timespec="seconds")
    if window == "qtd":
        # Calendar quarter: Jan/Apr/Jul/Oct
        q_start_month = ((now.month - 1) // 3) * 3 + 1
        return now.replace(month=q_start_month, day=1, hour=0, minute=0, second=0, microsecond=0).isoformat(timespec="seconds")
    return None  # 'all'
```

(The local `from datetime import` is intentional — `app.py` already imports `datetime` and `timedelta` at the top, but a local import keeps this helper self-contained for the engineer reading it. Feel free to drop the local import and rely on the module-level one if it's already in scope.)

- [ ] **Step 2: Wire the dashboard handler**

Find `staff_dashboard` in `app.py` (around line 293). Inside the function, **after** the existing stat-cards / aging / calendar data is computed but **before** `render_template(...)`, add:

```python
    # ---- Team activity widget --------------------------------------------
    window = request.args.get("window")
    if window in TEAM_WINDOWS:
        session["team_activity_window"] = window
    else:
        window = session.get("team_activity_window", "mtd")
    window_start = _team_activity_window_start(window)
    me_username = g.current_user["username"]

    team_rows = db.execute(
        """
        SELECT
          u.username AS username,
          (SELECT COUNT(*) FROM tickets t
              WHERE LOWER(t.created_by_staff) = LOWER(u.username)
                AND (? IS NULL OR t.created_at >= ?)
          ) AS created_count,
          (SELECT COUNT(*) FROM tickets t
              WHERE LOWER(t.assigned_to) = LOWER(u.username)
                AND t.status NOT IN ('resolved','closed')
          ) AS assigned_open_count,
          (SELECT COUNT(*) FROM tickets t
              WHERE LOWER(t.resolved_by) = LOWER(u.username)
                AND t.resolved_at IS NOT NULL
                AND (? IS NULL OR t.resolved_at >= ?)
          ) AS resolved_count
        FROM users u
        ORDER BY u.username COLLATE NOCASE
        """,
        (window_start, window_start, window_start, window_start),
    ).fetchall()
```

Then extend the `render_template(...)` call near the end of the function to pass:

```python
        team_rows=team_rows,
        team_window=window,
        team_windows=TEAM_WINDOWS,
        me_username=me_username,
```

- [ ] **Step 3: Add the panel to `templates/dashboard.html`**

Open the template. Find the closing `</div>` of the `<div class="stat-cards">` block (around line 23). Insert this *immediately after*, before the next `<div class="dash-grid">`:

```html
<section class="team-activity panel">
  <div class="team-head">
    <h2>Team activity</h2>
    <span class="ta-spacer"></span>
    <span class="window-pills">
      {% for w in team_windows %}
        <a href="{{ url_for('staff_dashboard', window=w) }}"
           class="{% if team_window == w %}active{% endif %}">
          {{ {'7d':'7d','30d':'30d','mtd':'MTD','qtd':'QTD','all':'All time'}[w] }}
        </a>
      {% endfor %}
    </span>
  </div>
  {% if team_rows %}
  <table class="team-table">
    <thead>
      <tr>
        <th>Staff member</th>
        <th class="num">Created</th>
        <th class="num">Assigned (open)</th>
        <th class="num">Resolved</th>
      </tr>
    </thead>
    <tbody>
      {% for r in team_rows %}
      <tr class="{% if r.username == me_username %}me{% endif %}">
        <td class="name">
          <a href="{{ url_for('staff_queue', assignee=r.username) }}">{{ r.username }}</a>
          {% if r.username == me_username %}<span class="me-tag">YOU</span>{% endif %}
        </td>
        <td class="num"><a href="{{ url_for('staff_queue', status='all', created_by=r.username) }}">{{ r.created_count }}</a></td>
        <td class="num"><a href="{{ url_for('staff_queue', status='active', assignee=r.username) }}">{{ r.assigned_open_count }}</a></td>
        <td class="num"><a href="{{ url_for('staff_queue', status='resolved', resolved_by=r.username) }}">{{ r.resolved_count }}</a></td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
  {% else %}
  <p class="empty">No staff users yet.</p>
  {% endif %}
</section>
```

- [ ] **Step 4: Manually verify**

```bash
venv\Scripts\python.exe app.py
```

Sign in. On `/staff`:
- Confirm the Team activity panel appears between the stat cards and the aging/priority row.
- Confirm pills: 7d / 30d / **MTD (default, highlighted)** / QTD / All time.
- Confirm your row is highlighted with a "YOU" badge.
- Click each pill — URL changes to `?window=7d` etc., active pill moves, counts update for that window.
- Reload `/staff` without `?window=` — confirm your last selection persists (session storage working).
- Click your username — lands on `/staff/tickets?assignee=adminandrew`.
- Click your "Created" number — lands on `/staff/tickets?status=all&created_by=adminandrew`.
- Click your "Assigned (open)" number — lands on `/staff/tickets?status=active&assignee=adminandrew`.
- Click your "Resolved" number — lands on `/staff/tickets?status=resolved&resolved_by=adminandrew`.

If you only have one staff user (adminandrew) seeded, create a second to confirm multi-row rendering:

```bash
venv\Scripts\python.exe app.py create-user testuser
```

(Pick any password ≥ 8 chars when prompted.) Refresh `/staff` and confirm `testuser` shows up as a second row with zeros across the board.

Stop the server.

- [ ] **Step 5: Commit**

```bash
git add app.py templates/dashboard.html
git commit -m "Add Team activity widget with selectable time window"
```

---

## Task 9: Final smoke test

End-to-end walkthrough of every path. Confirms nothing regressed.

- [ ] **Step 1: Boot a fresh server**

```bash
venv\Scripts\python.exe app.py
```

- [ ] **Step 2: Walk the full app**

Sign out (if signed in). As anonymous:

- `/` — Submit form renders with minimal header (no second tier).
- Submit a test ticket — confirm the "submitted" confirmation page renders.
- `/staff` — redirects to `/login`.

Sign in as `adminandrew`:

- `/staff` (Dashboard) — stat cards, **Team activity panel**, aging/priority widgets, calendar. Active nav underline on Dashboard. "+ New ticket" CTA visible.
- Click "+ New ticket" — quick-entry form. Chip summary, disclosure works, submit creates a ticket and redirects to its detail page. The Tickets badge count increments.
- On ticket detail — Update form works. Change status to `resolved`, save. Open detail again, confirm `Resolved by: adminandrew` is implied via the database (or sanity-check with the sqlite3 snippet from Task 3).
- Back on `/staff`: refresh, confirm Team activity "Resolved" column now reflects the change.
- `/staff/tickets` (Tickets) — Active nav underline on Tickets. Filter chips work (toggle status/priority/mine/unassigned + the new assignee/created_by/resolved_by via URL).
- `/staff/licenses` — list page, headers polished. Create / edit / delete still works. No `assigned_to` field anywhere.
- `/staff/recurring` — list page, polished headers. Create / edit / pause / resume / run-now still works.
- Calendar on dashboard — month nav, event create via `+` link, event edit via existing event chip, modal still opens. Recurring chips still amber with ↻.
- Sign out — redirects to login; minimal header reappears.

- [ ] **Step 3: Stop the server and check there are no uncommitted changes**

```bash
git status
```

Expected: `nothing to commit, working tree clean`. If anything is dirty, decide whether it's a real fix or an artifact, and either commit or revert.

- [ ] **Step 4: Done**

No commit for this task — it's verification only.

---

## Notes for the implementer

- **Commit messages** follow the existing repo style: imperative, no `feat:` / `fix:` prefixes, sentence-case, short.
- **Line endings:** Git will warn about LF → CRLF conversion on every commit. That's the repo's existing setup; ignore the warnings.
- **`venv\Scripts\activate.bat`** activates the venv in `cmd.exe`. In PowerShell use `venv\Scripts\Activate.ps1`. The `venv\Scripts\python.exe` form works in either shell without activation.
- **DB resets:** if you want a fresh DB during testing, delete `helpdesk.db`, then `python app.py create-user adminandrew` (password `Temp174480` per `.testing-notes.md`) and restart the server. The schema and migrations re-run on boot.
- **No tests:** the project has no automated test suite. Every task's verification is a manual browser walk. If a step's manual verification fails, debug, fix, and re-run that step's verification before moving on.
