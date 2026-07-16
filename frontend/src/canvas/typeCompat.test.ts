import { describe, expect, it } from "vitest";
import { slotTypesCompatible } from "./typeCompat";

// Every node type currently registered in the backend is TEXT-only (SPEC-001
// through SPEC-004 deliberately kept everything TEXT for cross-node
// connectivity), so there is no *real* node pair that produces an
// incompatible-type connection today -- there is nothing to screenshot. This
// test proves the actual comparison logic the canvas will use is correct
// against synthetic type shapes matching the backend's real SlotTypeSpec
// wire format, so the mechanism is provably in place ahead of a future spec
// introducing a second type (e.g. `json`).

const TEXT = { base: "text", element_type: null };
const JSON_TYPE = { base: "json", element_type: null };
const TEXT_AGAIN = { base: "text", element_type: null }; // distinct object, same shape

describe("slotTypesCompatible", () => {
  it("allows identical slot types", () => {
    expect(slotTypesCompatible(TEXT, TEXT_AGAIN)).toBe(true);
  });

  it("rejects genuinely incompatible slot types", () => {
    expect(slotTypesCompatible(JSON_TYPE, TEXT)).toBe(false);
    expect(slotTypesCompatible(TEXT, JSON_TYPE)).toBe(false);
  });

  it("rejects when either side is undefined (unresolved dynamic-schema port)", () => {
    expect(slotTypesCompatible(undefined, TEXT)).toBe(false);
    expect(slotTypesCompatible(TEXT, undefined)).toBe(false);
    expect(slotTypesCompatible(undefined, undefined)).toBe(false);
  });

  it("is not fooled by key order", () => {
    const a = { base: "text", element_type: null };
    const b = { element_type: null, base: "text" };
    expect(slotTypesCompatible(a, b)).toBe(true);
  });

  it("distinguishes nested list element types", () => {
    const listOfText = { base: "list", element_type: { base: "text", element_type: null } };
    const listOfJson = { base: "list", element_type: { base: "json", element_type: null } };
    expect(slotTypesCompatible(listOfText, listOfJson)).toBe(false);
    expect(slotTypesCompatible(listOfText, { ...listOfText })).toBe(true);
  });
});
