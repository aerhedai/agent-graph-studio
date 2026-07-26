# SPEC: Dynamic Connector & Tool Subsystem

**Component:** Agent Graph Studio — connector layer
**Status:** Draft for implementation
**Stack:** FastAPI + SQLAlchemy + Postgres (Supabase), React Flow frontend

---

## 1. Purpose

Enable Agent Graph Studio to expose third-party application capabilities (`search_gmail`,
`telegram_send_message`, `slack_post_message`) as canvas nodes and as tools bindable to agent
nodes — **without writing bespoke code per application**.

Adding an integration must be a data operation (insert rows), never a code change.

## 2. Non-goals

Explicitly out of scope for this spec. Do not implement these.

- **Trigger nodes** (webhooks, polling). Separate architecture, separate spec.
- **Building a proprietary integration catalog.** We consume catalogs; we do not curate one.
- **Multi-tenant SaaS concerns.** Single-operator self-hosted deployment. Design the schema so
  multi-tenancy is possible later (`user_id` on connections) but do not build org/team/RBAC.
- **Hosting our own OAuth applications.** Users bring their own client credentials.
- **Streaming or long-running tool calls.** Request/response only. Timeout and fail.

## 3. Core architectural decision

**One `Action` abstraction, multiple executors.**

An `Action` is the atomic unit: a name, a description, a JSON Schema for its inputs, and a
reference to an executor that knows how to run it. The canvas, the agent runtime, and the
config UI depend only on the `Action` interface. They never branch on integration source.

Two executors ship in v1:

| Executor | Source of actions | Auth handled by | Purpose |
|---|---|---|---|
| `http` | YAML manifest or OpenAPI import | Our auth subsystem | Depth. First-class UX on apps we care about. |
| `mcp` | `tools/list` on a configured MCP server | The MCP server itself | Breadth. Hundreds of apps immediately. |

The `mcp` executor is the **primary** path for catalog coverage and must be built first. The
`http` executor is for apps where we want curated tool names, better descriptions, and native
credential handling.

> **ADR-001** — Record this decision. The alternative considered was building only the manifest
> system. Rejected: catalog breadth is not a viable solo build, and MCP is now the de facto
> integration standard. The alternative of building only an MCP proxy was also rejected: it
> gives no control over tool granularity or naming, and MCP server auth is inconsistent.

## 4. Domain model

Five entities. Migrations via Alembic.

```sql
-- An application. Metadata + which auth schemes it supports.
CREATE TABLE providers (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    slug          TEXT UNIQUE NOT NULL,          -- 'gmail', 'telegram'
    name          TEXT NOT NULL,
    icon_url      TEXT,
    auth_schemes  JSONB NOT NULL DEFAULT '[]',   -- see §5.1
    source        TEXT NOT NULL,                 -- 'manifest' | 'openapi' | 'mcp'
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- User-registered OAuth client credentials (bring-your-own-app).
CREATE TABLE oauth_apps (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    provider_id        UUID NOT NULL REFERENCES providers(id) ON DELETE CASCADE,
    label              TEXT NOT NULL,
    client_id          TEXT NOT NULL,
    client_secret_enc  BYTEA NOT NULL,           -- envelope encrypted, §5.4
    redirect_uri       TEXT NOT NULL,
    scopes             TEXT[] NOT NULL DEFAULT '{}',
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- An authenticated instance. "My work Gmail", "the alerts bot".
CREATE TABLE connections (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id          UUID NOT NULL,
    provider_id      UUID NOT NULL REFERENCES providers(id) ON DELETE CASCADE,
    oauth_app_id     UUID REFERENCES oauth_apps(id) ON DELETE SET NULL,
    label            TEXT NOT NULL,
    auth_type        TEXT NOT NULL,              -- 'api_key' | 'oauth2_auth_code' | ...
    credentials_enc  BYTEA NOT NULL,
    expires_at       TIMESTAMPTZ,
    status           TEXT NOT NULL DEFAULT 'active',  -- active|expired|revoked|error
    last_error       TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One tool. The atomic unit.
CREATE TABLE actions (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    provider_id    UUID NOT NULL REFERENCES providers(id) ON DELETE CASCADE,
    name           TEXT NOT NULL,                -- 'search_gmail' — LLM-visible
    display_name   TEXT NOT NULL,
    description    TEXT NOT NULL,                -- LLM-visible. Quality matters.
    input_schema   JSONB NOT NULL,               -- JSON Schema draft 2020-12
    output_schema  JSONB,
    executor_type  TEXT NOT NULL,                -- 'http' | 'mcp'
    executor_config JSONB NOT NULL,              -- §6
    deprecated     BOOLEAN NOT NULL DEFAULT false,
    UNIQUE (provider_id, name)
);

-- A placed node on a canvas.
CREATE TABLE node_instances (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workflow_id    UUID NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
    action_id      UUID NOT NULL REFERENCES actions(id),
    connection_id  UUID REFERENCES connections(id) ON DELETE SET NULL,
    config         JSONB NOT NULL DEFAULT '{}',  -- static values + expression refs
    position       JSONB NOT NULL
);
```

Index `actions(provider_id)`, `actions(name)`, `connections(user_id, provider_id)`.

## 5. Auth subsystem

### 5.1 Strategies

Six strategies. Each is a class; each provider's `auth_schemes` selects one and parameterizes it.
Adding an app never adds a strategy.

```python
class AuthStrategy(Protocol):
    type: ClassVar[str]

    def credential_schema(self, config: dict) -> dict:
        """JSON Schema for what the user must supply. Renders the connect form."""

    async def apply(self, credential: dict, request: httpx.Request) -> httpx.Request:
        """Mutate the outbound request to carry auth."""

    async def refresh(self, credential: dict, config: dict) -> dict | None:
        """Return updated credential, or None if not refreshable."""
```

| `type` | Config keys | Example provider |
|---|---|---|
| `api_key` | `placement` (header/query/body), `param_name`, `prefix` | Telegram, Resend |
| `bearer` | — | Notion, Linear |
| `basic` | `username_label`, `password_label` | Jira, Twilio |
| `oauth2_auth_code` | `authorize_url`, `token_url`, `scopes`, `use_pkce`, `extra_params` | Google, Slack |
| `oauth2_client_credentials` | `token_url`, `scopes` | Spotify app-only |
| `custom` | `script` — sandboxed, restricted stdlib | edge cases |

Implement `custom` **last** and gate it behind a config flag. It is an escape hatch, not a
default. If more than three providers need it, the strategy set is wrong — fix that instead.

### 5.2 OAuth flow

Because this is self-hosted, we do not ship client secrets. Users register their own app.

1. User adds an `oauth_app` row: client ID, secret, redirect URI, scopes.
   UI must show the exact redirect URI to paste into the provider's console:
   `{BASE_URL}/api/oauth/callback/{provider_slug}`
2. `GET /api/connections/authorize?provider=gmail&oauth_app_id=…` builds the authorize URL
   (PKCE verifier + `state` stored in a short-TTL server-side cache) and 302s.
3. Provider redirects to the callback. Validate `state`, exchange code for tokens, encrypt,
   insert `connections` row, redirect to a UI success page.
4. Access token expiry recorded in `connections.expires_at`.

**Deployment note for the docs:** `BASE_URL` must be reachable by the provider's redirect. For
a local Windows host, `http://localhost:PORT` works for most providers (Google permits it);
otherwise a Cloudflare Tunnel or Tailscale Funnel URL is required. Document both.

### 5.3 Refresh middleware

A single httpx event hook, applied to every `http` executor call:

- Before send: if `expires_at` is within 60s, refresh proactively.
- On 401: refresh once, retry once, then mark the connection `status='expired'` and raise
  `ConnectionExpiredError`. Never retry more than once.
- Refreshes for the same `connection_id` must be serialized (advisory lock or in-process
  `asyncio.Lock` keyed by connection ID) to avoid a refresh-token race.

### 5.4 Credential encryption

- Envelope encryption. A master key from env (`AGS_MASTER_KEY`, 32 bytes base64). Per-record
  data key, wrapped by the master key. Use `cryptography` Fernet or AES-GCM.
- Credentials are **never** returned over the API. Serializers must omit them. Add a test that
  asserts no endpoint response body contains a known secret value.
- Redact secrets in logs and execution traces. Implement a `redact()` pass over trace payloads
  keyed on the provider's credential schema field names.
- Fail startup loudly if `AGS_MASTER_KEY` is unset. No dev-mode default.

## 6. Executors

```python
class Executor(Protocol):
    async def execute(
        self,
        action: Action,
        inputs: dict,
        connection: Connection | None,
    ) -> ExecutionResult: ...
```

`ExecutionResult` carries `output: Any`, `raw: dict | None`, `duration_ms: int`, `error: str | None`.

### 6.1 `http` executor

`executor_config` holds a request template:

```yaml
method: GET
url: "https://gmail.googleapis.com/gmail/v1/users/me/messages"
query:
  q: "{{ inputs.query }}"
  maxResults: "{{ inputs.limit | default(10) }}"
headers: {}
body: null
response:
  items_path: "$.messages"
  error_path: "$.error.message"
pagination:
  type: cursor            # cursor | offset | none
  cursor_param: pageToken
  cursor_path: "$.nextPageToken"
```

- Templating: Jinja2 with autoescape off, **sandboxed environment** (`jinja2.sandbox`). Only
  `inputs`, `connection.meta`, and a whitelist of filters are in scope.
- Response mapping via JSONPath (`jsonpath-ng`).
- Timeouts: 30s default, overridable per action, hard ceiling 120s.
- Never follow redirects to a different host without re-applying auth policy — do not leak
  credentials cross-origin.

### 6.2 `mcp` executor

`executor_config` holds `{ "server_id": "...", "tool_name": "..." }`.

- Maintain a pool of MCP client sessions keyed by server ID. Support `stdio` and
  `streamable-http` transports.
- `execute()` calls `tools/call` and returns the content blocks. Map MCP `isError: true` to
  `ExecutionResult.error`.
- Connections for MCP-sourced providers are typically null — the MCP server owns its own auth.
  Where a server accepts headers (a gateway with per-tenant auth), pass the connection's
  credential through as configured headers.
- Session failure must not crash the run. Reconnect once, then fail the action cleanly.

Store MCP server configs in a `mcp_servers` table: `id, name, transport, command/url, env_enc,
enabled`. Support the Docker MCP Gateway and MetaMCP as the reference targets.

## 7. Catalog ingestion

Three importers, all writing to `providers` + `actions`.

### 7.1 MCP introspection (build first)

`POST /api/catalog/sync/mcp/{server_id}` →
connect, call `tools/list`, upsert one `provider` per server (or per namespace if the gateway
exposes them), one `action` per tool. The MCP `inputSchema` becomes `actions.input_schema`
verbatim — it is already JSON Schema, which is the whole point.

Sync is idempotent. Tools that vanish are marked `deprecated=true`, never deleted (node
instances reference them).

### 7.2 YAML manifest loader

Directory `connectors/` at repo root, one file per provider, loaded at startup and on demand.

```yaml
slug: telegram
name: Telegram
auth_schemes:
  - type: api_key
    placement: path_segment
    param_name: bot_token
actions:
  - name: telegram_send_message
    display_name: Send message
    description: >
      Send a text message to a Telegram chat. Use when the workflow needs to
      notify a user or channel. Returns the sent message ID.
    input_schema:
      type: object
      required: [chat_id, text]
      properties:
        chat_id:
          type: string
          title: Chat ID
          x-ui: { widget: text }
        text:
          type: string
          title: Message
          x-ui: { widget: textarea, rows: 4 }
    executor_type: http
    executor_config:
      method: POST
      url: "https://api.telegram.org/bot{{ credential.bot_token }}/sendMessage"
      body:
        chat_id: "{{ inputs.chat_id }}"
        text: "{{ inputs.text }}"
```

Validate every manifest against a meta-schema at load. Fail loudly with the file path and
JSON Pointer on error.

### 7.3 OpenAPI importer

`POST /api/catalog/import/openapi` with a spec URL or upload.

- One `action` per operation. `operationId` → `name` (snake_cased, prefixed with provider slug
  if collision-prone). `summary` + `description` → `description`.
- Merge `parameters` and `requestBody` into a single flat `input_schema`. Resolve `$ref`s.
- Map `securitySchemes` onto our strategies.
- Import into a **staging state** (`deprecated=true`) and require explicit activation. Raw
  OpenAPI imports produce poor agent tools; a human must prune and rename before they go live.
  Do not auto-activate.

## 8. Agent toolset binding

An agent node holds an ordered list of `{action_id, connection_id}` pairs — tools wired on the
canvas, not discovered at runtime.

At run time, build the LLM tool list:

```python
tools = [
    {
        "name": f"{provider.slug}__{action.name}",   # namespaced, collision-free
        "description": action.description,
        "input_schema": strip_ui_extensions(action.input_schema),
    }
    for action, connection in agent_node.toolset
]
```

- `x-ui` extensions must be stripped before the schema reaches the model.
- Enforce a soft cap (default 40) with a warning in the canvas when exceeded. Do not implement
  a "bind all tools" mode — the explicit wiring *is* the context-management strategy, and it is
  a deliberate advantage over chat-based agents.
- Tool call → resolve to `(action, connection)` → executor → result serialized back as a tool
  result block. Truncate results over a configurable byte cap with an explicit marker.

## 9. Schema-driven UI contract

The node config panel is generated from `input_schema`. No per-app frontend code. Ever.

Recognized `x-ui` extension keys:

| Key | Effect |
|---|---|
| `widget` | `text` \| `textarea` \| `select` \| `code` \| `secret` \| `json` |
| `rows` | textarea height |
| `placeholder` | input placeholder |
| `order` | field ordering within the form |
| `depends_on` | `{field, value}` — conditional visibility |
| `options_source` | dynamic dropdown, see below |

### 9.1 Dynamic option loading

Required in v1, not deferred. Retrofitting means touching every manifest.

```yaml
channel_id:
  type: string
  title: Channel
  x-ui:
    widget: select
    options_source:
      method: GET
      url: "https://slack.com/api/conversations.list"
      items_path: "$.channels"
      label_path: "$.name"
      value_path: "$.id"
      depends_on: []          # re-fetch when these fields change
```

Backend endpoint: `POST /api/actions/{action_id}/options/{field_name}` with
`{connection_id, current_config}`. Executes the options request using the same auth pipeline,
maps to `[{label, value}]`, caches 60s per `(connection_id, field, deps_hash)`.

For `mcp` actions, `options_source` is unavailable — MCP has no equivalent. Fall back to a free
text input. Document this limitation.

## 10. API surface

```
GET    /api/providers                          list, filterable by source
GET    /api/providers/{id}/actions             action catalog for palette

POST   /api/oauth-apps                         register BYO client credentials
GET    /api/connections                        list (never includes credentials)
POST   /api/connections                        create for api_key/bearer/basic
GET    /api/connections/authorize              begin OAuth, 302 to provider
GET    /api/oauth/callback/{provider_slug}     OAuth callback
POST   /api/connections/{id}/test              live credential validation
DELETE /api/connections/{id}

POST   /api/actions/{id}/execute               direct invoke (testing a node)
POST   /api/actions/{id}/options/{field}       dynamic dropdown values

POST   /api/catalog/sync/mcp/{server_id}       introspect and upsert
POST   /api/catalog/import/openapi             staged import
POST   /api/catalog/reload-manifests
```

## 11. Directory structure

```
backend/app/connectors/
├── models.py                 SQLAlchemy models
├── schemas.py                Pydantic DTOs
├── auth/
│   ├── base.py               AuthStrategy protocol
│   ├── strategies/           api_key.py, bearer.py, basic.py, oauth2_*.py, custom.py
│   ├── registry.py           type -> strategy lookup
│   ├── crypto.py             envelope encryption
│   └── refresh.py            httpx event hook
├── executors/
│   ├── base.py               Executor protocol, ExecutionResult
│   ├── http_executor.py
│   ├── mcp_executor.py
│   └── mcp_pool.py           session pooling
├── catalog/
│   ├── manifest_loader.py
│   ├── openapi_importer.py
│   ├── mcp_introspector.py
│   └── meta_schema.json
├── options.py                dynamic option resolution
└── router.py

connectors/                   YAML manifests, repo root
frontend/src/components/nodes/
├── SchemaForm.tsx            JSON Schema -> form renderer
├── widgets/                  one file per x-ui widget type
└── ConnectionPicker.tsx
```

## 12. Phases

Each phase is a branch, ends in a merge, and must be independently demoable.

### Phase 1 — Skeleton + one manual connector
Models, migrations, crypto, `api_key` strategy, `http` executor, Telegram manifest, direct
execute endpoint.
**Done when:** `POST /api/actions/{id}/execute` sends a real Telegram message using a stored,
encrypted bot token. No frontend.

### Phase 2 — Schema-driven config UI
`SchemaForm.tsx` renders any `input_schema`. Connection picker. Node palette from
`/api/providers`.
**Done when:** the Telegram node is placeable, configurable, and runnable from the canvas with
zero Telegram-specific frontend code.

### Phase 3 — MCP executor + catalog sync
`mcp_servers` table, session pool, `mcp_executor`, `mcp_introspector`. Target the Docker MCP
Gateway.
**Done when:** pointing at a gateway with 3+ servers populates the palette automatically and
those nodes execute. This is the phase that proves the whole thesis — if the same
`SchemaForm.tsx` renders MCP tools with no changes, the abstraction is correct.

### Phase 4 — OAuth2
`oauth2_auth_code` strategy, BYO app registration, callback, refresh middleware, Gmail manifest
with `search_gmail`.
**Done when:** a Google connection survives an access-token expiry without user intervention.

### Phase 5 — Dynamic options
`options_source` resolution, caching, a Slack manifest with a live channel dropdown.

### Phase 6 — OpenAPI importer
Staged import with a review/activate UI.

### Phase 7 — Agent toolset binding
Wire actions onto agent nodes, namespaced tool list generation, tool-call dispatch, result
truncation.

> Phase 7 depends on phases 1–3 only. If agent execution is the priority, it can run in
> parallel with 4–6.

## 13. ADRs to record

- **ADR-001** Unified `Action` abstraction with pluggable executors (§3).
- **ADR-002** MCP as the primary catalog source; manifests as the depth path.
- **ADR-003** Bring-your-own OAuth application; no shipped client secrets.
- **ADR-004** Explicit canvas-wired toolsets over runtime tool discovery.
- **ADR-005** Envelope encryption with a required env-provided master key.

## 14. Testing

- **Unit:** each auth strategy against a table of fixture credentials + expected request mutations.
- **Contract:** every YAML manifest validated against `meta_schema.json` in CI. A malformed
  manifest fails the build.
- **Integration:** `http` executor against a `respx`-mocked provider, including a 401 → refresh
  → retry → success path and a 401 → refresh-fails → `ConnectionExpiredError` path.
- **MCP:** a minimal in-repo stub MCP server exposing two tools; assert introspection produces
  correct `actions` rows and that execution round-trips.
- **Security:** assert no API response body ever contains a plaintext credential; assert logs
  are redacted.
- **Golden:** snapshot the generated LLM tool list for a fixture agent node.

## 15. Open questions

Resolve before Phase 4. Do not block Phases 1–3 on these.

1. **Rate limiting.** Per-provider token bucket, or rely on the provider's 429 + our backoff?
   Leaning: 429 + exponential backoff in v1, token buckets only if it becomes a problem.
2. **Result size.** What is the truncation cap for tool results fed back to an LLM, and does the
   node output on the canvas get the full result while the agent gets the truncated one?
   (Probably yes — they have different consumers.)
3. **Connection sharing.** If two workflows use the same Gmail connection and one hits a refresh
   race, the advisory lock handles correctness — but should a revoked connection fail all
   dependent workflows loudly, or degrade?
4. **MCP server lifecycle on Windows.** `stdio` transport spawns child processes. Confirm
   cleanup on FastAPI shutdown and on reload; Windows process trees are not forgiving. Prefer
   `streamable-http` against a gateway over spawning `stdio` servers directly.

---

## Instructions for the implementing agent

- Work one phase per branch. Do not begin the next phase until the prior one's "done when"
  criterion is demonstrably met.
- Write the ADR at the point the decision is first implemented, not retroactively.
- If a design in this spec turns out to be wrong during implementation, **stop and say so**
  rather than working around it. A note in the PR describing the conflict is more valuable
  than a clever patch that preserves a bad abstraction.
- The single invariant that must not be violated: no frontend file and no core backend module
  may contain the name of a specific third-party application. If you find yourself writing
  `if provider == "gmail"`, the abstraction has leaked — raise it.