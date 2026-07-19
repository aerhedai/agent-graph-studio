import type { JsonSchemaProperty } from "../api/types";
import { Toggle } from "./Toggle";

// The generic, type-driven (boolean/number/string/JSON-fallback) field
// renderer shared by ConfigPanel (node config forms) and ConnectionPicker
// (its inline "+ New connection" form, spec-006) -- pulled out to its own
// module specifically so those two components can import it without a
// circular dependency (ConfigPanel special-cases the "connection" field
// into a <ConnectionPicker>, which itself needs this same renderer for its
// own connection-type-specific fields like api_key/host/port).
export function renderPrimitiveField(
  name: string,
  propSchema: JsonSchemaProperty,
  value: unknown,
  setField: (name: string, value: unknown) => void,
) {
  if (propSchema.type === "boolean") {
    return (
      <Toggle
        id={`field-${name}`}
        checked={Boolean(value)}
        onChange={(checked) => setField(name, checked)}
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

  // object/array/$ref -- raw JSON fallback. A flattened/raw view is this
  // project's established MVP convention elsewhere (spec-005 §7).
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
