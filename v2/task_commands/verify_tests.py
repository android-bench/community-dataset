# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Host command to execute F2P/P2P test verification inside Docker containers."""

import argparse
import asyncio
import json
import logging
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence
from uuid import uuid4
import tomllib
import tomli_w

from harbor.environments.factory import EnvironmentFactory
from harbor.models.environment_type import EnvironmentType
from harbor.models.task.task import Task
from harbor.models.trial.paths import TrialPaths

from rich import print as rprint
from rich.panel import Panel
from rich.table import Table

# Dynamically resolve repository ROOT_DIR
ROOT_DIR = Path(__file__).resolve().parents[2]

from v2.task_commands.common import (
    DEFAULT_DATASET_DIR,
    add_common_task_args,
    discover_tasks,
    print_error_panel,
    print_step_msg,
    print_success_msg,
)
from v2.task_commands.docker import (
    docker_image_exists,
    get_docker_image_tag,
)

logger = logging.getLogger("v2.verify_tests")


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Registers verify_tests CLI flags."""
    add_common_task_args(parser)
    parser.add_argument(
        "--write",
        "--update",
        action="store_true",
        dest="write",
        help="Update tests/spec.toml with the computed F2P and P2P sets.",
    )


def extract_commands(commands_field: Any) -> List[str]:
    """Helper to cleanly parse a single command string or list of commands."""
    if not commands_field:
        return []
    if isinstance(commands_field, str):
        return [commands_field]
    if isinstance(commands_field, list) or isinstance(commands_field, Sequence):
        return [str(c) for c in commands_field if c]
    return []


def update_spec_toml(task_path: Path, f2p_list: List[str], p2p_list: List[str]) -> bool:
    """Updates tests/spec.toml's acceptance_criteria block with verified lists."""
    spec_toml = task_path / "tests" / "spec.toml"
    if not spec_toml.is_file():
        return False
    try:
        with open(spec_toml, "rb") as f:
            data = tomllib.load(f)

        if "acceptance_criteria" not in data:
            data["acceptance_criteria"] = {}

        data["acceptance_criteria"]["fail_to_pass"] = f2p_list
        data["acceptance_criteria"]["pass_to_pass"] = p2p_list

        toml_text = tomli_w.dumps(data)
        spec_toml.write_text(toml_text, encoding="utf-8")
        return True
    except Exception as e:
        print_error_panel(f"[{task_path.name}] Failed to update tests/spec.toml: {e}")
        return False


def run_verification_on_task(
    t_id: str,
    t_path: Path,
    t_data: Dict[str, Any],
    write_back: bool,
    dataset_dir: Path,
) -> bool:
    """Runs verification inside Docker for a single task and processes the result."""
    # 1. Check if docker image exists, build automatically if missing
    tag = get_docker_image_tag(t_id, t_data)
    if not docker_image_exists(tag):
        print_step_msg(
            f"[{t_id}] Docker image '{tag}' not found locally. Triggering automatic container build..."
        )
        try:
            from v2.task_commands.docker import main_with_args as docker_main_with_args

            build_args = argparse.Namespace(
                dataset_dir=dataset_dir,
                task_id=[t_id],
                tasks_filter=None,
                clone=True,
                generate=True,
                build=True,
                push=False,
                max_workers=1,
            )
            docker_main_with_args(build_args)
            if not docker_image_exists(tag):
                print_error_panel(
                    f"Docker image build finished, but image '{tag}' still does not exist."
                )
                return False
        except Exception as e:
            print_error_panel(f"Failed to build Docker container for task {t_id}: {e}")
            return False

    # 2. Check if solution script and test patches are present
    solve_sh = t_path / "solution" / "solve.sh"
    test_patch = t_path / "tests" / "test.patch"

    if not solve_sh.is_file():
        print_error_panel(
            f"Solution script not found at: {solve_sh}", title=f"MISSING FILE: {t_id}"
        )
        return False

    if not test_patch.is_file():
        print_error_panel(
            f"Test patch not found at: {test_patch}", title=f"MISSING FILE: {t_id}"
        )
        return False

    # 3. Create host results path and map relative directories
    temp_dir = ROOT_DIR / "temp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    results_json_file = temp_dir / f"verify_tests_{t_id}.json"

    # Ensure results file is cleared first
    if results_json_file.exists():
        results_json_file.unlink()

    # Calculate paths relative to ROOT_DIR
    try:
        task_dir_rel = t_path.resolve().relative_to(ROOT_DIR.resolve())
        test_patch_rel = test_patch.resolve().relative_to(ROOT_DIR.resolve())
        results_json_rel = results_json_file.resolve().relative_to(ROOT_DIR.resolve())
    except ValueError as e:
        print_error_panel(
            f"Task directory or repository root structure is invalid: {e}"
        )
        return False

    # 4. Extract commands from spec data
    commands = t_data.get("commands", {})
    build_cmds = extract_commands(commands.get("build"))
    unit_cmds = extract_commands(commands.get("unit_test"))
    android_cmds = extract_commands(commands.get("android_test"))

    before_commit = t_data.get("before_commit", {}).get("sha")
    if not before_commit:
        print_error_panel(
            f"No before_commit.sha found in tests/spec.toml for task {t_id}"
        )
        return False

    target_sdk = t_data.get("before_commit", {}).get("target_sdk", 34)
    java_version = t_data.get("before_commit", {}).get("java_version", 17)
    after_agent_cmds = extract_commands(t_data.get("after_agent"))

    # 5. Build and execute runner using Harbor SDK
    async def run_async() -> bool:
        task = Task(t_path)
        emulator_name = task.config.environment.env.get(
            "EMULATOR_NAME", f"test_emulator_{target_sdk}"
        )

        # Setup mounts: mount host repository root to /dataset_root and task solution to /solution
        mounts = [
            {
                "type": "bind",
                "source": str(ROOT_DIR.resolve()),
                "target": "/dataset_root",
            },
            {
                "type": "bind",
                "source": str((t_path / "solution").resolve()),
                "target": "/solution",
            },
        ]

        with tempfile.TemporaryDirectory() as temp_trial_dir:
            trial_paths = TrialPaths(trial_dir=Path(temp_trial_dir))
            trial_paths.mkdir()

            environment = EnvironmentFactory.create_environment(
                EnvironmentType.DOCKER,
                environment_dir=task.paths.environment_dir,
                environment_name=task.short_name,
                session_id=str(uuid4()),
                trial_paths=trial_paths,
                task_env_config=task.config.environment,
                mounts=mounts,
            )

            print_step_msg(f"[{t_id}] Starting Harbor-managed environment container...")
            await environment.start(force_build=False)
            try:
                # Format runner arguments
                runner_args = [
                    "python3",
                    "-m",
                    "v2.task_commands.verify_tests_runner",
                    "--task-id",
                    t_id,
                    "--task-dir",
                    str(task_dir_rel),
                    "--before-commit",
                    before_commit,
                    "--build-commands",
                    json.dumps(build_cmds),
                    "--unit-test-commands",
                    json.dumps(unit_cmds),
                    "--android-test-commands",
                    json.dumps(android_cmds),
                    "--solution-script",
                    "/solution/solve.sh",
                    "--test-patch",
                    str(test_patch_rel),
                    "--results-output",
                    str(results_json_rel),
                    "--target-sdk",
                    str(target_sdk),
                    "--emulator-name",
                    emulator_name,
                    "--java-version",
                    str(java_version),
                    "--after-agent-commands",
                    json.dumps(after_agent_cmds),
                ]

                logger.info(f"Executing verify_tests_runner inside environment...")
                exec_result = await environment.exec(
                    command=shlex.join(runner_args),
                    env={"PYTHONPATH": "/dataset_root"},
                )

                if exec_result.stdout:
                    print(exec_result.stdout, flush=True)
                if exec_result.stderr:
                    print(exec_result.stderr, file=sys.stderr, flush=True)

                if exec_result.return_code != 0:
                    print_error_panel(
                        f"Verification container exited with non-zero exit code: {exec_result.return_code}",
                        title=f"VERIFICATION FAILED: {t_id}",
                    )
                    return False
            except Exception as e:
                print_error_panel(f"Failed to run verification inside container: {e}")
                return False
            finally:
                logger.info("Stopping Harbor-managed environment container...")
                await environment.stop(delete=True)

        return True

    if not asyncio.run(run_async()):
        return False

    # 6. Read results file
    if not results_json_file.is_file():
        print_error_panel(
            "Verification runner completed but did not output results JSON file."
        )
        return False

    try:
        results = json.loads(results_json_file.read_text())
    except Exception as e:
        print_error_panel(f"Failed to parse verify results JSON file: {e}")
        return False

    f2p = results.get("fail_to_pass", [])
    p2p = results.get("pass_to_pass", [])
    flaky = results.get("flaky", [])
    breaking = results.get("breaking", [])

    # State check warnings
    state1_info = results.get("state1", {})
    state2_info = results.get("state2", {})

    warnings_list = []
    if not state1_info.get("apply_success"):
        warnings_list.append("• Test patch failed to apply without golden patch.")
    elif not state1_info.get("build_success"):
        warnings_list.append("• Build failed without golden patch.")

    if not state2_info.get("apply_success"):
        warnings_list.append(
            "• Golden or test patch failed to apply with golden patch."
        )
    elif not state2_info.get("build_success"):
        warnings_list.append("• Build failed with golden patch.")

    # Render summary tables
    rprint(f"\n[bold yellow]═══ Verification Results for {t_id} ═══[/]")

    if warnings_list:
        rprint("[bold red]⚠️  Compilation/Apply Warnings:[/]")
        for w in warnings_list:
            rprint(f"  [red]{w}[/]")
        rprint()

    # Tables for F2P / P2P
    metrics_table = Table(
        title="Test Category Metrics Summary",
        show_header=True,
        header_style="bold magenta",
    )
    metrics_table.add_column("Category", style="cyan")
    metrics_table.add_column("Count", justify="right", style="green")
    metrics_table.add_row("Fail to Pass (F2P)", str(len(f2p)))
    metrics_table.add_row("Pass to Pass (P2P)", str(len(p2p)))
    metrics_table.add_row(
        "Flaky / Non-breaking", str(len(flaky)), style="yellow" if flaky else "cyan"
    )
    metrics_table.add_row(
        "Breaking Tests", str(len(breaking)), style="bold red" if breaking else "cyan"
    )
    rprint(metrics_table)

    def print_test_list(title: str, tests: List[str], color: str):
        if not tests:
            rprint(f"\n[bold {color}]{title}: (None)[/]")
            return
        rprint(f"\n[bold {color}]{title} ({len(tests)}):[/]")
        for t in tests:
            rprint(f"  • {t}")

    print_test_list("Fail-to-Pass Tests (F2P)", f2p, "green")
    print_test_list("Pass-to-Pass Tests (P2P)", p2p, "blue")

    if flaky:
        rprint(f"\n[bold yellow]⚠️ Flaky / Non-Breaking Tests ({len(flaky)}):[/]")
        for t in flaky:
            rprint(f"  • [yellow]{t}[/]")

    if breaking:
        rprint(f"\n[bold red]❌ Breaking Tests ({len(breaking)}):[/]")
        for t in breaking:
            rprint(f"  • [red]{t}[/]")

    # 7. Write results back if requested
    if write_back:
        if update_spec_toml(t_path, f2p, p2p):
            print_success_msg(f"Successfully updated tests/spec.toml for {t_id}")
        else:
            return False

    # Clean up JSON temp file
    results_json_file.unlink(missing_ok=True)
    return True


def main(args: Optional[argparse.Namespace] = None) -> None:
    if args is None:
        parser = argparse.ArgumentParser(
            description="Verify F2P and P2P sets for Dataset V2 Tasks."
        )
        add_arguments(parser)
        args = parser.parse_args()

    dataset_root: Path = getattr(
        args,
        "dataset_dir",
        DEFAULT_DATASET_DIR,
    )

    tasks = discover_tasks(
        dataset_root,
        getattr(args, "task_id", None),
        getattr(args, "tasks_filter", None),
    )

    if not tasks:
        logger.warning("No Dataset V2 tasks matched your selection criteria.")
        return

    logger.info(f"Starting test verification across {len(tasks)} task(s)...")

    success_count = 0
    fail_count = 0

    for t_id, t_path, t_data in tasks:
        rprint(
            Panel(
                f"Starting test verification run for: [bold cyan]{t_id}[/]",
                border_style="cyan",
            )
        )
        if run_verification_on_task(
            t_id, t_path, t_data, getattr(args, "write", False), dataset_root
        ):
            success_count += 1
        else:
            fail_count += 1

    rprint(
        Panel(
            f"Successfully verified tests for [bold green]{success_count}[/] task(s).\n"
            f"Failed or skipped verification for [bold red]{fail_count}[/] task(s).",
            title="[bold green]Test Verification Run Complete[/]",
            border_style="green",
        )
    )


if __name__ == "__main__":
    main()
