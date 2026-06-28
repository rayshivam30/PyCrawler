FROM python:3.13-slim

# Prevent writing pyc files and buffer outputs for real-time docker logs
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /workspace

# Install system build tools for PostgreSQL and compiling source packages if needed
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install uv globally in container
RUN pip install --no-cache-dir uv

# Pre-copy and resolve dependencies
COPY requirements.txt .
RUN uv pip install --system --no-cache --extra-index-url https://download.pytorch.org/whl/cpu -r requirements.txt

# Copy source tree
COPY . .

# Expose API/Dashboard port
EXPOSE 8000

# Start FastAPI via uvicorn
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
