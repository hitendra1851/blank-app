"""
service.py - Microsoft Teams Chat Export background service.

Usage:
  python service.py            # Run once and exit
  python service.py --loop     # Run every N minutes (from config.json)
  python service.py --status   # Show last export state
  python service.py --reset    # Clear state (re-export everything)

Designed to be called by Windows Task Scheduler on a schedule.
See install.bat to register it automatically.
"""

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

# ---------------------------------------------------------------------------
# Bootstrap: ensure script directory is in path when run from Task Scheduler
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).parent.resolve()
CONFIG_PATH = BASE_DIR / "config.json"
sys.path.insert(0, str(BASE_DIR))

import mailer
import reader

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def _setup_logging(log_file: str) -> None:
    log_path = BASE_DIR / log_file
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(str(log_path), encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )

logger = logging.getLogger("teams_export_service")


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def load_config() -> Dict[str, Any]:
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(
            f"config.json not found at {CONFIG_PATH}. "
            "Copy config.json.example and fill in your settings."
        )
    with open(CONFIG_PATH, encoding="utf-8") as f:
        cfg = json.load(f)
    return cfg


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------

def load_state(state_file: str) -> Dict[str, Any]:
    path = BASE_DIR / state_file
    if path.exists():
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {"last_timestamp": None, "last_run": None, "total_sent": 0}


def save_state(state_file: str, state: Dict[str, Any]) -> None:
    path = BASE_DIR / state_file
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


# ---------------------------------------------------------------------------
# Core export-and-send cycle
# ---------------------------------------------------------------------------

def run_once(cfg: Dict[str, Any]) -> bool:
    """
    Extract new Teams messages and email them.
    Returns True if the run completed without errors.
    """
    svc_cfg = cfg.get("service", {})
    teams_cfg = cfg.get("teams", {})
    email_cfg = cfg.get("email", {})
    state_file = svc_cfg.get("state_file", "last_export_state.json")

    state = load_state(state_file)
    since = state.get("last_timestamp")
    logger.info("Starting export. Last timestamp: %s", since or "none (first run)")

    # 1. Extract messages from local Teams cache
    try:
        all_messages = reader.extract_messages(
            custom_path=teams_cfg.get("custom_path"),
            copy_to_temp=teams_cfg.get("copy_to_temp", True),
        )
    except RuntimeError as exc:
        logger.error("Reader error: %s", exc)
        return False

    if not all_messages:
        logger.info("No messages found in Teams cache.")
        save_state(state_file, {**state, "last_run": _now()})
        return True

    # 2. Filter to only new messages
    new_messages = reader.filter_new_messages(all_messages, since)
    logger.info(
        "Total cached: %d | New since last run: %d",
        len(all_messages), len(new_messages),
    )

    # 3. Cap per-email if configured
    max_per_email = int(svc_cfg.get("max_messages_per_email", 100))
    chunk = new_messages[:max_per_email]

    # 4. Send email
    if chunk:
        ok = mailer.send_export(chunk, email_cfg)
        if not ok:
            return False
    else:
        logger.info("No new messages since last run. Nothing sent.")

    # 5. Update state with the latest timestamp seen
    all_timestamps = [
        msg.get("originalarrivaltime") or msg.get("composetime", "")
        for msg in all_messages
    ]
    all_timestamps = [t for t in all_timestamps if t]
    new_ts = max(all_timestamps) if all_timestamps else since

    save_state(state_file, {
        "last_timestamp": new_ts,
        "last_run": _now(),
        "total_sent": state.get("total_sent", 0) + len(chunk),
    })
    return True


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Teams Chat Export Service")
    parser.add_argument("--loop", action="store_true", help="Run in a loop")
    parser.add_argument("--status", action="store_true", help="Show last state")
    parser.add_argument("--reset", action="store_true", help="Clear state")
    args = parser.parse_args()

    cfg = load_config()
    svc_cfg = cfg.get("service", {})
    _setup_logging(svc_cfg.get("log_file", "teams_export.log"))

    state_file = svc_cfg.get("state_file", "last_export_state.json")

    if args.status:
        state = load_state(state_file)
        print(json.dumps(state, indent=2))
        return

    if args.reset:
        save_state(state_file, {"last_timestamp": None, "last_run": None, "total_sent": 0})
        logger.info("State reset. Next run will export all cached messages.")
        return

    if args.loop:
        interval = int(svc_cfg.get("check_interval_minutes", 30)) * 60
        logger.info("Running in loop mode. Interval: %d seconds.", interval)
        while True:
            try:
                run_once(cfg)
            except Exception as exc:
                logger.exception("Unexpected error in run_once: %s", exc)
            logger.info("Sleeping %d seconds until next check.", interval)
            time.sleep(interval)
    else:
        # Single run (default for Task Scheduler)
        success = run_once(cfg)
        sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
