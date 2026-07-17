# Aura Voice Latency Reduction Report

**Review date:** 2026-07-17  
**Evidence:** supplied live-call log, current backend/frontend source, refreshed Graphify code graph, and local validation  
**Goal:** reduce perceived voice-response latency without removing RAG, durable memory, conversational continuity, tools, or streaming behavior

## 1. Executive summary

The provider cascade is already reasonably fast. In the three logged user turns, Google LLM first-token latency was **0.598–0.675 s** and Deepgram TTS first-audio latency was **0.319–0.508 s**. The largest consistently avoidable delay is the context-retrieval gate between final STT and the LLM: every authenticated transcription runs hybrid RAG, including ordinary conversation that is ultimately logged as `RAG skipped`.

Measured end-of-turn-to-bot-audio latency was approximately **1.76 s, 2.30 s, and 2.88 s** (mean **2.31 s**). After the final STT event, rejected RAG retrieval alone took approximately **0.60 s, 0.97 s, and 0.56 s** (mean **0.71 s**). The slowest turn also spent about **1.40 s** between smart-turn completion and final STT, so STT/finalization variance is the second major target.

The best low-risk path is:

1. Keep RAG and episodic memory, but route before embedding and enforce a small retrieval deadline.
2. Preserve ordering with one supervised retrieval operation per session/turn.
3. Make retrieved context turn-scoped instead of permanently growing the LLM context.
4. Isolate and coalesce derived-memory jobs so they cannot race or consume the live assistant's quota.
5. Add timestamps around the exact boundaries needed to optimize p50/p95 rather than relying on provider TTFB alone.

With the observed sample, routing irrelevant turns before retrieval should save roughly **0.6–1.0 s** on ordinary turns. That would put the first two observed turns near **1.2–1.3 s** without changing providers or dropping features. The third turn remains limited by STT finalization and needs separate measurement/tuning.

## 2. What the log shows

| Turn | Smart EOT | Final STT event | RAG decision | LLM TTFB | TTS TTFA | Bot audio after EOT |
|---|---:|---:|---:|---:|---:|---:|
| “What is your name?” | 10:31:59.007 | +0.192 s | skipped at +0.788 s | 0.675 s | 0.319 s | 1.762 s |
| “What can you do for me?” | 10:32:07.134 | +0.311 s | skipped at +1.285 s | 0.656 s | 0.508 s | 2.296 s* |
| “Okay. Thank you.” | 10:32:16.136 | +1.403 s | skipped at +1.962 s | 0.598 s | 0.380 s | 2.875 s |

\* Pipecat logged `Bot started speaking` before its later TTFA metric on this turn; the transport event is used for the end-to-end value. This discrepancy is another reason to add application-level correlated timing.

Other observations:

- All three turns paid semantic/vector retrieval cost and returned no RAG context.
- Prompt usage rose from 790 to 819 to 875 tokens. Normal conversation growth explains some of this, but retrieved developer messages are also appended permanently when present.
- A background memory call hit Gemini free-tier quota (`429 RESOURCE_EXHAUSTED`) during the third turn. This call is derived-memory processing, not the foreground voice answer.
- Session construction began at 10:31:49.188, the client connected at 10:31:50.041, and greeting audio began at 10:31:52.008: about **2.82 s** from bot construction or **1.97 s** after client connection.

## 3. Current critical path

For an authenticated user turn, the effective path is:

`audio → VAD/smart-turn → Deepgram final transcript → ContextRetrievalProcessor → (memory lookup || RAG embedding + vector SQL + FTS SQL) → user aggregator → Google LLM → Deepgram TTS → WebRTC output`

`ContextRetrievalProcessor` creates a detached task and deliberately withholds the transcription until both memory and RAG finish. Memory is cheap for non-recall turns because it has a deterministic recall gate. RAG does not have an equivalent pre-retrieval gate: `retrieve_rag_chunks()` embeds first, then performs vector and text searches, and only afterwards decides whether the result is relevant enough to inject.

## 4. Prioritized recommendations

### P0 — High-impact, low-risk changes

#### 4.1 Route before semantic RAG

Add a deterministic, conservative pre-router before `embed_text()`. It should immediately skip retrieval for acknowledgements, greetings, assistant-identity/capability questions, and other conversational control turns. It should retrieve for explicit source/document/link/PDF language, lookup requests, and likely knowledge questions. Preserve recall by using a staged fallback:

1. Check a short-lived per-user cache indicating whether any ready RAG sources exist; skip all retrieval if none exist.
2. Run cheap PostgreSQL FTS first for ambiguous turns.
3. Run semantic embedding/vector search for explicit RAG intent, strong knowledge-seeking intent, or when lexical routing cannot decide.

This preserves semantic RAG where it can help while eliminating the measured 0.6–1.0 s waste on clearly irrelevant turns. The existing `is_rag_query()` is a useful starting point but should be expanded and tested against real transcripts before becoming the only gate.

#### 4.2 Give retrieval a strict latency budget

Wrap turn retrieval in a configurable deadline, initially **250–400 ms**, and continue without dynamic context on expiry. Cancel and await child tasks cleanly. Log timeout/fallback as a metric, not an error. Static facts and recent conversation context are already loaded at session start, so ordinary conversation remains fully functional when dynamic retrieval misses its budget.

For explicit document questions, a slightly larger budget or a short spoken filler can be used. Speak filler only if retrieval crosses a delay threshold; do not speak it immediately for work that may finish quickly.

#### 4.3 Preserve turn ordering and cancellation

Do not use an untracked `asyncio.create_task()` per transcription. Rapid turns can complete retrieval out of order and push older transcription after newer transcription; tasks can also outlive disconnect. Use a per-session task reference or ordered queue:

- assign a monotonically increasing turn ID;
- cancel superseded retrieval when barge-in semantics make the old turn obsolete;
- otherwise serialize delivery in turn order;
- cancel and await the task during pipeline cleanup.

#### 4.4 Make dynamic context turn-scoped

RAG and episodic-memory messages are currently added to the shared `LLMContext` and never removed. This increases token count, cost, and LLM latency over a long call and can make stale retrieved content influence later questions. Attach retrieval only to the current inference, or remove/tag the injected messages after `LLMFullResponseEndFrame`. Keep stable facts, summary, and recent transcript persistent; keep query-specific retrieval ephemeral.

### P1 — Stabilize memory and database work

#### 4.5 Separate foreground and derived-memory provider budgets

The background pipeline classifies every user message with Gemini, even turns such as “Okay, thank you.” It then may summarize and embed. Add a cheap deterministic fact-candidate gate before calling the memory LLM, coalesce summary work, and use a separate API project/key, rate limiter, and circuit breaker for derived memory. On 429, respect retry metadata and stop sending more classification work during the backoff window.

This protects foreground voice capacity and reduces cost without removing durable memory; likely durable statements still go through the existing classifier.

#### 4.6 Enforce per-conversation job ordering

The in-memory queue has three global workers. User and assistant post-processing for the same conversation can execute concurrently, regenerate summaries from different snapshots, and upsert overlapping memory chunks. Partition work by conversation ID or use a keyed lock, and coalesce “update summary/chunk” jobs. Commit raw messages immediately, then process one derived state transition per conversation.

The queue should also drain with `queue.join()` on graceful shutdown or durably persist work. Its current `stop()` cancels workers immediately and can discard queued memory/RAG work.

#### 4.7 Avoid repeated full-conversation scans

`update_conversation_summary_if_needed()` counts and then loads every message for each user and assistant message. Once thresholds are crossed it can regenerate a summary repeatedly. Store message/character counters, summarize incrementally at checkpoints, and load only the unsummarized suffix. Build episodic chunks from explicit message windows rather than re-querying the entire conversation.

#### 4.8 Optimize hybrid retrieval execution

After routing is fixed, reduce latency on true RAG turns:

- cache normalized query embeddings for short periods;
- reuse embedding clients rather than constructing Google/OpenAI clients per call;
- run vector and FTS branches concurrently using separate sessions/connections, or benchmark a single SQL query that unions/ranks both branches;
- batch ingestion embeddings so ingestion does not compete with live retrieval;
- set database statement timeouts below the overall retrieval budget;
- measure pool wait separately from SQL execution.

Do not increase database pool sizes blindly: the current 20 + 10 overflow connections per process can overload PostgreSQL when replicas are added.

### P2 — Provider and transport tuning after the critical path is bounded

#### 4.9 Measure and tune final-transcript delay

The third turn took about 1.40 s from smart EOT to the logged final STT event versus 0.19–0.31 s for the first two. Record speech-stop audio time, VAD decision, smart-turn completion, interim/final transcript arrival, and provider connection state with one session/turn ID. Then test Deepgram endpointing/utterance-end configuration against interruptions and sentence completeness. Do not lower endpointing globally without a corpus-based false-cutoff test.

#### 4.10 Reduce TTS start variance

Deepgram TTS TTFA ranged from 0.319–0.508 s and one turn had 205 ms leading silence. Update to the current settings API (the log shows the `voice` argument is deprecated), explicitly configure latency-relevant audio/stream settings, and measure first text frame → request sent → first provider audio → first transport audio. Keep streaming sentence aggregation, but test shorter first clauses so the assistant begins speaking sooner without producing choppy prosody.

#### 4.11 Parallelize session initialization safely

`load_memory_bundle()` runs before the worker/pipeline is started and contributes to call startup. Fetch independent facts, recent messages, current summary, and prior-conversation context concurrently where database capacity permits. Cache stable facts briefly per user. Initialize provider objects and the memory bundle concurrently only if provider constructors/connectors are safe to do so. Target greeting audio p95 below 1.5–2.0 s after signaling completes.

#### 4.12 Control prompt size

The initial prompt contains stable facts, current summary/recent messages, prior-conversation transcript, tools, and system instructions. Apply explicit token budgets to each section, prefer a compact prior summary over a verbatim prior transcript, and cap tool schemas/context. Track prompt tokens per turn and alert on abnormal growth. This retains continuity while keeping LLM TTFB stable over long sessions.

## 5. Instrumentation required before further provider changes

Add one structured event per boundary with `session_id`, `conversation_id`, `turn_id`, and monotonic timestamp:

- speech start / last audio packet / VAD stop / smart-turn complete;
- final STT received;
- route decision and reason;
- embedding start/end;
- DB pool wait and vector/FTS start/end;
- context accepted/skipped/timed out;
- LLM request and first token;
- first TTS text, provider first audio, transport first audio;
- interruption received and output audio stopped.

Primary SLOs should be segmented by no-retrieval, RAG, memory recall, and tool turns. A useful initial target for no-tool/no-retrieval turns is final-STT-to-first-audio **p50 < 900 ms, p95 < 1.5 s**. Also track end-of-speech-to-first-audio, because that is what the user perceives.

## 6. Suggested implementation sequence

1. Add correlated latency spans and a replayable transcript benchmark.
2. Add RAG pre-routing, ready-source cache, and a retrieval deadline.
3. Replace detached retrieval with ordered/cancellable session state.
4. Make retrieved context turn-scoped.
5. Gate, rate-limit, and serialize derived-memory work per conversation.
6. Parallelize/merge vector and FTS retrieval and reuse embedding clients.
7. Tune STT endpointing and TTS settings using recorded utterances.
8. Optimize startup memory loading and prompt budgets.

## 7. Validation findings from this review

- Graphify was stale/broken (`graphifyy` 0.8.14 behind the installed skill); its tool environment was updated and the code graph rebuilt to **444 nodes, 841 edges, 35 communities**.
- Backend tests currently fail during collection because `tests/test_rag.py` imports `RollingVoiceQueryBuffer` from `main`, but the class moved to `core.processors`.
- Frontend lint currently fails on three unused imports in `Sidebar.jsx` and `TranscriptPanel.jsx`.
- The Deepgram TTS constructor emits a deprecation warning for the `voice` argument.

These findings are appended to `issues.md`; no application behavior was changed during this audit.
