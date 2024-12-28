FROM selenium/standalone-chrome:latest

# Install Python and pip
USER root
RUN apt-get update && apt-get install -y \
    python3 \
    python3-pip \
    python3-venv \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies in a virtual environment
COPY requirements.txt .
RUN python3 -m venv /opt/venv && \
    . /opt/venv/bin/activate && \
    python3 -m pip install --upgrade pip && \
    pip3 install --no-cache-dir -r requirements.txt

# Copy application files
COPY app.py .
COPY config.py .
COPY marketcaps.py .
COPY weeklies.py .
COPY live_signals.py .

# Create directories for data persistence
RUN mkdir -p /app/signals

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV DISPLAY=:99

# Expose port
EXPOSE 5001

# Run the application
CMD ["/opt/venv/bin/python", "app.py"]
