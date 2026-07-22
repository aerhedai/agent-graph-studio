import { ChevronDown } from "lucide-react";
import { useEffect, useState } from "react";
import {
  createConnection,
  deleteConnection,
  fetchConnectionTypes,
  fetchConnections,
  testConnection,
} from "../api/client";
import type { ConnectionInfo, ConnectionTypeInfo } from "../api/types";
import { renderPrimitiveField } from "./fieldRenderers";

interface ConnectionPickerProps {
  value: string | undefined;
  onChange: (connectionName: string) => void;
}

const CATEGORY_LABELS: Record<string, string> = { local: "Local", cloud: "Cloud" };

// Picks an existing named connection, or creates a new one inline -- tabs
// generated from GET /connection-types' distinct `category` values (not
// hardcoded to "anthropic"/"ollama" by name), fields auto-rendered from
// that type's config_schema, gated behind a real "Test Connection" round-
// trip before "Save" is enabled (spec-006 §3/§6).
export function ConnectionPicker({ value, onChange }: ConnectionPickerProps) {
  const [connections, setConnections] = useState<ConnectionInfo[]>([]);
  const [connectionTypes, setConnectionTypes] = useState<ConnectionTypeInfo[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);

  const [activeCategory, setActiveCategory] = useState<string | null>(null);
  const [activeType, setActiveType] = useState<string | null>(null);
  const [draftName, setDraftName] = useState("");
  const [draftConfig, setDraftConfig] = useState<Record<string, unknown>>({});
  const [testResult, setTestResult] = useState<{ success: boolean; message: string } | null>(null);
  const [testing, setTesting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);

  function loadLists() {
    return Promise.all([fetchConnections(), fetchConnectionTypes()]).then(
      ([conns, types]) => {
        setConnections(conns);
        setConnectionTypes(types);
        if (activeCategory === null && types.length > 0) {
          setActiveCategory(types[0].category);
          setActiveType(types[0].type);
        }
      },
    );
  }

  useEffect(() => {
    loadLists().catch((e: unknown) => setLoadError(String(e)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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
      await createConnection(draftName, activeType, draftConfig);
      await loadLists();
      onChange(draftName);
      setShowForm(false);
      setDraftName("");
      setDraftConfig({});
      setTestResult(null);
    } catch (e) {
      setFormError(String(e));
    } finally {
      setSaving(false);
    }
  }

  // spec-018: replaces the curl-only DELETE /connections/{name} workaround
  // -- deletes whichever connection is currently selected in the dropdown.
  async function handleDelete() {
    if (!value) return;
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
                {c.name} ({c.type})
              </option>
            ))}
          </select>
          <ChevronDown className="select-wrap__chevron" size={14} />
        </span>
        <button type="button" className="btn btn--secondary" onClick={() => setShowForm((s) => !s)}>
          {showForm ? "Cancel" : "+ New connection"}
        </button>
        <button
          type="button"
          className="btn btn--secondary"
          onClick={() => void handleDelete()}
          disabled={!value || deleting}
          title={value ? `Delete "${value}"` : "Select a connection first"}
        >
          {deleting ? "Deleting..." : "Delete"}
        </button>
      </div>

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
