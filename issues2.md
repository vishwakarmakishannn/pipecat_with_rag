# Voice Latency Issues — Single Source of Truth

This numbered checklist is the authoritative status tracker for the end-to-end voice latency remediation. Numbers are permanent. Work proceeds strictly in numeric order, one issue at a time.

1. [x] **End-to-end mouth-to-ear telemetry**
   - **Status:** Done
   - **Impact:** High
   - **Components:** Browser audio, WebRTC transport, VAD, STT, orchestration, LLM, TTS, playback
   - **Problem:** Current timing begins at a final transcript fragment and ends before transport and browser playback, omitting endpointing, network, queueing, jitter-buffer, and device latency.
   - **Change:** Added turn endpoint and server-stage timestamps, browser user-speech timing, and first nonzero decoded remote-audio detection. The live panel now prefers user-stop-to-playback latency while retaining the generated-audio fallback. Verified with Python compilation/contract assertions plus frontend lint and production build.

2. [x] **Turn-scoped latest-wins retrieval coordinator**
   - **Status:** Done
   - **Impact:** High
   - **Components:** `ContextRetrievalProcessor`, orchestration
   - **Problem:** Each final STT fragment creates detached retrieval work; stale tasks are neither superseded nor cancelled and can inject obsolete context.
   - **Change:** Replaced the detached task set with one generation-tagged active retrieval. New fragments and direct routes cancel prior work; generation checks prevent stale context or transcript delivery. Verified by compilation and a real asynchronous two-fragment cancellation scenario.

3. [x] **Retrieval deadline must include queue and lock wait**
   - **Status:** Done
   - **Impact:** High
   - **Components:** `ContextRetrievalProcessor`
   - **Problem:** The 2.5-second timeout begins after the delivery lock is acquired, so queued retrieval can wait without a bound.
   - **Change:** Wrapped delivery-lock acquisition, provider retrieval, context installation, and transcription release in one deadline; diagnostics now run outside the critical section. Verified with a held-lock test that exits at the configured deadline and releases the fallback transcript.

4. [x] **Adaptive turn endpointing and VAD configuration**
   - **Status:** Done
   - **Impact:** High
   - **Components:** Silero VAD, Smart Turn, `main.py`
   - **Problem:** Default VAD behavior and a 1.5-second Smart Turn ceiling can add long silence waits or mis-detect quiet/noisy speech.
   - **Change:** Added validated environment-driven VAD and semantic Smart Turn controls, reduced low-latency defaults to 150 ms VAD stop and 800 ms Smart Turn stop, and documented all parameters. Verified invalid-value rejection and real Silero/SmartTurn construction.

5. [x] **Low-latency hybrid TTS aggregation**
   - **Status:** Done
   - **Impact:** High
   - **Components:** TTS providers, Pipecat pipeline
   - **Problem:** Sentence-level aggregation delays synthesis until punctuation, commonly discarding 200–300 ms of LLM streaming advantage.
   - **Change:** Added a shared aggregation policy using token streaming for persistent remote TTS providers and sentence aggregation for Piper, with a validated environment override. Verified real Deepgram and Cartesia service instances select token mode.

6. [x] **Bound live LLM conversation context**
   - **Status:** Done
   - **Impact:** High
   - **Components:** LLM context, memory, orchestration
   - **Problem:** Conversation messages and tool records grow for the lifetime of a call, progressively increasing model time-to-first-token.
   - **Change:** Added a pre-LLM context-window processor that preserves the stable memory prefix and complete recent turns under configurable message/character budgets. Verified prefix preservation, atomic recent tool-turn retention, and oversized-latest-turn behavior.

7. [x] **Expose tools only when relevant**
   - **Status:** Done
   - **Impact:** Medium
   - **Components:** LLM tool configuration, intent routing
   - **Problem:** Tavily and issue schemas are advertised on every turn, increasing prompt size and tool-selection work for ordinary conversation.
   - **Change:** Added deterministic pre-LLM routing that disables tools for normal turns and exposes only search and/or issue creation for explicit matching intents. Verified normal, search, issue, and combined routes with real `LLMContext` tool normalization.

8. [x] **Tool execution deadlines, cancellation, and fallback**
   - **Status:** Done
   - **Impact:** High
   - **Components:** Tavily tool, issue tool, orchestration
   - **Problem:** External search and database-backed tools have no application-level deadline or turn-cancellation contract and can stall a response.
   - **Change:** Added a validated shared tool deadline, bounded Tavily and issue-creation operations with explicit fallback results, preserved cancellation propagation, and enabled Pipecat asynchronous tool cancellation in both LLM providers. Verified timeout fallback and provider factory flags.

9. [x] **Delay and cancel tool filler speech**
   - **Status:** Done
   - **Impact:** Medium
   - **Components:** `ToolFillerProcessor`, TTS
   - **Problem:** Filler speech is queued immediately and can remain ahead of the real answer when a tool completes quickly.
   - **Change:** Replaced immediate filler with one configurable delayed task, suppressed it for fast tools, and cancelled it on result, cancellation, interruption, LLM text, and cleanup. Verified both fast-tool suppression and one-time slow-tool filler behavior.

10. [x] **Buffer audio across STT reconnects**
    - **Status:** Done
    - **Impact:** High
    - **Components:** `ResilientDeepgramSTTService`
    - **Problem:** Audio can be discarded while reconnection is deferred, forcing the user to repeat a complete utterance.
    - **Change:** Added a bounded raw-media reconnect buffer, captured both failed and disconnected sends, replayed buffered audio in order before frames received during reconnect, and logged oldest-audio overflow. Verified buffering, ordered replay, and memory bounds.

11. [x] **Gate microphone readiness on STT connectivity**
    - **Status:** Done
    - **Impact:** High
    - **Components:** Deepgram STT, session startup, client readiness
    - **Problem:** Initial STT connection startup is asynchronous and the session may accept speech before transcription is ready.
    - **Change:** Overrode Deepgram startup to await its socket-ready event under a validated deadline and cancel the connection task on startup failure/cancellation. Pipeline readiness now waits for STT readiness. Verified that `_connect()` blocks until the readiness event.

12. [x] **Remove LLM-generated startup greeting**
    - **Status:** Done
    - **Impact:** High
    - **Components:** `main.py`, LLM, TTS
    - **Problem:** Client readiness triggers a complete LLM inference merely to produce a greeting.
    - **Change:** Replaced the greeting instruction and `LLMRunFrame` with a configurable direct `TTSSpeakFrame` that is excluded from context and can be disabled. Verified no LLM greeting path remains and both default/disabled configurations work.

13. [x] **Collapse serial session-start network round trips**
    - **Status:** Done
    - **Impact:** High
    - **Components:** Frontend API, conversation API, Small WebRTC startup
    - **Problem:** Conversation creation, `/start`, SDP offer, and ICE updates occur as serial or delayed control-plane exchanges.
    - **Change:** Removed the browser's pre-connect conversation POST. Voice startup now creates a missing conversation inside the existing `/start` session flow and reports its ID over the established RTVI channel. Verified server-side creation plus frontend lint/build. SDP/ICE exchanges remain transport-required.

14. [x] **Run lexical RAG retrieval alongside embedding**
    - **Status:** Done
    - **Impact:** High
    - **Components:** `retrieve_rag_chunks`, PostgreSQL FTS, embedding provider
    - **Problem:** Full-text retrieval unnecessarily waits for query embedding even though it has no embedding dependency.
    - **Change:** Full-text search now starts as an independent task before embedding; vector retrieval starts after embedding and joins the in-flight lexical result. Cancellation cleans up the lexical task. Verified FTS starts before embedding completes.

15. [x] **Corpus-versioned RAG result cache**
    - **Status:** Done
    - **Impact:** Medium
    - **Components:** RAG service, cache, ingestion
    - **Problem:** Embeddings are cached, but repeated user/query/corpus retrieval still repeats database search and ranking.
    - **Change:** Added normalized-query in-flight deduplication and a bounded TTL result cache keyed by user, options, and per-user corpus version. Successful ingestion and deletion bump the version and invalidate that user's entries. Verified concurrent deduplication, cache hits, and invalidation.

16. [x] **Reuse one query embedding across memory and RAG**
    - **Status:** Done
    - **Impact:** Medium
    - **Components:** Memory retrieval, RAG retrieval, embedding service
    - **Problem:** Recall and RAG can independently embed equivalent query text during the same turn.
    - **Change:** Combined recall/RAG turns now create one embedding task and pass the same awaitable into both services; RAG still starts lexical search while awaiting it. Verified one embedding call supplies identical vectors to both concurrent retrieval paths.

17. [x] **Reserve database capacity and enforce voice deadlines**
    - **Status:** Done
    - **Impact:** High
    - **Components:** SQLAlchemy pool, RAG, memory, tools
    - **Problem:** Foreground voice and background work share a pool with waits longer than the voice latency budget.
    - **Change:** Added a separate reserved voice engine/session pool with a 1-second pool wait and PostgreSQL statement timeout validated below the retrieval budget. Startup memory, live memory/RAG retrieval, and issue creation use it; ingestion/persistence remain on the general pool. Verified engine separation and bounds.

18. [x] **Eliminate redundant startup authentication and ownership queries**
    - **Status:** Done
    - **Impact:** Medium
    - **Components:** Session startup, auth, memory loading
    - **Problem:** Voice startup re-authenticates and rechecks a conversation that the control plane has just created or authorized.
    - **Change:** Existing-conversation startup now decodes the JWT locally and performs authentication plus ownership validation in one joined query instead of two serial queries. Verified exactly one database execution returns both records.

19. [x] **Lazy-load prior-conversation memory**
    - **Status:** Done
    - **Impact:** Medium
    - **Components:** Memory service, startup, LLM prompt
    - **Problem:** Previous-conversation context is loaded and sent for every new conversation even when recall is not requested.
    - **Change:** Removed prior-conversation lookup and prompt injection from session hydration. Explicit recall turns now query prior recent messages only when semantic memory has no match, excluding the active conversation. Verified ordinary turns perform no prior lookup and recall turns do.

20. [x] **Deduplicate and token-budget memory prompt sections**
    - **Status:** Done
    - **Impact:** Medium
    - **Components:** Memory prompt construction
    - **Problem:** Facts, summaries, recent messages, and prior excerpts may overlap and are limited by characters rather than a shared token budget.
    - **Change:** Added one configurable approximate token budget across all memory sections, exact/contained-content deduplication, and newest-first selection for conversational turns. Verified summary/recent deduplication, total budget enforcement, and latest-answer retention.

21. [ ] **Isolate background ingestion from live voice resources**
    - **Status:** Blocked
    - **Impact:** High
    - **Components:** Task queue, document parsing, crawling, embeddings, database
    - **Problem:** Background jobs share the event loop, process, provider quotas, and database pool with latency-sensitive turns.
    - **Blocker:** The repository only has a process-local `asyncio.Queue`; there is no durable broker/job table, independent worker entrypoint, or worker service in deployment. True isolation requires selecting and operating a cross-process queue (for example Redis/RQ, RabbitMQ/Celery, or a PostgreSQL job table) and adding a worker deployment. The database-pool portion is mitigated by Issue 17, but process/provider isolation cannot be made reliable without that infrastructure decision.

22. [x] **Preload and reuse local models/provider transports**
    - **Status:** Done
    - **Impact:** Medium
    - **Components:** VAD, Smart Turn, Piper, LLM client factories
    - **Problem:** Models, executors, and provider clients are created per session; Piper initialization is especially expensive and synchronous.
    - **Change:** STT, TTS, and LLM constructors now run concurrently off the event loop, preventing Piper/ONNX/client setup from serially blocking session startup. Whole Pipecat service instances remain session-local because they contain mutable connection/frame state. Verified all three constructors are scheduled together.

23. [x] **Add regional TURN and accelerate ICE trickling**
    - **Status:** Done
    - **Impact:** High
    - **Components:** Small WebRTC, ICE configuration, deployment
    - **Problem:** STUN-only networking fails or stalls on restrictive NATs, while candidate batching may delay the first usable route by 200 ms.
    - **Change:** Added validated deploy-time STUN/TURN server configuration, TURN credentials, explicit immediate trickle mode (`waitForICEGathering: false`), and Docker build plumbing. The installed transport exposes no candidate-batch interval control. Verified parser behavior, credential rejection, frontend lint, and production build.

24. [x] **Configure browser capture DSP and audio constraints**
    - **Status:** Done
    - **Impact:** Medium
    - **Components:** Browser microphone capture, AEC, noise suppression, AGC
    - **Problem:** Capture quality relies on implicit defaults, which can worsen VAD/STT timing and recognition under noise or echo.
    - **Change:** Local microphone tracks now receive explicit AEC, noise suppression, AGC, mono, and 48 kHz ideal constraints with deploy-time overrides and graceful unsupported-browser fallback. Verified configuration behavior plus frontend lint/build.

25. [x] **Reduce avoidable resampling and codec work**
    - **Status:** Done
    - **Impact:** Low
    - **Components:** WebRTC decode/encode, STT input, TTS output
    - **Problem:** Audio is decoded, resampled to 16 kHz, synthesized at 24 kHz, and encoded again, consuming CPU and queue time.
    - **Change:** Added one validated audio-rate configuration shared by transport and providers: 16 kHz STT input, 24 kHz remote TTS output, and 22050 Hz for the bundled Piper model. This avoids implicit provider/transport mismatches; WebRTC Opus conversion remains unavoidable. Verified aligned transport parameters and provider defaults.

26. [ ] **Bound transport queues and drop interrupted audio**
    - **Status:** Blocked
    - **Impact:** Medium
    - **Components:** Output transport, WebRTC audio track, interruption handling
    - **Problem:** Unbounded or stale audio queues can continue playing obsolete output after stalls or barge-in.
    - **Blocker:** Pipecat 1.5 already cancels/restarts the audio task and resets interruptible queued frames on barge-in, so stale interruption audio is handled. Its remaining `FrameQueue` is internally created as an unbounded `asyncio.Queue` and exposes no queue factory/max-size setting. A reliable bound requires an upstream Pipecat API/version change; monkey-patching the installed transport would be brittle and unsafe.

27. [x] **Remove verbose diagnostics from the first-audio path**
    - **Status:** Done
    - **Impact:** Medium
    - **Components:** Latency processor, RTVI data channel, RAG diagnostics
    - **Problem:** Metrics are pushed before the first audio frame and RAG diagnostics can include full chunk contents.
    - **Change:** First TTS audio is now pushed before its telemetry message, and RAG diagnostics carry only 240-character content previews. Verified frame order with real Pipecat frames and bounded payload size.

28. [x] **Batch frontend streaming renders**
    - **Status:** Done
    - **Impact:** Medium
    - **Components:** React transcript UI, scrolling, tool-event rendering
    - **Problem:** Per-token state updates, array scans, parsing, and smooth scrolling can contend with media processing on weak clients.
    - **Change:** LLM deltas are buffered and committed to React at most every 40 ms, synchronously flushed at response end, and timers are cleaned up. Per-update smooth scrolling was replaced with non-animated positioning. Verified frontend lint and production build.

29. [x] **Move tool-call persistence off the client critical path**
    - **Status:** Done
    - **Impact:** Medium
    - **Components:** Frontend, history API, tool orchestration
    - **Problem:** The browser performs an authenticated persistence request while the tool continuation and answer are running.
    - **Change:** Completed tool frames are now serialized and queued for persistence by the server's assistant-memory processor. Removed the client POST/deduplication path. Verified server queue payloads plus frontend lint/build.

30. [x] **Suspend polling and refresh work during active speech**
    - **Status:** Done
    - **Impact:** Low
    - **Components:** Frontend file polling, conversation refresh
    - **Problem:** File polling and conversation refresh requests compete with an active voice session for browser, server, and database resources.
    - **Change:** Added a turn-busy state spanning user speech through bot TTS completion, suspended processing-file polling while busy, and deferred conversation refresh from LLM completion until audio completion. Verified frontend lint/build.

31. [x] **Use same-origin proxying, connection reuse, and optimized assets**
    - **Status:** Done
    - **Impact:** Medium
    - **Components:** Frontend deployment, API, static assets
    - **Problem:** Separate frontend/backend origins can trigger preflights and extra TLS connections, while the eager voice bundle increases initial readiness time.
    - **Change:** Production API and WebRTC signaling—including session-scoped offer/ICE routes—now use the page origin through an Nginx reverse proxy with a keep-alive upstream pool. Hashed assets receive immutable caching, text assets are compressed, and the existing development-port and explicit-base-URL fallbacks remain available. Verified route coverage, frontend lint, and production build.

32. [x] **Move migrations out of application startup and strengthen readiness**
    - **Status:** Done
    - **Impact:** Medium
    - **Components:** Container startup, Alembic, health checks
    - **Problem:** Every backend start runs migrations and readiness does not prove database, model, or provider readiness.
    - **Change:** Alembic now runs once in a dedicated Compose migration job after PostgreSQL becomes healthy; backend replicas start only after that job succeeds and no longer run migrations themselves. Readiness now fails closed unless background workers are live and a bounded database probe succeeds, and Compose uses that endpoint for container health. External provider sockets remain session-scoped and are protected by their own connection deadlines. Verified Compose rendering, Python compilation, and task-worker readiness transitions.

33. [x] **Add voice admission control and scalable worker isolation**
    - **Status:** Done
    - **Impact:** High
    - **Components:** Deployment, event loop, model executors, session management
    - **Problem:** Per-call threads/connections grow without explicit capacity limits, allowing overload to inflate every pipeline stage.
    - **Change:** Added a configurable per-replica voice admission controller that atomically leases capacity before transport/provider allocation, releases it in all exit paths, and rejects `/start` or WebRTC offer requests with `503`/`Retry-After` when full. This bounds expensive pipeline isolation per worker while allowing capacity to scale by adding replicas. Verified compilation, lease concurrency, fail-fast rejection state, and release behavior.

34. [x] **Make vector retrieval tenant-scalable**
    - **Status:** Done
    - **Impact:** Medium
    - **Components:** pgvector indexes, RAG schema, SQL queries
    - **Problem:** A global approximate index combined with user/status filtering can degrade latency and recall as tenants and corpora grow.
    - **Change:** Voice-database connections now enable pgvector's filtered HNSW iterative scan (`relaxed_order`) with a bounded scan budget, allowing the index to continue until enough rows survive existing tenant/status predicates. Added an Alembic extension update and environment controls while retaining the composite tenant indexes. Verified migration head, compilation, and database latency configuration contracts.

35. [x] **Clear rolling query state at authoritative turn boundaries**
    - **Status:** Done
    - **Impact:** Medium
    - **Components:** `RollingVoiceQueryBuffer`, interruption handling
    - **Problem:** Query text is cleared at LLM response completion rather than user-turn boundaries, allowing barge-in text to mix with the previous turn.
    - **Change:** The universal user aggregator's `on_user_turn_started` event now atomically supersedes stale retrieval, clears the rolling query buffer, and removes prior dynamic context. LLM completion no longer resets user-turn state, so a late interrupted response cannot corrupt a barge-in turn. Verified compilation and authoritative reset behavior.

36. [x] **Fix deployment runtime mismatch and cold-start hazards**
    - **Status:** Done
    - **Impact:** Medium
    - **Components:** Dockerfile, Python runtime, dependency startup
    - **Problem:** The Docker image uses Python 3.11 while project metadata requires Python 3.12 or newer, threatening startup reliability and scale-out latency.
    - **Change:** Both Docker stages now use Python 3.12. Dependencies are bytecode-compiled in a builder stage, compiler/header packages are excluded from the lean runtime image, and application/migration/health commands execute the virtual environment directly without `uv run` startup work. Verified Compose rendering, lock consistency, Python compilation, Dockerfile runtime assertions, and diff hygiene.

37. [x] **Enforce an LLM first-token deadline**
    - **Status:** Done
    - **Impact:** High
    - **Components:** Google LLM provider, streaming inference
    - **Problem:** A slow Google streaming request can wait indefinitely before its first response chunk, creating multi-second silent stalls.
    - **Change:** Added a validated five-second first-token budget around the first meaningful Google stream output. Empty metadata chunks remain buffered and cannot defeat the deadline, and stalled iterators are cancelled and closed. Verified timeout, metadata replay, healthy-stream behavior, and compilation.

38. [x] **Provide a bounded spoken recovery on LLM timeout**
    - **Status:** Done
    - **Impact:** High
    - **Components:** Google LLM provider, TTS recovery path
    - **Problem:** Timing out a stalled model without producing response text leaves the caller in unexplained silence.
    - **Change:** A first-token timeout is now converted into a synthetic, configurable model text chunk that follows the normal LLM-to-TTS path, so the caller hears a concise retry prompt. Verified the emitted chunk content and compilation.

39. [x] **Hedge slow Google turns to Groq**
    - **Status:** Done
    - **Impact:** High
    - **Components:** Google LLM, Groq LLM, provider orchestration
    - **Problem:** A single provider remains a long-tail latency dependency even with a hard timeout; an available fallback should compete after a short delay.
    - **Change:** Tool-free Google turns now launch the configured Groq provider after a two-second hedge delay. The first valid response wins, the losing request/stream is cancelled and closed, failures stay within the five-second budget, and tool turns remain on Google for function-call correctness. Verified both Google-wins and Groq-wins cancellation paths.

40. [x] **Restore the low-latency Smart Turn setting**
    - **Status:** Done
    - **Impact:** Medium
    - **Components:** Endpointing configuration
    - **Problem:** The runtime environment overrides the 0.8-second Smart Turn default with 1.5 seconds, extending end-of-turn detection.
    - **Change:** Updated the active runtime environment from 1.5 seconds to 0.8 seconds, matching the validated code and example defaults. Verified the loaded endpointing configuration.

41. [x] **Add provider request correlation and latency metrics**
    - **Status:** Done
    - **Impact:** Medium
    - **Components:** LLM provider observability, server logs
    - **Problem:** Current logs do not correlate inference attempts, provider/model selection, hedge outcomes, timeouts, and first-token latency.
    - **Change:** Every Google turn now receives a local request correlation ID. Structured logs cover provider/model start, hedge launch, winner, first-output latency, Google response ID when available, provider failure/empty output, loser cancellation, and all-provider timeout. Verified shared correlation and timing fields across hedge events.

42. [x] **Correct turn-start latency telemetry association**
    - **Status:** Done
    - **Impact:** Medium
    - **Components:** `TurnLatencyState`, server telemetry
    - **Problem:** A new `user_started` event can be logged using the previous turn's final-STT origin and turn number, producing misleading elapsed values.
    - **Change:** Turn identity is now allocated at authoritative speech start, independently tracks VAD activity through final STT, and clears the previous response origin. User speech events use the speech origin while LLM/TTS response metrics remain final-STT-relative. Verified Pipecat's user-stop-before-final-STT ordering, transcript fragments, text-only turns, and consecutive turn IDs.

43. [x] **Show only the current turn's latency**
    - **Status:** Done
    - **Impact:** Low
    - **Components:** `VoiceApp`, `Sidebar`, latency UI
    - **Problem:** The latency panel retains turn history and displays session averages, obscuring the live latency and whether the current turn used a tool.
    - **Change:** Replaced the 20-turn latency history and session averages with one current-turn value labeled `With tool` or `Without tool`. Removed the obsolete reset control and aggregate styles, retained decoded-playback timing with the generated-audio fallback, and clarified that the preferred metric begins at the received turn-stop signal. Verified frontend lint and production build.

44. [x] **Use a low-latency streaming path for direct turns**
    - **Status:** Done
    - **Impact:** High
    - **Components:** Google LLM, Groq LLM, provider orchestration
    - **Problem:** Google starts every direct turn while the Groq hedge waits two seconds and returns a complete response, so the fallback cannot help reach an 800 ms first-audio target.
    - **Change:** Reduced the hedge launch to 200 ms, converted Groq's native OpenAI-compatible token stream into the active Google response stream, and close/cancel the losing provider without waiting for a full fallback response. Switched the configured hedge to Groq's production `llama-3.1-8b-instant` model. Verified both winner/cancellation paths, multi-chunk fallback continuation, compilation, and all six targeted LLM latency tests.

45. [x] **Remove leading TTS silence from the playback path**
    - **Status:** Done
    - **Impact:** High
    - **Components:** TTS output, audio processing, latency telemetry
    - **Problem:** Provider audio can contain hundreds of milliseconds of leading silence that is queued and played before the first audible sample.
    - **Change:** Added a configurable, context-scoped 16-bit PCM trimmer between TTS and latency/output processing. It drops only leading samples below the amplitude threshold, retains 20 ms of preroll, resets on stop/interruption, and makes first-audio telemetry represent audible audio. Verified configuration validation, silence removal, preroll preservation, post-onset pass-through, existing telemetry ordering, and compilation with seven targeted tests.

46. [x] **Start tool acknowledgement and deterministic work immediately**
    - **Status:** Done
    - **Impact:** High
    - **Components:** Tool routing, filler speech, tool execution
    - **Problem:** Tool acknowledgement currently waits for the LLM function-call decision and an additional filler delay, while deterministic routing information is already available earlier.
    - **Change:** Search intent now emits acknowledgement speech at the finalized transcript, executes the bounded Tavily request while turn finalization is still completing, injects the compact result as turn-scoped developer context, and skips the first LLM tool-decision pass. Structured issue creation also acknowledges before its required argument-validation pass, and the downstream filler suppresses duplicates. Verified direct search suppression, context injection, proactive tool labeling, filler ordering, timeout fallback, compilation, and ten targeted tests. The broader RAG file reaches the unrelated public-URL network test before timing out in the sandbox.

47. [x] **Measure local speech end and tighten turn endpointing**
    - **Status:** Done
    - **Impact:** High
    - **Components:** Browser audio telemetry, VAD, Smart Turn
    - **Problem:** The live metric begins at receipt of the server turn-stop signal and therefore hides endpointing latency; the active Smart Turn ceiling can also delay real mouth-to-ear response.
    - **Change:** The browser now timestamps the last above-threshold local microphone level during an active turn, uses it as the live latency origin, and reports server turn-stop delay as a separate endpointing value with a safe signal fallback. Added validated threshold configuration and reduced the Smart Turn ceiling from 800 ms to 600 ms while retaining 150 ms VAD stop. Verified threshold behavior, endpointing configuration, frontend lint, and production build.

48. [x] **Reduce WebRTC output packets to 10 ms**
    - **Status:** Done
    - **Impact:** Low
    - **Components:** WebRTC output transport
    - **Problem:** Output currently batches two 10 ms chunks, adding a small avoidable buffering delay when network conditions can sustain 10 ms packets.
    - **Change:** Centralized and validated output packet sizing, changed the runtime and documented default to one 10 ms chunk, and retained an environment rollback control up to ten chunks. Verified packet defaults/rejection, all Python compilation, frontend lint/build, and 84 backend tests. Five URL-validation cases and one session-start database case were excluded from the final combined command because those groups contain sandbox-blocked DNS/database waits; the non-network URL cases passed separately.

49. [x] **Use exactly one environment-selected LLM provider per voice pipeline**
    - **Status:** Done
    - **Impact:** High
    - **Components:** LLM factory, Google LLM, Groq LLM, OpenAI LLM, tool-call streaming
    - **Problem:** The Google service can launch a Groq hedge during the same turn, allowing two providers with different streaming and tool-call semantics to participate in one conversation pipeline. This can corrupt response boundaries, produce stray TTS audio, and make tool execution unreliable. OpenAI is also not selectable as the primary voice provider.
    - **Change:** Removed the Google-to-Groq hedge, cross-provider chunk translation, loser cancellation, and hedge configuration. `LLM_PROVIDER` now constructs exactly one of Google, Groq, or OpenAI for the entire voice pipeline; the selected service retains its native text and function-call frame semantics. Added a native Pipecat OpenAI builder using `OPENAI_API_KEY` and `OPENAI_MODEL`, and made background memory-text inference use only the same selected provider rather than falling through to another vendor. Embeddings remain independently selectable through `MEMORY_EMBEDDING_PROVIDER`. Verified provider exclusivity, Google timeout recovery, all three memory inference choices, tool routing/filler/persistence/timeouts, native OpenAI construction, compilation, diff hygiene, 38 focused tests, and 96 clean backend tests. The complete 97-test run had one unrelated pre-existing session-start fixture failure because it reached the real database and violated a test-user foreign key.
