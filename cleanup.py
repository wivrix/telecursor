"""Temp upload cleanup helpers."""

from __future__ import annotations

import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_MAX_AGE_HOURS = 4.0


def cleanup_stale_uploads(
    temp_dir: Path,
    *,
    max_age_hours: float = DEFAULT_MAX_AGE_HOURS,
) -> int:
    """Delete files in temp_dir older than max_age_hours. Returns count removed."""
    if not temp_dir.is_dir():
        return 0
    cutoff = time.time() - (max_age_hours * 3600.0)
    removed = 0
    try:
        entries = list(temp_dir.iterdir())
    except OSError as exc:
        logger.warning("Cannot list temp dir %s: %s", temp_dir, exc)
        return 0
    for entry in entries:
        try:
            if not entry.is_file():
                continue
            if entry.stat().st_mtime >= cutoff:
                continue
            entry.unlink(missing_ok=True)
            removed += 1
        except OSError as exc:
            logger.debug("Failed to remove stale temp %s: %s", entry, exc)
    if removed:
        logger.info("Removed %s stale temp file(s) from %s", removed, temp_dir)
    return removed
