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
Unified Dataset V2 Docker & Repo Curation Utility.
Supports cloning repos (--clone), generating Dockerfiles (--generate),
building images (--build), and automatic task.toml sync.
Fully self-contained single script producing standard V2 Dockerfiles.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import fnmatch
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import time
import pty
import tomllib
from typing import Any, Dict, List, Optional, Set, Tuple, Union
from rich import print as rprint
from rich.console import Group
from rich.live import Live
from rich.panel import Panel
import tomli_w
import yaml

from v2.task_commands.common import (
    DEFAULT_DATASET_DIR,
    DEFAULT_HISTORY_EXCLUSIONS,
    add_common_task_args,
    discover_tasks,
    ensure_commits_exist,
    ensure_standard_git_ref_format,
    get_staged_repo_dir,
    is_private_repo,
    setup_repo,
    reformat_task_toml,
)

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
logger = logging.getLogger("v2_docker")


def get_docker_registry() -> str:
    """Returns the target container registry prefix (default: android-bench)."""
    return os.environ.get("DOCKER_REGISTRY", "android-bench").strip().rstrip("/")


class BuildManager:
    """Manages concurrent rich live output panels for Docker operations."""

    def __init__(self, output_lines: int = 5):
        self._default_output_lines = output_lines
        self.panels: dict[str, Panel] = {}
        self.lock = threading.Lock()
        self.group = Group()

    @property
    def output_lines(self) -> int:
        with self.lock:
            num_active = len(self.panels)
        if num_active == 0:
            return self._default_output_lines
        try:
            term_height = shutil.get_terminal_size().lines
        except Exception:
            term_height = 24
        calculated = ((term_height - 4) // num_active) - 3
        return max(2, min(20, calculated))

    def add_panel(self, title: str, style: str = "bold yellow") -> Panel:
        with self.lock:
            panel = Panel("Initializing...", title=title, style=style)
            self.panels[title] = panel
            self.group = Group(*self.panels.values())
        return panel

    def update_panel(
        self,
        title: str,
        content: str,
        subtitle: Optional[str] = None,
        style: Optional[str] = None,
    ):
        with self.lock:
            if title in self.panels:
                self.panels[title].renderable = content
                if subtitle:
                    self.panels[title].subtitle = subtitle
                if style:
                    self.panels[title].style = style

    def remove_panel(self, title: str):
        with self.lock:
            if title in self.panels:
                del self.panels[title]
                self.group = Group(*self.panels.values())


def get_base_image_name(repo_url: str) -> str:
    clean_url = re.sub(r"[^a-zA-Z0-9-_/]", "/", repo_url.replace("://", "/"))
    parts = [p for p in clean_url.split("/") if p]
    if len(parts) >= 2:
        return f"{parts[-2]}-{parts[-1].replace('.git', '')}".lower()
    elif len(parts) == 1:
        return f"{parts[0].replace('.git', '')}".lower()
    return "default"


def prune_git_history_script(
    commit_sha: str, remove_items: Optional[List[str]] = None
) -> str:
    """Generates shell commands to prune git repository history and remove sensitive paths."""
    base = (
        f"git reset --hard {commit_sha} && \\\n"
        f"    git clean -fd && \\\n"
        f"    git remote remove origin || true && \\\n"
        f"    git branch | grep -v '*' | xargs git branch -D || true && \\\n"
        f"    TARGET_TIMESTAMP=$(git show -s --format=%ct {commit_sha}) && \\\n"
        f"    git tag -l | while read tag; do \\\n"
        f'        TAG_COMMIT=$(git rev-list -n 1 "$tag"); \\\n'
        f'        TAG_TIME=$(git show -s --format=%ct "$TAG_COMMIT"); \\\n'
        f'        if [ "$TAG_TIME" -gt "$TARGET_TIMESTAMP" ]; then \\\n'
        f'            git tag -d "$tag"; \\\n'
        f"        fi; \\\n"
        f"    done && \\\n"
        f"    git reflog expire --expire=now --all && \\\n"
        f"    git gc --prune=now --aggressive"
    )

    if not remove_items:
        return base

    formatted_items = " \\\n".join(f'        "{item}"' for item in remove_items)

    return (
        f"{base} && \\\n"
        f"    git filter-branch \\\n"
        f"        --force \\\n"
        f"        --index-filter 'git rm --cached --ignore-unmatch -r \\\n"
        f"{formatted_items} ' \\\n"
        f"        --prune-empty \\\n"
        f"        --tag-name-filter cat -- --all && \\\n"
        f"    git reflog expire --expire=now --all && \\\n"
        f"    git gc --prune=now --aggressive"
    )


def is_file_skipped(path: Path) -> bool:
    if path.is_file():
        try:
            first_line = path.read_text().splitlines()[0]
            return "SKIP_GENERATE" in first_line or (
                "PRESERVE_DOCKERFILE" in first_line and path.name == "Dockerfile"
            )
        except Exception:
            pass
    return False


def is_dockerfile_skipped(df_path: Path) -> bool:
    return is_file_skipped(df_path)


def get_docker_image_tag(task_id: str, task_data: Dict[str, Any]) -> str:
    registry = get_docker_registry()
    return (
        task_data.get("environment", {}).get("docker_image")
        or f"{registry}/{task_id}:latest"
    )


def docker_image_exists(image_tag: str) -> bool:
    res = subprocess.run(
        ["docker", "image", "inspect", image_tag],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return res.returncode == 0


def tag_docker_image(source_tag: str, target_tag: str) -> None:
    subprocess.run(["docker", "tag", source_tag, target_tag], check=True)


def build_task_containers(
    task_ids: List[str],
    dataset_dir: Path = DEFAULT_DATASET_DIR,
    max_workers: int = 1,
) -> None:
    if not task_ids:
        return
    build_args = argparse.Namespace(
        dataset_dir=dataset_dir,
        task_id=task_ids,
        tasks_filter=None,
        clone=False,
        generate=False,
        build=True,
        max_workers=max_workers,
    )
    main_with_args(build_args)


def derive_base_image_name(repo_url: str) -> str:
    if not repo_url:
        return "android-bench-env"
    url_to_split = repo_url
    if "@" in url_to_split and ":" in url_to_split and "://" not in url_to_split:
        url_to_split = url_to_split.replace(":", "/")
    repo_parts = url_to_split.split("/")
    if len(repo_parts) < 2:
        return "android-bench-env"
    owner = repo_parts[-2]
    repo = repo_parts[-1].replace(".git", "")
    return f"{owner}-{repo}-base".lower()


def get_original_base_image(task_dir: Path, default_tag: str) -> str:
    df_path = (task_dir / "environment" / "Dockerfile").resolve()
    try:
        repo_root = Path(
            subprocess.check_output(
                ["git", "rev-parse", "--show-toplevel"], text=True
            ).strip()
        )
        rel_df_path = df_path.relative_to(repo_root).as_posix()
        original_content = subprocess.check_output(
            ["git", "show", f"HEAD:{rel_df_path}"],
            stderr=subprocess.DEVNULL,
            text=True,
        )
        for line in original_content.splitlines():
            if line.startswith("FROM "):
                return line.split(" ", 1)[1].strip()
    except Exception:
        pass

    if df_path.is_file():
        try:
            for line in df_path.read_text().splitlines():
                if line.startswith("FROM "):
                    return line.split(" ", 1)[1].strip()
        except Exception:
            pass

    return default_tag


def generate_task_dockerfile(
    task_id: str, task_dir: Path, task_data: Dict[str, Any]
) -> Path:
    """Generates the Dockerfile directly to environment/Dockerfile."""
    env_dir = task_dir / "environment"
    env_dir.mkdir(parents=True, exist_ok=True)
    df_path = env_dir / "Dockerfile"
    compose_path = env_dir / "docker-compose.yaml"

    is_variant_task = False
    base_task_name = task_dir.name
    if "-" in task_dir.name:
        parts = task_dir.name.split("-", 1)
        if (task_dir.parent / parts[1]).is_dir():
            is_variant_task = True
            base_task_name = parts[1]
    elif "-" in task_id:
        parts = task_id.split("-", 1)
        if (task_dir.parent / parts[1]).is_dir():
            is_variant_task = True
            base_task_name = parts[1]

    base_task_dir = task_dir.parent / base_task_name

    if is_file_skipped(df_path):
        rprint(
            f"[yellow][WARNING][/yellow] \\[{task_id}] Dockerfile marked with # SKIP_GENERATE. Skipping re-generation."
        )
    elif is_variant_task and base_task_dir.is_dir():
        b_data = {}
        base_toml = base_task_dir / "task.toml"
        if base_toml.is_file():
            try:
                with open(base_toml, "rb") as bf:
                    b_data = tomllib.load(bf)
            except Exception:
                pass
        base_img_tag = get_docker_image_tag(base_task_name, b_data)

        content = f"FROM {base_img_tag}\n"
        df_path.write_text(content)
        logger.info(f"Generated variant Dockerfile: {df_path}")
    else:
        before_commit = task_data.get("before_commit", {})
        commit_sha = before_commit.get("sha") or "HEAD"
        java_ver = before_commit.get("java_version", 17)
        remove_items = task_data.get("repository", {}).get(
            "remove_from_git_history"
        ) or task_data.get("remove_from_git_history")

        cmds = task_data.get("commands", {})
        before_build = (
            cmds.get("before_build")
            or task_data.get("environment", {}).get("before_build")
            or []
        )
        if "build" in cmds and cmds["build"] is not None:
            build_cmds = cmds["build"]
        else:
            build_cmds = []

        joined_build = " && \\\n    ".join(
            [c for c in before_build + build_cmds if c and c.strip()]
        )

        repo_info = task_data.get("repository", {})
        repo_url = repo_info.get("url") or task_data.get("metadata", {}).get(
            "repository"
        )
        if repo_url and not (
            repo_url.startswith("http")
            or repo_url.startswith("git@")
            or repo_url.startswith("ssh://")
        ):
            repo_url = f"https://github.com/{repo_url}"

        default_tag = f"{get_docker_registry()}/android-bench-env:latest"
        original_from = get_original_base_image(task_dir, default_tag)

        # Resolve self-referential task image tags to their corresponding base image tags
        image_name_part = original_from.split(":")[0].rsplit("/", 1)[-1]
        if task_id == image_name_part or original_from.endswith(f"/{task_id}:latest"):
            original_from = derive_base_image_name(repo_url)

        if original_from == "android-bench-env":
            base_img_tag = default_tag
        elif "/" not in original_from:
            tag = ":latest" if ":" not in original_from else ""
            base_img_tag = f"{get_docker_registry()}/{original_from}{tag}"
        else:
            base_img_tag = original_from

        is_custom_base = (
            base_img_tag != default_tag and "android-bench-env" not in base_img_tag
        )
        env_cfg = task_data.get("environment", {})
        setup_cmds = cmds.get("docker_setup") or env_cfg.get("setup_commands") or []

        content = (
            f"FROM {base_img_tag}\n"
            f"ENV JAVA_HOME=/usr/lib/jvm/java-{java_ver}-openjdk-amd64\n"
        )
        staged_repo = get_staged_repo_dir(task_dir)
        staged_rel = os.path.relpath(staged_repo, task_dir)

        if is_custom_base:
            content += "WORKDIR /workspace/testbed\n"
        elif is_private_repo(repo_url, task_data):
            content += (
                f"COPY {staged_rel} /workspace/testbed\n"
                "WORKDIR /workspace/testbed\n"
            )
        else:
            content += (
                f"RUN git clone {repo_url} /workspace/testbed\n"
                "WORKDIR /workspace/testbed\n"
            )

        content += f'ENV GRADLE_OPTS="-Xmx6g"\n' f"RUN pip install tomli\n"
        for cmd in setup_cmds:
            if cmd and cmd.strip():
                content += f"RUN {cmd.strip()}\n"

        prune_script = prune_git_history_script(commit_sha, remove_items)
        if prune_script:
            content += f"RUN {prune_script}\n"

        assets_dir = env_dir / "assets"
        if assets_dir.is_dir():
            content += (
                "COPY environment/assets/ /workspace/testbed/assets/\n"
                "RUN echo '/assets/' >> .git/info/exclude\n"
            )

        # Global ignores added to avoid committing build artifacts
        content += "RUN echo 'build/' >> .git/info/exclude\n"

        if joined_build:
            content += f"RUN {joined_build}\n"

        df_path.write_text(content)
        logger.info(f"Generated Dockerfile: {df_path}")

    if is_file_skipped(compose_path):
        rprint(
            f"[yellow][WARNING][/yellow] \\[{task_id}] docker-compose.yaml marked with # SKIP_GENERATE. Skipping re-generation."
        )
    else:
        volumes_str = "      - ../../../utils/agent:/utils:ro\n"
        entrypoint_str = ""

        if is_variant_task:
            if (task_dir / "tests" / "test.patch").is_file():
                volumes_str += "      - ../tests/test.patch:/tmp/open_test.patch:ro\n"
                entrypoint_str = (
                    '    entrypoint: ["bash", "/utils/open-tests-entrypoint.sh"]\n'
                )
            if (task_dir / "tests" / "open").is_dir():
                volumes_str += "      - ../tests/open/:/workspace/validate/:ro\n"

        compose_content = (
            "services:\n"
            "  main:\n"
            "    build:\n"
            "      context: ..\n"
            "      dockerfile: environment/Dockerfile\n"
            "    init: true\n"
            "    devices:\n"
            '      - "/dev/kvm"\n'
            "    environment:\n"
            "      - EMULATOR_NAME\n"
            "    volumes:\n"
            f"{volumes_str}"
            f"{entrypoint_str}"
            "    sysctls:\n"
            "      - net.ipv6.conf.all.disable_ipv6=1\n"
        )
        compose_path.write_text(compose_content)

    return df_path


def verify_container_runtime_initialization(
    target_tag: str, compose_path: Path, lines: List[str], manager: BuildManager
) -> None:
    """Runs docker compose up/down to verify that container runtime initialization and volume mounts succeed without runtime errors."""
    if not compose_path.is_file():
        return

    project_name = target_tag.replace(":", "_").replace("/", "_").replace(".", "_")
    up_cmd = [
        "docker",
        "compose",
        "-p",
        project_name,
        "-f",
        str(compose_path),
        "up",
        "-d",
    ]
    lines.append("--- Verifying docker-compose up ---\n")
    manager.update_panel(target_tag, "".join(lines[-manager.output_lines :]))
    try:
        up_proc = subprocess.run(
            up_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
        )
        if up_proc.returncode != 0:
            if up_proc.stdout:
                lines.append(f"{up_proc.stdout}\n")
            raise subprocess.CalledProcessError(
                up_proc.returncode, up_cmd, output="".join(lines)
            )
    finally:
        down_cmd = [
            "docker",
            "compose",
            "-p",
            project_name,
            "-f",
            str(compose_path),
            "down",
            "--rmi",
            "local",
            "-v",
            "--remove-orphans",
            "-t",
            "0",
        ]
        lines.append("--- Verifying docker-compose down ---\n")
        manager.update_panel(target_tag, "".join(lines[-manager.output_lines :]))
        down_proc = subprocess.run(
            down_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
        )
        if down_proc.returncode != 0:
            if down_proc.stdout:
                lines.append(f"{down_proc.stdout}\n")
            raise subprocess.CalledProcessError(
                down_proc.returncode, down_cmd, output="".join(lines)
            )


def build_image(
    target_tag: str,
    df_path: Path,
    ctx_dir: Path,
    lines: List[str],
    manager: BuildManager,
) -> None:
    cmd = ["docker", "build", "-t", target_tag, "-f", str(df_path), str(ctx_dir)]
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    if proc.stdout:
        for l in proc.stdout:
            lines.append(l)
            manager.update_panel(target_tag, "".join(lines[-manager.output_lines :]))
    proc.wait()
    if proc.returncode != 0:
        raise subprocess.CalledProcessError(proc.returncode, cmd, output="".join(lines))

    verify_container_runtime_initialization(
        target_tag, df_path.parent / "docker-compose.yaml", lines, manager
    )


def open_pty_master(fd: int):
    return open(fd, "r", encoding="utf-8", errors="ignore")


def build_worker(
    target_tag: str,
    df_path: Path,
    ctx_dir: Path,
    do_build: bool,
    total: int,
    counter: List[int],
    failed_builds: List[str],
    manager: BuildManager,
    lock: threading.Lock,
) -> None:
    manager.add_panel(target_tag)
    lines = []

    try:
        if do_build:
            build_image(target_tag, df_path, ctx_dir, lines, manager)

        with lock:
            counter[0] += 1
            manager.remove_panel(target_tag)
            rprint(
                f"[bold green][✓] [{counter[0]}/{total}] Successfully built: {target_tag}[/bold green]"
            )
    except Exception as e:
        with lock:
            counter[0] += 1
            failed_builds.append(target_tag)
            err_msg = (
                str(e.output)
                if isinstance(e, subprocess.CalledProcessError)
                else str(e)
            )
            manager.update_panel(
                target_tag,
                f"[bold red]❌ Failed building: {target_tag}[/bold red]\n{err_msg}",
            )
            rprint(
                f"[bold red][✗] [{counter[0]}/{total}] Failed building: {target_tag}[/bold red]"
            )


def main_with_args(args: argparse.Namespace) -> None:
    tasks = discover_tasks(args.dataset_dir, args.task_id, args.tasks_filter)
    logger.info(f"Discovered {len(tasks)} matching task(s).")
    if not tasks:
        return

    # 1. Clone & Verify Commits
    valid_tasks = []
    for t_id, t_path, t_data in tasks:
        is_variant = False
        if "-" in t_path.name:
            parts = t_path.name.split("-", 1)
            if (t_path.parent / parts[1]).is_dir():
                is_variant = True
        elif "-" in t_id:
            parts = t_id.split("-", 1)
            if (t_path.parent / parts[1]).is_dir():
                is_variant = True

        if is_variant:
            if args.clone:
                logger.info(
                    f"[{t_id}] Skipping git clone (inherits base benchmark container)."
                )
            valid_tasks.append((t_id, t_path, t_data))
            continue

        repo_url = t_data.get("repository", {}).get("url") or t_data.get(
            "metadata", {}
        ).get("repository", "")

        staged_repo_path = Path(get_staged_repo_dir(t_path))

        if args.clone:
            before_sha = t_data.get("before_commit", {}).get("sha")
            if before_sha:
                ok = ensure_commits_exist(
                    staged_repo_path,
                    t_path,
                    before_sha,
                )
            else:
                ok = bool(setup_repo(str(t_path)))

            if not ok and is_private_repo(repo_url, t_data):
                rprint(
                    f"[bold red][ERROR][/bold red] \\[{t_id}] Required private git repository ({repo_url}) could not be cloned. Skipping task."
                )
                continue

        if (
            is_private_repo(repo_url, t_data)
            and not staged_repo_path.is_dir()
        ):
            rprint(
                f"[bold red][ERROR][/bold red] \\[{t_id}] Required git repository missing at {staged_repo_path}. Skipping task."
            )
            continue

        if staged_repo_path.is_dir():
            ensure_standard_git_ref_format(staged_repo_path)

        valid_tasks.append((t_id, t_path, t_data))

    # 2. Generate
    if args.generate:
        for t_id, t_path, t_data in valid_tasks:
            generate_task_dockerfile(t_id, t_path, t_data)

    # 3. Build (Using environment/ directory as context for COPY staged-repo)
    if args.build:
        try:
            if not docker_image_exists("android-bench-env:latest"):
                logger.info(
                    "Ensuring base image 'android-bench-env:latest' tag exists..."
                )
                tag_docker_image(
                    get_docker_image_tag("android-bench-env", {}),
                    "android-bench-env:latest",
                )
        except Exception:
            pass

    build_targets = []
    for t_id, t_path, t_data in valid_tasks:
        tag = get_docker_image_tag(t_id, t_data)
        df = t_path / "environment" / "Dockerfile"
        if df.is_file():
            build_targets.append((tag, df, t_path))

    # 3. Build
    if args.build and build_targets:
        manager = BuildManager()
        counter = [0]
        failed_builds: List[str] = []
        lock = threading.Lock()
        total = len(build_targets)

        with Live(
            manager.group, refresh_per_second=10, vertical_overflow="visible"
        ) as live:
            with ThreadPoolExecutor(max_workers=args.max_workers) as ex:
                fs = [
                    ex.submit(
                        build_worker,
                        tag,
                        df,
                        t_path,
                        args.build,
                        total,
                        counter,
                        failed_builds,
                        manager,
                        lock,
                    )
                    for tag, df, t_path in build_targets
                ]

                def ref() -> None:
                    while any(not f.done() for f in fs):
                        live.update(manager.group)
                        time.sleep(0.1)

                rt = threading.Thread(target=ref)
                rt.start()
                for f in as_completed(fs):
                    f.result()
                rt.join()

        if failed_builds:
            logger.error(f"Build failed for: {failed_builds}")
            sys.exit(1)

    # 5. Summary of skipped Dockerfiles
    skipped_summary = [
        t_id
        for t_id, t_path, _ in valid_tasks
        if is_dockerfile_skipped(t_path / "environment" / "Dockerfile")
    ]
    if skipped_summary:
        rprint(
            f"[yellow][WARNING][/yellow] Summary: Skipped Dockerfile re-generation for {len(skipped_summary)} task(s) marked with # SKIP_GENERATE: "
            + ", ".join(f"\\[{st}]" for st in skipped_summary)
        )

    for _, t_path, _ in valid_tasks:
        reformat_task_toml(t_path)


def add_arguments(parser: argparse.ArgumentParser) -> None:
    add_common_task_args(parser)
    parser.add_argument(
        "--clone",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Clone repo into environment/ staging (default: --clone).",
    )
    parser.add_argument(
        "--generate",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Generate environment/Dockerfile (default: --generate).",
    )
    parser.add_argument(
        "--build",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Build Docker images (default: --build).",
    )
    parser.add_argument(
        "--max-workers", type=int, default=4, help="Parallel build threads."
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Unified V2 Curation Utility.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    add_arguments(parser)
    args = parser.parse_args()
    if not (args.task_id or args.all or args.tasks_filter):
        parser.error("Specify positional task_id, --all, or --tasks-filter")

    main_with_args(args)


if __name__ == "__main__":
    main()
