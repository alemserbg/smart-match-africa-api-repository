# --- STAGE 1: BUILD ENGINE & DEPENDENCY COMPILATION ---
    FROM python:3.11-slim AS builder

    WORKDIR /app
    
    # Prevent Python from writing pyc files and enable unbuffered logging stdout/stderr
    ENV PYTHONDONTWRITEBYTECODE=1 \
        PYTHONUNBUFFERED=1
    
    # Install system dependencies needed for compiling certain Python packages
    RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
        && rm -rf /var/lib/apt/lists/*
    
    # Upgrade pip and isolate dependency wheels compilation
    RUN pip install --no-cache-dir --upgrade pip
    COPY requirements.txt .
    RUN pip wheel --no-cache-dir --no-deps --wheel-dir /app/wheels -r requirements.txt
    
    
    # --- STAGE 2: FINAL MINIMAL RUNTIME ENGINE ---
    FROM python:3.11-slim AS runner
    
    WORKDIR /app
    
    ENV PYTHONDONTWRITEBYTECODE=1 \
        PYTHONUNBUFFERED=1 \
        PORT=8080
    
    # Install runtime security patches and libpq for PostgreSQL communication
    RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
        && rm -rf /var/lib/apt/lists/*
    
    # Pull compiled wheels directly from stage 1 builder
    COPY --from=builder /app/wheels /wheels
    COPY --from=builder /app/requirements.txt .
    RUN pip install --no-cache-dir --no-index --find-links=/wheels -r requirements.txt && rm -rf /wheels
    
    # Copy the entire src directory and frontend payloads preserving the src package structure
    COPY ./src /app/src
    COPY ./frontend /app/frontend
    
    # Create a dedicated, unprivileged system user to execute the app securely
    RUN useradd -u 8888 appuser && chown -R appuser:appuser /app
    USER appuser
    
    # Expose the port allocated for Render's edge proxy network mapping
    EXPOSE ${PORT}
    
    # Fire up Uvicorn targeting the correct src.smartmatch.main entrypoint module path
    CMD ["uvicorn", "src.smartmatch.main:app", "--host", "0.0.0.0", "--port", "8080"]