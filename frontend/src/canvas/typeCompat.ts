// Mirrors backend SlotTypeSpec.is_compatible_with (backend/schema/types.py)
// exactly: exact structural match, no coercion. The backend compares two
// Pydantic model instances with `==`; here we deep-compare the same
// model_dump()'d JSON shape ({base, element_type}) the API sends over the
// wire, so a "text" output can never connect to a "json" input in the UI,
// matching what backend validation would reject anyway -- this is the UI
// half of "validate at connection time, not just runtime" (CLAUDE.md).
export function deepEqual(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  if (typeof a !== "object" || typeof b !== "object" || a === null || b === null) {
    return false;
  }
  const aKeys = Object.keys(a as Record<string, unknown>);
  const bKeys = Object.keys(b as Record<string, unknown>);
  if (aKeys.length !== bKeys.length) return false;
  return aKeys.every((key) =>
    bKeys.includes(key) &&
    deepEqual((a as Record<string, unknown>)[key], (b as Record<string, unknown>)[key]),
  );
}

export function slotTypesCompatible(
  outputType: Record<string, unknown> | undefined,
  inputType: Record<string, unknown> | undefined,
): boolean {
  if (!outputType || !inputType) return false;
  return deepEqual(outputType, inputType);
}
