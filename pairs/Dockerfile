# Use Python 3.9 as base image
FROM python:3.9-slim

# Set working directory
WORKDIR /app

# Install system dependencies required for numpy and other packages
RUN apt-get update && apt-get install -y \
    gcc \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first to leverage Docker cache
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Create necessary directories
RUN mkdir -p dashboard/signals dashboard/results dashboard/templates

# Copy core Python files
COPY config.py data_utils.py main.py pairs_trader.py portfolio_utils.py live_signals.py ./

# Copy dashboard files
COPY dashboard/app.py dashboard/
COPY dashboard/templates/index.html dashboard/templates/orders.html dashboard/templates/

# Set environment variables
ENV PYTHONPATH=/app
ENV FLASK_APP=app.py
ENV FLASK_ENV=production

# Expose the port the app runs on
EXPOSE 5002

# Change working directory to dashboard before running
WORKDIR /app/dashboard

# Command to run the application
CMD ["python", "app.py"]
