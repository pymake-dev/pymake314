from __future__ import annotations
"""
Execute the build for an ordered list of targets.

Recipe prefix handling:
  @cmd   — silent (don't echo)
  -cmd   — ignore errors
  @-cmd  — silent + ignore errors
  +cmd   — always run (even with --dry-run) — we just run it

Double-colon rules:
  Each :: block for the same target is checked and run independently.
  A :: block with no prerequisites always runs its recipe.
"""
import os
import subprocess
import sys
from pathlib import Path
from .model import Makefile, Rule
from .parser import expand_variables
from .resolver import _find_rule, _find_double_colon_rules


def _abspath(p: str, basedir: str) -> Path:
    """Resolve p relative to basedir if not absolute."""
    path = Path(p)
    if path.is_absolute():
        return path
    return (Path(basedir) / path).resolve()


def _is_up_to_date(rule: Rule, mf: Makefile) -> bool:
    """
    Return True if the target file is newer than all prerequisite files.
    - Phony targets are never up to date.
    - A :: rule with no prerequisites always runs (never up to date).
    - Missing targets always rebuild.
    """
    if rule.is_phony:
        return False
    # :: rule with no prereqs — always runs
    if rule.is_double_colon and not rule.prerequisites:
        return False
    target_path = _abspath(rule.target, mf.basedir)
    if not target_path.exists():
        return False
    target_mtime = target_path.stat().st_mtime
    # Only normal prerequisites affect the timestamp check;
    # order-only prerequisites do not.
    for prereq in rule.prerequisites:
        p = _abspath(prereq, mf.basedir)
        if p.exists() and p.stat().st_mtime > target_mtime:
            return False
    return True


def _run_command(cmd: str, env: dict, dry_run: bool, silent: bool, ignore_error: bool) -> int:
    silent_flag = cmd.startswith('@') or silent
    ignore_flag = cmd.startswith('-') or ignore_error
    bare = cmd
    while bare and bare[0] in ('@', '-', '+'):
        bare = bare[1:]

    if not silent_flag:
        print(bare)

    if dry_run:
        return 0

    result = subprocess.run(bare, shell=True, env=env)
    if result.returncode != 0 and not ignore_flag:
        return result.returncode
    return 0


def _run_rule(rule: Rule, mf: Makefile, env: dict,
              dry_run: bool, silent: bool, ignore_errors: bool, keep_going: bool) -> int:
    """Run a single rule's recipe. Returns 0 on success."""
    if _is_up_to_date(rule, mf):
        print(f"pymake: '{rule.target}' is up to date.")
        return 0

    if not rule.recipe:
        return 0

    auto: dict[str, str] = {
        '@': rule.target,
        '<': rule.prerequisites[0] if rule.prerequisites else '',
        '^': ' '.join(rule.prerequisites),            # normal prereqs only
        '|': ' '.join(rule.order_only_prerequisites), # order-only prereqs
        '*': os.path.splitext(rule.target)[0],
        '(@D)': str(Path(rule.target).parent),
        '(@F)': Path(rule.target).name,
    }

    for cmd_raw in rule.recipe:
        cmd = expand_variables(cmd_raw, mf.variables, auto)
        rc = _run_command(cmd, env, dry_run, silent, ignore_errors)
        if rc != 0:
            print(f"pymake: *** [{rule.target}] Error {rc}", file=sys.stderr)
            if not keep_going:
                return rc
            break
    return 0


def _safe_getcwd() -> str | None:
    try:
        return os.getcwd()
    except OSError:
        return None


def execute(
    targets: list[str],
    mf: Makefile,
    *,
    dry_run: bool = False,
    silent: bool = False,
    ignore_errors: bool = False,
    keep_going: bool = False,
    jobs: int = 1,
) -> int:
    """
    Run the recipes for each target in `targets` (already topo-sorted).
    Returns 0 on success, non-zero on first failure (unless keep_going).

    For double-colon targets every :: block is checked and run independently.
    """
    env = {**os.environ, **{k: v for k, v in mf.variables.items()
                             if not k.startswith('.')}}
    failed = 0
    original_cwd = _safe_getcwd()

    try:
        if os.path.isdir(mf.basedir):
            os.chdir(mf.basedir)

        for target in targets:
            dc_rules = _find_double_colon_rules(target, mf)

            if dc_rules:
                # Run each :: block independently
                for rule in dc_rules:
                    rc = _run_rule(rule, mf, env, dry_run, silent, ignore_errors, keep_going)
                    if rc != 0:
                        failed = rc
                        if not keep_going:
                            return failed
            else:
                rule = _find_rule(target, mf)
                if rule is None:
                    continue
                rc = _run_rule(rule, mf, env, dry_run, silent, ignore_errors, keep_going)
                if rc != 0:
                    failed = rc
                    if not keep_going:
                        return failed

    finally:
        if original_cwd and os.path.isdir(original_cwd):
            try:
                os.chdir(original_cwd)
            except OSError:
                pass

    return failed
