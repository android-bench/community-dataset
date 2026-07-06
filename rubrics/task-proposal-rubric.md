# Android Bench Task Proposal Rubric

This rubric will be used as a guiding light by task creators and reviewers to evaluate if a task idea is worth bringing into Android Bench. Our goal is to set a high bar for difficult, valuable, and uniquely Android-focused challenges for agent evaluation.

When proposing a task, evaluate it against the following six criteria. Ensure you provide a solid justification for why it should be accepted.

---

## 1. Verifiable
All Android tasks must be verifiable programmatically. 
- You must be able to write an Android test (UI/Instrumentation) or a Unit test that definitively proves the bug is fixed, or the feature is fully implemented.
- If testing specifically named classes or layouts is difficult because it assumes agent naming conventions, a bash `validate.sh` script must be able to definitively extract and verify logical correctness (e.g., checking for Gradle dependencies, manifest tags, or specific abstract logic).
- Tests must be highly reliable. Flaky tests that fail >1% of the time arbitrarily will fail the entire agent validation suite and thus render the task unacceptable.

## 2. Well-Specified
The problem description must completely describe what needs to be solved and what the verifier will check for, leaving nothing up to guessing or implicit institutional knowledge.
- The description must be completely hand-written, mirroring a GitHub Issue you would find in an open source repository.
- Tasks will be rejected if they require the agent to discover obscure corner cases that aren't mentioned or readily deducible from standard Android architecture. 
- A well-specified problem should succinctly capture the end-goal in 2-3 paragraphs.

## 3. Solvable
The task must provide a working solution. 
- The project must build cleanly in the "before commit" (unless the task is specifically about fixing a build failure).
- The project must fully implement the solution, satisfy all tests, and build cleanly in the "after commit".
- A human domain expert with knowledge of the codebase should be able to implement this within a few days at most. Insoluble or fundamentally broken environment tasks are rejected.

## 4. Difficult
Difficulty is judged by how hard the problem would be for a human Android developer.
- We require it to be difficult for a "good reason.". This means it is a realistic problem that would be given to a senior engineer, not artifically made difficult just to increase complexity.
- We expect tasks to require significant professional experience (e.g., Senior Android Developer expertise). 
- **Requirement:** A human developer should take \>12 hours to complete the task.
- Tasks should touch *multiple* logic/code files. Adding a single missing annotation or a one-line bug fix is generally too easy. Test files do not count. Gradle files do not count unless the task is specifically about build configuration.
- **Exception:** Small fixes in massively obfuscated or incredibly dense algorithmic logic might qualify if the root cause analysis takes a uniquely long time.

## 5. Realistic and Valuable
The task should simulate a real-world scenario where solving this problem has utility to an enterprise or open-source software project. 
- Example: Modifying `Now In Android` to adopt a new architectural pattern or migrating a legacy module in `Pocket Casts` to Compose are realistic.
- Contrived scenarios (e.g., "Implement a custom 3D rendering engine in pure Kotlin without OpenGL for a calculator app") will be rejected.

## 6. Outcome-Verified
We grade agents on outcomes, not their specific procedure (unless constrained for anti-cheat purposes).
- Agents have internet access.
- We do not restrict what Android Studio tooling or command-line tricks the agent uses, nor do we require strict procedural steps, so long as the final tests pass.
- Constraints should only be put in place to enforce modern practices if they are explicitly part of the task (e.g., "You must use Kotlin Coroutines, not RxJava for this migration").

