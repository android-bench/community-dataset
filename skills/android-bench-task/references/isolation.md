# Isolating the fragment

The agent gets the whole before-state tree, a shell, and internet access. Everything you leave
behind is a channel back to the answer. This is the step that decides whether the task measures
anything, and it is harder than picking the candidate.

## The test to hold yourself to

> Hand the before state to a competent Android developer who has never seen your repo. They should
> be able to state **what is required**. They should not be able to state **how you built it**.

If they can describe your design from the before state, you have shipped a specification and the
task grades transcription.

## What the container actually protects

The generated Dockerfile clones the repo, checks out the before commit, then prunes: `git reset
--hard`, `git clean -fd`, `git remote remove origin`, delete other local branches, delete tags
newer than the before commit, expire the reflog, `gc --prune=now`. With
`[repository].remove_from_git_history` it also runs `git filter-branch --index-filter` over `--all`.

| Protected | Not protected |
|---|---|
| The remote — no `git fetch` back to your branches | **Ancestry.** Every parent of the before commit survives |
| Sibling branches and newer tags | Anything reachable on the public internet |
| Paths listed in `remove_from_git_history` | Anything you left in the working tree |

`remove_from_git_history` removes *paths* from history. It does not remove *commits*. If the
implementation exists in an ancestor commit, `git log -p` still finds it.

## The channels

Work every row against the actual before state.

### Documentation and KDoc

Specs, ADRs, design docs, READMEs, and KDoc on the surviving seam. The single most common leak,
because the most interesting feature is usually the best-documented one.

```bash
ls -1 CLAUDE.md AGENTS.md GEMINI.md .cursorrules .github/copilot-instructions.md 2>/dev/null
find . -path ./.git -prune -o -name '*.md' -print | grep -iE 'doc|adr|design|spec|rfc|todo|handoff'
git grep -n -iE '<algorithm name>|<the API you expect>|<distinctive term>'
```

Close it by deleting the file from the before state, or listing it in
`[repository].remove_from_git_history`. Prefer deleting — it is total and visible in review.

KDoc on the seam needs surgery, not deletion. Keep the contract sentence, cut the mechanism.

### Names

The user-facing example of the failure: an interface called `UnsecureGateApi`. The name announces
both the defect and the fix.

```bash
git grep -nE 'Legacy|Naive|Unsafe|Unsecure|Insecure|Temp|Workaround|Broken|Deprecated|Old[A-Z]|V1\b|TODO|FIXME|HACK'
```

**Rename anything that describes the missing work.** A stub named `DropVetoScorer` with a method
`scoreThinPulse(widthNanos)` is a design document with a `.kt` extension.

### The seam itself

The rule: **name the obligation, not the method.**

| Leaks the design | States the requirement |
|---|---|
| `DropVetoScorer.scoreThinPulse(widthNanos, saturationCount)` | `FallDetectionEngine.onSample(s): FallVerdict` |
| `CacheEvictor.evictLeastRecentlyUsed()` | `Cache.put(k, v)` with a documented size bound |
| `RetryPolicy.exponentialBackoff(base, jitter)` | `Client.send(req): Result` that must survive flaky transport |

Every parameter name, every enum constant, every sealed subclass is part of the seam. An enum
`SuppressionReason.THIN_PULSE_BELOW_THRESHOLD` tells the agent the discriminator.

### Tests and fixtures left behind

Test names describe behaviour. Fixtures define what the inputs *mean*.

```bash
git grep -n 'fun .*<behaviour keyword>' -- '*Test.kt'
git ls-files | grep -E 'testFixtures|/fixtures/|golden|testdata'
```

Move the revealing ones into `test.patch`. Remember that `test.sh` must wipe fixtures and helpers
too, not just `*Test.kt` — anything the tests read is part of the verifier, and an agent that
edits a fixture rewrites the premise.

### Call sites

Twenty call sites with descriptive argument names reconstruct the design without any docs.

```bash
git grep -n '<seam type or method>' -- '*.kt' | grep -v Test
```

If the callers encode the design, either the seam is at the wrong level or the candidate is not
extractable.

### Sibling implementations

A parallel module doing nearly the same thing — a test harness, a reference implementation, a
sample app, a KMP twin, an old module kept for comparison.

```bash
git grep -l '<distinctive symbol>' | cut -d/ -f1-2 | sort -u
```

This is why the task repo should hold the fragment's project only, not the whole monorepo.

### Generated files and schemas

Room schema JSONs record the exact table and column design. Binary-compatibility `.api` dumps
record the full public surface. Also: ProGuard rules naming classes, `META-INF/services` entries,
generated navigation args, lockfiles pinning a giveaway dependency.

```bash
git ls-files | grep -E '\.api$|schemas/.*\.json$|proguard.*\.pro$|META-INF/services'
```

### Packaged copies in the tree

Tarballs, AARs, JARs, bootstrap archives, `_to_delete/` directories. They are opaque to grep and
routinely contain a verbatim copy of what you just deleted.

```bash
git ls-files | grep -iE '\.(tgz|tar\.gz|zip|jar|aar|apk)$'
```

### Git ancestry

If the implementation exists in any ancestor commit, `git log -p` recovers it. `remove_from_git_history`
does not help — it strips paths from every commit, so the file is gone, but so is the history you
wanted to keep for realism.

```bash
git log --oneline --all -S '<distinctive symbol from the implementation>'
git log --oneline -- <path you deleted>
```

Two ways out:

- **Orphan commit.** Publish a single root commit into a fresh public task repo. Total, and it
  also solves a private source repo. Cost: `git log` shows one commit, which is itself a signal.
- **Branch from before the feature landed.** Honest history, but the whole repo is frozen at that
  point, and later ancestors may still leak related work.

### The public internet

The agent has internet and the repo URL is in the Dockerfile.

- If the source repo is public and contains the implementation, the agent can clone it. Publish the
  before commit from a **separate task repo**, not a branch of the source repo.
- **Never push the after commit anywhere.** It ships as `solution.patch` in the community-dataset
  PR, canary-guarded — that is the published form of the solution, and the corpus relies on the
  canary rather than on the patch being hidden. A branch on the task repo is different in kind: that
  URL is in the agent's Dockerfile, so it is one `git clone` away rather than a search away.
  `refresh-patches` reads both SHAs from a local staged clone, so nothing needs it on a remote.
- If the library is published to Maven Central or JitPack, the implementation is downloadable and
  often decompilable. Check.
- If the source repo is a fork or a mirror of something public, check the upstream too.

### Comments and commit messages

Surviving comments explaining why a threshold is what it is. And the before commit's own message:
`"remove drop veto scoring for benchmark task"` gives the whole game away. Write before-state
commit messages as if they were real work.

## Residue sweep

After building the before state, before recording its SHA. Pick three or four identifiers that
only appear in the removed implementation, then:

```bash
git grep -nI '<identifier>' $(git rev-parse HEAD)   # working tree
git log --all -S '<identifier>' --oneline           # history
git ls-files -z | xargs -0 grep -lI '<identifier>'  # belt and braces
```

Zero hits, or the extraction is decorative.

## Verify inside the container

The only check that reflects what the agent sees. After `v2.task docker`:

```bash
docker run --rm -it <image-tag> bash -lc '
  git log --all --oneline | head -20
  git branch -a; git remote -v
  ls <path you deleted> 2>&1
  grep -rn "<distinctive identifier>" . --include=*.kt --include=*.md | head
'
```

Expect: no remote, no branch or tag pointing at the solution, deleted paths absent, identifier not
found.

## When a channel looks unclosable

Almost never a reason to drop the candidate. Work down this ladder and stop at the first rung that
holds. "Cannot be isolated" is a verdict you earn at the bottom, not a first reaction.

| Rung | Move | Use when |
|---|---|---|
| 1 | **Delete** | The file is not needed to build or to state the requirement |
| 2 | **Trim** | The file must stay, but only part of it leaks. Keep the requirement sentence, cut the mechanism |
| 3 | **Relocate** | The values must exist somewhere. Move them out of code into a config file, resource, seeded database or service the app reads — where they would live in a real product anyway. See `sanitizing.md` |
| 4 | **Rewrite** | A name or a KDoc states the design. Rename, reword |
| 5 | **Move to `test.patch`** | A test or fixture describes the missing behaviour |
| 6 | **Narrow the fragment** | The leak covers the whole feature but not the sub-behaviour you actually want graded |
| 7 | **Change the task shape** | Everything leaks because the implementation is the repo. Then make the *absence* the task: before is an empty or skeleton project, after is the implementation. See the greenfield mechanism in `extracting-tasks.md` |

Rung 3 is the one people miss. A constant declared with a default in a file the build requires looks
immovable — but specifications do not normally live in source. They live in a product doc, and the
code reads values from somewhere. Moving eighteen scoring weights into a config the agent can see
the *shape* of, without the values being the design, closes the channel and makes the repo more
realistic at the same time.

Rung 7 is the other one. If the answer cannot be separated from the repo, invert it: an empty
project plus a requirement is a legitimate and often very hard task, and nothing leaks because
nothing is there.

Reject only when every rung fails — and say which ones you tried, so the author can disagree.
