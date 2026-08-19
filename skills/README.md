# Skills

Agent skills for authoring tasks in this dataset. Each directory is a
[Claude Code skill](https://code.claude.com/docs/en/skills): a `SKILL.md` plus reference material
that an agent loads on demand.

| Skill | What it does |
|---|---|
| [android-bench-task](android-bench-task/) | Turns an existing Android repository into a task: sanitizes it so it cannot leak the answer, ranks candidate tasks in it, then writes a build plan covering the manifests, patches, container and validation. |

## Using them

The repo's `.gitignore` excludes `.claude`, so these live at `skills/` and you wire them up
yourself. Symlink into your personal skills directory once, and the skill is then available from
**any** repository — which matters, because you author a task while sitting in your *source* repo,
not in this one:

```bash
mkdir -p ~/.claude/skills
ln -s "$(pwd)/android-bench-task" ~/.claude/skills/android-bench-task
```

Then invoke it with `/android-bench-task`, or just describe what you want — the skill's description
covers the usual phrasings.

To scope a skill to this repo only, symlink it under `.claude/skills/` here instead. That path is
git-ignored, so the link is yours alone and does not travel with the repo.

## Contributing a skill

Keep `SKILL.md` under ~500 lines and push detail into `references/`, which the agent reads only when
a phase needs it. Anything that should run rather than be described belongs in `scripts/`.

Cite the code, not the docs. Several things in this repository disagree with each other —
`task-template.toml` still shows the V1 flat manifest, `v2/README.md` calls `task.toml` the
definitive manifest, and `v2.task create` prints a next-step pointing at the wrong file. A skill
that repeats those sends its reader down a path the tooling does not support, so cite
`v2/task_commands/*.py` with line numbers and re-check them when the tooling changes.
