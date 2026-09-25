# LlamaForge 0.34.0 Smart Skills — Local AI + Skill-driven Agent

## Adaptive AutoTune

`Adaptive` benchmarks the selected GGUF with the installed `llama-bench` and stores the best measured CPU-thread, GPU-layer and batch/ubatch plan for that exact model file. Manual CPU/GPU/Hybrid/Max Both modes remain available. Large models use launch-local context/buffer guards and mmap when full RAM residency is unsafe; small models can use limited shared-server parallel slots without duplicating weights. Vision `mmproj` files are loaded only when an image is actually requested.

## CPU + iGPU Hybrid inference

On Windows, LlamaForge now detects AMD/Intel integrated graphics as well as NVIDIA adapters. The managed runtime prefers the official `llama.cpp` Vulkan x64 package when an accelerator is present. Hybrid mode defaults to a 35% GPU transformer-layer share while the remaining layers stay on CPU; change it under **Engine → CPU + GPU & launch**. `Run optimized` automatically upgrades an older managed CPU-only runtime to Vulkan when Hybrid is enabled.

این نسخه مسیر پیش‌فرض را برای سیستم‌های CPU/RAM محدود سبک کرده است.

## Internal Agent / Internet Skills

Version 0.21 uses a model-first Skill System designed for local 4B/7B-class models:

- The local GGUF first identifies the task goal and capability category.
- A Skill Registry shortlists only the relevant skills instead of dumping every installed tool into the prompt.
- The local model chooses the next skill with plain JSON; LlamaForge does not depend on llama.cpp native function-call parsers.
- Runtime preflight validates arguments, permissions and installed requirements before execution.
- Tool results are returned as compact observations; failures are classified and include fallback hints.
- Repeated identical failed calls are blocked.
- Low-cost tools are preferred before browser automation.
- State-changing actions remain permission-gated and should be verified before success is claimed.

Built-in skills include web check/read/search, HTTP/API, downloads, OpenAPI connectors, and optional Selenium browser open/snapshot/wait/scroll/click/type/select.

Custom Skill v2 files under `~/.llamaforge/agent/skills` support request headers/query/JSON body, required parameters, timeout, retries and response extraction. See `AGENT_SKILLS_FA.md` and `SKILL_SYSTEM_FA.md`.

Open **Agent** in the sidebar to inspect the skill catalog, permissions, browser support and OpenAPI connectors. In Chat, toggle **Agent on/off** per use.


## مدل پیشنهادی

- Chat: `bartowski/Qwen2.5-1.5B-Instruct-GGUF` با `Q4_K_M` حدود 986 MB
- Learning source: `Qwen/Qwen2.5-1.5B-Instruct` حدود 3.1 GB
- معماری: `qwen2`، text-only، حدود 1.54B پارامتر

کاربر فقط یک مدل می‌بیند. GGUF کپی inference است و checkpoint رسمی Qwen کپی training همان مدل است.

## روند ساده

1. `run.bat` را اجرا کن.
2. وارد Models شو.
3. روی **Download light model** بزن.
4. بعد از آماده‌شدن GGUF، مدل خودکار انتخاب و برای Chat لود می‌شود.
5. checkpoint آموزشی همان مدل در پس‌زمینه دانلود می‌شود.
6. Personal Brain به‌صورت خودکار با تنظیمات سبک فعال می‌شود: rank=4، alpha=8، 3 micro-step، replay=4، max_length=128.
7. فقط facts/corrections صریح و معتبر کاربر برای LoRA آماده می‌شوند؛ سلام، پرسش و پاسخِ تولیدشدهٔ خود مدل هدف آموزش نیستند.

## چرا Qwen2.5 1.5B؟

مدل حدود 1.54B پارامتر دارد و در کلاس حدود 2B قرار می‌گیرد، اما Q4_K_M آن زیر 1GB است. checkpoint آموزشی کامل هم فقط حدود 3.1GB است، بنابراین نسبت به Gemma 3 4B فشار RAM و disk بسیار پایین‌تری دارد و معماری آن text-only است؛ در نتیجه مشکل loader چندوجهی Gemma حذف می‌شود.

## پاک‌سازی نسخه قبلی

اگر می‌خواهی تست کاملاً تمیز باشد، پوشه‌های مدل قبلی را خودت حذف کن. برنامه هیچ فایل مدل کاربر را خودکار پاک نمی‌کند.
## Chat-first behavior

The Qwen GGUF is the only artifact required to start chatting. Personal Brain training files are downloaded and prepared in the background; an incomplete training checkpoint never blocks `Load & chat`.


## Personal Brain 0.34

Brain now accepts an explicit question and user-provided correct answer. Automatic learning skips plain questions and greetings; teacher-generated answers need a supporting quote from the current user message. The replay curriculum suppresses superseded answers and interleaves earlier examples within short step budgets. Candidate loss checks and a successful adapter reload precede confirmation.

Zero-context remains an explicit setting: training archives are never injected into chat. The bounded loss check is a sanity check, not a held-out recall score. See [BRAIN_SYSTEM_FA.md](BRAIN_SYSTEM_FA.md) and [AUDIT_REPORT_FA.md](AUDIT_REPORT_FA.md).
