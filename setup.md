# Aura Voice: Local & Production Setup Guide

This guide contains everything you need to know to build, configure, and run the Pipecat-powered Aura Voice application from scratch.

## 🛠️ Prerequisites

Before you start, ensure you have the following installed on your machine:
- **Node.js** (v18+ recommended)
- **uv** (Extremely fast Python package manager: `curl -LsSf https://astral.sh/uv/install.sh | sh`)
- **Docker & Docker Compose** (For running the database and production containers)
- **Git**

---

## ⚙️ Initial Configuration

### 1. Environment Variables
You need a `.env` file at the root of the project to supply API keys and configuration secrets to the backend.

Create a `.env` file in the root folder (where `docker-compose.yml` lives) and populate it with the following template:

```env
# Database Settings
# (Local dev uses localhost:5434. Docker uses db:5432. The backend overrides this automatically in Docker via docker-compose.yml)
DATABASE_URL=postgresql+asyncpg://pipecat_user:pipecat_password@localhost:5434/pipecat_db
POSTGRES_USER=pipecat_user
POSTGRES_PASSWORD=pipecat_password
POSTGRES_DB=pipecat_db

# JWT & Authentication
SECRET_KEY=your-super-secret-jwt-key
ALGORITHM=HS256

# AI Providers (At least one must be set depending on your configuration)
OPENAI_API_KEY=your-openai-api-key
GOOGLE_API_KEY=your-google-gemini-api-key
CARTESIA_API_KEY=your-cartesia-api-key

# Memory Vector Search Configurations
LLM_PROVIDER=google
MEMORY_EMBEDDING_PROVIDER=google
MEMORY_VECTOR_DB=pgvector
```

---

## 🚀 Option A: Local Development Workflow (Hot-Reload)

This is the recommended workflow for developing and debugging. You run the database in Docker, but run the Backend and Frontend natively on your machine for hot-reloading.

### Step 1: Start the Database
The application relies on PostgreSQL with the `pgvector` extension. 
```bash
# This starts only the database service defined in docker-compose.yml
docker compose up -d db
```
> The local database is exposed on **port 5434** to avoid conflicts with existing local Postgres instances.

### Step 2: Run the Backend (Python/FastAPI + Pipecat)
Navigate to the `backend` folder. We use `uv` to securely manage dependencies without polluting your global Python environment.

```bash
cd backend

# This automatically syncs dependencies from pyproject.toml / uv.lock and runs the server
uv run main.py
```
> The backend runs on **http://localhost:7860**. 
> Note: You do not need to manually create or activate a `.venv`. `uv run` handles the environment boundaries seamlessly.

### Step 3: Run the Frontend (React/Vite)
Open a new terminal window and navigate to the `frontend` folder.

```bash
cd frontend

# Install JavaScript dependencies
npm install

# Start the Vite development server
npm run dev
```
> The frontend runs on **http://localhost:5173** and will hot-reload as you make changes.

---

## 🐳 Option B: Full Docker Deployment (Production)

If you want to deploy the application or just want to run everything in containers without dealing with Node or `uv` directly, use the full Docker Compose setup.

### Build and Start Everything
From the root directory:

```bash
# Build the images and start all containers (db, backend, frontend) in the background
docker compose up -d --build
```

### Accessing the App
- **Frontend**: http://localhost:8080 (Served by Nginx in the frontend container)
- **Backend API**: http://localhost:7860 (Served by Uvicorn)
- **Database**: localhost:5434

### Stopping the Stack
```bash
docker compose down
```
> **Note on Volumes:** The database data is stored in a Docker volume named `pgdata`. Running `docker compose down` will **not** delete your data. If you want to completely wipe the database, run `docker compose down -v`.

---

## 🗃️ Database Migrations & Structure

### Alembic Migrations
The backend uses Alembic to manage database schema changes. When the backend starts (either via `uv run` or inside Docker), it will automatically run migrations to ensure your tables (`users`, `conversations`, `messages`, `memory_chunks`, `user_memories`, `rag_files`) are up to date.

To manually generate a new migration after modifying `core/models.py`:
```bash
cd backend
uv run alembic revision --autogenerate -m "Describe your changes"
uv run alembic upgrade head
```

---

## 🧹 Common Maintenance Commands

- **Update Python Dependencies**: 
  If you add a new dependency to `backend/pyproject.toml`, lock it and sync:
  ```bash
  cd backend
  uv lock
  uv sync
  ```

- **Viewing Backend Logs (Docker)**:
  ```bash
  docker compose logs -f backend
  ```

- **Restarting the Backend (Docker)**:
  ```bash
  docker compose restart backend
  ```
