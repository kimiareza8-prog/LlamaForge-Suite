# LlamaForge 0.15.1 design notes

## Brain control-state contract

The Brain master power control is intentionally separate from advanced learning preferences. A master toggle request mutates only `enabled`. UI state is refreshed through both direct API responses and Brain SSE events; structural Brain fields participate in the Brain route render key.

Turning Brain off or pressing Stop signals cancellation to active setup/download/trainer subprocesses. A cancelled run may restore inference, but it may not convert/promote/confirm candidate Personal LoRA weights. The visible job state moves through `running -> cancelling -> cancelled` rather than masquerading as an error or remaining stuck in `running`.

## UI architecture

The macOS-inspired redesign is deliberately implemented as a presentation layer rather than a workflow rewrite:

```text
index.html
  styles.css       <- stable structural/component CSS
  macos.css        <- 0.15 visual system overrides
  app.js           <- existing routes, controls and API wiring
```

This separation lowers regression risk. Element ids, route names and backend endpoints are unchanged. The new layer changes typography, spacing, materials, radii, borders, shadows, focus/hover/pressed states and responsive behavior.

The theme uses only system font fallbacks and inline SVG icons. It does not fetch Apple assets, external fonts, CDN resources or third-party UI libraries.

## Personal Brain transaction contract

A learning turn is only considered confirmed after:

```text
compile examples
→ unload inference model
→ train candidate Personal LoRA
→ convert candidate LoRA to GGUF
→ promote candidate with rollback record
→ reload llama.cpp with candidate adapter
→ verify healthy server
→ confirm transaction / mark packet learned
```

A crash or reload failure before confirmation rolls the adapter pair back to the last confirmed state.

## Portable trainable-model placement

Let `APP_ROOT` be the extracted LlamaForge directory:

```text
TRAINABLE_ROOT = APP_ROOT.parent / "LlamaForgeModels"
```

Runtime binaries, trainer environment, Brain metadata and adapter profiles remain under `~/.llamaforge`; large downloaded source/base checkpoints use the shared sibling directory. Older LlamaForge-managed checkpoints can be relocated there without moving arbitrary user-selected folders.
