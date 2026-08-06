# SPEC-028: Frontend Design Overhaul — Tailwind + shadcn/ui, Light/Dark Theming, Whole-App Polish

**Status:** Implemented — verified live, full test suite passing
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

- [x] Tailwind CSS + shadcn/ui are installed and configured; `npx tsc -b` and `npx vitest run` both continue to pass — confirmed clean after every phase, 22/22 Vitest passing throughout.
- [x] A real light theme and the evolved dark theme both exist as full CSS-variable sets (`:root` + `.dark`), both visibly the same "circuit" identity (violet accent family, category/status colors carried through) — not a generic default light palette. `tokens.css` defines both; live-verified via screenshots in both themes.
- [x] A working theme toggle switches instantly between light/dark, persists across reloads via `localStorage`, and defaults to system preference when no stored choice exists yet — live-verified in a real (Playwright-driven) browser: toggle flips `.dark` + computed background color, `localStorage` key set, persists across reload.
- [x] Every panel listed in §3 is rebuilt on shadcn components, not raw custom-styled native elements — `ConfigPanel`, `ConnectionPicker`, `HistoryPanel`, `NodeInspectorPanel`, `SettingsPanel`, `TraceInspector`, `Toggle`, `ModelField`, `fieldRenderers` all migrated. `OperationField` did not exist on this branch (it's part of SPEC-022, still unmerged/stashed on a separate branch as of this spec's implementation) — nothing to migrate; whoever rebases SPEC-022 onto this work will build it directly on the new foundation.
- [x] Canvas chrome (`GenericNode`, `Palette`, `StatusEdge`) reflects the same token system and typography in both themes, with React Flow's own drag/connect/zoom behavior unaffected — live-verified: real drag-from-palette, real edge connection, real run with correct execution-state (error/success) border colors, in both themes.
- [x] Typography updated to Inter (UI text) + JetBrains Mono (code/technical surfaces) across the app — self-hosted via `@fontsource-variable/inter` + `@fontsource-variable/jetbrains-mono` (see §8, resolved).
- [x] No hardcoded hex colors remain outside the token definition layer — spot-checked via a repo-wide grep across `canvas/`, `panels/`, and `components/ui/`: zero matches outside `tokens.css` itself.
- [x] Existing functionality verified working via real live checks, not just type-checking/unit tests — a full live demonstration: dragged `text_input`/`text_output` onto the canvas, connected them with a real edge, configured and saved a literal value, toggled theme mid-session, ran the graph for real, and inspected its real trace record (`{"text": "SPEC-028 final demo"}` → success). Also separately verified: a real Ollama model dropdown populated with live models, a real connection picker showing a real saved connection, real execution history (50 real runs) and settings data.
- [x] `git diff main -- backend/` is empty — confirmed, zero lines.

A real, non-obvious bug was found and fixed during Phase 4 live verification, not papered over for a screenshot: Radix `Select`'s hidden native "bubble select" (used for form autofill participation) fires a spurious `onValueChange("")` the moment it syncs, whenever the controlled `value` doesn't yet match any *mounted* `SelectItem` — which is always true for a value set programmatically from already-saved data (e.g. an existing node's saved `connection` field) before the dropdown has ever been opened, since `SelectContent`'s items aren't mounted until then. Undetected, this silently wiped a real saved value the instant a config panel was opened. Confirmed via direct reproduction (a debug trace showing `setField("bot_token_connection", "")` firing from inside `radix-ui.js`, unprompted by any user action), not theoretical. Fixed once, at the shared `components/ui/select.tsx` primitive (filtering out `onValueChange("")` calls, since a real `SelectItem` can never legitimately have `value=""` in Radix), rather than patched per-consumer.

## 8. Open questions

- **Exact final token values.** Resolved: locked in during implementation (see `tokens.css`), grounded in but not identical to the §5 research (kept the project's own existing violet `#a970ff` dark-mode accent rather than switching to the research tool's suggested `#7C3AED`, consistent with "evolve, don't replace" — `#7C3AED` was used instead for the *light*-mode primary, where a deeper violet is needed for contrast on white). Tuned live against real screenshots in both themes, not applied blind.
- **Self-hosting Inter/JetBrains Mono vs. a build-time font subsetting step.** Resolved: `@fontsource-variable/inter` and `@fontsource-variable/jetbrains-mono` (npm packages shipping the actual variable-font WOFF2 files, bundled by Vite at build time) — genuinely self-hosted, zero runtime CDN request, no manual font-file vendoring needed.
