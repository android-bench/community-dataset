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
Pristine Golden Patchset Generator for Dataset V2 Tasks (`v2.refresh-patches`).

Calculates canonical Git diffs between `before_commit.sha` and `after_commit.sha` (or HEAD),
exporting clean `solution.patch` (filtering out AI scratch folders and test files)
and `test.patch` (scoped exclusively to `test_files`).
"""

import argparse
import logging
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tomllib
import tomli_w
from typing import Any, Dict, List, Optional, Tuple
from rich import print as rprint
from rich.panel import Panel

from v2.task_commands.common import (
    DEFAULT_DATASET_DIR,
    add_common_task_args,
    discover_tasks,
    ensure_commits_exist,
    get_git_history_exclusions,
    get_staged_repo_dir,
    setup_repo,
    reformat_task_toml,
)

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
logger = logging.getLogger("v2.refresh")


def update_spec_toml(spec_path: Path, updates: Dict[str, Any]) -> None:
    """Updates spec.toml with the provided updates."""
    if not spec_path.is_file():
        return

    try:
        with open(spec_path, "rb") as f:
            data = tomllib.load(f)

        if "test_files" in updates:
            if "acceptance_criteria" not in data:
                data["acceptance_criteria"] = {}
            data["acceptance_criteria"]["test_files"] = updates["test_files"]

        if "ignored_files" in updates:
            if "repository" not in data:
                data["repository"] = {}
            data["repository"]["ignored_files"] = updates["ignored_files"]

        with open(spec_path, "wb") as f:
            tomli_w.dump(data, f)
        logger.info(f"Updated spec.toml: {spec_path}")
    except Exception as e:
        logger.error(f"Failed to update spec.toml at {spec_path}: {e}")


def analyze_git_changes(
    repo_path: Path, before_sha: str, after_sha: str
) -> Tuple[List[Tuple[str, str]], List[str], List[str]]:
    """Analyzes git changes and returns (renames, deletes, additions)."""
    cmd = ["git", "diff", "--name-status", f"{before_sha}..{after_sha}"]
    try:
        res = subprocess.run(
            cmd, cwd=repo_path, check=True, capture_output=True, text=True
        )
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to run git diff in {repo_path}: {e.stderr}")
        return [], [], []

    renames = []
    deletes = []
    additions = []

    for line in res.stdout.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        status = parts[0]
        if status.startswith("R") and len(parts) >= 3:
            renames.append((parts[1], parts[2]))
        elif status == "D":
            deletes.append(parts[1])
        elif status == "A":
            additions.append(parts[1])
        elif status.startswith("C") and len(parts) >= 3:
            additions.append(parts[2])

    return renames, deletes, additions


def check_and_update_spec(
    t_id: str,
    t_path: Path,
    t_data: Dict[str, Any],
    repo_path: Path,
    before_sha: str,
    after_sha: str,
) -> Tuple[bool, List[str]]:
    """Checks git changes, updates spec.toml if safe, and collects anomalies."""
    spec_path = t_path / "tests" / "spec.toml"
    if not spec_path.is_file():
        return False, []

    test_files = (
        t_data.get("acceptance_criteria", {}).get("test_files")
        or t_data.get("test_files")
        or []
    )
    ignored_files = (
        t_data.get("repository", {}).get("ignored_files")
        or t_data.get("ignored_files")
        or []
    )

    renames, deletes, additions = analyze_git_changes(repo_path, before_sha, after_sha)
    if not (renames or deletes or additions):
        return False, []

    annotated_excludes = get_annotated_exclusions(
        repo_path, before_sha, after_sha, t_id
    )
    excluded_paths = {p[2:] for p in annotated_excludes if p.startswith(":!")}

    spec_changed = False
    updated_test_files = list(test_files)
    updated_ignored_files = list(ignored_files)
    auto_updated_files = set()

    # 1. Automatic Updates (Safe Actions)
    for old_path, new_path in renames:
        if old_path in updated_test_files:
            idx = updated_test_files.index(old_path)
            updated_test_files[idx] = new_path
            auto_updated_files.add(new_path)
            logger.info(
                f"[{t_id}] Auto-updating renamed test file: {old_path} -> {new_path}"
            )
            spec_changed = True

        if old_path in updated_ignored_files:
            if new_path not in updated_ignored_files:
                updated_ignored_files.append(new_path)
                auto_updated_files.add(new_path)
                logger.info(
                    f"[{t_id}] Auto-adding renamed ignored file: {new_path} (keeping {old_path})"
                )
                spec_changed = True

    # Filter out test_files explicitly excluded by @ExcludeFromDataset
    filtered_test_files = [tf for tf in updated_test_files if tf not in excluded_paths]
    if len(filtered_test_files) != len(updated_test_files):
        removed_files = set(updated_test_files) - set(filtered_test_files)
        updated_test_files = filtered_test_files
        spec_changed = True
        logger.info(
            f"[{t_id}] Auto-removed @ExcludeFromDataset annotated test file(s) from test_files: {removed_files}"
        )

    if spec_changed:
        updates = {
            "test_files": updated_test_files,
            "ignored_files": updated_ignored_files,
        }
        update_spec_toml(spec_path, updates)
        if "acceptance_criteria" in t_data:
            t_data["acceptance_criteria"]["test_files"] = updated_test_files
        else:
            t_data["test_files"] = updated_test_files

        if "repository" in t_data:
            t_data["repository"]["ignored_files"] = updated_ignored_files
        else:
            t_data["ignored_files"] = updated_ignored_files

    # 2. Warnings for Uncategorized Potential Tests
    potential_missing_tests = []

    for f in additions:
        if f in auto_updated_files:
            continue
        if f in updated_test_files or f in updated_ignored_files:
            continue
        if f in excluded_paths:
            continue

        path_parts = Path(f).parts
        is_test = any(
            p in ("test", "androidTest", "tests", "sharedTest", "screenshots")
            for p in path_parts
        ) or f.endswith(("Test.kt", "Test.java", "Tests.kt", "Tests.java"))
        if is_test:
            potential_missing_tests.append(f)

    for old_path, new_path in renames:
        if new_path in auto_updated_files:
            continue
        if new_path in updated_test_files or new_path in updated_ignored_files:
            continue
        if new_path in excluded_paths:
            continue
        path_parts = Path(new_path).parts
        is_test = any(
            p in ("test", "androidTest", "tests", "sharedTest", "screenshots")
            for p in path_parts
        ) or new_path.endswith(("Test.kt", "Test.java", "Tests.kt", "Tests.java"))
        if is_test:
            potential_missing_tests.append(new_path)

    return spec_changed, potential_missing_tests


def get_test_files_from_patch(patch_path: Path) -> List[str]:
    """Extracts target file paths from a git patch file."""
    if not patch_path.is_file():
        return []
    files = []
    try:
        for line in patch_path.read_text(
            encoding="utf-8", errors="ignore"
        ).splitlines():
            if line.startswith("+++ b/") and line != "+++ /dev/null":
                files.append(line[6:].strip())
    except Exception:
        pass
    return sorted(list(set(files)))


def get_added_lines_for_file_in_patch(patch_path: Path, filename: str) -> str:
    """Extracts only lines starting with '+' for a specific file in a git patch."""
    if not patch_path.is_file():
        return ""
    lines = []
    in_file = False
    try:
        for line in patch_path.read_text(
            encoding="utf-8", errors="ignore"
        ).splitlines():
            if line.startswith("diff --git "):
                in_file = (
                    f" a/{filename} " in line
                    or f" b/{filename}" in line
                    or line.endswith(f"/{filename}")
                )
            elif in_file:
                if line.startswith("+") and not line.startswith("+++"):
                    lines.append(line[1:])
    except Exception:
        pass
    return "\n".join(lines)


def sync_visual_validation(
    t_id: str,
    t_path: Path,
    t_data: Dict[str, Any],
    repo_path: Path,
    warnings: Optional[Dict[str, List[str]]] = None,
) -> bool:
    """Synchronizes visual_evaluator.py invocation arguments in tests/validate.sh based on detected screenshot tests."""
    validate_sh = t_path / "tests" / "validate.sh"
    if not validate_sh.is_file():
        return False

    content = validate_sh.read_text(encoding="utf-8", errors="ignore")

    test_files = get_test_files_from_patch(t_path / "tests" / "test.patch")
    if not test_files:
        return False

    test_classes = []
    total_test_methods = 0
    total_assertions = 0
    diff_dir = ""

    for tf in test_files:
        if not (tf.endswith(".kt")):
            continue

        added_fc = get_added_lines_for_file_in_patch(
            t_path / "tests" / "test.patch", tf
        )
        methods_count = len(re.findall(r"@Test\b", added_fc))
        if methods_count == 0:
            continue

        is_screenshot_test = (
            any(
                kw in added_fc
                for kw in ("Dropshots", "assertSnapshot", "assertScreenshot")
            )
            or re.search(r"(?:Screenshot|Dropshot)s?Test", tf) is not None
        )
        if not is_screenshot_test:
            continue

        fc = None
        file_path = repo_path / tf
        if file_path.is_file():
            try:
                fc = file_path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                pass
        if fc is None and (t_path / tf).is_file():
            try:
                fc = (t_path / tf).read_text(encoding="utf-8", errors="ignore")
            except Exception:
                pass
        if fc is None and repo_path.is_dir():
            after_sha = t_data.get("after_commit", {}).get("sha") or "HEAD"
            try:
                res = subprocess.run(
                    ["git", "show", f"{after_sha}:{tf}"],
                    cwd=repo_path,
                    capture_output=True,
                    text=True,
                    check=True,
                )
                fc = res.stdout
            except Exception:
                pass
        if not fc:
            continue

        pkg_match = re.search(r"^\s*package\s+([\w\.]+)", fc, re.MULTILINE)
        pkg = pkg_match.group(1).rstrip(";") if pkg_match else ""

        for m in re.finditer(
            r"^\s*(?:@\w+(?:\([^)]*\))?\s+)*(?:(?:abstract|open|final|internal|public|data|sealed)\s+)*class\s+([A-Za-z0-9_]+)",
            fc,
            re.MULTILINE,
        ):
            if "abstract " in m.group(0):
                continue
            cls_name = m.group(1)
            fq_class = f"{pkg}.{cls_name}" if pkg else cls_name
            test_classes.append(fq_class)

        assertions_count = len(
            re.findall(r"\bassert\w*(?:Snapshot|Screenshot)\b", added_fc)
        )
        total_test_methods += methods_count
        total_assertions += assertions_count

        if not diff_dir:
            mod_dir = "app"
            if "/src/androidTest" in tf:
                mod_dir = tf.split("/src/androidTest")[0]
            elif "/src/test" in tf:
                mod_dir = tf.split("/src/test")[0]
            if mod_dir and mod_dir != tf:
                diff_dir = f"/workspace/testbed/{mod_dir}/build/test-results/dropshots/DebugAndroidTest/diff/"
            else:
                diff_dir = "/workspace/testbed/app/build/test-results/dropshots/DebugAndroidTest/diff/"

    if total_test_methods == 0:
        return False

    png_count = sum(1 for tf in test_files if tf.endswith(".png"))
    if png_count > 0 and not (total_test_methods == total_assertions == png_count):
        warn_msg = (
            f"Visual test count mismatch in {t_id}: @Test methods ({total_test_methods}) != "
            f"assertions ({total_assertions}) != .png goldens ({png_count})"
        )
        logger.warning(f"[{t_id}] {warn_msg}")
        if warnings is not None:
            warnings.setdefault(t_id, []).append(warn_msg)

    test_classes = sorted(list(set(test_classes)))
    new_content = content

    if "visual_evaluator.py" not in content:
        block_lines = [
            'echo ""',
            'echo "--- Visual Validation ---"',
            "PYTHONPATH=/tests/verifier python3 /tests/verifier/vision/visual_evaluator.py \\",
            f"    --expected-count {total_test_methods} \\",
        ]
        if len(test_classes) == 1:
            block_lines.append(f"    --test-class {test_classes[0]} \\")
        else:
            block_lines.append("    --test-class \\")
            for c in test_classes:
                block_lines.append(f"        {c} \\")
        block_lines.append(f"    --diff-dir {diff_dir} \\")
        block_lines.append(
            "    --validate-results-file /logs/verifier/validate_results.txt"
        )
        block_lines.extend(
            [
                "if [ $? -ne 0 ]; then",
                "    CHECK_FAILED=1",
                "fi",
                "",
            ]
        )
        block_str = "\n".join(block_lines)
        if "validate_finalize" in content:
            new_content = content.replace(
                "validate_finalize", f"{block_str}validate_finalize", 1
            )
        else:
            new_content = content.rstrip() + "\n\n" + block_str
    else:
        new_content = re.sub(
            r"--expected-count\s+\d+",
            f"--expected-count {total_test_methods}",
            new_content,
        )

        if test_classes and "--test-class" in new_content:

            def replace_test_class(m: re.Match) -> str:
                if len(test_classes) == 1:
                    return f"--test-class {test_classes[0]} \\\n    "
                lines = ["--test-class \\"] + [f"        {c} \\" for c in test_classes]
                return "\n".join(lines) + "\n    "

            new_content = re.sub(
                r"--test-class\s+(?:\\?\s*[a-zA-Z0-9_$.]+\s*\\?\s*)+(?=--|\s*$)",
                replace_test_class,
                new_content,
            )

        if diff_dir and "--diff-dir" in new_content:
            new_content = re.sub(
                r"--diff-dir\s+\S+", f"--diff-dir {diff_dir}", new_content
            )

    if new_content != content:
        validate_sh.write_text(new_content, encoding="utf-8")
        logger.info(
            f"[{t_id}] Synced visual validation in tests/validate.sh (expected-count={total_test_methods}, test-classes={len(test_classes)})"
        )
        return True
    return False


def uses_full_index(patch_file: Path) -> bool:
    """Checks if an existing patch file uses full 40-character index hashes."""
    if not patch_file.is_file():
        return False
    try:
        with open(patch_file, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                if line.startswith("index "):
                    parts = line.strip().split(" ")[1].split("..")
                    if any(len(p) >= 40 and not p.startswith("0" * 40) for p in parts):
                        return True
                    if any(len(p) == 7 and not p.startswith("0" * 7) for p in parts):
                        return False
    except Exception:
        pass
    return False


def run_git_diff(
    repo_path: Path,
    before_ref: str,
    after_ref: str,
    pathspecs: List[str],
    extra_flags: Optional[List[str]] = None,
) -> str:
    """Runs git diff between before_ref and after_ref with given pathspecs."""
    flags = ["--binary"] + (extra_flags or [])
    cmd = ["git", "diff", f"{before_ref}..{after_ref}"] + flags + ["--"] + pathspecs
    logger.debug(f"Executing diff: {' '.join(cmd)}")
    try:
        res = subprocess.run(
            cmd, cwd=repo_path, check=True, capture_output=True, text=True
        )
        return res.stdout
    except subprocess.CalledProcessError as e:
        logger.error(f"Git diff failed in {repo_path}:\n{e.stderr}")
        raise


def write_patch(target_path: Path, content: str, label: str) -> None:
    """Writes patch content to disk atomically."""
    if not content or not content.strip():
        logger.info(f"[{label}] No changes detected. Skipping {target_path}")
        return

    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(content + "\n")

    try:
        disp_path = target_path.relative_to(Path.cwd())
    except ValueError:
        disp_path = target_path

    logger.info(f"[{label}] Successfully exported golden patch: {disp_path}")


def trim_task_id(task_id: str) -> str:
    """Trims task ID or variant prefix for comparison."""
    if not task_id:
        return ""
    s = task_id.lower().strip()
    if s.startswith("open-"):
        s = s[5:]
    return re.sub(r"[^a-z0-9]", "", s)


def is_task_excluded_by_annotation(content: str, t_id: str) -> bool:
    """Checks if file content contains @ExcludeFromDataset matching current task ID t_id."""
    if "@ExcludeFromDataset" not in content:
        return False

    matches = list(
        re.finditer(
            r"@ExcludeFromDataset(?:\s*\(\s*taskId\s*=\s*\"([^\"]*)\"\s*\))?",
            content,
        )
    )
    if not matches:
        return True

    norm_tid = trim_task_id(t_id)

    for m in matches:
        task_id_param = m.group(1)
        # Permanent exclusion if taskId is omitted or empty string
        if task_id_param is None or task_id_param == "":
            return True

        norm_param = trim_task_id(task_id_param)

        # Strict exact trimmed equality
        if norm_param == norm_tid:
            return True

    return False


def get_annotated_exclusions(
    repo_path: Path, before_sha: str, after_sha: str, t_id: str
) -> List[str]:
    """Discovers changed files annotated with @ExcludeFromDataset in after_sha (tracking renames) and returns git pathspec exclusions."""
    if not repo_path.is_dir():
        return []

    cmd = ["git", "diff", "--name-status", f"{before_sha}..{after_sha}"]
    try:
        res = subprocess.run(
            cmd, cwd=repo_path, check=True, capture_output=True, text=True
        )
        lines = [line.strip() for line in res.stdout.splitlines() if line.strip()]
    except subprocess.CalledProcessError:
        return []

    exclusions = []
    candidates = []

    for l in lines:
        parts = l.split()
        status = parts[0]
        if status == "D":
            continue
        elif status.startswith("R") and len(parts) >= 3:
            old_f = parts[1]
            new_f = parts[2]
            candidates.append((new_f, old_f))
        elif len(parts) >= 2:
            new_f = parts[1]
            candidates.append((new_f, None))

    for cf, orig_f in candidates:
        try:
            s_cmd = ["git", "show", f"{after_sha}:{cf}"]
            s_res = subprocess.run(
                s_cmd, cwd=repo_path, capture_output=True, text=True, check=True
            )
            content = s_res.stdout
            if is_task_excluded_by_annotation(content, t_id):
                exclusions.append(f":!{cf}")
                if orig_f:
                    exclusions.append(f":!{orig_f}")
        except Exception:
            pass

    return exclusions


def generate_canonical_patches(
    t_id: str,
    t_path: Path,
    t_data: Dict[str, Any],
    stale_tasks: Optional[List[str]] = None,
    warnings: Optional[Dict[str, List[str]]] = None,
) -> bool:
    """Calculates pristine canonical golden diffs and writes solution.patch and test.patch directly to target directories."""
    logger.info(f"[{t_id}] Refreshing golden patches...")

    before_commit = t_data.get("before_commit", {})
    before_sha = before_commit.get("sha")
    if not before_sha:
        logger.error(f"[{t_id}] 'before_commit.sha' missing in task.toml")
        return False

    after_commit = t_data.get("after_commit", {})
    after_sha = after_commit.get("sha") or "HEAD"

    repo_path = Path(get_staged_repo_dir(t_path))
    if not ensure_commits_exist(repo_path, t_path, before_sha, after_sha):
        logger.error(
            f"[{t_id}] Failed to prepare repository or verify commits at {repo_path}"
        )
        return False

    # Check and update spec.toml before generating patches
    _, missing_tests = check_and_update_spec(
        t_id, t_path, t_data, repo_path, before_sha, after_sha
    )
    if missing_tests and warnings is not None:
        warnings[t_id] = missing_tests

    try:
        log_res = subprocess.run(
            ["git", "log", "--oneline", f"{before_sha}..{after_sha}"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True,
        )
        commits_str = log_res.stdout.strip()
        if commits_str:
            rprint(
                Panel(
                    commits_str,
                    title=f"[cyan]{t_id}[/]: Commits ({before_sha[:7]}..{after_sha[:7] if after_sha != 'HEAD' else 'HEAD'})",
                    border_style="cyan",
                )
            )
        else:
            logger.info(
                f"[{t_id}] No commits found between {before_sha[:7]}..{after_sha[:7] if after_sha != 'HEAD' else 'HEAD'}"
            )
    except Exception as e:
        logger.debug(f"[{t_id}] Failed to retrieve commit log: {e}")

    df_path = t_path / "environment" / "Dockerfile"
    if df_path.is_file():
        try:
            df_content = df_path.read_text(encoding="utf-8", errors="ignore")
            if before_sha not in df_content:
                logger.warning(
                    f"[{t_id}] Notice: 'before_commit.sha' differed from environment/Dockerfile. "
                    "Regenerating Dockerfile automatically..."
                )
                from v2.task_commands.docker import generate_task_dockerfile

                generate_task_dockerfile(t_id, t_path, t_data)
                if stale_tasks is not None:
                    stale_tasks.append(t_id)
        except Exception:
            pass

    test_files = (
        t_data.get("acceptance_criteria", {}).get("test_files")
        or t_data.get("test_files")
        or []
    )
    ignored_files = (
        t_data.get("repository", {}).get("ignored_files")
        or t_data.get("ignored_files")
        or []
    )
    history_excludes = get_git_history_exclusions(t_data)
    annotated_excludes = get_annotated_exclusions(
        repo_path, before_sha, after_sha, t_id
    )

    ignore_specs = [f":!{inf}" for inf in ignored_files if inf]
    solution_base_excludes = history_excludes + ignore_specs
    test_base_excludes = history_excludes + annotated_excludes + ignore_specs

    # 1. Capture Golden Solution Diff (Excluding test_files & scratch/ignores)
    test_excludes = [f":!{tf}" for tf in test_files if tf]
    solution_pathspecs = (
        ["."] if not test_excludes else ["."] + test_excludes
    ) + solution_base_excludes

    try:
        sol_content = run_git_diff(repo_path, before_sha, after_sha, solution_pathspecs)
        sol_patch = t_path / "solution" / "solution.patch"
        write_patch(sol_patch, sol_content, f"{t_id}:solution")
    except Exception as e:
        logger.error(f"[{t_id}] Failed to generate solution patch: {e}")
        return False

    # 2. Capture Verification Test Suite Diff (Scoped strictly to test_files)
    if test_files:
        test_pathspecs = [tf for tf in test_files if tf] + test_base_excludes
        try:
            test_content = run_git_diff(
                repo_path, before_sha, after_sha, test_pathspecs
            )
            test_patch = t_path / "tests" / "test.patch"
            write_patch(test_patch, test_content, f"{t_id}:test")
        except Exception as e:
            logger.error(f"[{t_id}] Failed to generate test patch: {e}")
            return False
    else:
        logger.info(
            f"[{t_id}] No 'test_files' registered in task.toml. Omit test.patch export."
        )

    sync_visual_validation(t_id, t_path, t_data, repo_path, warnings=warnings)

    return True


def main(args: Optional[argparse.Namespace] = None) -> None:
    if args is None:
        parser = argparse.ArgumentParser(
            description="Pristine Golden Patchset Generator for Dataset V2 Tasks (`v2.refresh-patches`)."
        )
        add_common_task_args(parser)

        args = parser.parse_args()

    dataset_root: Path = getattr(
        args,
        "dataset_dir",
        getattr(args, "v2_dir", DEFAULT_DATASET_DIR),
    )

    tasks = discover_tasks(
        dataset_root,
        getattr(args, "task_id", None),
        getattr(args, "tasks_filter", None),
    )

    if not tasks:
        logger.warning("No Dataset V2 tasks matched your selection criteria.")
        return

    logger.info(f"Starting golden patch generation across {len(tasks)} task(s)...")

    stale_cohort: list[str] = []
    warnings_cohort: Dict[str, List[str]] = {}
    success_count = 0
    fail_count = 0

    for t_id, t_path, t_data in tasks:
        if generate_canonical_patches(
            t_id,
            t_path,
            t_data,
            stale_tasks=stale_cohort,
            warnings=warnings_cohort,
        ):
            success_count += 1
            reformat_task_toml(t_path)
        else:
            fail_count += 1

    rprint(
        Panel(
            f"Successfully refreshed patches for [bold green]{success_count}[/] task(s).\n"
            f"Errors encountered in [bold red]{fail_count}[/] task(s).",
            title="[bold green]Golden Patchset Generation Complete[/]",
            border_style="green",
        )
    )

    if stale_cohort:
        rprint(
            Panel(
                f"[bold yellow]⚠️ ATTENTION: Stale Task Baseline(s) Detected![/]\n\n"
                f"The following {len(stale_cohort)} task(s) had a `before_commit.sha` that differed from their active `environment/Dockerfile`.\n"
                f"Their Dockerfiles were automatically regenerated on disk:\n\n"
                + "\n".join(f"  • [cyan]{st}[/]" for st in stale_cohort)
                + "\n\n[bold yellow]Next Action[/]: Run `v2.task docker [task_id]` to rebuild their evaluation container images.",
                title="[bold yellow]Out-of-Sync Evaluation Container Alert[/]",
                border_style="yellow",
            )
        )

    if warnings_cohort:
        warn_msg = (
            "[bold red]⚠️  ATTENTION: Potential Unregistered Test Files Detected![/]\n\n"
            "The following tasks have changed files that look like tests but are not registered in `spec.toml`.\n"
            "They will be included in the `solution.patch` instead of `test.patch` unless registered.\n\n"
        )
        for t_id, files in warnings_cohort.items():
            warn_msg += f"[cyan]{t_id}[/]:\n"
            for f in files:
                warn_msg += f"  • {f}\n"
        rprint(Panel(warn_msg, title="[bold red]Unregistered Test Files[/]", border_style="red"))


if __name__ == "__main__":
    main()
