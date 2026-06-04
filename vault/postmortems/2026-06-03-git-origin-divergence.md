---
title: Git origin divergence after duplicate-looking quest commit
date: 2026-06-03
area: Git
status: DONE
tags:
  - postmortem
  - git
  - repository
---

# Git Origin Divergence After Duplicate-Looking Quest Commit

## Summary

The local `main` branch appeared to be behind `origin/main` even though the repo is normally pushed outward to Codeberg and GitHub. The cause was not GitHub pulling anything unexpectedly. The local branch and remote branch had diverged because the same logical README/quest update existed as two different commits with different SHAs.

The issue was resolved by rebasing local `main` onto `origin/main`, then pushing the rebased branch to `origin`. Because `origin` has two push URLs configured, the push updated both Codeberg and GitHub.

## Impact

- Git reported remote-only history, which made the branch look behind.
- Local work was not lost.
- The branch history briefly contained a duplicate-looking commit message on both sides of the split.
- After rebase, local `main`, Codeberg, and GitHub were aligned at `403ca54`.

## Timeline

- Local `main` contained `68af508 feat(gpt5.5): better README project vision + create quests to meet it`.
- Remote `origin/main` contained `f86fb1a feat(gpt5.5): better README project vision + create quests to meet it`.
- Both commits had the same message, but different SHAs.
- The two commits differed only in `vault/HNTR Quests.base`.
- Local work continued on top of `68af508` with the HNTR-05 through HNTR-07 commits.
- `git rebase origin/main` replayed the local commits on top of `f86fb1a`.
- `git push origin main` updated both configured push URLs:
  - `https://codeberg.org/evillevi/hunter-agent.git`
  - `https://github.com/the-evillevi/hunter-agent.git`

## Root Cause

The repo's `origin` remote fetches from Codeberg but pushes to both Codeberg and GitHub:

```text
origin https://codeberg.org/evillevi/hunter-agent.git (fetch)
origin https://codeberg.org/evillevi/hunter-agent.git (push)
origin https://github.com/the-evillevi/hunter-agent.git (push)
```

Git compares local `main` against the fetched remote-tracking branch, `origin/main`, which comes from Codeberg. The local and remote histories had split at `e759571`, then each side received a similar README/quest commit independently.

That produced this shape:

```text
local:  68af508 feat(gpt5.5): better README project vision + create quests to meet it
remote: f86fb1a feat(gpt5.5): better README project vision + create quests to meet it
```

Same message, different content by three lines in `vault/HNTR Quests.base`, and different ancestry. Git correctly treated this as divergence.

## Resolution

The working tree was confirmed clean, then local `main` was rebased onto `origin/main`:

```sh
git rebase origin/main
```

The rebase completed without conflicts. The rebased commits became:

```text
403ca54 feat(hntr-07): render applications partial
bad628f feat(hntr-06): register applications router
07d4bbf feat(hntr-05): add list_applications
b1c01cc feat: refactor one model type per file
4b62df6 feat(gpt5.5): better README project vision + create quests to meet it
f86fb1a feat(gpt5.5): better README project vision + create quests to meet it
```

Then `main` was pushed:

```sh
git push origin main
```

Git reported successful updates to both Codeberg and GitHub:

```text
f86fb1a..403ca54 main -> main
```

## What Went Well

- The working tree was clean before rebasing.
- The divergence was diagnosed before pushing.
- The rebase completed without conflicts.
- Both configured push targets updated successfully.

## What Was Confusing

- `origin` means Codeberg for fetch, but both Codeberg and GitHub for push.
- GitHub looked like it was "ahead", but the remote-tracking truth came from Codeberg.
- The duplicate commit message hid the fact that the commits were different objects.

## Prevention

- Run `git log --oneline --decorate --graph --all --max-count=20` when "behind" does not make intuitive sense.
- Check `git remote -v` before assuming which host `origin/main` represents.
- Prefer one canonical fetch remote for branch status, and document that Codeberg is canonical if that remains true.
- Consider setting upstream tracking for `main` so `git status --branch` reports ahead/behind directly:

```sh
git branch --set-upstream-to=origin/main main
```

- Avoid recreating equivalent commits on different machines or hosts. Fetch first, then rebase or merge before adding more work.
