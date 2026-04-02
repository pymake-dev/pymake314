from __future__ import annotations
"""
pymake — a pure-Python Makefile interpreter.

Public API
----------
    from pymake import run

    run("all")
    run("test", makefile="GNUmakefile")
    run("build", variables={"CC": "clang"})

CLI
---
    pymake [options] [target ...]

    -f FILE, --file FILE       Makefile to read (default: Makefile)
    -C DIR, --directory DIR    Change to DIR before doing anything
    -n, --dry-run              Print commands without running them
    -s, --silent               Don't echo commands
    -i, --ignore-errors        Continue despite errors
    -k, --keep-going           Continue after first error
    -j N, --jobs N             (reserved; sequential only for now)
    -p, --print-data-base      Print the parsed variable/rule database
    -e, --environment          Variables from env override Makefile assignments
    VAR=VALUE                  Override a variable
"""
import argparse
import os
import sys
from contextlib import contextmanager
from pathlib import Path

from .parser import parse
from .resolver import resolve
from .executor import execute
from .model import Makefile


@contextmanager
def _chdir(path):
    """Context manager: temporarily change cwd to `path`, restore on exit."""
    old = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(old)


def run(
    *targets: str,
    makefile: str | Path = "Makefile",
    variables: dict[str, str] | None = None,
    dry_run: bool = False,
    silent: bool = False,
    ignore_errors: bool = False,
    keep_going: bool = False,
) -> int:
    """
    Parse `makefile` and build `targets`.

    Returns 0 on success, non-zero on failure.
    Raises FileNotFoundError if the Makefile is missing.
    Raises RuntimeError on dependency cycles or missing targets.

    Automatically chdirs to the Makefile's directory so that relative
    paths in prerequisites and recipes resolve correctly.
    """
    makefile = Path(makefile).resolve()
    makefile_dir = makefile.parent

    with _chdir(makefile_dir):
        mf = parse(makefile, variables)

        if not targets:
            if mf.default_target is None:
                raise RuntimeError("No targets specified and no default target found.")
            targets = (mf.default_target,)

        for target in targets:
            order = resolve(target, mf)
            rc = execute(
                order, mf,
                dry_run=dry_run,
                silent=silent,
                ignore_errors=ignore_errors,
                keep_going=keep_going,
            )
            if rc != 0 and not keep_going:
                return rc

    return 0


def _print_database(mf: Makefile) -> None:
    sys.stdout.write("# Variables\n")
    for k, v in sorted(mf.variables.items()):
        if k not in os.environ:
            sys.stdout.write(f"  {k} = {v}\n")
    sys.stdout.write("\n# Rules\n")
    for target, rule in sorted(mf.rules.items()):
        phony_tag = " [phony]" if rule.is_phony else ""
        prereqs = " ".join(rule.prerequisites) if rule.prerequisites else ""
        sys.stdout.write(f"  {target}: {prereqs}{phony_tag}\n")
        for cmd in rule.recipe:
            sys.stdout.write(f"    {cmd}\n")
    sys.stdout.flush()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="pymake",
        description="A pure-Python Makefile interpreter.",
    )
    parser.add_argument(
        "targets_and_vars",
        nargs="*",
        metavar="[target|VAR=value]",
        help="Targets to build and/or variable overrides (VAR=VALUE)",
    )
    parser.add_argument("-f", "--file", default="Makefile", metavar="FILE",
                        help="Makefile to read (default: Makefile)")
    parser.add_argument("-C", "--directory", default=None, metavar="DIR",
                        help="Change to DIR before doing anything")
    parser.add_argument("-n", "--dry-run", action="store_true",
                        help="Print commands without running them")
    parser.add_argument("-s", "--silent", action="store_true",
                        help="Don't echo commands")
    parser.add_argument("-i", "--ignore-errors", action="store_true",
                        help="Continue despite recipe errors")
    parser.add_argument("-k", "--keep-going", action="store_true",
                        help="Continue building other targets after failure")
    parser.add_argument("-j", "--jobs", type=int, default=1, metavar="N",
                        help="Number of parallel jobs (reserved; currently sequential)")
    parser.add_argument("-p", "--print-data-base", action="store_true",
                        help="Print parsed variables and rules, then exit")
    parser.add_argument("-e", "--environment", action="store_true",
                        help="Environment variables override Makefile variables")

    args = parser.parse_args(argv)

    # -C: change directory first, before anything else
    if args.directory:
        try:
            os.chdir(args.directory)
        except OSError as e:
            sys.stderr.write(f"pymake: {e}\n")
            return 2

    # Split targets from VAR=VALUE overrides
    targets: list[str] = []
    overrides: dict[str, str] = {}
    for item in args.targets_and_vars:
        if "=" in item and not item.startswith("-"):
            k, _, v = item.partition("=")
            overrides[k] = v
        else:
            targets.append(item)

    # Resolve the Makefile path, then chdir to its directory so all
    # relative paths in prereqs and recipes work correctly.
    makefile_path = Path(args.file).resolve()

    try:
        with _chdir(makefile_path.parent):
            mf = parse(makefile_path, overrides if not args.environment else None)
            if args.environment:
                mf.variables.update(os.environ)
            # Command-line VAR=VALUE always wins (applied after parsing)
            mf.variables.update(overrides)

            if args.print_data_base:
                _print_database(mf)
                return 0

            if not targets:
                if mf.default_target is None:
                    sys.stderr.write("pymake: No targets and no default target.\n")
                    return 2
                targets = [mf.default_target]

            overall_rc = 0
            for target in targets:
                try:
                    order = resolve(target, mf)
                except RuntimeError as e:
                    sys.stderr.write(f"pymake: *** {e}\n")
                    return 2

                rc = execute(
                    order, mf,
                    dry_run=args.dry_run,
                    silent=args.silent,
                    ignore_errors=args.ignore_errors,
                    keep_going=args.keep_going,
                )
                if rc != 0:
                    overall_rc = rc
                    if not args.keep_going:
                        return overall_rc

    except FileNotFoundError as e:
        sys.stderr.write(f"pymake: {e}\n")
        return 2

    return overall_rc


if __name__ == "__main__":
    sys.exit(main())
