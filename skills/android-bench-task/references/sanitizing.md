# Sanitizing the repository

Most repos are not ready to be a task, and the reasons are fixable. Sanitizing is the pass that
turns a leaky, agent-authored, over-documented tree into a before state that looks like ordinary
production code.

**Never a rejection when it can be a fix.** "The spec is in the repo" is not a verdict — a spec can
be trimmed, relocated, or moved out of code entirely. "The commits say an agent wrote it" is a
provenance problem with a provenance fix. Reach for a mitigation before you reach for a no.

Sanitizing changes the before state only. Nothing here touches the author's working branches — it
all happens on the throwaway branch or in the orphan import, and the author runs every command.

## What a real repository looks like

The target is not a clean repo. It is a *typical* one. Agents are being measured on the work
developers actually hand them, and that work arrives as a vague ticket against a codebase whose
conventions live in the code.

| Real repos have | Benchmark-spoiling repos have |
|---|---|
| Conventions you infer from neighbouring code | A `CONVENTIONS.md` listing them |
| Architecture you read off the module graph | An ADR explaining the intended design |
| Library choices visible in the version catalog | A doc explaining which library to use and why |
| Terse or absent KDoc | KDoc stating the algorithm |
| A ticket describing a symptom | A spec describing the solution |

Stripping documentation is not vandalism here. It restores the condition the benchmark is trying to
measure: **can the agent derive architecture, naming and library choice from the codebase?** A repo
that answers those questions in prose has removed the interesting part of the job.

Judgement call worth stating to the author: the community-dataset rubric requires the *instruction*
to be well-specified, leaving nothing to guessing. That is about the issue text, not the tree. Keep
the required **outcome** unambiguous, so it can be graded; leave the **method** undetermined, so
there is something to solve.

## When an agent wrote the repository

```bash
git log --format='%(trailers:key=Co-Authored-By,valueonly)' | sort | uniq -c
git log --format='%an <%ae>' | sort | uniq -c
```

**This is a question, not a blocker.** "An agent built it, so an agent can solve it" is a tempting
inference and usually wrong, because the two situations are not comparable. The agent that built it
had a human-written specification up front, dozens of correcting turns, no isolation requirement,
and as much wall-clock as it needed. A benchmark agent gets an issue and a tree.

The real question is narrower: **would an agent reproduce this from the requirement alone, with the
spec removed?** Four steps to find out, in order.

**1. Check whether it was actually one-shot.** Volume of correction is the signal.

```bash
git log --oneline -- <feature paths> | wc -l
git log --oneline --grep -iE 'fix|revert|actually|correct|regress' -- <feature paths>
git log --diff-filter=M --format='%h' -- <one core file> | wc -l
```

Forty commits with reverts and follow-up fixes is not a one-shot. Neither is a feature trailing a
deferred list, open change requests, or unticked done-criteria.

**2. Find what does not work.** This is the most productive half hour available, and it inverts the
whole picture: every broken behaviour is a place the agent *failed*. Deferred items, known bugs,
`@Ignore`d tests, unticked DoD boxes, TODO entries with measurements attached. Those are candidates
with proven difficulty and clean provenance — nobody solved them.

**3. Remove the over-information and re-test.** The spec is what made it easy. Draft the instruction
without it, hand the sanitized before state to a strong model, and watch. If it still lands the
patch, the candidate is genuinely easy and the trailers were never the reason. If it does not,
agent-authored history is irrelevant and you have a task.

**4. Harden if it is still short.** Inject a defect into the dense part, add a requirement with
lifecycle or concurrency coupling, widen the regression net. See `difficulty.md`.

Only after all four does "too easy" become a finding — and even then it is a finding about *this
candidate*, not about the repository.

The trailer itself is a separate, cosmetic problem, but reviewers read it as a signal about the
whole submission. Fix it in the before state:

| Trace | Action |
|---|---|
| `Co-Authored-By:` trailers | Rewrite them out. An orphan import drops all history at once, which is simpler and also closes the ancestry leak |
| `CLAUDE.md`, `AGENTS.md`, `GEMINI.md`, `.cursorrules`, `.claude/`, `.github/copilot-instructions.md` | Delete |
| Commit messages written in agent voice | Rewrite when squashing, or use a single import commit |
| Session logs, handoff notes, `PROGRESS.md`, kickoff prompts | Delete |

Be honest with the author about the limit: rewriting trailers changes how the history reads, not
who wrote the code. If the feature itself was agent-built, pick a different candidate.

## The documentation pass

Three moves, in increasing order of effort. Prefer the cheapest that closes the channel.

**Trim.** Keep the sentence that states the requirement, cut the one that states the mechanism.
A spec section describing *what a fall detector must not do* can stay; the table of weights and the
ramp formulas cannot.

Trimming is per-occurrence judgement, so an instruction to trim is useless without a worked pair
lifted from their repo. Not "strip the mechanism KDoc" — this:

```kotlin
// before
/** Verdict. CLASSIFIED_AS_DEVICE_DROP when jerk > 8.2 and the post-impact window stays under
 *  0.4g for 900ms — see docs/03 §4.2. */
// after
/** Verdict. CLASSIFIED_AS_DEVICE_DROP when the motion is the device falling, not the wearer. */
```

Beyond ~15 files this is a prompt to delegate, not an edit to hand over.

**Relocate out of code.** Values that only exist in the repo because it was convenient can move to
where they would live in a real product — a config file, a resource, a seeded database, a fixture
the app reads at startup, or a service the code calls. This is closer to reality than a constants
file with defaults, and it moves the design decision out of the compile surface.

This is the answer when a leak looks unfixable because the code will not build without it. A
`ScoringProfile` whose eighteen weights are declared as defaults in a frozen contract hands over the
design — but a `ScoringProfile` loaded from a config the agent can see the *shape* of, without the
values being the design, does not.

**Rewrite.** For KDoc on a surviving seam, and for names. See the naming rules in `isolation.md`.

## The build pass — strip the gates

A mature repo's build carries hygiene machinery: traceability guards, purity checks, lint, detekt,
ktlint, coverage verification, screenshot comparison, API-dump checks, custom `Exec` guard scripts.
None of it belongs in a before state. **Delete it from the build files, not just from the graded
command.**

```bash
git grep -nE 'guard[A-Z]|check-.*\.sh|detekt|ktlint|kover|roborazzi|paparazzi|apiCheck|dependsOn\("check"\)|tasks\.named\("check"\)' -- '*.gradle.kts' '*.gradle'
```

Three reasons, and the first is the one people miss:

- **They block sanitizing.** A `guardTraceability` task that regenerates a doc from source
  annotations fails the moment you delete `docs/`. The reflex is to keep the doc; the right move is
  to delete the gate. You are not maintaining this repo, you are freezing one commit of it.
- **They fail correct solutions.** A 90% coverage threshold punishes a fix that adds one untested
  branch. Detekt punishes a style the instruction never mentioned. Screenshot goldens punish a
  different renderer.
- **They encode conventions the instruction never states**, which is the definition of an unfair
  verifier.

What survives: compiling the project, and the one test task that grades the work. Everything else
goes. Check afterwards that `assemble` and the graded task still run — removing a task that others
`dependsOn` breaks configuration.

Keep the deletions in the before state only. The author's repo keeps its gates; this is a
throwaway commit.

## The naming pass

```bash
git grep -nE 'Legacy|Naive|Unsafe|Unsecure|Insecure|Temp|Workaround|Broken|Deprecated|Old[A-Z]|V1\b|TODO|FIXME|HACK'
```

Any name that advertises the missing work has to go. `UnsecureGateApi` tells the agent both where to
look and what the fix is. So does a method named `scoreThinPulse` in a class the agent is supposed
to design.

## When the repo is genuinely too easy

Not every sanitized repo yields a task. When it does not, the answer is still concrete: propose what
to *build* so that a task exists later. Name three specific things, each with a sentence on why an
agent would fail it — a feature with real lifecycle or concurrency coupling, a migration that must
preserve shipped data, or a defect worth injecting into code that does not exist yet.

Vague advice ("write something harder") is not an answer. If the author has to invent the idea
themselves, the skill did nothing.

## Output

A sanitization checklist, as imperatives, ordered, each naming a real path:

- delete `app-project/docs/`, `CLAUDE.md`, `HANDOFF.md`, `.claude/`, `_to_delete/`
- trim `Engine.kt` KDoc: keep the contract sentence on `FallVerdict`, cut the mechanism note on
  `CLASSIFIED_AS_DEVICE_DROP`
- move the eighteen `ScoringProfile` defaults into `config/scoring.json`, loaded at startup
- rename `DropVetoScorer` → `EvidenceScorer`
- import as an orphan commit, which drops every `Co-Authored-By` trailer at once

Then re-run the residue check in `isolation.md`. Sanitizing changes the tree, so the sweep has to
happen after it, not before.
