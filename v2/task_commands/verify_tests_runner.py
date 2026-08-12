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
"""Runs build and test verification inside the Docker container for a given task with feedback loops."""

import argparse
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

# Set PYTHONPATH to include /dataset_root to allow local imports
sys.path.insert(0, "/dataset_root")


class TestsExecutionResult:
    def __init__(self):
        self.passed_tests: Set[str] = set()
        self.failed_tests: Set[str] = set()
        self.exit_code: int = 0
        self.stdout: str = ""
        self.stderr: str = ""


def update_local_properties(repo_dir: str, java_home: str) -> None:
    """Updates local properties files and JAVA_HOME environment for Gradle builds."""
    if java_home:
        os.environ["JAVA_HOME"] = java_home
        gradle_dir = os.path.join(repo_dir, ".gradle")
        os.makedirs(gradle_dir, exist_ok=True)
        config_properties_path = os.path.join(gradle_dir, "config.properties")
        try:
            os.remove(config_properties_path)
        except OSError:
            pass


def start_and_wait_for_emulator(
    log_file: str, emulator_avd_name: str, timeout_seconds: int = 180
) -> Optional[subprocess.Popen]:
    """Starts an Android emulator and waits for it to be fully booted."""
    android_home = os.environ.get("ANDROID_HOME", "/sdk")
    emulator_path = os.path.join(android_home, "emulator", "emulator")
    adb_path = os.path.join(android_home, "platform-tools", "adb")

    emulator_command = [
        emulator_path,
        "-avd",
        emulator_avd_name,
        "-no-snapshot",
        "-no-window",
    ]
    check_boot_command = [adb_path, "shell", "getprop", "sys.boot_completed"]

    print(f"Starting emulator: {' '.join(emulator_command)}")
    start_time = time.time()
    try:
        proc = subprocess.Popen(
            emulator_command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        time.sleep(5)

        print("Waiting for emulator to boot...")
        while time.time() - start_time < timeout_seconds:
            res = subprocess.run(
                check_boot_command, capture_output=True, text=True, check=False
            )
            if "1" in res.stdout.strip():
                print("Emulator fully booted and ready!")
                return proc
            time.sleep(5)
        print("Emulator did not boot within the timeout period.")
        return proc
    except Exception as e:
        print(f"An error occurred during emulator startup: {e}")
        return None


def run_tests(
    project_dir: str,
    run_tests_command: str,
    timeout: Optional[int] = 1800,
    workers: int = 8,
    mount_path: str = "/dataset_root",
    remove_task_names: bool = False,
) -> TestsExecutionResult:
    """Runs test execution command and parses results."""
    res_obj = TestsExecutionResult()
    try:
        proc = subprocess.run(
            run_tests_command,
            shell=True,
            cwd=project_dir,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        res_obj.exit_code = proc.returncode
        res_obj.stdout = proc.stdout
        res_obj.stderr = proc.stderr
    except subprocess.TimeoutExpired as e:
        res_obj.exit_code = -1
        res_obj.stdout = str(e.stdout or "")
        res_obj.stderr = "Timed out executing test command."
    return res_obj


def run_cmd(args, cwd=None, check=True):
    print(f"Running command: {' '.join(args)} (cwd={cwd})")
    res = subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=False)
    if res.stdout:
        print(f"STDOUT:\n{res.stdout}")
    if res.stderr:
        print(f"STDERR:\n{res.stderr}")
    if check and res.returncode != 0:
        raise RuntimeError(
            f"Command {' '.join(args)} failed with exit code {res.returncode}"
        )
    return res


def clean_test_results(repo_dir: str):
    """Deletes any stale test result XML files on disk before a new test run."""
    print("Cleaning stale test result files...")
    for results_dir in Path(repo_dir).glob("**/androidTest-results"):
        if results_dir.is_dir():
            shutil.rmtree(results_dir, ignore_errors=True)
    for results_dir in Path(repo_dir).glob("**/test-results"):
        if results_dir.is_dir():
            shutil.rmtree(results_dir, ignore_errors=True)


def find_repo_dir():
    return "/workspace/testbed"


def get_task_name_from_command(cmd: str) -> str:
    """Extracts task name (e.g. connectedDebugAndroidTest or testDebugUnitTest) from Gradle command."""
    match = re.search(r"([a-zA-Z0-9_]+UnitTest|[a-zA-Z0-9_]+AndroidTest)", cmd)
    if match:
        return match.group(1)
    raise ValueError(f"Could not extract task name from command: {cmd}")


def split_gradle_command(cmd: str) -> list[str]:
    """Splits a single gradle command with multiple tasks into individual task commands."""
    if not cmd:
        return []
    parts = cmd.split()
    if not parts:
        return []

    gradlew = parts[0]
    options = []
    tasks = []

    for part in parts[1:]:
        if part.startswith("-"):
            options.append(part)
        else:
            tasks.append(part)

    if not tasks:
        return [cmd]

    individual_cmds = []
    for task in tasks:
        individual_cmds.append(f"{gradlew} {' '.join(options)} {task}")

    return individual_cmds


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


def extract_tests_from_file(file_path: Path, task_name: str) -> set[str]:
    """Parses a Kotlin or Java test file to find individual test methods."""
    tests = set()
    if not file_path.is_file():
        return tests
    try:
        content = file_path.read_text(encoding="utf-8")

        # Extract package name
        package_match = re.search(r"package\s+([a-zA-Z0-9._]+)", content)
        package = package_match.group(1).strip() if package_match else ""

        # Extract class name
        class_match = re.search(r"(?:class|object)\s+([a-zA-Z0-9_]+)", content)
        class_name = class_match.group(1).strip() if class_match else file_path.stem

        full_class_name = f"{package}.{class_name}" if package else class_name

        # Find all test methods annotated with @Test
        matches = re.finditer(r"@Test\b", content)
        for match in matches:
            start_idx = match.end()
            lookahead = content[start_idx : start_idx + 300]
            # Match Kotlin 'fun method()' or Java 'void method()'
            method_match = re.search(r"(?:fun|void)\s+([a-zA-Z0-9_]+)", lookahead)
            if method_match:
                method_name = method_match.group(1).strip()
                tests.add(f"{task_name}#Test {method_name}({full_class_name})")
    except Exception as e:
        print(f"Error parsing tests from {file_path}: {e}")
    return tests


def run_command_with_feedback(
    repo_dir: str,
    cmd: str,
    task_name: str,
    failed_set: set[str],
    renamed_files: list[tuple[Path, Path]],
) -> tuple[bool, str, str]:
    """Runs a Gradle build or test command.

    If compilation fails, identifies the compilation errors, parses individual tests
    from failing source files, registers them as failed, renames files to exclude them, and retries.
    """
    max_iterations = 10
    for iteration in range(max_iterations):
        print(f"\n[Compilation Loop Iteration {iteration+1}] Running: {cmd}")
        res = subprocess.run(
            cmd, shell=True, cwd=repo_dir, capture_output=True, text=True
        )

        # ONLY inspect compile errors if the command actually failed (non-zero exit code)!
        if res.returncode != 0:
            combined_output = res.stdout + "\n" + res.stderr
            compilation_errors_found = False
            failing_files = set()

            # Parse compile errors for file paths (Kotlin & Java)
            for line in combined_output.splitlines():
                line_lower = line.lower()
                # Check for explicit Kotlin and Java compiler signatures
                is_kotlin_err = re.search(r"\b[eE]:\s+([^\s:]+\.kt)\b", line)
                is_java_err = re.search(r"([^\s:]+\.java):\d+:\s+error:", line)
                is_generic_err = (
                    "error" in line_lower or "unresolved" in line_lower
                ) and re.search(r"([^\s:]+\.(?:kt|java))", line)

                match = is_kotlin_err or is_java_err or is_generic_err
                if match:
                    path_str = match.group(1)
                    f_path = Path(path_str)

                    # Try resolving absolute path first
                    if f_path.is_absolute() and f_path.is_file():
                        failing_files.add(f_path)
                        compilation_errors_found = True
                        continue

                    # Try resolving relative to repo_dir
                    f_path_rel = Path(repo_dir) / path_str
                    if f_path_rel.is_file():
                        failing_files.add(f_path_rel)
                        compilation_errors_found = True
                        continue

                    # Fallback: Search repo_dir recursively for filename
                    filename = Path(path_str).name
                    found_file = False
                    for root, dirs, files in os.walk(repo_dir):
                        if filename in files:
                            f_path_found = Path(root) / filename
                            failing_files.add(f_path_found)
                            compilation_errors_found = True
                            found_file = True
                            break

                    if found_file:
                        continue

            if compilation_errors_found and failing_files:
                print(
                    f"Compilation error detected in {len(failing_files)} file(s). Renaming to exclude and retrying..."
                )
                for f in failing_files:
                    # 1. Parse individual tests in file and add them to failed set
                    extracted_tests = extract_tests_from_file(f, task_name)
                    if extracted_tests:
                        print(
                            f"Adding {len(extracted_tests)} test(s) from {f.name} to FAILED due to compilation error."
                        )
                        failed_set.update(extracted_tests)

                    # 2. Rename to .bak to exclude from Kotlin/Java compilation
                    bak_path = f.with_suffix(f.suffix + ".bak")
                    try:
                        print(f"EXCLUDING FILE: {f.absolute().as_posix()}")
                        f.rename(bak_path)
                        renamed_files.append((f, bak_path))
                    except Exception as e:
                        print(f"Failed to rename {f} to {bak_path}: {e}")

                print("Retrying after renaming...")
                continue

            # If the build failed, but it wasn't a compiler error, return failure
            return (False, res.stdout, res.stderr)

        else:
            # Build succeeded! No compilation errors, return success immediately
            return (True, res.stdout, res.stderr)

    return (False, "Max feedback iterations reached", "")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--task-dir", required=True)
    parser.add_argument("--before-commit", required=True)
    parser.add_argument("--build-commands", required=True)
    parser.add_argument("--unit-test-commands", required=True)
    parser.add_argument("--android-test-commands", required=True)
    parser.add_argument("--solution-script", required=True)
    parser.add_argument("--test-patch", required=True)
    parser.add_argument("--results-output", required=True)
    parser.add_argument("--target-sdk", type=int, default=34)
    parser.add_argument("--emulator-name", required=True)
    parser.add_argument("--java-version", type=int, default=17)
    parser.add_argument("--after-agent-commands", default="[]")
    args = parser.parse_args()

    task_id = args.task_id
    before_commit = args.before_commit
    build_commands = json.loads(args.build_commands)
    unit_test_commands = json.loads(args.unit_test_commands)
    android_test_commands = json.loads(args.android_test_commands)
    target_sdk = args.target_sdk
    java_version = args.java_version
    after_agent_commands = json.loads(args.after_agent_commands)

    solution_script_path = Path(args.solution_script)
    test_patch_path = Path("/dataset_root") / args.test_patch
    results_output_path = Path("/dataset_root") / args.results_output

    print(f"Starting test verification for task: {task_id}")
    repo_dir = find_repo_dir()
    print(f"Found repository directory at: {repo_dir}")

    # Set JAVA_HOME and update local properties dynamically
    java_home = f"/usr/lib/jvm/java-{java_version}-openjdk-amd64"
    print(f"Instance {task_id}: Setting JAVA_HOME to: {java_home}")
    update_local_properties(repo_dir, java_home)

    # Run repository-specific startup script if present
    repo_name = Path(repo_dir).name
    startup_script = Path(f"/workspace/launch_scripts/{repo_name}.sh")
    if startup_script.is_file():
        print(f"Running task-specific startup script: {startup_script}")
        try:
            res = subprocess.run(
                ["bash", str(startup_script)],
                cwd=repo_dir,
                capture_output=True,
                text=True,
            )
            print(res.stdout)
            print(res.stderr)
            if res.returncode != 0:
                print(f"WARNING: Startup script failed with exit code {res.returncode}")
        except Exception as e:
            print(f"Failed to execute startup script: {e}")

    # Start emulator if there are android tests
    emulator_proc = None
    if android_test_commands:
        emulator_name = args.emulator_name
        log_file = "/tmp/emulator_log.txt"
        print(f"Starting emulator {emulator_name}...")
        emulator_proc = start_and_wait_for_emulator(
            log_file, emulator_name, timeout_seconds=600
        )

    try:
        # ----------------------------------------------------
        # STATE 1: Without Golden Patch, with Test Patch
        # ----------------------------------------------------
        print("\n=== Running State 1: Without Golden Patch, With Test Patch ===")
        state1_passed = set()
        state1_failed = set()
        state1_build_success = True
        state1_apply_success = True
        renamed_files_state1 = []

        try:
            run_cmd(["git", "reset", "--hard"], cwd=repo_dir)
            run_cmd(["git", "clean", "-fd"], cwd=repo_dir)
            run_cmd(["git", "checkout", "-f", before_commit], cwd=repo_dir)
            clean_test_results(repo_dir)

            if test_patch_path.is_file():
                print(f"Applying test patch: {test_patch_path}")
                res = subprocess.run(
                    ["git", "apply", "--3way", str(test_patch_path)],
                    cwd=repo_dir,
                    capture_output=True,
                    text=True,
                )
                print(res.stdout)
                print(res.stderr)
                if res.returncode != 0:
                    print("WARNING: Applying test patch failed without golden patch.")
                    state1_apply_success = False
            else:
                print("No test patch file found/provided.")

            # 1. Run build commands through compilation feedback loop
            for orig_build_cmd in build_commands:
                for build_cmd in split_gradle_command(orig_build_cmd):
                    t_name = (
                        "connectedDebugAndroidTest"
                        if "android" in build_cmd.lower()
                        else "testDebugUnitTest"
                    )
                    ok, stdout, stderr = run_command_with_feedback(
                        repo_dir, build_cmd, t_name, state1_failed, renamed_files_state1
                    )
                    if not ok:
                        state1_build_success = False
                    print(stdout)
                    print(stderr)

            # 2. Run unit tests sequentially and independently through feedback loop
            for orig_test_cmd in unit_test_commands:
                for test_cmd in split_gradle_command(orig_test_cmd):
                    t_name = get_task_name_from_command(test_cmd)
                    _, stdout, stderr = run_command_with_feedback(
                        repo_dir, test_cmd, t_name, state1_failed, renamed_files_state1
                    )
                    print(stdout)
                    print(stderr)
                    cmd_passed_runs = []
                    for run_idx in range(3):
                        print(
                            f"Running unit test run {run_idx+1}/3 for command: {test_cmd}"
                        )
                        res = run_tests(
                            project_dir=repo_dir,
                            run_tests_command=test_cmd,
                            timeout=1800,
                            workers=8,
                            mount_path="/dataset_root",
                            remove_task_names=False,
                        )
                        cmd_passed_runs.append(res.passed_tests)
                        state1_failed.update(res.failed_tests)
                    if cmd_passed_runs:
                        cmd_passed_all = set.intersection(*cmd_passed_runs)
                        state1_passed.update(cmd_passed_all)

            # 3. Run android tests sequentially and independently through feedback loop
            for orig_test_cmd in android_test_commands:
                for test_cmd in split_gradle_command(orig_test_cmd):
                    t_name = get_task_name_from_command(test_cmd)
                    _, stdout, stderr = run_command_with_feedback(
                        repo_dir, test_cmd, t_name, state1_failed, renamed_files_state1
                    )
                    print(stdout)
                    print(stderr)
                    cmd_passed_runs = []
                    for run_idx in range(3):
                        print(
                            f"Running android test run {run_idx+1}/3 for command: {test_cmd}"
                        )
                        res = run_tests(
                            project_dir=repo_dir,
                            run_tests_command=test_cmd,
                            timeout=1800,
                            workers=8,
                            mount_path="/dataset_root",
                            remove_task_names=False,
                        )
                        cmd_passed_runs.append(res.passed_tests)
                        state1_failed.update(res.failed_tests)
                    if cmd_passed_runs:
                        cmd_passed_all = set.intersection(*cmd_passed_runs)
                        state1_passed.update(cmd_passed_all)
        finally:
            # Restore files renamed during State 1 so State 2 starts clean
            print("\nRestoring backup files from State 1 feedback loop...")
            for orig, bak in reversed(renamed_files_state1):
                if bak.is_file():
                    try:
                        bak.rename(orig)
                    except Exception as e:
                        print(f"Failed to restore {bak} to {orig}: {e}")

        # ----------------------------------------------------
        # STATE 2: With Golden Patch, with Test Patch
        # ----------------------------------------------------
        print("\n=== Running State 2: With Golden Patch, With Test Patch ===")
        state2_passed = set()
        state2_failed = set()
        state2_build_success = True
        state2_apply_success = True
        renamed_files_state2 = []

        try:
            run_cmd(["git", "reset", "--hard"], cwd=repo_dir)
            run_cmd(["git", "clean", "-fd"], cwd=repo_dir)
            run_cmd(["git", "checkout", "-f", before_commit], cwd=repo_dir)
            clean_test_results(repo_dir)

            if solution_script_path.is_file():
                print(f"Executing solution script: {solution_script_path}")
                try:
                    os.chmod(solution_script_path, 0o755)
                except Exception as e:
                    print(f"Warning: Failed to chmod {solution_script_path}: {e}")
                res = subprocess.run(
                    ["bash", str(solution_script_path)],
                    cwd=repo_dir,
                    capture_output=True,
                    text=True,
                )
                print(res.stdout)
                print(res.stderr)
                if res.returncode != 0:
                    print("ERROR: Executing solution script failed!")
                    state2_apply_success = False
            else:
                print(f"ERROR: Solution script {solution_script_path} not found!")
                state2_apply_success = False

            if state2_apply_success and test_patch_path.is_file():
                print(f"Applying test patch: {test_patch_path}")
                res = subprocess.run(
                    ["git", "apply", str(test_patch_path)],
                    cwd=repo_dir,
                    capture_output=True,
                    text=True,
                )
                print(res.stdout)
                print(res.stderr)
                if res.returncode != 0:
                    print("ERROR: Applying test patch failed with golden patch!")
                    state2_apply_success = False

            # Run after-agent commands if solution and test patches were applied successfully
            if state2_apply_success and after_agent_commands:
                print(f"Executing after_agent commands: {after_agent_commands}")
                for cmd in after_agent_commands:
                    print(f"Executing after_agent command: {cmd}")
                    res = subprocess.run(
                        cmd,
                        shell=True,
                        cwd=repo_dir,
                        capture_output=True,
                        text=True,
                    )
                    print(res.stdout)
                    print(res.stderr)
                    if res.returncode != 0:
                        print(f"ERROR: after_agent command failed: {cmd}")
                        state2_apply_success = False
                        break

            # 1. Run build commands through feedback loop
            for orig_build_cmd in build_commands:
                for build_cmd in split_gradle_command(orig_build_cmd):
                    t_name = (
                        "connectedDebugAndroidTest"
                        if "android" in build_cmd.lower()
                        else "testDebugUnitTest"
                    )
                    ok, stdout, stderr = run_command_with_feedback(
                        repo_dir, build_cmd, t_name, state2_failed, renamed_files_state2
                    )
                    if not ok:
                        state2_build_success = False
                    print(stdout)
                    print(stderr)

            # 2. Run unit tests sequentially and independently through feedback loop
            for orig_test_cmd in unit_test_commands:
                for test_cmd in split_gradle_command(orig_test_cmd):
                    t_name = get_task_name_from_command(test_cmd)
                    _, stdout, stderr = run_command_with_feedback(
                        repo_dir, test_cmd, t_name, state2_failed, renamed_files_state2
                    )
                    cmd_passed_runs = []
                    for run_idx in range(3):
                        print(
                            f"Running unit test run {run_idx+1}/3 for command: {test_cmd}"
                        )
                        res = run_tests(
                            project_dir=repo_dir,
                            run_tests_command=test_cmd,
                            timeout=1800,
                            workers=8,
                            mount_path="/dataset_root",
                            remove_task_names=False,
                        )
                        cmd_passed_runs.append(res.passed_tests)
                        state2_failed.update(res.failed_tests)
                    if cmd_passed_runs:
                        cmd_passed_all = set.intersection(*cmd_passed_runs)
                        state2_passed.update(cmd_passed_all)

            # 3. Run android tests sequentially and independently through feedback loop
            for orig_test_cmd in android_test_commands:
                for test_cmd in split_gradle_command(orig_test_cmd):
                    t_name = get_task_name_from_command(test_cmd)
                    _, stdout, stderr = run_command_with_feedback(
                        repo_dir, test_cmd, t_name, state2_failed, renamed_files_state2
                    )
                    cmd_passed_runs = []
                    for run_idx in range(3):
                        print(
                            f"Running android test run {run_idx+1}/3 for command: {test_cmd}"
                        )
                        res = run_tests(
                            project_dir=repo_dir,
                            run_tests_command=test_cmd,
                            timeout=1800,
                            workers=8,
                            mount_path="/dataset_root",
                            remove_task_names=False,
                        )
                        cmd_passed_runs.append(res.passed_tests)
                        state2_failed.update(res.failed_tests)
                    if cmd_passed_runs:
                        cmd_passed_all = set.intersection(*cmd_passed_runs)
                        state2_passed.update(cmd_passed_all)
        finally:
            # Restore files renamed during State 2
            print("\nRestoring backup files from State 2 feedback loop...")
            for orig, bak in reversed(renamed_files_state2):
                if bak.is_file():
                    try:
                        bak.rename(orig)
                    except Exception as e:
                        print(f"Failed to restore {bak} to {orig}: {e}")

        # ----------------------------------------------------
        # STRICT MUTUALLY EXCLUSIVE CLASSIFICATION
        # ----------------------------------------------------
        clean_state1_failed = state1_failed
        clean_state2_failed = state2_failed

        # Discover excluded tests by parsing files containing @ExcludeFromDataset in repo_dir
        excluded_test_methods = set()
        if repo_dir and Path(repo_dir).is_dir():
            for root, dirs, files in os.walk(repo_dir):
                for f in files:
                    if f.endswith((".kt", ".java")):
                        f_path = Path(root) / f
                        try:
                            fc = f_path.read_text(encoding="utf-8", errors="ignore")
                            if is_task_excluded_by_annotation(fc, task_id):
                                for t_task in (
                                    "connectedDebugAndroidTest",
                                    "testDebugUnitTest",
                                ):
                                    extracted = extract_tests_from_file(f_path, t_task)
                                    excluded_test_methods.update(extracted)
                        except Exception:
                            pass

        if excluded_test_methods:
            print(
                f"Filtering out {len(excluded_test_methods)} @ExcludeFromDataset test method(s) from classification."
            )

        # Collect the universe of all test names discovered across both states
        all_tests = (
            state1_passed | state1_failed | state2_passed | state2_failed
        ) - excluded_test_methods

        p2p = set()
        f2p = set()
        breaking = set()
        flaky = set()

        for test_name in all_tests:
            s1_pass = (test_name in state1_passed) and (
                test_name not in clean_state1_failed
            )
            s2_pass = (test_name in state2_passed) and (
                test_name not in clean_state2_failed
            )

            if s1_pass and s2_pass:
                p2p.add(test_name)
            elif not s1_pass and s2_pass:
                f2p.add(test_name)
            elif s1_pass and not s2_pass:
                breaking.add(test_name)
            else:
                flaky.add(test_name)

        results = {
            "fail_to_pass": sorted(list(f2p)),
            "pass_to_pass": sorted(list(p2p)),
            "flaky": sorted(list(flaky)),
            "breaking": sorted(list(breaking)),
            "state1": {
                "apply_success": state1_apply_success,
                "build_success": state1_build_success,
                "passed": sorted(list(state1_passed)),
                "failed": sorted(list(state1_failed)),
            },
            "state2": {
                "apply_success": state2_apply_success,
                "build_success": state2_build_success,
                "passed": sorted(list(state2_passed)),
                "failed": sorted(list(state2_failed)),
            },
        }

        results_output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(results_output_path, "w") as f:
            json.dump(results, f, indent=2)

        print("\nVerification Complete. Results written successfully.")

    finally:
        if emulator_proc:
            print("Stopping emulator...")
            emulator_proc.terminate()
            try:
                emulator_proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                emulator_proc.kill()


if __name__ == "__main__":
    main()
