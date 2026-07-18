# SPEC-013: Visual Design System — Node Aesthetics, Execution Feedback, and Node Palette

**Status:** Draft — ready for implementation
**Milestone:** Canvas Visual Polish
**Author:** Rohan
**Depends on:** SPEC-005 (canvas), SPEC-012 (sub-node connectors — this spec's connector styling directly builds on that edge-kind distinction)
**Reference:** Provided n8n screenshot — cite specific elements directly in implementation, not just "make it look like n8n"

## 1. Goal

Give the canvas a genuinely polished, modern visual identity: distinct node anatomy per type, live color-coded execution feedback (idle → running → success/error) on both nodes and the connectors between them, an organized/collapsible categorized node palette, a cohesive modern color system, and elegant custom-styled form controls — replacing whatever default/unstyled state SPEC-005 shipped with.

## 2. Why this, why now

Everything built so far (SPEC-001–012) is architecturally solid but visually whatever React Flow gives you by default. Given this project's purpose (a portfolio piece meant to be *shown*), visual polish is not cosmetic — it's the first impression anyone evaluating this will actually judge, before they ever read a line of your backend code. This is also the natural point to do it: the node/edge *model* (SPEC-012's sub-node vs. data edges) is now stable enough to design a visual language around without redoing this work later.

## 3. Scope

In scope:
- **Node anatomy redesign**: icon + title + operation subtitle (e.g. "HTTP Request — POST", matching the screenshot's "Send a message in Slack / post: message" pattern), a small top-corner badge indicating special modes (the screenshot's "M"/"N" badges — adapt meaning to this project, e.g. indicating "has sub-node inputs" or connection/model type)
- **Execution-state color coding on nodes**: distinct, animated visual states for idle, running (e.g. a pulsing or rotating indicator), success, and error — applied live during an actual run, not just static mockup states
- **Execution-state color/animation on edges**: connectors visually indicate active data flow (the screenshot's animated dashed/flowing line style) while a run is in progress, and settle into a distinct completed/error color once that step finishes
- **Distinct visual treatment for `sub_node` edges vs. data edges** (per SPEC-012) — the screenshot's dashed vs. solid vs. thick-gradient distinction is a good reference point for making these two edge kinds unmistakably different at a glance, not just different in a tooltip
- **Categorized, collapsible node palette** (left sidebar): node types grouped into sections (e.g. Triggers, Core/Logic, AI, Data/Storage, Custom), each section an accordion that expands/collapses smoothly, populated dynamically from the backend registry (per SPEC-005's existing `GET /node-types` — this spec only restyles/reorganizes presentation, doesn't change that data source)
- **A cohesive design token system**: a defined color palette (not ad-hoc per-component colors), consistent spacing/radius/typography scale, applied via CSS variables so the whole canvas shares one visual language
- **Custom-styled form controls**: dropdowns, text inputs, toggles, sliders used in node config panels get a deliberate, elegant custom style — not browser-default form elements

Out of scope (future specs):
- User-configurable/switchable themes (light mode, custom accent colors) — ship one deliberate, cohesive dark theme well, rather than a half-built theming system
- Full accessibility audit (screen reader support, full keyboard navigation) — worth keeping in mind (don't actively break it), but not this spec's focus or acceptance bar
- The JSON diff/parameter-change view shown in the screenshot's right panel — a genuinely nice feature, but a distinct, separate capability (config change history) from visual polish; note as a good candidate for its own future spec

## 4. Design decisions (resolved)

- **Design token approach**: implement as CSS custom properties (`--color-*`, `--radius-*`, `--space-*`) at a root/theme level, per the project's existing frontend-design conventions — every component reads from these, no hardcoded hex values scattered through component code. This is what makes "cohesive" actually achievable rather than aspirational.
- **Execution-state animation should be driven by real run data**, not a decorative loop — node/edge visual state must reflect actual per-node status from the existing trace/polling mechanism (SPEC-005's `GET /runs/{run_id}` polling), so what you see genuinely reflects what's happening, not a canned animation unrelated to real execution.
- **Node badge meaning**: adapt the screenshot's badge concept to actually carry information for this project — e.g. a badge indicating a node has sub-node slots (per SPEC-012), or which connection/provider a `model` node is using — rather than copying n8n's specific badge letters verbatim, since they encode n8n's own internal concepts, not yours.
- **Palette categorization**: base initial categories on the node types you actually have (Triggers: `schedule_trigger`/`webhook_trigger`; Core: `conditional_branch`/`code`/`fan_out`/`merge`/`loop`; AI: `llm_call`/`agent`/`model`/`vector_search`/`ingest_document`; Connectivity: `mcp_call`) — derived from existing registry metadata, not hardcoded separately from it, so a new node type added later needs a category tag at registration, not a manual palette edit.

## 5. Data model / implementation notes

- Node type registry entries (already returned by `GET /node-types` per SPEC-005) gain a `category` field, used to group the palette — a registry/schema addition, not a new endpoint
- Node/edge visual state derives from existing trace polling data already available client-side since SPEC-005 — this spec is presentation logic on top of existing data, not new backend work
- CSS variables defined once (e.g. `frontend/src/styles/tokens.css`), consumed throughout `canvas/` and `panels/` components

## 6. Acceptance criteria

- [ ] Every node type in the palette displays with an icon, title, and operation-style subtitle where applicable
- [ ] The node palette is organized into collapsible category sections, populated from registry metadata, expanding/collapsing with a smooth animation
- [ ] Triggering a real run visibly transitions each node through idle → running → success/error states, live, matching actual per-node completion order — verified against a real multi-node graph run, not a static screenshot
- [ ] Edges visibly indicate active data flow during a run and settle into a distinct completed/error state afterward
- [ ] `sub_node` edges (SPEC-012) are visually unmistakable from data edges at a glance, without needing to hover/click
- [ ] All form controls in node config panels (dropdowns, text inputs, toggles) use the custom-styled components, not unstyled browser defaults
- [ ] The entire canvas draws its colors/spacing from the defined token system — spot-check confirms no hardcoded hex values bypassing it in at least the primary node/edge/palette components
- [ ] Existing SPEC-005/012 functionality (drag, connect, run, inspect trace) still works correctly — this is a visual layer on top of existing behavior, not a functional rewrite, and must not regress it

## 7. Open questions

- Should the node palette support search/filter in addition to category browsing, given the list will keep growing (already at a dozen-plus node types)? Recommend: yes, include a simple text filter at the top of the palette now — cheap to add alongside the category work, and avoids a second spec just for this once the palette has 20+ entries.
- Should error-state nodes show the actual error message inline on hover, or only in the full trace inspector panel? Recommend: a short inline tooltip on hover for immediate visibility, full detail still lives in the trace panel — matches how the screenshot's own nodes surface just enough info at a glance without cluttering the canvas.
