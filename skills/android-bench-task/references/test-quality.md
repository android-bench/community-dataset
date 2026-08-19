# Are the tests good enough to grade an agent?

A benchmark test has a harder job than a normal test. A normal test guards *your* implementation.
A benchmark test has to accept **every correct implementation** — including ones you did not think
of, written by something that does not share your naming instincts — and reject every incorrect
one, reproducibly, without you there to interpret the result.

Two failure modes, and they pull in opposite directions:

- **Too specific** → a correct solution fails. This is the worse one: it produces a wrong signal
  about the model, and it is the most common reason a first task gets sent back.
- **Too loose** → an incomplete or cheating solution passes. Caught by reading the trajectory,
  not the score.

## The rule everything follows from

**`test.patch` is applied to the agent's tree, after the agent finishes. The tests compile
against whatever the agent built.**

So every symbol a test references must be in one of three places:

1. **Already in the before commit.** The best option. Put the interface, the abstract class, the
   sealed hierarchy, the function signature in the before commit and let the task be
   *implementing* it. The agent gets a seam to fill, you get a stable compile surface, and the
   instruction stays honest.
2. **Named explicitly in the instruction.** Fair, but spend these sparingly — the rubric warns
   against artificial hints, and a pile of "you must create `FooRepositoryImpl` in package
   `x.y.z`" reads as a spec dictation rather than an issue.
3. **Nowhere — checked by `validate.sh` instead.** Grep, parse, inspect. See below.

If a test imports something the agent had to invent and you never mentioned, the test does not
compile and every correct solution scores zero.

## Implementation-agnosticism checklist

Run each existing or planned test past these. Anything failing needs a rewrite, a move to
`validate.sh`, or a change to the before commit.

**Fails the check**

- Imports a class the agent must invent, and the instruction never names it.
- Reflection on private fields or methods to check "did they store it *here*".
- Asserts a specific async type — `StateFlow` vs `Flow` vs `LiveData` vs suspend function —
  when the instruction did not require one. Assert what comes out, not what it comes out of.
- Mocks the class under construction and asserts call counts on it. This tests one particular
  decomposition, not the behaviour.
- Compose: index-based node selection (`onAllNodes(...)[3]`), or a `testTag` the instruction
  never declared. Tags are fine when the instruction states them as the contract — that is a
  legitimate output-format requirement — but a tag you invented while writing the test is a trap.
- Asserts exact user-visible strings that the instruction did not fix ("Loading…" vs "Loading",
  or anything that goes through string resources and locale).
- Screenshot/golden-image comparison. Three separate disqualifiers, in `task-format.md` §4b: the
  golden PNGs sit in the before state and show the agent the exact UI to produce; rendering depends
  on JDK, fonts and architecture, and `docker.py` pins `openjdk-amd64` while never setting
  `--platform`; and a correct solution with a different layout fails every comparison. Roborazzi and
  Paparazzi run on the JVM, so an empty `android_test` does not exclude them — strip them from the
  graded command:

  ```bash
  git grep -nE 'roborazzi|paparazzi|captureRoboImage|assertAgainstGolden' -- '*.gradle.kts' '*.kt'
  ```
- Asserts on log output, file paths, or database table/column names the agent chose.
- Checks behaviour the instruction never described. Explicitly listed as a rejection reason.

**Passes the check**

- Drives a public API that exists in the before commit and asserts observable results.
- Asserts state transitions as data — a list of emitted values, a final rendered state — not
  the mechanism that produced them.
- Compose: `onNodeWithText`, `onNodeWithContentDescription`, or a tag the instruction declares.
- Asserts through the seam the before commit provides, so any implementation behind it works.
- Round-trips: write with the new code, read with the old reader, assert equality. Migration
  tests are naturally agnostic — they test the contract, not the code.
- Checks that a specific *outcome* holds at every existing call site, enumerated by the test.

## Determinism — the 1% rule

**Flakiness above ~1% is disqualifying**, because CI runs the task many times and one arbitrary
failure poisons the whole validation. Android supplies more flakiness sources than most platforms.

| Source | Fix |
|---|---|
| `Thread.sleep`, real `delay` | `runTest` + `TestDispatcher`; advance virtual time |
| `Dispatchers.Main` / `.IO` hardcoded | Inject dispatchers; `Dispatchers.setMain(StandardTestDispatcher())` |
| Collecting a Flow with a timeout | Turbine (`test { awaitItem() }`) |
| `System.currentTimeMillis()`, `Instant.now()` | Inject a `Clock` / time source, fixed in tests |
| `Random()` unseeded | Seed it, or inject the source |
| Real network | MockWebServer or a fake; never hit the internet from a verifier |
| Room on disk | `Room.inMemoryDatabaseBuilder(...).allowMainThreadQueries()` — except in migration tests, where the on-disk fixture *is* the point |
| Locale / timezone | Pin them in the test; never assert on formatted dates or numbers |
| Espresso without idling | `IdlingResource`, or `ComposeTestRule` with an idling clock; never `Thread.sleep` |
| Instrumentation tests generally | Slower, need `/dev/kvm` and a booted emulator, more failure surface. Prefer Robolectric or plain JVM unit tests where they can express the assertion |
| Test order dependence | Each test builds its own fixtures; no shared mutable statics |
| `@Ignore`d or already-failing tests in the repo | These land in `breaking` during `verify-tests` and must be resolved before submitting |

`v2.task verify-tests` classifies tests into `fail_to_pass`, `pass_to_pass`, `flaky` and
`breaking`. Anything in `flaky` must be fixed or removed. Run it more than once.

## Grade on the narrowest command

Pick the Gradle task that runs exactly the tests you are grading — `:engine:detection:test`, not
`check`, not `koverVerify`, not `build`.

Quality gates fail correct solutions for reasons the instruction never stated. A ≥90% coverage
threshold punishes a fix that adds one untested branch. `check` drags in detekt, lint and
screenshot goldens, each with its own flakiness and platform sensitivity. None of them are the
task.

Confirm the command actually runs your modules. `testDebugUnitTest` is Android-variant-specific and
silently skips pure-Kotlin JVM modules — a README that advertises it as "all JVM tests" can be
wrong about the very module you care about. Run it and count.

## A test harness in the repo is a verifier, not a rival

When a repo contains a second project built to exercise the first — a replay harness, a scoring
tool, a reference corpus, a fuzzer, a simulator — the reflex is to call it a leak and exclude it.
Sometimes it is. Often it is the best grading mechanism available, and excluding it throws away the
thing that would have made the task fair.

Ask which it is:

| It is a leak when | It is a verifier when |
|---|---|
| It contains a working implementation of the thing being extracted | It only drives and measures the thing being extracted |
| Its fixtures encode the answer | Its fixtures encode the *requirement* — inputs plus expected outcomes |
| Reading it tells you how to build the solution | Reading it tells you what "correct" means, which the instruction says anyway |

A harness that loads implementations through an interface — `ServiceLoader`, a factory, DI — is
close to ideal, because it grades behaviour through a seam and cannot name anything the agent
invented. A labelled corpus with expected verdicts is a ready-made fail-to-pass set.

Splitting the difference is usually possible: ship the harness, its corpus and its scoring, and drop
only the reference implementation it happens to carry. That is a `rm` of one file, not the exclusion
of a project.

If you do ship it, remember it becomes part of the environment: it has to build in the container, it
must be in `test_files` and covered by the `test.sh` wipe, and its runtime has to fit the verifier
timeout.

## When to reach for validate.sh

`validate.sh` runs after `test.sh` and can check what a JUnit test cannot express without
pinning a name. Use it when the requirement is structural rather than behavioural:

- A dependency, plugin, or version catalog entry is declared in the right module.
- A manifest entry exists — permission, `<queries>`, `android:exported`, a service declaration.
- A ProGuard/R8 keep rule is present.
- An architectural invariant: "nothing under `core/domain/` imports `android.*`".
- A generated artifact exists at a known path after the build.

Keep it flexible on purpose. Match on the *thing that matters* — a regex for the dependency
coordinate, not the exact line formatting — and echo what it found when it fails, so a human
reading the log can tell a real failure from a matcher that was too strict.

## Auditing a repo's existing tests

When the task is built from a feature that already exists, its tests already exist too. Sort
them:

**Reusable as pass-to-pass** — green on the before commit, unrelated to the change, deterministic.
These are your regression net; the wider the better. Verify they actually pass on the before
commit rather than assuming.

**Reusable as fail-to-pass after rewriting** — they test the right behaviour but reference
symbols the agent must invent, or use a brittle mechanism. Rewrite against a seam you add to the
before commit, or relax to behavioural assertions.

**Not reusable** — they encode your implementation. The usual form is a literal lifted from the
reference implementation:

```kotlin
assertEquals(0.64f,  dropConfidence, 0.03f)
assertEquals(0.918f, fallConfidence, 1e-3f)
```

A correct solution that weights things differently fails all of them. Rewrite to assert on the
contract types — the verdict, the reason, the observable output — not on intermediate numbers. Say
so explicitly and name the replacement.

**Fixtures and helpers are part of the verifier.** A builder that defines what "a drop" means is as
load-bearing as the assertions that use it. List them in `[acceptance_criteria].test_files` and
cover them in `test.sh`'s wipe globs, or an agent rewrites the premise while leaving every
assertion intact.

**Leaks** — a test *name* can give the answer away, and so can a test that exists in the before
commit describing the missing behaviour. `shouldRestoreScrollPositionAfterProcessDeath` in the
before commit tells the agent exactly what to do. Move those into `test.patch`.

Also check the reverse direction: does the before commit's suite **already** cover the behaviour
you want to grade? Then F2P will be empty, nop will pass, and the task is broken before you start.

## Watch for reward hacking

Score alone does not tell you the task works. Read the trajectory of a passing run and confirm
the agent solved the problem rather than the test:

- Did it edit or delete tests? (`test.sh` should have wiped them — confirm your wipe globs
  actually covered your `test_files`.)
- Did it hardcode the expected values, special-case the test's inputs, or detect that it is
  running under test?
- Did it change build config to skip the failing module?
- Did it find the answer in a doc, a comment, or by re-cloning the public repo?

Any of these means the verifier needs work, even though the number came out right. This is what
the checklist means by "the trajectory, patches, and results from local runs have been manually
evaluated".
