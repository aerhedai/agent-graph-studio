---
description: Commit verified work, open a PR with the acceptance checklist, and (after my explicit confirmation) merge and clean up
---

You are closing out a feature branch for Agent Graph Studio, after I have already personally verified the work myself. Do not treat "tests pass" as equivalent to my verification — only proceed past step 3 once I explicitly confirm I've reviewed and verified it.

1. **Confirm current branch and status**
   ```
   git branch --show-current
   git status
   ```
   Show me both. Confirm this matches the feature branch for the spec we're closing out.

2. **Run the full test suite one more time** and show me real output, even if it was already run earlier in this session — confirm nothing changed since.

3. **Stop here and ask me directly**: "Have you personally verified this work, including any live/non-mocked checks the spec required? Confirm before I commit and open a PR." Do not proceed until I give explicit confirmation. If I raise any concern instead of confirming, address that first and return to this checkpoint.

4. **Once I confirm, commit and push**
   ```
   git add .
   git commit -m "<a real, descriptive conventional-commit message covering what was actually built, not a generic message>"
   git push
   ```

5. **Open the PR**
   ```
   gh pr create \
     --base main \
     --title "<matches the issue title>" \
     --body "<short summary of what was built, plus the issue's acceptance checklist with items ticked based on what was actually verified, plus 'Closes #N', plus an explicit note of any known/deferred gaps>"
   ```

6. **Show me the PR URL and stop.** Do not merge yet. Ask me to confirm I've reviewed the diff in GitHub's PR view.

7. **Only after I explicitly confirm the PR review**:
   ```
   gh pr merge --squash
   ```

8. **Update the project board**: move the card through "In Review" to "Done" (or directly to Done if merge auto-closes the issue and your board automation already handles that — check first rather than double-moving it).

9. **Clean up**
   ```
   git checkout main
   git pull
   git branch -d <the feature branch>
   ```

10. Confirm the issue is closed (merge with "Closes #N" should do this automatically — verify with `gh issue view <N>` rather than assuming).
