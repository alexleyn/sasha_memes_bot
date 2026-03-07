"""
Auto-updater for yt-dlp.
Runs as a background asyncio task, checks for updates every UPDATE_INTERVAL seconds.
If a new version is installed, restarts the entire process via os.execv.
"""

import asyncio
import logging
import os
import subprocess
import sys

logger = logging.getLogger(__name__)

UPDATE_INTERVAL = 24 * 60 * 60  # 24 hours


def update_ytdlp() -> bool:
    """
    Run pip install -U yt-dlp.
    Returns True if a new version was installed.
    """
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-U", "yt-dlp", "--quiet"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        logger.error("yt-dlp update failed:\n%s", result.stderr)
        return False

    # pip prints "Successfully installed yt-dlp-XXXX" only on actual upgrade
    upgraded = "Successfully installed" in result.stdout
    if upgraded:
        logger.info("yt-dlp upgraded: %s", result.stdout.strip())
    else:
        logger.info("yt-dlp is already up to date.")
    return upgraded


async def auto_update_loop(first_delay: int = UPDATE_INTERVAL) -> None:
    """
    Asyncio task: wait, update, restart process if updated.
    first_delay lets you stagger the first check (default: full interval).
    """
    await asyncio.sleep(first_delay)
    while True:
        try:
            upgraded = await asyncio.get_event_loop().run_in_executor(
                None, update_ytdlp
            )
            if upgraded:
                logger.info("Restarting bot process to apply yt-dlp update…")
                # Replace current process — Docker restart policy keeps it alive
                os.execv(sys.executable, [sys.executable] + sys.argv)
        except Exception:
            logger.exception("Unexpected error in auto-update loop")
        await asyncio.sleep(UPDATE_INTERVAL)
