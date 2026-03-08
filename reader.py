"""
reader.py - Microsoft Teams local LevelDB cache reader.

Supports:
  - Classic Teams  (%APPDATA%\\Microsoft\\Teams\\...)
  - New Teams 2.0  (%LOCALAPPDATA%\\Packages\\MSTeams_8wekyb3d8bbwe\\...)

Strategy:
  1. Copy the LevelDB directory to a temp folder (avoids file-lock conflicts
     while Teams is running).
  2. Open the copy with plyvel and iterate every record.
  3. Extract embedded strings from V8-serialised values.
  4. Detect and JSON-parse Teams message objects from those strings.
"""

import os
import re
import json
import shutil
import struct
import logging
import tempfile
from pathlib import Path
from typing import Iterator, List, Dict, Any, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Teams data directories
# ---------------------------------------------------------------------------

def _env(name: str) -> Path:
    return Path(os.environ.get(name, ""))


CLASSIC_TEAMS_INDEXEDDB = (
    _env("APPDATA") / "Microsoft" / "Teams" / "IndexedDB"
)

NEW_TEAMS_BASES = [
    _env("LOCALAPPDATA") / "Packages" / "MSTeams_8wekyb3d8bbwe"
    / "LocalCache" / "Local" / "Microsoft" / "MSTeams",
    _env("LOCALAPPDATA") / "Microsoft" / "Teams",
]


def find_leveldb_dirs(custom_path: Optional[str] = None) -> List[Path]:
    """Return a list of LevelDB directories found for Teams installations."""
    dirs: List[Path] = []

    if custom_path:
        p = Path(custom_path)
        if p.is_dir():
            dirs.extend(p.rglob("*.leveldb"))
        return dirs

    # Classic Teams
    if CLASSIC_TEAMS_INDEXEDDB.is_dir():
        for p in CLASSIC_TEAMS_INDEXEDDB.iterdir():
            if p.is_dir() and p.suffix == ".leveldb":
                dirs.append(p)
        logger.info("Classic Teams LevelDB: %s", [str(d) for d in dirs])

    # New Teams
    for base in NEW_TEAMS_BASES:
        if base.is_dir():
            for p in base.rglob("*.leveldb"):
                if p not in dirs:
                    dirs.append(p)
            logger.info("New Teams base found: %s", base)

    return dirs


# ---------------------------------------------------------------------------
# Safe copy (works even when Teams holds a read lock on the files)
# ---------------------------------------------------------------------------

def safe_copy_leveldb(src: Path, dest_root: Path) -> Optional[Path]:
    """
    Copy a LevelDB directory to dest_root/<src.name>, skipping files that
    cannot be read (e.g. LOCK file held exclusively by Teams).
    Returns the destination path, or None if nothing could be copied.
    """
    dest = dest_root / src.name
    dest.mkdir(parents=True, exist_ok=True)
    copied = 0
    for item in src.iterdir():
        try:
            shutil.copy2(str(item), str(dest / item.name))
            copied += 1
        except (PermissionError, OSError) as exc:
            logger.debug("Skipping %s: %s", item.name, exc)
    return dest if copied > 0 else None


# ---------------------------------------------------------------------------
# V8 serialisation string extractor
# ---------------------------------------------------------------------------

def _read_varint(data: bytes, pos: int):
    """Read a base-128 varint. Returns (value, bytes_consumed)."""
    result = 0
    shift = 0
    while pos < len(data):
        b = data[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            break
        shift += 7
    return result, pos


def _extract_v8_strings(data: bytes) -> List[str]:
    """
    Walk V8-serialised bytes and pull out embedded string content.

    V8 wire types relevant here:
      0x22  – one-byte (Latin-1) string:  varint length, then raw bytes
      0x63  – two-byte (UTF-16LE) string: varint char-count, then UTF-16LE
      0x22 is also used inside Blink's SerializedScriptValue wrapper.

    We scan for these tag bytes rather than doing a full parse, so we work
    with any Blink version.
    """
    strings: List[str] = []
    i = 0
    n = len(data)

    while i < n:
        tag = data[i]
        i += 1

        if tag == 0x22:  # one-byte string
            length, i = _read_varint(data, i)
            if 0 < length <= 1_000_000 and i + length <= n:
                try:
                    s = data[i : i + length].decode("latin-1")
                    strings.append(s)
                except Exception:
                    pass
                i += length

        elif tag == 0x63:  # two-byte string
            char_count, i = _read_varint(data, i)
            byte_len = char_count * 2
            if 0 < byte_len <= 2_000_000 and i + byte_len <= n:
                try:
                    s = data[i : i + byte_len].decode("utf-16-le")
                    strings.append(s)
                except Exception:
                    pass
                i += byte_len
        # other tags: advance 1 byte (best-effort scan)

    return strings


# ---------------------------------------------------------------------------
# JSON message detection
# ---------------------------------------------------------------------------

# Fields that identify a Teams chat message record
_MSG_REQUIRED = {"messageType", "content"}
_MSG_OPTIONAL = {"from", "body", "id", "composetime", "originalarrivaltime"}

# Regex to grab top-level JSON objects quickly before full parse
_JSON_RE = re.compile(r"\{[^\{\}]{20,}\}", re.DOTALL)


def _parse_message_from_string(s: str) -> Optional[Dict[str, Any]]:
    """Return a parsed Teams message dict if s looks like one, else None."""
    if "messageType" not in s:
        return None
    for match in _JSON_RE.finditer(s):
        try:
            obj = json.loads(match.group())
            if _MSG_REQUIRED.issubset(obj):
                return obj
        except json.JSONDecodeError:
            pass

    # Fallback: try the whole string as JSON
    try:
        obj = json.loads(s)
        if isinstance(obj, dict) and _MSG_REQUIRED.issubset(obj):
            return obj
    except json.JSONDecodeError:
        pass

    return None


# ---------------------------------------------------------------------------
# LevelDB iteration (requires plyvel)
# ---------------------------------------------------------------------------

def _iter_leveldb(db_path: Path) -> Iterator[bytes]:
    """Yield all values from a LevelDB at db_path using plyvel."""
    try:
        import plyvel  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "plyvel is not installed. Run: pip install plyvel-wheels"
        ) from exc

    try:
        db = plyvel.DB(str(db_path), create_if_missing=False)
        try:
            for _key, value in db:
                yield value
        finally:
            db.close()
    except Exception as exc:
        logger.warning("Cannot open LevelDB at %s: %s", db_path, exc)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract_messages(
    custom_path: Optional[str] = None,
    copy_to_temp: bool = True,
) -> List[Dict[str, Any]]:
    """
    Locate all Teams LevelDB directories, read them, and return a list of
    parsed message dicts.

    Each dict will contain at minimum:
      messageType, content
    and optionally:
      from, id, composetime, originalarrivaltime, threadType, ...
    """
    leveldb_dirs = find_leveldb_dirs(custom_path)
    if not leveldb_dirs:
        logger.warning("No Teams LevelDB directories found.")
        return []

    messages: List[Dict[str, Any]] = []
    seen_ids: set = set()

    with tempfile.TemporaryDirectory(prefix="teams_export_") as tmp:
        tmp_path = Path(tmp)

        for db_dir in leveldb_dirs:
            if copy_to_temp:
                work_dir = safe_copy_leveldb(db_dir, tmp_path)
                if work_dir is None:
                    logger.warning("Nothing copied from %s, skipping.", db_dir)
                    continue
            else:
                work_dir = db_dir

            logger.info("Reading LevelDB: %s", work_dir)

            for value_bytes in _iter_leveldb(work_dir):
                strings = _extract_v8_strings(value_bytes)
                for s in strings:
                    msg = _parse_message_from_string(s)
                    if msg is None:
                        continue
                    msg_id = msg.get("id") or msg.get("clientmessageid") or s[:64]
                    if msg_id in seen_ids:
                        continue
                    seen_ids.add(msg_id)
                    messages.append(msg)

    logger.info("Extracted %d unique messages.", len(messages))
    return messages


def filter_new_messages(
    messages: List[Dict[str, Any]],
    since_timestamp: Optional[str],
) -> List[Dict[str, Any]]:
    """
    Return only messages newer than since_timestamp.
    Timestamp field tried in order: originalarrivaltime, composetime.
    Both are ISO-8601 strings like '2024-01-15T10:30:00.000Z'.
    """
    if not since_timestamp:
        return messages

    result = []
    for msg in messages:
        ts = msg.get("originalarrivaltime") or msg.get("composetime", "")
        if ts > since_timestamp:
            result.append(msg)
    return result
