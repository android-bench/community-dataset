# Single-step or multi-step?

Harbor supports multi-step tasks: an ordered sequence of steps sharing one environment, each with
its own instruction, tests and optional setup, producing per-step rewards that roll up into a
single trial reward.

Decide from Harbor's own definition and from what the task is *for* — not from what tooling is
convenient. A multi-step task is one whose purpose needs ordered stages sharing an environment; a
single-step task is one whose purpose is a single end state, however large the work to reach it.

## When multi-step genuinely wins

Reach for it only if at least one of these is true and matters:

- **Later work is pointless if earlier work failed.** Step 1 extracts a module; steps 2–3 rewire
  consumers. If step 1 fails there is nothing to measure, and `min_reward` early-stopping saves
  the run. Under a single-step task the whole thing just scores zero, which tells you less.
- **You want to know *where* an agent fell over.** Per-step rewards localise the failure. On a
  five-hour migration task, "failed at the consumer rewiring, not the extraction" is a far more
  useful datapoint than a zero.
- **The steps must share accumulated environment state.** Step 2 depends on artifacts step 1
  produced in the container — a generated schema, a built AAR, a populated database — not just on
  source changes.
- **You are deliberately testing continual learning.** With `--resume-trajectory` the agent keeps
  its conversation across steps, so you can measure whether it builds on its own prior work.

## When single-step is right

Which is most of the time:

- One coherent issue, however large. A 400-line change in eight files is still one step.
- The phases are only *conceptually* sequential — a human would do them in order, but the tests
  only care about the end state. That is not multi-step, that is one task with a plan.
- The end state is what matters, and no intermediate state is worth a separate score.

**A useful test:** would a partially-complete solution to step 1 make step 2 meaningless to grade?
If yes, multi-step. If step 2 could be graded independently, you have one task, or two tasks.

## The Harbor multi-step format

Replace the task-root `instruction.md` and `tests/` with a `steps/` directory. The environment
builds once and persists across all steps.

```
tasks/<task-id>/
├── task.toml
├── environment/
│   └── Dockerfile
└── steps/
    ├── 01-extract-module/
    │   ├── instruction.md
    │   ├── workdir/          # staged into the container before the agent runs
    │   │   └── setup.sh      # runs before the agent; non-zero exit aborts the step and the rest
    │   └── tests/
    │       └── test.sh
    ├── 02-rewire-consumers/
    │   ├── instruction.md
    │   └── tests/
    │       └── test.sh
    └── 03-remove-legacy-path/
        ├── instruction.md
        └── tests/
            └── test.sh
```

```toml
schema_version = "1.4"

[task]
name = "<owner>/<slug>"

multi_step_reward_strategy = "mean"   # "mean" (default) | "final"

[[steps]]
name = "extract-module"
min_reward = 1.0                      # below this, remaining steps are skipped

[steps.agent]
timeout_sec = 3600.0

[steps.verifier]
timeout_sec = 1800.0

[[steps]]
name = "rewire-consumers"
min_reward = 0.8

[[steps]]
name = "remove-legacy-path"
```

Per-step fields: `name`, `min_reward` (scalar, or a dict for multi-dimensional gates like
`{ correctness = 0.8, style = 0.5 }`), `artifacts` (paths to snapshot after the step),
`healthcheck.*` (pre-step environment checks), `[steps.agent].timeout_sec`,
`[steps.verifier].timeout_sec`.

Each step still writes its own reward to `/logs/verifier/reward.txt` or `reward.json` from its own
`tests/test.sh`. `multi_step_reward_strategy` decides how they combine: `"mean"` averages across
completed steps, `"final"` takes the last one.

By default each step starts a **fresh agent conversation** — the container state carries over, the
agent's memory does not. `harbor run --resume-trajectory` keeps the native session, delivering each
step's instruction as a follow-up turn. Choose deliberately: fresh conversations measure whether
the *code state* is enough to continue; resumed ones measure whether the agent can build on its
own reasoning.

Harbor ships a worked example at `examples/tasks/hello-multi-step-advanced/` covering per-step
instructions, workdir uploads, env vars, healthchecks, `min_reward` gating and artifact collection.

## Building a multi-step Android task by hand

The `v2.task` conveniences do not apply, so plan for this extra work:

- **Patches per step.** You need a commit for each step boundary — `bench/<id>/step-0`,
  `step-1`, … — and a diff between consecutive pairs for each step's tests. `refresh-patches`
  will not do this; script it, and keep the script in the task's `README.md` so a maintainer can
  reproduce your patches.
- **A per-step test wipe.** Each `steps/<n>/tests/test.sh` needs its own "discard the agent's test
  edits, apply this step's hidden tests" block, scoped to that step's test files.
- **Cumulative pass-to-pass.** Step 3's tests should include steps 1 and 2's, or an agent can
  pass step 3 by undoing step 1.
- **A per-step oracle.** `solve.sh` has to apply the right patch for the current step. Without a
  working oracle path, CI cannot validate the task.
- **Canary strings** in every file under every `steps/*/tests/` and `solution/`, same as usual.

If that list looks like more work than the task is worth, that is a real signal — take the
single-step version.
