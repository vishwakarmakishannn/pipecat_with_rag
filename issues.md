# Realtime Voice Latency and Reliability Backlog

This file is the authoritative implementation checklist for the 500 ms latency
work. Items are numbered so runtime measurements, commits, and follow-up
experiments can refer to one stable identifier.

Status legend:

- `[ ]` Pending
- `[~]` In progress
- `[x]` Implemented and verified
- `[-]` Requires a deployment decision, provider experiment, or production data

The latency targets use three separate clocks:

1. **Server response latency:** completed user turn to first audible TTS frame.
2. **End-to-ear latency:** last detected user speech sample to decoded browser audio.
3. **Extended-turn latency:** RAG or tool turn to useful audio/filler.

A 500 ms objective applies to warmed, direct conversational turns. RAG and tool
turns have separate budgets because their external work cannot be hidden
reliably inside the same deadline.

## Numbered issues

1. [x] **Define measurable latency SLOs and a repeatable benchmark.**
   - Direct warm target: server response p50 <= 450 ms and p95 <= 700 ms.
   - Direct warm target: browser end-to-ear p50 <= 600 ms initially, then pursue
     <= 500 ms after endpointing/provider experiments.
   - Report cold first turns separately.
   - Report direct, RAG, and tool turns separately.
   - Add a utility that summarizes JSONL telemetry as count/p50/p95/p99/min/max.
   - Done: `backend/scripts/summarize_voice_latency.py` groups persisted turns by
     category and cold/warm state and reports min/p50/p95/p99/max.

2. [x] **Send Groq completion controls through `GroqLLMSettings.extra`.**
   - Remove completion parameters from `client_kwargs.extra_body`; that location
     configures the client, not chat completions, in the installed Pipecat version.
   - Send `parallel_tool_calls=false` through `GroqLLMSettings.extra`.
   - For GPT-OSS models, send configurable `reasoning_effort=low` and
     `include_reasoning=false`.
   - Do not send GPT-OSS-only parameters to non-GPT-OSS models.
   - Add factory regression tests that inspect the exact settings.
   - Done: request controls now live in `GroqLLMSettings.extra`; regression tests
     prove `client_kwargs` is absent and GPT-OSS-only fields are model-gated.

3. [x] **Make the low-latency Groq model policy explicit.**
   - Keep the model configurable.
   - Default new deployments to `llama-3.1-8b-instant`.
   - Preserve GPT-OSS as an A/B option with low reasoning.
   - Record model and reasoning policy in every provider timing log.
   - Done: the code and active runtime default to `llama-3.1-8b-instant`; GPT-OSS
     remains configurable with validated low/medium/high effort.

4. [x] **Instrument Groq request creation, raw first chunk, meaningful first
   output, completion, and usage.**
   - Assign a request ID.
   - Distinguish stream-creation time from raw TTFT and audible-content TTFT.
   - Log usage fields when provided, including prompt/completion/reasoning/cached
     tokens and provider queue/prompt time.
   - Preserve existing first-output and total stream deadlines.
   - Done: structured logs include request ID, model, reasoning effort, cold
     state, raw/content TTFT, completion status, and available usage details.

5. [x] **Warm the Groq HTTP connection before the session is declared ready.**
   - Use the same service/client that will handle the conversation.
   - Use a bounded, no-generation authenticated request.
   - Fail open: warmup failure must never prevent a call.
   - Record cold/warm status in telemetry.
   - Done: a bounded `models.list()` request runs on the conversation's existing
     client while the pipeline is assembled; failures are logged and fail open.

6. [x] **Restore LLM-to-TTS streaming overlap without robotic speech.**
   - Remote TTS defaults to token streaming; local Piper remains sentence-based.
   - Cartesia must use a small configurable buffer rather than raw token +
     zero-buffer or full-sentence buffering.
   - Start with a 150 ms default and allow 0-5000 ms overrides.
   - Preserve an explicit sentence override for quality comparison.
   - Add configuration tests.
   - Done: remote TTS defaults to token mode, active Cartesia configuration is
     token mode with 150 ms buffering, and Piper remains sentence-based.

7. [x] **Separate TTS aggregation delay from provider TTFB.**
   - Record first speakable LLM text.
   - Record first `TTSStartedFrame`/provider request.
   - Record first audible audio frame after leading-silence trimming.
   - Emit speakable-to-request, request-to-audio, and speakable-to-audio timings.
   - Done: server telemetry now emits `tts_aggregation_ms`,
     `tts_provider_ms`, and `speakable_to_audio_ms`; Cartesia also logs
     per-context provider TTFB.

8. [x] **Bypass RAG when the user has no ready indexed corpus.**
   - Add a short-lived per-user ready-corpus cache.
   - Invalidate it when ingestion completes or a source is deleted.
   - Query shape still decides whether a ready corpus is relevant.
   - Preserve explicit document/link requests and strong semantic retrieval.
   - Add tests proving ordinary chat with no corpus reaches the LLM directly.
   - Done: ready-corpus presence is loaded with session authentication, cached
     for 30 seconds, and invalidated after successful ingestion/deletion.

9. [x] **Stop treating every ordinary utterance as an unconditional RAG query.**
   - Use explicit source language as the high-confidence path.
   - When a corpus exists, allow broad retrieval only behind a configurable smart
     routing policy.
   - Continue evidence gating before injecting retrieved chunks.
   - Record `rag_considered`, `rag_bypassed`, and `rag_used` separately.
   - Done: `explicit`, `hybrid`, and `always` policies are validated; the active
     low-latency policy is `explicit`.

10. [x] **Validate and correctly implement memory embedding-provider selection.**
    - Supported modes must be explicit (`google`, `openai`, or `disabled`) unless
      a real local embedder is implemented.
    - `local` must not silently reverse provider order and call OpenAI.
    - Never fall back to a different paid provider unless explicitly configured.
    - Keep embedding cache/in-flight deduplication.
    - Add provider-selection and disabled-mode tests.
    - Done: supported values are `google`, `openai`, and `disabled`; no paid
      cross-provider fallback occurs, and fake `local` mode is rejected.

11. [x] **Keep latency-critical work gated until first audible audio.**
    - `LLMFullResponseEndFrame` must not release the realtime gate before audio.
    - First audible TTS audio releases the critical gate.
    - Cancellation, interruption, no-audio responses, and cleanup must still
      release it safely.
    - Add lifecycle tests to prevent gate leaks.
    - Done: LLM completion no longer releases the gate; first audible audio,
      interruption, no-audio TTS completion, and session cleanup release it.
      Stable token keys also prevent Python object-ID reuse collisions.

12. [x] **Prevent background memory/enrichment from creating event-loop tail
    latency.**
    - Background work waits for the realtime gate.
    - CPU-heavy local work must use an executor/process instead of the event loop.
    - External retries use bounded deadlines/backoff.
    - Keep event-loop-lag monitoring enabled and include turn/session context where
      available.
    - Done: enrichment already uses its own gated lane; the corrected gate now
      covers the actual critical window, remote calls remain bounded, fake local
      embedding work is eliminated, and lag monitoring remains enabled.

13. [x] **Persist browser end-to-ear and WebRTC telemetry.**
    - Send the existing browser measurements back to an authenticated backend
      endpoint.
    - Include server turn ID/category, endpointing, playback detection, RTT,
      jitter, packet loss, and timestamps.
    - Store structured JSONL or equivalent durable telemetry without blocking the
      realtime media path.
    - Do not include transcript text or secrets.
    - Done: the browser posts metrics only after playback begins to an
      authenticated endpoint; the backend validates a transcript-free schema
      and appends JSONL asynchronously.

14. [x] **Make telemetry sufficient to compare cold/warm and direct/RAG/tool
    turns.**
    - Include session ID, turn ID, provider/model, cold-start flag, category, and
      all stage deltas.
    - Ensure the server sends diagnostics after the first audio frame so metrics
      cannot delay audio.
    - Keep client receipt/playback as separate signals.
    - Done: telemetry contains session/turn/provider/model/category/warm state,
      server stages, browser playback, and WebRTC quality fields. Diagnostics
      remain queued after the first audio frame.

15. [-] **Reduce provider/session startup latency.**
    - Preserve concurrent provider construction and database warmup.
    - Measure Deepgram and Cartesia WebSocket connection time separately.
    - Investigate safe parallel StartFrame/provider connection setup.
    - Do not mark the client ready until required realtime services are usable.
    - Partial: Deepgram STT and Cartesia WebSocket connection durations are now
      logged, Groq is warmed concurrently with pipeline assembly, and existing
      provider construction/database warmup remain concurrent. Parallel
      StartFrame connection requires a controlled Pipecat lifecycle experiment;
      doing it ad hoc risks frame-order and reconnect bugs.

16. [-] **Run a controlled model/TTS latency-quality matrix.**
    - Compare GPT-OSS 20B low reasoning and Llama 3.1 8B Instant.
    - Compare Cartesia token buffers at 75/100/150/200 ms and explicit sentence
      mode.
    - Use at least 100 direct turns per candidate, with cold turns separated.
    - Select defaults from p50/p95 plus transcript/voice quality, not one trace.

17. [-] **Trial Deepgram Flux as an alternative turn detector.**
    - Replace, rather than stack, local Smart Turn strategies.
    - Test EagerEOT speculative LLM start and cancellation on resumed speech.
    - Measure false stops, split utterances, endpoint p50/p95, and provider cost.
    - Keep current VAD 0.20 s + Smart Turn 0.30 s until the trial wins.

18. [-] **Make a production transport and region decision.**
    - Keep SmallWebRTC for local/single-region use.
    - Configure TURN for restrictive networks.
    - Co-locate backend capacity with users and provider regions.
    - For geographically distributed production, evaluate Daily or regional
      backends using measured WebRTC RTT/jitter.

19. [x] **Add latency regression tests and operational documentation.**
    - Unit-test every configuration and routing decision above.
    - Keep correctness tests green.
    - Keep the frontend production build green.
    - Document the benchmark command and explain that timeout values are failure
      bounds, not performance optimizations.
    - Done: configuration, routing, gate, telemetry persistence, and summary
      regression tests were added. Use:

      ```bash
      cd backend
      .venv/bin/python -m scripts.summarize_voice_latency \
        logs/voice-latency.jsonl
      ```

      `VOICE_LLM_FIRST_TOKEN_TIMEOUT_SECONDS` and total/tool deadlines bound
      failures; lowering them does not make a provider faster.

20. [-] **Consider advanced tail-latency techniques only after the basic path
    meets its budget.**
    - Speculative retrieval from stable interim STT.
    - Model hedging only for direct, no-tool turns with explicit cost controls.
    - Cached/pre-synthesized audio for deterministic greetings and acknowledgments.
    - Prompt-cache tuning based on observed cached-token metrics.

## Baseline from the 2026-07-23 supplied trace

1. LLM first speakable text: median 608.5 ms, range 524.1-971.3 ms.
2. Speakable text to first server TTS audio: median 474.2 ms,
   range 344.7-576.2 ms.
3. Completed user turn to first server TTS audio: median 1,134.4 ms,
   range 997.2-1,316.0 ms.
4. Empty RAG work: 10.2-31.3 ms per ordinary turn.
5. Final STT after semantic turn release: 2.6-4.8 ms.
6. Event-loop lag observed: 157 ms during a turn and 604 ms around disconnect.

## Verification log

- Initial audit: backend `160 passed`; frontend production build passed.
- Groq/TTS targeted verification: `22 passed`.
- RAG/memory/gate targeted verification: `72 passed`.
- Telemetry/provider integration verification: `85 passed`.
- Final backend verification: `172 passed`, 2 dependency deprecation warnings.
- Final frontend production build: passed.
