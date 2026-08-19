# Clearing the bar

**A senior Android developer needs more than 24 hours, and Gemini 3.0 Flash fails it.**

The author will overestimate their own code, because they are pricing in the time it took them to
learn the domain. An agent starts with the whole tree, internet access and no fatigue.

Saying "nothing here clears it" early is the most valuable output of this step.

## Two calibration questions

**Could a competent Android developer who has never seen this repo produce a correct patch in one
sitting, given only the instruction?** If yes, it is a sub-4-hour task whatever the line count.

**What must the solver discover that the instruction does not state?** Not withheld information —
derived understanding. If the answer is "nothing, they just have to type it", volume will not save
it.

## Too-easy detectors

Any one disqualifies the candidate.

| Signal | Why |
|---|---|
| One file, or one function | The rubric requires multiple logic files |
| Mostly additions of new independent code | Hard tasks require modifying code others depend on |
| Version bump, dependency add, config flag, annotation | Not engineering |
| Under ~30 lines of localised non-test logic | The published dataset's median is 32; community tasks must beat it |
| A documented API call is the whole answer | `enableEdgeToEdge()`, `collectAsStateWithLifecycle()` — agents have read that page |
| A published migration guide applied once, in one place | Pattern matching |
| The instruction already contains the design | No discovery step |
| No lifecycle, threading, persistence or build-graph coupling | Difficulty in Android comes from the runtime |
| The repo documents the algorithm | Strip the doc and re-judge, or accept it grades transcription |

Agent-authored history is **not** on this list. An agent that built a feature had the spec, the
corrections and the time; a benchmark agent has none of those. Investigate it rather than reject it
— the four steps are in `sanitizing.md`.

**The one-shot test beats all of them.** Draft the instruction, hand it to a strong model with the
before state, and watch. If it patches correctly without exploring, the candidate is dead. Cheap,
and it beats wishful thinking.

## Where hard tasks live in Android

Difficulty comes from the runtime, not the algorithm — behaviour whose correctness depends on
conditions the code does not make visible.

| Area | Example |
|---|---|
| State that must survive something | Configuration change, process death, back-stack restore, low-memory kill. The bug reproduces on the second entry, or after rotation |
| Concurrency with ordering constraints | Two Flows combined without dropping emissions; replay/buffer choice that depends on subscription order; cancellation that must or must not propagate |
| Migrations that must not lose data | Room schema change with a real fixture database from the old version; a shipped serialization format; Preferences to DataStore |
| Consistency across existing call sites | Small insight, applied coherently everywhere the pattern appears, with tests checking every one |
| Root-cause distance | UI glitch from an equality bug in a data class; leak from a listener in the wrong lifecycle callback; API 34 crash from a `PendingIntent` flag. Small patch, long investigation |
| Build graph | Module extraction with consumer rewiring, convention plugins, a KMP split. Gradle files count only here |

Root-cause distance is the explicit exception to "multiple files". Use it sparingly and argue for
it.

## Legitimate hardening

**Make the problem harder, not the instruction vaguer.**

| Move | Effect |
|---|---|
| Frame the symptom, not the diagnosis | The largest lever. "Progress resets to 0% when the phone rotates mid-download" is fair and hard. "Fix the missing `SavedStateHandle` write in `DownloadViewModel`" is the same task with the work removed |
| Widen the blast radius | Require the behaviour at every existing call site, and test all of them |
| Add a constraint the codebase already has | A public API that cannot change, a minSdk ruling out the obvious call, data on disk that must stay readable, a performance budget the tests measure |
| Move the goalposts to the seam | Require the work through an abstraction three other features already use |
| Widen pass-to-pass | An agent that solves the problem by breaking adjacent behaviour fails |
| Couple two extractions | Two features sharing a seam is one hard task; two independent ones are two easy tasks |

## Moves that get tasks rejected

- Withholding a requirement, then testing for it
- Requiring a class or file name for no stated reason, then failing a different name
- Manufactured complexity — the rubric names "a 3D renderer in pure Kotlin for a calculator app"
- Difficulty from a broken environment rather than the problem
- Assuming the agent lacks internet or tooling. It has both

## Reproduce before you argue — in both directions

Run the code. Apply the extraction or inject the bug. Watch it fail. Measure it.

A difficulty argument taken from the repo's own documentation is unverified, and the agent will
read that same document. **A difficulty claim that ships inside the repository is a leak, not a
claim.**

The mirror image is easier to miss and costs more: **a rejection sourced only from a repo document
is unearned too.** Killing a candidate because a TODO says "needs the requirement owner" adopts
that team's process decision as a benchmarking finding. Reproduce first, then reject — see the
deferred-work section of `extracting-tasks.md`.

## Writing the argument

The proposal asks why it is hard, how long for an expert, and where models will struggle. Make it
falsifiable.

Break the hours down: *reproduce under process death 1–2h · locate the state loss across three
ViewModels 3–4h · design the restore path without breaking the deep-link entry 4h · migration test
with a fixture bundle 2h · fix the two call sites the change breaks 2h*. A reviewer can argue with
that. They cannot argue with "this is hard".

Predict the failure specifically: *models will add `SavedStateHandle` to the obvious ViewModel and
stop, because the second loss path only appears when the deep-link entry rehydrates from a
different source.* Not "models will struggle with the complexity".

If you cannot fill in either honestly, that is the finding. Report it.
