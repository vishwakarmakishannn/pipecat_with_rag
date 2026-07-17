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
22. **done** Monolithic Frontend Bundle: App.jsx handles almost all data fetching, WebRTC state, and UI rendering. Needs code-splitting.
23. **done** Inconsistent Error/Auth Handling: The frontend lacks a centralized fetch wrapper for handling 401 Unauthorized errors and retries.

### 🔴 Newly Verified Latency & Correctness Issues (2026-07-17)
24. **done** Context Retrieval Still Blocks Inference: `ContextRetrievalProcessor` moves retrieval into a task but deliberately withholds the transcription until it completes. RAG embedding/vector/FTS runs even for ordinary turns that are ultimately skipped, adding 0.56–0.97 seconds after final STT in the supplied log. Add pre-routing and a strict deadline.
25. **done** Untracked Retrieval Tasks Can Reorder Turns: A detached task is created for every transcription with no session task registry, cancellation, sequence check, or delivery queue. Rapid/barge-in turns can reach the LLM out of order, and retrieval can outlive disconnect.
26. **done** Retrieved Context Accumulates Permanently: Query-specific RAG and episodic-memory developer messages are appended to the shared `LLMContext` and never removed after the turn. Long calls grow tokens and allow stale retrieved content to affect unrelated later turns.
27. **done** Background Queue Does Not Preserve Conversation Ordering or Drain on Shutdown: Three workers can process user and assistant derived-memory jobs for one conversation concurrently. `stop()` immediately cancels workers without `queue.join()`, so queued work can be lost.
28. **done** Derived Memory Exhausts the Foreground Provider Quota: Every user message invokes the memory LLM without a cheap fact-candidate gate, separate quota, rate limiter, or 429 circuit breaker. The supplied call logged a Gemini `RESOURCE_EXHAUSTED` failure from background memory processing.
29. **done** Repeated Full-Conversation Summary Work: Each user and assistant post-processing job counts and loads the complete conversation; above thresholds it can repeatedly regenerate the full summary and overlapping embeddings. This increases DB/provider load and can race across workers.
30. **done** Backend Test Suite Is Broken After Processor Refactor: `uv run --with pytest pytest -q` fails during collection because `tests/test_rag.py` imports `RollingVoiceQueryBuffer` from `main` after it moved to `core.processors`.
31. **done** Frontend Lint Regression: `npm run lint` fails on unused `React` imports in `Sidebar.jsx` and `TranscriptPanel.jsx`, plus an unused `fetchWithAuth` import in `Sidebar.jsx`.
32. **done** Deepgram TTS Uses a Deprecated Constructor Argument: Runtime logs warn that `voice=` is deprecated; use `DeepgramTTSService.Settings(voice=...)` and validate latency/audio settings.
33. **done** No Correlated End-to-End Turn Timing: Provider TTFB metrics exist, but there is no session/turn-correlated timing for speech stop, final STT, routing, embedding, DB pool/queries, first LLM token, provider audio, and transport audio. One logged turn even reports bot-speaking before TTFA, making optimization and regression detection unreliable.
34. **done** RAG Deadline Prevents Retrieval: The initial 400 ms voice deadline consistently expired before embedding and hybrid SQL completed, so explicit PDF questions reached the LLM without context. Restrict live RAG to explicit source-grounded turns and allow a bounded 2.5-second cold-retrieval window for those turns.
35. **done** Tool-Call Transcript API Schema Mismatch: The frontend persists `ToolCall` messages, but `MessageCreate` accepted only `You|Aura`, producing HTTP 422 errors during every web-search tool call.
36. **done** Web Search Results Inflate the Voice Prompt: Full Tavily result snippets pushed the prompt above 5,000 tokens in the supplied log. Keep the answer plus three bounded source snippets.
37. **done** Interrupted Split Turn Removes Fresh RAG Context: A partial transcription could start an inference whose later end frame removed RAG context injected for the completed transcription. Clear prior dynamic context before a new transcription, but retain newly injected context across interrupted LLM end frames.
38. **done** RAG-Grounded Turns Can Invoke Web Search: Retrieved source context previously only suggested using it "before" web search. Mark grounded turns explicitly and prohibit web search when the retrieved source already answers the question.
39. **done** No In-App Real-Time Latency View: Stream final-transcript-to-first-answer-audio timing for direct, RAG, and tool turns and show latest plus rolling with/without-tool averages in the history sidebar.
40. **done** Deepgram STT Keepalive Failure Does Not Trigger Recovery: Pipecat logged a broken SDK websocket every five seconds without exiting the listener or entering its reconnect loop. Schedule Pipecat's buffered/deferred STT reconnect on the first keepalive failure and stop the failed keepalive loop.
41. **done** Broken Deepgram Socket Aborts Explicit Reconnect: Recovery called `send_close_stream()` on the same failed websocket, raised another assertion, emitted a non-fatal error frame, and waited 8–13 seconds for incidental recovery. Treat CloseStream as best-effort and always cancel the old connection task before reconnecting.
42. **done** No Per-Source Vector Store Inspector: Files and links exposed only a chunk count, so ingestion quality and the exact stored text/vector/index state could not be verified. Add an authenticated, paginated chunk inspector for every ready source.
43. **done** Chunk Inspector Action Is Invisible: The database action reused the history delete-button class, which keeps buttons transparent outside a history-row hover. Give file inspection its own always-visible action style.
44. **done** Stale Backend Produces Misleading Empty Inspector: A backend started before the chunk endpoint was added returns a generic 404 while the inspector still displays the cached sidebar chunk count. Recognize endpoint 404s, explain that a backend restart is required, and suppress the misleading result count.
45. **done** Link and Plural Source Queries Bypass RAG: The low-latency pre-router recognizes singular PDF/file wording but not `documents`, `links`, `articles`, or webpages. Explicit saved-link questions are therefore sent directly to the LLM, which can invoke web search despite 28 correctly ingested chunks. Expand source-grounding patterns without enabling RAG for ordinary general-web questions.
46. **done** Failed Audio Replay Strands Reconnected Deepgram Socket: When buffered audio replay fails immediately after keepalive recovery, Pipecat clears the connection reference while the one-shot recovery guard is still active. The replacement receives neither audio nor keepalives and closes with `NET-0001`. Queue one deduplicated follow-up reconnect after the active recovery completes.
47. **partially done** Final Transcript Fragments Are Treated as Separate Turns: Fragmented transcripts now retain one active latency turn, and persistence uses Pipecat's combined `on_user_turn_stopped` transcript. Pre-aggregator RAG routing still observes individual frames because Smart Turn needs them in real time; complete turn-level routing coordination remains.
48. **done** Alembic Baseline Drops Latency-Critical Indexes: Rebuilt the baseline, added additive index/schema-sync migrations, declared custom indexes in ORM metadata, upgraded the live database, and verified `alembic check` reports no drift.
49. **partially done** Latency Dashboard Does Not Measure User-Perceived End to End: Multiple transcript fragments now share one turn ID, but speech-stop, Smart Turn, transport-send, and browser-playback timestamps are still absent.
50. **done** Embedding and Memory Provider Clients Are Recreated Per Call: Reuse lazy process-scoped Google/OpenAI clients and deduplicate/cache normalized embeddings with bounded TTL/LRU storage.
51. **done** Hybrid RAG Queries Execute Sequentially: Vector and full-text branches now execute concurrently in independent async sessions after embedding completes.
52. **done** Session Hydration Is Sequential and Prompt Sections Are Unbounded: Facts and recent messages load concurrently, and facts, summaries, recent transcript, and prior context now have explicit character budgets.
53. **done** RAG Inspection Payload Is Sent Before Inference: Dynamic context is installed and the transcription is released before diagnostic payload delivery/persistence.
54. **done** RAG Ingestion Embeds Chunks Sequentially: Chunk embeddings now run with configurable bounded concurrency while retaining provider backpressure control.
55. **done** Live Latency Averages Persist Across Sessions Without Clear Labels: The latest value can decrease, but the rolling direct/tool averages retained up to 20 samples across disconnects and conversations, making them look like high scores. Reset samples when a session/new conversation starts, label them as session averages, and provide a manual reset.
