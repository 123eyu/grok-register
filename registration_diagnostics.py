"""Capture registration browser failure screenshots without affecting task results."""

from datetime import datetime, timezone
import os
from pathlib import Path
import re


def _filename_part(value):
    return re.sub(r"[^A-Za-z0-9_-]+", "_", str(value or "na"))[:48] or "na"


def capture_failure_screenshot(
    page,
    registration_run_id,
    details,
    log_callback=None,
    screenshot_dir=None,
):
    """Save a private screenshot for one failed attempt; return an empty path on failure."""
    screenshot = getattr(page, "get_screenshot", None)
    if not callable(screenshot):
        return ""

    directory = Path(screenshot_dir) if screenshot_dir else (
        Path(__file__).resolve().parent / "data" / "diagnostics" / "registration"
    )
    try:
        directory.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(directory, 0o700)
        except OSError:
            pass

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        worker = _filename_part(details.get("worker_key"))
        name = "run%s_slot%s_attempt%s_%s_%s.png" % (
            _filename_part(registration_run_id),
            _filename_part(details.get("slot_index")),
            _filename_part(details.get("attempt_number")),
            worker,
            timestamp,
        )
        path = directory / name
        try:
            screenshot(path=str(path), full_page=True)
        except TypeError:
            screenshot(path=str(path))

        if not path.is_file() or path.stat().st_size == 0:
            return ""
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        return str(path.resolve())
    except Exception as exc:
        if log_callback:
            log_callback("[Debug] 注册失败截图未保存: %s" % type(exc).__name__)
        return ""
