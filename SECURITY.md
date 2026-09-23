# Security

## Secrets

Never commit Bridge `owner_key`, `agent_token`, API keys, cookies, browser profiles, or generated `web-bridge/data/config.php`.

## Deployment

Use HTTPS for the hosted web bridge. Keep the management page private. The browser-scoped history mechanism isolates ordinary browser installations, but it is not an account authentication system. A public/multi-tenant deployment should add authentication and server-side authorization and bind chat ownership to an authenticated user ID.

## Remote controls

The Bridge can request model load/switch/unload operations from a connected LlamaForge instance. Treat access to the chat/management surface as privileged if those controls are enabled.

## Agent actions

Agent skills that mutate remote state remain permission-gated in LlamaForge. Review enabled skills/connectors and do not expose private-network access unless required.
