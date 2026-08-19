---
name: android-bench-task
description: >
  Turn an existing Android repository into a task for Android Bench, submitted through
  github.com/android-bench/community-dataset. Runs in three gated phases: sanitize the repo so it
  cannot leak answers to the agent, find and rank candidate tasks in it, then write a precise build
  plan for the one chosen. Handles feature extraction, injected bugs, deferred work and
  greenfield builds, and the isolation problem that decides whether any of them can be graded.
  Use whenever someone mentions Android Bench, android-bench, community-dataset, contributing or
  proposing a benchmark task, Harbor tasks for Android, task.toml / solution.patch / test.patch /
  oracle and nop validation, or asks whether their repo could evaluate coding agents — even if they
  never say "Android Bench".
---

# Building an Android Bench task from a repository

An Android developer has a working repo and wants a task for
[Android Bench](https://developer.android.com/bench). They know Android and nothing about
benchmarking.

A task is a **before state** an agent starts from, an **instruction**, and **hidden tests**. Their
repo is the after state. The work is subtractive: remove or break something, and make sure the
answer cannot be recovered from what remains.

## Phases

Three phases, strictly in order. **Run one phase, then stop.** Never preview a later phase — it
buries the thing they need now.

| Phase | Output | Ends when |
|---|---|---|
| 1. Sanitize | `android-bench/SANITIZE.md` | They have worked through it and you re-check clean. **Loops.** |
| 2. Candidates | `android-bench/CANDIDATES.md` | They pick one |
| 3. Plan | `android-bench/PLAN-<task-id>.md` | Done |

Analysis is not a phase. It is the first half of phase 1, it asks nothing of the reader, and
stopping after it costs them a turn for nothing. Analyze, then write `SANITIZE.md` in the same
reply.

### Work out the phase before doing anything

Read `android-bench/` first. It is the state.

- No `android-bench/` → phase 1.
- `SANITIZE.md` exists → phase 1 re-check. Re-scan the repo, and either mark it clean or reissue
  only the items still outstanding. Do not restate what is fixed.
- Repo clean, no `CANDIDATES.md` → phase 2.
- `CANDIDATES.md` exists and they have named a candidate → phase 3.

## Two hard rules

**Never modify their code.** Not a file, not a branch, not a commit. You print commands; they run
them. Read-only git — `log`, `show`, `diff`, `status`, `grep`, `rev-parse`, `ls-files` — is
expected. This governs what you do, not what you write: do not announce it.

**Three repositories exist, but only in phase 3.** The source repo (their code), the public task
repo (the before commit the container clones), their community-dataset fork (`tasks/<id>/` and the
PR). Nothing in phases 1–2 touches the last two, so do not mention them.

## Phase 1 — Sanitize

Assume the working directory is the source repo. If it is not a git repo with Android code in it,
ask. If the tree holds more than one project, ask how they relate — a purpose-built test harness is
a **verifier**, not a rival, and treating it as a competitor throws away the best grading mechanism
in the repo.

Collect only what later phases need: Gradle roots, module graph, the command that runs the tests you
would grade on, whether they pass, JDK and SDK versions.

Then decide whether the repo leaks. Read `references/sanitizing.md`. Four things leak:

| Leak | Looks like |
|---|---|
| Agent-authored history | `Co-Authored-By` trailers, session logs, kickoff prompts, operating manuals |
| Over-specification | Design docs, ADRs, specs that state the mechanism rather than the requirement |
| Design-carrying names and constants | `UnsecureGateApi`, `scoreThinPulse`, tuned defaults compiled into a contract |
| Sibling implementations | A second working copy of the thing you want to extract |

Agent-authored is not a verdict — that agent had the spec, the corrections and the time. Investigate
it (four steps in `sanitizing.md`) rather than rejecting the repo.

If it leaks, write `SANITIZE.md` now — same reply. If it does not, say so and go to phase 2.

### The sanitize document

**Scope: make the repository clean. Nothing else.** No task repo, no fork, no publishing, no
worktrees, no before/after commits — those are phase 3. A step that is not a deletion, a relocation
or a rename does not belong here.

Fill `assets/SANITIZE-TEMPLATE.md` into `android-bench/SANITIZE.md`.

The document is the blockers. Each blocker states what leaks, shows the evidence, and carries its
own fix as checkboxes with commands — no separate checklist, no cross-referencing between sections.
**Every blocker must be fully fixed by its own boxes.** One surviving leak makes the task grade
transcription regardless of the rest.

Think one step deeper on each fix. Deleting a doc breaks a build gate → delete the gate, not the
doc. A constant leaks the design → move it out of code into config, do not rename it. A sibling
project leaks → work out whether it grades from outside the tree instead of being excluded.

In chat: the verdict, the blockers one line each, and a Next section. Then stop.

This phase repeats. When they come back, re-scan and reissue only what remains.

## Phase 2 — Candidates

Read `references/extracting-tasks.md`. Four mechanisms:

| Mechanism | Before state |
|---|---|
| **Deferred work** | Unchanged — it was never built |
| **Feature extraction** | A capability removed, a thin seam left |
| **Bug injection** | A plausible defect written in |
| **Greenfield** | A skeleton project |

Candidates can also be built by cherry-picking: merge two coupled features onto a branch so the
before and after commits are clean, or lift specific files or fragments.

Judge each on difficulty (`references/difficulty.md`, bar is **>24h senior and Gemini 3.0 Flash
fails**) and isolation (`references/isolation.md`). **Reproduce before you claim, and before you
reject** — a rejection whose only citation is a repo document is unearned.

Mark each candidate single-step or multi-step from Harbor's definition
(`references/multi-step.md`), per candidate, never per repo.

Write `android-bench/CANDIDATES.md`: ranked hardest first, each with mechanism, files, a per-step
hour breakdown, isolation verdict, and the single/multi-step call. Ask which one. Stop.

## Phase 3 — Plan

Fill `assets/PLAN-TEMPLATE.md` into `android-bench/PLAN-<task-id>.md`. Read
`references/git-workflow.md` and `references/task-format.md` first.

Precise enough to follow without knowing anything: real paths, real SHAs, real Gradle tasks, the
commands to run, the URLs to visit, the prompts to paste. Every git-write command is theirs to run.
Dry-run any non-obvious sequence in a throwaway repo first — plans fail on ordering, not knowledge.

**Check the tooling's output against its code, not its docs.** community-dataset's
`task-template.toml`, its `v2/README.md` and `v2.task create`'s own success message all disagree
with what `v2/task_commands/create.py` writes. `v2.task docker` generates a Dockerfile that is not
submittable at all — private base, amd64 hardcoded with no `--platform`, and a skip marker that the
canary inserter silently disables. `task-format.md` §3 and §6 carry the corrections with line
citations; read both before writing the plan, and never present generated output as finished.

**Three artifacts, three homes, and only one of them is published as a repo.** The before commit
goes to a public task repo — the container clones that URL. The after commit **never leaves their
machine**: the solution ships as `solution.patch` in the community-dataset PR, guarded by the canary
string, and that is its only published form. `refresh-patches` reads both SHAs out of a local staged
clone, so nothing forces the after commit onto a remote. Canary the after state before generating
the patch (`scripts/add-canary.sh`) — the canary reaches `solution.patch` only if it was in the files
when the diff was taken.

**The instruction is theirs to write.** The contributing guide rejects tasks that read as
LLM-generated. Give them the constraints and a skeleton; not the prose.

## How to write

Instructions, not essays. **Cut every sentence that is not a verdict, a fact or an instruction.**

Budget: **one line per blocker in chat, three lines per blocker in the file** outside its checkboxes.
Over budget means you are explaining; explain in the command instead.

- **Prose only for the verdict line and a blocker's one-line claim.** Everything that resolves to an
  action is a checkbox with a command. Written a paragraph? Write what the reader should *do*.
- **Every checklist item carries its command.** "Confirm the suite passes" is an intention; the
  `docker run …` line is an instruction. Where no command exists, say what decides it.
- **Every item stands alone.** "See blocker 3, which touches the same files" is not an instruction —
  the reader cannot act on it without holding two sections in their head. Repeat the paths.
- **A judgement edit ships a worked before-and-after**, copied from a real line in their repo.
  "Strip the mechanism KDoc from the five contract files" tells them nothing: which files, which
  lines are mechanism, what survives. One `before:` / `after:` pair answers all three.
- **If you know the values, emit the file, not a description of it.** "Fill `test_files` with the 19
  paths from Part 4" makes them transcribe a list you already have, by hand, into TOML, with quoting
  and commas to get right — and a typo there silently breaks the grading. Print the finished block.
  Same for any config, script, manifest or fixture whose contents you have already worked out: a
  table of key-value pairs is a transcription exercise, a fenced block is a paste. A `test.sh` whose
  wipe list you described in prose is the same mistake — write the script.
- **Check for a tool before assigning hand work.** The inventory is `references/task-format.md` §4c.
  Before writing "add X", "check every Y" or "list the Z", look there — and if it is not listed,
  grep the dataset clone before assuming it does not exist.
- **No second sentence restating the first.** No sentence explaining why a fact matters when the fact
  says it. No transitions.
- **A warning with no action is not a warning.** "Incomplete list, hole in the grading", "never
  force-push these commits", "flakiness above 1% disqualifies the task" all name a failure and leave
  the reader holding it. Every one converts into something they can run: the command that proves the
  list is complete, the tag that makes the commit immutable, the loop that measures the flake rate.
  If no such command exists, say what to look at and what decides it — and if neither exists, the
  sentence is decoration and goes.
- **Never report on your own behaviour.** *"The repo was not modified — ..."*, *"I only read ..."*,
  listing checks that came back fine: banned. The reader assumes it.
- **Never give a later phase its own section.** Answer in one sentence where it comes up. A heading
  like "Your two questions" turns a deferral into a digression.
- Lead with the verdict. Tables over nested lists. Flat one-line bullets. Bold the load-bearing
  phrase. Name everything — "write something harder" hands the thinking back.
- Admit uncertainty plainly.

### Commands have to survive the copy

A command the reader cannot paste is not an instruction. Two rules, both mechanical:

**Fences are ``` at column 0.** Never indent a fence under a list item and never nest one inside
another — that is where the backticks get dropped, and a block that opens with a single backtick
copies as garbage while still *looking* like a code block in the file.

**Verify before you hand the file over**, because you cannot see it rendered:

```bash
f=android-bench/SANITIZE.md
[ $(( $(grep -c '^```' $f) % 2 )) -eq 0 ] && echo FENCES-OK || echo FENCES-BROKEN
grep -nE '^ +`[a-z]*$|^ *`bash' $f    # must print nothing
```

If a command needs the reader to be somewhere, put the `cd` in the block. They paste one thing.

### Delegate the big edits

**No `sed` can do it, and it touches more than ~15 files → hand them a prompt, not the edit.**
Hand-editing a thousand call sites is a day of work; they will skip it or do it badly. Same for
anything needing judgement per occurrence.

Write the prompt to its **own file**, `android-bench/prompts/<slug>.md`. Not inline — a prompt
inline means a fence inside a fence inside a list item, which is the thing that breaks. The checkbox
becomes one line:

```
- [ ] 1,039 citations across 148 files — too many to hand-edit. New session in the repo, paste
      `android-bench/prompts/strip-citations.md`, then read `git diff`.
```

The prompt file is self-contained — the agent running it has not read your document:

- The branch, and that it must not commit.
- The scope as the `git grep` that enumerates it.
- The rule as **keep X / cut Y with one worked before-and-after** from a real line in their repo.
  This is the difference between a usable prompt and a wish.
- What must still hold afterwards, as a command.
- Report back: count changed, anything it could not decide.

Never explain benchmarking in it. "Remove references to files that no longer exist" is the job.

### Checks have to adjudicate themselves

A box whose command prints output the reader must interpret is not a check. They tick it blind or
write `[?]`, and either way you learned nothing.

**State the pass condition as the command's own output.**

- A zero-hit grep must actually reach zero. Exclude the paths another box deletes (`-- ':!imu-testkit'`)
  and allowlist the legitimate survivors inline, each with its reason. A grep that returns 1,256
  lines of material already scheduled for deletion taught the reader nothing except to distrust it.
- Where zero is impossible, print a verdict — a few lines of shell that echo `CLEAN` or the offending
  lines and exit non-zero.
- Say the number either way: `# expect 0 lines`, `# expect exactly these 3`.

**Never put a check above the work it depends on.** Anything spanning blockers belongs in *Check it
is clean*, at the end, after the deletions have happened.

### The file is the document; the reply is the summary

One canonical checklist exists and it lives in the file.

| | Chat reply | `android-bench/*.md` |
|---|---|---|
| Carries | Verdict · blockers one line each · Next | Evidence, checklist, detail |
| Length | Skimmable in a minute | However long it needs |

The reply ends with a Next section — a checklist, then what to return with. Not a question they
cannot answer yet.

```
## Next

- [ ] Work through `android-bench/SANITIZE.md` — 9 fixes, in order
- [ ] Re-run this skill when done; I will re-check and reissue anything still outstanding
```

## Reference material

Load only what the current phase needs.

| File | Phase |
|---|---|
| `references/sanitizing.md` | 1 |
| `assets/SANITIZE-TEMPLATE.md` | 1 |
| `references/extracting-tasks.md` | 2 |
| `references/difficulty.md` | 2 |
| `references/isolation.md` | 2 |
| `references/multi-step.md` | 2 |
| `references/test-quality.md` | 2, 3 |
| `references/task-format.md` | 3 |
| `references/git-workflow.md` | 3 |
| `assets/PLAN-TEMPLATE.md` | 3 |
| `scripts/add-canary.sh` | 3 — canaries the published after state; covers the `.kt` files `canary_check.py` skips |

## Failure modes

**Running two phases at once.** Every phase costs the reader a session of work. Previewing the next
one buries what they need now.

**Padding a sanitization list with setup.** Creating repos, forking, branching for the before commit
— all phase 3. Phase 1 deletes, relocates and renames.

**Reading agent-authored history as a verdict.** That agent had the spec. Investigate: was it
one-shot, what does not work, does it still fall over with the spec removed.

**Treating a leak as a verdict.** Trim, relocate, rename, narrow the fragment, or change the task
shape. Reject at the bottom of the ladder, and say which rungs you tried.

**Settling for the obvious fix.** The gate that blocks a deletion can be deleted too.

**Assigning by hand what the dataset automates.** community-dataset ships a canary inserter, a
proposal grader, and four `v2.task` subcommands that scaffold, build, diff and classify. A checklist
item telling someone to add canary comments to every file under `tests/` is not just slower than the
one-line command — it is wrong, because the tool also knows which extensions are out of scope and
the author does not. `references/task-format.md` §4c is the inventory; read it before Part 7 of the
plan and again before any "add X to every Y" instruction.
