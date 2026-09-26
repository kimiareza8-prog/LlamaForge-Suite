# 0.34.2-diagnostics

- Full local and Web Bridge request transcripts: routing, typed Skill catalog/shortlist, exact model HTTP payloads and fallback attempts, raw planner replies, tool arguments/results/verification, compact observations and final output.
- Downloadable JSONL + readable Markdown + manifest ZIP from Logs, with live request IDs, bounded retention, explicit incomplete/interrupted states and credential redaction before disk writes.
- Preserve streaming delivery and token metrics; record latency, TTFT, runtime snapshots, shared runtime log tail and available transport usage/timings.
- Accept PHP empty associative file indexes; reject malformed file sync before replacing the calendar.
- Stop swallowing remote cancellation and propagate heartbeat cancellation to model I/O.
- Reject incompatible prequantized training sources during Windows CPU preflight, including Intel display-GPU systems.

- Preserve Agent permission drafts during state refresh and late saves; enable fresh-install permissions while preserving stored opt-outs and validate JSON booleans.
- Shorten family-only routing instructions; retain guard/repaired families instead of requesting them again.
- Add an optional personal-account Telegram adapter with OS-vault login, bounded per-chat context, unique recipient resolution, serialized sends, cancellation and uncertain-delivery receipts. Include Telegram-only profile and independent read/write permissions.
- Install runtimes in immutable directories and atomically update the installed pointer; failed promotion leaves the previous build intact.
- Require tokenizer data instead of treating tokenizer_config.json alone as ready.
- 400 pytest cases passed, one environment skip; 18 executable UI cases passed. See DIAGNOSTICS_FA.md for measurements, supported Telegram scope and live-hardware limitations.

# 0.34.1-stability

- Fix Brain installer failure reporting, sticky in-flight cancellation, stale preview cancellation, model-switch detection during compilation and preflight failure restoring an initially stopped model.
- Bind adapter reload to the taught model; reject a model switch at compilation, preflight and reload/confirmation boundaries.
- Keep chat usable during background Brain setup/download/doctor stages using one activity classifier.
- Calendar rescheduling accepts time/Jalali/Gregorian/relative dates and preserves event duration; blank dates are rejected atomically and all-day defaults to 24 hours.
- Add cross-month calendar search with edit access, stale-response protection and escaped results. Date conversion cannot overwrite newer typing.
- Add 18 backend regression cases and 8 executable UI cases. No new runtime dependency, model instance, or Bridge updater/credential change.

# 0.34.0-smart-brain

- Refresh local chat with a neutral sidebar, compact controls, per-thread drafts/attachments, history search/rename and accessible mobile navigation.
- Add a readable Jalali month/selected-day calendar, create/edit/cancel dialog, explicit host timezone, local date conversion and multiday display.
- Fix late-cancel and multi-file/thread-switch races; cancelled responses cannot remove a newer answer.
- Refresh hosted Bridge CSS for readable chat/calendar controls; updater, tokens and transport code are unchanged.

- Brain supervision compiler and replay curriculum rewritten; no automatic targets from plain questions or ungrounded teacher answers.
- Explicit question/answer teaching form, correction-aware replay, duplicate recent-lesson detection, finite-gradient guard and bounded candidate loss checks.
- Live adapter reload no longer rolls back its own candidate. Generation/ledger are confirmed after reload, with durable crash recovery.
- Silent subprocess timeout, cancellable conversion, checkpoint-based CPU memory admission and terminal UI unlock.
- See BRAIN_SYSTEM_FA.md and AUDIT_REPORT_FA.md for test evidence and limits; real GGUF training quality has not been benchmarked.


- Reuse the router's capability decision, cache request-local discovery, and bypass routing for exact greetings.
- Add typed operation policies and preflight input validation; show reusable operation branches and their independent permissions in the Skill Tree.
- Preserve attachment IDs after save/rename and browser reload; deduplicate repeated staging; protect workspace destinations and archive reads.
- Bound ZIP/TAR and Office decompression; resolve XLSX shared strings; preserve large vision JSON envelopes.
- Verify local writes and reuse successful same-turn write receipts; block repeated preflight/tool failures and serialize mutations.
- Preserve workspace ownership in parallel read workers; cancel without blocking on executor shutdown.
- Stream every final-answer path, preserve explicitly read images in budget-final synthesis, and propagate inference errors/final markers.
- Add ordered SSE history/reconnect and long-poll fallback, token-ID dedup, backend chat cancellation, and persistent context drafts.
- Keep image storage separate from projector loading; disable internal control reasoning; redact credential fields in logs/progress.
- Keep adaptive memory guards active on CPU, retain manual thread settings, count per-slot context/KV, default to one slot, and leave unmeasured automatic speculation off.
- AutoTune uses bounded staged search, 3 repeats, median/stability scoring, real batch/ubatch comparisons, candidate timeout/cancellation/memory monitoring, and runtime/hardware/profile cache identity.
- No change to hosted Bridge PHP/JS, token lifecycle or atomic updater (CSS only). No new production dependency.

# 0.33.0-adaptive-engine

- Added real token streaming for Agent final answers. Routing/planning stays structured, while the final user-facing generation is streamed directly from llama-server.
- Hosted Web Bridge now consumes a live SSE state stream with automatic long-poll fallback for hosts/proxies that buffer SSE.
- Added universal attachment staging for arbitrary file types (ZIP, code, PDF, Office, audio/video and unknown binaries) instead of treating only images/text as first-class attachments.
- Added metadata-first `probe`: archives can expose a bounded file tree without opening file contents; `read_content` is only selected when the user goal actually needs the bytes/text.
- Strengthened Skill routing so staged attachments always retain the File skill, while generic "check this file" requests probe before opening content.
- Reworked Smart RAM: models that safely fit memory use `--load-mode none` (modern no-mmap/full process-backed load); oversized models retain mmap. Full RAM no longer relies on Windows mlock.
- Added one-shot mmap recovery if a Full RAM allocation fails at load time.
- Compacted and polished both local and hosted Calendar/File web layouts with denser spacing, smaller day cells/cards and responsive CSS.
- Preserved Max Both CPU+iGPU mode from 0.31.2, including Intel iGPU-aware low offload share, all logical CPU workers and host-op retention when supported.
- Web Bridge remains atomically updatable and preserves owner/agent tokens across code updates.

# 0.31.1-stable-bridge

- Reworked Web Bridge updates into a staged ZIP pipeline with checksum verification and atomic file replacement; live code is no longer bulk-deleted before activation.
- Pauses the connected-site background worker before a Bridge update and resumes it afterward to avoid Long-Poll races during migration from the legacy updater.
- Preserves the Web Bridge owner key and Agent connection token across update/rollback, exposes non-secret credential fingerprints, and removes accidental token rotation from the management UI.
- Added PharData as a ZIP extraction fallback when PHP ZipArchive is unavailable.
- Added automatic one-shot mmap/Hybrid recovery when Windows RAM Only/mlock crashes with `GGML_ASSERT(addr)`.

# 0.31.0 Control Center / Reliable Loading

- Rebuilt the Calendar web view with a cleaner month grid, Today strip, Agenda and clearer Agent integration.
- Fixed Settings context edits being overwritten by background state refreshes; exact values such as 8000 and 8192 now persist as drafts until Save/Load.
- Added explicit CPU only, GPU max-offload and CPU + GPU Hybrid compute modes in Settings and Advanced.
- Added a Hybrid GPU-share slider up to 95%; GPU mode requests maximum llama.cpp Vulkan layer offload.
- Model Load now exposes runtime preparation instead of appearing unresponsive.
- Runtime downloads now show percentage, downloaded/total size, speed and ETA, with a Cancel action.
- Runtime logs now report meaningful progress buckets instead of repeating an identical Downloading line for every chunk.
- GPU/Hybrid runtime installation no longer silently falls back to CPU if Vulkan fails; the real error is surfaced so the user can fix the driver or explicitly choose CPU.
- Added regression tests for exact 8000-token context persistence and GPU maximum-offload launch arguments.

# 0.29.1 Hybrid Vulkan CPU + GPU

- Added Windows AMD/Intel integrated-GPU detection through Win32_VideoController in addition to NVIDIA discovery.
- Added managed official llama.cpp Vulkan runtime selection/install with safe CPU fallback.
- Added true hybrid model loading: a configurable percentage of transformer blocks is offloaded to Vulkan while remaining blocks stay on CPU.
- Added CPU + GPU controls in Advanced, defaulting to a 35% GPU layer share, plus runtime backend/status visibility.
- Run Optimized and Settings model loads now automatically upgrade an old managed CPU runtime to Vulkan when Hybrid mode is enabled.
- Preserved CPU-only mode and cluster/RPC behavior.

# 0.29.0 Smart Elastic Compute Cluster

- Added modular Standalone / Master / Worker cluster roles with automatic private-LAN UDP discovery, heartbeat health, secure one-time pairing and authenticated control APIs.
- Added a hardware/network-aware scheduler with Auto / Selected Pool / Force Selected modes, Smart / Maximum Compute / Maximum Model Size / Lowest Latency / Manual policies, independent RAM allocation and compute share, and per-node CPU/RAM controls.
- Added strict Worker RAM enforcement at the OS process level where supported, with RPC memory advertising used only when the active llama.cpp runtime exposes it.
- Added RPC-capable llama.cpp source builds (`GGML_RPC=ON`), capability probing, RPC Worker lifecycle management and safe model-switch/recovery generation guards.
- Added Worker microbenchmarks, network latency/throughput sampling, measured token/s configuration cache, cluster profiles, Worker startup integration and a full Cluster Dashboard.
- Existing model management, Smart Router, Calendar/File domain agents, multimodal attachments, settings, API and hosted Web Bridge remain intact.

# 0.28.0 Smart Domain Agents

- Added two general domain agents requested for this release: Calendar and File Manager.
- Calendar exposes composable primitives (now/convert/month/list/create/update/cancel/delete); novel requests such as free-time reasoning are solved by the model by chaining primitives rather than by one skill per question.
- File Manager stages attachments without reading them, supports metadata/search/organization operations, and only reads file content when the model determines content is necessary.
- Added browser-scoped Persian calendar and file-manager views to the hosted Web Bridge, with host↔local workspace synchronization.
- Added model-driven family routing so irrelevant skill schemas stay out of the planning context.

# 0.27.0 Multimodal + Atomic Model Lifecycle

- Serialized model select/start/stop across desktop and connected web clients, invalidated stale readiness watchers, and refuse to clear a llama-server handle until the process has actually exited.
- Fixed Settings form refresh races so Context and manual generation controls such as Temperature persist after Save instead of being overwritten by live state refreshes.
- Added text/code file attachments plus image attachments for supported vision GGUF models; sibling `mmproj*.gguf` files are detected, downloaded with the model when available, and passed to llama.cpp with `--mmproj`.
- Added conservative Vision detection to both local and hosted model selectors; text-only models reject image attachments with a clear message.
- Updated the hosted Web Bridge to 3.5.0 with attachment controls, visible Web/Core version labels, and a tighter ChatGPT-like composer/layout.
- Synced the embedded Bridge updater payload so updating the Bridge from the Python app deploys the same 3.5.0 web files.

# 0.26.0 Smart Router + Bridge Updater

- Replaced the regex-only top-level gate with a **local-model routing stage**: Direct Chat vs Skills.
- Added a second model-driven Skill-family stage: Web / API / Browser / Connector / Custom, followed by a concrete-skill shortlist and normal Plan → Execute → Observe → Re-plan.
- Preserved deterministic runtime guardrails for permissions, lightweight URL reads, failed-call deduplication and preflight validation without taking the routing decision away from the model.
- Added direct-chat language finalization so Persian/other user-language turns do not accidentally end in English when internal control prompts are English.
- Added an embedded Web Bridge update package to LlamaForge plus authenticated remote **Update Bridge** and **Rollback** controls for connected sites.
- Added server-side Bridge package checksums, path validation, update locking, backups, automatic rollback on failed install, and retention of runtime config/tokens/history.
- Refined both local Agent activity and hosted chat activity to show route decision, Skill family, concrete skill/action, execution and observations instead of repetitive generic Working states.
- Refined the hosted web UI for a softer ChatGPT-like desktop/mobile layout, safer wrapping, composer spacing, activity compaction and mobile safe areas.
- Older Bridges require one manual bootstrap to an updater-capable release; later Bridge updates can be installed from LlamaForge.

# 0.24.0 Professional Agent

- Added automatic Direct-vs-Agent routing so ordinary chat never enters the Skill loop.
- Refined routing so explanatory questions about API/HTTP/websites stay direct unless an external operation is requested.
- Added language-safe Agent finalization: internal control can remain English while the final answer follows the user language.
- Added adaptive context budgeting for conversation, shortlisted skills and compact observations.
- Added turn-aware history compaction instead of arbitrary tail truncation.
- Added richer Agent activity events for route, context policy, decisions, tool timing, observation size and fallbacks.
- Improved local Agent activity UI: open while streaming and compact/collapsed after completion.
- Full automated suite includes direct-route, API explanatory-route, Persian final-language and 8K context-policy coverage.

# 0.23.0 Model Control

- Added persistent context-limit control in Settings.
- Added manual Temperature / Top-P / Top-K / Min-P / Repeat Penalty / Max Tokens overrides.
- Added Load / Reload / Unload model controls directly in Settings.
- Connected websites now receive a safe opaque model catalog and can request a real model load/switch.
- Remote model selection works even when no model is currently loaded.
- Local filesystem model paths are never exposed to the website.
- Sampling controls remain local to LlamaForge and are intentionally hidden from the web client.

- Added persistent Connected websites/apps with Connection URL + optional token.
- Added background remote task polling while a local model is ready.
- Remote tasks run through the same AgentEngine and Skill Registry as local chat.
- Added live activity, partial answer, lease heartbeat and final reply transport back to the website.
- Added pause/resume/test/remove controls for connected apps.
- Tokens embedded in connection URLs are separated from the public URL and stored in OS keyring when available.
- User-visible activity redacts common bearer/token patterns.

# 0.21.0 Skill System

- Added model-based capability discovery before skill planning.
- Added Skill Registry with category/risk/cost/requirements/fallback metadata and task-specific shortlisting for small local models.
- Added deterministic preflight validation and structured failure classification.
- Added `web_check`, `web_find`, `download_file`, richer HTTP query/form controls, browser wait/scroll/hover/tabs/select/session skills.
- Added Custom Skill v2 with headers/query/JSON body, required arguments, timeout, retry policy, response format, dot-path extraction and `{env:NAME}` templates.
- Each configured OpenAPI operation is now exposed as its own `conn_*` skill; write operations stay hidden until write permission is enabled.
- Agent UI now shows the execution rule ladder and grouped skill catalog including unavailable reasons.
- Preserved model-first Plan → Execute → Observe behavior while keeping runtime guardrails for permissions and repeated failures.
- Added regression tests for registry routing, URL checks, downloads, and Custom Skill v2.

# 0.20.1 Agent Runtime

- Added execution-policy layer after local-model planning: simple supplied URLs use `web_read` before escalating to Chrome automation.
- Browser skills are hidden from the model when Selenium Browser Skill is unavailable.
- Identical failed tool calls are blocked from being repeated with the same arguments.
- Planner explicitly receives failed-call recovery rules.
- Tool failure details are shown in Agent Activity (`HTTP`, Selenium/Chrome, timeout, etc.).
- Browser page-load timeout reduced to 20 seconds; timeout can preserve a partial DOM snapshot instead of hanging the whole Agent.
- Added regression tests for the exact repeated-browser failure shown in the UI screenshot.

# 0.20.1 Agent Runtime

- Rewrote the Agent loop as model-first Plan → Skill → Execute → Observe → Re-plan.
- Removed automatic URL prefetch and protocol-specific execution from the Agent core.
- Removed dependency on llama.cpp native tool-call parser for Agent decisions.
- Added plain JSON control decisions with JSON response mode when supported and repair fallback for small local models.
- Added compact observations for limited-context 4B/7B models.
- Added visible Agent activity trace for understanding, planning, skill execution and observations.
- Added regression tests for read, GET/POST chaining and invalid-JSON recovery.

# 0.19.1 Agent

- Fixed strict Jinja chat templates failing with `Conversation roles must alternate user/assistant`.
- URL prefetch evidence is merged into the active user turn instead of creating adjacent `user` roles.
- Added a strict user/assistant-only fallback for templates that reject native tool/system/tool role histories.
- Once native llama.cpp tool parsing fails for a turn, LlamaForge stays on its textual tool protocol instead of repeatedly re-triggering the parser error.

# 0.19.0 Agent

- Added a local tool/skill runtime for GGUF models.
- Added web search, URL reading, HTTP/API tools and permission-gated writes.
- Added generic OpenAPI connector import; the supplied AI Bridge schema works as an example connector.
- Added optional Selenium/Chrome browser automation with a persistent Agent profile.
- Added dynamic declarative JSON skills under `~/.llamaforge/agent/skills`.
- Added Agent page and per-chat Agent toggle.
- Added native llama.cpp tool-calling loop plus textual fallback for incompatible templates.
- Preserved RAM Only / SSD Test / Hybrid Smart model memory modes.

# 0.18.1

## Memory modes patch
- Added persistent Settings selector: RAM Only / SSD Test / Hybrid Smart.
- RAM Only uses mlock and blocks launch when the model cannot fit the safe RAM budget.
- SSD Test uses mmap + lazy on-demand loading + no-warmup when supported.
- Hybrid Smart uses mmap/page cache so hot weights can stay in RAM and cold pages remain SSD-backed.
- Added capability-aware fallbacks for older llama.cpp runtimes.

- Chat-first loading: the sub-1GB Qwen GGUF opens immediately even when Personal Brain learning files are still downloading.
- The one-click Qwen card now treats an already-downloaded GGUF as usable and shows **Load & chat** instead of forcing the whole training bundle to finish first.
- Reordered model activation so `llama-server` starts before background Brain auto-setup.
- Added `[model:select]`, `[chat:start]`, and background Brain setup log events so diagnostics show which stage is blocking (if any).
- Training download remains resumable and independent from chat runtime.

# 0.18.0

- Lightweight Qwen2.5 1.5B one-click model (~986 MB Q4_K_M chat artifact).
- Matching official Qwen trainable checkpoint is managed automatically for LoRA learning.