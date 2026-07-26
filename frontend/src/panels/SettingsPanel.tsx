import { ChevronDown } from "lucide-react";
import { useEffect, useState } from "react";
import {
  clearApiKey,
  createConnection,
  deleteConnection,
  fetchConnectionTypes,
  fetchConnections,
  fetchPrivateConnectionsSummary,
  getMe,
  getSettings,
  inviteUser,
  mcpOAuthStartUrl,
  testConnection,
  updateConnection,
  updateSettings,
} from "../api/client";
import type { ConnectionInfo, ConnectionTypeInfo, MeResponse, PrivateConnectionSummary } from "../api/types";
import { renderPrimitiveField } from "./fieldRenderers";

interface SettingsPanelProps {
  onClose: () => void;
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
    <div className="history-panel-overlay" onClick={onClose}>
      <aside className="history-panel" onClick={(e) => e.stopPropagation()}>
        <div className="history-panel__header">
          <h2>Settings</h2>
          <button type="button" className="run-bar__secondary" onClick={onClose}>
            Close
          </button>
        </div>

        <div className="config-panel__field">
          <label htmlFor="public-base-url">Public base URL</label>
          <input
            id="public-base-url"
            type="text"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="https://your-tunnel-or-domain.example.com"
          />
        </div>
        <p className="history-panel__empty">
          Used to auto-register external webhooks (e.g. Telegram's <code>setWebhook</code>) when
          you Activate a graph that needs one -- this is wherever this backend is actually
          reachable from the outside (a Tailscale Funnel/ngrok URL, or your real domain once
          deployed).
        </p>

        <button type="button" onClick={() => void handleSave()} disabled={saving}>
          {saving ? "Saving..." : "Save"}
        </button>

        {saved && <p className="history-panel__empty">Currently set to: {saved}</p>}
        {warning && <div className="run-bar__error">{warning}</div>}
        {error && <div className="run-bar__error">{error}</div>}

        {me && (
          <>
            <div className="history-panel__header">
              <h2>Account</h2>
            </div>
            <p className="history-panel__empty">
              Signed in as {me.display_name} ({me.email}) -- {me.role}
            </p>
            <button type="button" className="run-bar__secondary" onClick={handleSignOut}>
              Sign out
            </button>
          </>
        )}

        {me?.role === "admin" && (
          <>
            <div className="history-panel__header">
              <h2>Invite a user</h2>
            </div>
            <div className="config-panel__field">
              <label htmlFor="invite-email">Email</label>
              <input
                id="invite-email"
                type="email"
                value={inviteEmail}
                onChange={(e) => setInviteEmail(e.target.value)}
                placeholder="teammate@example.com"
              />
            </div>
            <button type="button" onClick={() => void handleInvite()} disabled={inviting}>
              {inviting ? "Inviting..." : "Invite"}
            </button>
            {inviteStatus && <p className="history-panel__empty">{inviteStatus}</p>}
            {inviteError && <div className="run-bar__error">{inviteError}</div>}
          </>
        )}

        {me?.role === "admin" && (
          <>
            <div className="history-panel__header">
              <h2>Connections</h2>
            </div>
            <p className="history-panel__empty">
              Global connections are visible to every user (e.g. a shared Gmail/Discord app they can each
              connect their own account to) and are only manageable here, by an admin. A user's own private
              connections stay self-service, unaffected -- this section never shows their config or secrets.
            </p>
            {connError && <div className="run-bar__error">{connError}</div>}

            {globalConnections.length === 0 && !showConnForm && (
              <p className="history-panel__empty">No global connections yet.</p>
            )}
            {globalConnections.map((c) => (
              <div key={c.name} className="config-panel__field">
                <label>
                  {c.name} ({c.type})
                  {c.requires_oauth ? (c.oauth_connected ? " ✓ connected" : " — needs OAuth") : ""}
                </label>
                {c.requires_oauth && !c.oauth_connected && (
                  // Was missing entirely -- this section had no OAuth connect
                  // affordance at all, unlike ConnectionPicker.tsx's own
                  // equivalent "Connect" link. Same real top-level navigation
                  // (not a fetch), same fragment-based return Canvas.tsx
                  // already parses.
                  <a
                    className="btn btn--primary"
                    href={mcpOAuthStartUrl(c.name, window.location.origin + window.location.pathname)}
                  >
                    Connect
                  </a>
                )}
                {editingConn === c.name ? (
                  <>
                    <p className="history-panel__empty">
                      Config isn't shown back for security -- re-enter every field to save changes.
                    </p>
                    {(() => {
                      const typeInfo = connectionTypes.find((t) => t.type === c.type);
                      const requiredFields = new Set(typeInfo?.config_schema.required ?? []);
                      return Object.entries(typeInfo?.config_schema.properties ?? {}).map(([name, propSchema]) => (
                        <div key={name} className="config-panel__field">
                          <label htmlFor={`edit-${c.name}-${name}`}>
                            {propSchema.title ?? name}
                            {!requiredFields.has(name) && (
                              <span className="config-panel__optional-tag">optional</span>
                            )}
                          </label>
                          {renderPrimitiveField(name, propSchema, editDraftConfig[name], (n, v) =>
                            setEditDraftConfig((cfg) => ({ ...cfg, [n]: v })),
                          )}
                        </div>
                      ));
                    })()}
                    <button
                      type="button"
                      onClick={() => void handleSaveEditedConnection(c.name)}
                      disabled={editSaving}
                    >
                      {editSaving ? "Saving..." : "Save"}
                    </button>
                    <button type="button" className="run-bar__secondary" onClick={() => setEditingConn(null)}>
                      Cancel
                    </button>
                  </>
                ) : (
                  <div className="connection-picker__row">
                    <button type="button" className="btn btn--secondary" onClick={() => startEditingConnection(c)}>
                      Edit
                    </button>
                    <button
                      type="button"
                      className="btn btn--secondary"
                      onClick={() => void handleDeleteGlobalConnection(c.name)}
                      disabled={deletingConn === c.name}
                    >
                      {deletingConn === c.name ? "Deleting..." : "Delete"}
                    </button>
                  </div>
                )}
              </div>
            ))}

            <button type="button" className="run-bar__secondary" onClick={() => setShowConnForm((s) => !s)}>
              {showConnForm ? "Cancel" : "+ New global connection"}
            </button>

            {showConnForm && (
              <div className="connection-picker__form">
                <div className="config-panel__field">
                  <label htmlFor="new-global-conn-name">Connection name</label>
                  <input
                    id="new-global-conn-name"
                    type="text"
                    value={connDraftName}
                    onChange={(e) => setConnDraftName(e.target.value)}
                    placeholder="e.g. shared-gmail"
                  />
                </div>
                <div className="config-panel__field">
                  <label htmlFor="new-global-conn-type">Type</label>
                  <span className="select-wrap">
                    <select
                      id="new-global-conn-type"
                      value={connDraftType ?? ""}
                      onChange={(e) => {
                        setConnDraftType(e.target.value || null);
                        setConnDraftConfig({});
                        setConnTestResult(null);
                      }}
                    >
                      <option value="" disabled>
                        Select type...
                      </option>
                      {connectionTypes.map((t) => (
                        <option key={t.type} value={t.type}>
                          {t.type}
                        </option>
                      ))}
                    </select>
                    <ChevronDown className="select-wrap__chevron" size={14} />
                  </span>
                </div>

                {connDraftTypeInfo &&
                  (() => {
                    const requiredFields = new Set(connDraftTypeInfo.config_schema.required ?? []);
                    return Object.entries(connDraftTypeInfo.config_schema.properties ?? {}).map(
                      ([name, propSchema]) => (
                        <div key={name} className="config-panel__field">
                          <label htmlFor={`new-global-conn-${name}`}>
                            {propSchema.title ?? name}
                            {!requiredFields.has(name) && (
                              <span className="config-panel__optional-tag">optional</span>
                            )}
                          </label>
                          {renderPrimitiveField(name, propSchema, connDraftConfig[name], (n, v) =>
                            setConnDraftConfig((cfg) => ({ ...cfg, [n]: v })),
                          )}
                        </div>
                      ),
                    );
                  })()}

                {connTestResult && (
                  <div
                    className={`connection-picker__test-result ${connTestResult.success ? "success" : "failure"}`}
                  >
                    {connTestResult.message}
                  </div>
                )}
                {connFormError && <div className="config-panel__error">{connFormError}</div>}

                <div className="connection-picker__form-actions">
                  <button
                    type="button"
                    className="btn btn--secondary"
                    onClick={() => void handleTestNewConnection()}
                    disabled={connTesting || !connDraftType}
                  >
                    {connTesting ? "Testing..." : "Test Connection"}
                  </button>
                  <button
                    type="button"
                    className="btn btn--primary"
                    onClick={() => void handleSaveNewGlobalConnection()}
                    disabled={connSaving || !connTestResult?.success || !connDraftName}
                  >
                    {connSaving ? "Saving..." : "Save"}
                  </button>
                </div>
              </div>
            )}

            <button
              type="button"
              className="run-bar__secondary"
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
            </button>
            {showPrivateSummary && (
              <>
                {privateSummary.length === 0 ? (
                  <p className="history-panel__empty">No private connections exist yet.</p>
                ) : (
                  <ul>
                    {privateSummary.map((p) => (
                      <li key={`${p.user_id}-${p.name}`} className="history-panel__empty">
                        {p.user_id}: {p.name} ({p.type})
                      </li>
                    ))}
                  </ul>
                )}
              </>
            )}
          </>
        )}
      </aside>
    </div>
  );
}
