---
description: Get main up to date, commit a new spec, create its GitHub issue + board card, and branch off for implementation
---

You are running the "start a new spec" workflow for Agent Graph Studio. The spec file path is provided as: $ARGUMENTS

If no path was provided, ask for it before doing anything else.

Follow these steps in order. Run each command with the Bash tool and check its actual output before moving to the next step — do not assume success.

1. **Sync main**
   ```
   git checkout main
   git pull
   ```
   Confirm this succeeded and there are no local uncommitted changes in the way (`git status` should be clean). If it isn't clean, stop and tell me what's uncommitted rather than proceeding.

2. **Add the spec to main**
   ```
   git add <the spec path>
   git commit -m "docs: add SPEC-NNN (<short title, derived from the spec's own H1>)"
   git push
   ```

3. **Read the spec's acceptance criteria section** (look for a "## Acceptance criteria" heading) and extract every `- [ ]` line verbatim — these become the issue's checklist.

4. **Create the GitHub issue**
   ```
   gh issue create \
     --title "Implement SPEC-NNN: <title>" \
     --body "Spec: docs/specs/<filename>

   ## Acceptance criteria
   <paste the extracted checklist here>"
   ```
   Capture the issue number from the output.

5. **Add it to the project board**, set status to "Spec'd". Use `gh project item-add` with the correct project number for this repo (check `gh project list` if you don't already know it) — then `gh project item-edit` to set the status field. If you're not certain of the exact project/field IDs, run `gh project view` first to look them up rather than guessing.

6. **Create and push the feature branch**
   ```
   git checkout -b feature/<short-kebab-case-name-matching-the-spec-topic>
   git push -u origin feature/<same-name>
   ```

7. **Move the board card to "In Progress"** via `gh project item-edit`.

8. **Report back to me**: the issue number, issue URL, branch name, and confirmation every step above actually succeeded (paste real command output, don't summarize it away). If any step failed, stop there and tell me exactly which one and why — don't try to silently work around it.
