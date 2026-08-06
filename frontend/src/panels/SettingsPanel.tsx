import { useEffect, useState, type ReactNode } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { cn } from "@/lib/utils";
import {
  bootstrapCatalogConnection,
  clearApiKey,
  connectMcpOAuthViaPopup,
  createConnection,
  deleteConnection,
  fetchConnectionTypes,
  fetchConnections,
  fetchPrivateConnectionsSummary,
  getMe,
  getSettings,
  inviteUser,
  mcpOAuthStartUrl,
  setConnectionApiKey,
  testConnection,
  updateConnection,
  updateSettings,
} from "../api/client";
import type { ConnectionInfo, ConnectionTypeInfo, MeResponse, PrivateConnectionSummary } from "../api/types";
import { renderPrimitiveField } from "./fieldRenderers";

interface SettingsPanelProps {
  onClose: () => void;
}

function OptionalTag() {
  return <span className="ml-1 text-[10px] font-normal text-muted-foreground italic">optional</span>;
}

function SectionHeading({ children }: { children: string }) {
  return <h2 className="mt-4 text-sm font-semibold text-foreground">{children}</h2>;
}

function Hint({ children }: { children: ReactNode }) {
  return <p className="text-[13px] text-muted-foreground">{children}</p>;
}

function ErrorText({ children }: { children: ReactNode }) {
  return <div className="text-xs text-[var(--status-error)]">{children}</div>;
}

function TestResult({ result }: { result: { success: boolean; message: string } }) {
  return (
    <div
      className={cn(
        "rounded-[var(--radius-sm)] px-2 py-1.5 text-xs",
        result.success
          ? "bg-[color-mix(in_srgb,var(--status-success)_15%,transparent)] text-[var(--status-success)]"
          : "bg-[color-mix(in_srgb,var(--status-error)_15%,transparent)] text-[var(--status-error)]",
      )}
    >
      {result.message}
    </div>
  );
}

// spec-018: the one app-level setting needed to auto-register external
// webhooks (Telegram's setWebhook/deleteWebhook) -- the app can't discover
// its own externally-reachable address, so this is an explicit,
// operator-set value, not something inferred. Saving triggers a real
// (non-blocking) reachability check against {url}/health, surfaced as a
// warning, never a hard block.
export function SettingsPanel({ onClose }: SettingsPanelProps) {
  const [draft, setDraft] = useState("");
  const [saved, setSaved] = useState<string | null>(null);
  const [warning, setWarning] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  // spec-020: who's signed in, and whether they're allowed to invite
  // others -- a shared-API-key caller (no `me`) simply doesn't see the
  // account section or the invite affordance at all.
  const [me, setMe] = useState<MeResponse | null>(null);
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteStatus, setInviteStatus] = useState<string | null>(null);
  const [inviteError, setInviteError] = useState<string | null>(null);
  const [inviting, setInviting] = useState(false);

  // spec-023: the real Connections management surface -- previously the
  // only way to see/edit/delete a connection was the picker embedded in a
  // node's config panel. Admin-only, listing global connections (the ones
  // this spec makes admin-managed) plus a read-only "names only" view into
  // other users' private connections for support.
  const [globalConnections, setGlobalConnections] = useState<ConnectionInfo[]>([]);
  const [privateSummary, setPrivateSummary] = useState<PrivateConnectionSummary[]>([]);
  const [connectionTypes, setConnectionTypes] = useState<ConnectionTypeInfo[]>([]);
  const [connError, setConnError] = useState<string | null>(null);
  const [showConnForm, setShowConnForm] = useState(false);
  const [connDraftName, setConnDraftName] = useState("");
  const [connDraftType, setConnDraftType] = useState<string | null>(null);
  const [connDraftConfig, setConnDraftConfig] = useState<Record<string, unknown>>({});
  const [connTestResult, setConnTestResult] = useState<{ success: boolean; message: string } | null>(null);
  const [connTesting, setConnTesting] = useState(false);
  const [connSaving, setConnSaving] = useState(false);
  const [connFormError, setConnFormError] = useState<string | null>(null);
  const [deletingConn, setDeletingConn] = useState<string | null>(null);
  const [editingConn, setEditingConn] = useState<string | null>(null);
  const [editDraftConfig, setEditDraftConfig] = useState<Record<string, unknown>>({});
  const [editSaving, setEditSaving] = useState(false);
  const [showPrivateSummary, setShowPrivateSummary] = useState(false);

  // spec-025: per-row api-key paste state -- this list view can show several
  // global connections at once (unlike ConnectionPicker.tsx, which only ever
  // has one selected connection), so drafts/errors/loading are keyed by
  // connection name rather than a single flat value.
  const [apiKeyDrafts, setApiKeyDrafts] = useState<Record<string, string>>({});
  const [settingApiKeyFor, setSettingApiKeyFor] = useState<string | null>(null);
  const [apiKeyErrors, setApiKeyErrors] = useState<Record<string, string>>({});

  // spec-025: catalog-bootstrap -- explicit admin action to (re)generate a
  // global connection's node types using the admin's own connected
  // credential, so the catalog entry's nodes exist before any other user
  // connects. Per-row state, same rationale as the api-key drafts above.
  const [bootstrappingFor, setBootstrappingFor] = useState<string | null>(null);
  const [bootstrapStatus, setBootstrapStatus] = useState<Record<string, string>>({});

  // spec-025: popup-based OAuth connect, same rationale/per-row state shape
  // as the api-key drafts above.
  const [poppingUpFor, setPoppingUpFor] = useState<string | null>(null);
  const [popupErrors, setPopupErrors] = useState<Record<string, string>>({});

  function loadConnectionsAdminData() {
    return Promise.all([fetchConnections(), fetchConnectionTypes(), fetchPrivateConnectionsSummary()]).then(
      ([conns, types]) => {
        setGlobalConnections(conns.filter((c) => c.is_global));
        setConnectionTypes(types);
      },
    );
  }

  useEffect(() => {
    getSettings()
      .then((res) => {
        setSaved(res.public_base_url);
        setDraft(res.public_base_url ?? "");
      })
      .catch((e: unknown) => setError(String(e)));
    getMe()
      .then((m) => {
        setMe(m);
        if (m.role === "admin") {
          loadConnectionsAdminData().catch((e: unknown) => setConnError(String(e)));
        }
      })
      .catch(() => setMe(null));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const connDraftTypeInfo = connectionTypes.find((t) => t.type === connDraftType);

  async function handleTestNewConnection() {
    if (!connDraftType) return;
    setConnTesting(true);
    setConnFormError(null);
    setConnTestResult(null);
    try {
      setConnTestResult(await testConnection(connDraftName || "draft", { type: connDraftType, config: connDraftConfig }));
    } catch (e) {
      setConnFormError(String(e));
    } finally {
      setConnTesting(false);
    }
  }

  async function handleSaveNewGlobalConnection() {
    if (!connDraftType || !connDraftName) return;
    setConnSaving(true);
    setConnFormError(null);
    try {
      await createConnection(connDraftName, connDraftType, connDraftConfig, "global");
      await loadConnectionsAdminData();
      setShowConnForm(false);
      setConnDraftName("");
      setConnDraftType(null);
      setConnDraftConfig({});
      setConnTestResult(null);
    } catch (e) {
      setConnFormError(String(e));
    } finally {
      setConnSaving(false);
    }
  }

  async function handleDeleteGlobalConnection(name: string) {
    if (!window.confirm(`Delete global connection "${name}"? This can't be undone.`)) return;
    setDeletingConn(name);
    setConnError(null);
    try {
      await deleteConnection(name);
      await loadConnectionsAdminData();
    } catch (e) {
      setConnError(String(e));
    } finally {
      setDeletingConn(null);
    }
  }

  async function handleSetApiKeyFor(name: string) {
    const apiKey = (apiKeyDrafts[name] ?? "").trim();
    if (!apiKey) return;
    setSettingApiKeyFor(name);
    setApiKeyErrors((errs) => ({ ...errs, [name]: "" }));
    try {
      await setConnectionApiKey(name, apiKey);
      await loadConnectionsAdminData();
      setApiKeyDrafts((drafts) => ({ ...drafts, [name]: "" }));
    } catch (e) {
      setApiKeyErrors((errs) => ({ ...errs, [name]: String(e) }));
    } finally {
      setSettingApiKeyFor(null);
    }
  }

  async function handleConnectViaPopup(name: string) {
    setPoppingUpFor(name);
    setPopupErrors((errs) => ({ ...errs, [name]: "" }));
    try {
      const result = await connectMcpOAuthViaPopup(name);
      if (result.error) {
        setPopupErrors((errs) => ({ ...errs, [name]: result.error! }));
      } else {
        await loadConnectionsAdminData();
      }
    } catch (e) {
      setPopupErrors((errs) => ({ ...errs, [name]: String(e) }));
    } finally {
      setPoppingUpFor(null);
    }
  }

  async function handleBootstrapFor(name: string) {
    setBootstrappingFor(name);
    setBootstrapStatus((s) => ({ ...s, [name]: "" }));
    try {
      const res = await bootstrapCatalogConnection(name);
      setBootstrapStatus((s) => ({ ...s, [name]: `Generated ${res.generated_types.length} node type(s).` }));
    } catch (e) {
      setBootstrapStatus((s) => ({ ...s, [name]: String(e) }));
    } finally {
      setBootstrappingFor(null);
    }
  }

  function startEditingConnection(c: ConnectionInfo) {
    setEditingConn(c.name);
    setEditDraftConfig({});
  }

  async function handleSaveEditedConnection(name: string) {
    setEditSaving(true);
    setConnError(null);
    try {
      await updateConnection(name, editDraftConfig);
      await loadConnectionsAdminData();
      setEditingConn(null);
    } catch (e) {
      setConnError(String(e));
    } finally {
      setEditSaving(false);
    }
  }

  async function handleInvite() {
    if (!inviteEmail.trim()) return;
    setInviting(true);
    setInviteError(null);
    setInviteStatus(null);
    try {
      const res = await inviteUser(inviteEmail.trim());
      setInviteStatus(`Invited ${res.email} as ${res.role}.`);
      setInviteEmail("");
    } catch (e) {
      setInviteError(String(e));
    } finally {
      setInviting(false);
    }
  }

  function handleSignOut() {
    clearApiKey();
    window.location.reload();
  }

  async function handleSave() {
    if (!draft.trim()) return;
    setSaving(true);
    setError(null);
    setWarning(null);
    try {
      const res = await updateSettings(draft.trim());
      setSaved(res.public_base_url);
      setDraft(res.public_base_url);
      setWarning(res.warning);
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Sheet open onOpenChange={(open) => !open && onClose()}>
      <SheetContent className="w-[420px] max-w-[90vw] gap-3 overflow-y-auto p-4">
        <SheetHeader className="p-0">
          <SheetTitle>Settings</SheetTitle>
        </SheetHeader>

        <div className="flex flex-col gap-1">
          <Label htmlFor="public-base-url">Public base URL</Label>
          <Input
            id="public-base-url"
            type="text"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="https://your-tunnel-or-domain.example.com"
          />
        </div>
        <Hint>
          Used to auto-register external webhooks (e.g. Telegram's <code>setWebhook</code>) when
          you Activate a graph that needs one -- this is wherever this backend is actually
          reachable from the outside (a Tailscale Funnel/ngrok URL, or your real domain once
          deployed).
        </Hint>

        <Button type="button" onClick={() => void handleSave()} disabled={saving} className="self-start">
          {saving ? "Saving..." : "Save"}
        </Button>

        {saved && <Hint>Currently set to: {saved}</Hint>}
        {warning && <ErrorText>{warning}</ErrorText>}
        {error && <ErrorText>{error}</ErrorText>}

        {me && (
          <>
            <SectionHeading>Account</SectionHeading>
            <Hint>
              Signed in as {me.display_name} ({me.email}) -- {me.role}
            </Hint>
            <Button type="button" variant="outline" onClick={handleSignOut} className="self-start">
              Sign out
            </Button>
          </>
        )}

        {me?.role === "admin" && (
          <>
            <SectionHeading>Invite a user</SectionHeading>
            <div className="flex flex-col gap-1">
              <Label htmlFor="invite-email">Email</Label>
              <Input
                id="invite-email"
                type="email"
                value={inviteEmail}
                onChange={(e) => setInviteEmail(e.target.value)}
                placeholder="teammate@example.com"
              />
            </div>
            <Button type="button" onClick={() => void handleInvite()} disabled={inviting} className="self-start">
              {inviting ? "Inviting..." : "Invite"}
            </Button>
            {inviteStatus && <Hint>{inviteStatus}</Hint>}
            {inviteError && <ErrorText>{inviteError}</ErrorText>}
          </>
        )}

        {me?.role === "admin" && (
          <>
            <SectionHeading>Connections</SectionHeading>
            <Hint>
              Global connections are visible to every user (e.g. a shared Gmail/Discord app they can each
              connect their own account to) and are only manageable here, by an admin. A user's own private
              connections stay self-service, unaffected -- this section never shows their config or secrets.
            </Hint>
            {connError && <ErrorText>{connError}</ErrorText>}

            {globalConnections.length === 0 && !showConnForm && <Hint>No global connections yet.</Hint>}
            {globalConnections.map((c) => (
              <div key={c.name} className="flex flex-col gap-2 rounded-[var(--radius-sm)] border border-border p-2">
                <Label>
                  {c.name} ({c.type})
                  {c.requires_oauth ? (c.oauth_connected ? " ✓ connected" : " — needs OAuth") : ""}
                  {c.auth_type !== "oauth2" ? (c.api_key_connected ? " ✓ connected" : " — needs API key") : ""}
                </Label>
                {c.requires_oauth && !c.oauth_connected && (
                  <div className="flex gap-1.5">
                    <Button
                      type="button"
                      onClick={() => void handleConnectViaPopup(c.name)}
                      disabled={poppingUpFor === c.name}
                    >
                      {poppingUpFor === c.name ? "Connecting..." : "Connect"}
                    </Button>
                    {/* spec-025 additive fallback -- some browsers/contexts block
                        popups, so the original real top-level navigation (same
                        fragment-based return Canvas.tsx already parses) stays
                        available. */}
                    <Button type="button" variant="outline" asChild>
                      <a href={mcpOAuthStartUrl(c.name, window.location.origin + window.location.pathname)}>
                        Connect (full page)
                      </a>
                    </Button>
                  </div>
                )}
                {popupErrors[c.name] && <ErrorText>{popupErrors[c.name]}</ErrorText>}
                {c.auth_type !== "oauth2" && !c.api_key_connected && (
                  <div className="flex flex-col gap-1">
                    <Input
                      type="password"
                      value={apiKeyDrafts[c.name] ?? ""}
                      onChange={(e) => setApiKeyDrafts((drafts) => ({ ...drafts, [c.name]: e.target.value }))}
                      placeholder={c.auth_type === "bearer" ? "Paste bearer token" : "Paste API key"}
                    />
                    <Button
                      type="button"
                      onClick={() => void handleSetApiKeyFor(c.name)}
                      disabled={settingApiKeyFor === c.name || !(apiKeyDrafts[c.name] ?? "").trim()}
                      className="self-start"
                    >
                      {settingApiKeyFor === c.name ? "Connecting..." : "Connect"}
                    </Button>
                    {apiKeyErrors[c.name] && <ErrorText>{apiKeyErrors[c.name]}</ErrorText>}
                  </div>
                )}
                {editingConn === c.name ? (
                  <>
                    <Hint>Config isn't shown back for security -- re-enter every field to save changes.</Hint>
                    {(() => {
                      const typeInfo = connectionTypes.find((t) => t.type === c.type);
                      const requiredFields = new Set(typeInfo?.config_schema.required ?? []);
                      return Object.entries(typeInfo?.config_schema.properties ?? {}).map(([name, propSchema]) => (
                        <div key={name} className="flex flex-col gap-1">
                          <Label htmlFor={`edit-${c.name}-${name}`}>
                            {propSchema.title ?? name}
                            {!requiredFields.has(name) && <OptionalTag />}
                          </Label>
                          {renderPrimitiveField(name, propSchema, editDraftConfig[name], (n, v) =>
                            setEditDraftConfig((cfg) => ({ ...cfg, [n]: v })),
                          )}
                        </div>
                      ));
                    })()}
                    <div className="flex gap-2">
                      <Button type="button" onClick={() => void handleSaveEditedConnection(c.name)} disabled={editSaving}>
                        {editSaving ? "Saving..." : "Save"}
                      </Button>
                      <Button type="button" variant="outline" onClick={() => setEditingConn(null)}>
                        Cancel
                      </Button>
                    </div>
                  </>
                ) : (
                  <div className="flex gap-1.5">
                    <Button type="button" variant="outline" onClick={() => startEditingConnection(c)}>
                      Edit
                    </Button>
                    <Button
                      type="button"
                      variant="outline"
                      onClick={() => void handleDeleteGlobalConnection(c.name)}
                      disabled={deletingConn === c.name}
                    >
                      {deletingConn === c.name ? "Deleting..." : "Delete"}
                    </Button>
                    {(c.oauth_connected || c.api_key_connected) && (
                      <Button
                        type="button"
                        variant="outline"
                        onClick={() => void handleBootstrapFor(c.name)}
                        disabled={bootstrappingFor === c.name}
                      >
                        {bootstrappingFor === c.name ? "Bootstrapping..." : "Bootstrap catalog nodes"}
                      </Button>
                    )}
                  </div>
                )}
                {bootstrapStatus[c.name] && <Hint>{bootstrapStatus[c.name]}</Hint>}
              </div>
            ))}

            <Button type="button" variant="outline" onClick={() => setShowConnForm((s) => !s)} className="self-start">
              {showConnForm ? "Cancel" : "+ New global connection"}
            </Button>

            {showConnForm && (
              <div className="flex flex-col gap-2 rounded-[var(--radius-md)] border border-border bg-background p-2.5">
                <div className="flex flex-col gap-1">
                  <Label htmlFor="new-global-conn-name">Connection name</Label>
                  <Input
                    id="new-global-conn-name"
                    type="text"
                    value={connDraftName}
                    onChange={(e) => setConnDraftName(e.target.value)}
                    placeholder="e.g. shared-gmail"
                  />
                </div>
                <div className="flex flex-col gap-1">
                  <Label htmlFor="new-global-conn-type">Type</Label>
                  <Select
                    value={connDraftType ?? ""}
                    onValueChange={(v) => {
                      setConnDraftType(v || null);
                      setConnDraftConfig({});
                      setConnTestResult(null);
                    }}
                  >
                    <SelectTrigger id="new-global-conn-type" className="w-full">
                      <SelectValue placeholder="Select type..." />
                    </SelectTrigger>
                    <SelectContent>
                      {connectionTypes.map((t) => (
                        <SelectItem key={t.type} value={t.type}>
                          {t.type}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>

                {connDraftTypeInfo &&
                  (() => {
                    const requiredFields = new Set(connDraftTypeInfo.config_schema.required ?? []);
                    return Object.entries(connDraftTypeInfo.config_schema.properties ?? {}).map(
                      ([name, propSchema]) => (
                        <div key={name} className="flex flex-col gap-1">
                          <Label htmlFor={`new-global-conn-${name}`}>
                            {propSchema.title ?? name}
                            {!requiredFields.has(name) && <OptionalTag />}
                          </Label>
                          {renderPrimitiveField(name, propSchema, connDraftConfig[name], (n, v) =>
                            setConnDraftConfig((cfg) => ({ ...cfg, [n]: v })),
                          )}
                        </div>
                      ),
                    );
                  })()}

                {connTestResult && <TestResult result={connTestResult} />}
                {connFormError && <ErrorText>{connFormError}</ErrorText>}

                <div className="flex gap-2">
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() => void handleTestNewConnection()}
                    disabled={connTesting || !connDraftType}
                  >
                    {connTesting ? "Testing..." : "Test Connection"}
                  </Button>
                  <Button
                    type="button"
                    onClick={() => void handleSaveNewGlobalConnection()}
                    disabled={connSaving || !connTestResult?.success || !connDraftName}
                  >
                    {connSaving ? "Saving..." : "Save"}
                  </Button>
                </div>
              </div>
            )}

            <Button
              type="button"
              variant="outline"
              className="self-start"
              onClick={() => {
                setShowPrivateSummary((s) => !s);
                if (!showPrivateSummary && privateSummary.length === 0) {
                  fetchPrivateConnectionsSummary()
                    .then(setPrivateSummary)
                    .catch((e: unknown) => setConnError(String(e)));
                }
              }}
            >
              {showPrivateSummary ? "Hide" : "Show"} other users' private connections
            </Button>
            {showPrivateSummary && (
              <>
                {privateSummary.length === 0 ? (
                  <Hint>No private connections exist yet.</Hint>
                ) : (
                  <ul className="flex flex-col gap-1">
                    {privateSummary.map((p) => (
                      <li key={`${p.user_id}-${p.name}`} className="text-[13px] text-muted-foreground">
                        {p.user_id}: {p.name} ({p.type})
                      </li>
                    ))}
                  </ul>
                )}
              </>
            )}
          </>
        )}
      </SheetContent>
    </Sheet>
  );
}
