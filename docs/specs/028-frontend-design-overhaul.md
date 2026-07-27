# SPEC-028: Frontend Design Overhaul — Tailwind + shadcn/ui, Light/Dark Theming, Whole-App Polish

**Status:** Draft — ready for review
**Milestone:** Frontend quality push
**Author:** Rohan
**Depends on:** SPEC-013 (visual design system — this spec evolves, not replaces, its "circuit at night" identity and token-driven-styling discipline), SPEC-018 (canvas UX parity), SPEC-005 (canvas), SPEC-022 (unified app nodes — most recent panel/canvas surface, must survive this migration intact)

## 1. Goal

Migrate the frontend's styling foundation from hand-rolled CSS (`App.css` at 1,500+ lines, `tokens.css`) to **Tailwind CSS + shadcn/ui** (owned, Radix-based component source), extend SPEC-013's "circuit at night" visual identity into a real **light + dark theme pair**, and bring every panel and app-chrome surface — not just the canvas SPEC-013/018 already covered — up to the same level of polish.

## 2. Why this, why now

SPEC-013 gave the canvas and node palette a real, considered identity, but explicitly scoped itself to "Node Aesthetics, Execution Feedback, and Node Palette" and deliberately deferred theming ("ship one deliberate, cohesive dark theme well, rather than a half-built theming system"). Since then, SPEC-019 through SPEC-027 added a large amount of new UI surface — `SettingsPanel`, `ConnectionPicker`, `HistoryPanel`, `NodeInspectorPanel`, `TraceInspector`, `ModelField`/`OperationField`, `Toggle`, `fieldRenderers` — built against the same `tokens.css` custom properties but without a component system behind them, so consistency depends entirely on discipline rather than anything structural. The plain-CSS approach is also increasingly expensive to keep consistent as the app grows: 1,500+ lines in one file, no reusable styled-component layer, every new control (dropdown, dialog, toggle) hand-built from scratch.

This spec is grounded in real research, not ad hoc taste: the `ui-ux-pro-max` local design-intelligence skill (a searchable database of styles/palettes/typography/stack guidance) was queried live against this project's actual identity (developer tool, node-graph canvas, dark/technical/precision mood, existing violet accent) — see §5 for the specific outputs that inform the choices below, not invented values.

## 3. Scope

**In scope:**

- **Tailwind CSS + shadcn/ui** installed and configured in `frontend/` as the new styling foundation. shadcn components are generated into `frontend/src/components/ui/` as owned source (its own convention — copy-in, not an opaque dependency), so they can keep being edited like any other component in this codebase.
- **A real light theme, not a generic default** — the same violet "circuit" accent family (`#a970ff`/`#7C3AED`-adjacent) expressed in both a `:root` (light) and `.dark` variable set, so light mode reads as the same product, not a different one. Category colors (`--cat-*`) and status colors (`--status-*`) get light-mode-appropriate counterparts too (their current values were tuned only for dark backgrounds).
- **Theme toggle + persistence**: a real switch in app chrome, backed by `localStorage`, defaulting to the OS `prefers-color-scheme` on first load (no stored preference yet).
- **Typography refresh**: adopt **Inter** as the primary UI sans-serif (replacing today's bare `system-ui` stack) — per the researched "Modern Dark Cinema" pairing, which matches this product's already-established "dark, cinematic, technical, precision, developer" mood almost exactly. Introduce a dedicated monospace face (candidate: **JetBrains Mono**, researched as the standard pairing for "developer tools... precision, OLED") for code/technical surfaces — the `code` node's CodeMirror editor, trace/log output, raw JSON views, node type identifiers — replacing the current generic `ui-monospace, Consolas` fallback stack.
- **Every existing panel rebuilt on shadcn primitives**: `ConfigPanel`, `ConnectionPicker`, `HistoryPanel`, `NodeInspectorPanel`, `SettingsPanel`, `TraceInspector`, `Toggle`, `ModelField`, `OperationField`, `fieldRenderers` — dropdowns become shadcn `Select`, modals/side panels become `Dialog`/`Sheet`, toggles become `Switch`, tooltips become `Tooltip`, badges become `Badge`, etc., instead of the current custom-styled native elements.
- **Canvas chrome restyled on the same foundation**: `GenericNode`, `Palette`, `StatusEdge` migrate their presentational styling (card chrome, badges, palette rows/accordions) to Tailwind utility classes and shared tokens — React Flow's own positioning/transform/drag machinery is untouched, this only touches what's rendered inside each node/edge's DOM.
- **App shell**: the top-level header/layout in `App.tsx` gets real chrome (not just an `<h1>`) — this is also where the theme toggle lives.
- **Accessibility improvements as a side effect, not a project**: Radix primitives (which shadcn wraps) are accessible-by-default (focus management, ARIA roles, keyboard nav), so adopting them raises the floor automatically. This is not a full accessibility audit and isn't held to a WCAG acceptance bar — same deliberate deferral SPEC-013 made, just no longer actively working against it.

**Out of scope:**

- **A full accessibility audit / WCAG certification.** Still explicitly deferred, per SPEC-013's own precedent — a future spec's job if/when it becomes a priority.
- **Replacing React Flow** (`@xyflow/react`). This spec restyles the presentational chrome rendered inside/around the canvas; the graph library itself is unchanged.
- **Any backend change.** This is a pure frontend/presentation migration — `git diff main -- backend/` must be empty.
- **New product features.** This is a visual/component migration of existing surfaces, not new panels or functionality.

## 4. Design decisions (resolved)

Resolved directly with the user before drafting this spec, rather than assumed:

- **Foundation: Tailwind + shadcn/ui**, not an incremental evolution of the hand-rolled CSS system — chosen for long-term leverage (owned component source an agent can keep editing directly) over the lower-risk, smaller-lift alternative.
- **Scope: whole app**, not canvas/palette-only — every panel gets the same treatment, not just the surfaces SPEC-013/018 already covered.
- **Visual identity: keep and evolve "circuit at night"**, not a fresh redesign — it's already grounded in the product's own subject (a graph that executes/flows), confirmed as a considered identity worth extending rather than discarding.
- **Light mode: in scope**, superseding SPEC-013 §3's original "dark-only, ship one theme well" deferral now that the app has matured — this spec is what closes that gap, deliberately, not by accident.

## 5. Research inputs (ui-ux-pro-max, live queries — not fabricated)

Concrete outputs from the local `ui-ux-pro-max` skill, used as the starting point for §6's token table (subject to live visual iteration during implementation, not pasted in blind):

- **Design system query** (`"developer tool node graph canvas dark mode technical" --design-system`): recommended style **"Dark Mode (OLED)"**, pattern "Minimal + Documentation", typeface **Inter** ("dark, cinematic, technical, precision, clean, premium, developer, professional, high-end utility" — matches this product's mood almost exactly), primary `#7C3AED`, accent `#0891B2`, background `#1C1917`.
- **Color domain query** (`"dark mode accessible color pairs violet purple technical" --domain color`): closest match "Photo Editor & Filters" — primary `#7C3AED`, secondary `#6366F1`, accent `#0891B2`, background `#0F172A`, card `#192134`, muted `#171939`, muted-foreground `#94A3B8`, border `rgba(255,255,255,0.08)`, destructive `#DC2626` — structurally very close to this project's existing `tokens.css` values (`--color-accent: #a970ff` sits in the same violet family), used as the reference shape for filling in shadcn's fuller variable set (`--muted-foreground`, `--card`, etc.) that `tokens.css` doesn't currently define.
- **Typography domain query** (`"technical clean developer tool monospace pairing" --domain typography`): top matches were "Modern Dark Cinema (Inter System)" (Inter, sans+mono, "developer tools, fintech/trading, AI dashboards... high-end productivity apps") and "Terminal CLI Monospace" (JetBrains Mono, "developer tools... precision, OLED" — recommended here specifically for code/log surfaces, not general UI text).
- **Stack guidance** (`--stack shadcn`, `--stack react`): confirmed shadcn's own documented pattern — CSS variables in `:root` **and** `.dark`, never hardcoded colors in components (`bg-primary` not `bg-blue-500`) — directly reinforces this project's own pre-existing "no hardcoded hex, tokens only" rule from SPEC-013 §4, just extended to Tailwind's utility-class idiom.

## 6. Data model / implementation notes

- New `frontend/tailwind.config.ts`, `postcss.config.js`; Tailwind directives added to a global stylesheet entry point (`index.css`, or a new one if cleaner).
- shadcn/ui initialized via its CLI (`npx shadcn@latest init`), components added individually as needed (`npx shadcn@latest add select dialog sheet switch tooltip badge ...`) into `frontend/src/components/ui/` — owned source, editable like any other component.
- `tokens.css`'s existing custom properties are remapped onto shadcn's expected variable names (`--background`, `--foreground`, `--primary`, `--primary-foreground`, `--card`, `--muted`, `--muted-foreground`, `--border`, `--ring`, `--destructive`, etc.), defined under both `:root` (light) and `.dark`. Project-specific concepts shadcn has no vocabulary for — `--cat-*` (node category colors), `--status-*` (execution state colors), `--ease-*` (motion curves) — stay as their own tokens, layered alongside the shadcn set, each with a light and dark value.
- A new `ThemeProvider`/`useTheme` (localStorage-backed, `prefers-color-scheme`-aware on first load) mounted at `App.tsx`'s root; the toggle control lives in the app header.
- `--font-sans` becomes an Inter stack (self-hosted or `@font-face`, not a runtime Google Fonts CDN call — matches this project's general "no unnecessary external runtime dependencies" instincts); `--font-mono` becomes a JetBrains Mono stack for code/log/technical surfaces specifically (CodeMirror's own font config, trace/JSON views), while general UI keeps `--font-sans`.

## 7. Acceptance criteria

- [ ] Tailwind CSS + shadcn/ui are installed and configured; `npx tsc -b` and `npx vitest run` both continue to pass.
- [ ] A real light theme and the evolved dark theme both exist as full CSS-variable sets (`:root` + `.dark`), both visibly the same "circuit" identity (violet accent family, category/status colors carried through) — not a generic default light palette.
- [ ] A working theme toggle switches instantly between light/dark, persists across reloads via `localStorage`, and defaults to system preference when no stored choice exists yet — live-verified in a real browser.
- [ ] Every panel listed in §3 (`ConfigPanel`, `ConnectionPicker`, `HistoryPanel`, `NodeInspectorPanel`, `SettingsPanel`, `TraceInspector`, `Toggle`, `ModelField`, `OperationField`, `fieldRenderers`) is rebuilt on shadcn components, not raw custom-styled native elements.
- [ ] Canvas chrome (`GenericNode`, `Palette`, `StatusEdge`) reflects the same token system and typography in both themes, with React Flow's own drag/connect/zoom behavior unaffected.
- [ ] Typography updated to Inter (UI text) + JetBrains Mono (code/technical surfaces) across the app.
- [ ] No hardcoded hex colors remain outside the token definition layer — spot-checked across `canvas/`, `panels/`, and `components/ui/` — same bar SPEC-013 §6 originally set.
- [ ] Existing functionality (drag/connect/run/inspect trace, every panel's real interactions — connecting an MCP server, picking an operation, viewing history/trace, editing settings) verified working, unchanged, via real live checks in a running browser — not just type-checking/unit tests, since this touches every interactive surface in the app.
- [ ] `git diff main -- backend/` is empty.

## 8. Open questions

- **Exact final token values.** §5/§6 give a researched, concrete starting point (not invented, not final) — actual hex/HSL values get tuned live against the real running app during implementation rather than locked in from a database match alone. Not spec-blocking; flagging so "the spec's numbers" aren't mistaken for a final, unchangeable palette.
- **Self-hosting Inter/JetBrains Mono vs. a build-time font subsetting step.** Both are open-source (SIL OFL) and can be vendored directly into the repo (no runtime CDN dependency) — exact mechanism (raw `@font-face` + static files vs. a Vite font-loading plugin) to be decided during implementation, not a design-blocking decision.
