# Use official Python 3.11 slim image for a lightweight, secure container
FROM python:3.11-slim

# Set working directory inside container
WORKDIR /app

# Ensure stdout and stderr streams are sent straight to terminal without buffering
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Copy dependency definitions first to leverage Docker layer caching
COPY requirements.txt ./
COPY requirments.txt* ./

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source code and web templates
COPY . .

# Expose standard Flask web server port
EXPOSE 5000

# Start the Flask web application
CMD ["python", "app.py"]
