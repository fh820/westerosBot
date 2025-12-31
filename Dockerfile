# Use a lightweight Python image
FROM python:3.10-slim

# Install system dependencies required for OpenCV (Image processing)
RUN apt-get update && apt-get install -y \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements first (to cache dependencies)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code
COPY . .

# We use the -m flag to run the module, which helps with imports
CMD ["python", "-m", "app.bot"]