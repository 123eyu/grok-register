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
    path = None
    try:
        screenshot = getattr(page, "get_screenshot", None)
        if not callable(screenshot):
            return ""

        directory = Path(screenshot_dir) if screenshot_dir else (
            Path(__file__).resolve().parent / "data" / "diagnostics" / "registration"
        )
        directory.mkdir(parents=True, exist_ok=True)
        if os.name == "posix":
            os.chmod(directory, 0o700)

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
        screenshot(path=str(path), full_page=True)

        if not path.is_file():
            return ""
        if path.stat().st_size == 0:
            path.unlink()
            return ""
        if os.name == "posix":
            os.chmod(path, 0o600)
        return str(path.resolve())
    except Exception as exc:
        if path is not None:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        if log_callback:
            log_callback("[Debug] 注册失败截图未保存: %s" % type(exc).__name__)
        return ""
