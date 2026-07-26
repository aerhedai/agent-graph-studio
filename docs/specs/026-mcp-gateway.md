# SPEC-026: Self-Hosted MCP Gateway — Centralized Container Deployment

**Status:** Draft — ready for review
**Author:** Rohan
**Depends on:** SPEC-006 (connections abstraction), SPEC-019 (dynamic MCP node generation), SPEC-021 (per-user MCP OAuth engine), SPEC-025 (app integration catalog, `api_key`/`bearer` auth, admin catalog-bootstrap)

## 1. Goal

Give the admin one centralized, container-based place to run **self-hosted** MCP servers — replacing today's ad hoc "SSH in, write a PowerShell script, register a Task Scheduler entry" pattern (how `google_workspace_mcp` is currently deployed) with a single Docker Compose stack anyone can add a new entry to. As a real side effect, this also removes a genuine limitation: a `stdio`-transport MCP server currently must run on the exact same machine as the Agent Graph Studio backend process itself (confirmed in code — `backend/mcp/client.py`'s `list_tools`/`call_tool` spawn a local subprocess directly), which rules out most published open-source MCP servers (they ship as an npm/pip package meant to be run via stdio, not as an already-hosted remote endpoint) unless someone manually installs that package's runtime onto the backend host.

**Explicitly not a change to how connections/credentials work.** From Agent Graph Studio's point of view, a gateway-hosted server is indistinguishable from any other `mcp_server` connection with `transport: "remote"` — its `url` just happens to point at the gateway instead of the app's own vendor-hosted endpoint. Every mechanism already built and proven (per-user OAuth tokens, per-user API keys, `CredentialType`, admin catalog-bootstrap, the picker) needs zero changes.

## 2. Why this, why now

This session hit the ad hoc deployment pain directly twice: `google_workspace_mcp` went down because the external drive its scripts lived on got disconnected, and bringing it back required an SSH session and manually re-running two Task Scheduler entries. There's no single place to see "what self-hosted MCP servers exist and are they running" — it's whatever scripts happen to be scattered across `D:\mcp-servers\`.

Separately, this session evaluated Docker's own MCP Gateway/Toolkit as a candidate to build on. Real research (not assumed) confirms it manages exactly **one shared credential set per Docker Desktop install** — the same shape MetaMCP already had, and the same reason MetaMCP was ruled out in SPEC-025: every user of a given app would share one login, which directly breaks the "each user connects their own personal account" property this whole project is built around. **This spec explicitly does not adopt Docker's MCP Gateway/Toolkit as the credential authority.** The gateway built here is deliberately dumb about credentials — it has no concept of users at all, and never sees or stores a token. It only proxies whatever `Authorization` header Agent Graph Studio's backend already attached (exactly as it does today when calling a vendor-hosted server directly), and manages container lifecycle for the servers themselves.

## 3. Scope

**In scope:**

- A new, separate, deployable service (`mcp-gateway`) that:
  - Keeps a small catalog of server definitions — for each self-hosted server: a name, how to run it (a Docker image + command/args, or an existing prebuilt image), and its native transport (`stdio` or an already-HTTP-speaking server).
  - For a `stdio`-native server, wraps it in a stdio-to-streamable-HTTP bridge inside its container so it's reachable the exact same way as a native remote server (an existing OSS bridge tool, e.g. `mcp-proxy`/`supergateway`, is the likely implementation — see Open Questions).
  - Exposes each defined server at a predictable path, e.g. `https://<gateway-host>/<server-name>/mcp`.
  - Manages container lifecycle via Docker Compose (`restart: unless-stopped`, one service per definition) — one file, not per-app Task Scheduler entries.
  - A small admin-facing way to add/remove/list catalog entries without hand-editing YAML for every new app (a CLI or a couple of HTTP endpoints on the gateway itself — implementation detail, not a hard requirement of this spec).
- Caddy in front of the gateway for real TLS, reusing the exact pattern already proven for Gmail (`tailscale cert`, one reverse proxy, internal plain-HTTP bind).
- Migrating `google_workspace_mcp`'s existing deployment into this gateway as the first real, proven entry — replacing its current Task Scheduler + PowerShell script pair.
- Adding at least one brand-new, previously-unusable `stdio`-only open-source MCP server to prove the actual new capability (not just re-hosting something that already worked).

**Out of scope (deliberately, not deferred-and-forgotten):**

- **Any change to `backend/mcp/*`, `backend/connections/*`, `backend/execution/*`, or per-user credential storage.** A gateway-hosted server is just a `remote` connection whose `url` points at the gateway — if implementation turns out to need a change here, that itself is a signal scope has crept beyond "just a URL" and needs re-evaluating, not a quiet extension of this spec.
- **Adopting Docker's own MCP Gateway/Toolkit as the literal implementation.** Its single-shared-credential model doesn't fit; its general "run MCP servers as containers, expose one endpoint" shape is a fine inspiration, not the product being adopted.
- **Kubernetes.** Proportionate to a single-host, single-admin deployment is Docker Compose. Revisit only if a real multi-node scaling need shows up later — none exists today.
- **Migrating already-hosted-elsewhere apps** (Context7, kpidepot.com, any future official OAuth-based SaaS MCP server like Linear/Zoom/Stripe/Figma). Those need nothing self-hosted at all and stay connected directly, exactly as today — the gateway is only for servers the admin has to run themselves.
- **Deciding MetaMCP's fate.** Its Docker deployment currently sits idle (confirmed running but unused, see this session's own investigation). Flagged as a real decision to make once this gateway is proven, not decided here (see Open Questions).

## 4. Design decisions (resolved)

- **Custom-built, credential-blind gateway — not Docker's MCP Gateway/Toolkit.** Confirmed via research: Docker's product stores one shared credential set per app per install. This gateway never stores or interprets a credential at all; it's pure request-proxying + container lifecycle. Resolved explicitly this session after weighing both options.
- **A gateway-hosted server is an ordinary `remote` `mcp_server` connection.** No new `transport` value, no new connection type, no engine/execution changes. `_server_config_for` (SPEC-021/023/025's per-user credential resolution) attaches the caller's own token/key to the request exactly as it already does for any other remote URL; the gateway receives that header as opaque pass-through and never inspects it.
- **Docker Compose on a single host, not Kubernetes.** Matches the current one-admin, one-machine (rohan-pc) deployment reality.

## 5. Data model (illustrative)

This is the gateway's *own* catalog, entirely separate from Agent Graph Studio's own database — the gateway is a standalone service with no shared storage with the main app.

```yaml
# gateway catalog (exact format decided during implementation -- YAML file vs
# small SQLite is an implementation detail, not a design decision requiring
# sign-off, since it's write-rarely/admin-only)
servers:
  - name: google-workspace
    native_transport: stdio
    image: <image for google_workspace_mcp>
    command: [...]
    env: {...}          # non-secret runtime config only -- OAuth client
                          # id/secret/tokens never live here, they stay in
                          # Agent Graph Studio's own encrypted stores exactly
                          # as today
    restart: unless-stopped
```

No change to any Agent Graph Studio schema (`McpServerConnectionConfig`, `ConnectionInfo`, `oauth_token_storage`, `api_key_storage` all untouched).

## 6. Acceptance criteria

- [ ] The gateway runs as one or more containers on rohan-pc, managed by a single Docker Compose file with `restart: unless-stopped`.
- [ ] `google_workspace_mcp` is migrated into the gateway as a real running entry, replacing its Task Scheduler + PowerShell script deployment — Gmail continues working end-to-end afterward (a real, live tool call, not just "the container started").
- [ ] At least one brand-new `stdio`-only open-source MCP server (previously unusable without installing its runtime on the backend host) is added to the gateway's catalog and reachable at a predictable gateway URL — live-verified with a real tool call through it.
- [ ] A per-user personal credential (OAuth or `api_key`) continues to work through a gateway-hosted connection exactly as it does through a directly-hosted one — live-verified, proving the "gateway never touches credentials" design decision holds in practice, not just on paper.
- [ ] Full existing backend/frontend test suite passes with **zero changes** to `backend/mcp/*`, `backend/connections/*`, or `backend/execution/*` (a gateway-hosted connection needs no new code in the main app at all).
- [ ] `git diff main -- backend/execution/engine.py` is empty.

## 7. Open questions

- **Exact stdio-to-HTTP bridging mechanism**: write a small custom bridge, or adopt an existing OSS tool (`mcp-proxy`, `supergateway`, or similar)? Proposing to decide this during implementation research rather than block spec approval on it — it's an internal detail of the gateway container, invisible to Agent Graph Studio's own connection model either way.
- **MetaMCP's fate**: decommission its currently-idle Docker deployment once this gateway is proven, or leave it running unused indefinitely? Leaning toward "decommission once this ships" to avoid two idle-or-redundant pieces of infrastructure, but flagging rather than deciding silently.
