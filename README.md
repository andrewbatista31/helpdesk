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

## Create your first staff user

Before anyone can sign in, create at least one user from the command line:

```powershell
venv\Scripts\Activate.ps1
python app.py create-user alex
```

You'll be prompted for a password (min 8 chars). Repeat for each IT staff member.

Other user commands:

```powershell
python app.py list-users
python app.py delete-user alex
```

## What's in passes 1 & 2

- `/` — public form anyone can submit a ticket through (no login required).
- `/login` — staff sign-in. `/staff` and ticket pages require a session.
- `/staff` — IT dashboard. Filter by status, click a row to open a ticket,
  change status/priority/assignee, add public comments or internal notes.
- Passwords are bcrypt-hashed in a `users` table.
- The Flask session secret is generated on first run and stored in
  `.flask_secret` (gitignored). Override by setting `HELPDESK_SECRET_KEY`.
- SQLite database `helpdesk.db` is created automatically on first run.

## What's next

- Pass 3: email-to-ticket polling from your shared mailbox over IMAP.
- Pass 4: outbound email notifications via SMTP, plus running as a Windows
  Service via NSSM + waitress.
