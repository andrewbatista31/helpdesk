# Helpdesk UI refresh + Team activity stats — design

**Status:** approved 2026-05-27
**Scope:** one implementation pass

## Goals

- Make staff-driven ticket entry the primary path, fast and obvious from anywhere.
- Show per-staff activity (Created / Assigned-open / Resolved) so the team can see what each tech is doing.
- Make "all tickets" prominent in the top nav.
- Visually evolve the UI so it stops feeling like raw Flask scaffolding, without changing the project's "small, no-framework" character.

## Non-goals

- Email-to-ticket polling and SMTP outbound notifications (Pass 4 / 5 in the README roadmap).
- Global search.
- Packaging as a Windows service.
- Mobile-first redesign.
- Audit log of ticket state transitions.

## 1. Header / nav

Two-tier layout in `templates/base.html`, **only when `g.current_user` is set**. Public submitters keep the existing minimal header.

**Top tier** (dark `#1f2937`, current header color):
- `Helpdesk` brand on the left.
- Spacer.
- **`+ New ticket`** primary blue button linking to `/staff/ticket/new`.
- "Signed in as **username**" text.
- Sign-out form.

**Bottom tier** (white background, bottom border `#e5e7eb`):
- Dashboard
- **Tickets** with a red open-count badge (count = tickets where status in `('new', 'in_progress')`).
- Licenses
- Recurring

Active page gets a blue (`#2563eb`) bottom underline.

The route name `staff_queue` stays unchanged; only the link label changes from "Queue" to "Tickets" so existing `url_for('staff_queue')` calls keep working.

The current `<details class="nav-menu">` hamburger is removed — all four nav items fit comfortably in the second tier.

## 2. Dashboard

`templates/dashboard.html` gains a **Team activity** panel between the existing 4 stat cards and the existing aging / open-by-priority row. Calendar stays at the bottom unchanged.

**Team activity panel:**
- Table: one row per staff user (from `users` table), columns:
  - **Staff member** — username, with "YOU" badge on the current user's row, row background `#eff6ff`.
  - **Created** (numeric, right-aligned) — count of tickets where `created_by_staff = username` and the create timestamp falls in the selected window.
  - **Assigned (open)** (numeric) — count of tickets where `assigned_to = username` and status in `('new', 'in_progress')`. Note: this column ignores the time window — it reflects current workload.
  - **Resolved** (numeric) — count of tickets where `resolved_by = username` and `resolved_at` falls in the selected window.
- Time-window pill group above the table, in this order: `7d · 30d · MTD · QTD · All time`. **Default: MTD.**
- Time-window selection persists in the Flask session (`session['team_activity_window']`). No DB column.
- Clicking a staff member's name navigates to `/staff/tickets?assignee=<name>`.
- Clicking a numeric cell navigates to the queue with the matching status filter (e.g., Created → `?status=any&created_by=<name>`; Assigned → `?assignee=<name>&status=active`; Resolved → `?status=resolved&resolved_by=<name>`).

## 3. Staff "New ticket" form

New template `templates/staff_ticket_new.html` to back the existing in-flight route at `/staff/ticket/new`.

**Layout:**
- Page heading "New ticket" with a back link to `/staff/tickets`.
- Subject input (single line).
- Description textarea (~90px min-height).
- Defaults summary line:
  > Will be saved as `priority: normal` `status: in_progress` `assigned: you`
  Each value rendered as a small chip.
- A `<details>` block labeled "⚙ Change priority, status, or assignee" containing three `<select>` controls in a three-column grid:
  - Priority — options from `PRIORITIES` constant, default `normal`.
  - Status — options from `STATUSES` constant, default `in_progress`.
  - Assigned to — populated from `users` table, default current user; includes "— unassigned —".
- Actions: blue "Create ticket" submit + secondary "Cancel" link to `/staff/tickets`.

Submit posts to the existing route handler. On success, redirect to `/staff/ticket/<new_id>` (existing behavior).

The route's validation logic is unchanged. The error-rendering branch currently passes `errors`, `form`, `priorities`, `statuses`, `me` — the template will use all of these.

## 4. Data model

Three new columns on `tickets`, all added via the existing idempotent `ALTER TABLE` pattern in `init_db()` (wrapped in `try / except sqlite3.OperationalError`):

| Column | Type | Notes |
|---|---|---|
| `created_by_staff` | TEXT | **Already in WIP.** Set on staff-created tickets to `g.current_user['username']`. NULL for public submissions. Drives the **Created** column. |
| `resolved_at` | TEXT (ISO-8601) | Set when status transitions to `'resolved'`. Drives time-window filtering for **Resolved**. |
| `resolved_by` | TEXT | Set to `g.current_user['username']` on the same transition. Drives the **Resolved** column. |

**Reopen behavior:** if a ticket leaves `'resolved'` and is later re-resolved, the columns are overwritten with the most recent resolution. The previous resolution is not retained. Accepted limitation — no audit log this pass.

The existing WIP `ALTER TABLE licenses DROP COLUMN assigned_to` migration stays as-is. The whole `assigned_to` removal is shipped together with the UI work.

## 5. Routes / handlers

- **`/staff/ticket/new`** (existing in-flight route) — keep current handler. Ensure `created_by_staff = me` is set on insert (already does).
- **`/staff/ticket/<id>` (POST)** — when the status field changes:
  - To `'resolved'`: set `resolved_at = now_iso()`, `resolved_by = me`.
  - Away from `'resolved'`: leave `resolved_at` / `resolved_by` alone (do not clear).
  - No-op if status didn't change or is staying within non-resolved values.
- **`/staff` (dashboard)** — extend the existing handler:
  - Read `?window=` query param; fall back to `session['team_activity_window']`; fall back to `'mtd'`.
  - On any explicit `?window=` param, write it back to the session.
  - Compute the window's start datetime (ISO string) in Python: `7d` / `30d` use `now - timedelta`; `mtd` uses first of current month at 00:00; `qtd` uses first of current quarter; `all` uses NULL (no lower bound).
  - One SQL query joining `users` to ticket counts via correlated subqueries or grouped `LEFT JOIN`s. Columns: username, created_count (date-window-filtered, `created_by_staff` match), assigned_open_count (no window, `assigned_to` match + active status), resolved_count (window-filtered against `resolved_at`, `resolved_by` match). Result passed to template as `team_rows`.
  - Compute open-ticket badge total (used by the header) and expose via `g` or pass into `base.html` as a global. (Implementation detail: a `@app.context_processor` returning `{'open_ticket_count': ...}` when `g.current_user` is set.)
- **No new top-level routes added.**

## 6. Visual polish pass

CSS-only changes to `static/style.css`. No HTML/layout changes outside sections 1–3.

- **Forms** (existing `form.stacked`, license/recurring/event forms): adopt the uppercase tracked-out label style from the new ticket form. Same border/radius/padding.
- **Tables** (Tickets queue, Licenses list, Recurring list, dashboard aging widget): unified header style (uppercase 0.7rem labels, `#6b7280`), row hover `#f9fafb`, `font-variant-numeric: tabular-nums` on numeric columns, last-row no bottom border.
- **Buttons**: all primary buttons use `#2563eb`. Secondary buttons unified to transparent bg + `#d1d5db` border + `#4b5563` text.
- **Stat cards** (dashboard): tighter padding, uppercase 0.7rem labels, `#1f2937` 1.7rem value, warning state stays amber.
- **Badge** style for the Tickets open-count: red `#ef4444` pill, white text, 0.68rem, 999px radius.

No new colors introduced beyond what's already in the stylesheet plus the badge red.

## Out of scope

- Email features (Pass 4 / 5).
- Avg-time-to-resolve metric (would need the audit log we're skipping).
- Per-staff drill-down pages.
- Configurable time windows or custom date pickers.
- Search.
- Pagination on the queue (still fine at current ticket volume).
