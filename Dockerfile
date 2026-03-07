FROM python:3.13-slim

# Playwright browsers will be stored here (accessible to all users)
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# ffmpeg — required by yt-dlp to merge video+audio streams
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    nodejs \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright Chromium + its system dependencies (must run as root)
RUN playwright install chromium && playwright install-deps chromium

# Make browsers dir readable by any user
RUN chmod -R 755 /ms-playwright

COPY . .

CMD ["python", "-u", "bot.py"]
