# Use Python 3.11 as base
FROM python:3.11-slim

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV HEADLESS=true

# Install system dependencies for Playwright
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxext6 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libpango-1.0-0 \
    libcairo2 \
    libasound2 \
    libpq-dev \
    xvfb \
    x11vnc \
    fluxbox \
    dbus-x11 \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install a production WSGI server
RUN pip install --no-cache-dir gunicorn==23.0.0

# Install Playwright browsers
RUN playwright install chromium
RUN playwright install-deps chromium

# Copy the rest of the application
COPY . .

# Use HuggingFace persistent storage (/data) so browser sessions survive rebuilds.
# If /data exists (HF persistent storage enabled), symlink /app/data → /data
# Otherwise, create /app/data as a regular directory.
RUN mkdir -p /app/data

# Expose the Web UI port
EXPOSE 5001

# Startup: link persistent storage if available, then run gunicorn
CMD ["sh", "-c", "if [ -d /data ]; then rm -rf /app/data && ln -s /data /app/data && echo 'Linked /app/data -> /data (persistent)'; else echo 'Using /app/data (ephemeral)'; fi && gunicorn --bind 0.0.0.0:${PORT:-5001} --workers 4 --threads 4 --timeout 120 web_app:app"]
