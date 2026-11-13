# Use official Python 3.11 slim image
FROM python:3.11-slim

# Set working directory in container
WORKDIR /app

# Copy requirements first for caching
COPY requirments.txt .

# Install dependencies
RUN pip install --no-cache-dir -r requirments.txt

# Copy the rest of the app
COPY . .

# Expose port 5000 (Flask)
EXPOSE 5000

# Run the Flask app
CMD ["python", "app.py"]