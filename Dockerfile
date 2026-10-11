FROM python:3.11-slim

# Install system dependencies (ffmpeg is required for audio conversions)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python requirements
COPY web/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copy web files
COPY web/ /app/

# Create downloads folder
RUN mkdir -p /app/downloads

ENV PORT=10000
EXPOSE 10000

CMD ["sh", "-c", "gunicorn -b 0.0.0.0:${PORT:-10000} app:app --workers 2 --timeout 180"]
