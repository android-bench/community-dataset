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
Shared Common Utilities for Dataset V2 Task Management (`v2.task_commands.common`).
Houses centralized task discovery (`discover_tasks`) and repository staging (`setup_repo`).
Renders a cohesive Git Staging output under a single panel with clean sub-bullets
(silencing raw Git stdout/stderr and avoiding nested box spam).
"""

import argparse
import fnmatch
import logging
import os
from pathlib import Path
import shutil
import subprocess
import tomllib
import tomli_w
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union
import yaml

from rich.console import Console
from rich.panel import Panel

logger = logging.getLogger("v2.common")
console = Console()

# Environment-configurable private repository prefixes (if any)
_ENV_PREFIXES = os.environ.get("PRIVATE_REPO_PREFIXES", "")
PRIVATE_REPO_PREFIXES = [p.strip() for p in _ENV_PREFIXES.split(",") if p.strip()]


def print_info_panel(msg: str, title: str = "INFO") -> None:
    console.print(
        Panel(f"[cyan]{msg}[/]", title=f"[bold cyan]{title}[/]", border_style="cyan")
    )


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


def print_step_msg(msg: str) -> None:
    console.print(f"  [dim]• {msg}[/]")


def print_success_msg(msg: str) -> None:
    console.print(f"  [green]✓ {msg}[/]")


def is_private_repo(repo_url: str, task_data: Optional[Dict[str, Any]] = None) -> bool:
    """Checks if a repository is private based on its URL or task configuration."""
    if task_data:
        repo_cfg = task_data.get("repository", {})
        if repo_cfg.get("is_private") or repo_cfg.get("private"):
            return True
        if task_data.get("is_private") or task_data.get("private"):
            return True
    if not repo_url:
        return False
    return (
        any(prefix in repo_url for prefix in PRIVATE_REPO_PREFIXES)
        or "@" in repo_url
        or "private" in repo_url.lower()
        or repo_url.startswith("git@")
        or repo_url.startswith("ssh://")
    )


def handle_gerrit_ref_checkout(
    task_dir: str,
    repo_url: str,
    change_id: Union[int, str],
    target_path: str,
    silent: bool = False,
) -> Optional[str]:
    """Discovers and fetches the latest patchset ref for a Gerrit change_id."""
    change_id_str = str(change_id)
    if not silent:
        print_step_msg(f"Handling Gerrit change_id: {change_id_str}...")

    ls_remote_cmd = [
        "git",
        "ls-remote",
        "--sort=v:refname",
        repo_url,
        f"refs/changes/*/{change_id_str}/*",
    ]

    try:
        ls_remote_output = subprocess.check_output(
            ls_remote_cmd, text=True, stderr=subprocess.DEVNULL
        ).strip()
        if ls_remote_output:
            refs = [
                line.split("\t")[1]
                for line in ls_remote_output.splitlines()
                if "meta" not in line
            ]
            if refs:
                latest_ref = refs[-1]
                if not silent:
                    print_step_msg(f"Found latest ref: {latest_ref}. Fetching...")
                subprocess.run(
                    ["git", "fetch", "origin", latest_ref],
                    cwd=target_path,
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                return subprocess.check_output(
                    ["git", "rev-parse", "FETCH_HEAD"],
                    cwd=target_path,
                    text=True,
                    stderr=subprocess.DEVNULL,
                ).strip()

        if not silent:
            print_step_msg(
                f"No valid refs found for change_id {change_id_str}, falling back to SHA."
            )
    except subprocess.CalledProcessError:
        if not silent:
            print_step_msg(
                f"Could not find refs for change_id {change_id_str} via ls-remote, falling back to SHA."
            )

    return None


def get_staged_repo_dir(task_dir: Union[str, Path]) -> str:
    """Returns path to the staged repository directory."""
    staged_path = os.path.join(str(task_dir), "environment", "staged-repo")
    alt_path = os.path.join(str(task_dir), "environment", "staged-git-repo")
    if not os.path.isdir(staged_path) and os.path.isdir(alt_path):
        return alt_path
    return staged_path


def ensure_standard_git_ref_format(repo_path: Union[str, Path]) -> bool:
    """Converts a local git repository to standard files ref format if reftable extension is present.

    Prevents Docker build failures caused by older Git versions inside containers being unable
    to read Git 2.45+ reftable storage format (fatal: unknown repository extension found: refstorage).
    """
    path = Path(repo_path)
    git_config_file = path / ".git" / "config"
    if not git_config_file.is_file():
        return True

    try:
        if "refstorage = reftable" in git_config_file.read_text():
            res = subprocess.run(
                ["git", "refs", "migrate", "--ref-format=files"],
                cwd=path,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if res.returncode != 0:
                shutil.rmtree(path)
                return False
    except Exception as e:
        logger.warning(f"Failed to check/migrate ref format in {repo_path}: {e}")
    return True


def setup_repo(task_dir: str, silent: bool = False) -> Optional[bool]:
    """Sets up a git repository locally based on task.toml under a cohesive staging presentation."""
    task_name = Path(task_dir).name
    task_toml = os.path.join(task_dir, "task.toml")

    if not os.path.isfile(task_toml):
        if not silent:
            print_error_panel(f"[{task_name}] Error: {task_toml} not found.")
        return None

    try:
        with open(task_toml, "rb") as f:
            data = tomllib.load(f)

        spec_toml = os.path.join(task_dir, "tests", "spec.toml")
        if os.path.isfile(spec_toml):
            with open(spec_toml, "rb") as f:
                spec_data = tomllib.load(f)
                data.update(spec_data)
    except Exception as e:
        if not silent:
            print_error_panel(f"[{task_name}] Error parsing TOML files:\n{e}")
        return False

    try:
        repo_info = data.get("repository", {})
        repo_url = repo_info.get("url") or data.get("metadata", {}).get("repository")
        if not repo_url:
            raise KeyError("Missing repo url")
        if (
            not repo_url.startswith("http")
            and not repo_url.startswith("git@")
            and not repo_url.startswith("ssh://")
        ):
            repo_url = f"https://github.com/{repo_url}"

        before_commit = data.get("before_commit", {})
        commit_sha = before_commit.get("sha")
        change_id = before_commit.get("change_id")
    except KeyError:
        if not silent:
            print_error_panel(
                f"[{task_name}] Error: repository details missing in {task_toml}"
            )
        return False

    # Public repos are cloned inside the Dockerfile rather than copied from the build context,
    # but refresh-patches and verify-tests still diff before against after locally, so every
    # task needs the staged clone regardless of visibility.
    target_path = get_staged_repo_dir(task_dir)
    target_dir = os.path.relpath(target_path, task_dir)

    try:
        if os.path.isdir(target_path):
            if not ensure_standard_git_ref_format(target_path):
                if not silent:
                    print_step_msg(
                        "Re-cloning repository using standard files ref format for Docker compatibility..."
                    )

        if not os.path.isdir(target_path):
            os.makedirs(os.path.join(task_dir, "environment"), exist_ok=True)
            if not silent:
                print_step_msg(f"Cloning repository {repo_url} into {target_dir}...")
            # Force the legacy 'files' ref-format during clone to prevent the host's modern git
            # from defaulting to 'reftable' (refstorage). The older git version inside the
            # Docker container cannot parse reftable repositories,
            # which breaks `git filter-branch` during the image build process.
            subprocess.run(
                ["git", "clone", "--ref-format=files", repo_url, target_dir],
                cwd=task_dir,
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            ensure_standard_git_ref_format(target_path)

        gerrit_sha = None
        if change_id:
            gerrit_sha = handle_gerrit_ref_checkout(
                task_dir, repo_url, change_id, target_path, silent=silent
            )
            if gerrit_sha:
                commit_sha = gerrit_sha

        if not gerrit_sha and commit_sha:
            if not silent:
                print_step_msg("Fetching updates from origin...")
            subprocess.run(
                ["git", "fetch", "origin"],
                cwd=target_path,
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

        if commit_sha:
            if not silent:
                print_step_msg(f"Resetting to commit {commit_sha}...")
            subprocess.run(
                ["git", "reset", "--hard", commit_sha],
                cwd=target_path,
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

        try:
            branches_output = subprocess.check_output(
                ["git", "branch"],
                cwd=target_path,
                text=True,
                stderr=subprocess.DEVNULL,
            )
            for line in branches_output.splitlines():
                if not line.startswith("*"):
                    branch_name = line.strip()
                    if branch_name:
                        subprocess.run(
                            ["git", "branch", "-D", branch_name],
                            cwd=target_path,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                        )
        except subprocess.CalledProcessError:
            pass

        if not silent:
            print_success_msg("Repository prepared successfully.")
        return True
    except subprocess.CalledProcessError as e:
        if not silent:
            print_error_panel(
                f"[{task_name}] Error during git operations on {task_dir}:\n{e}"
            )
        return False


DEFAULT_DATASET_DIR = (
    Path("tasks")
    if Path("tasks").exists()
    else Path(__file__).resolve().parent.parent.parent / "tasks"
)


def add_common_task_args(
    parser: argparse.ArgumentParser,
    default_dir: Path = DEFAULT_DATASET_DIR,
    dir_help: Optional[str] = None,
    include_filter: bool = True,
    include_all: bool = True,
    allow_multiple_tasks: bool = True,
) -> None:
    """Adds standardized task selection arguments across v2 subcommands."""
    nargs_val = "*" if allow_multiple_tasks else "?"
    h_task = (
        "Target Task ID(s) or glob pattern(s) (optional positional)."
        if allow_multiple_tasks
        else "Target Task ID (optional positional)."
    )
    parser.add_argument(
        "task_id",
        type=str,
        nargs=nargs_val,
        metavar="TASK_ID",
        help=h_task,
    )
    h_text = dir_help or f"Target dataset root path (default: {default_dir})."
    parser.add_argument(
        "--dataset-dir",
        "--v2-dir",
        dest="dataset_dir",
        type=Path,
        default=default_dir,
        metavar="PATH",
        help=h_text,
    )
    if include_filter:
        parser.add_argument(
            "--tasks-filter",
            "--filter",
            dest="tasks_filter",
            type=str,
            metavar="FILTER",
            help="Target YAML filter list or wildcard string.",
        )
    if include_all:
        parser.add_argument(
            "-a",
            "--all",
            action="store_true",
            help="Target all matching tasks.",
        )


def discover_tasks(
    dataset_dir: Path,
    task_id: Optional[Union[str, List[str]]],
    filter_str: Optional[str],
) -> List[Tuple[str, Path, Dict[str, Any]]]:
    """Recursively discovers matching task directories across subfolders under dataset_dir."""
    f_set: Optional[Set[str]] = None
    wildcard_pattern: Optional[str] = None
    neg = False

    if filter_str:
        if filter_str.startswith("!"):
            neg = True
            p = Path(filter_str[1:])
        else:
            p = Path(filter_str)

        if p.is_file():
            with open(p) as f:
                d = yaml.safe_load(f)
                f_set = {
                    i.lower()
                    for i in (d if isinstance(d, list) else d.get("tasks", []))
                }
        else:
            wildcard_pattern = filter_str.lower()

    res = []
    if not dataset_dir.exists():
        return res

    if (dataset_dir / "task.toml").is_file():
        candidates = [dataset_dir / "task.toml"]
    else:
        candidates = sorted(dataset_dir.rglob("task.toml"))

    patterns: List[str] = []
    if isinstance(task_id, str):
        patterns = [task_id]
    elif isinstance(task_id, list):
        patterns = task_id

    if len(patterns) > 1:
        cwd_all = set(os.listdir("."))
        cwd_vis = {f for f in cwd_all if not f.startswith(".")}
        if set(patterns) in (cwd_all, cwd_vis):
            patterns = ["*"]
        else:
            prefixes = {p.split("/", 1)[0] for p in patterns if "/" in p}
            if len(prefixes) == 1:
                pfx = prefixes.pop()
                if os.path.isdir(pfx):
                    d_all = {f"{pfx}/{f}" for f in os.listdir(pfx)}
                    d_vis = {f for f in d_all if not f.split("/", 1)[1].startswith(".")}
                    if set(patterns) in (d_all, d_vis):
                        patterns = [f"{pfx}/*"]

    for toml_f in candidates:
        try:
            rel_parts = toml_f.relative_to(dataset_dir).parts[:-1]
        except ValueError:
            rel_parts = ()

        if any(
            p in ("utils", "environment", ".git", "build") or p.startswith(".")
            for p in rel_parts
        ):
            continue

        item = toml_f.parent
        t_id = item.name.lower()
        rel_path = str(item.relative_to(dataset_dir)).replace("\\", "/")
        if patterns and not any(
            fnmatch.fnmatch(t_id, pat.lower())
            or fnmatch.fnmatch(rel_path.lower(), pat.lower())
            for pat in patterns
        ):
            continue

        if f_set is not None:
            if (neg and t_id in f_set) or (not neg and t_id not in f_set):
                continue

        if wildcard_pattern and not fnmatch.fnmatch(t_id, wildcard_pattern):
            continue

        try:
            with open(toml_f, "rb") as tf:
                data = tomllib.load(tf)
            spec_f = item / "tests" / "spec.toml"
            if spec_f.is_file():
                with open(spec_f, "rb") as sf:
                    data.update(tomllib.load(sf))
            res.append((t_id, item, data))
        except Exception as e:
            print_error_panel(f"Error loading {toml_f}:\n{e}")
    return res


DEFAULT_HISTORY_EXCLUSIONS = [
    ".gemini",
    "conductor",
    ".agents",
    "AGENTS.md",
    "GEMINI.md",
    "ONBOARDING.md",
    ".github/workflows/.gemini",
    ".github/workflows/gemini-*",
    "ui-review-migration.patch",
]


def get_git_history_exclusions(task_data: Dict[str, Any]) -> List[str]:
    """Extracts git history exclusions from task.toml (or defaults) and formats them as git diff pathspecs."""
    items = (
        task_data.get("repository", {}).get("remove_from_git_history")
        or task_data.get("remove_from_git_history")
        or DEFAULT_HISTORY_EXCLUSIONS
    )
    res = []
    for it in items:
        clean = it.strip("/").replace('"', "")
        if "/" not in it and not clean.startswith("*"):
            clean = f"*{clean}*"
        res.append(f":!{clean}")
    return res


# ---------------------------------------------------------------------------
# Local commit sources
#
# The after state is deliberately never published: the before commit is public,
# and the fix ships only as solution/solution.patch in the dataset repo, behind the
# canary. refresh-patches still has to diff before against after, so it needs the
# after commit in the staged clone, fetched from a clone on the author's machine.
# ---------------------------------------------------------------------------

LOCAL_SOURCE_MEMO = ".local-source"
LOCAL_SOURCE_ENV = "ANDROID_BENCH_LOCAL_SOURCE"
LOCAL_REF_NAMESPACE = "refs/local-source"

_CLI_LOCAL_SOURCES: List[str] = []


def configure_local_sources(paths: Optional[List[str]]) -> None:
    """Records --local-source paths for this run."""
    global _CLI_LOCAL_SOURCES
    _CLI_LOCAL_SOURCES = [p for p in (paths or []) if p]


def get_local_sources(t_path: Path) -> List[str]:
    """Local repositories to look in, most explicit first: CLI, env, per-task memo."""
    sources: List[str] = list(_CLI_LOCAL_SOURCES)
    env_value = os.environ.get(LOCAL_SOURCE_ENV, "")
    sources.extend(p for p in env_value.split(os.pathsep) if p.strip())

    memo = Path(t_path) / LOCAL_SOURCE_MEMO
    if memo.is_file():
        try:
            sources.extend(
                line.strip()
                for line in memo.read_text().splitlines()
                if line.strip() and not line.startswith("#")
            )
        except Exception:
            pass

    seen, unique = set(), []
    for src in sources:
        expanded = os.path.expanduser(src)
        if expanded not in seen:
            seen.add(expanded)
            unique.append(expanded)
    return unique


def remember_local_source(t_path: Path, source: str) -> None:
    """Persists a working source so later runs need no flag. The memo is gitignored."""
    memo = Path(t_path) / LOCAL_SOURCE_MEMO
    existing = (
        [l.strip() for l in memo.read_text().splitlines() if l.strip()]
        if memo.is_file()
        else []
    )
    if source in existing:
        return
    try:
        memo.write_text(
            "# Local clones holding commits that are not published. Not committed.\n"
            + "\n".join(existing + [source])
            + "\n"
        )
    except Exception:
        pass


def fetch_from_local_sources(
    repo_path: Path, t_path: Path, commit_exists: Callable[[str], bool], missing: List[str]
) -> bool:
    """Fetches missing commits from a local clone into the staged repository."""
    sources = get_local_sources(t_path)
    if not sources:
        return False

    for source in sources:
        if not os.path.isdir(source):
            print_step_msg(f"Local source not found, skipping: {source}")
            continue
        print_step_msg(f"Fetching missing commit(s) from {source}...")
        result = subprocess.run(
            [
                "git",
                "fetch",
                "--no-tags",
                source,
                f"+refs/heads/*:{LOCAL_REF_NAMESPACE}/*",
            ],
            cwd=repo_path,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        if result.returncode != 0:
            print_step_msg(f"Fetch from {source} failed: {result.stderr.strip()[:200]}")
            continue
        if all(commit_exists(sha) for sha in missing):
            remember_local_source(t_path, source)
            return True

    return False


def ensure_commits_exist(
    repo_path: Path,
    t_path: Path,
    before_sha: str,
    after_sha: Optional[str] = None,
) -> bool:
    """Ensures the staged clone exists and holds both target commits.

    The before commit comes from origin. The after commit is deliberately never published --
    it ships only as solution/solution.patch in the dataset -- so when it is missing it is
    fetched from a local clone named by --local-source, the .local-source memo, or
    ANDROID_BENCH_LOCAL_SOURCE.
    """
    if repo_path.is_dir() and not ensure_standard_git_ref_format(repo_path):
        print_info_panel(
            f"[{t_path.name}] Reftable format detected in {repo_path}. Re-cloning with files format for Docker compatibility...",
            title="Git Staging",
        )
        if not setup_repo(str(t_path)):
            return False

    if not repo_path.is_dir():
        print_info_panel(
            f"[{t_path.name}] Cloned repository not detected. Running setup_repo...",
            title="Git Staging",
        )
        if not setup_repo(str(t_path)) or not repo_path.is_dir():
            print_error_panel(
                f"[{t_path.name}] No repository staged at {repo_path}. "
                "Run `v2.task docker <task-id> --no-generate --no-build` to clone it."
            )
            return False

    def commit_exists(ref: str) -> bool:
        if not ref or ref == "HEAD":
            return True
        return (
            subprocess.run(
                ["git", "cat-file", "-e", f"{ref}^{{commit}}"],
                cwd=repo_path,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            ).returncode
            == 0
        )

    def still_missing() -> List[str]:
        return [
            sha for sha in (before_sha, after_sha) if sha and not commit_exists(sha)
        ]

    missing = still_missing()
    if missing:
        print_info_panel(
            f"[{t_path.name}] Target commit(s) on {repo_path} missing locally. Fetching from origin...",
            title="Git Staging",
        )
        if not setup_repo(str(t_path)):
            return False
        missing = still_missing()

    if missing and fetch_from_local_sources(repo_path, t_path, commit_exists, missing):
        missing = still_missing()

    if missing:
        print_error_panel(
            f"[{t_path.name}] Commit(s) not in {repo_path}: {', '.join(missing)}\n"
            "The after state is never published, so it has to come from a local clone:\n"
            f"  v2.task refresh-patches {t_path.name} --local-source /path/to/local/repo\n"
            f"The path is remembered in {t_path}/{LOCAL_SOURCE_MEMO} "
            f"(gitignored), or set {LOCAL_SOURCE_ENV}."
        )
        return False

    return True


def leading_comment_block(text: str) -> str:
    """Returns the run of comment lines at the top of a file, including its trailing newline."""
    kept = []
    for line in text.splitlines():
        if line.startswith("#"):
            kept.append(line)
        else:
            break
    return "".join(f"{line}\n" for line in kept)


def reformat_task_toml(task_dir: Path) -> None:
    """Loads and re-dumps task.toml to enforce standard formatting."""
    task_toml = task_dir / "task.toml"
    if not task_toml.is_file():
        return
    try:
        with open(task_toml, "rb") as f:
            data = tomllib.load(f)

        ordered_data = {}
        # 1. Root properties (scalars/lists)
        for k, v in data.items():
            if not isinstance(v, dict):
                ordered_data[k] = v

        # 2. [task] table
        if "task" in data:
            ordered_data["task"] = data["task"]

        # 3. All other tables
        for k, v in data.items():
            if isinstance(v, dict) and k != "task":
                ordered_data[k] = v

        existing_text = task_toml.read_text(encoding="utf-8")
        # tomli_w drops comments, and the two lines at the top of every task file are the
        # training-corpus canary that CI checks for. Carry the leading comment block over.
        toml_text = leading_comment_block(existing_text) + tomli_w.dumps(ordered_data)

        if existing_text != toml_text:
            task_toml.write_text(toml_text, encoding="utf-8")
            logger.info(f"[{task_dir.name}] Reformatted task.toml")
    except Exception as e:
        print_error_panel(f"[{task_dir.name}] Failed to reformat task.toml: {e}")
