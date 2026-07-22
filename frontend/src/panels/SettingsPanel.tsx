import { useEffect, useState } from "react";
import { getSettings, updateSettings } from "../api/client";

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

  useEffect(() => {
    getSettings()
      .then((res) => {
        setSaved(res.public_base_url);
        setDraft(res.public_base_url ?? "");
      })
      .catch((e: unknown) => setError(String(e)));
  }, []);

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
      </aside>
    </div>
  );
}
