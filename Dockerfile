FROM python:3.13-slim

# ffmpeg — required by yt-dlp to merge video+audio streams
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# deno — required by yt-dlp to solve YouTube's JS n-challenge
# (yt-dlp needs deno >= 2.3 or node >= 22; Debian's nodejs is too old)
COPY --from=denoland/deno:bin /deno /usr/local/bin/deno

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "-u", "bot.py"]
