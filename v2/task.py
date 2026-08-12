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
Central Dispatcher for Dataset V2 Curation Suite (v2.task).
Routes subcommands: create, docker, refresh-patches, verify-tests.
"""

import argparse
from pathlib import Path
import sys
from rich import print as rprint
from rich.panel import Panel
from v2.task_commands import (
    create,
    docker,
    refresh_patches,
    verify_tests,
)
from v2.task_commands.common import add_common_task_args


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Dataset V2 Task Management & Curation Suite.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    subparsers = parser.add_subparsers(
        title="subcommands", dest="subcommand", help="Available suite operations"
    )

    # 1. create
    create_parser = subparsers.add_parser(
        "create",
        help="Interactive TUI to scaffold fresh V2 evaluation tasks",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    create.add_arguments(create_parser)

    # 2. docker
    docker_parser = subparsers.add_parser(
        "docker",
        help="Build, generate Dockerfiles, and publish container layers",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    docker.add_arguments(docker_parser)

    # 3. refresh-patches
    refresh_parser = subparsers.add_parser(
        "refresh-patches",
        help="Calculate pristine solution.patch and test.patch goldens",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    add_common_task_args(refresh_parser)

    # 4. verify-tests
    verify_parser = subparsers.add_parser(
        "verify-tests",
        help="Determine F2P and P2P sets for tasks inside Docker containers",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    verify_tests.add_arguments(verify_parser)

    if len(sys.argv) == 1:
        menu = (
            "[bold cyan]Dataset V2 Curation Suite (v2.task)[/]\n\n"
            "• [bold green]create[/]          : Interactive TUI to scaffold fresh V2 evaluation tasks\n"
            "• [bold green]docker[/]          : Build, generate Dockerfiles, and publish container layers\n"
            "• [bold green]refresh-patches[/] : Calculate pristine solution.patch and test.patch goldens\n"
            "• [bold green]verify-tests[/]    : Determine F2P and P2P sets for tasks inside Docker containers\n\n"
            "Run `v2.task <subcommand> --help` for specific usage."
        )
        rprint(
            Panel(
                menu,
                title="[bold yellow]Available Operations[/]",
                border_style="yellow",
            )
        )
        sys.exit(0)

    args = parser.parse_args()

    if args.subcommand == "create":
        create.main(args)
    elif args.subcommand == "docker":
        docker.main_with_args(args)
    elif args.subcommand == "refresh-patches":
        refresh_patches.main(args)
    elif args.subcommand == "verify-tests":
        verify_tests.main(args)


if __name__ == "__main__":
    main()
