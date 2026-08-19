# Sanitize template

Fill in and write to `android-bench/SANITIZE.md`. Replace every `{{...}}`.

**Scope: make the repository clean.** Deletions, relocations, renames. Nothing about task repos,
forks, publishing, before/after commits or worktrees — that is phase 3 and does not belong here.

**The blockers are the document.** Each carries its own fix as checkboxes with commands — no
separate checklist, no cross-referencing. **Every blocker is fully fixed by its own boxes.** One
surviving leak makes the task grade transcription regardless of the rest.

**Three lines per blocker** outside its checkboxes: the claim, then the evidence as bullets. Nothing
that came back fine appears.

**No cross-references.** "See blocker 3, which touches the same files" is not actionable. Repeat the
paths and say what to cut.

**Anything no `sed` can do that touches more than ~15 files becomes a prompt file** under
`android-bench/prompts/`, referenced by one checkbox line. See the SKILL's *Delegate the big edits*.

**Every fence is ``` at column 0, never indented, never nested.** A block opened with a single
backtick still looks like code in the file and copies as garbage. Run the fence check in the SKILL's
*Commands have to survive the copy* before handing the file over.

**Every check reaches zero, or prints its own verdict.** A grep that returns hits the reader has to
judge produces a `[?]`, not an answer. Exclude what other boxes delete; allowlist real survivors
inline; state the expected count. Checks spanning blockers go at the end, never mid-document.

On a re-check pass: keep only the blockers still outstanding, and say in the verdict line what was
cleared.

---

# Sanitize: {{repo-name}}

**{{One sentence: what leaks, and whether it is fixable.}}**

Work on a branch so your own history is untouched:

```bash
git switch -c bench/sanitize
```

Work top to bottom. Every box below deletes, moves or renames; the checks are at the end, because
each one depends on the deletions above it having happened.

---

## {{1. The design is written down}}

**{{One-line claim — what an agent gets for free.}}**

- {{`docs/03-DETECTION-SPEC.md:3` — "Implement it literally", then every threshold}}
- {{`Engine.kt:6` — points back at the spec by path}}
- {{N files under `src/main/` citing `docs/NN` or `ADR-NNN`}}

- [ ] Delete the documentation

```bash
git rm -r --quiet {{docs}} && git rm --quiet {{PROGRESS.md TODO.md DECISIONS.md}}
```

- [ ] {{N}} citations across {{M}} files, most inside string literals and test names — too many to
      hand-edit, and `sed` breaks the build. New session in the repo, paste
      `{{android-bench/prompts/strip-citations.md}}`, then read `git diff`.

## {{2. The build gates block the cleanup}}

**{{`guardTraceability` regenerates a doc from source and fails once `docs/` is gone.}}**

- {{`app-project/build.gradle.kts:NN` — `guardTraceability` wired into `check`}}
- {{Kover 90% floor, N screenshot comparisons — both fail correct solutions}}

- [ ] Delete the task registrations and their `check` wiring. Keep compilation and
      `{{:engine:detection:test}}`; nothing else. Find them with:

```bash
git grep -nE 'guard[A-Z]|check-.*\.sh|detekt|ktlint|kover|roborazzi|apiCheck' -- '*.gradle.kts'
```

## {{3. The tuned constants are the design}}

**{{N defaults compiled into the contract, in M byte-identical copies.}}**

- {{`shared-contract/kotlin/Engine.kt` — `ScoringProfile`, `EngineConfig`}}

- [ ] Move them into a config file the build reads — {{`config/engine-tuning.json`, loaded the way
      `config/power-model.json` already is}}. Highest-value item here; follow
      `{{android-bench/prompts/extract-tuning.md}}`.

## {{4. A sibling project ships the solution}}

**{{`imukit-refengine/ReferenceFallEngine.kt` is a complete working implementation.}}**

- {{822 lines, registered as a drop-in factory}}
- {{`imukit-scenarios` encodes the same discriminating physics}}

- [ ] {{Decide whether the harness grades from outside the tree — it has no Gradle dependency on
      the app and loads engines off `--classpath`, so it loses nothing by living outside.}}
- [ ] Remove it from the tree

```bash
git rm -r --quiet {{imu-testkit}}
```

## {{5. The history is an agent's build log}}

**{{N of M commits carry a `Co-Authored-By` trailer, and `git show <sha>^:<file>` restores what was
stripped.}}**

- [ ] Delete the agent-instruction files and the tracked archives — grep cannot see inside a tarball

```bash
git rm -r --quiet --ignore-unmatch \
  {{CLAUDE.md AGENTS.md GEMINI.md .cursorrules .claude KICKOFF_PROMPT.md HANDOFF.md ops _to_delete}}
```

- [ ] The trailers and the recoverable history close in phase 3, by importing the before state as a
      single orphan commit. Nothing to do now.

## {{6. Names that announce the missing work}}

**{{`UnsecureGateApi` tells the agent where to look and what the fix is.}}**

- [ ] Rename anything describing the defect or the missing work rather than the thing itself:

```bash
git grep -nE 'Legacy|Naive|Unsafe|Unsecure|Temp|Workaround|Broken|V1\b|TODO|FIXME'
```

---

## Check it is clean

Run these only after every box above is done — each one depends on the deletions having happened.
Each prints its own verdict; no output to interpret.

- [ ] No reference survives to anything you removed

```bash
git grep -nI -E '{{ReferenceFallEngine|imukit|guardTraceability|docs/0}}' -- ':!android-bench' \
  || echo CLEAN
```

- [ ] Nothing agent-authored is still tracked

```bash
git ls-files | grep -iE '{{claude|kickoff|handoff|agents\.md|cursorrules|_to_delete}}' \
  || echo CLEAN
```

- [ ] Build and graded suite pass from a clean state

```bash
cd {{app-project}} && ./gradlew clean assemble {{:engine:detection:test}}
```

- [ ] Commit

```bash
git add -A && git commit -m "{{message}}"
```

- [ ] Re-run `/android-bench-task`. It re-scans and either marks the repo clean or reissues what is
      still outstanding.
