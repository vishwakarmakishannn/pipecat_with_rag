FROM python:3.11-slim

WORKDIR /app

# Install system dependencies required for building some python packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install uv
RUN pip install uv

# Copy dependency files
COPY pyproject.toml uv.lock ./

# Install dependencies using uv
RUN uv sync --frozen --no-install-project

COPY . .

# Install the actual project using uv
RUN uv sync --frozen

# Run the app using uv
CMD ["/bin/sh", "-c", "uv run alembic upgrade head && exec uv run main.py"]
