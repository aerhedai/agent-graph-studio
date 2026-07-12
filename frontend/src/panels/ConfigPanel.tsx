import { python } from "@codemirror/lang-python";
import CodeMirror from "@uiw/react-codemirror";
import { useEffect, useState } from "react";
import { resolveSlots } from "../api/client";
import type { JsonSchemaProperty, SlotInfo } from "../api/types";
import type { GenericFlowNode } from "../canvas/GenericNode";

interface ConfigPanelProps {
  node: GenericFlowNode | null;
  onConfigChange: (
    nodeId: string,
    config: Record<string, unknown>,
    inputs: SlotInfo[],
    outputs: SlotInfo[],
  ) => void;
}

// Auto-generated from config_schema (the same Pydantic model the backend
// validates against -- ADR-001's one-schema payoff, spec-005 §4) by default.
// `function_source` is the one deliberate special case (spec-005 §7): a real
// multi-line editor instead of a generic single-line input, since that's a
// foreseeable UX problem worth solving directly.
export function ConfigPanel({ node, onConfigChange }: ConfigPanelProps) {
  const [draft, setDraft] = useState<Record<string, unknown>>({});
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    setDraft(node?.data.config ?? {});
    setError(null);
  }, [node?.id]);

  if (!node) {
    return (
      <aside className="config-panel config-panel--empty">
        <p>Select a node to edit its configuration.</p>
      </aside>
    );
  }

  const properties = node.data.configSchema.properties ?? {};

  function setField(name: string, value: unknown) {
    setDraft((d) => ({ ...d, [name]: value }));
  }

  async function handleSave() {
    if (!node) return;
    setSaving(true);
    setError(null);
    try {
      if (node.data.dynamicSchema) {
        // Re-resolve ports for this instance's new config (SPEC-002's
        // resolve_slots, over HTTP) -- e.g. a code node's params change
        // when function_source changes. Only on save/blur, not per
        // keystroke: mcp_call's resolution spawns a real subprocess.
        const resolved = await resolveSlots(node.data.nodeType, draft);
        onConfigChange(node.id, draft, resolved.inputs, resolved.outputs);
      } else {
        onConfigChange(node.id, draft, node.data.inputs, node.data.outputs);
      }
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <aside className="config-panel">
      <h2>{node.data.nodeType}</h2>
      <p className="config-panel__id">{node.id}</p>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void handleSave();
        }}
      >
        {Object.entries(properties).map(([name, propSchema]) => (
          <div key={name} className="config-panel__field">
            <label htmlFor={`field-${name}`}>{propSchema.title ?? name}</label>
            {renderField(name, propSchema, draft[name], setField)}
          </div>
        ))}
        {error && <div className="config-panel__error">{error}</div>}
        <button type="submit" disabled={saving}>
          {saving ? "Resolving..." : "Save"}
        </button>
      </form>
    </aside>
  );
}

function renderField(
  name: string,
  propSchema: JsonSchemaProperty,
  value: unknown,
  setField: (name: string, value: unknown) => void,
) {
  if (name === "function_source") {
    return (
      <CodeMirror
        value={typeof value === "string" ? value : ""}
        height="200px"
        extensions={[python()]}
        onChange={(v) => setField(name, v)}
      />
    );
  }

  if (propSchema.type === "boolean") {
    return (
      <input
        id={`field-${name}`}
        type="checkbox"
        checked={Boolean(value)}
        onChange={(e) => setField(name, e.target.checked)}
      />
    );
  }

  if (propSchema.type === "integer" || propSchema.type === "number") {
    return (
      <input
        id={`field-${name}`}
        type="number"
        value={typeof value === "number" ? value : ""}
        onChange={(e) =>
          setField(name, e.target.value === "" ? undefined : Number(e.target.value))
        }
      />
    );
  }

  if (propSchema.type === "string") {
    return (
      <input
        id={`field-${name}`}
        type="text"
        value={typeof value === "string" ? value : ""}
        onChange={(e) => setField(name, e.target.value)}
      />
    );
  }

  // object/array/$ref (e.g. loop's nested sub_graph, llm_call's
  // provider_options) -- raw JSON fallback. A proper nested editor for a
  // whole sub-graph is explicitly canvas-later scope (spec-005 §3: loop
  // sub-graphs shown as a single node with a config panel for MVP), and a
  // flattened/raw view is this project's established MVP convention
  // elsewhere (spec-005 §7 for nested trace display).
  const textValue =
    value === undefined ? "" : typeof value === "string" ? value : JSON.stringify(value, null, 2);
  return (
    <textarea
      id={`field-${name}`}
      className="config-panel__json-field"
      defaultValue={textValue}
      onBlur={(e) => {
        try {
          setField(name, JSON.parse(e.target.value));
        } catch {
          // leave prior value in place until the text is valid JSON again
        }
      }}
    />
  );
}
