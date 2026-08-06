import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import type { JsonSchemaProperty } from "../api/types";
import { Toggle } from "./Toggle";

// The generic, type-driven (boolean/number/string/JSON-fallback) field
// renderer shared by ConfigPanel (node config forms) and ConnectionPicker
// (its inline "+ New connection" form, spec-006) -- pulled out to its own
// module specifically so those two components can import it without a
// circular dependency (ConfigPanel special-cases the "connection" field
// into a <ConnectionPicker>, which itself needs this same renderer for its
// own connection-type-specific fields like api_key/host/port).
// Pydantic's `X | None` fields have no top-level `type` -- unwrap the
// standard `anyOf: [{type: X}, {type: "null"}]` shape to find the real
// underlying type, so an optional string field renders as a normal text
// input instead of falling through to the raw-JSON fallback (which
// silently drops a plain-text value, since it isn't valid JSON).
function effectiveSchema(propSchema: JsonSchemaProperty): JsonSchemaProperty {
  if (propSchema.type !== undefined || !propSchema.anyOf) return propSchema;
  const nonNull = propSchema.anyOf.find((branch) => branch.type !== "null");
  return nonNull ?? propSchema;
}

export function renderPrimitiveField(
  name: string,
  rawPropSchema: JsonSchemaProperty,
  value: unknown,
  setField: (name: string, value: unknown) => void,
) {
  const propSchema = effectiveSchema(rawPropSchema);

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
      <Input
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
      <Input
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
    <Textarea
      id={`field-${name}`}
      className="min-h-[80px] font-mono text-xs"
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
