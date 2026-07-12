import os
import re
import uuid
import logging
import asyncio
import tempfile
from pathlib import Path

import yt_dlp
from updater import auto_update_loop, update_ytdlp
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQueryResultArticle,
    InputMediaVideo,
    InputTextMessageContent,
)
from telegram.ext import (
    Application,
    CommandHandler,
    ChosenInlineResultHandler,
    MessageHandler,
    InlineQueryHandler,
    ContextTypes,
    filters,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
ADMIN_IDS = {
    int(x) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip().isdigit()
}

URL_PATTERN = re.compile(
    r"https?://"
    r"(?:(?:www|m)\.)?"
    r"(?:"
    r"(?:youtube\.com/shorts/[\w-]+)"
    r"|(?:youtu\.be/[\w-]+)"
    r"|(?:youtube\.com/watch\?[^\s]*v=[\w-]+)"
    r"|(?:instagram\.com/(?:reels?|p)/[\w-]+)"
    r"|(?:vm\.tiktok\.com/[\w-]+)"
    r"|(?:tiktok\.com/@[\w.]+/video/\d+)"
    r")"
    r"[^\s]*",
    re.IGNORECASE,
)

MAX_FILE_SIZE_MB = 50  # Telegram Bot API limit

COOKIES_FILE = os.environ.get("COOKIES_FILE", "/cookies/cookies.txt")


def _base_ydl_opts(output_template: str, use_cookies: bool = True) -> dict:
    opts = {
        "format": "bv*[vcodec^=avc1]+ba[ext=m4a]/b[ext=mp4]/b",
        "outtmpl": output_template,
        "merge_output_format": "mp4",
        "quiet": True,
        "no_warnings": True,
        # Use Deno to solve YouTube's n-challenge (required on server IPs).
        # The solver script is downloaded from GitHub once and cached.
        "js_runtimes": {"deno": {}},
        "remote_components": {"ejs:github"},
    }
    if not use_cookies:
        return opts
    if os.path.isfile(COOKIES_FILE):
        opts["cookiefile"] = COOKIES_FILE
        logger.info("Using cookies from %s", COOKIES_FILE)
    else:
        logger.warning("cookies.txt not found at %s — YouTube may block downloads", COOKIES_FILE)
    return opts


AUTH_ERROR_MARKERS = ("sign in", "not a bot", "login required", "authentication", "cookies")


def _is_auth_error(msg: str) -> bool:
    m = msg.lower()
    return any(marker in m for marker in AUTH_ERROR_MARKERS)


def extract_url(text: str) -> str | None:
    """Extract first supported video URL from text."""
    match = URL_PATTERN.search(text)
    return match.group(0) if match else None


async def download_video(
    url: str, output_dir: str, use_cookies: bool = True
) -> tuple[str | None, str | None, int | None, int | None]:
    """
    Download video using yt-dlp, trying format presets in order.
    Returns (file_path, title, width, height) or (None, error_message, None, None).
    """
    output_template = os.path.join(output_dir, "%(id)s.%(ext)s")
    loop = asyncio.get_event_loop()

    # Tried in order; falls back to next on "format not available".
    # [aspect_ratio<1] explicitly selects portrait (width < height) streams.
    format_attempts = [
        "bv*[vcodec^=avc1][aspect_ratio<1]+ba[ext=m4a]/bv*[vcodec^=avc1]+ba[ext=m4a]/b[ext=mp4]/b",
        "bestvideo[aspect_ratio<1]+bestaudio/bestvideo+bestaudio/best",
        "best",
    ]

    def _download(fmt: str) -> tuple[str, str, int | None, int | None]:
        opts = _base_ydl_opts(output_template, use_cookies)
        opts["format"] = fmt
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            if not os.path.exists(filename):
                stem = Path(filename).stem
                for f in Path(output_dir).glob(f"{stem}.*"):
                    filename = str(f)
                    break
            return filename, info.get("title", "video"), info.get("width"), info.get("height")

    filename = title = None
    width = height = None
    last_err = ""
    try:
        for fmt in format_attempts:
            try:
                filename, title, width, height = await loop.run_in_executor(None, _download, fmt)
                break
            except yt_dlp.utils.DownloadError as e:
                last_err = str(e)
                if "requested format is not available" in last_err.lower():
                    logger.warning("Format %r unavailable, trying fallback", fmt)
                    continue
                raise
        else:
            raise yt_dlp.utils.DownloadError(last_err)
    except yt_dlp.utils.DownloadError as e:
        msg = str(e)
        logger.error("DownloadError for %s: %s", url, msg)
        if "sign in" in msg.lower() or "bot" in msg.lower():
            return None, "YouTube requires authentication. Update cookies via /setcookies.", None, None
        if "private" in msg.lower():
            return None, "Video is private.", None, None
        if "removed" in msg.lower() or "deleted" in msg.lower():
            return None, "Video has been deleted.", None, None
        if "not available in your country" in msg.lower():
            return None, "Video is not available in your region.", None, None
        return None, f"Download error:\n{msg[-300:]}", None, None
    except Exception as e:
        logger.error("Unexpected error for %s: %s", url, e)
        return None, f"Unexpected error: {e}", None, None

    if not os.path.exists(filename):
        return None, "File not found after download.", None, None
    size_mb = os.path.getsize(filename) / (1024 * 1024)
    if size_mb > MAX_FILE_SIZE_MB:
        os.remove(filename)
        return None, f"Video is too large ({size_mb:.1f} MB). Telegram limit is {MAX_FILE_SIZE_MB} MB.", None, None
    return filename, title, width, height


async def download_with_cookie_fallback(
    url: str, output_dir: str
) -> tuple[str | None, str | None, int | None, int | None, bool]:
    """
    Download with cookies; if that fails with an auth error, retry without them.
    Returns (file_path, title_or_error, width, height, cookies_expired).
    """
    filepath, result, width, height = await download_video(url, output_dir)
    if (
        filepath is not None
        or not os.path.isfile(COOKIES_FILE)
        or not _is_auth_error(result or "")
    ):
        return filepath, result, width, height, False

    logger.warning("Auth error with cookies for %s — retrying without cookies", url)
    filepath, result, width, height = await download_video(url, output_dir, use_cookies=False)
    return filepath, result, width, height, True


async def notify_cookies_expired(bot, user_id: int) -> None:
    """DM the user that cookies.txt is stale. Best-effort: user may not have started the bot."""
    try:
        await bot.send_message(
            chat_id=user_id,
            text=(
                "⚠️ YouTube cookies have expired — I retried the download without them.\n"
                "Please upload a fresh cookies.txt via /setcookies."
            ),
        )
    except Exception as e:
        logger.warning("Could not notify user %s about expired cookies: %s", user_id, e)


# ── /setcookies — upload cookies.txt directly as a file ──────────────────────

def _is_admin(user_id: int) -> bool:
    return not ADMIN_IDS or user_id in ADMIN_IDS

async def setcookies_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin: upload a cookies.txt file (Netscape format) to the bot."""
    if not _is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Admins only.")
        return

    doc = update.message.document
    if not doc:
        await update.message.reply_text(
            "Attach your cookies.txt file to this message.\n\n"
            "How to get it:\n"
            "1. Install the 'Get cookies.txt LOCALLY' extension (Chrome/Firefox)\n"
            "2. Go to youtube.com\n"
            "3. Click the extension → Export → save the file\n"
            "4. Send it here with /setcookies"
        )
        return

    file = await doc.get_file()
    data = await file.download_as_bytearray()

    from pathlib import Path
    dest = Path(COOKIES_FILE)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)

    await update.message.reply_text(
        f"✅ cookies.txt saved ({len(data)} bytes). Bot is ready."
    )


# ── Direct chat handlers ──────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Hey! 👋\n\n"
        "Send me a YouTube Shorts or Instagram Reels link and I'll download and send the video.\n\n"
        "Also works in inline mode: type "
        f"@{context.bot.username} <link> in any chat."
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text or ""
    url = extract_url(text)
    if not url:
        await update.message.reply_text(
            "Send me a YouTube Shorts or Instagram Reels link."
        )
        return

    status_msg = await update.message.reply_text("⏳ Downloading...")

    with tempfile.TemporaryDirectory() as tmpdir:
        filepath, result, width, height, cookies_expired = await download_with_cookie_fallback(
            url, tmpdir
        )
        if cookies_expired:
            await notify_cookies_expired(context.bot, update.effective_user.id)
        if filepath is None:
            await status_msg.edit_text(f"❌ {result}")
            return

        title = result
        try:
            with open(filepath, "rb") as f:
                await update.message.reply_video(
                    video=f,
                    caption=title,
                    supports_streaming=True,
                    width=width,
                    height=height,
                )
            await status_msg.delete()
        except Exception as e:
            logger.error("Failed to send video: %s", e)
            await status_msg.edit_text("❌ Failed to send video.")


# ── Inline query handler ──────────────────────────────────────────────────────

async def inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.inline_query.query.strip()
    url = extract_url(query)

    if not url:
        await update.inline_query.answer(
            [
                InlineQueryResultArticle(
                    id="help",
                    title="Paste a video link",
                    description="YouTube Shorts or Instagram Reels",
                    input_message_content=InputTextMessageContent(
                        "Supported: YouTube Shorts and Instagram Reels."
                    ),
                )
            ],
            cache_time=60,
        )
        return

    # Reply immediately without fetching metadata — the actual download happens
    # in chosen_inline_result after the user picks this result.
    # reply_markup is required to receive inline_message_id in chosen_inline_result.
    await update.inline_query.answer(
        [
            InlineQueryResultArticle(
                id=str(uuid.uuid4()),
                title="Download video",
                description="Tap — the bot will download and send the video to this chat",
                input_message_content=InputTextMessageContent("⏳ Downloading video..."),
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("⏳ Downloading...", callback_data="noop")]]
                ),
            )
        ],
        cache_time=0,
    )


async def chosen_inline_result(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    result = update.chosen_inline_result
    inline_message_id = result.inline_message_id
    user_id = result.from_user.id
    url = extract_url(result.query)

    if not inline_message_id or not url:
        return

    with tempfile.TemporaryDirectory() as tmpdir:
        filepath, res, width, height, cookies_expired = await download_with_cookie_fallback(
            url, tmpdir
        )
        if cookies_expired:
            await notify_cookies_expired(context.bot, user_id)

        if filepath is None:
            await context.bot.edit_message_text(
                inline_message_id=inline_message_id,
                text=f"❌ {res}",
            )
            return

        title = res
        try:
            # Telegram forbids uploading new files via inline_message_id.
            # Workaround: upload to user's DM first → get file_id → edit inline msg.
            with open(filepath, "rb") as f:
                dm_msg = await context.bot.send_video(
                    chat_id=user_id,
                    video=f,
                    caption=title,
                    supports_streaming=True,
                    width=width,
                    height=height,
                )
            file_id = dm_msg.video.file_id

            await context.bot.edit_message_media(
                inline_message_id=inline_message_id,
                media=InputMediaVideo(
                    media=file_id,
                    caption=title,
                    supports_streaming=True,
                    width=width,
                    height=height,
                ),
            )
        except Exception as e:
            logger.error("Failed to send inline video: %s", e)
            err = str(e)
            if "bot can't initiate conversation" in err or "chat not found" in err.lower():
                await context.bot.edit_message_text(
                    inline_message_id=inline_message_id,
                    text="❌ Send /start to the bot in a private chat first — it's required to deliver videos to groups.",
                )
            else:
                await context.bot.edit_message_text(
                    inline_message_id=inline_message_id,
                    text=f"❌ Error: {e}",
                )


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN is not set. Set the BOT_TOKEN environment variable."
        )

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("setcookies", setcookies_command))
    # Any document sent by an admin is treated as a cookies.txt upload
    app.add_handler(MessageHandler(filters.Document.ALL, setcookies_command))
    app.add_handler(InlineQueryHandler(inline_query))
    app.add_handler(ChosenInlineResultHandler(chosen_inline_result))
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
    )

    # Update yt-dlp once on startup, then schedule daily checks
    logger.info("Checking yt-dlp version on startup…")
    update_ytdlp()

    async def on_startup(app: Application) -> None:
        asyncio.create_task(auto_update_loop())

    app.post_init = on_startup

    logger.info("Bot started.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
