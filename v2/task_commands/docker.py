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
    leading_comment_block,
    setup_repo,
    reformat_task_toml,
)

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
logger = logging.getLogger("v2_docker")


# Image prefix for tasks built from this repository. Override with DOCKER_REGISTRY.
DEFAULT_DOCKER_REGISTRY = "community-dataset"
# Where the prebuilt private base images live, independent of where task images are tagged.
ANDROID_BENCH_REGISTRY = "android-bench"


def get_docker_registry() -> str:
    """Returns the target container registry prefix (default: community-dataset)."""
    return (
        os.environ.get("DOCKER_REGISTRY", DEFAULT_DOCKER_REGISTRY).strip().rstrip("/")
    )


# Public base image plus the toolchain the generated Dockerfile installs on top of it.
# android-bench-env lives in a private registry, so it cannot be the default here.
PUBLIC_BASE_IMAGE = "ubuntu:22.04"
ANDROID_CMDLINE_TOOLS_VERSION = "11076708"
DEFAULT_TARGET_SDK = 35
DEFAULT_MEMORY_MB = 8192

# aapt2 ships x86_64-only for Linux, the emulator system images are x86_64, and the JAVA_HOME
# written below resolves to the -amd64 JDK path. Without this pin an arm64 host (Apple Silicon)
# produces java-<ver>-openjdk-arm64 and sdkmanager exits 1.
DEFAULT_BUILD_PLATFORM = "linux/amd64"


def get_default_base_image() -> str:
    """Base image for generated Dockerfiles.

    Defaults to a public Ubuntu image, on top of which the generated Dockerfile installs the
    JDK and Android SDK itself. Set ANDROID_BENCH_BASE_IMAGE=android-bench-env to build against
    the prebuilt android-bench base instead (requires access to the private registry).
    """
    return os.environ.get("ANDROID_BENCH_BASE_IMAGE", PUBLIC_BASE_IMAGE).strip()


def get_build_platform() -> str:
    """Platform pinned on FROM lines. Set ANDROID_BENCH_PLATFORM='' to omit the pin."""
    return os.environ.get("ANDROID_BENCH_PLATFORM", DEFAULT_BUILD_PLATFORM).strip()


def from_line(image_tag: str) -> str:
    platform = get_build_platform()
    if platform:
        return f"FROM --platform={platform} {image_tag}\n"
    return f"FROM {image_tag}\n"


def parse_memory_to_mb(value: Any) -> Optional[int]:
    """Parses a memory declaration into MB. Accepts "12G", "512M", "8Gi" or a bare MB number."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    match = re.match(
        r"^(\d+(?:\.\d+)?)\s*([kmgt]?)i?b?$", str(value).strip().lower()
    )
    if not match:
        return None
    multipliers = {"": 1.0, "k": 1 / 1024, "m": 1.0, "g": 1024.0, "t": 1024.0 * 1024}
    return int(float(match.group(1)) * multipliers[match.group(2)])


def get_declared_memory_mb(task_data: Dict[str, Any]) -> int:
    """Container memory in MB.

    `v2.task create` and task-template.toml write `memory = "72G"`; older tasks carry
    `memory_mb = 8192`. Both spellings have to resolve, or the JVM sizing below silently
    falls back to the default and the container is misconfigured.
    """
    env_cfg = task_data.get("environment", {}) or {}
    for key in ("memory", "memory_mb"):
        parsed = parse_memory_to_mb(env_cfg.get(key))
        if parsed:
            return parsed
    return DEFAULT_MEMORY_MB


def gradle_opts_line(task_data: Dict[str, Any]) -> str:
    """GRADLE_OPTS heap for the Gradle client JVM.

    GRADLE_OPTS sizes the launcher, not the daemon that does the work (see
    gradle_memory_properties), and the launcher only parses arguments and relays logs. The old
    fixed -Xmx6g gave a near-idle process a ceiling that, added to the daemon and Kotlin daemon
    ceilings, oversubscribed the container.
    """
    return f'ENV GRADLE_OPTS="-Xmx{max(1024, get_declared_memory_mb(task_data) // 8)}m"\n'


def gradle_memory_properties(task_data: Dict[str, Any]) -> str:
    """Caps the Gradle and Kotlin daemon heaps to fit the memory the task declares.

    A project's own gradle.properties is written for a developer laptop and routinely asks for
    more than the container has (this dataset's typical 8 GB). org.gradle.jvmargs there beats
    GRADLE_OPTS, so the daemon starts oversized, the Kotlin daemon starts beside it, and the
    kernel kills one of them mid-build. GRADLE_USER_HOME/gradle.properties takes precedence over
    the project file and sits outside the testbed, so it is invisible to the agent.
    """
    memory_mb = get_declared_memory_mb(task_data)
    daemon_mb = max(1536, int(memory_mb * 0.40))
    kotlin_mb = max(1024, int(memory_mb * 0.20))
    return (
        "RUN mkdir -p /root/.gradle && \\\n"
        f"    echo 'org.gradle.jvmargs=-Xmx{daemon_mb}m -XX:MaxMetaspaceSize=1024m -Dfile.encoding=UTF-8' > /root/.gradle/gradle.properties && \\\n"
        f"    echo 'kotlin.daemon.jvmargs=-Xmx{kotlin_mb}m' >> /root/.gradle/gradle.properties\n"
    )


def gradle_wrapper_prefetch(build_commands: List[str]) -> str:
    """Downloads the Gradle distribution in its own layer, with retries.

    The wrapper aborts on a stalled read (networkTimeout, 10s by default) and does not resume,
    so a slow link kills the whole build layer after minutes of work. Fetching it separately
    keeps the retry cheap and the distribution cached.
    """
    if not any("./gradlew" in (c or "") for c in build_commands):
        return ""
    return (
        "RUN for attempt in 1 2 3 4 5; do \\\n"
        "        ./gradlew --version && exit 0; \\\n"
        '        echo "Gradle distribution download failed (attempt $attempt), retrying"; \\\n'
        "        sleep 15; \\\n"
        "    done; exit 1\n"
    )


def android_toolchain_script(java_ver: Any, target_sdk: Any) -> str:
    """Dockerfile lines installing the JDK and Android SDK on a bare public base image."""
    sdk = str(target_sdk or DEFAULT_TARGET_SDK)
    return (
        "ENV DEBIAN_FRONTEND=noninteractive\n"
        "RUN apt-get update && \\\n"
        "    apt-get install -y --no-install-recommends \\\n"
        "    python3 python3-pip git wget unzip curl ca-certificates libglu1-mesa \\\n"
        f"    openjdk-{java_ver}-jdk \\\n"
        "    && apt-get clean && rm -rf /var/lib/apt/lists/*\n"
        "ENV ANDROID_HOME=/opt/android-sdk\n"
        "ENV PATH=${PATH}:${JAVA_HOME}/bin:${ANDROID_HOME}/cmdline-tools/latest/bin:${ANDROID_HOME}/platform-tools\n"
        "RUN mkdir -p ${ANDROID_HOME}/cmdline-tools && \\\n"
        f'    wget -q "https://dl.google.com/android/repository/commandlinetools-linux-{ANDROID_CMDLINE_TOOLS_VERSION}_latest.zip" -O /tmp/android-sdk.zip && \\\n'
        "    unzip -q /tmp/android-sdk.zip -d ${ANDROID_HOME}/cmdline-tools && \\\n"
        "    mv ${ANDROID_HOME}/cmdline-tools/cmdline-tools ${ANDROID_HOME}/cmdline-tools/latest && \\\n"
        "    rm /tmp/android-sdk.zip\n"
        "RUN yes | sdkmanager --licenses > /dev/null && \\\n"
        f'    sdkmanager "platform-tools" "platforms;android-{sdk}" "build-tools;{sdk}.0.0"\n'
    )


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
    commit_sha: str,
    remove_items: Optional[List[str]] = None,
    strip_extra_refs: bool = False,
) -> str:
    """Generates shell commands to prune git repository history and remove sensitive paths.

    strip_extra_refs deletes refs outside refs/heads and refs/tags. A staged clone copied into
    the image can carry refs the author fetched locally (refs/bench/after and the like), and
    `git branch -D` does not touch those, so the after commit would stay reachable.
    """
    extra_refs = (
        "    git for-each-ref --format='%(refname)' \\\n"
        "        | grep -v -e '^refs/heads/' -e '^refs/tags/' \\\n"
        "        | xargs -r -n 1 git update-ref -d || true && \\\n"
        if strip_extra_refs
        else ""
    )
    base = (
        f"git reset --hard {commit_sha} && \\\n"
        f"    git clean -fd && \\\n"
        f"    git remote remove origin || true && \\\n"
        f"{extra_refs}"
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
    """True when a hand-written file opts out of regeneration.

    Scans the first few lines, not only the first: canary_check.py prepends its two-line block
    at the top of the file, which pushes a first-line marker down.
    """
    if path.is_file():
        try:
            head = path.read_text().splitlines()[:5]
            return any(
                "SKIP_GENERATE" in line
                or ("PRESERVE_DOCKERFILE" in line and path.name == "Dockerfile")
                for line in head
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


def parse_from_line(line: str) -> Optional[str]:
    """Extracts the image from a FROM line, dropping flags (--platform=...) and an AS alias."""
    tokens = line.split()[1:]
    for i, token in enumerate(tokens):
        if token.startswith("--"):
            continue
        if token.upper() == "AS":
            break
        return token
    return None


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
                image = parse_from_line(line)
                if image:
                    return image
    except Exception:
        pass

    if df_path.is_file():
        try:
            for line in df_path.read_text().splitlines():
                if line.startswith("FROM "):
                    image = parse_from_line(line)
                    if image:
                        return image
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

    # Regeneration overwrites the file; the canary block at the top has to survive it.
    existing_header = (
        leading_comment_block(df_path.read_text()) if df_path.is_file() else ""
    )

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

        content = existing_header + from_line(base_img_tag)
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

        default_tag = get_default_base_image()
        original_from = get_original_base_image(task_dir, default_tag)

        # Resolve self-referential task image tags to their corresponding base image tags
        image_name_part = original_from.split(":")[0].rsplit("/", 1)[-1]
        if task_id == image_name_part or original_from.endswith(f"/{task_id}:latest"):
            original_from = derive_base_image_name(repo_url)

        if original_from == "android-bench-env":
            # Pinned to its own registry: this image is published by android-bench, wherever
            # the task images built from it happen to be tagged.
            base_img_tag = f"{ANDROID_BENCH_REGISTRY}/android-bench-env:latest"
        elif "/" not in original_from and ":" not in original_from:
            # A bare, untagged name is one of our own locally built base images
            # (android-bench-env, <owner>-<repo>-base). A public image such as
            # "ubuntu:22.04" carries a tag and must never be registry-prefixed.
            base_img_tag = f"{get_docker_registry()}/{original_from}:latest"
        else:
            base_img_tag = original_from

        base_name = base_img_tag.split(":")[0].rsplit("/", 1)[-1]
        # Prebuilt <owner>-<repo>-base images already contain the checked out repository;
        # every other base, ours or public, still needs the repo staged into it.
        repo_baked_into_base = base_name.endswith("-base")
        # Bases that already ship the JDK and Android SDK. Anything else gets them installed here.
        toolchain_in_base = repo_baked_into_base or base_name == "android-bench-env"

        env_cfg = task_data.get("environment", {})
        setup_cmds = cmds.get("docker_setup") or env_cfg.get("setup_commands") or []

        content = existing_header + from_line(base_img_tag)
        content += f"ENV JAVA_HOME=/usr/lib/jvm/java-{java_ver}-openjdk-amd64\n"
        if not toolchain_in_base:
            content += android_toolchain_script(
                java_ver, before_commit.get("target_sdk", DEFAULT_TARGET_SDK)
            )

        staged_repo = get_staged_repo_dir(task_dir)
        staged_rel = os.path.relpath(staged_repo, task_dir)

        if repo_baked_into_base:
            content += "WORKDIR /workspace/testbed\n"
        elif is_private_repo(repo_url, task_data):
            content += (
                f"COPY {staged_rel} /workspace/testbed\n"
                "WORKDIR /workspace/testbed\n"
            )
        else:
            # The commit is named in the clone layer on purpose: Docker caches `git clone` on
            # its text alone, so after the before commit moves upstream a cached clone would
            # silently lack it and the checkout below would fail on a stale layer.
            content += (
                f"RUN git clone {repo_url} /workspace/testbed && \\\n"
                f"    git -C /workspace/testbed cat-file -e {commit_sha}^{{commit}}\n"
                "WORKDIR /workspace/testbed\n"
            )

        content += gradle_opts_line(task_data)
        content += gradle_memory_properties(task_data)
        content += "RUN pip install tomli\n"
        for cmd in setup_cmds:
            if cmd and cmd.strip():
                content += f"RUN {cmd.strip()}\n"

        prune_script = prune_git_history_script(
            commit_sha,
            remove_items,
            strip_extra_refs=is_private_repo(repo_url, task_data),
        )
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
            content += gradle_wrapper_prefetch(before_build + build_cmds)
            content += f"RUN {joined_build}\n"

        df_path.write_text(content)
        logger.info(f"Generated Dockerfile: {df_path}")

    if is_file_skipped(compose_path):
        rprint(
            f"[yellow][WARNING][/yellow] \\[{task_id}] docker-compose.yaml marked with # SKIP_GENERATE. Skipping re-generation."
        )
    else:
        volumes_str = "      - ../task.toml:/task.toml\n"
        entrypoint_str = ""

        # utils/agent is android-bench harness infrastructure and is absent from the community
        # dataset. Mounting a path that does not exist makes `docker compose up` fail outright.
        utils_dir = task_dir.parent.parent / "utils" / "agent"
        has_utils = utils_dir.is_dir()
        if has_utils:
            volumes_str += "      - ../../../utils/agent:/utils:ro\n"

        if is_variant_task:
            if (task_dir / "tests" / "test.patch").is_file():
                volumes_str += "      - ../tests/test.patch:/tmp/open_test.patch:ro\n"
                if has_utils:
                    entrypoint_str = (
                        '    entrypoint: ["bash", "/utils/open-tests-entrypoint.sh"]\n'
                    )
                else:
                    rprint(
                        f"[yellow][WARNING][/yellow] \\[{task_id}] utils/agent not found; open-tests entrypoint omitted."
                    )
            if (task_dir / "tests" / "open").is_dir():
                volumes_str += "      - ../tests/open/:/workspace/validate/:ro\n"

        # /dev/kvm is only needed by tasks that boot an emulator, and requesting a device the
        # host lacks makes `docker compose up` fail outright -- fatal under Harbor, which runs
        # `up --wait` itself. Emit it only when the task declares instrumented tests.
        task_cmds = task_data.get("commands", {}) or {}
        needs_emulator = bool(task_cmds.get("android_test")) or bool(
            task_data.get("environment", {}).get("requires_kvm")
        )
        emulator_block = (
            '    devices:\n      - "/dev/kvm"\n    environment:\n      - EMULATOR_NAME\n'
            if needs_emulator
            else ""
        )

        compose_content = (
            "services:\n"
            "  main:\n"
            "    build:\n"
            "      context: ..\n"
            "      dockerfile: environment/Dockerfile\n"
            "    init: true\n"
            f"{emulator_block}"
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
            # /dev/kvm is absent on macOS and on any host without nested virtualization. The
            # compose file is written for the eval runners, which do have it, so this is a
            # property of the machine running the build, not a fault in the task.
            if "/dev/kvm" in (up_proc.stdout or "") and "no such file" in (
                up_proc.stdout or ""
            ):
                rprint(
                    f"[yellow][WARNING][/yellow] \\[{target_tag}] Host has no /dev/kvm; skipped compose runtime check. The image itself built successfully."
                )
            else:
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
    # Only relevant when tasks actually build on the private android-bench base image.
    if args.build and "android-bench-env" in get_default_base_image():
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
