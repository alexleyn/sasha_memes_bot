# sasha_memes_bot

A Telegram bot that downloads YouTube Shorts and Instagram Reels and sends them back as videos. Supports both direct messages and inline mode.

## Features

- Download YouTube Shorts and Instagram Reels by URL
- Inline mode: `@botname <url>` in any chat
- Auto-updates yt-dlp daily without rebuilding the container
- Cookie-based YouTube authentication to bypass bot detection
- Browser-based Google login via Playwright (headless Chromium)
- Solves YouTube's JS n-challenge via Node.js

## Setup

### 1. Create a Telegram bot

1. Talk to [@BotFather](https://t.me/BotFather) and create a bot
2. Copy the token
3. Enable inline mode: `/setinline`
4. Enable inline feedback at 100%: `/setinlinefeedback`

### 2. Configure environment

Create a `.env` file:

```env
BOT_TOKEN=your_telegram_bot_token
ADMIN_IDS=123456789,987654321
COOKIES_FILE=/cookies/cookies.txt
```

- `BOT_TOKEN` — required
- `ADMIN_IDS` — comma-separated Telegram user IDs allowed to use admin commands (`/login`, `/setcookies`). If empty, all users are admins.
- `COOKIES_FILE` — path inside the container (default: `/cookies/cookies.txt`)

### 3. Run with Docker Compose

```bash
docker compose up -d --build
```

### 4. Provide YouTube cookies (required)

YouTube blocks downloads from server IPs without valid cookies. Two options:

**Option A — Upload cookies file:**

1. Install the [Get cookies.txt LOCALLY](https://chrome.google.com/webstore/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc) extension
2. Log in to [youtube.com](https://youtube.com)
3. Click the extension and export cookies
4. Send the file to the bot with `/setcookies`

**Option B — Browser login via bot:**

Send `/login` to the bot. It will launch a headless Chromium browser and send screenshots as it goes. Follow the prompts to log in with your Google account.

## Commands

| Command | Description |
|---|---|
| `/start` | Welcome message |
| `/setcookies` | Upload a `cookies.txt` file (attach as document) |
| `/login` | Start browser-based Google login (admin only) |
| `/cancel` | Cancel an in-progress login |
| `/done` | Save cookies and finish login at current step |

## Architecture

| File | Purpose |
|---|---|
| `bot.py` | Main bot: handlers, download logic, inline mode |
| `updater.py` | Background task: updates yt-dlp daily, restarts process if updated |
| `cookies_manager.py` | Playwright browser session for Google login and cookie export |
| `Dockerfile` | Python 3.13 + ffmpeg + Node.js + Playwright Chromium |
| `docker-compose.yml` | Service definition with named volumes for cookies and yt-dlp cache |

## Volumes

| Volume | Mount | Purpose |
|---|---|---|
| `cookies` | `/cookies` | Persistent storage for `cookies.txt` |
| `ytdlp_cache` | `/home/botuser/.cache/yt-dlp` | yt-dlp JS solver cache (avoids re-downloading on restart) |

## Supported URLs

- `youtube.com/shorts/<id>`
- `youtu.be/<id>`
- `youtube.com/watch?v=<id>`
- `m.youtube.com/shorts/<id>`
- `instagram.com/reel/<id>`
- `instagram.com/p/<id>`
- `tiktok.com/@<user>/video/<id>`
- `vm.tiktok.com/<id>`
