import { ChevronDown } from "lucide-react";
import { useEffect, useState } from "react";
import { fetchConnectionModels, fetchConnectionTypes, fetchConnections } from "../api/client";

interface ModelFieldProps {
  value: unknown;
  onChange: (value: string) => void;
  connectionName: string | undefined;
}

// Renders llm_call's `model` field as a live dropdown of real models when
// the selected connection's type supports discovering them (Ollama today),
// falling back to the original plain text input otherwise -- no connection
// selected yet, a type without listing (Anthropic today), or the live
// fetch itself failing (spec-006 §9). A value typed before the fallback
// is never silently dropped: if it's not in the fetched list, it's kept
// as an extra selectable option.
export function ModelField({ value, onChange, connectionName }: ModelFieldProps) {
  const [models, setModels] = useState<string[] | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setModels(null);
    if (!connectionName) return undefined;
    const selectedConnectionName = connectionName;

    async function load() {
      setLoading(true);
      try {
        const [connections, types] = await Promise.all([fetchConnections(), fetchConnectionTypes()]);
        const connection = connections.find((c) => c.name === selectedConnectionName);
        const typeInfo = connection ? types.find((t) => t.type === connection.type) : undefined;
        if (!typeInfo?.supports_model_listing) {
          if (!cancelled) setModels(null);
          return;
        }
        const fetched = await fetchConnectionModels(selectedConnectionName);
        if (!cancelled) setModels(fetched);
      } catch {
        if (!cancelled) setModels(null); // graceful fallback to plain text input
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    void load();
    return () => {
      cancelled = true;
    };
  }, [connectionName]);

  const stringValue = typeof value === "string" ? value : "";

  if (models && models.length > 0) {
    const options = !stringValue || models.includes(stringValue) ? models : [stringValue, ...models];
    return (
      <span className="select-wrap">
        <select id="field-model" value={stringValue} onChange={(e) => onChange(e.target.value)}>
          <option value="" disabled>
            Select a model...
          </option>
          {options.map((m) => (
            <option key={m} value={m}>
              {m}
            </option>
          ))}
        </select>
        <ChevronDown className="select-wrap__chevron" size={14} />
      </span>
    );
  }

  return (
    <input
      id="field-model"
      type="text"
      value={stringValue}
      onChange={(e) => onChange(e.target.value)}
      placeholder={loading ? "Loading models..." : undefined}
    />
  );
}
