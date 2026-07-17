# Aura Voice Project Audit and Pre-Deployment Readiness Report

**Audit date:** 2026-07-15  
**Target product:** A multi-user, real-time conversational voice platform comparable in direction to Vapi/Retell  
**Current assessment:** Functional prototype / early alpha; not production-ready

## 1. Executive summary

The project has a coherent prototype architecture and several meaningful capabilities already work together: WebRTC audio transport, streaming STT/LLM/TTS, authenticated conversation history, durable facts, semantic episodic memory, hybrid PDF/link RAG, tool calls, and a usable React interface. The code is small enough to evolve quickly, provider selection is partially abstracted, user ownership checks are present on most REST data paths, and the focused backend tests currently pass.

The main deployment risk is not a single bug; it is that prototype concerns and production concerns are still combined in one process and one request path. The real-time turn path synchronously waits for local embedding inference and RAG/database work before the LLM sees the user's transcription. Background work is launched with unmanaged `asyncio.create_task`, migrations are executed inside application startup, configuration contains development credentials and host assumptions, and there is little operational instrumentation beyond Pipecat's built-in metrics. These choices will cause variable response latency, event-ordering bugs, difficult failure recovery, and poor horizontal scalability.

**Recommendation:** Do not expose this version to public production traffic. Treat it as an internal alpha until the P0 security/configuration items and P1 latency/reliability items in this report are completed and measured under load.

## 2. Scope and evidence

Reviewed areas:

- All tracked backend Python source, APIs, providers, tools, models, configuration, and tests
- Frontend connection/authentication/data flow, package configuration, linting, and production build
- Database model and startup schema behavior
- RAG ingestion and retrieval flow
- Memory ingestion and retrieval flow
- Deployment files, dependency declarations, repository hygiene, and generated architecture graph

Checks executed:

- `backend`: `../.venv/bin/python -m pytest -q` → **23 passed**, one Python 3.13-related `audioop` deprecation warning
- project root: `.venv/bin/python -m pytest -q` → **3 collection errors** caused by working-directory-dependent imports
- frontend: `npm run lint` → **passed**
- frontend: `npm run build` → **passed**, but emitted a large-chunk warning (671.98 kB minified / 190.02 kB gzip)
- Python byte compilation → **passed**
- Existing code graph traversal → 206 relevant nodes across voice runtime, RAG, memory, auth, storage, tests, and frontend

No live provider latency, browser call, telephony call, database load test, dependency vulnerability scan, or destructive migration test was run. Those require controlled credentials/infrastructure and should be part of the next readiness phase.

## 3. Current architecture

### Real-time path

1. React creates a Pipecat client using `SmallWebRTCTransport`.
2. The frontend calls `/start`, sending a JWT and conversation ID in the request body.
3. The backend creates STT, LLM, and TTS providers per session.
4. Audio flows through transport input → STT → persistence → contextual retrieval → user aggregation → LLM → tool filler → assistant persistence → TTS → transport output.
5. A final transcription triggers memory retrieval and RAG retrieval concurrently, but the pipeline awaits both before forwarding the frame.
6. User and assistant messages are persisted through detached tasks. Persistence also invokes memory classification, summary generation, and episodic chunk creation.

### Data and supporting paths

- PostgreSQL + pgvector stores users, conversations, messages, durable facts, episodic memory chunks, RAG sources/chunks, and issues.
- RAG ingestion supports PDF parsing and URL extraction through Crawl4AI with Trafilatura fallback.
- RAG retrieval combines pgvector similarity with PostgreSQL full-text search using reciprocal-rank fusion.
- A local `BAAI/bge-base-en-v1.5` sentence-transformer produces both memory and RAG embeddings.
- JWT/bcrypt provide basic authentication.
- Provider factories support Google/Groq LLM, Deepgram STT, and Deepgram/Cartesia/Piper TTS.

## 4. Positive findings

- The voice pipeline is understandable and uses Pipecat processors rather than embedding all behavior in handlers.
- Memory and RAG retrieval run concurrently, avoiding an unnecessary serial wait between those two operations.
- User ownership is checked for conversations, files, memories, and RAG retrieval queries.
- RAG source content is explicitly described to the LLM as untrusted context, reducing prompt-injection risk.
- Link ingestion includes scheme checks, public-address validation, redirect revalidation, size limits, timeouts, content-type checks, and optional robots.txt respect.
- PDF size and nominal MIME/extension checks exist.
- Hybrid retrieval, configurable thresholds, result provenance, and RAG-call UI payloads are good foundations.
- Fact extraction has normalization, confidence thresholds, whitelisted keys, and user-only prompt rules.
- Focused unit tests cover core memory formatting, RAG routing, URL validation, chunk formatting, and rolling query behavior.
- Frontend lint and production compilation are clean.

## 5. Prioritized findings

### P0 — Block public deployment

#### P0.1 Insecure default secrets and credentials

`backend/api/auth.py` falls back to a known JWT secret (`supersecret-aura-voice-key`). `backend/core/database.py` hardcodes the complete database URL and password. `docker-compose.yml` publishes PostgreSQL on the host with a fixed development password.

**Impact:** Token forgery, database exposure, environment coupling, and accidental production use of development credentials.

**Required change:** Make `JWT_SECRET` and `DATABASE_URL` mandatory at startup; fail fast if absent or weak. Move database credentials to secret management. Do not publish the database port in production. Add `.env.example` containing names only, never values.

#### P0.2 No production deployment definition

Only the database is containerized. There is no backend image, frontend image/static-server configuration, reverse proxy, TLS termination, health/readiness endpoints, process limits, deployment manifest, or documented production command.

**Impact:** The application cannot be deployed reproducibly or safely, and the browser's microphone/WebRTC requirements cannot be reliably satisfied without HTTPS.

**Required change:** Add pinned backend and frontend builds, a reverse proxy/TLS strategy, same-origin routing, `/health/live` and `/health/ready`, non-root containers, persistent upload storage, and environment-specific Compose/Kubernetes definitions.

#### P0.3 Public registration and authentication lack abuse controls

Registration is open. Passwords and usernames have no meaningful validation. Login/register have no rate limiting, lockout/backoff, audit trail, refresh/revocation scheme, or verified identity. JWTs live for seven days and are stored in browser `localStorage`.

**Impact:** Account spam, brute force, durable token theft after XSS, and no practical session revocation.

**Required change:** Establish the intended tenancy/onboarding model. Enforce password/username policy and request limits; use short-lived access tokens with revocable refresh sessions (prefer secure, HttpOnly, SameSite cookies for the web app); add security headers and authentication audit events.

#### P0.4 No explicit cross-origin and production network policy

The frontend constructs the API as `current-host:7860`; there is no environment-aware same-origin `/api` base, and no explicit CORS policy is visible in project code.

**Impact:** Production topology is brittle. HTTPS pages can fail if the backend is not served securely, non-default ports are commonly blocked, and adding permissive CORS later would be dangerous.

**Required change:** Put UI/API/WebRTC signaling behind a defined HTTPS origin or configure a strict allowlist. Make both API and start endpoints build-time/runtime configuration, not inferred fixed ports.

### P1 — High priority for a real-time beta

#### P1.1 Context retrieval blocks every authenticated voice turn

`ContextRetrievalProcessor` starts memory and RAG tasks and awaits `asyncio.gather` before forwarding the transcription. RAG always attempts query embedding before vector and text queries. The default embedding provider is a local BGE model executed via `asyncio.to_thread`.

**Impact:** Embedding inference + database round trips are directly added to end-of-turn-to-first-LLM-token latency. Under concurrent sessions, Python's thread pool and CPU become shared contention points. Even ordinary chat pays the RAG embedding cost because smart routing happens after retrieval.

**Recommended design:**

- Add a very cheap pre-router before embedding. Skip RAG entirely for clearly unrelated turns.
- Skip episodic embedding unless `_is_recall_query` is true (already done for memory); apply an equivalent decision before RAG embedding.
- Put strict independent deadlines around memory and RAG (for example 100–250 ms budgets), use `gather(..., return_exceptions=True)`, and proceed without context on timeout.
- Cache query embeddings and consider a dedicated embedding service with batching and bounded concurrency.
- Record p50/p95/p99 timing for VAD end, final STT, retrieval, first LLM token, first TTS audio, and interruption stop.
- Consider speculative LLM start only if the context policy can safely support it; otherwise keep retrieval bounded and predictable.

#### P1.2 Detached tasks are unmanaged and can reorder or lose messages

Voice persistence and RAG payload persistence use raw `asyncio.create_task`. Task references are not retained, exceptions are not observed, shutdown does not drain them, and the user and assistant writes may execute concurrently. Each write opens a new session and performs expensive derived-memory processing.

**Impact:** Lost writes on disconnect/restart, unhandled exceptions, assistant messages committed before user messages, race conditions in summaries/chunks, duplicate processing, and increasing task counts during load.

**Recommended design:** Add a per-conversation ordered event queue. Persist raw turns quickly in a single writer, then enqueue idempotent derived jobs for fact extraction, summaries, embeddings, and RAG events. Use durable job infrastructure (or at minimum a supervised task group) with retries, dead-letter state, and shutdown draining.

#### P1.3 The message write path performs LLM and embedding work inside a DB transaction

`save_conversation_message` flushes, calls `process_saved_message`, may call a memory LLM, may regenerate a full summary, may embed/store a chunk, then commits. The REST message endpoint does the same. Summary logic loads every conversation message on every user/assistant save after thresholds are reached.

**Impact:** Long transactions, connection-pool starvation, repeated O(n) transcript reads, repeated paid model calls, lock duration, and latency spikes. Concurrent user/assistant tasks amplify this.

**Recommended design:** Commit the message first. Update title/counters atomically. Move fact classification, summarization, and embedding to asynchronous idempotent jobs. Summarize incrementally at checkpoints rather than re-reading and re-summarizing the entire transcript each turn.

#### P1.4 RAG ingestion is in-process, sequential, and not durable

Upload/link endpoints create detached processing tasks. PDF parsing and browser crawling share the API/voice process. Each chunk is embedded one-by-one. A restart strands records in `processing`; horizontal replicas have no ownership or recovery protocol.

**Impact:** CPU/RAM spikes affect calls, ingestion throughput is low, browser/parser failures destabilize the main process, and jobs are lost on restart.

**Recommended design:** Use a separate ingestion worker and durable queue. Batch embeddings, cap document pages/chunks and concurrency, add job attempts/progress/heartbeats, recover stale `processing` jobs, and scan uploads before parsing.

#### P1.5 No end-to-end latency or concurrency validation

Pipecat metrics are enabled, but no observer/exporter, dashboards, SLOs, correlation IDs, or load-test harness exist. There are no tests for interruption/barge-in, concurrent calls, provider timeouts, disconnect cleanup, network loss, slow DB, or tool latency.

**Impact:** The team cannot tell whether changes improve the defining product property—real-time responsiveness—or identify regressions before users do.

**Required change:** Define SLOs and build a repeatable synthetic audio benchmark. Suggested initial goals: final-STT-to-first-audio p50 < 800 ms, p95 < 1.5 s for no-tool turns; barge-in audio stop < 200 ms; call start p95 < 2 s; dropped call rate < 0.5%. Tune these after measuring provider/network baselines.

#### P1.6 Tool filler speech can be duplicated

There is a `ToolFillerProcessor`, a newly added LLM `on_function_calls_started` handler in the uncommitted `backend/main.py`, and the RAG processor also emits “Let me look that up for you.” for likely RAG queries.

**Impact:** Users can hear repeated filler, filler may overlap real output, and extra TTS work increases latency/cost. The LLM-level event handler also pushes frames directly in a way that should be validated against Pipecat lifecycle semantics.

**Recommended design:** Centralize filler policy in one processor/state machine with a debounce (only speak if the operation exceeds a short threshold), cancel it if results arrive quickly, and distinguish RAG retrieval from actual tool execution.

#### P1.7 Startup performs ad hoc schema migrations and index rebuild work

Application lifespan executes `ALTER TABLE`, data updates, constraint changes, vector dimension conversion, index creation, and an embedding model load. Some index setup exceptions are logged and ignored.

**Impact:** Slow/unpredictable startup, replica races, locks/outages on large tables, partially migrated deployments, readiness before required capabilities are verified, and difficult rollback.

**Recommended design:** Adopt Alembic migrations run once by deployment automation. Treat required schema/index failures as readiness failures. Keep model warming explicit and measured; do not let every replica download independently at startup.

#### P1.8 Dependency versions are not reproducible

Backend requirements are unpinned (except `websockets`), duplicated between root and backend, and include very large optional stacks. There is no lockfile or automated vulnerability/license scan.

**Impact:** Builds can change without code changes, provider API breaks can appear unexpectedly, images become large, and known vulnerabilities are not gated.

**Required change:** Choose one dependency source, lock exact resolved versions/hashes, separate runtime/dev/provider extras, and add Dependabot/Renovate plus pip-audit/npm audit (with an explicit review policy).

### P2 — Medium priority structural and correctness issues

#### P2.1 Import/package layout depends on current working directory

The root test command fails with `ModuleNotFoundError` for `services`, `main`, and `tools`; tests pass only from `backend/`. Imports use top-level names such as `from core...` instead of a consistently installed package.

**Recommendation:** Package the backend (for example `aura_voice`), use absolute package imports, add `pyproject.toml`, define test paths/configuration, and run CI from the repository root.

#### P2.2 The API and voice runtime are concentrated in `main.py`

Pipeline processors, bot construction, transport creation, router mounting, migrations, model warming, and process startup share one module and router imports occur only inside the `__main__` branch.

**Impact:** Difficult unit/integration testing, hidden import side effects, unclear ASGI entry point, and poor separation for API versus worker scaling.

**Recommendation:** Split into application factory, lifespan, voice session service, processors, and CLI/worker entry points. Routers should be registered by the app factory regardless of invocation style.

#### P2.3 Database pool and lifecycle are implicit

No pool size/timeouts/recycle settings are configured, and the engine is not explicitly disposed on shutdown.

**Recommendation:** Configure pool size based on expected concurrent calls/workers, set connection and statement timeouts, instrument pool wait time, and dispose cleanly.

#### P2.4 Input and domain validation are incomplete

- Message `role` is client-controlled through REST.
- Conversation title, message content, username, password, RAG search query, and issue description have no explicit length bounds.
- A PDF is accepted primarily by filename/content-type; magic bytes and parser safety are not checked.
- Error text from ingestion is returned and stored with potentially sensitive implementation details.

**Recommendation:** Use strict Pydantic constraints/enums, server-derived roles where possible, content limits, PDF signature/scanning/sandboxing, and sanitized public errors with internal error IDs.

#### P2.5 Local filesystem storage prevents clean horizontal scaling

Uploaded PDFs and extracted Markdown are stored under local paths.

**Impact:** Files disappear across replicas/redeploys unless a shared volume is carefully mounted; deletion and DB state can diverge.

**Recommendation:** Introduce an object-storage abstraction with checksums, lifecycle policies, tenant-scoped keys, signed access only when necessary, and transactional/outbox cleanup.

#### P2.6 Frontend is a large monolithic component and bundle

`App.jsx` contains most data fetching, connection state, transcript logic, panels, and rendering. The production build is a single 671.98 kB JS chunk.

**Recommendation:** Split by feature (voice session, conversations, memory, RAG sources, tool events), centralize an authenticated API client, add an error boundary, lazy-load secondary panels, and configure chunking. This improves maintainability more than raw voice latency, but reduces startup and regression risk.

#### P2.7 Frontend error and auth handling are inconsistent

Some failed requests are ignored or only logged. A locally decoded, unexpired JWT is treated as valid until a server call fails. There is no common 401 handler, abort/cancellation, request timeout, or retry policy.

**Recommendation:** Centralize fetch behavior, clear/refresh sessions on 401, use `AbortController`, present actionable states, and prevent stale responses from overwriting newer UI state.

#### P2.8 Provider abstraction is too narrow for a Vapi/Retell-like platform

Factories select providers, but capability metadata, per-agent configuration, fallbacks, health, regional routing, timeouts, and normalized metrics/errors are absent. Provider objects are constructed per call without an explicit lifecycle strategy.

**Recommendation:** Define provider interfaces and capability/config schemas; validate credentials at readiness; add circuit breakers, timeout budgets, fallback policy, cost/usage normalization, and per-tenant/agent configuration.

#### P2.9 Privacy and data-governance controls are not defined

The system stores transcripts, inferred facts, embeddings, RAG documents, tool payloads, email/mobile/device IDs, and logs source metadata. There is no retention schedule, consent flow, encryption policy, export/delete-all workflow, redaction, or administrator access model.

**Recommendation:** Write a data inventory and retention policy before beta. Add explicit memory/transcript consent, full user export/deletion, encryption in transit/at rest, PII-safe structured logging, access audit, and configurable recording/transcript retention.

#### P2.10 Database timestamps are naive

Models use `datetime.utcnow` and timestamp columns without timezone awareness while JWT uses timezone-aware UTC.

**Recommendation:** Standardize on timezone-aware UTC (`TIMESTAMP WITH TIME ZONE`) and serialize consistently.

### P3 — Quality and maintainability improvements

- Test files include disabled `.bak` suites; either restore meaningful tests or remove/archive them outside test source.
- `test_tavily.py` appears integration-oriented and credential-dependent; mark and isolate external tests.
- The frontend README remains the Vite starter and there is no root operational README, architecture decision record, API contract, or runbook.
- No formatter/type checker is configured for Python. Add Ruff and Pyright/mypy; consider TypeScript for frontend domain/event payloads.
- No CI definition is present. Add root-level lint, type check, unit/integration tests, build, migration validation, secret scan, and dependency scan.
- The checked-in Piper ONNX assets materially enlarge the repository; use an artifact/model registry or documented build download with checksums.
- `graphify` project metadata is stale relative to its installed package, indicating generated tooling is not reproducible. Regenerate or exclude incomplete intermediate graph files.
- Logs are free-form and lack request/session/conversation correlation fields. Use structured logs and never include raw tokens, transcript content, or PII by default.

## 6. Product gaps versus a Vapi/Retell-class system

These are not necessarily defects for the current stage, but they are major platform capabilities still absent or not evident:

- Telephony provisioning and inbound/outbound call control (SIP/PSTN numbers, carrier webhooks, call transfer, DTMF, voicemail/answering-machine detection)
- Multi-tenant organizations, projects, roles, API keys, quotas, and billing/usage accounting
- Declarative agent/version configuration (prompt, voice, tools, knowledge, provider, language, endpointing) with rollback
- Call lifecycle records, recordings, event timelines, analytics, quality scores, and searchable logs
- Webhook delivery with signing, retries, idempotency, and dead-letter handling
- Tool/server integration framework with schemas, secrets, authorization, timeout/retry policy, and sandboxing
- Provider failover, regional routing, concurrency admission control, and capacity planning
- Barge-in tuning, endpointing controls, noise cancellation, echo behavior, multilingual/language switching, and pronunciation dictionaries
- Human transfer/escalation and post-call workflows
- Enterprise security/privacy controls and compliance evidence

The recommended strategy is to first make one excellent, measurable WebRTC voice path reliable. Telephony and platform breadth should come after the session core, job system, configuration model, and observability are stable.

## 7. Recommended target architecture

Keep a modular monolith initially, but separate runtime responsibilities:

- **Web/API service:** authentication, CRUD/config APIs, call creation, signed session negotiation, health/readiness.
- **Real-time session worker:** owns one or more voice sessions with strict concurrency limits; performs only latency-sensitive streaming work.
- **Durable job worker:** memory extraction, summaries, embeddings, RAG ingestion, webhook delivery, and cleanup.
- **PostgreSQL/pgvector:** transactional metadata and retrieval at current scale; add Redis only when needed for queues, rate limits, ephemeral session state, or distributed coordination.
- **Object storage:** uploads, extracted artifacts, and recordings.
- **Observability pipeline:** OpenTelemetry traces/metrics/logs with call/session correlation and provider spans.

Important boundaries:

- The real-time worker must never wait on unbounded background/model/database work.
- Every externally triggered operation needs an idempotency key and explicit state machine.
- Raw turn persistence and derived memory/RAG work must be separate.
- Provider-specific types should stop at adapter boundaries.
- Agent configuration should be versioned and snapshotted at call start.

## 8. Proposed delivery plan

### Phase 0 — One week: deployment blockers

1. Remove default secrets/hardcoded DB URL; add validated settings and `.env.example`.
2. Package backend correctly; make root tests pass; add CI.
3. Add Alembic and move all startup DDL into migrations.
4. Add Dockerfiles, HTTPS/reverse-proxy design, health/readiness endpoints, and production Compose.
5. Add strict request validation and login/register rate limiting.
6. Centralize tool filler to eliminate duplicate speech.

### Phase 1 — One to two weeks: latency and reliability

1. Add end-to-end timing spans and a synthetic voice benchmark.
2. Add pre-routing and hard retrieval deadlines; benchmark local embedding concurrency.
3. Replace detached persistence with ordered per-conversation writes and supervised/durable jobs.
4. Move memory derivation and RAG ingestion out of DB transactions and out of the voice process.
5. Add provider timeouts, normalized failures, retries only where safe, and circuit breakers.
6. Test disconnect, cancellation, barge-in, slow provider, slow DB, and concurrent sessions.

### Phase 2 — Two to four weeks: beta platform foundation

1. Versioned agent configuration and provider credentials/config validation.
2. Object storage and durable ingestion/webhook jobs.
3. Call/session/event schema, dashboards, cost/usage records, and retention controls.
4. Refresh/revocable sessions, audit events, security headers, and user data export/deletion.
5. Frontend feature split, API client, error boundary, and bundle chunking.

### Phase 3 — after measured WebRTC stability

Add telephony, transfers, webhooks, organization/RBAC controls, billing/quota enforcement, recordings, and advanced voice quality features. Gate each on load tests and defined SLOs.

## 9. Deployment gate checklist

Public beta should remain blocked until all of the following are true:

- [ ] No default secrets or fixed production credentials
- [ ] Reproducible locked builds and automated CI/security scans
- [ ] Database migrations are separate, reversible, and tested
- [ ] Backend/frontend deploy behind HTTPS with strict origin policy
- [ ] Health/readiness and graceful shutdown work
- [ ] Root test command passes; integration tests run against disposable PostgreSQL/pgvector
- [ ] Turn latency and barge-in SLOs are measured at expected concurrency
- [ ] Retrieval has strict time budgets and graceful degradation
- [ ] Background jobs are durable/supervised and idempotent
- [ ] Message ordering is deterministic per conversation
- [ ] Provider/network/DB failure tests pass
- [ ] Rate limits, password policy, session revocation, and audit events exist
- [ ] Transcript/memory/RAG retention and deletion behavior are documented and implemented
- [ ] Upload parsing is bounded and isolated
- [ ] Monitoring alerts cover call failures, latency, provider errors, queue depth, DB pool wait, and worker saturation

## 10. Final management assessment

The prototype demonstrates the core product idea and is a credible base for continued development. Its strongest technical direction is the combination of Pipecat streaming, hybrid user-scoped RAG, and memory continuity. Its weakest area is the boundary between real-time work and derived/background work: too much computation and persistence is coupled to session execution without deadlines, durable scheduling, or observability.

The next milestone should not be “more providers” or broad telephony features. It should be a **measured, failure-tolerant single-session core** that remains responsive under realistic concurrency and can be deployed reproducibly without insecure defaults. Once that foundation exists, the current modules can evolve into a Vapi/Retell-like platform without a full rewrite.
