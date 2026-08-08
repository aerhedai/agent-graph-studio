import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { cn } from "@/lib/utils";
import {
  connectMcpOAuthViaPopup,
  createConnection,
  deleteConnection,
  fetchAppCatalog,
  fetchConnectionTypes,
  fetchConnections,
  getMe,
  mcpOAuthStartUrl,
  promoteConnectionToGlobal,
  setConnectionApiKey,
  testConnection,
} from "../api/client";
import type { AppCatalogEntryInfo, ConnectionInfo, ConnectionTypeInfo } from "../api/types";
import { AppCatalogGallery } from "./AppCatalogGallery";
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

function connectionOptionLabel(c: ConnectionInfo): string {
  let label = `${c.name} (${c.type})${c.is_global ? " — global" : ""}`;
  if (c.requires_oauth) label += c.oauth_connected ? " ✓ connected" : " — needs OAuth";
  if (c.auth_type !== "oauth2") label += c.api_key_connected ? " ✓ connected" : " — needs API key";
  return label;
}

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
  // spec-030: "+ New connection" opens the catalog gallery first; picking
  // a known app pre-fills the form below instead of starting blank.
  const [catalogEntries, setCatalogEntries] = useState<AppCatalogEntryInfo[]>([]);
  const [showCatalogGallery, setShowCatalogGallery] = useState(false);

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
    // spec-030: static per-deploy data, no refresh mechanism needed the way
    // node types/connections themselves now have -- fetched once, same as
    // connection *types* just above.
    fetchAppCatalog()
      .then(setCatalogEntries)
      .catch(() => setCatalogEntries([])); // non-fatal -- "Custom connection" still works with an empty gallery
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // spec-030: applies a chosen catalog entry's non-secret fields to the
  // draft config, then falls through to the exact same config form "Custom
  // connection" reaches. requires_oauth is deliberately NOT pre-filled here
  // -- confirmed live against the real create_connection route while
  // implementing this spec: pre-setting it to true at creation time skips
  // the server's own probe-then-register logic, which is what actually
  // discovers/registers OAuth for a brand-new connection. Leaving it unset
  // lets that same mechanism do its job, using whatever oauth_client_id/
  // secret the admin fills in below.
  function applyCatalogEntry(entry: AppCatalogEntryInfo) {
    setActiveCategory(entry.category);
    setActiveType("mcp_server");
    const credentialType = requiredCredentialType ?? entry.credential_type ?? undefined;
    setDraftConfig({
      transport: "remote",
      url: entry.server_url ?? "",
      auth_type: entry.auth_type,
      ...(entry.default_scope ? { oauth_scope: entry.default_scope } : {}),
      ...(credentialType ? { credential_type: credentialType } : {}),
    });
    setTestResult(null);
    setShowCatalogGallery(false);
    setShowForm(true);
  }

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
  // Bug fix: `oauth_connected` only reflects "a token was stored at some
  // point" (backend/api/app.py's _connection_info), never "that token is
  // still valid" -- a connection whose refresh token has since expired or
  // been revoked upstream still reports oauth_connected=true, so the
  // one-click Connect affordance above used to disappear exactly when it
  // was most needed, leaving delete-and-recreate-the-whole-connection as
  // the only way back in. This always offers a low-key reconnect option
  // for any OAuth-requiring connection, whether or not it currently
  // claims to be connected.
  const canReconnectOAuth = selectedConnection?.requires_oauth && selectedConnection.oauth_connected;
  const needsApiKey = selectedConnection && selectedConnection.auth_type !== "oauth2" && !selectedConnection.api_key_connected;
  const canPromote = isAdmin && !!selectedConnection && !selectedConnection.is_global && selectedConnection.can_manage;

  return (
    <div className="flex flex-col gap-2">
      {loadError && <div className="text-xs text-[var(--status-error)]">{loadError}</div>}

      <div className="flex flex-wrap gap-1.5">
        <Select value={value ?? ""} onValueChange={onChange}>
          <SelectTrigger id="field-connection" className="min-w-0 flex-1">
            {/* Radix's SelectValue only auto-derives display text from a
                SelectItem that has actually mounted (i.e. after the
                dropdown has been opened once) -- a value pre-populated
                from an existing node's saved config would otherwise show
                the placeholder instead of the real connection name.
                Explicit children sidesteps that lookup entirely. */}
            <SelectValue placeholder="Select connection...">
              {selectedConnection ? connectionOptionLabel(selectedConnection) : undefined}
            </SelectValue>
          </SelectTrigger>
          <SelectContent>
            {connections.map((c) => (
              <SelectItem key={c.name} value={c.name}>
                {connectionOptionLabel(c)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Button
          type="button"
          variant="outline"
          onClick={() => {
            if (showForm) {
              setShowForm(false);
              setShowCatalogGallery(false);
              return;
            }
            setDraftScope("private");
            setDraftConfig({});
            if (catalogEntries.length > 0) {
              // spec-030: known apps first -- "Custom connection" inside
              // the gallery reaches exactly today's original blank form.
              setShowCatalogGallery(true);
              setShowForm(true);
              return;
            }
            // Catalog failed to load or is empty -- fall straight through
            // to the original behavior rather than showing an empty gallery.
            setShowForm(true);
            // spec-025: pre-fill so a connection created from this field
            // is tagged correctly without the user needing to retype the
            // credential type slug by hand.
            if (requiredCredentialType) {
              setDraftConfig((c) => ({ ...c, credential_type: requiredCredentialType }));
            }
          }}
        >
          {showForm ? "Cancel" : "+ New connection"}
        </Button>
        <Button
          type="button"
          variant="outline"
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
        </Button>
        {canPromote && (
          <Button
            type="button"
            variant="outline"
            onClick={() => void handlePromote()}
            disabled={promoting}
            title={`Make "${value}" visible to every user`}
          >
            {promoting ? "Promoting..." : "Promote to global"}
          </Button>
        )}
      </div>

      {needsOAuthConnect && (
        // spec-021: this server requires a real per-user OAuth login before
        // its tools do anything -- a real top-level navigation to Google's/
        // the server's own consent screen, not a fetch(). Returns here
        // (via a URL fragment Canvas.tsx already parses) once complete.
        <div className="flex flex-col items-start gap-2 rounded-[var(--radius-sm)] bg-[color-mix(in_srgb,var(--status-error)_15%,transparent)] p-2 text-xs text-[var(--status-error)]">
          <span>"{value}" needs to be connected before its tools will work.</span>
          <div className="flex gap-1.5">
            <Button type="button" onClick={() => void handleConnectViaPopup()} disabled={poppingUp}>
              {poppingUp ? "Connecting..." : "Connect"}
            </Button>
            <Button type="button" variant="outline" asChild>
              <a href={mcpOAuthStartUrl(value ?? "", window.location.origin + window.location.pathname)}>
                Connect (full page)
              </a>
            </Button>
          </div>
          {popupError && <div className="text-[var(--status-error)]">{popupError}</div>}
        </div>
      )}

      {canReconnectOAuth && (
        // A stored token can go stale upstream (expired/revoked at the
        // provider) without this app finding out until something actually
        // tries to use it -- this stays available even while the
        // connection still reports "connected", so fixing it never
        // requires deleting and recreating the whole connection.
        <div className="flex flex-wrap items-center gap-2 text-[13px] text-muted-foreground">
          <span>Tools not working for "{value}"?</span>
          <Button type="button" variant="outline" size="sm" onClick={() => void handleConnectViaPopup()} disabled={poppingUp}>
            {poppingUp ? "Reconnecting..." : "Reconnect"}
          </Button>
          {popupError && <span className="text-[var(--status-error)]">{popupError}</span>}
        </div>
      )}

      {needsApiKey && (
        // spec-025: the api_key/bearer counterpart of the OAuth "Connect"
        // block above -- no redirect, the caller pastes a key they
        // already generated (from the app's own developer/API settings)
        // and submits it directly.
        <div className="flex flex-col items-start gap-2 rounded-[var(--radius-sm)] bg-[color-mix(in_srgb,var(--status-error)_15%,transparent)] p-2 text-xs text-[var(--status-error)]">
          <span>"{value}" needs your personal API key/bearer token before its tools will work.</span>
          <Input
            type="password"
            value={apiKeyDraft}
            onChange={(e) => setApiKeyDraft(e.target.value)}
            placeholder="Paste your API key"
          />
          <Button
            type="button"
            onClick={() => void handleSetApiKey()}
            disabled={settingApiKey || !apiKeyDraft.trim()}
          >
            {settingApiKey ? "Saving..." : "Connect"}
          </Button>
          {apiKeyError && <div className="text-[var(--status-error)]">{apiKeyError}</div>}
        </div>
      )}

      {showForm && showCatalogGallery && (
        <div className="flex flex-col gap-2 rounded-[var(--radius-md)] border border-border bg-background p-2.5">
          <AppCatalogGallery
            entries={catalogEntries}
            onSelect={applyCatalogEntry}
            onCustom={() => {
              setShowCatalogGallery(false);
              if (requiredCredentialType) {
                setDraftConfig((c) => ({ ...c, credential_type: requiredCredentialType }));
              }
            }}
          />
        </div>
      )}

      {showForm && !showCatalogGallery && (
        <div className="flex flex-col gap-2 rounded-[var(--radius-md)] border border-border bg-background p-2.5">
          <div className="flex gap-1 border-b border-border">
            {categories.map((category) => (
              <button
                key={category}
                type="button"
                className={cn(
                  "cursor-pointer border-none border-b-2 border-b-transparent bg-transparent px-2.5 py-1 text-xs text-foreground opacity-60",
                  activeCategory === category && "font-semibold border-b-primary opacity-100",
                )}
                onClick={() => selectCategory(category)}
              >
                {CATEGORY_LABELS[category] ?? category}
              </button>
            ))}
          </div>

          {typesInActiveCategory.length > 1 && (
            <Select
              value={activeType ?? ""}
              onValueChange={(v) => {
                setActiveType(v);
                setDraftConfig({});
                setTestResult(null);
              }}
            >
              <SelectTrigger className="w-full">
                <SelectValue>{activeType || undefined}</SelectValue>
              </SelectTrigger>
              <SelectContent>
                {typesInActiveCategory.map((t) => (
                  <SelectItem key={t.type} value={t.type}>
                    {t.type}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}

          <div className="flex flex-col gap-1">
            <Label htmlFor="connection-draft-name">Connection name</Label>
            <Input
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
            <div className="flex flex-col gap-1">
              <Label htmlFor="connection-draft-scope">Visibility</Label>
              <Select value={draftScope} onValueChange={(v) => setDraftScope(v as "private" | "global")}>
                <SelectTrigger id="connection-draft-scope" className="w-full">
                  <SelectValue>{draftScope === "private" ? "Private -- only me" : "Global -- every user"}</SelectValue>
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="private">Private -- only me</SelectItem>
                  <SelectItem value="global">Global -- every user</SelectItem>
                </SelectContent>
              </Select>
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
                  <div key={name} className="flex flex-col gap-1">
                    <Label htmlFor={`field-${name}`}>
                      {propSchema.title ?? name}
                      {!requiredFields.has(name) && (
                        <span className="ml-1 text-[10px] font-normal text-muted-foreground italic">optional</span>
                      )}
                    </Label>
                    {renderPrimitiveField(name, propSchema, draftConfig[name], setDraftField)}
                  </div>
                ),
              );
            })()}

          {testResult && (
            <div
              className={cn(
                "rounded-[var(--radius-sm)] px-2 py-1.5 text-xs",
                testResult.success
                  ? "bg-[color-mix(in_srgb,var(--status-success)_15%,transparent)] text-[var(--status-success)]"
                  : "bg-[color-mix(in_srgb,var(--status-error)_15%,transparent)] text-[var(--status-error)]",
              )}
            >
              {testResult.message}
            </div>
          )}
          {formError && <div className="text-xs text-[var(--status-error)]">{formError}</div>}

          <div className="flex gap-2">
            <Button type="button" variant="outline" onClick={() => void handleTest()} disabled={testing || !activeType}>
              {testing ? "Testing..." : "Test Connection"}
            </Button>
            <Button
              type="button"
              onClick={() => void handleSaveNewConnection()}
              disabled={saving || !testResult?.success || !draftName}
            >
              {saving ? "Saving..." : "Save"}
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
