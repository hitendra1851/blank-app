# Microsoft Teams Chat Export Service

A lightweight Python background service that reads your Microsoft Teams local cache and emails new chat messages to a configured address. No UI. Designed to run silently via **Windows Task Scheduler**.

---

## How it works

1. Teams stores all chat data locally in a LevelDB (Chromium IndexedDB) directory.
2. The service copies those files to a temp folder (avoiding file-lock conflicts while Teams is open).
3. It parses embedded V8-serialised message objects from the raw bytes.
4. New messages (since last run) are emailed as an HTML table + JSON attachment.
5. State is persisted in `last_export_state.json` so only new messages are sent each cycle.

Supports both **Classic Teams** (`%APPDATA%\Microsoft\Teams\...`) and **New Teams 2.0** (`%LOCALAPPDATA%\Packages\MSTeams_8wekyb3d8bbwe\...`).

---

## Quick start

### 1. Configure

Edit `config.json`:

```json
{
  "email": {
    "smtp_host": "smtp.gmail.com",
    "smtp_port": 587,
    "use_tls": true,
    "sender": "you@gmail.com",
    "password": "your_gmail_app_password",
    "recipient": "destination@example.com"
  },
  "service": {
    "check_interval_minutes": 30
  }
}
```

> **Gmail users**: Use an [App Password](https://myaccount.google.com/apppasswords), not your real password (requires 2FA to be enabled).

### 2. Install

Run `install.bat` as **Administrator** — it installs dependencies and registers the Task Scheduler entry.

```
Right-click install.bat -> Run as administrator
```

### 3. Done

The service will run every 30 minutes automatically. To verify:

```cmd
python service.py --status
```

---

## Manual usage

```cmd
python service.py           # Run once
python service.py --loop    # Run in a loop (no Task Scheduler needed)
python service.py --status  # Show last export info
python service.py --reset   # Clear state (re-export all cached messages)
```

---

## File structure

```
service.py                  Main service entry point
reader.py                   Teams LevelDB cache reader & message parser
mailer.py                   SMTP email sender
config.json                 Your settings (edit this)
install.bat                 Task Scheduler registration script
requirements.txt            Python dependencies
last_export_state.json      Auto-created; tracks last export timestamp
teams_export.log            Auto-created; rolling log file
```

---

## Uninstall

```cmd
schtasks /delete /tn TeamsChatExportService /f
```
