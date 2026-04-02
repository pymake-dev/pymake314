from __future__ import annotations
"""
Tests for double-colon (::) rule support.

GNU Make semantics being tested:
  - Each :: block for the same target is independent
  - Each block has its own up-to-date check
  - A :: block with no prerequisites always runs
  - :: targets can have multiple blocks with different prereqs and recipes
  - Mixing : and :: for the same target is an error
  - :: pattern rules work the same way
  - Default target detection works with :: rules
"""

import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from pymake.parser import parse
from pymake.resolver import resolve
from pymake.executor import execute

PYMAKE = [sys.executable, "-m", "pymake"]
PASS: list[str] = []


# ─── helpers ──────────────────────────────────────────────────────────────────

def mk(content: str, d: str, name: str = "Makefile") -> str:
    path = Path(d) / name
    path.write_text(textwrap.dedent(content))
    return str(path)


def run_pymake(*args, cwd=None) -> tuple[int, str]:
    result = subprocess.run(
        PYMAKE + list(args),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        cwd=cwd,
    )
    return result.returncode, result.stdout


def assert_out(out: str, *frags):
    for f in frags:
        assert f in out, f"Expected {f!r} in:\n{out}"


def assert_not_out(out: str, *frags):
    for f in frags:
        assert f not in out, f"Did NOT expect {f!r} in:\n{out}"


def passed(name: str):
    PASS.append(name)
    print(f"PASS {name}")


# ═══════════════════════════════════════════════════════════════════════════════
# Parser tests — model is built correctly
# ═══════════════════════════════════════════════════════════════════════════════

def test_parse_double_colon_stored_separately():
    """:: rules land in double_colon_rules, not rules."""
    with tempfile.TemporaryDirectory() as d:
        mf = parse(mk("""
            all:: foo
            \techo block1
            all:: bar
            \techo block2
        """, d))
    assert "all" not in mf.rules, ":: target should not be in single-colon rules"
    assert "all" in mf.double_colon_rules
    blocks = mf.double_colon_rules["all"]
    assert len(blocks) == 2
    assert blocks[0].prerequisites == ["foo"]
    assert blocks[0].recipe == ["echo block1"]
    assert blocks[1].prerequisites == ["bar"]
    assert blocks[1].recipe == ["echo block2"]
    passed("parse/double_colon_stored_separately")


def test_parse_single_colon_unaffected():
    """Single-colon rules are unaffected when :: rules exist for other targets."""
    with tempfile.TemporaryDirectory() as d:
        mf = parse(mk("""
            .PHONY: single
            single:
            \techo single
            multi:: dep
            \techo multi
        """, d))
    assert "single" in mf.rules
    assert "multi" in mf.double_colon_rules
    passed("parse/single_colon_unaffected")


def test_parse_double_colon_is_double_colon_flag():
    """is_double_colon flag is set on :: rules."""
    with tempfile.TemporaryDirectory() as d:
        mf = parse(mk("""
            target::
            \techo hi
        """, d))
    rule = mf.double_colon_rules["target"][0]
    assert rule.is_double_colon is True
    passed("parse/is_double_colon_flag")


def test_parse_mixed_colon_error():
    """Mixing : and :: for the same target raises ValueError."""
    with tempfile.TemporaryDirectory() as d:
        try:
            parse(mk("""
                target: dep
                \techo single
                target:: dep
                \techo double
            """, d))
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "single-colon and double-colon" in str(e)
    passed("parse/mixed_colon_raises_error")


def test_parse_double_colon_default_target():
    """First :: target becomes the default target."""
    with tempfile.TemporaryDirectory() as d:
        mf = parse(mk("""
            first::
            \techo first
            second::
            \techo second
        """, d))
    assert mf.default_target == "first"
    passed("parse/double_colon_default_target")


# ═══════════════════════════════════════════════════════════════════════════════
# Execution behaviour
# ═══════════════════════════════════════════════════════════════════════════════

def test_exec_both_blocks_run():
    """Both :: blocks run when target is out of date (no file exists)."""
    rc, out = run_pymake("-f", mk("""
        .PHONY: all
        all:: 
        \techo block1
        all::
        \techo block2
    """, tempfile.mkdtemp()), "all")
    assert rc == 0
    assert_out(out, "block1", "block2")
    passed("exec/both_blocks_run")


def test_exec_no_prereqs_always_runs():
    """A :: block with no prerequisites always runs its recipe."""
    with tempfile.TemporaryDirectory() as d:
        # Create the target file — it should still run because no prereqs
        Path(d, "target.txt").write_text("exists")
        rc, out = run_pymake("-f", mk("""
            target.txt::
            \techo always runs
        """, d), "target.txt")
        assert rc == 0
        assert_out(out, "always runs")
    passed("exec/no_prereqs_always_runs")


def test_exec_stale_block_runs_fresh_skipped():
    """Only the :: block whose prereqs are newer than the target runs."""
    with tempfile.TemporaryDirectory() as d:
        target = Path(d, "out.txt")
        stale_dep = Path(d, "stale.dep")
        fresh_dep = Path(d, "fresh.dep")

        # Write target first so it's the oldest
        target.write_text("old")
        time.sleep(0.05)
        fresh_dep.write_text("new")   # newer than target → block2 should run
        stale_dep.write_text("old")   # same age; target is newer → block1 skips

        # Make target newer than stale_dep but older than fresh_dep
        # Re-touch stale_dep to be older than target
        stale_time = target.stat().st_mtime - 1
        os.utime(stale_dep, (stale_time, stale_time))

        rc, out = run_pymake("-f", mk("""
            out.txt:: stale.dep
            \techo block1 ran
            out.txt:: fresh.dep
            \techo block2 ran
        """, d), "out.txt")
        assert rc == 0
        assert_not_out(out, "block1 ran")
        assert_out(out, "block2 ran")
    passed("exec/stale_block_runs_fresh_skipped")


def test_exec_independent_recipes():
    """:: blocks run their own recipes — they are truly independent."""
    with tempfile.TemporaryDirectory() as d:
        sentinel = Path(d, "log.txt")
        rc, out = run_pymake("-f", mk(f"""
            .PHONY: build
            build::
            \techo A >> {sentinel}
            build::
            \techo B >> {sentinel}
            build::
            \techo C >> {sentinel}
        """, d), "build")
        assert rc == 0
        lines = sentinel.read_text().strip().splitlines()
        assert lines == ["A", "B", "C"]
    passed("exec/independent_recipes_run_in_order")


def test_exec_block_error_stops_remaining():
    """An error in a :: block stops execution (without -k)."""
    rc, out = run_pymake("-f", mk("""
        .PHONY: all
        all::
        \tfalse
        all::
        \techo should not run
    """, tempfile.mkdtemp()), "all")
    assert rc != 0
    assert_not_out(out, "should not run")
    passed("exec/block_error_stops_remaining")


def test_exec_keep_going_runs_all_blocks():
    """-k continues to remaining :: blocks after a failure."""
    rc, out = run_pymake("-f", mk("""
        .PHONY: all
        all::
        \tfalse
        all::
        \techo second block ran
    """, tempfile.mkdtemp()), "-k", "all")
    assert_out(out, "second block ran")
    passed("exec/keep_going_runs_all_blocks")


def test_exec_dry_run_shows_all_blocks():
    """-n shows commands from all :: blocks without running them."""
    with tempfile.TemporaryDirectory() as d:
        sentinel = Path(d, "created.txt")
        rc, out = run_pymake("-f", mk(f"""
            .PHONY: build
            build::
            \techo block1
            build::
            \ttouch {sentinel}
        """, d), "-n", "build")
        assert rc == 0
        assert_out(out, "echo block1", f"touch {sentinel}")
        assert not sentinel.exists(), "Dry run must not create files"
    passed("exec/dry_run_shows_all_blocks")


def test_exec_automatic_vars_per_block():
    """$< and $^ are correct for each :: block's own prerequisites."""
    with tempfile.TemporaryDirectory() as d:
        Path(d, "a.txt").write_text("")
        Path(d, "b.txt").write_text("")
        rc, out = run_pymake("-f", mk("""
            out:: a.txt
            \techo first=$<
            out:: b.txt
            \techo first=$<
        """, d), "out")
        assert rc == 0
        lines = [l for l in out.splitlines() if l.startswith("first=")]
        assert lines[0] == "first=a.txt"
        assert lines[1] == "first=b.txt"
    passed("exec/automatic_vars_per_block")


# ═══════════════════════════════════════════════════════════════════════════════
# Interaction with single-colon rules and other features
# ═══════════════════════════════════════════════════════════════════════════════

def test_exec_single_and_double_colon_different_targets():
    """Single-colon and double-colon rules for *different* targets coexist."""
    rc, out = run_pymake("-f", mk("""
        .PHONY: all single
        all: single double
        single:
        \techo single ran
        double::
        \techo double ran
    """, tempfile.mkdtemp()), "all")
    assert rc == 0
    assert_out(out, "single ran", "double ran")
    passed("exec/single_and_double_colon_different_targets")


def test_exec_double_colon_with_phony_prereq():
    """A :: block can depend on a .PHONY target."""
    rc, out = run_pymake("-f", mk("""
        .PHONY: dep
        dep:
        \techo dep ran
        target::
        \techo block ran
        target:: dep
        \techo block2 ran
    """, tempfile.mkdtemp()), "target")
    assert rc == 0
    assert_out(out, "dep ran", "block ran", "block2 ran")
    passed("exec/double_colon_with_phony_prereq")


def test_exec_double_colon_silent_prefix():
    """@ prefix works in :: recipe lines."""
    rc, out = run_pymake("-f", mk("""
        .PHONY: all
        all::
        \t@echo silent
        all::
        \techo loud
    """, tempfile.mkdtemp()), "all")
    assert_out(out, "silent", "echo loud", "loud")
    assert_not_out(out, "@echo silent")
    passed("exec/double_colon_silent_prefix")


def test_exec_double_colon_variable_expansion():
    """Variables expand correctly in :: recipes."""
    rc, out = run_pymake("-f", mk("""
        .PHONY: all
        MSG = hello
        all::
        \techo $(MSG) block1
        all::
        \techo $(MSG) block2
    """, tempfile.mkdtemp()), "all")
    assert_out(out, "hello block1", "hello block2")
    passed("exec/double_colon_variable_expansion")


# ═══════════════════════════════════════════════════════════════════════════════
# Runner
# ═══════════════════════════════════════════════════════════════════════════════

ALL_TESTS = [
    test_parse_double_colon_stored_separately,
    test_parse_single_colon_unaffected,
    test_parse_double_colon_is_double_colon_flag,
    test_parse_mixed_colon_error,
    test_parse_double_colon_default_target,
    test_exec_both_blocks_run,
    test_exec_no_prereqs_always_runs,
    test_exec_stale_block_runs_fresh_skipped,
    test_exec_independent_recipes,
    test_exec_block_error_stops_remaining,
    test_exec_keep_going_runs_all_blocks,
    test_exec_dry_run_shows_all_blocks,
    test_exec_automatic_vars_per_block,
    test_exec_single_and_double_colon_different_targets,
    test_exec_double_colon_with_phony_prereq,
    test_exec_double_colon_silent_prefix,
    test_exec_double_colon_variable_expansion,
]

if __name__ == "__main__":
    failed = []
    for fn in ALL_TESTS:
        try:
            fn()
        except Exception as e:
            import traceback
            print(f"FAIL {fn.__name__}: {e}")
            traceback.print_exc()
            failed.append(fn.__name__)

    print(f"\n{'='*60}")
    print(f"Double-colon tests: {len(PASS)} passed, {len(failed)} failed")
    if failed:
        print("Failed:")
        for n in failed:
            print(f"  {n}")
        sys.exit(1)
    else:
        print("All passed!")
