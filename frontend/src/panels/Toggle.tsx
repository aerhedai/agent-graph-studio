import { Switch } from "@/components/ui/switch";

interface ToggleProps {
  id?: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
}

// spec-013 §5 (resolved), spec-028: booleans get a real sliding toggle
// switch, not a native checkbox -- now shadcn's Radix-based `Switch`
// (`@/components/ui/switch.tsx`), which already provides the same
// keyboard-nav/space-to-toggle/screen-reader semantics this component's
// original hand-rolled version was built to preserve. Kept as its own
// thin wrapper (rather than inlining `<Switch>` at every call site) so
// `fieldRenderers.tsx`'s call site doesn't need to change.
export function Toggle({ id, checked, onChange }: ToggleProps) {
  return <Switch id={id} checked={checked} onCheckedChange={onChange} />;
}
