<!--
Copyright 2026 Google LLC

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
-->

# Dataset V2 CLI (`v2.task`)

CLI for creating, building, and running Dataset V2 tasks.

> **Single Source of Truth:** `task.toml` is the definitive manifest. Subcommands (`docker`, `refresh-patches`) read it and automatically reformat/re-dump it to enforce standard styling.

## Quickstart

```bash
v2.task <subcommand> [TASK_ID] [OPTIONS]
```

- [`create`](#step-1-create-a-task-v2task-create): Interactive wizard or flags to scaffold tasks.
- [`docker`](#step-2-build-docker-container-v2task-docker): Generate `Dockerfile` and build containers.
- [`refresh-patches`](#step-3-capture-patches-v2task-refresh-patches): Extract canonical `solution.patch` and `test.patch`.
- [`verify-tests`](#step-4-verify-tests-v2task-verify-tests): Determine F2P and P2P sets for tasks inside Docker.

## Command Overview

Use `v2.task` subcommands during the following stages of task creation and curation:

* **`v2.task create`**: Use when initializing a new task to scaffold `tasks/<task_id>/` directory layout, `task.toml`, and `instruction.md`.
* **`v2.task docker`**: Use when building or updating container environments. Clones target repositories and generates `environment/Dockerfile`.
* **`v2.task refresh-patches`**: Use after modifying task implementations to recalculate clean `solution.patch` and `test.patch` diff files.
* **`v2.task verify-tests`**: Use before task submission to run tests inside Docker containers and generate mutually exclusive F2P/P2P test sets.

> **Task Discovery & Help:** All subcommands accept `--dataset-dir PATH` (default: `tasks`) and recursively discover tasks across subfolders (e.g. `tasks/`), skipping `utils/` and `environment/`. Supports relative subfolder globs (e.g. `tasks/*`) and unquoted shell directory expansions.

## Task Creation Workflow

### Step 1: Create a Task (`v2.task create`)
Scaffolds task folders under `tasks/` with `task.toml` and `instruction.md`.

```bash
# Launch interactive wizard
v2.task create

# Or create via CLI flags
v2.task create pocketcasts-room-1 \
    --repo-url "https://github.com/Automattic/pocket-casts-android.git" \
    --before-sha "94d54b606d3df89f5d0bb430483f2ffc64db47cb" \
    --after-sha "8080d85c36dc887d7900390ac7ce434d24203ffc" \
    --test-files "app/src/androidTest/java/au/com/shiftyjelly/pocketcasts/ui/MainActivityTest.kt" \
    --defaults
```

---

### Step 2: Build Docker Container (`v2.task docker`)
Clones repo, generates container files (`environment/*`) from `task.toml`, builds locally, and verifies runtime initialization (running `docker compose`). 

```bash
# Build locally
v2.task docker pocketcasts-room-1

# Generate manifests without building
v2.task docker pocketcasts-room-1 --generate --no-build
```

### Overwrite Prevention (`# >>> SKIP_GENERATE <<<`)
To prevent overwriting custom Dockerfiles (via `v2.task docker`), add the following to the first line of `environment/Dockerfile` or `environment/docker-compose.yaml`:
```
# >>> SKIP_GENERATE <<<
``` 

### Container Customization via `task.toml`
Because `docker --generate` translates `task.toml` into container configs, customize behavior via:

#### Setup Commands (`[commands].docker_setup`)
Use for CLI utilities (`apt-get`, `pip`, `curl`) installed near top of Dockerfile for layer caching. For example:
  ```toml
  [commands]
  docker_setup = [
      "apt-get update && apt-get install -y xxd openssl telnet && rm -rf /var/lib/apt/lists/*",
      "pip install --upgrade pip && pip install tomli Pillow pydantic"
  ]
  ```
#### Git Exclusions (`[repository].remove_from_git_history`)

Confidential files or runner tools stripped from the Git repository history:
  ```toml
  [repository]
  remove_from_git_history = [
      ".gemini",
      "conductor",
      ".agents",
      "AGENTS.md",
      "GEMINI.md",
      "private_keys/"
  ]
  ```
#### Pre-Build Steps (`[commands].before_build`) 

Commands run during Docker container build before Gradle compilation:
  ```toml
  [commands]
  before_build = ["RUNS BEFORE BUILDING THE APP DURING THE DOCKER CONTAINER CREATION"]
  build = ["./gradlew assembleDebug"]
  ```

#### Runtime Env Vars (`[verifier.env]`)
API keys or host variables interpolated into evaluation runtime containers:
  ```toml
  [verifier.env]
  MAPS_API_KEY = "${MAPS_API_KEY}"       # Interpolated from host environment
  GEMINI_API_KEY = "${GEMINI_API_KEY}"
  ```

---

### Step 3: Capture Patches (`v2.task refresh-patches`)
Extracts canonical `solution/solution.patch` and `tests/test.patch` between `before_commit.sha` and `after_commit.sha`.

```bash
v2.task refresh-patches pocketcasts-room-1
```

#### Patch Scoping & Filtering (`task.toml`)
During diff calculation, `task.toml` fields act as diff filters:

- **Test Suite Scoping (`[acceptance_criteria].test_files`)**: Isolated exclusively into `tests/test.patch` and stripped from `solution/solution.patch`:
  ```toml
  [acceptance_criteria]
  test_files = ["app/src/androidTest/java/com/example/MainActivityTest.kt"]
  ```
- **Ignored Paths (`[repository].ignored_files`)**: Omitted from **both** `solution.patch` and `test.patch`:
  ```toml
  [repository]
  ignored_files = ["build/", ".gradle/", "local.properties"]
  ```

*(Note: `@ExcludeFromDataset` annotated code and `remove_from_git_history` entries are also omitted).*

---

### Step 4: Verify Tests (`v2.task verify-tests`)
Executes, isolates, and dynamically classifies test outcomes for a task inside its corresponding Docker container with hardware-accelerated KVM support. It determines the mathematically disjoint `fail_to_pass` (F2P), `pass_to_pass` (P2P), `flaky`, and `breaking` test sets.

```bash
# Run verification on a single task
v2.task verify-tests pocketcasts-room-1

# Run verification and automatically update tests/spec.toml with computed F2P and P2P sets
v2.task verify-tests pocketcasts-room-1 --write
```

#### Self-Healing & Exclusions
- **Automated Container Setup**: If the local Docker image is missing, the tool automatically clones missing repository layers, generates Dockerfiles, and compiles the image prior to launching the verifier.
- **Gradle Task Splitting**: Splits multi-target Gradle runs into independent task execution steps so that configuration/compilation failures in one subproject/module do not halt or abort other valid subprojects.
- **Kotlin/Java Compiler Feedback Loop**: If compiling `test.patch` fails because of missing golden references, the loop detects the compilation error, parses and marks those individual test classes/methods as failed, excludes them from the compiler (renaming to `.bak`), and retries the Gradle task recursively.
- **Strict Disjoint Classification**: Enforces a strict outcome matrix where successful test runs dynamically override transient compile failures or stale XML artifacts, delivering pristine mutually exclusive test sets.
- **Continuous Real-Time Feedback**: Streams container output line-by-line continuously to your terminal, providing instant visibility into compiler loops, emulator boots, and test runs.

## Batch Processing

Run subcommands across multiple tasks via wildcards or YAML list files:

```bash
# Target all tasks
v2.task refresh-patches --all

# Target wildcard groups (positional glob or --tasks-filter)
v2.task refresh-patches pocketcasts-*
v2.task refresh-patches --tasks-filter "pocketcasts-*"

# Target YAML list files (supports parallel workers)
v2.task docker --tasks-filter batch_tasks.yaml --max-workers 8
```
