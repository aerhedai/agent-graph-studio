interface ToggleProps {
  id?: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
}

// spec-013 §5 (resolved): booleans get a real sliding toggle switch, not a
// native checkbox -- a visually-hidden real <input type="checkbox"> drives
// a styled track/thumb via CSS sibling selectors, so keyboard nav, space-
// to-toggle, and screen-reader semantics all stay exactly what a real
// checkbox already provides "for free" (same reasoning as the reskinned
// selects: never rebuild what the native control already does correctly).
export function Toggle({ id, checked, onChange }: ToggleProps) {
  return (
    <label className="toggle">
      <input
        id={id}
        type="checkbox"
        className="toggle__input"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
      />
      <span className="toggle__track">
        <span className="toggle__thumb" />
      </span>
    </label>
  );
}
