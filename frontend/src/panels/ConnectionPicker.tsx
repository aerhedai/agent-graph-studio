import { ChevronDown } from "lucide-react";
import { useEffect, useState } from "react";
import {
  connectMcpOAuthViaPopup,
  createConnection,
  deleteConnection,
  fetchConnectionTypes,
  fetchConnections,
  getMe,
  mcpOAuthStartUrl,
  promoteConnectionToGlobal,
  setConnectionApiKey,
  testConnection,
} from "../api/client";
import type { ConnectionInfo, ConnectionTypeInfo } from "../api/types";
import { renderPrimitiveField } from "./fieldRenderers";

interface ConnectionPickerProps {
  value: string | undefined;
  onChange: (connectionName: string) => void;
  // spec-fix: restricts which connection types this picker shows/creates,
  // derived from the field's own json_schema_extra (see JsonSchemaProperty).
  // Undefined/empty means unrestricted -- the pre-fix behavior, kept as the
  // fallback so a field with no filter metadata still works.
  allowedTypes?: string[];
  requiredCapability?: keyof ConnectionTypeInfo;
  // spec-025: unlike allowedTypes/requiredCapability (which filter which
  // connection *types* are allowed at all), this filters individual
  // *connections* by their own tagged credential_type -- e.g. only "Work
  // Gmail"/"Personal Gmail" out of every mcp_server connection the caller
  // has, when a field declares it needs "google_gmail_oauth2" specifically.
  requiredCredentialType?: string;
}

const CATEGORY_LABELS: Record<string, string> = { local: "Local", cloud: "Cloud" };

// Picks an existing named connection, or creates a new one inline -- tabs
// generated from GET /connection-types' distinct `category` values (not
// hardcoded to "anthropic"/"ollama" by name), fields auto-rendered from
// that type's config_schema, gated behind a real "Test Connection" round-
// trip before "Save" is enabled (spec-006 §3/§6).
export function ConnectionPicker({
  value,
  onChange,
  allowedTypes,
  requiredCapability,
  requiredCredentialType,
}: ConnectionPickerProps) {
  const [allConnections, setAllConnections] = useState<ConnectionInfo[]>([]);
  const [allConnectionTypes, setAllConnectionTypes] = useState<ConnectionTypeInfo[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);

  const [activeCategory, setActiveCategory] = useState<string | null>(null);
  const [activeType, setActiveType] = useState<string | null>(null);
  const [draftName, setDraftName] = useState("");
  const [draftConfig, setDraftConfig] = useState<Record<string, unknown>>({});
  const [draftScope, setDraftScope] = useState<"private" | "global">("private");
  const [testResult, setTestResult] = useState<{ success: boolean; message: string } | null>(null);
  const [testing, setTesting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [promoting, setPromoting] = useState(false);
  const [apiKeyDraft, setApiKeyDraft] = useState("");
  const [settingApiKey, setSettingApiKey] = useState(false);
  const [apiKeyError, setApiKeyError] = useState<string | null>(null);
  // spec-023: only an admin can create/edit/delete a global connection --
  // the picker needs to know the caller's own role to show that choice at
  // all, same getMe() SettingsPanel.tsx already fetches for its own
  // admin-only "Invite a user" section.
  const [isAdmin, setIsAdmin] = useState(false);

  function isTypeAllowed(t: ConnectionTypeInfo): boolean {
    if (allowedTypes && allowedTypes.length > 0) return allowedTypes.includes(t.type);
    if (requiredCapability) return t[requiredCapability] === true;
    return true;
  }

  // Every list/tab/dropdown below is derived from these two, never from
  // the unfiltered fetch results directly -- so "+ New connection", the
  // category tabs, the existing-connection dropdown, and Delete can only
  // ever touch a type-appropriate connection for whichever field this
  // picker instance renders.
  const connectionTypes = allConnectionTypes.filter(isTypeAllowed);
  const connections = allConnections.filter(
    (c) =>
      connectionTypes.some((t) => t.type === c.type) &&
      (!requiredCredentialType || c.credential_type === requiredCredentialType),
  );

  function loadLists() {
    return Promise.all([fetchConnections(), fetchConnectionTypes()]).then(
      ([conns, types]) => {
        setAllConnections(conns);
        setAllConnectionTypes(types);
      },
    );
  }

  useEffect(() => {
    loadLists().catch((e: unknown) => setLoadError(String(e)));
    getMe()
      .then((me) => setIsAdmin(me.role === "admin"))
      .catch(() => setIsAdmin(false)); // a shared-API-key caller has no `me` -- treated as non-admin here (UI only; server-side it's unrestricted, see _require_admin's own docstring)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (activeCategory !== null) return;
    if (connectionTypes.length === 0) return;
    setActiveCategory(connectionTypes[0].category);
    setActiveType(connectionTypes[0].type);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [connectionTypes.length]);

  const categories = Array.from(new Set(connectionTypes.map((t) => t.category)));
  const typesInActiveCategory = connectionTypes.filter((t) => t.category === activeCategory);
  const activeTypeInfo = connectionTypes.find((t) => t.type === activeType);

  function selectCategory(category: string) {
    setActiveCategory(category);
    const firstOfCategory = connectionTypes.find((t) => t.category === category);
    setActiveType(firstOfCategory?.type ?? null);
    setDraftConfig({});
    setTestResult(null);
  }

  function setDraftField(name: string, fieldValue: unknown) {
    setDraftConfig((c) => ({ ...c, [name]: fieldValue }));
    setTestResult(null); // any edit invalidates the last test result
  }

  async function handleTest() {
    if (!activeType) return;
    setTesting(true);
    setFormError(null);
    setTestResult(null);
    try {
      const result = await testConnection(draftName || "draft", {
        type: activeType,
        config: draftConfig,
      });
      setTestResult(result);
    } catch (e) {
      setFormError(String(e));
    } finally {
      setTesting(false);
    }
  }

  async function handleSaveNewConnection() {
    if (!activeType || !draftName) return;
    setSaving(true);
    setFormError(null);
    try {
      await createConnection(draftName, activeType, draftConfig, draftScope);
      await loadLists();
      onChange(draftName);
      setShowForm(false);
      setDraftName("");
      setDraftConfig({});
      setDraftScope("private");
      setTestResult(null);
    } catch (e) {
      setFormError(String(e));
    } finally {
      setSaving(false);
    }
  }

  // spec-018: replaces the curl-only DELETE /connections/{name} workaround
  // -- deletes whichever connection is currently selected in the dropdown.
  // spec-023: can_manage is the real (server-computed) source of truth --
  // this guard plus the button's own `disabled` below are both UI-side
  // reflections of it, not the enforcement itself (that's the 403 on the
  // server).
  async function handleDelete() {
    if (!value || !selectedConnection?.can_manage) return;
    if (!window.confirm(`Delete connection "${value}"? This can't be undone.`)) return;
    setDeleting(true);
    setLoadError(null);
    try {
      await deleteConnection(value);
      onChange("");
      await loadLists();
    } catch (e) {
      setLoadError(String(e));
    } finally {
      setDeleting(false);
    }
  }

  // spec-023: closes the exact "created before this spec existed, no other
  // way to make it visible to every user" situation -- an admin turning
  // their own private connection into a global one in place.
  async function handlePromote() {
    if (!value) return;
    if (!window.confirm(`Make "${value}" a global connection, visible to every user?`)) return;
    setPromoting(true);
    setLoadError(null);
    try {
      await promoteConnectionToGlobal(value);
      await loadLists();
    } catch (e) {
      setLoadError(String(e));
    } finally {
      setPromoting(false);
    }
  }

  // spec-025: the api_key/bearer counterpart of handlePromote's own
  // "paste in hand, no redirect" shape -- unlike OAuth, there's no
  // provider consent screen to send the user to.
  async function handleSetApiKey() {
    if (!value || !apiKeyDraft.trim()) return;
    setSettingApiKey(true);
    setApiKeyError(null);
    try {
      await setConnectionApiKey(value, apiKeyDraft.trim());
      setApiKeyDraft("");
      await loadLists();
    } catch (e) {
      setApiKeyError(String(e));
    } finally {
      setSettingApiKey(false);
    }
  }

  // spec-025: popup-based OAuth connect -- doesn't navigate the canvas away
  // at all; the plain <a href> below stays as the fallback for a blocked
  // popup (some browsers/embedded contexts disallow window.open).
  const [poppingUp, setPoppingUp] = useState(false);
  const [popupError, setPopupError] = useState<string | null>(null);

  async function handleConnectViaPopup() {
    if (!value) return;
    setPoppingUp(true);
    setPopupError(null);
    try {
      const result = await connectMcpOAuthViaPopup(value);
      if (result.error) {
        setPopupError(result.error);
      } else {
        await loadLists();
      }
    } catch (e) {
      setPopupError(String(e));
    } finally {
      setPoppingUp(false);
    }
  }

  const selectedConnection = connections.find((c) => c.name === value);
  const needsOAuthConnect = selectedConnection?.requires_oauth && !selectedConnection.oauth_connected;
  const needsApiKey = selectedConnection && selectedConnection.auth_type !== "oauth2" && !selectedConnection.api_key_connected;
  const canPromote = isAdmin && !!selectedConnection && !selectedConnection.is_global && selectedConnection.can_manage;

  return (
    <div className="connection-picker">
      {loadError && <div className="config-panel__error">{loadError}</div>}

      <div className="connection-picker__row">
        <span className="select-wrap">
          <select
            id="field-connection"
            value={value ?? ""}
            onChange={(e) => onChange(e.target.value)}
          >
            <option value="" disabled>
              Select connection...
            </option>
            {connections.map((c) => (
              <option key={c.name} value={c.name}>
                {c.name} ({c.type}){c.is_global ? " — global" : ""}
                {c.requires_oauth ? (c.oauth_connected ? " ✓ connected" : " — needs OAuth") : ""}
                {c.auth_type !== "oauth2" ? (c.api_key_connected ? " ✓ connected" : " — needs API key") : ""}
              </option>
            ))}
          </select>
          <ChevronDown className="select-wrap__chevron" size={14} />
        </span>
        <button
          type="button"
          className="btn btn--secondary"
          onClick={() => {
            setShowForm((s) => !s);
            setDraftScope("private");
            // spec-025: pre-fill so a connection created from this field
            // is tagged correctly without the user needing to retype the
            // credential type slug by hand.
            if (requiredCredentialType) {
              setDraftConfig((c) => ({ ...c, credential_type: requiredCredentialType }));
            }
          }}
        >
          {showForm ? "Cancel" : "+ New connection"}
        </button>
        <button
          type="button"
          className="btn btn--secondary"
          onClick={() => void handleDelete()}
          disabled={!value || deleting || !selectedConnection?.can_manage}
          title={
            !value
              ? "Select a connection first"
              : selectedConnection?.can_manage
                ? `Delete "${value}"`
                : "Only an admin can delete a global connection"
          }
        >
          {deleting ? "Deleting..." : "Delete"}
        </button>
        {canPromote && (
          <button
            type="button"
            className="btn btn--secondary"
            onClick={() => void handlePromote()}
            disabled={promoting}
            title={`Make "${value}" visible to every user`}
          >
            {promoting ? "Promoting..." : "Promote to global"}
          </button>
        )}
      </div>

      {needsOAuthConnect && (
        // spec-021: this server requires a real per-user OAuth login before
        // its tools do anything -- a real top-level navigation to Google's/
        // the server's own consent screen, not a fetch(). Returns here
        // (via a URL fragment Canvas.tsx already parses) once complete.
        <div className="connection-picker__test-result failure">
          <span>"{value}" needs to be connected before its tools will work.</span>
          <button type="button" className="btn btn--primary" onClick={() => void handleConnectViaPopup()} disabled={poppingUp}>
            {poppingUp ? "Connecting..." : "Connect"}
          </button>
          <a
            className="btn btn--secondary"
            href={mcpOAuthStartUrl(value ?? "", window.location.origin + window.location.pathname)}
          >
            Connect (full page)
          </a>
          {popupError && <div className="config-panel__error">{popupError}</div>}
        </div>
      )}

      {needsApiKey && (
        // spec-025: the api_key/bearer counterpart of the OAuth "Connect"
        // block above -- no redirect, the caller pastes a key they
        // already generated (from the app's own developer/API settings)
        // and submits it directly.
        <div className="connection-picker__test-result failure">
          <span>"{value}" needs your personal API key/bearer token before its tools will work.</span>
          <input
            type="password"
            value={apiKeyDraft}
            onChange={(e) => setApiKeyDraft(e.target.value)}
            placeholder="Paste your API key"
          />
          <button
            type="button"
            className="btn btn--primary"
            onClick={() => void handleSetApiKey()}
            disabled={settingApiKey || !apiKeyDraft.trim()}
          >
            {settingApiKey ? "Saving..." : "Connect"}
          </button>
          {apiKeyError && <div className="config-panel__error">{apiKeyError}</div>}
        </div>
      )}

      {showForm && (
        <div className="connection-picker__form">
          <div className="connection-picker__tabs">
            {categories.map((category) => (
              <button
                key={category}
                type="button"
                className={`connection-picker__tab ${activeCategory === category ? "active" : ""}`}
                onClick={() => selectCategory(category)}
              >
                {CATEGORY_LABELS[category] ?? category}
              </button>
            ))}
          </div>

          {typesInActiveCategory.length > 1 && (
            <span className="select-wrap">
              <select
                value={activeType ?? ""}
                onChange={(e) => {
                  setActiveType(e.target.value);
                  setDraftConfig({});
                  setTestResult(null);
                }}
              >
                {typesInActiveCategory.map((t) => (
                  <option key={t.type} value={t.type}>
                    {t.type}
                  </option>
                ))}
              </select>
              <ChevronDown className="select-wrap__chevron" size={14} />
            </span>
          )}

          <div className="config-panel__field">
            <label htmlFor="connection-draft-name">Connection name</label>
            <input
              id="connection-draft-name"
              type="text"
              value={draftName}
              onChange={(e) => setDraftName(e.target.value)}
              placeholder="e.g. my-pc-ollama"
            />
          </div>

          {isAdmin && (
            // spec-023: a non-admin never sees this choice at all -- their
            // connections are always private, exactly the pre-spec-023
            // behavior. Global is admin-only, both here and enforced
            // server-side.
            <div className="config-panel__field">
              <label htmlFor="connection-draft-scope">Visibility</label>
              <span className="select-wrap">
                <select
                  id="connection-draft-scope"
                  value={draftScope}
                  onChange={(e) => setDraftScope(e.target.value as "private" | "global")}
                >
                  <option value="private">Private -- only me</option>
                  <option value="global">Global -- every user</option>
                </select>
                <ChevronDown className="select-wrap__chevron" size={14} />
              </span>
            </div>
          )}

          {activeTypeInfo &&
            (() => {
              // spec-014: same "read the schema's own required array"
              // treatment as ConfigPanel's node-config fields, applied here
              // to a connection type's own config_schema.
              const requiredFields = new Set(activeTypeInfo.config_schema.required ?? []);
              return Object.entries(activeTypeInfo.config_schema.properties ?? {}).map(
                ([name, propSchema]) => (
                  <div key={name} className="config-panel__field">
                    <label htmlFor={`field-${name}`}>
                      {propSchema.title ?? name}
                      {!requiredFields.has(name) && (
                        <span className="config-panel__optional-tag">optional</span>
                      )}
                    </label>
                    {renderPrimitiveField(name, propSchema, draftConfig[name], setDraftField)}
                  </div>
                ),
              );
            })()}

          {testResult && (
            <div
              className={`connection-picker__test-result ${testResult.success ? "success" : "failure"}`}
            >
              {testResult.message}
            </div>
          )}
          {formError && <div className="config-panel__error">{formError}</div>}

          <div className="connection-picker__form-actions">
            <button
              type="button"
              className="btn btn--secondary"
              onClick={() => void handleTest()}
              disabled={testing || !activeType}
            >
              {testing ? "Testing..." : "Test Connection"}
            </button>
            <button
              type="button"
              className="btn btn--primary"
              onClick={() => void handleSaveNewConnection()}
              disabled={saving || !testResult?.success || !draftName}
            >
              {saving ? "Saving..." : "Save"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
