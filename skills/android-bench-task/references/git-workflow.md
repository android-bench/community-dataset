# Building the two commits

A task is two commits:

- **before** — where the agent starts. Builds cleanly, its own test suite passes, contains all
  setup that is not part of the problem.
- **after** — the reference solution plus the hidden tests. Builds cleanly, everything passes.

`solution.patch`, `test.patch`, the Dockerfile checkout and the F2P/P2P sets are all derived from
those two SHAs.

Put every command in the plan with real values substituted. The author runs them.

## Rule zero: work in a worktree

A worktree is a second checkout sharing the same object store. Their current branch and working
tree are untouched.

```bash
git status --short                                       # expect no output
git worktree add ../bench-<task-id> -b bench/<task-id>/before <base-sha>
cd ../bench-<task-id>
```

Pick `<base-sha>` deliberately: a commit where the build is green and the suite passes. **Pin a
SHA, never a branch tip** — tips move, sometimes while you are working.

Cleanup once the SHAs are recorded:

```bash
git worktree remove ../bench-<task-id>     # branches survive; only the checkout goes
```

## Choosing the publishing shape

Isolation decides this, not convenience. Read `isolation.md` first.

| Shape | Use when | Cost |
|---|---|---|
| **Orphan commit in a fresh task repo** | The history, a sibling module, or the public source repo leaks the answer. Also when the source repo is private | `git log` shows one commit — itself a signal |
| **Revert on a branch of the source repo** | History is clean and the repo is already public | Ancestors remain walkable |
| **Branch from before the feature landed** | The feature is recent and self-contained | The whole tree is frozen at that point |

Default to the orphan commit. It closes the ancestry channel completely, and the container's prune
step does not.

### Orphan commit

Create the worktree with `--detach`, not `-b` — `--orphan` refuses to create a branch that already
exists.

```bash
git worktree add ../bench-<task-id> --detach <base-sha>
cd ../bench-<task-id>
git checkout --orphan bench/<task-id>/before
```

`--orphan` keeps the working tree and leaves every file staged. **Do not run `git rm -r --cached .`**
— it empties the index, `git add -A` puts it straight back, and any later `git rm <path>` fails with
*did not match any files*. Delete with plain `rm`.

```bash
rm -rf <leaked docs, sibling modules, tarballs>
# strip the extraction, rename the tells
git add -A
git commit -m "<a message that reads like real work, not like a benchmark setup>"
git log --oneline          # exactly one commit
```

Then publish into a **new public repo** that holds only this. A fresh repo matters: pushing the
orphan branch alongside the original history leaves the ancestors reachable, which is the thing you
just spent effort removing.

```bash
git remote add task git@github.com:<user>/<task-repo>.git
git push task bench/<task-id>/before:main
```

Record in the task's `README.md` which source commit it corresponds to. Nobody will reconstruct
that later.

### Revert on a branch

```bash
git log --oneline --follow -- <path/to/feature/...>
git log --oneline -S '<distinctive symbol from the feature>'

git worktree add ../bench-<task-id> -b bench/<task-id>/before <recent-green-sha>
cd ../bench-<task-id>
git revert --no-commit <oldest-feature-sha>^..<newest-feature-sha>
# resolve anything the revert could not undo cleanly
git commit -m "<message that reads like real work>"
```

## Building the before state

The revert only handles the mechanical part. The rest depends on the mechanism.

**Feature extraction** — delete the implementation, leave a compiling seam. A stub that throws
beats an empty file: the project compiles, the suite runs, the contract is unambiguous.

**Bug injection** — write the defect, then move every existing test that catches it into
`test.patch`. Otherwise the before commit fails its own suite, which is a hard rejection.

**Deferred work** — nothing to remove. The before state is the current code minus the documents
that record the diagnosis.

All three then need a **scaffolding commit** for anything the solution needs that is not the
problem: dependencies, plugins, version-catalog entries, empty modules wired into
`settings.gradle.kts`, test infrastructure (fixtures, fakes, `MainDispatcherRule`, MockWebServer),
and the seam the hidden tests compile against. The acceptance checklist requires this setup to be
present in the before commit.

```bash
git add -A && git commit -m "<setup message>"
```

Then the isolation sweep and residue check from `isolation.md`. Any deletion or rename changes the
SHA, so sweep before recording it.

## Verify the before state

Hard acceptance gate. Run it; do not infer it from documentation.

```bash
./gradlew clean assembleDebug <the narrow test task>
```

`BUILD SUCCESSFUL`, no failures. A red before commit fails review immediately.

## Build the after state

```bash
git switch -c bench/<task-id>/after
```

Cherry-pick the original work, or re-implement it, then write the hidden tests.

```bash
git cherry-pick <oldest-feature-sha>^..<newest-feature-sha>   # if reverting
git reset --soft bench/<task-id>/before && git commit -m "..." # to squash into one clean commit
```

Verify the same way: builds, and every test passes, old and new.

## Publish and back up

**The before commit is published. The after commit is not.** Two artifacts, two homes:

| State | Where it lives | Why |
|---|---|---|
| before | a public task repo, `main` | the container clones this URL and checks out the SHA |
| after | your machine only — never pushed | it ships as `solution.patch` in the community-dataset PR, canaried |

`refresh-patches` resolves both SHAs, but it does so in `tasks/<id>/environment/staged-repo`
(`common.py:154`) — a local clone on your machine. Both commits being present *there* is what it
needs; neither has to be reachable from a remote. The published answer is the patch, not a branch.

```bash
git push task bench/<task-id>/before:main
git bundle create ~/bench-<task-id>.bundle bench/<task-id>/before bench/<task-id>/after
```

Keep that bundle. It is the only copy of the after state outside your working tree, and losing it
means rebuilding the solution from `solution.patch`.

**Canary the after state before generating the patch.** `solution.patch` is public in the PR, and
the canary lines only appear in it if they were in the files when the diff was taken. Run this on
the after branch, then regenerate:

```bash
git switch bench/<task-id>/after
scripts/add-canary.sh <before-sha> <after-sha>   # every file the solution touches
git commit -aqm "chore: canary the reference solution"
```

Record both SHAs:

```bash
git rev-parse bench/<task-id>/before
git rev-parse bench/<task-id>/after
```

Tag the before commit so it survives garbage collection. **Give the tag a different name from the
branch** — identical names make `git push task bench/<task-id>/before` an ambiguous ref.

```bash
git tag -a bench-<task-id>-before -m "<task-id> before state" bench/<task-id>/before
git push task bench-<task-id>-before
```

Tag the after commit locally too, so a stray `git branch -D` cannot lose it, but do not push it:

```bash
git tag -a bench-<task-id>-after -m "<task-id> after state" bench/<task-id>/after
```

Confirm the remote carries the before state and nothing else — the agent has internet and this URL:

```bash
git ls-remote --heads --tags task
# expect refs/heads/main and refs/tags/bench-<task-id>-before, nothing more
```

## Freeze

Once a SHA is in `task.toml`, that commit is frozen. Rebasing, amending, force-pushing or deleting
the branch breaks the published task silently, some time after it merged. To change anything: make
a new commit, update the SHA, re-run `v2.task refresh-patches` and `verify-tests`.

## Private source repos

`v2.task docker --clone` stages the repo into `environment/` and the generated Dockerfile `COPY`s
it instead of cloning. The source then ships inside the task directory — check the licence, and
check the staged history for anything confidential.

The orphan-commit shape usually solves this better: publish only the pruned before state, and the
private source repo never leaves the author's machine.

## Record what you did

The task's `README.md` is for reviewers and for future-you: which source commit the before state
came from, what was removed or injected and why, and the exact commands. Six weeks later, when a
maintainer asks why the before commit lacks three unrelated things, that note is the difference
between an answer and an archaeology session.
