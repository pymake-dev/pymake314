from __future__ import annotations
"""
Tests for order-only prerequisites support.

Syntax:
    target: normal-prereqs | order-only-prereqs

GNU Make semantics tested:
  - Order-only prereqs are built before the target (ordering)
  - Order-only prereqs do NOT trigger a rebuild based on mtime
  - Normal prereqs still trigger a rebuild when newer than target
  - $^ contains only normal prereqs; $| contains order-only prereqs
  - Order-only prereqs work with double-colon rules
  - Order-only prereqs work with static pattern rules
  - Order-only-only rules (no normal prereqs): target: | oo
  - Multiple rule lines can accumulate order-only prereqs
  - -n dry run still resolves order-only prereqs
"""

import os
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from pymake.parser import parse

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
# Parser / model tests
# ═══════════════════════════════════════════════════════════════════════════════

def test_parse_splits_on_pipe():
    """| splits prereqs into normal and order-only lists."""
    with tempfile.TemporaryDirectory() as d:
        mf = parse(mk("""
            output: src.c | builddir
            \techo build
        """, d))
    rule = mf.rules["output"]
    assert rule.prerequisites == ["src.c"]
    assert rule.order_only_prerequisites == ["builddir"]
    passed("parse/splits_on_pipe")


def test_parse_multiple_each_side():
    """Multiple prereqs on both sides of | are collected correctly."""
    with tempfile.TemporaryDirectory() as d:
        mf = parse(mk("""
            output: a.o b.o | dir1 dir2
            \techo link
        """, d))
    rule = mf.rules["output"]
    assert rule.prerequisites == ["a.o", "b.o"]
    assert rule.order_only_prerequisites == ["dir1", "dir2"]
    passed("parse/multiple_each_side")


def test_parse_order_only_only():
    """Target with no normal prereqs but order-only prereqs."""
    with tempfile.TemporaryDirectory() as d:
        mf = parse(mk("""
            output: | builddir
            \techo build
        """, d))
    rule = mf.rules["output"]
    assert rule.prerequisites == []
    assert rule.order_only_prerequisites == ["builddir"]
    passed("parse/order_only_only")


def test_parse_no_order_only():
    """Rule with no | has empty order_only_prerequisites."""
    with tempfile.TemporaryDirectory() as d:
        mf = parse(mk("""
            output: a.c b.c
            \techo build
        """, d))
    rule = mf.rules["output"]
    assert rule.prerequisites == ["a.c", "b.c"]
    assert rule.order_only_prerequisites == []
    passed("parse/no_order_only")


def test_parse_merge_across_lines():
    """Multiple rule lines for the same target accumulate order-only prereqs."""
    with tempfile.TemporaryDirectory() as d:
        mf = parse(mk("""
            output: a.c | dir1
            output: b.c | dir2
            \techo build
        """, d))
    rule = mf.rules["output"]
    assert "a.c" in rule.prerequisites
    assert "b.c" in rule.prerequisites
    assert "dir1" in rule.order_only_prerequisites
    assert "dir2" in rule.order_only_prerequisites
    passed("parse/merge_across_lines")


def test_parse_static_pattern_order_only():
    """Static pattern rules support | for order-only prereqs."""
    with tempfile.TemporaryDirectory() as d:
        mf = parse(mk("""
            foo.o bar.o: %.o: %.c | builddir
            \techo compile
        """, d))
    assert mf.rules["foo.o"].prerequisites == ["foo.c"]
    assert mf.rules["foo.o"].order_only_prerequisites == ["builddir"]
    assert mf.rules["bar.o"].prerequisites == ["bar.c"]
    assert mf.rules["bar.o"].order_only_prerequisites == ["builddir"]
    passed("parse/static_pattern_order_only")


def test_parse_double_colon_order_only():
    """Double-colon rules support | for order-only prereqs."""
    with tempfile.TemporaryDirectory() as d:
        mf = parse(mk("""
            target:: src.c | builddir
            \techo build
        """, d))
    rule = mf.double_colon_rules["target"][0]
    assert rule.prerequisites == ["src.c"]
    assert rule.order_only_prerequisites == ["builddir"]
    passed("parse/double_colon_order_only")


# ═══════════════════════════════════════════════════════════════════════════════
# Core behaviour: ordering without mtime influence
# ═══════════════════════════════════════════════════════════════════════════════

def test_exec_order_only_built_first():
    """Order-only prereq is built before the target that depends on it."""
    with tempfile.TemporaryDirectory() as d:
        sentinel = Path(d, "order.txt")
        rc, out = run_pymake("-f", mk(f"""
            .PHONY: all builddir
            all: | builddir
            \techo all >> {sentinel}
            builddir:
            \techo builddir >> {sentinel}
        """, d), "all")
        assert rc == 0
        lines = sentinel.read_text().strip().splitlines()
        assert lines.index("builddir") < lines.index("all")
    passed("exec/order_only_built_first")


def test_exec_order_only_does_not_trigger_rebuild():
    """Updating an order-only prereq does NOT cause the target to rebuild."""
    with tempfile.TemporaryDirectory() as d:
        target = Path(d, "output.txt")
        oo_dep = Path(d, "builddir")

        # Write target first, then order-only dep (making oo dep newer)
        target.write_text("built")
        time.sleep(0.05)
        oo_dep.mkdir()  # order-only prereq exists and is newer

        rc, out = run_pymake("-f", mk("""
            output.txt: | builddir
            \techo REBUILDING
        """, d), "output.txt")
        assert rc == 0
        assert_not_out(out, "REBUILDING")
        assert_out(out, "up to date")
    passed("exec/order_only_does_not_trigger_rebuild")


def test_exec_normal_prereq_still_triggers_rebuild():
    """A normal prereq newer than the target still causes a rebuild."""
    with tempfile.TemporaryDirectory() as d:
        target = Path(d, "output.txt")
        normal = Path(d, "source.c")
        oo = Path(d, "builddir")

        target.write_text("old")
        oo.mkdir()
        time.sleep(0.05)
        normal.write_text("new")   # normal prereq is newer → rebuild

        rc, out = run_pymake("-f", mk("""
            output.txt: source.c | builddir
            \techo REBUILDING
        """, d), "output.txt")
        assert rc == 0
        assert_out(out, "REBUILDING")
    passed("exec/normal_prereq_still_triggers_rebuild")


def test_exec_missing_target_always_rebuilds():
    """Missing target always rebuilds even if order-only prereq is present."""
    with tempfile.TemporaryDirectory() as d:
        Path(d, "builddir").mkdir()
        rc, out = run_pymake("-f", mk("""
            output.txt: | builddir
            \techo BUILDING
        """, d), "output.txt")
        assert rc == 0
        assert_out(out, "BUILDING")
    passed("exec/missing_target_always_rebuilds")


def test_exec_order_only_only_no_normal_prereqs():
    """Target with only order-only prereqs (no normal) rebuilds only when missing."""
    with tempfile.TemporaryDirectory() as d:
        oo = Path(d, "builddir")
        target = Path(d, "output.txt")
        oo.mkdir()

        # First run: target missing → build
        rc, out = run_pymake("-f", mk("""
            output.txt: | builddir
            \techo BUILT
        """, d), "output.txt")
        assert_out(out, "BUILT")

        # Create target; second run: target exists, no normal prereqs → up to date
        target.write_text("exists")
        time.sleep(0.05)
        # Even if oo is "newer", it shouldn't trigger rebuild
        rc, out = run_pymake("-f", mk("""
            output.txt: | builddir
            \techo BUILT
        """, d), "output.txt")
        assert_not_out(out, "BUILT")
        assert_out(out, "up to date")
    passed("exec/order_only_only_no_normal_prereqs")


# ═══════════════════════════════════════════════════════════════════════════════
# Automatic variables
# ═══════════════════════════════════════════════════════════════════════════════

def test_exec_caret_excludes_order_only():
    """$^ contains only normal prereqs, not order-only ones."""
    with tempfile.TemporaryDirectory() as d:
        Path(d, "a.o").write_text("")
        Path(d, "b.o").write_text("")
        Path(d, "builddir").mkdir()
        rc, out = run_pymake("-f", mk("""
            output: a.o b.o | builddir
            \techo normal=$^
        """, d), "output")
        assert_out(out, "normal=a.o b.o")
        assert_not_out(out, "builddir")
    passed("exec/$^_excludes_order_only")


def test_exec_pipe_var_contains_order_only():
    """$| expands to the order-only prerequisites."""
    with tempfile.TemporaryDirectory() as d:
        Path(d, "a.o").write_text("")
        Path(d, "builddir").mkdir()
        rc, out = run_pymake("-f", mk("""
            output: a.o | builddir
            \techo oo=$|
        """, d), "output")
        assert_out(out, "oo=builddir")
    passed("exec/$|_contains_order_only")


def test_exec_lt_is_first_normal_prereq():
    """$< is still the first *normal* prereq, not the first order-only one."""
    with tempfile.TemporaryDirectory() as d:
        Path(d, "main.c").write_text("")
        Path(d, "builddir").mkdir()
        rc, out = run_pymake("-f", mk("""
            main.o: main.c | builddir
            \techo first=$<
        """, d), "main.o")
        assert_out(out, "first=main.c")
    passed("exec/$<_is_first_normal_prereq")


# ═══════════════════════════════════════════════════════════════════════════════
# Real-world pattern: ensure build directory exists
# ═══════════════════════════════════════════════════════════════════════════════

def test_exec_create_builddir_pattern():
    """Classic use-case: ensure build/ directory exists before compiling."""
    with tempfile.TemporaryDirectory() as d:
        Path(d, "main.c").write_text("src")
        rc, out = run_pymake("-f", mk("""
            .PHONY: all
            BUILD = build

            all: $(BUILD)/main.o

            $(BUILD)/main.o: main.c | $(BUILD)
            \techo compile $< -> $@

            $(BUILD):
            \techo creating build dir
            \tmkdir -p $(BUILD)
        """, d), "all")
        assert rc == 0
        assert_out(out, "creating build dir", "compile main.c -> build/main.o")
        assert Path(d, "build").is_dir()
    passed("exec/create_builddir_pattern")


def test_exec_builddir_already_exists_no_rebuild():
    """If the build directory already exists, the target is still up to date."""
    with tempfile.TemporaryDirectory() as d:
        Path(d, "main.c").write_text("src")
        Path(d, "build").mkdir()
        obj = Path(d, "build", "main.o")
        time.sleep(0.05)
        obj.write_text("compiled")  # obj newer than main.c
        rc, out = run_pymake("-f", mk("""
            .PHONY: all
            all: build/main.o
            build/main.o: main.c | build
            \techo RECOMPILE
            build:
            \tmkdir -p build
        """, d), "all")
        assert rc == 0
        assert_not_out(out, "RECOMPILE")
    passed("exec/builddir_already_exists_no_rebuild")


# ═══════════════════════════════════════════════════════════════════════════════
# Interaction with other features
# ═══════════════════════════════════════════════════════════════════════════════

def test_exec_order_only_with_pattern_rule():
    """Order-only prereqs work alongside a generic pattern rule."""
    with tempfile.TemporaryDirectory() as d:
        Path(d, "foo.c").write_text("src")
        Path(d, "build").mkdir()
        rc, out = run_pymake("-f", mk("""
            .PHONY: all
            all: build/foo.o
            build/%.o: %.c | build
            \techo compile $< -> $@
            build:
            \tmkdir -p build
        """, d), "all")
        assert rc == 0
        assert_out(out, "compile foo.c -> build/foo.o")
    passed("exec/order_only_with_pattern_rule")


def test_exec_dry_run_resolves_order_only():
    """-n still resolves and shows order-only prereq commands."""
    rc, out = run_pymake("-f", mk("""
        .PHONY: all builddir
        all: | builddir
        \techo all
        builddir:
        \techo creating builddir
    """, tempfile.mkdtemp()), "-n", "all")
    assert rc == 0
    assert_out(out, "echo creating builddir", "echo all")
    passed("exec/dry_run_resolves_order_only")


def test_exec_double_colon_order_only_exec():
    """Order-only prereqs work correctly with double-colon rules."""
    with tempfile.TemporaryDirectory() as d:
        Path(d, "src.c").write_text("src")
        sentinel = Path(d, "order.txt")
        rc, out = run_pymake("-f", mk(f"""
            .PHONY: builddir
            target:: src.c | builddir
            \techo compiled >> {sentinel}
            builddir:
            \techo makedir >> {sentinel}
        """, d), "target")
        assert rc == 0
        lines = sentinel.read_text().strip().splitlines()
        assert lines.index("makedir") < lines.index("compiled")
    passed("exec/double_colon_order_only_exec")


# ═══════════════════════════════════════════════════════════════════════════════
# Runner
# ═══════════════════════════════════════════════════════════════════════════════

ALL_TESTS = [
    test_parse_splits_on_pipe,
    test_parse_multiple_each_side,
    test_parse_order_only_only,
    test_parse_no_order_only,
    test_parse_merge_across_lines,
    test_parse_static_pattern_order_only,
    test_parse_double_colon_order_only,
    test_exec_order_only_built_first,
    test_exec_order_only_does_not_trigger_rebuild,
    test_exec_normal_prereq_still_triggers_rebuild,
    test_exec_missing_target_always_rebuilds,
    test_exec_order_only_only_no_normal_prereqs,
    test_exec_caret_excludes_order_only,
    test_exec_pipe_var_contains_order_only,
    test_exec_lt_is_first_normal_prereq,
    test_exec_create_builddir_pattern,
    test_exec_builddir_already_exists_no_rebuild,
    test_exec_order_only_with_pattern_rule,
    test_exec_dry_run_resolves_order_only,
    test_exec_double_colon_order_only_exec,
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
    print(f"Order-only prerequisite tests: {len(PASS)} passed, {len(failed)} failed")
    if failed:
        print("Failed:")
        for n in failed:
            print(f"  {n}")
        sys.exit(1)
    else:
        print("All passed!")
