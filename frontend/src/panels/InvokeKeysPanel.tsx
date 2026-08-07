import { useEffect, useState, type ReactNode } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { createInvokeKey, fetchGraphContract, listInvokeKeys, revokeInvokeKey } from "../api/client";
import type { InvokeContractResponse, InvokeKeyInfo } from "../api/types";

interface InvokeKeysPanelProps {
  graphId: string;
  onClose: () => void;
}

function SectionHeading({ children }: { children: string }) {
  return <h2 className="mt-4 text-sm font-semibold text-foreground">{children}</h2>;
}

function Hint({ children }: { children: ReactNode }) {
  return <p className="text-[13px] text-muted-foreground">{children}</p>;
}

function ErrorText({ children }: { children: ReactNode }) {
  return <div className="text-xs text-[var(--status-error)]">{children}</div>;
}

// spec-029: read-only preview of what an external caller sees -- lets a
// graph author confirm the invoke contract (field names, required/optional)
// before sharing a key, without reading the raw graph JSON.
function ContractTable({ contract }: { contract: InvokeContractResponse }) {
  if (contract.inputs.length === 0 && contract.outputs.length === 0) {
    return (
      <Hint>
        This graph has no <code>text_input</code>/<code>text_output</code> nodes yet -- add at
        least one of each to give it an invoke contract.
      </Hint>
    );
  }
  return (
    <div className="flex flex-col gap-1.5">
      {[...contract.inputs, ...contract.outputs].map((field) => (
        <div
          key={`${field.direction}-${field.node_id}`}
          className="grid grid-cols-[auto_1fr_auto] items-center gap-2 rounded-[var(--radius-sm)] border border-border bg-popover px-2.5 py-1.5"
        >
          <Badge variant="outline" className="text-[11px] font-semibold tracking-[0.03em] uppercase">
            {field.direction}
          </Badge>
          <span className="overflow-hidden font-mono text-xs text-ellipsis whitespace-nowrap">{field.name}</span>
          <span className="text-xs whitespace-nowrap text-muted-foreground">
            {field.direction === "input" ? (field.required ? "required" : "optional") : ""}
          </span>
        </div>
      ))}
    </div>
  );
}

// spec-029: generate/list/revoke per-graph invoke keys, plus a read-only
// contract preview -- the human-facing counterpart to POST /graphs/{id}
// /invoke-keys and GET /graphs/{id}/contract. Mirrors SettingsPanel.tsx's
// Sheet shell and section conventions.
export function InvokeKeysPanel({ graphId, onClose }: InvokeKeysPanelProps) {
  const [contract, setContract] = useState<InvokeContractResponse | null>(null);
  const [keys, setKeys] = useState<InvokeKeyInfo[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [labelDraft, setLabelDraft] = useState("");
  const [timeoutDraft, setTimeoutDraft] = useState("60");
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  const [justCreatedToken, setJustCreatedToken] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const [revoking, setRevoking] = useState<string | null>(null);

  function refresh() {
    setLoading(true);
    setError(null);
    Promise.all([fetchGraphContract(graphId), listInvokeKeys(graphId)])
      .then(([contractRes, keysRes]) => {
        setContract(contractRes);
        setKeys(keysRes);
      })
      .catch((e: unknown) => setError(String(e)))
      .finally(() => setLoading(false));
  }

  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [graphId]);

  async function handleCreate() {
    const timeoutSeconds = Number(timeoutDraft);
    if (!labelDraft.trim()) {
      setCreateError("Give this key a label so you can recognize it later.");
      return;
    }
    if (!Number.isInteger(timeoutSeconds) || timeoutSeconds < 1 || timeoutSeconds > 300) {
      setCreateError("Timeout must be a whole number of seconds between 1 and 300.");
      return;
    }
    setCreating(true);
    setCreateError(null);
    setCopied(false);
    try {
      const res = await createInvokeKey(graphId, labelDraft.trim(), timeoutSeconds);
      setJustCreatedToken(res.token);
      setLabelDraft("");
      setTimeoutDraft("60");
      refresh();
    } catch (e) {
      setCreateError(String(e));
    } finally {
      setCreating(false);
    }
  }

  async function handleRevoke(keyId: string) {
    setRevoking(keyId);
    try {
      await revokeInvokeKey(graphId, keyId);
      setKeys((prev) => prev.filter((k) => k.key_id !== keyId));
    } catch (e) {
      setError(String(e));
    } finally {
      setRevoking(null);
    }
  }

  async function handleCopy() {
    if (!justCreatedToken) return;
    await navigator.clipboard.writeText(justCreatedToken);
    setCopied(true);
  }

  return (
    <Sheet open onOpenChange={(open) => !open && onClose()}>
      <SheetContent className="w-[420px] max-w-[90vw] gap-3 overflow-y-auto p-4">
        <SheetHeader className="p-0">
          <SheetTitle>Invoke API</SheetTitle>
        </SheetHeader>
        <Hint>
          Call this graph synchronously from another application: <code>POST /graphs/{graphId}/invoke</code>{" "}
          with a JSON object of named inputs, authenticated by one of the keys below. See the contract for
          exact field names.
        </Hint>

        {error && <ErrorText>{error}</ErrorText>}
        {loading && !contract && <Hint>Loading...</Hint>}

        {contract && (
          <>
            <SectionHeading>Contract</SectionHeading>
            <ContractTable contract={contract} />
          </>
        )}

        <SectionHeading>Keys</SectionHeading>
        {keys.length === 0 && <Hint>No invoke keys yet -- create one below.</Hint>}
        {keys.map((k) => (
          <div
            key={k.key_id}
            className="flex flex-col gap-1 rounded-[var(--radius-sm)] border border-border p-2"
          >
            <div className="flex items-center justify-between gap-2">
              <Label>{k.label}</Label>
              <Button
                type="button"
                variant="outline"
                onClick={() => void handleRevoke(k.key_id)}
                disabled={revoking === k.key_id}
              >
                {revoking === k.key_id ? "Revoking..." : "Revoke"}
              </Button>
            </div>
            <span className="font-mono text-xs text-muted-foreground">{k.key_prefix}...</span>
            <span className="text-[13px] text-muted-foreground">
              timeout {k.timeout_seconds}s -- created {new Date(k.created_at).toLocaleString()}
              {k.last_used_at ? ` -- last used ${new Date(k.last_used_at).toLocaleString()}` : " -- never used"}
            </span>
          </div>
        ))}

        <SectionHeading>Create a new key</SectionHeading>
        <div className="flex flex-col gap-1">
          <Label htmlFor="invoke-key-label">Label</Label>
          <Input
            id="invoke-key-label"
            type="text"
            value={labelDraft}
            onChange={(e) => setLabelDraft(e.target.value)}
            placeholder="e.g. production backend"
          />
        </div>
        <div className="flex flex-col gap-1">
          <Label htmlFor="invoke-key-timeout">Timeout (seconds)</Label>
          <Input
            id="invoke-key-timeout"
            type="number"
            min={1}
            max={300}
            value={timeoutDraft}
            onChange={(e) => setTimeoutDraft(e.target.value)}
          />
        </div>
        <Button type="button" onClick={() => void handleCreate()} disabled={creating} className="self-start">
          {creating ? "Creating..." : "Create key"}
        </Button>
        {createError && <ErrorText>{createError}</ErrorText>}

        {justCreatedToken && (
          <div className="flex flex-col gap-1.5 rounded-[var(--radius-sm)] border border-[var(--status-running)] p-2">
            <Hint>
              Copy this token now -- you won't be able to see it again after closing this panel.
            </Hint>
            <code className="overflow-x-auto rounded-[var(--radius-sm)] bg-muted px-2 py-1 text-xs break-all">
              {justCreatedToken}
            </code>
            <Button type="button" variant="outline" onClick={() => void handleCopy()} className="self-start">
              {copied ? "Copied" : "Copy"}
            </Button>
          </div>
        )}
      </SheetContent>
    </Sheet>
  );
}
