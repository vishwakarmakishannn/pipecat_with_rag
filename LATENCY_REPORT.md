# Aura Voice Final Latency Audit

**Review date:** 2026-07-17  
**Scope:** complete backend/frontend source, current Graphify graph, all supplied runtime logs, live PostgreSQL schema, current runtime configuration, backend tests, frontend lint/build
**Verdict:** substantially improved and usable for local beta testing, but not yet latency-optimal or production-ready at scale

## 1. Executive verdict

The system is not perfect, but the earlier dominant latency defects have been fixed:

- ordinary turns bypass RAG and memory retrieval;
- explicit PDF/link turns receive bounded retrieval;
- retrieval delivery is serialized and cancelled during cleanup;
- query-specific context is removed before the next transcription;
- derived-memory work is gated, keyed by conversation, and drained at shutdown;
- web-search results and RAG context are bounded;
- Deepgram keepalive/media failures now schedule recovery;
- the UI exposes direct/RAG/tool timing.

The provider cascade is already competitive when turn detection behaves normally. Current logs show direct turns at approximately **1.2–1.4 seconds from final transcript to first generated audio**. Explicit RAG adds about **0.62–0.65 seconds**, producing roughly **2.25 seconds** from final transcript to first answer audio. Provider timings are normally:

| Stage | Observed healthy range |
|---|---:|
| Google/Gemini first response text | 0.60–1.05 s |
| Deepgram TTS first audio | 0.30–0.52 s |
| Explicit RAG retrieval | 0.62–0.65 s |
| Direct final-STT → first generated audio | 1.2–1.4 s |
| RAG final-STT → first generated audio | about 2.25 s |

The largest remaining user-visible variance is now **turn finalization**, not normal LLM or TTS performance. Several logs show finalized transcript fragments arriving well before Smart Turn triggers inference. One direct turn took **4.14 seconds** from the last recorded fragment boundary to generated audio, including about **2.76 seconds before inference began**. The default Smart Turn maximum silence window is three seconds, matching the observed delay.

The migration/index risk identified during inspection has now been repaired: the baseline creates the schema, additive migrations synchronize legacy timestamp types and indexes, custom indexes are represented in ORM metadata, and `alembic check` reports no drift.

Post-change targeted retrieval timing for one identical explicit saved-link query was **1.42 seconds cold** and **14 milliseconds warm**. The warm result includes a cached embedding plus concurrent vector/FTS SQL. First-query latency remains dominated by the remote embedding provider, while repeated or fragmented queries benefit strongly from deduplication and caching.

## 2. Current critical paths

### Direct turn

`browser audio → WebRTC → VAD/Smart Turn → Deepgram final transcript(s) → direct router → user aggregator waits for turn completion → Gemini → Deepgram TTS → WebRTC output`

No database or embedding call is now required for ordinary conversation. Remaining variance comes from speech finalization, LLM TTFB, TTS TTFA, and network/provider reconnects.

### Explicit RAG or recall turn

`final transcript → route → optional filler → embedding API → vector SQL → FTS SQL → relevance filter/context formatting → user aggregator → Gemini → TTS`

The 2.5-second deadline prevents unbounded waiting. Vector and FTS now execute concurrently after the embedding call, and identical normalized embeddings are deduplicated and cached.

### Tool turn

`final transcript → Gemini tool decision → filler TTS → Tavily/DB tool → Gemini answer → answer TTS`

The filler improves perceived responsiveness but does not reduce completion time. Tavily still uses a thread around its synchronous API, but the client is now reused.

### Session startup

`POST /start → construct STT/TTS/LLM → memory authentication/conversation → concurrent facts/recent queries → optional prior context → bounded prompt → connect providers → greeting LLM → greeting TTS`

Memory hydration is still performed before pipeline execution, but independent facts/recent-message queries now overlap and prompt sections are bounded.

## 3. Remaining findings

### P0 — Fix before claiming stable latency

#### 3.1 Aggregate one user turn before routing, persistence, and timing

Deepgram can emit multiple finalized `TranscriptionFrame`s for one spoken turn. The current code treats every frame as a new latency turn, saves every fragment as a separate user message, clears dynamic context, and independently routes it. Smart Turn later combines the fragments for one LLM inference.

Evidence from the logs:

- `What are top five documentaries of your` became turn 1;
- `twenty twenty two according to the documents?` became turn 2;
- inference occurred only after the second fragment and Smart Turn completion;
- another direct utterance logged fragments at 12:08:48 and 12:08:50, but inference did not start until 12:08:52.928.

This makes the dashboard inaccurate, fragments conversation history, and can launch redundant or stale retrieval tasks. Introduce a turn coordinator keyed by Pipecat user-turn start/stop events. Accumulate final transcript fragments, route once, persist once, assign one turn ID, and start end-to-end timing at last user audio/VAD stop rather than at each transcript.

#### 3.2 Repair Alembic before any fresh or scaled deployment — completed

`3c5c3ec4e525_initial.py` contains only `drop_index()` operations in `upgrade()` and index creation in `downgrade()`. It does not create the application tables. A fresh database cannot be reliably built from migrations, and applying it to a legacy database removes:

- RAG HNSW vector index;
- RAG GIN full-text index;
- RAG ownership/status indexes;
- episodic-memory vector index.

The baseline, additive migrations, live indexes, timestamp types, and ORM index metadata are now synchronized. Docker applies migrations before starting the backend. A disposable empty-database migration test remains a useful CI addition.

#### 3.3 Finish end-to-end instrumentation

The live panel is useful but currently measures `TranscriptionFrame → first TTSAudioRawFrame`, not speech stop → audio heard by the browser. It resets on transcript fragments and records generated TTS audio before `transport.output()`. It lacks:

- last user audio/VAD stop and Smart Turn decision;
- embedding start/end;
- DB pool wait, vector SQL, and FTS SQL;
- LLM request start versus first text;
- transport enqueue/send and browser playback;
- reconnect state and dropped/buffered audio;
- p50/p95 aggregation persisted across sessions.

Without these boundaries, the dashboard can show a healthy number while the user waited several seconds before final transcription or browser playback.

### P1 — High-value latency improvements

#### 3.4 Reuse provider clients and cache query embeddings — completed

Google/OpenAI clients are reused, and normalized embeddings share in-flight requests plus a bounded TTL/LRU cache keyed by provider, model, content, and dimension.

Expected gain is smaller than provider network latency but consistent, and it reduces connection churn under concurrent calls.

#### 3.5 Parallelize or combine hybrid retrieval — completed

After embedding returns, vector and FTS branches now run concurrently in independent async sessions. The application-level 2.5-second deadline remains the hard bound.

At the current 49 RAG chunks, SQL time is negligible. This becomes important at tens or hundreds of thousands of chunks; correct HNSW/GIN indexes are a prerequisite.

#### 3.6 Tune Smart Turn from recorded speech, not guesses — configurable 1.5 s default applied

`SMART_TURN_STOP_SECS` now defaults to 1.5 seconds instead of Pipecat's 3-second default. Validate false cutoffs and barge-ins against real utterances, pauses, accents, and noisy audio before lowering it further.

#### 3.7 Parallelize and budget session hydration — completed

After token/conversation ownership is established, active facts and recent messages now load concurrently. Explicit character caps apply to:

- facts;
- current summary;
- recent messages;
- prior summary/transcript.

Prior-conversation lookup remains conditional and sequential only for a newly empty conversation. A tokenizer-aware total budget could further refine the current character caps.

#### 3.8 Keep inspection/transcript payloads off the inference gate — completed

The processor now installs dynamic context and releases the user transcription before sending/persisting `rag_call` inspection data. Persisting bounded previews instead of complete selected content remains an optional storage reduction.

#### 3.9 Batch background ingestion embeddings — completed with bounded concurrency

Link/PDF ingestion now uses configurable bounded concurrency (default four) instead of one request at a time. Provider-native batch APIs and a separate ingestion quota remain possible future improvements.

### P2 — Smaller or workload-dependent gains

#### 3.10 Tune transport output buffering — initial reduction applied

Output buffering now defaults to two 10-ms chunks instead of four and is configurable with `AUDIO_OUT_10MS_CHUNKS`. Validate underruns and jitter during longer calls before lowering it further.

#### 3.11 Reuse the Tavily client — completed

Tavily now reuses its client while keeping the synchronous SDK call in a worker thread. A native async transport remains a secondary improvement if the SDK supports one.

#### 3.12 Warm only what affects first-call latency

The first PDF ingestion loads Docling/OCR and embedding dependencies, but voice startup does not proactively warm all provider paths. Warm the configured embedding path and local Smart Turn model at application startup only if memory/CPU limits permit. Avoid warming Docling in the voice process; ingestion should ultimately be a separate worker service.

## 4. What should not be optimized away

- Do not remove Smart Turn and rely only on an aggressively short VAD timeout.
- Do not skip RAG for explicit saved-source questions.
- Do not remove relevance checks merely to return faster.
- Do not put derived-memory LLM work back into the foreground path.
- Do not increase DB pool size before measuring pool wait and PostgreSQL capacity.
- Do not switch providers based on a few outliers while Deepgram reconnects/turn boundaries remain unresolved.

## 5. Recommended implementation order

1. Build a turn-level transcript coordinator and correct latency semantics.
2. Replace the broken Alembic baseline and assert required indexes in CI.
3. Add speech-stop, retrieval-substage, transport, and browser-playback timing.
4. Make Smart Turn silence configurable and run a recorded-utterance benchmark.
5. Reuse embedding/memory clients and add a normalized embedding cache.
6. Parallelize or combine vector and FTS retrieval with SQL timeouts.
7. Parallelize session hydration and enforce prompt budgets.
8. Move RAG inspection payload delivery behind inference and batch ingestion embeddings.
9. Benchmark transport chunk size and Tavily client reuse.

## 6. Suggested SLOs

Measure both end-of-speech and final-transcript latency, segmented by direct/RAG/tool/reconnect:

| Metric | Initial target |
|---|---:|
| Direct final transcript → browser audio p50 | < 1.2 s |
| Direct final transcript → browser audio p95 | < 1.8 s |
| Direct end of speech → browser audio p50 | < 1.5 s |
| Direct end of speech → browser audio p95 | < 2.5 s |
| RAG final transcript → browser answer audio p50 | < 2.3 s |
| RAG retrieval p95 | < 1.2 s, hard timeout 2.5 s |
| Tool acknowledgement audio | < 1.5 s |
| Session signal complete → greeting audio p95 | < 2.0 s |
| STT reconnect recovery p95 | < 3.0 s with no lost completed turn |

## 7. Validation result

- Backend: **34 tests passed**.
- Frontend: ESLint passed.
- Frontend: production build passed.
- Live database: 49 RAG chunks, 11 messages, 1 RAG source at inspection time.
- Alembic is at `20260717_schema_sync` head and reports no model/schema drift.
- Graphify was used for the architecture trace and refreshed after this audit.

The project is in a much healthier state than at the first review. The next major gains will come from treating a spoken turn—not each transcript fragment—as the unit of routing and measurement, then fixing migration/index reproducibility and tuning turn completion with recorded audio.
