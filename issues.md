# Issues to Fix

### 🔴 Security & Deployment (Deployment Blockers)
1. **done** Hardcoded Secrets: Remove hardcoded JWT_SECRET and DATABASE_URL. Use env variables and provide .env.example.
2. **done** Missing Production Infrastructure: Dockerfiles for backend/frontend, reverse proxy/TLS setup, and /health endpoints.
3. **done** No Abuse Controls on Auth: Rate limiting, password strength policies, lockout mechanisms, and secure session revocation.
4. **done** Missing CORS & Network Policies: Explicit, environment-aware CORS policies and host bindings.

### 🟠 Performance & Latency (Critical for Real-Time Voice)
5. **done** Inline Context Retrieval Blocks Pipeline: ContextRetrievalProcessor synchronously awaits RAG and Memory DB/vector queries.
6. **done** Thread Pool Exhaustion: Heavy tasks (Docling PDF parsing, web crawling, local embedding, sync LLM API calls) share Python's default thread pool.
7. **done** Blocking DNS Resolution: socket.getaddrinfo in rag.py executes synchronously inside an async coroutine.
8. **done** In-Memory ML Model Overhead: Loading the ~400MB embedding model directly into the web server process.
9. **done** Synchronous LLM Clients: _generate_text_with_memory_llm wraps synchronous OpenAI/Google clients in threads. Use native async clients.

### 🟡 Architecture & Background Jobs
10. **done** Unmanaged Background Tasks (No Queue): RAG ingestion, conversation summarization, and memory generation use raw asyncio.create_task().
11. **done** Heavy Logic Inside DB Transactions: save_conversation_message executes LLM calls and embedding logic before committing the transaction.
12. **done** Raw SQL Migrations on Startup: The app executes raw ALTER TABLE and CREATE INDEX scripts on startup. Switch to Alembic.
13. **done** Local Filesystem Storage: Uploaded PDFs are stored on the local disk. Move uploads to S3-compatible object storage.
14. **done** Duplicate Tool Filler Speech: Multiple parts of the pipeline trigger "Let me look that up for you."

### 🔵 Code Quality & Maintainability
15. **done** Unpinned Dependencies: requirements.txt lacks exact version pins and a lockfile.
16. **done** Monolithic API File (main.py): Processors, router mounts, model warming, and migrations are all stuffed into main.py.
17. **done** Missing Database Indices: Key tables are missing composite indices on (conversation_id, created_at).
18. **done** Implicit DB Connection Pool: The SQLAlchemy asyncpg engine has no configured pool limits, timeouts, or disposal logic.
19. **done** Working-Directory Dependent Imports: Tests fail unless run from a specific directory. Make backend a standard Python package.
20. **done** Naive Timestamps: Database models use naive datetime.utcnow() instead of timezone-aware UTC timestamps.
21. **done** Incomplete Input Validation: Fields lack strict length bounds and Pydantic constraints.

### 🟣 Frontend & User Experience
22. Monolithic Frontend Bundle: App.jsx handles almost all data fetching, WebRTC state, and UI rendering. Needs code-splitting.
23. Inconsistent Error/Auth Handling: The frontend lacks a centralized fetch wrapper for handling 401 Unauthorized errors and retries.

### 🔴 Newly Verified Latency & Correctness Issues (2026-07-17)
24. Context Retrieval Still Blocks Inference: `ContextRetrievalProcessor` moves retrieval into a task but deliberately withholds the transcription until it completes. RAG embedding/vector/FTS runs even for ordinary turns that are ultimately skipped, adding 0.56–0.97 seconds after final STT in the supplied log. Add pre-routing and a strict deadline.
25. Untracked Retrieval Tasks Can Reorder Turns: A detached task is created for every transcription with no session task registry, cancellation, sequence check, or delivery queue. Rapid/barge-in turns can reach the LLM out of order, and retrieval can outlive disconnect.
26. Retrieved Context Accumulates Permanently: Query-specific RAG and episodic-memory developer messages are appended to the shared `LLMContext` and never removed after the turn. Long calls grow tokens and allow stale retrieved content to affect unrelated later turns.
27. Background Queue Does Not Preserve Conversation Ordering or Drain on Shutdown: Three workers can process user and assistant derived-memory jobs for one conversation concurrently. `stop()` immediately cancels workers without `queue.join()`, so queued work can be lost.
28. Derived Memory Exhausts the Foreground Provider Quota: Every user message invokes the memory LLM without a cheap fact-candidate gate, separate quota, rate limiter, or 429 circuit breaker. The supplied call logged a Gemini `RESOURCE_EXHAUSTED` failure from background memory processing.
29. Repeated Full-Conversation Summary Work: Each user and assistant post-processing job counts and loads the complete conversation; above thresholds it can repeatedly regenerate the full summary and overlapping embeddings. This increases DB/provider load and can race across workers.
30. Backend Test Suite Is Broken After Processor Refactor: `uv run --with pytest pytest -q` fails during collection because `tests/test_rag.py` imports `RollingVoiceQueryBuffer` from `main` after it moved to `core.processors`.
31. Frontend Lint Regression: `npm run lint` fails on unused `React` imports in `Sidebar.jsx` and `TranscriptPanel.jsx`, plus an unused `fetchWithAuth` import in `Sidebar.jsx`.
32. Deepgram TTS Uses a Deprecated Constructor Argument: Runtime logs warn that `voice=` is deprecated; use `DeepgramTTSService.Settings(voice=...)` and validate latency/audio settings.
33. No Correlated End-to-End Turn Timing: Provider TTFB metrics exist, but there is no session/turn-correlated timing for speech stop, final STT, routing, embedding, DB pool/queries, first LLM token, provider audio, and transport audio. One logged turn even reports bot-speaking before TTFA, making optimization and regression detection unreliable.
