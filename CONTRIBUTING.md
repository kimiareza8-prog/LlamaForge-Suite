# Contributing

Keep changes compatible with both halves of the suite:

1. Run the LlamaForge test suite with `PYTHONPATH=. pytest -q` from `llamaforge/`.
2. Run `node --check web-bridge/assets/app.js` after JavaScript changes.
3. Run `php -l` on changed PHP files.
4. Never add generated Bridge secrets or model weights to commits.
5. Preserve protocol backward compatibility where possible; bump application versions separately from protocol versions.
