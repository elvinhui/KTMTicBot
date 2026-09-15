# Playwright official Python runtime image (pre-bundled with Chromium Linux dependencies)
FROM mcr.microsoft.com/playwright/python:v1.49.0-noble

WORKDIR /app

# Set non-interactive environment and UTF-8 locale
ENV PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive \
    TZ=Asia/Kuala_Lumpur

# Install Python requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Ensure Playwright Chromium is ready
RUN playwright install chromium

# Copy application source
COPY . .

# Default command: run sniper daemon
CMD ["python", "main.py"]
