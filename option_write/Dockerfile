FROM python:3.9-slim

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
COPY weeklies.py .
COPY live_signals.py .

# Create directories for data persistence
RUN mkdir -p /app/signals

# Set environment variables
ENV PYTHONUNBUFFERED=1

# Expose port
EXPOSE 5001

# Run the application
CMD ["/opt/venv/bin/python", "app.py"]
