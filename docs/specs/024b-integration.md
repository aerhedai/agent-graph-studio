# SPEC: App Integrations & User Connections

**Component:** Agent Graph Studio — integrations layer
**Supersedes:** CONNECTORS_SPEC.md (v1). Do not implement from that document.
**Stack:** FastAPI + SQLAlchemy + Postgres (Supabase), React Flow frontend

---

## 0. Read this first

If you implemented anything from the previous spec, the following assumptions were wrong and
must be reversed:

| v1 said | Correct |
|---|---|
| Multi-tenancy out of scope | Per-user connections are the core feature |
| Users register their own OAuth apps | **We** own the OAuth apps. Users click "Connect" and consent. |
| Auth attaches to a provider | Auth attaches to a **credential type**, which is separate from both the app and the node |
| MCP executor is the primary path | Native app connectors are the primary path. MCP is optional, later, appendix only. |

**What we are building, stated plainly:** the n8n / Zapier "Connected accounts" experience.
A user opens a Gmail node, sees a dropdown of their Gmail connections, clicks "Create new",
gets a Google consent popup, comes back, and the node works. A second user on the same
instance sees only their own connections and never the first user's.

---

## 1. The four entities

Getting these separated correctly is the entire design. Collapsing any two of them produces
the wrong product.

**`App`** — Google, Telegram, Slack. Branding and grouping only. Holds no auth.

**`CredentialType`** — a *specific way of authenticating to an app*. This is the reusable unit.
One app can have several. Google needs `google_gmail_oauth2` and `google_sheets_oauth2` as
distinct types because OAuth scopes differ and requesting every Google scope in one consent
screen triggers a punishing verification review. Telegram has exactly one: `telegram_bot_token`.

**`Connection`** — an instance of a credential type, owned by a user. "Rohan's work Gmail".
This is what lives in the vault, encrypted. This is what appears in the node dropdown.

**`Action`** — one tool (`gmail_search_messages`). Declares which `CredentialType` it requires.
Never references a `Connection` — that binding happens per node instance, per user.

The payoff: a user connects Google once, and every action declaring `google_gmail_oauth2`
immediately offers that connection. Adding a new Gmail action requires no auth work at all.

---

## 2. Schema

```sql
CREATE TABLE apps (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    slug       TEXT UNIQUE NOT NULL,            -- 'google', 'telegram'
    name       TEXT NOT NULL,
    icon_url   TEXT,
    docs_url   TEXT,
    category   TEXT                             -- palette grouping
);

CREATE TABLE credential_types (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    app_id       UUID NOT NULL REFERENCES apps(id) ON DELETE CASCADE,
    slug         TEXT UNIQUE NOT NULL,          -- 'google_gmail_oauth2'
    name         TEXT NOT NULL,                 -- 'Gmail (OAuth2)'
    auth_type    TEXT NOT NULL,                 -- see §3
    config       JSONB NOT NULL,                -- urls, scopes, placement
    field_schema JSONB NOT NULL                 -- JSON Schema for manual fields
);

-- OAuth applications WE register with each provider. Seeded from env at boot.
CREATE TABLE oauth_clients (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    credential_type_id UUID NOT NULL REFERENCES credential_types(id) ON DELETE CASCADE,
    client_id          TEXT NOT NULL,
    client_secret_enc  BYTEA NOT NULL,
    scopes             TEXT[] NOT NULL DEFAULT '{}',
    is_system          BOOLEAN NOT NULL DEFAULT true,
    owner_user_id      UUID,                    -- non-null only when a user overrides
    CHECK (is_system OR owner_user_id IS NOT NULL)
);

-- THE VAULT. One row per user per connected account.
CREATE TABLE connections (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_user_id      UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    credential_type_id UUID NOT NULL REFERENCES credential_types(id),
    oauth_client_id    UUID REFERENCES oauth_clients(id),
    name               TEXT NOT NULL,           -- 'Work Gmail' — user-facing
    data_enc           BYTEA NOT NULL,          -- tokens or api key, encrypted
    account_label      TEXT,                    -- 'rohan@example.com', from userinfo
    expires_at         TIMESTAMPTZ,
    status             TEXT NOT NULL DEFAULT 'active',  -- active|expired|revoked|error
    last_error         TEXT,
    last_tested_at     TIMESTAMPTZ,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (owner_user_id, credential_type_id, name)
);

CREATE INDEX ON connections (owner_user_id, credential_type_id);

CREATE TABLE connection_shares (
    connection_id     UUID NOT NULL REFERENCES connections(id) ON DELETE CASCADE,
    shared_with_user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    role              TEXT NOT NULL DEFAULT 'use',   -- 'use' | 'edit'
    PRIMARY KEY (connection_id, shared_with_user_id)
);

CREATE TABLE actions (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    app_id             UUID NOT NULL REFERENCES apps(id) ON DELETE CASCADE,
    credential_type_id UUID REFERENCES credential_types(id),  -- null = no auth needed
    name               TEXT NOT NULL,           -- 'gmail_search_messages'
    display_name       TEXT NOT NULL,
    description        TEXT NOT NULL,
    input_schema       JSONB NOT NULL,
    request_template   JSONB NOT NULL,
    deprecated         BOOLEAN NOT NULL DEFAULT false,
    UNIQUE (app_id, name)
);

-- A node placed on a canvas. connection_id is nullable and user-specific.
CREATE TABLE node_instances (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workflow_id   UUID NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
    action_id     UUID NOT NULL REFERENCES actions(id),
    connection_id UUID REFERENCES connections(id) ON DELETE SET NULL,
    config        JSONB NOT NULL DEFAULT '{}',
    position      JSONB NOT NULL
);
```

`connection_id` is nullable and `ON DELETE SET NULL` on purpose. When a workflow is duplicated,
templated, or shared, connection references must be re-bound by the receiving user. The node
renders in a "needs connection" state rather than silently failing or leaking access.

---

## 3. Auth types

Five. Each is one class. Adding an app never adds a class.

| `auth_type` | `config` keys | `field_schema` collects |
|---|---|---|
| `api_key` | `placement` (header/query/path/body), `param_name`, `prefix` | the key |
| `bearer` | — | the token |
| `basic` | — | username, password |
| `oauth2_auth_code` | `authorize_url`, `token_url`, `scopes`, `use_pkce`, `extra_authorize_params`, `userinfo_url`, `userinfo_label_path` | nothing — the flow supplies it |
| `oauth2_client_credentials` | `token_url`, `scopes` | client id, client secret |

```python
class AuthType(Protocol):
    slug: ClassVar[str]

    async def apply(self, data: dict, request: httpx.Request) -> httpx.Request: ...
    async def refresh(self, data: dict, cfg: dict, client: OAuthClient | None) -> dict | None: ...
    async def test(self, data: dict, cfg: dict) -> TestResult: ...
```

`test()` is not optional. Every credential type must define a cheap read-only call used by the
"Test connection" button and by the periodic health check. For Gmail it's `users.getProfile`;
for Telegram it's `getMe`. Its response also populates `account_label` so the dropdown reads
"Work Gmail (rohan@example.com)" rather than an opaque name.

---

## 4. OAuth: we own the app

This is the part v1 got wrong and it defines the whole user experience.

### 4.1 Registration (one-time, by you, not by users)

For each OAuth credential type you register an application in that provider's console — Google
Cloud Console, Slack API dashboard, GitHub Developer Settings — and set the redirect URI to a
**single shared callback**:

```
{PUBLIC_BASE_URL}/api/oauth/callback
```

One URL for every provider. The `state` parameter carries the routing, so you register one
redirect per provider console and never touch it again.

Client IDs and secrets come from env, seeded into `oauth_clients` at boot:

```
AGS_OAUTH_GOOGLE_GMAIL_CLIENT_ID=...
AGS_OAUTH_GOOGLE_GMAIL_CLIENT_SECRET=...
AGS_OAUTH_SLACK_CLIENT_ID=...
```

A credential type whose env vars are absent is marked unavailable and hidden from the palette
with a "not configured on this instance" note. Do not crash; do not show a broken connect button.

### 4.2 The flow

```
1. User clicks "Connect Google" in a node's connection picker.
2. Frontend opens a popup:  GET /api/oauth/authorize?credential_type=google_gmail_oauth2
3. Backend:
     - authenticates the user from their session
     - generates PKCE verifier + a random opaque state token
     - stores {user_id, credential_type_id, oauth_client_id, verifier} keyed by state,
       TTL 10 minutes, single-use
     - 302 -> provider authorize_url
4. User consents on the provider's page.
5. Provider redirects to /api/oauth/callback?code=...&state=...
6. Backend:
     - looks up and IMMEDIATELY DELETES the state record (single-use)
     - exchanges code + verifier for tokens
     - calls userinfo_url to derive account_label
     - encrypts and inserts a `connections` row with owner_user_id from the state record
     - returns a tiny HTML page that does:
         window.opener.postMessage({type:'ags:oauth', ok:true, connectionId}, ORIGIN)
         window.close()
7. Parent window receives the message, refetches the connection list, auto-selects the new one.
```

The `owner_user_id` comes from the server-side state record, never from a query parameter or
anything the browser could tamper with. This is the single most security-critical line in the
system.

### 4.3 Refresh

- A background task refreshes any connection within 5 minutes of `expires_at`.
- On a 401 during execution: refresh once, retry once, then set `status='expired'` and surface
  a "Reconnect" prompt on every node bound to it.
- Serialize refreshes per `connection_id` with a Postgres advisory lock keyed on the UUID hash.
  Two concurrent workflow runs sharing one connection will otherwise race and one will burn the
  refresh token.
- Providers that rotate refresh tokens (Google does under some configurations) require writing
  the new refresh token back. Always persist the full token response, not just the access token.

### 4.4 User-supplied OAuth clients (optional, later)

Some users will hit your app's rate limits or want their own consent screen branding. The
`oauth_clients.is_system` flag already supports this — a user adds a row with
`is_system=false, owner_user_id=<them>`, and the authorize endpoint accepts an optional
`oauth_client_id`. Build the schema for it now; build the UI in a later phase.

---

## 5. Tenant isolation

Non-negotiable. Every one of these must hold.

- **Repository layer.** All connection reads go through a repository whose every method takes
  `user_id` as a required, non-defaulted argument. Filter is
  `owner_user_id = :uid OR id IN (SELECT connection_id FROM connection_shares WHERE shared_with_user_id = :uid)`.
  No raw session queries against `connections` anywhere else in the codebase.

- **RLS as defense in depth.** Enable RLS on `connections` and `connection_shares` in Supabase.
  If FastAPI connects with a service-role key, RLS will not fire — so it is a backstop against
  a leaked anon key, not the primary control. Do not treat it as sufficient.

```sql
ALTER TABLE connections ENABLE ROW LEVEL SECURITY;
CREATE POLICY conn_owner ON connections FOR ALL
  USING (owner_user_id = auth.uid()
         OR EXISTS (SELECT 1 FROM connection_shares s
                    WHERE s.connection_id = id AND s.shared_with_user_id = auth.uid()));
```

- **Execution-time authorization.** Before any node runs, assert the executing principal can
  access `node_instances.connection_id`. A workflow author cannot bind a connection they do not
  own, and a shared workflow cannot carry the author's credentials to another user.

- **Never serialize `data_enc`.** No API response, no websocket frame, no execution trace, no
  error message. Add a test that runs every endpoint and asserts a canary secret value appears
  in zero response bodies.

- **Redact in traces.** Execution traces must scrub values matching any field in the credential
  type's `field_schema`, plus `Authorization` headers, before persisting.

### 5.1 Encryption

Envelope encryption, AES-GCM. `AGS_ENCRYPTION_KEY` (32 bytes, base64) from env. Per-record data
key wrapped by the master key, stored alongside the ciphertext. Refuse to start if the key is
missing — no dev default, no generated fallback, because a generated fallback means every
credential in the database becomes unreadable on the next restart.

Support key rotation from day one: store a `key_version` byte prefix so a rotation job can
re-wrap records incrementally.

---

## 6. Node ↔ connection binding

An action declares `credential_type_id`. The node config panel does exactly this:

```
GET /api/connections?credential_type=google_gmail_oauth2
  -> [{id, name, account_label, status}]
```

Rendered as a dropdown:

```
┌──────────────────────────────────────┐
│ Credential to connect with           │
│ ┌──────────────────────────────────┐ │
│ │ Work Gmail (rohan@example.com) ▾ │ │
│ ├──────────────────────────────────┤ │
│ │ Work Gmail (rohan@example.com)   │ │
│ │ Personal (r.dev@gmail.com)       │ │
│ │ ─────────────────────────────    │ │
│ │ + Create new connection          │ │
│ └──────────────────────────────────┘ │
└──────────────────────────────────────┘
```

- "Create new" launches the popup flow in §4.2 without leaving the canvas.
- A connection with `status='expired'` renders with a warning and a "Reconnect" button that
  re-runs the authorize flow against the existing row rather than creating a duplicate.
- The dropdown is one shared React component used by every node. It takes `credentialTypeSlug`
  and nothing else. There is no per-app frontend code.

There is also a standalone **Connections page** (`/settings/connections`) listing all of a
user's connections grouped by app, with test, rename, share, and delete. Deleting a connection
must warn about the count of node instances that reference it.

---

## 7. Defining an app

One YAML file per app in `connectors/`. Loaded and validated at startup. This is the only
artifact required to add an integration.

```yaml
slug: telegram
name: Telegram
category: communication
icon_url: /icons/telegram.svg

credential_types:
  - slug: telegram_bot_token
    name: Telegram Bot
    auth_type: api_key
    config:
      placement: path_segment
      param_name: bot_token
    field_schema:
      type: object
      required: [bot_token]
      properties:
        bot_token:
          type: string
          title: Bot token
          description: From @BotFather
          x-ui: { widget: secret }
    test:
      method: GET
      url: "https://api.telegram.org/bot{{ credential.bot_token }}/getMe"
      label_path: "$.result.username"

actions:
  - name: telegram_send_message
    display_name: Send message
    credential_type: telegram_bot_token
    description: >
      Send a text message to a Telegram chat or channel. Returns the sent message ID.
    input_schema:
      type: object
      required: [chat_id, text]
      properties:
        chat_id: { type: string, title: Chat ID }
        text:   { type: string, title: Message, x-ui: { widget: textarea, rows: 4 } }
    request_template:
      method: POST
      url: "https://api.telegram.org/bot{{ credential.bot_token }}/sendMessage"
      body:
        chat_id: "{{ inputs.chat_id }}"
        text: "{{ inputs.text }}"
      response:
        data_path: "$.result"
        error_path: "$.description"
```

A Google file declares two credential types with different scope lists and several actions
across Gmail and Sheets, all sharing whichever credential type each one names.

Validate every file against `connectors/meta_schema.json` in CI. A malformed manifest fails the
build, not runtime.

Templating is Jinja2 in a **sandboxed environment**, with only `inputs`, `credential`, and a
filter whitelist in scope.

---

## 8. Dynamic option loading

Required in v1 of this system. It is what makes nodes feel finished, and retrofitting it means
editing every manifest.

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
      depends_on: []
```

`POST /api/actions/{action_id}/options/{field}` with `{connection_id, current_config}`. Runs
through the same auth pipeline. Authorize the connection against the caller before executing —
this endpoint is a credential-use path and must not be treated as a read-only lookup. Cache 60s
per `(connection_id, field, deps_hash)`.

---

## 9. API surface

```
GET    /api/apps                                  palette, grouped by category
GET    /api/apps/{slug}/actions
GET    /api/credential-types?app={slug}

GET    /api/connections?credential_type={slug}    CURRENT USER ONLY
POST   /api/connections                           manual types (api_key/basic/bearer)
GET    /api/oauth/authorize?credential_type=...   begin flow, 302
GET    /api/oauth/callback                        single shared callback
POST   /api/connections/{id}/reconnect            re-auth existing row
POST   /api/connections/{id}/test
PATCH  /api/connections/{id}                      rename only
DELETE /api/connections/{id}
POST   /api/connections/{id}/shares
DELETE /api/connections/{id}/shares/{user_id}

POST   /api/actions/{id}/execute                  test a node
POST   /api/actions/{id}/options/{field}
```

---

## 10. Phases

Each is a branch and must be independently demoable.

**Phase 1 — Vault + manual auth.**
Schema, migrations, encryption, `api_key` type, repository with mandatory user scoping, Telegram
manifest, HTTP executor.
*Done when:* two different users each store a Telegram bot token, each sees only their own via
the API, and each can send a message. Prove isolation with a test that authenticates as user B
and asserts user A's connection is invisible.

**Phase 2 — Connections UI + schema-driven node config.**
Connections page, the shared connection picker, `SchemaForm.tsx` rendering any `input_schema`.
*Done when:* the Telegram node is fully usable on the canvas with zero Telegram-specific
frontend code.

**Phase 3 — OAuth2.**
`oauth2_auth_code`, env-seeded `oauth_clients`, authorize + callback, popup + postMessage,
refresh with advisory locking, Google manifest with `gmail_search_messages`.
*Done when:* a user clicks "Connect Google", consents, and the node works — having never seen a
developer console. And it survives an access-token expiry unattended.

**Phase 4 — Breadth.** Slack, Notion, GitHub, Discord, Airtable manifests. No new backend code
should be required. If any of them forces a backend change, that is a signal the abstraction is
wrong — stop and report it rather than special-casing.

**Phase 5 — Dynamic options.** Slack channel dropdown as the reference implementation.

**Phase 6 — Sharing + health.** `connection_shares` UI, periodic background `test()` sweep
marking stale connections, reconnect prompts on affected nodes.

**Phase 7 — Agent toolset binding.** Actions bindable to agent nodes; namespaced tool names
(`gmail__search_messages`); per-tool connection resolution at call time.

---

## 11. Appendix: MCP (optional, do not build yet)

An `mcp` executor can later be added behind the same `Action` interface to pull in external MCP
servers as extra nodes. It is explicitly **not** part of this build. Native connectors with
per-user OAuth are the product; MCP is a bonus surface. Do not let it influence the design of
anything above.

---

## Instructions for the implementing agent

- One phase per branch. Do not start the next until the "done when" is demonstrated.
- **The isolation test in Phase 1 is the gate for everything.** If user B can see user A's
  connection at any point in any later phase, stop and fix it before continuing.
- Invariant: no file under `frontend/src/components/` and no module under `app/core/` may
  contain the name of a specific third-party app. If you write `if app == "gmail"`, the
  abstraction has leaked — raise it rather than working around it.
- If a design here proves wrong during implementation, say so in the PR instead of patching
  around it.