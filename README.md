# Helpdesk (pass 1)

Internal IT ticketing system. Flask + SQLite.

## Setup

1. Install Python 3.11+ (check **Add to PATH** during install).
2. Open PowerShell in this folder and run:

   ```powershell
   python -m venv venv
   venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   python app.py
   ```

   (If activation is blocked by execution policy, run once:
   `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.)

3. Visit `http://localhost:5000/` on the server, or
   `http://<server-name>:5000/` from another machine on the LAN.

4. Allow inbound TCP 5000 in Windows Firewall to let other machines reach it:

   ```powershell
   New-NetFirewallRule -DisplayName "Helpdesk 5000" -Direction Inbound -Protocol TCP -LocalPort 5000 -Action Allow
   ```

## What's in pass 1

- `/` — public form anyone can submit a ticket through.
- `/staff` — IT dashboard. Filter by status, click a row to open a ticket,
  change status/priority/assignee, add public comments or internal notes.
- SQLite database `helpdesk.db` is created automatically on first run.

## Important caveat

Pass 1 has **no authentication**. Anyone who can reach the server can see `/staff`.
Keep it on the LAN until pass 2 adds login.

## What's next

- Pass 2: staff login (bcrypt-hashed passwords, sessions, `@login_required`).
- Pass 3: email-to-ticket polling from your shared mailbox over IMAP.
- Pass 4: outbound email notifications via SMTP, plus running as a Windows
  Service via NSSM + waitress.
