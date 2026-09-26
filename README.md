# LlamaForge Suite

A local-first AI stack that connects a local GGUF model to a ChatGPT-style PHP web chat through a live agent bridge.

## Repository layout

- `llamaforge/` — Windows/Linux Python application for local GGUF models, model loading/unloading, context/sampling controls, Agent/Skills, browser/API tools, and remote web-app sync.
- `web-bridge/` — PHP web chat with live progress, browser-scoped conversation history, remote model selection, response cancellation, model unload, and responsive mobile UI.

## What this suite does

1. Run a GGUF model locally with llama.cpp through LlamaForge.
2. Connect a hosted PHP chat to that local LlamaForge instance using the Bridge connection URL/token.
3. Let the local model decide whether a request needs direct inference or Agent skills.
4. Stream Agent activity and partial/final replies back to the web UI.
5. Let web users select an available local model without exposing local file paths.

## Current UX features

- ChatGPT-style recent-conversation sidebar.
- Server-backed history isolated by browser identity.
- New chat, delete one conversation, clear all browser history.
- Draft text saved separately per conversation in the browser.
- Stop current response and `Esc` shortcut.
- Load/switch local models from the web UI and unload/stop the current model.
- Copy assistant answers.
- Live Agent activity panel that stays open while running, auto-scrolls, and collapses after completion.
- Responsive desktop/mobile layouts with mobile safe-area handling.
- Reconnect state and long-poll live updates.
- No local model filesystem paths are published to the website.

## Quick start

### 1. LlamaForge

Open `llamaforge/README.md`, install its requirements, add/select your GGUF model and start LlamaForge.

On Windows the included `run.bat` is the simplest entry point.

### 2. Web Bridge

Upload the contents of `web-bridge/` to a PHP 7.4+ host. The first request generates a private `web-bridge/data/config.php` automatically.

Read `web-bridge/START-HERE.txt` for the setup steps. Keep `owner_key`, `agent_token`, and `data/config.php` private.

### 3. Connect them

Open the Bridge management page, copy the **LlamaForge Connection URL**, then add it in LlamaForge under the connected websites/apps section. The web UI will then receive model status, available model metadata, Agent activity and replies.

## Smart Skill routing

LlamaForge now uses a model-driven Skill tree instead of a regex-only gate:

1. The local model decides **Direct chat** vs **Skills**.
2. If Skills are needed, the model selects a family such as Web, API, Browser, Connector or Custom.
3. LlamaForge shortlists only the concrete skills in that branch.
4. The model chooses one action, receives the real observation, then re-plans until it can answer.

This keeps greetings, writing and ordinary explanations out of the tool loop while operational requests such as “open this URL”, web research, HTTP/API calls and browser interaction can enter the Skill runtime. Final answers are corrected back to the user's language when internal control prompts are English.

## Updating the hosted Web Bridge from LlamaForge

Web Bridge 3.4 includes an authenticated updater endpoint. Once an updater-capable Bridge is installed on the host, LlamaForge can send its bundled Bridge ZIP directly to the connected site, create a server-side backup, install the new code and refresh the connection descriptor. The same Settings card can restore a previous backup. Runtime configuration, tokens and browser chat history are not replaced by Bridge updates.

**One-time bootstrap:** an older Bridge that does not already contain `bridge-update.php` cannot install this feature by itself. Upgrade that host to this Bridge version once manually; subsequent Bridge releases can be installed or rolled back from LlamaForge.

## History isolation

Each browser installation gets a cryptographically random browser identity stored in its own `localStorage`. The server stores a hash of that identity with each session/message. History, deletion and watch operations are scoped to that browser identity, so one normal browser does not receive another browser's chat list or messages.

This is browser/device isolation, not a replacement for authenticated user accounts. For a public multi-user service, add your own login/session layer and bind Bridge ownership to authenticated account IDs.

## Security

- Runtime secrets and message files under `web-bridge/data/` are excluded from Git.
- Do not commit generated `config.php`, tokens, model files or browser profiles.
- Use HTTPS for the hosted Bridge.
- Restrict the Bridge management URL and owner key.
- Public remote model switching/unloading is powerful; put authentication/authorization in front of the web app for public deployments.

See `SECURITY.md` and `web-bridge/SECURITY.md`.

## Large model files

Do not commit `.gguf`, `.safetensors`, checkpoints or other model weights to this repository. Keep them outside Git and select them locally in LlamaForge.

## License

See `LICENSE`.

## 0.33.0 highlights

Adaptive AutoTune now benchmarks the exact GGUF with the installed `llama-bench`, persists the winning CPU-thread/GPU-layer/batch plan per model, guards context and transient buffers for oversized models, keeps one shared model server for Agent work, parallelizes independent read-only Skills, lazy-loads vision projectors, and capability-gates n-gram speculative decoding.

## 0.32.0 highlights

This build adds true Agent/web response streaming, universal metadata-first file attachments, smarter file/calendar Skill routing, compact calendar UI, and Full RAM loading with `--load-mode none` when the model fits safely.

Published package version: 0.34.1-stability

## 0.34.0 Smart Skills

Operation contracts, focused capability routing, durable attachment receipts, bounded ZIP/TAR and Office reading, verified local writes, genuine streaming and cancellation, and measured-search safeguards for Adaptive AutoTune. No new model instance or mandatory dependency. See [the audit report](llamaforge/AUDIT_REPORT_FA.md) for tests, measurements and remaining limits.

## 0.34 Personal Brain and web interface

Explicit question/answer teaching, grounded automatic lessons, correction-aware replay and adapter confirmation after successful reload. The local web app has a quieter conversation layout, per-chat drafts, searchable history and an editable Jalali calendar. The hosted Bridge receives a readable chat/calendar skin; updater and credentials are unchanged. See [Brain](llamaforge/BRAIN_SYSTEM_FA.md) and [the technical report](llamaforge/AUDIT_REPORT_FA.md).

## 0.34.1 stability follow-up

Fixes Brain setup/cancellation and background chat availability; adds reliable duration-preserving calendar rescheduling and cross-month event search. [Follow-up report](llamaforge/FOLLOWUP_REPORT_FA.md).
