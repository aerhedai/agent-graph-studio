---
description: Load a spec and implement it against this project's established conventions, pausing for approval before writing code
---

You are implementing a spec for Agent Graph Studio. The spec file path is provided as: $ARGUMENTS

If no path was provided, ask which spec before doing anything else.

1. **Read the full spec file**, plus `CLAUDE.md` at the repo root, before doing anything else. If the spec references prior ADRs or specs (its "Depends on" line), read those too.

2. **Confirm you're on the correct feature branch** (`git branch --show-current`) — it should match the branch created for this spec. If you're on `main` or an unrelated branch, stop and tell me rather than proceeding.

3. **Use plan mode.** Do not write any code yet. Produce a plan that explicitly:
   - Restates the spec's already-resolved design decisions (from its "Design decisions" / "resolved" sections) and confirms you will implement to them, not reopen them
   - Flags any part of the spec that involves a genuine architectural exception or a change to `backend/execution/engine.py` — per this project's convention, such changes need explicit justification, not silent inclusion
   - States exactly how you intend to satisfy each acceptance criterion, including which ones require a real, non-mocked live verification (per `CLAUDE.md`'s testing convention: anything touching the CLI, execution, or an external integration needs at least one real invocation demonstrated, not just mocked tests)
   - If the spec is large, proposes a phased breakdown rather than one single pass

   Show me this plan and stop. Do not proceed to implementation until I respond with approval.

4. **Once approved, implement in the phases you proposed**, pausing between phases for me to check in if the plan called for that.

5. **Before reporting anything as complete**:
   - Run the full test suite (`uv run pytest tests/ -v`) and show me the real output
   - For every acceptance criterion requiring live verification, actually perform it and show me the real command + output — do not describe what a live check "would show"
   - Show me `git diff main -- backend/execution/engine.py` regardless of whether you expect it to be empty, and explain any changes in it
   - If anything in the spec's "Open questions" section wasn't already resolved in the spec text itself, ask me before deciding it yourself

6. Do not commit or push anything as part of this command — leave the working directory as-is with changes ready for me to review. Committing happens after I've verified the work myself.
