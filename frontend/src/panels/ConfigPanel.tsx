import { python } from "@codemirror/lang-python";
import CodeMirror from "@uiw/react-codemirror";
import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { resolveNodeTypeOptions, resolveSlots } from "../api/client";
import type { JsonSchemaProperty, SlotInfo } from "../api/types";
import { displayName } from "../canvas/GenericNode";
import type { GenericFlowNode } from "../canvas/GenericNode";
import { ConnectionPicker } from "./ConnectionPicker";
import { renderPrimitiveField } from "./fieldRenderers";
import { ModelField } from "./ModelField";

interface ConfigPanelProps {
  node: GenericFlowNode;
  onConfigChange: (
    nodeId: string,
    config: Record<string, unknown>,
    inputs: SlotInfo[],
    outputs: SlotInfo[],
    inputValues: Record<string, unknown>,
  ) => void;
  connectedSubNodes: { slot: string; node: GenericFlowNode }[];
  // spec-025: names of this node's own data input slots that currently
  // have an incoming edge -- only slots NOT in this set get a literal-value
  // field below (an edge always wins if both exist for the same slot).
  wiredInputSlotNames: Set<string>;
}

// A config field absent from its schema's own `required` array gets the
// same muted "optional" tag treatment as an optional data port -- one
// visual language for "you don't have to fill this in," reused across
// ports and form fields.
function OptionalTag() {
  return <span className="ml-1 text-[10px] font-normal text-muted-foreground italic">optional</span>;
}

// Auto-generated from config_schema (the same Pydantic model the backend
// validates against -- ADR-001's one-schema payoff, spec-005 §4) by default.
// `function_source` is the one deliberate special case (spec-005 §7): a real
// multi-line editor instead of a generic single-line input, since that's a
// foreseeable UX problem worth solving directly.
export function ConfigPanel({ node, onConfigChange, connectedSubNodes, wiredInputSlotNames }: ConfigPanelProps) {
  const [draft, setDraft] = useState<Record<string, unknown>>({});
  const [inputValuesDraft, setInputValuesDraft] = useState<Record<string, unknown>>({});
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    setDraft(node.data.config ?? {});
    setInputValuesDraft(node.data.inputValues ?? {});
    setError(null);
  }, [node.id]);

  function setInputValue(slotName: string, value: unknown) {
    setInputValuesDraft((v) => ({ ...v, [slotName]: value }));
  }

  // spec-025: only a slot with no incoming edge gets a literal-value field
  // -- an edge always wins, so offering one here would be misleading.
  const unwiredInputs = node.data.inputs.filter((slot) => !wiredInputSlotNames.has(slot.name));

  // spec-025 Phase 5: dynamic option loading -- a slot named in
  // dynamicOptionSlots renders as a live-fetched dropdown instead of the
  // plain text field every other unwired input slot gets.
  const dynamicOptionSlots = new Set(node.data.dynamicOptionSlots ?? []);
  const [liveOptions, setLiveOptions] = useState<Record<string, { label: string; value: string }[]>>({});
  const [loadingOptionsFor, setLoadingOptionsFor] = useState<string | null>(null);
  const [optionsError, setOptionsError] = useState<Record<string, string>>({});

  async function loadOptionsFor(slotName: string) {
    if (!node.data.integration) return;
    setLoadingOptionsFor(slotName);
    setOptionsError((errs) => ({ ...errs, [slotName]: "" }));
    try {
      const options = await resolveNodeTypeOptions(node.data.nodeType, slotName, node.data.integration, inputValuesDraft);
      setLiveOptions((o) => ({ ...o, [slotName]: options }));
    } catch (e) {
      setOptionsError((errs) => ({ ...errs, [slotName]: String(e) }));
    } finally {
      setLoadingOptionsFor(null);
    }
  }

  useEffect(() => {
    setLiveOptions({});
    setOptionsError({});
    for (const slotName of dynamicOptionSlots) {
      void loadOptionsFor(slotName);
    }
    // Only re-fetch on a node change, not on every keystroke of a sibling
    // field -- "Refresh options" (rendered per dynamic-options field below)
    // covers picking up an in-progress edit to a field the binding forwards.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [node.id]);

  const properties = node.data.configSchema.properties ?? {};
  // spec-014: a config field's own schema already says whether it's
  // required (JsonSchema.required, generated straight from the Pydantic
  // model -- ADR-001's one-schema payoff) -- this was already typed but
  // unused by the frontend until now.
  const requiredFields = new Set(node.data.configSchema.required ?? []);

  function setField(name: string, value: unknown) {
    setDraft((d) => ({ ...d, [name]: value }));
  }

  async function handleSave() {
    if (!node) return;
    setSaving(true);
    setError(null);
    try {
      if (node.data.resolveSlotsFromSubNode) {
        // spec-012: this node's ports mirror a connected sub-node
        // (webhook_trigger's trigger_adapter), not its own config -- there
        // is nothing to re-resolve via POST /resolve-slots (config-based
        // dynamism only). Ports are kept as-is; onConnect already updates
        // them the moment the relevant sub-node edge is drawn.
        onConfigChange(node.id, draft, node.data.inputs, node.data.outputs, inputValuesDraft);
      } else if (node.data.dynamicSchema) {
        // Re-resolve ports for this instance's new config (SPEC-002's
        // resolve_slots, over HTTP) -- e.g. a code node's params change
        // when function_source changes. Only on save/blur, not per
        // keystroke: mcp_call's resolution spawns a real subprocess.
        const resolved = await resolveSlots(node.data.nodeType, draft);
        onConfigChange(node.id, draft, resolved.inputs, resolved.outputs, inputValuesDraft);
      } else {
        onConfigChange(node.id, draft, node.data.inputs, node.data.outputs, inputValuesDraft);
      }
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <form
      className="flex flex-col gap-3"
      onSubmit={(e) => {
        e.preventDefault();
        void handleSave();
      }}
    >
      {Object.entries(properties).map(([name, propSchema]) => (
        <div key={name} className="flex flex-col gap-1">
          <Label htmlFor={`field-${name}`}>
            {propSchema.title ?? name}
            {!requiredFields.has(name) && <OptionalTag />}
          </Label>
          {renderField(name, propSchema, draft[name], setField, draft)}
        </div>
      ))}

      {unwiredInputs.length > 0 && (
        <div className="mt-2 border-t border-border pt-3">
          <h3 className="text-xs font-semibold">Input values</h3>
          <p className="mt-1 mb-2 text-[11px] text-muted-foreground">
            No incoming edge -- type a value directly, or wire it from another node on canvas
            (an edge always takes over from a typed value).
          </p>
          {unwiredInputs.map((slot) => (
            <div key={slot.name} className="mb-3 flex flex-col gap-1">
              <Label htmlFor={`input-value-${slot.name}`}>
                {slot.name}
                {!slot.required && <OptionalTag />}
              </Label>
              {dynamicOptionSlots.has(slot.name) ? (
                <>
                  {(() => {
                    const currentValue =
                      typeof inputValuesDraft[slot.name] === "string" ? (inputValuesDraft[slot.name] as string) : "";
                    // See ModelField.tsx's comment: Radix's SelectValue only
                    // auto-derives display text from a SelectItem that has
                    // actually mounted -- explicit children sidesteps that.
                    const currentLabel = (liveOptions[slot.name] ?? []).find((o) => o.value === currentValue)?.label;
                    return (
                      <Select value={currentValue} onValueChange={(v) => setInputValue(slot.name, v)}>
                        <SelectTrigger id={`input-value-${slot.name}`} className="w-full">
                          <SelectValue placeholder="-- select --">{currentLabel}</SelectValue>
                        </SelectTrigger>
                        <SelectContent>
                          {(liveOptions[slot.name] ?? []).map((opt) => (
                            <SelectItem key={opt.value} value={opt.value}>
                              {opt.label}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    );
                  })()}
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    className="mt-1 self-start"
                    onClick={() => void loadOptionsFor(slot.name)}
                    disabled={loadingOptionsFor === slot.name}
                  >
                    {loadingOptionsFor === slot.name ? "Loading..." : "Refresh options"}
                  </Button>
                  {optionsError[slot.name] && (
                    <div className="text-xs text-[var(--status-error)]">{optionsError[slot.name]}</div>
                  )}
                </>
              ) : (
                <Input
                  id={`input-value-${slot.name}`}
                  type="text"
                  value={typeof inputValuesDraft[slot.name] === "string" ? (inputValuesDraft[slot.name] as string) : ""}
                  onChange={(e) => setInputValue(slot.name, e.target.value)}
                />
              )}
            </div>
          ))}
        </div>
      )}

      {error && <div className="text-xs text-[var(--status-error)]">{error}</div>}
      <Button type="submit" disabled={saving}>
        {saving ? "Resolving..." : "Save"}
      </Button>

      {connectedSubNodes.length > 0 && (
        <div className="mt-2 border-t border-border pt-3">
          <h3 className="text-xs font-semibold">Connected sub-nodes</h3>
          <p className="mt-1 mb-2 text-[11px] text-muted-foreground">
            Read-only -- click the node on canvas to edit its settings.
          </p>
          {connectedSubNodes.map(({ slot, node: subNode }) => (
            <div
              key={`${slot}-${subNode.id}`}
              className="mb-2 rounded-[var(--radius-sm)] border border-border bg-popover p-2"
            >
              <div className="mb-1 flex items-center justify-between gap-2">
                <span className="text-[10px] font-semibold tracking-[0.03em] text-muted-foreground uppercase">
                  {slot}
                </span>
                <span className="text-xs font-semibold">
                  {displayName(subNode.data.nodeType, subNode.data.integration)}
                </span>
              </div>
              <dl className="flex flex-col gap-0.5">
                {Object.entries(subNode.data.config).map(([key, value]) => (
                  <div key={key} className="flex justify-between gap-2 text-[11px]">
                    <dt className="text-muted-foreground">{key}</dt>
                    <dd className="truncate text-right">{typeof value === "string" ? value : JSON.stringify(value)}</dd>
                  </div>
                ))}
              </dl>
            </div>
          ))}
        </div>
      )}
    </form>
  );
}

// `function_source`, `connection`, and `model` are the deliberate per-
// field-name special cases (spec-005 §7, spec-006 §4/§9): a real multi-line
// editor, a named-connection picker, and (when the selected connection
// supports it) a live model dropdown, instead of a generic single-line
// input, since all three are foreseeable UX problems worth solving
// directly. Everything else falls through to the shared type-driven
// renderer. `draft` (the whole in-progress config, not just this field's
// value) is threaded through so `model` can read the sibling `connection`
// field -- a small, general widening rather than a model-specific hack, so
// any future field needing cross-field context gets it for free.
function renderField(
  name: string,
  propSchema: JsonSchemaProperty,
  value: unknown,
  setField: (name: string, value: unknown) => void,
  draft: Record<string, unknown>,
) {
  if (name === "function_source") {
    return (
      <CodeMirror
        value={typeof value === "string" ? value : ""}
        height="200px"
        theme="dark"
        extensions={[python()]}
        onChange={(v) => setField(name, v)}
      />
    );
  }

  // spec-018: mirrors backend/connections/resolver.py's
  // connection_reference_names() rule exactly -- any field that is
  // literally "connection" or ends with "_connection" (bot_token_connection,
  // embedding_model_connection, ...) is a connection reference and gets the
  // real picker, not a plain text box. Was previously an exact match on
  // "connection" only, which is what let a real bot token get typed
  // directly into a graph JSON file (no picker existed to make the
  // reference-vs-value distinction obvious).
  if (name === "connection" || name.endsWith("_connection")) {
    return (
      <ConnectionPicker
        value={typeof value === "string" ? value : undefined}
        onChange={(connectionName) => setField(name, connectionName)}
        allowedTypes={propSchema.connectionTypes}
        requiredCapability={propSchema.connectionCapability}
        requiredCredentialType={propSchema.credentialType}
      />
    );
  }

  if (name === "model") {
    return (
      <ModelField
        value={value}
        onChange={(v) => setField(name, v)}
        connectionName={typeof draft.connection === "string" ? draft.connection : undefined}
      />
    );
  }

  return renderPrimitiveField(name, propSchema, value, setField);
}
