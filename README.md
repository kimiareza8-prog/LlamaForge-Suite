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
