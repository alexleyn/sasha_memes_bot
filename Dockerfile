FROM python:3.13-slim

# ffmpeg — required by yt-dlp to merge video+audio streams
# nodejs — required by yt-dlp to solve YouTube's JS n-challenge
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    nodejs \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "-u", "bot.py"]
