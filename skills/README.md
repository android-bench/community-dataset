# Skills

Agent skills for authoring tasks in this dataset. Each directory is an
[Agent Skill](https://agentskills.io): a `SKILL.md` holding instructions, plus optional `scripts/`,
`references/` and `assets/` that get loaded only when a step needs them.

| Skill                                     | What it does                                                                                                                                                                                                                                  |
| ----------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [android-bench-task](android-bench-task/) | Turns an existing Android repository into a task. Runs in three gated phases — sanitize the repo so it cannot leak the answer, rank candidate tasks in it, then write a build plan covering the manifests, patches, container and validation. |

Nothing here is tied to a particular agent or model. The frontmatter is `name` and `description`
only, both spec fields, and the body is plain Markdown.

## Installing

A skill is just a folder. Installing it means putting that folder where your agent looks for skills.

**1. Find your agent's skills directory.** Its documentation will name one — search its docs for
"skills". The convention is a per-user directory under your home directory and a per-project one at
the repository root, with the project directory taking precedence. A few current examples:

| Agent       | Per-user                                 | Per-project                          |
| ----------- | ---------------------------------------- | ------------------------------------ |
| Claude Code | `~/.claude/skills/`                      | `.claude/skills/`                    |
| Codex       | `~/.codex/skills/`                       | `.codex/skills/`                     |
| Cursor      | `~/.agents/skills/`, `~/.cursor/skills/` | `.agents/skills/`, `.cursor/skills/` |
| Gemini CLI  | `~/.agents/skills/`, `~/.gemini/skills/` | `.agents/skills/`, `.gemini/skills/` |

`~/.agents/skills/` is the shared path several agents are converging on, so try it first if yours is
not listed. The [client showcase](https://agentskills.io/clients) tracks which tools support the
format and links each one's setup notes.

**2. Link the skill into it.** Prefer the per-user directory: you author a task while sitting in
your _own_ repository, not in this one, so a per-project install here would never be loaded when you
need it.

```bash
git clone https://github.com/android-bench/community-dataset.git
cd community-dataset/skills

mkdir -p ~/.agents/skills
ln -s "$(pwd)/android-bench-task" ~/.agents/skills/
```

Substitute whichever directory step 1 gave you. Copy the folder instead of symlinking if you would
rather it not change under you when you `git pull`.

**3. Check it loaded.** Most agents list available skills, or accept `/android-bench-task`. You can
also just say what you want — the skill's `description` covers the usual phrasings, so an agent
picks it up when you mention Android Bench, `task.toml`, `solution.patch`, or contributing a task.

### If your agent does not support skills

The format degrades gracefully, because `SKILL.md` is ordinary Markdown with no required tooling.
Point your agent at the file, or paste it in:

```
Read skills/android-bench-task/SKILL.md and follow it. It references files under
references/ and assets/ — read those when it tells you to, not before.
```

That last sentence matters: the skill is written to pull in reference files one phase at a time, and
feeding it all ~3,000 lines up front wastes context on material the current step does not need.

## Why a plain `skills/` directory

The Agent Skills standard defines the _skill folder_ and deliberately not the directory that holds
it, so every client picks its own scan path. Keeping the canonical copy at `skills/` commits this
repository to none of the agents, and makes it easy to symlink into whatever path your agent expects.

## Contributing a skill

Keep `SKILL.md` under ~500 lines and push detail into `references/`, which the agent reads only when
a phase needs it. Anything that should run rather than be described belongs in `scripts/`. Write for
an agent that has none of your context: name real paths, and give commands rather than descriptions
of commands.

Cite the code, not the docs. Several things in this repository disagree with each other —
`task-template.toml` still shows the V1 flat manifest, `v2/README.md` calls `task.toml` the
definitive manifest, and `v2.task create` prints a next step pointing at the wrong file. A skill
that repeats those sends its reader down a path the tooling does not support, so cite
`v2/task_commands/*.py` with line numbers and re-check them when the tooling changes.
