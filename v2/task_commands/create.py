#!/usr/bin/env python3
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
"""
Canonical Interactive Dataset V2 Task Scaffolding Wizard (`v2.create`).
Implements interactive Rich prompting and non-interactive scripted scaffolding.
"""

import argparse
from datetime import datetime, timezone
import logging
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Dict, List, Optional, Tuple, Union

from rich import print as rprint
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, IntPrompt, Prompt
import tomli_w

from v2.task_commands.common import (
    DEFAULT_DATASET_DIR,
    add_common_task_args,
    get_staged_repo_dir,
    setup_repo,
)
from v2.task_commands.docker import generate_task_dockerfile

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
logger = logging.getLogger("v2.create")
console = Console()


def _copy_verifier_files(verifier_src: Path, verifier_dst: Path) -> None:
    """Copies verifier files from verifier_src into verifier_dst."""
    verifier_dst.mkdir(parents=True, exist_ok=True)
    for item in verifier_src.rglob("*"):
        if item.is_dir():
            if item.name == "__pycache__":
                continue
            rel = item.relative_to(verifier_src)
            (verifier_dst / rel).mkdir(parents=True, exist_ok=True)
        elif item.is_file():
            if item.suffix in (".pyc", ".pyo"):
                continue
            rel = item.relative_to(verifier_src)
            target = verifier_dst / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(item.read_bytes())


def print_warning_panel(msg: str, title: str = "WARNING") -> None:
    console.print(
        Panel(
            f"[yellow]{msg}[/]",
            title=f"[bold yellow]{title}[/]",
            border_style="yellow",
        )
    )


def print_error_panel(msg: str, title: str = "ERROR") -> None:
    console.print(
        Panel(f"[red]{msg}[/]", title=f"[bold red]{title}[/]", border_style="red")
    )


def print_prompt_desc(desc: str, title: Optional[str] = None) -> None:
    """Wraps every prompt explanation inside a subtle bordered panel."""
    console.print(
        Panel(
            f"[dim]{desc}[/]",
            border_style="dim",
            **({"title": f"[bold blue]{title}[/]"} if title else {}),
        )
    )


def strip_quotes(s: Optional[Union[str, Any]]) -> str:
    """Removes leading and trailing quote characters."""
    if not isinstance(s, str) or not s:
        return ""
    cleaned = s.strip()
    if cleaned.startswith(r"\"") and cleaned.endswith(r"\""):
        cleaned = cleaned[2:-2].strip()
    if cleaned.startswith('"') and cleaned.endswith('"'):
        cleaned = cleaned[1:-1].strip()
    if cleaned.startswith("'") and cleaned.endswith("'"):
        cleaned = cleaned[1:-1].strip()
    return cleaned


def derive_commit_from_change(
    repo_url: str, change_id: Union[int, str], repo_dir: Path, prefer_ps1: bool = False
) -> Optional[str]:
    """Queries Gerrit via git ls-remote to resolve a specific Change ID.

    If prefer_ps1 is True, returns Patchset 1; otherwise returns Latest Patchset.
    """
    change_id_str = str(change_id).strip()
    label = "PS1" if prefer_ps1 else "Latest PS"
    logger.info(
        f"Deriving {label} commit for Change ID {change_id_str} via git ls-remote..."
    )

    cmd = [
        "git",
        "ls-remote",
        "--sort=v:refname",
        repo_url,
        f"refs/changes/*/{change_id_str}/*",
    ]
    try:
        output = subprocess.check_output(cmd, text=True).strip()
    except subprocess.CalledProcessError:
        logger.error(f"Failed to query ls-remote for Change ID {change_id_str}")
        return None

    if not output:
        logger.warning(
            f"No valid refs discovered for Change ID {change_id_str} on {repo_url}"
        )
        return None

    ps_lines = [line.split("\t") for line in output.splitlines() if "meta" not in line]
    if not ps_lines:
        return None

    if not repo_dir.is_dir():
        repo_dir.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "-b", "main"], cwd=repo_dir, check=True)
        subprocess.run(
            ["git", "remote", "add", "origin", repo_url], cwd=repo_dir, check=True
        )

    target_ref = ps_lines[0][1] if prefer_ps1 else ps_lines[-1][1]
    logger.info(f"Resolved Change ID {change_id_str} ref: {target_ref}")

    subprocess.run(
        ["git", "fetch", "origin", target_ref],
        cwd=repo_dir,
        check=True,
        capture_output=True,
    )
    sha = subprocess.check_output(
        ["git", "rev-parse", "FETCH_HEAD"], cwd=repo_dir, text=True
    ).strip()
    return sha


def retrieve_project_config(repo_path: Path) -> Tuple[int, int]:
    """Scans Gradle build files in repo_path to automatically detect Java version and Target SDK."""
    detected_java = 17
    detected_sdk = 34

    if not repo_path.is_dir():
        return detected_java, detected_sdk

    build_files = []
    for ext in ("*.gradle.kts", "*.gradle", "*.toml"):
        build_files.extend(repo_path.rglob(ext))

    java_found = False
    sdk_found = False

    for bf in build_files:
        if java_found and sdk_found:
            break
        try:
            content = bf.read_text(encoding="utf-8", errors="ignore")

            # Detect Target / Compile SDK
            if not sdk_found:
                m = re.search(
                    r"(?:targetSdk|targetSdkVersion|compileSdk)\s*=?\s*(\d{2})",
                    content,
                )
                if m:
                    detected_sdk = int(m.group(1))
                    sdk_found = True

            # Detect Java version
            if not java_found:
                m_java = re.search(
                    r"(?:VERSION_1_(\d)|VERSION_(\d{2})|jvmTarget\s*=\s*[\"'](\d+)[\"'])",
                    content,
                )
                if m_java:
                    val = m_java.group(1) or m_java.group(2) or m_java.group(3)
                    if val:
                        v = int(val)
                        if v == 8:
                            detected_java = 8
                        elif v in (11, 17, 21):
                            detected_java = v
                        java_found = True
        except Exception:
            pass

    return detected_java, detected_sdk


def select_numbered_list_option(
    title: str, options: list[tuple[str, str]], default_idx: int = 4
) -> str:
    """Renders a plain numbered list selection menu highlighting default option in green."""
    lines = []
    for idx, (val, desc) in enumerate(options, 1):
        if idx == default_idx:
            lines.append(
                f"  [bold green]{idx}[/] - [bold green]{val}[/] [bold green]({desc}) [[default]][/]"
            )
        else:
            lines.append(f"  [bold yellow]{idx}[/] - [cyan]{val}[/] [dim]({desc})[/]")

    menu_str = "\n".join(lines)
    console.print(
        Panel(
            menu_str,
            title=f"[bold cyan]{title}[/]",
            border_style="cyan",
        )
    )

    choice_map = {str(idx): val for idx, (val, _) in enumerate(options, 1)}
    choice_keys = list(choice_map.keys())
    default_key = str(default_idx)

    selected_key = Prompt.ask(
        f"[bold cyan]Enter option number[/] [[yellow]1-{len(options)}[/]] [[green]default={default_idx}[/]]",
        choices=choice_keys,
        default=default_key,
        show_choices=False,
        show_default=False,
    )

    return choice_map[selected_key]


def prompt_test_files() -> str:
    """Reads comma-separated or multi-line pasted test files until an empty line."""
    console.print(
        "[bold cyan]Test Files[/] (comma-separated or paste multi-line; press [green]Enter[/] on an empty line to finish):"
    )
    lines = []
    while True:
        try:
            line = input()
            if not line or not line.strip():
                break
            lines.append(line)
        except (EOFError, KeyboardInterrupt):
            break
    return "\n".join(lines)


def scaffold_task(args: argparse.Namespace) -> None:
    v2_dir: Path = getattr(
        args, "dataset_dir", getattr(args, "v2_dir", DEFAULT_DATASET_DIR)
    )
    v2_dir.mkdir(parents=True, exist_ok=True)

    interactive = not getattr(args, "defaults", False)
    if getattr(args, "no_interactive", False):
        interactive = False

    if interactive:
        console.print(
            Panel(
                "[bold green]Scaffolding fresh Dataset V2 task layout & manifest.[/]",
                title="[bold cyan]✨ Dataset V2 Task Creation Wizard ✨[/]",
                border_style="cyan",
            )
        )

    # 1. Strict Lowercase Task ID Enforcement (^[a-z0-9_-]+$)
    task_id = strip_quotes(getattr(args, "task_id", None))
    if interactive:
        print_prompt_desc(
            "Assign a unique, descriptive ID for this task using lowercase letters and hyphens (e.g., pocketcasts-room-1). This creates the task folder under tasks/."
        )
        while (
            not task_id
            or not task_id.strip()
            or not re.match(r"^[a-z0-9_-]+$", task_id.strip())
        ):
            task_id = strip_quotes(
                Prompt.ask("[bold cyan]Enter new Task ID[/] (must be lowercase)")
            )
            if not task_id or not task_id.strip():
                rprint("[bold red]Error: Task ID is strictly required.[/]")
            elif not re.match(r"^[a-z0-9_-]+$", task_id.strip()):
                rprint(
                    "[bold red]Error: Task ID must contain only lowercase letters, digits, hyphens, or underscores.[/]"
                )
                task_id = None
    if not task_id or not task_id.strip():
        logger.error("Task ID (--task-id) is required.")
        sys.exit(1)
    if not re.match(r"^[a-z0-9_-]+$", task_id.strip()):
        logger.error(
            "Task ID (--task-id) must contain only lowercase letters, digits, hyphens, or underscores."
        )
        sys.exit(1)

    task_id = task_id.strip()
    task_dir = v2_dir / task_id

    if task_dir.is_dir():
        logger.info(
            f"Task directory {task_dir} already exists. Overwriting automatically."
        )

    # 2 & 3. Repository URL with NO e.g. guidance; explicit error on empty
    repo_url = strip_quotes(getattr(args, "repo_url", None))
    if interactive:
        print_prompt_desc(
            "Enter the upstream Git repository URL (SSH or HTTP) where the project lives."
        )
        while not repo_url or not repo_url.strip():
            repo_url = strip_quotes(Prompt.ask("[bold cyan]Repository URL[/]"))
            if not repo_url or not repo_url.strip():
                rprint(
                    "[bold red]Error: Repository URL is strictly required. Please enter a valid Git URL.[/]"
                )
    if not repo_url or not repo_url.strip():
        logger.error(
            "Repository URL (--repo-url) is required. Please enter a valid Git URL."
        )
        sys.exit(1)

    repo_url = strip_quotes(repo_url)

    before_change_id = strip_quotes(getattr(args, "before_change_id", None))
    after_change_id = strip_quotes(getattr(args, "after_change_id", None))
    change_id = strip_quotes(getattr(args, "change_id", None))

    if change_id and not before_change_id and not after_change_id:
        before_change_id = change_id
        after_change_id = change_id

    before_sha = strip_quotes(getattr(args, "before_sha", None))
    after_sha = strip_quotes(getattr(args, "after_sha", None))
    skip_clone = getattr(args, "skip_clone", False)

    is_gerrit = "googlesource.com" in repo_url

    if interactive and is_gerrit and not before_sha and not after_change_id:
        print_prompt_desc(
            "Gerrit review repo detected. Let's derive baseline and golden commit SHAs automatically from Gerrit Change IDs."
        )
        if Confirm.ask(
            "[bold cyan]Gerrit review repo detected. Derive commits from Gerrit Change ID(s)?[/] [[green]default=y[/]]",
            default=True,
            show_default=False,
        ):
            before_change_id = strip_quotes(
                Prompt.ask(
                    "[bold cyan]Before Commit Change ID[/] (e.g., [yellow]1080[/], leave blank if none)"
                )
            )
            after_change_id = strip_quotes(
                Prompt.ask(
                    "[bold cyan]After Commit Change ID[/] (e.g., [yellow]1081[/], leave blank if identical to before)"
                )
            )

    before_change_id = before_change_id.strip() if before_change_id else None
    after_change_id = after_change_id.strip() if after_change_id else None

    if before_change_id and not after_change_id:
        after_change_id = before_change_id
    elif after_change_id and not before_change_id:
        before_change_id = after_change_id

    repo_dir = Path(get_staged_repo_dir(task_dir))

    if before_change_id and after_change_id and (not before_sha or not after_sha):
        if before_change_id == after_change_id:
            # Same CL: Before = PS1, After = Latest PS
            b_sha = derive_commit_from_change(
                repo_url, before_change_id, repo_dir, prefer_ps1=True
            )
            a_sha = derive_commit_from_change(
                repo_url, after_change_id, repo_dir, prefer_ps1=False
            )
        else:
            # Distinct CLs: Before = Latest PS of before_change_id, After = Latest PS of after_change_id
            b_sha = derive_commit_from_change(
                repo_url, before_change_id, repo_dir, prefer_ps1=False
            )
            a_sha = derive_commit_from_change(
                repo_url, after_change_id, repo_dir, prefer_ps1=False
            )

        if b_sha:
            before_sha = b_sha
        if a_sha:
            after_sha = a_sha

    # 4 & 5. No defaults for Before Commit SHA or After Commit SHA! Both are strictly required 40-char alphanumeric.
    if interactive:
        print_prompt_desc(
            "Enter the 40-character Git commit SHA for the before commit. This defines the exact codebase state presented to agents."
        )
        while not before_sha or not re.match(r"^[a-zA-Z0-9]{40}$", before_sha.strip()):
            before_sha = strip_quotes(
                Prompt.ask("[bold cyan]Before Commit SHA[/] (mandatory 40-char hash)")
            )
            if not before_sha or not re.match(r"^[a-zA-Z0-9]{40}$", before_sha.strip()):
                print_error_panel(
                    "Commit SHA must be exactly 40 alphanumeric characters.",
                    title="Invalid SHA",
                )
                before_sha = None

        print_prompt_desc("Enter the 40-character Git commit SHA for the after commit.")
        while not after_sha or not re.match(r"^[a-zA-Z0-9]{40}$", after_sha.strip()):
            after_sha = strip_quotes(
                Prompt.ask("[bold cyan]After Commit SHA[/] (mandatory 40-char hash)")
            )
            if not after_sha or not re.match(r"^[a-zA-Z0-9]{40}$", after_sha.strip()):
                print_error_panel(
                    "Commit SHA must be exactly 40 alphanumeric characters.",
                    title="Invalid SHA",
                )
                after_sha = None
    else:
        if not before_sha or not re.match(r"^[a-zA-Z0-9]{40}$", before_sha.strip()):
            print_error_panel(
                "Before Commit SHA (--before-sha) must be exactly 40 alphanumeric characters.",
                title="Invalid SHA",
            )
            sys.exit(1)
        if not after_sha or not re.match(r"^[a-zA-Z0-9]{40}$", after_sha.strip()):
            print_error_panel(
                "After Commit SHA (--after-sha) must be exactly 40 alphanumeric characters.",
                title="Invalid SHA",
            )
            sys.exit(1)

    before_sha = before_sha.strip()
    after_sha = after_sha.strip()

    task_dir.mkdir(parents=True, exist_ok=True)

    java_ver = getattr(args, "java_version", None)
    target_sdk = getattr(args, "target_sdk", None)

    if not skip_clone and (java_ver is None or target_sdk is None):
        print_info_panel(
            f"🚀 Cloning repository [bold]{repo_url}[/] to analyze project build properties...",
            title="Git Staging",
        )
        temp_toml = task_dir / "task.toml"
        temp_content = (
            f'id = "{task_id}"\n'
            f'[repository]\nurl = "{repo_url}"\n'
            f'[before_commit]\nsha = "{before_sha}"\n'
        )
        temp_toml.write_text(temp_content)

        if setup_repo(str(task_dir), silent=False):
            det_java, det_sdk = retrieve_project_config(repo_dir)
            if java_ver is None:
                java_ver = det_java
            if target_sdk is None:
                target_sdk = det_sdk
        else:
            print_warning_panel(
                "Could not stage repository during retrieval. Falling back to defaults.",
                title="Git Staging Warning",
            )

    java_ver = java_ver if java_ver is not None else 17
    target_sdk = target_sdk if target_sdk is not None else 34

    if interactive:
        print_prompt_desc(
            "Check the Java JDK and Android Target SDK versions required to build this project inside test runners."
        )
        java_ver = IntPrompt.ask(
            f"[bold cyan]Java Version[/] [[green]default={java_ver}[/]]",
            default=java_ver,
            show_default=False,
        )
        target_sdk = IntPrompt.ask(
            f"[bold cyan]Target SDK[/] [[green]default={target_sdk}[/]]",
            default=target_sdk,
            show_default=False,
        )

    # 6. Plain Numbered Selection List (No Radio Buttons) with 6-Tiered Scale (1h, 4h, 1d, 3d, 7d, 7d+) & Default = 4 (3d)
    time_est = getattr(args, "time_estimate", None)
    if interactive and not time_est:
        print_prompt_desc(
            "Estimate the research and engineering effort required for an agent to solve this task."
        )
        time_est = select_numbered_list_option(
            "Select Effort Estimate Options",
            [
                ("1h", "Quick patch / isolated edit"),
                ("4h", "Standard feature / minor fix"),
                ("1d", "Complex iteration / multi-module"),
                ("3d", "Substantial architectural feature"),
                ("7d", "Major structural overhaul / redesign"),
                ("7d+", "Multi-week research challenge"),
            ],
            default_idx=4,
        )
    time_est = time_est or "3d"

    # Optional Test Files prompt
    tf_val = getattr(args, "test_files", None)
    if interactive and tf_val is None:
        print_prompt_desc(
            "Specify test files (comma-separated or paste multi-line; press Enter on an empty line to finish)."
        )
        tf_val = prompt_test_files()

    if isinstance(tf_val, str):
        raw_items = tf_val.replace(",", "\n").splitlines()
        test_files = [strip_quotes(f) for f in raw_items if strip_quotes(f)]
    elif isinstance(tf_val, list):
        test_files = [strip_quotes(f) for f in tf_val if strip_quotes(f)]
    else:
        test_files = []

    has_assets = getattr(args, "include_assets", False)
    if interactive and not has_assets:
        print_prompt_desc(
            "Indicate whether this task provides external design assets (UI screenshots, icons, mockups)."
        )
        has_assets = Confirm.ask(
            "[bold cyan]Include external assets/ directory for UI design assets?[/]",
            default=False,
            show_default=False,
        )

    DEFAULT_BUILD_CMD = "./gradlew assembleDebug"
    DEFAULT_UNIT_CMD = "./gradlew testDebugUnitTest --continue"
    DEFAULT_ANDROID_CMD = "./gradlew connectedDebugAndroidTest --continue"

    build_cmd = getattr(args, "build_cmd", None)
    build_cmd = strip_quotes(build_cmd) if build_cmd is not None else None

    unit_test_cmd = getattr(args, "unit_test_cmd", None)
    unit_test_cmd = strip_quotes(unit_test_cmd) if unit_test_cmd is not None else None

    android_test_cmd = getattr(args, "android_test_cmd", None)
    android_test_cmd = (
        strip_quotes(android_test_cmd) if android_test_cmd is not None else None
    )
    want_validate = getattr(args, "include_validate", True)

    if interactive:
        print_prompt_desc(
            "Define the primary build command required to verify successful compilation (typically ./gradlew assembleDebug)."
        )
        if not build_cmd:
            build_cmd = strip_quotes(
                Prompt.ask(
                    f"[bold cyan]Build Command[/] [[green]default={DEFAULT_BUILD_CMD}[/]]",
                    default=DEFAULT_BUILD_CMD,
                    show_default=False,
                )
            )

        # Gated Unit Test Prompt
        print_prompt_desc(
            "Unit tests verify isolated JVM business logic. Select whether to include automated Gradle unit test runs."
        )
        want_unit = Confirm.ask(
            "[bold cyan]Include Unit Test verification?[/] [[green]default=y[/]]",
            default=True,
            show_default=False,
        )
        if want_unit:
            print_prompt_desc(
                "Supply the exact scoped Gradle unit test execution string."
            )
            unit_test_cmd = strip_quotes(
                Prompt.ask(
                    f"[bold cyan]Unit Test Command[/] [[green]default={DEFAULT_UNIT_CMD}[/]]",
                    default=DEFAULT_UNIT_CMD,
                    show_default=False,
                )
            )
        else:
            unit_test_cmd = ""

        # Gated Android Test Prompt
        print_prompt_desc(
            "Instrumentation tests run on physical or emulator devices. Select whether to include connected Android test runs."
        )
        want_android = Confirm.ask(
            "[bold cyan]Include Android Instrumentation Test verification?[/] [[green]default=y[/]]",
            default=True,
            show_default=False,
        )
        if want_android:
            print_prompt_desc(
                "Supply the exact scoped instrumentation execution string."
            )
            android_test_cmd = strip_quotes(
                Prompt.ask(
                    f"[bold cyan]Android Test Command[/] [[green]default={DEFAULT_ANDROID_CMD}[/]]",
                    default=DEFAULT_ANDROID_CMD,
                    show_default=False,
                )
            )
        else:
            android_test_cmd = ""

        # Gated Acceptance Validation Check Prompt
        print_prompt_desc(
            "The standalone validation harness (tests/validate.sh) runs custom checks (checking files, running adb commands, regex matching, etc.)."
        )
        want_validate = Confirm.ask(
            "[bold cyan]Include starter Acceptance Validation check harness (tests/validate.sh)?[/] [[green]default=y[/]]",
            default=True,
            show_default=False,
        )

        # Task Metadata Prompts (V2)

        print_prompt_desc(
            "Enter keywords/tags for this task (comma-separated, e.g., compose, room, navigation)."
        )
        keywords_raw = Prompt.ask("[bold cyan]Keywords[/]", default="")
        keywords = (
            [k.strip() for k in keywords_raw.split(",") if k.strip()]
            if keywords_raw
            else []
        )

        print_prompt_desc("Enter the name of the author who created this task.")
        author = strip_quotes(Prompt.ask("[bold cyan]Author Name[/]", default=""))
    else:
        build_cmd = build_cmd or DEFAULT_BUILD_CMD
        unit_test_cmd = unit_test_cmd if unit_test_cmd is not None else DEFAULT_UNIT_CMD
        android_test_cmd = (
            android_test_cmd if android_test_cmd is not None else DEFAULT_ANDROID_CMD
        )

        keywords = []
        author = ""

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000000Z")

    task_dict: dict[str, Any] = {}
    spec_dict: dict[str, Any] = {
        "id": task_id,
    }

    task_dict["task"] = {
        "name": f"android-bench/{task_id.lower()}",
        "keywords": keywords,
        "time_estimate": time_est,
        "author": author,
    }

    spec_dict["repository"] = {
        "url": repo_url,
        "ignored_files": [],
    }

    task_dict["environment"] = {
        "cpus": 16,
        "memory": "72G",
        "storage": "500G",
        "env": {
            "EMULATOR_NAME": f"test_emulator_{target_sdk}",
        },
    }

    task_dict["verifier"] = {
        "timeout_sec": 7200.0,
        "env": {},
    }

    task_dict["agent"] = {
        "timeout_sec": 20000.0,
    }

    spec_dict["before_commit"] = {
        "java_version": java_ver,
        "sha": before_sha,
        "target_sdk": target_sdk,
    }
    if before_change_id:
        spec_dict["before_commit"]["change_id"] = str(before_change_id)

    spec_dict["after_commit"] = {
        "java_version": java_ver,
        "sha": after_sha,
        "target_sdk": target_sdk,
    }
    if after_change_id:
        spec_dict["after_commit"]["change_id"] = str(after_change_id)

    spec_dict["commands"] = {
        "docker_setup": [
            "pip install --upgrade pip && pip install Pillow pydantic"
        ],
        "before_build": [],
        "build": [build_cmd.strip()] if build_cmd and build_cmd.strip() else [],
        "unit_test": (
            [unit_test_cmd.strip()] if unit_test_cmd and unit_test_cmd.strip() else []
        ),
        "android_test": (
            [android_test_cmd.strip()]
            if android_test_cmd and android_test_cmd.strip()
            else []
        ),
        "after_agent": [],
    }

    # Position acceptance_criteria as the final item in spec.toml
    spec_dict["acceptance_criteria"] = {
        "fail_to_pass": [],
        "pass_to_pass": [],
        "test_files": test_files,
    }

    # Write canonical manifests
    toml_path = task_dir / "task.toml"
    with open(toml_path, "wb") as wf:
        tomli_w.dump(task_dict, wf)

    tests_dir = task_dir / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    spec_path = tests_dir / "spec.toml"
    with open(spec_path, "wb") as wf:
        tomli_w.dump(spec_dict, wf)

    # Inject helpful TODO comment directly above test_files
    spec_text = spec_path.read_text(encoding="utf-8")
    spec_text = spec_text.replace(
        "test_files =",
        "# TODO: Enumerate target verification test files below (can be filled in later)\ntest_files =",
    )
    spec_path.write_text(spec_text, encoding="utf-8")

    logger.info(f"Written canonical manifests: {toml_path} and {spec_path}")

    # 1. environment/
    env_dir = task_dir / "environment"
    env_dir.mkdir(parents=True, exist_ok=True)
    if has_assets:
        (env_dir / "assets").mkdir(parents=True, exist_ok=True)

    compose_path = env_dir / "docker-compose.yaml"
    compose_path.write_text(
        "services:\n"
        "  main:\n"
        "    build:\n"
        "      context: ..\n"
        "      dockerfile: environment/Dockerfile\n"
        "    init: true\n"
        "    devices:\n"
        '      - "/dev/kvm"\n'
        "    volumes:\n"
        "      - ../../../utils/agent:/utils:ro\n"
        "    sysctls:\n"
        "      - net.ipv6.conf.all.disable_ipv6=1\n"
    )

    # Copy verifier into tests/verifier
    verifier_src = v2_dir / "utils" / "verifier"
    verifier_dst = tests_dir / "verifier"
    if verifier_src.is_dir():
        _copy_verifier_files(verifier_src, verifier_dst)

    # 2. solution/
    sol_dir = task_dir / "solution"
    sol_dir.mkdir(parents=True, exist_ok=True)

    solve_sh = sol_dir / "solve.sh"
    solve_sh.write_text(
        "#!/bin/bash\n"
        'if [ -f "/solution/solution.patch" ]; then\n'
        '    echo "Applying solution.patch."\n'
        '    git apply "/solution/solution.patch"\n'
        "else\n"
        '    echo "No solution.patch found or required."\n'
        "fi\n"
    )
    os.chmod(solve_sh, 0o755)

    # 3. tests/
    test_sh = tests_dir / "test.sh"
    test_sh.write_text(
        "#!/bin/bash\n"
        "export PYTHONPATH=/tests/verifier\n"
        "python3 /tests/verifier/evaluator.py\n"
    )
    os.chmod(test_sh, 0o755)

    if want_validate:
        validate_sh = tests_dir / "validate.sh"
        validate_sh.write_text(
            "#!/bin/bash\n"
            "source /tests/verifier/validate_utils.sh\n\n"
            'echo "====================================================="\n'
            f'echo "🚀 Starting Acceptance Validation for {task_id}..."\n'
            'echo "====================================================="\n\n'
            "# Verify evaluation testbed workspace staged\n"
            '[ -d "/app" ]\n'
            'validate "hello_world_sanity_check" \\\n'
            '    --fail "Implementation failed basic validation verification." \\\n'
            '    --pass "Implementation verified successfully."\n\n'
            "validate_finalize\n"
        )
        os.chmod(validate_sh, 0o755)

    # 4. instruction.md (Absolute Minimalist Template)
    inst_path = task_dir / "instruction.md"
    inst_content = (
        f"# {task_id}\n"
        "<!-- Here is where you add instruction for the agents to work on -->\n"
    )
    if has_assets:
        inst_content += "<!-- TODO: Refer to external design assets (UI screenshots, icons) in assets/ -->\n"
    inst_path.write_text(inst_content, encoding="utf-8")

    auto_docker = getattr(args, "auto_docker", True)
    if auto_docker:
        logger.info(
            f"Automatically continuing with evaluation environment staging for {task_id}..."
        )
        generate_task_dockerfile(task_id, task_dir, {**task_dict, **spec_dict})
    else:
        logger.info(
            "Skipped Dockerfile generation (--no-auto-docker). Run `v2.task docker` when ready."
        )

    try:
        rel_dir = task_dir.relative_to(Path.cwd())
    except ValueError:
        rel_dir = task_dir

    actions = [
        f"  1. Fill in [bold red]`{rel_dir}/instruction.md`[/] with instructions for the agents to work on."
    ]
    if want_validate:
        actions.append(
            f"  2. Populate [bold red]`{rel_dir}/tests/validate.sh`[/] with your physical verification checks."
        )

    next_idx = len(actions) + 1
    actions.extend(
        [
            f"  {next_idx}. Edit [cyan]`{rel_dir}/task.toml`[/] to enumerate your [green]`test_files`[/].",
            f"  {next_idx+1}. Run [yellow]`v2.task docker {task_id}`[/] to build your testbed container.",
            f"  {next_idx+2}. Run [magenta]`v2.task refresh-patches {task_id}`[/] to capture golden patches.",
        ]
    )

    actions_str = "\n".join(actions)

    rprint(
        Panel(
            f"[bold green]Successfully Scaffolded Fresh Task Layout[/]: [bold cyan]{task_id}[/]\n\n"
            f"📁 [bold]Created Layout[/]:\n"
            f"  {rel_dir}/\n"
            f"  ├── task.toml            # Task metadata manifest\n"
            f"  ├── instruction.md       # Authoring instructions\n"
            f"  ├── environment/\n"
            + (
                f"  │   ├── assets/          # External design assets\n"
                if has_assets
                else ""
            )
            + f"  │   ├── Dockerfile       # Evaluation environment\n"
            f"  │   └── docker-compose.yaml\n"
            f"  ├── solution/\n"
            f"  │   └── solve.sh         # Solution verification execution\n"
            f"  └── tests/\n"
            f"      ├── test.sh          # Evaluation entrypoint\n"
            f"      └── validate.sh      # Starter Hello-World validation check\n\n"
            f"[bold yellow]Next Actions[/]:\n"
            f"{actions_str}",
            title="[bold green]Dataset V2 Task Scaffolding Complete[/]",
            border_style="green",
        )
    )


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Populates all interactive and non-interactive arguments onto a parser."""
    add_common_task_args(
        parser,
        default_dir=DEFAULT_DATASET_DIR,
        dir_help=f"Target tasks directory for new tasks (default: {DEFAULT_DATASET_DIR}).",
        include_filter=False,
        include_all=False,
        allow_multiple_tasks=False,
    )
    non_int_group = parser.add_argument_group(
        "Non-Interactive Requirements",
        "When running non-interactively (--defaults), you MUST provide --repo-url and either (--before-sha and --after-sha) or --change-id.",
    )
    non_int_group.add_argument(
        "--repo-url",
        type=str,
        help="Target Git repository URL (SSH/HTTP). [Required non-interactive]",
    )
    non_int_group.add_argument(
        "--before-sha",
        type=str,
        help="Starting baseline commit SHA. [Required if no Change ID]",
    )
    non_int_group.add_argument(
        "--after-sha",
        type=str,
        help="Finished golden solution commit SHA. [Required if no Change ID]",
    )
    parser.add_argument(
        "--change-id",
        type=str,
        help="Target Gerrit Change ID (acts as golden solution CL).",
    )
    parser.add_argument(
        "--before-change-id",
        type=str,
        help="Distinct Gerrit Change ID for before_commit (if different).",
    )
    parser.add_argument(
        "--java-version",
        type=int,
        help="Target Java JDK version (e.g., 17).",
    )
    parser.add_argument(
        "--target-sdk",
        type=int,
        help="Target Android SDK version (e.g., 34).",
    )
    parser.add_argument(
        "--time-estimate",
        type=str,
        help="Task completion effort estimate (e.g., 4h, 3d).",
    )
    parser.add_argument(
        "--test-files",
        type=str,
        help="Comma-separated or multiline target verification test files.",
    )
    parser.add_argument(
        "--build-cmd",
        type=str,
        help="Custom build command string (e.g., ./gradlew assembleDebug).",
    )
    parser.add_argument(
        "--unit-test-cmd",
        type=str,
        help="Custom unit test execution string.",
    )
    parser.add_argument(
        "--android-test-cmd",
        type=str,
        help="Custom android test execution string.",
    )
    parser.add_argument(
        "--defaults",
        action="store_true",
        help="Run non-interactively using smart defaults.",
    )
    parser.add_argument(
        "--include-assets",
        action="store_true",
        help="Create environment/assets/ directory for external design assets.",
    )
    parser.add_argument(
        "--no-validate",
        dest="include_validate",
        action="store_false",
        help="Omit starter tests/validate.sh harness.",
    )


def main(args: Optional[argparse.Namespace] = None) -> None:
    if args is None:
        parser = argparse.ArgumentParser(
            description="Interactive Dataset V2 Task Creation Wizard (`v2.task create`)."
        )
        add_arguments(parser)
        args = parser.parse_args()

    if getattr(args, "no_interactive", False):
        args.defaults = True

    try:
        scaffold_task(args)
    except KeyboardInterrupt:
        rprint("\n[bold red]Cancelled creating the task.[/]")
        sys.exit(130)


if __name__ == "__main__":
    main()
