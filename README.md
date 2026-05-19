# Helpdesk

Self-hosted ticketing system for a small IT team. Flask + SQLite, designed
to run on a Windows server and serve a few staff members plus an internal
submission portal for everyone else.

Built deliberately small: no JavaScript framework, no ORM, no Docker, no
external services. One Python file, a handful of templates, and a SQLite
database that's just a file you can copy.

## What's in it

**For end users (no login required)**

- Public ticket submission form at `/` — name, email, subject, priority,
  description.

**For IT staff (login required)**

- **Dashboard** at `/staff`: four stat cards (open / unassigned / mine /
  in-progress), open-by-priority breakdown, an aging widget that lists
  tickets open more than 3 days, a "my open tickets" panel, and an
  inline month-grid **calendar**.
- **Queue** at `/staff/tickets`: filterable ticket list with status,
  "assigned to me," "unassigned," and priority filters that all compose.
  Active filters render as removable chips.
- **Ticket detail** at `/staff/ticket/<id>`: change status / priority /
  assignee, add public comments or internal-only notes. Comment author is
  the logged-in staff user (no spoofing).
- **Calendar** (embedded on the dashboard): Sunday-first month grid with
  prev/today/next navigation. Hover any day to reveal a `+` button, or
  click an existing event chip — both open a popup form (modal) for fast
  create / edit / delete without leaving the page.
- **Recurring schedules** at `/staff/recurring`: define tickets that
  auto-generate at simple intervals (every N days, weeks, or months).
  A background scheduler thread fires every 60 seconds; `/staff` also
  triggers a lazy catch-up on each load. Schedules can be paused,
  resumed, or run on-demand. Upcoming runs show on the calendar as
  amber chips with a `↻` glyph.

**Security / ops**

- Passwords are bcrypt-hashed in a `users` table.
- Flask session secret is loaded from `HELPDESK_SECRET_KEY` env var or
  generated into `.flask_secret` on first boot (gitignored).
- Failed logins return 401 with a generic "Invalid username or password"
  — doesn't leak whether the username exists.
- `<dialog>`-based modals with progressive enhancement: every modal
  trigger keeps its original `href`, so the standalone form pages still
  work if JavaScript is disabled.

## Setup

1. Install Python 3.11+ (check **Add to PATH** during install).
2. Clone this repo and open PowerShell in the project folder.
3. Create a venv and install dependencies:

   ```powershell
   python -m venv venv
   venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   ```

   (If activation is blocked by execution policy, run once:
   `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.)

4. Create your first staff user (interactive password prompt, min 8 chars):

   ```powershell
   python app.py create-user alex
   ```

5. Start the server:

   ```powershell
   python app.py
   ```

6. Visit `http://localhost:5000/` on the server, or
   `http://<server-name>:5000/` from another machine on the LAN.

7. Allow inbound TCP 5000 in Windows Firewall if other machines need to
   reach it:

   ```powershell
   New-NetFirewallRule -DisplayName "Helpdesk 5000" -Direction Inbound `
     -Protocol TCP -LocalPort 5000 -Action Allow
   ```

## CLI reference

```powershell
python app.py                       # Start the web server
python app.py create-user <name>    # Create a staff user (prompts for password)
python app.py list-users            # List staff users
python app.py delete-user <name>    # Delete a staff user
python app.py run-scheduler         # Generate any due recurring tickets and exit
python app.py help                  # Show this list
```

The `run-scheduler` command is provided as a hook for Windows Task
Scheduler if you'd rather drive generation externally than rely on the
in-process thread.

## Project layout

```
app.py                    All routes, helpers, models, scheduler
requirements.txt          Flask, bcrypt
helpdesk.db               SQLite database (auto-created on first run)
.flask_secret             Generated session-secret (gitignored)
templates/
  base.html               Layout, header nav, signed-in chrome
  submit.html             Public ticket-submission form
  submitted.html          "Ticket #X created" confirmation
  login.html              Staff sign-in
  dashboard.html          /staff: stats + aging + calendar + my queue
  staff.html              /staff/tickets: queue with composable filters
  ticket.html             Per-ticket page with comments
  event_form.html         New/edit calendar event (also used inside the
                          dashboard modal as a no-JS fallback)
  recurring_list.html     /staff/recurring: schedule list
  recurring_form.html     /staff/recurring/new and /<id>: schedule form
  error.html              4xx error page
static/
  style.css               All styles
```

## Tables

- `users` — staff accounts
- `tickets` — every submission, including ones auto-created by recurring schedules (submitter "[Recurring]")
- `comments` — public + internal-only notes on tickets
- `events` — ad-hoc calendar entries (date, optional time, title, notes)
- `recurring_tickets` — templates + interval + next-run state

Schema is declared in `init_db()` with `CREATE TABLE IF NOT EXISTS`, so
new tables added in later versions migrate automatically.

## Roadmap

- **Pass 4** — email-to-ticket polling: an IMAP background worker that
  watches a shared mailbox and threads replies onto existing tickets via
  a `[#NNN]` tag in the subject.
- **Pass 5** — outbound notifications via SMTP (status changes, comments
  visible to the submitter, recurring ticket generation), plus
  packaging as a proper Windows service via NSSM + waitress.

## Stack notes

- Python 3.11+, Flask 3.x, bcrypt, SQLite.
- Sessions use Flask's signed cookies (HttpOnly, SameSite=Lax).
- The scheduler runs as a daemon thread inside the Flask process; lazy
  catch-up at `/staff` load is the belt-and-suspenders fallback.
- Native HTML5 `<dialog>` is used for the event modals — no jQuery, no
  Alpine, no React. ~30 lines of vanilla JS via event delegation.
- Time handling: `datetime.utcnow()` + ISO-8601 strings in SQLite. Date
  math uses `timedelta` for day/week and a custom `_add_months` that
  clamps to the last day of the target month (Jan 31 + 1 month → Feb 28).
