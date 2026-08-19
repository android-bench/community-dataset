# Getting a task out of working code

The repo is the *after* state. A task needs a *before* state, so you make one by taking something
away. Four ways, in order of how well they usually work.

| Mechanism | Before state | Best when |
|---|---|---|
| **Deferred work** | Unchanged — it was never built | The repo records something an earlier attempt could not finish |
| **Feature extraction** | A capability removed, a thin seam left | A capability sits behind a real boundary |
| **Bug injection** | A plausible defect written in | The logic is dense enough that diagnosis is the work |
| **Greenfield** | An empty or skeleton project | The implementation cannot be separated from the repo, so make its absence the task |

Before judging any of them, read `sanitizing.md`. Most repos need a pass first — provenance traces
removed, documentation trimmed back to what a real codebase would carry, design-announcing names
renamed. A candidate that looks unusable often just needs that pass.

---

## 1. Deferred work

The strongest source, because the difficulty is already measured and nothing solved it. Look for:

- `TODO.md`, `## Deferred`, `## v2`, `## Known issues`, `## Won't fix`, change-request documents
- `git log --grep -iE 'defer|known issue|revisit|left as'`
- `@Ignore`d tests, disabled test classes, tests asserting the wrong-but-current behaviour
- ADRs recorded as rejected or postponed
- Tracker issues labelled later, wontfix, help wanted

Why it beats the other two: nobody has solved it, so the difficulty claim is honest; the diagnosis
is usually written down, so you can price it; and if a previous coding agent explicitly gave up,
that is the strongest available evidence an agent will fail.

**The catch is the same thing that makes it attractive.** The document recording the deferred item
usually contains the diagnosis and often the remedy. It has to go — see `isolation.md`. If deleting
it makes the task impossible rather than hard, the document was the task, and the candidate is
weaker than it looked.

### The disposition note is a hypothesis, not a verdict

Deferred items come with a stated reason for deferring: *needs the requirement owner · deliberately
not fixed · no single correct answer · out of scope for v1*. Those record what that team decided
about their own process. They are not findings about whether a benchmark task exists, and adopting
them unexamined is how a viable candidate gets killed on paper.

The distinction that matters: **an undetermined policy is not an undetermined outcome.** A team may
genuinely not know how aggressively to tune something, while a pass/fail invariant still exists
that every acceptable answer satisfies. "Suppress the drop across the whole rate band, keep the
real fall detected" grades cleanly no matter which tuning philosophy wins.

So before rejecting a deferred item:

1. Reproduce the defect. Run it, measure it, find the boundary.
2. Establish headroom with a control — show the behaviour you must preserve still works.
3. Try to state an invariant that holds regardless of the undecided policy.
4. Reject only if step 3 fails.

If the only citation for a rejection is a document in the repo, the rejection is unearned. This is
the same discipline applied to difficulty claims, pointed the other way.

---

## 2. Feature extraction

Delete a capability, keep a seam, make the agent build it back from the requirement.

### What makes a feature extractable

| Property | Why it matters |
|---|---|
| Sits behind a real boundary | You can delete the body and leave a compiling type |
| Callers use the boundary, not the internals | Call sites do not encode the design |
| Behaviour is observable at the boundary | Hidden tests can assert without naming internals |
| Its tests are separable | They move wholesale into `test.patch` |
| Its documentation can be trimmed or relocated | Heavy docs are a cost, not a disqualifier — see `sanitizing.md` |

### Where the seam usually is in Android

| Seam | Extract by |
|---|---|
| Interface + Hilt binding | Delete the implementation; keep the interface and the binding, pointing at a stub that throws |
| Gradle module | Empty `src/main`; keep `build.gradle.kts` and the entry in `settings.gradle.kts` |
| `ServiceLoader` / factory SPI | Delete the provider; keep the service interface and the `META-INF/services` declaration |
| Sealed result type | Keep the type hierarchy; delete whatever produces it |
| Repository over a data source | Keep the repository interface; delete the implementation and its mapping layer |
| `WorkManager` worker | Keep the enqueue call and the output-data contract; delete the `doWork` body |

Leaving a stub that throws is better than leaving nothing: the project compiles, the existing
suite runs, and the contract is unambiguous without being a design document.

### Sizing: combine for coupling, not for volume

One feature rarely reaches 24 hours. Two features do — but only if they are **coupled**.

- Two independent features are two easy tasks stapled together. An agent does them in sequence.
- Two features sharing a seam force the agent to design the interaction, and the tests can assert
  on the interaction rather than on each piece.

So extract along a dependency, not across a list. Detector plus the policy that consumes its
output. Cache plus the invalidation that depends on its eviction order. Migration plus the reader
that must keep working across it.

### What not to extract

- The most documented capability in the repo — you will lose the fight with the docs
- Anything whose implementation is reachable in a public upstream or a published artifact
- Something reconstructible from its surviving call sites
- Boilerplate: DI wiring, navigation graphs, generated bindings. Large diff, no thinking
- Anything an agent authored — check `Co-Authored-By` trailers first

---

## 3. Bug injection

Write a defect a competent developer could plausibly have written, and state the symptom as an
issue. The work is diagnosis, so this suits dense logic where the patch is small and finding it
is not.

### Defects that make good tasks

| Defect | Why it works |
|---|---|
| Wrong boundary among several interacting thresholds | Fixing one in isolation breaks another |
| Lifecycle mismatch — register in `onCreate`, release in `onDestroy` | Only shows after a configuration change or a second entry |
| Missing cancellation, or work in the wrong scope | Surfaces as a leak or a stale emission, far from the cause |
| Equality on an array field in a `data class` | Recomposition or diffing misbehaves; the cause is three layers away |
| `SharedFlow` replay or buffer set wrong | Depends on subscription order |
| Off-by-one in a ring buffer or window | Correct almost always |
| A precision or rate assumption | Correct on your device, wrong on others |

### Defects that do not

- Anything the compiler, lint or detekt flags
- Deleted null checks, inverted booleans, hardcoded returns — vandalism, not a bug
- A one-line revert visible on inspection
- A symptom you cannot state without naming the cause

### The two diagnostics that decide it

**If an existing test catches your injected bug**, that test must move into `test.patch`. Otherwise
the before commit fails its own suite, which is a hard rejection.

**If no existing test catches it**, ask why before celebrating. Usually it means the behaviour is
not observable at any seam — in which case you cannot grade it either.

---

---

## 4. Greenfield

Before is an empty or skeleton project; after is the implementation. Reach for it when the answer
cannot be separated from the repo — the design lives in a contract the build requires, a sibling
module implements the same thing, the spec is load-bearing. Rather than fight every channel, remove
the codebase and keep the requirement.

Nothing leaks, because nothing is there. That is the whole appeal.

**What the skeleton must carry**, or the hidden tests cannot compile against anything:

- The module structure and `settings.gradle.kts` wiring
- The dependency set and version catalog, so library choice is not the task unless you want it to be
- The seam the tests drive — an interface, a sealed result type, an entry point
- Enough of a build to run: manifest, application class, DI graph

**What makes it hard rather than tedious.** Greenfield goes wrong when it becomes "type out an app".
It works when the requirement carries constraints an implementation must satisfy and a test can
check: behaviour under configuration change and process death, a throughput or allocation budget,
determinism across sample rates, data written by an earlier version that must still read back. The
task is then a design problem with a measurable outcome, not a transcription.

**The risk to watch.** Greenfield diffs are all additions of new independent code, which the
difficulty rubric treats as a weak signal. Compensate by making the requirement, not the volume,
the hard part — and expect to argue that in the proposal.

## Judging a candidate

Ask these in order and stop at the first no.

1. Can the answer be isolated? (`isolation.md` — usually the binding constraint)
2. Is it more than 24 hours of senior work? (`difficulty.md`)
3. Can hidden tests grade it without naming symbols the agent invents? (`test-quality.md`)
4. Does the before state build and pass its own suite?
5. Was the code written by an agent? Not a stopping condition — run the four checks in
   `sanitizing.md`. What that agent could not finish is usually the best candidate in the repo.

Then reproduce it. Run the code, apply the extraction or the bug, watch the failure. An argument
built from the repo's documentation is unverified — and the agent reads that document too.

## Presenting candidates

A table, ranked, then a question. Do not write an essay per candidate.

| Candidate | Mechanism | Files | Senior hours | Isolation | Verdict |
|---|---|---|---|---|---|
| Rate-dependent drop veto | Deferred | 6 across 3 modules | 14–20 | Hard — spec doc must go | Best |
| Calibration module | Extraction | 4, seam exists | 20+ | Clean | Viable |
| Room v4 migration | Extraction | 5 | 6–10 | Clean | Below bar |

Ask which one to build, and offer to record the rest in `android-bench/CANDIDATES.md` as a
backlog. Then go straight to the plan — picking is the decision.
