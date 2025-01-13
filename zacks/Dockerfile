# Use an official Python runtime as the base image
FROM python:3.10-slim

# Set working directory in the container
WORKDIR /app

# Install system dependencies required for pandas and scipy
RUN apt-get update && apt-get install -y \
    gcc \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first to leverage Docker cache
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application code
COPY . .

# Expose the port the app runs on
EXPOSE 5052

# Command to run the application
CMD ["python", "app.py"]
