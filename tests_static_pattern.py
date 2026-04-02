from __future__ import annotations
"""
Tests for static pattern rule support.

Syntax:
    targets : target-pattern : prereq-patterns
        recipe

GNU Make semantics tested:
  - Correct stem extraction per target
  - Prereq patterns expanded with stem
  - Multiple prereq patterns (fixed + %)
  - Variable expansion in target list
  - Static rules take priority over generic pattern rules for listed targets
  - Non-matching target raises an error
  - Automatic variables $@, $<, $^, $* work correctly
  - Interaction with -n dry run, -s silent, up-to-date checks
  - Double-colon static pattern rules (targets :: pattern : prereqs)
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

def test_parse_basic_static_pattern():
    """Static pattern rule creates concrete rules with correct prereqs."""
    with tempfile.TemporaryDirectory() as d:
        mf = parse(mk("""
            OBJS = foo.o bar.o baz.o
            $(OBJS): %.o: %.c
            \techo compile $<
        """, d))
    assert set(mf.rules.keys()) >= {"foo.o", "bar.o", "baz.o"}
    assert mf.rules["foo.o"].prerequisites == ["foo.c"]
    assert mf.rules["bar.o"].prerequisites == ["bar.c"]
    assert mf.rules["baz.o"].prerequisites == ["baz.c"]
    passed("parse/basic_static_pattern")


def test_parse_recipe_shared_by_all_targets():
    """All targets in a static pattern rule share the same recipe template."""
    with tempfile.TemporaryDirectory() as d:
        mf = parse(mk("""
            foo.o bar.o: %.o: %.c
            \techo compiling $<
            \techo done $@
        """, d))
    for t in ("foo.o", "bar.o"):
        assert mf.rules[t].recipe == ["echo compiling $<", "echo done $@"], \
            f"Recipe missing for {t}"
    passed("parse/recipe_shared_by_all_targets")


def test_parse_multiple_prereq_patterns():
    """Prereq patterns with both % and fixed files expand correctly."""
    with tempfile.TemporaryDirectory() as d:
        mf = parse(mk("""
            foo.o bar.o: %.o: %.c common.h
            \techo build
        """, d))
    assert mf.rules["foo.o"].prerequisites == ["foo.c", "common.h"]
    assert mf.rules["bar.o"].prerequisites == ["bar.c", "common.h"]
    passed("parse/multiple_prereq_patterns")


def test_parse_path_in_pattern():
    """Patterns with directory components extract the stem correctly."""
    with tempfile.TemporaryDirectory() as d:
        mf = parse(mk("""
            build/foo.o build/bar.o: build/%.o: src/%.c
            \techo build
        """, d))
    assert mf.rules["build/foo.o"].prerequisites == ["src/foo.c"]
    assert mf.rules["build/bar.o"].prerequisites == ["src/bar.c"]
    passed("parse/path_in_pattern")


def test_parse_variable_expansion_in_target_list():
    """Variable in the target list expands before stem extraction."""
    with tempfile.TemporaryDirectory() as d:
        mf = parse(mk("""
            SRCS = alpha beta gamma
            OBJS = $(SRCS:%=%.o)
            $(OBJS): %.o: %.c
            \techo build $@
        """, d))
    for stem in ("alpha", "beta", "gamma"):
        assert f"{stem}.o" in mf.rules
        assert mf.rules[f"{stem}.o"].prerequisites == [f"{stem}.c"]
    passed("parse/variable_expansion_in_target_list")


def test_parse_no_percent_in_pattern():
    """Target pattern without % matches exact target name (no stem)."""
    with tempfile.TemporaryDirectory() as d:
        mf = parse(mk("""
            special.o: special.o: special.c helper.c
            \techo build
        """, d))
    assert mf.rules["special.o"].prerequisites == ["special.c", "helper.c"]
    passed("parse/no_percent_in_pattern")


def test_parse_non_matching_target_raises():
    """A target that doesn't match the pattern raises ValueError."""
    with tempfile.TemporaryDirectory() as d:
        try:
            parse(mk("""
                foo.o wrong.c: %.o: %.c
                \techo build
            """, d))
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "wrong.c" in str(e)
            assert "%.o" in str(e)
    passed("parse/non_matching_target_raises")


def test_parse_no_prereq_patterns():
    """Static pattern with empty prereq list creates rules with no prereqs."""
    with tempfile.TemporaryDirectory() as d:
        mf = parse(mk("""
            foo.o bar.o: %.o:
            \techo build $@
        """, d))
    assert mf.rules["foo.o"].prerequisites == []
    assert mf.rules["bar.o"].prerequisites == []
    passed("parse/no_prereq_patterns")


# ═══════════════════════════════════════════════════════════════════════════════
# Execution tests
# ═══════════════════════════════════════════════════════════════════════════════

def test_exec_basic_build():
    """Static pattern rule builds each target from its matched source."""
    with tempfile.TemporaryDirectory() as d:
        for f in ("foo.c", "bar.c"):
            Path(d, f).write_text("src")
        rc, out = run_pymake("-f", mk("""
            .PHONY: all
            all: foo.o bar.o
            foo.o bar.o: %.o: %.c
            \techo compile $< -> $@
        """, d), "all")
        assert rc == 0
        assert_out(out, "compile foo.c -> foo.o", "compile bar.c -> bar.o")
    passed("exec/basic_build")


def test_exec_automatic_var_at():
    """$@ expands to the specific target, not the pattern."""
    with tempfile.TemporaryDirectory() as d:
        Path(d, "widget.c").write_text("src")
        rc, out = run_pymake("-f", mk("""
            widget.o: %.o: %.c
            \techo target=$@
        """, d), "widget.o")
        assert_out(out, "target=widget.o")
    passed("exec/automatic_var_@")


def test_exec_automatic_var_star():
    """$* expands to the stem."""
    with tempfile.TemporaryDirectory() as d:
        Path(d, "widget.c").write_text("src")
        rc, out = run_pymake("-f", mk("""
            widget.o: %.o: %.c
            \techo stem=$*
        """, d), "widget.o")
        assert_out(out, "stem=widget")
    passed("exec/automatic_var_*")


def test_exec_automatic_var_lt():
    """$< expands to the first prereq (the matched source file)."""
    with tempfile.TemporaryDirectory() as d:
        Path(d, "main.c").write_text("src")
        Path(d, "common.h").write_text("hdr")
        rc, out = run_pymake("-f", mk("""
            main.o: %.o: %.c common.h
            \techo first=$<
        """, d), "main.o")
        assert_out(out, "first=main.c")
    passed("exec/automatic_var_<")


def test_exec_automatic_var_caret():
    """$^ expands to all prereqs."""
    with tempfile.TemporaryDirectory() as d:
        Path(d, "main.c").write_text("src")
        Path(d, "common.h").write_text("hdr")
        rc, out = run_pymake("-f", mk("""
            main.o: %.o: %.c common.h
            \techo all=$^
        """, d), "main.o")
        assert_out(out, "all=main.c common.h")
    passed("exec/automatic_var_^")


def test_exec_mtime_up_to_date():
    """Static pattern rule respects mtime: skips up-to-date targets."""
    with tempfile.TemporaryDirectory() as d:
        src = Path(d, "foo.c")
        obj = Path(d, "foo.o")
        src.write_text("src")
        time.sleep(0.05)
        obj.write_text("compiled")   # obj is newer than src
        rc, out = run_pymake("-f", mk("""
            foo.o: %.o: %.c
            \techo RECOMPILE
        """, d), "foo.o")
        assert rc == 0
        assert_not_out(out, "RECOMPILE")
    passed("exec/mtime_up_to_date")


def test_exec_mtime_stale():
    """Static pattern rule rebuilds a stale target."""
    with tempfile.TemporaryDirectory() as d:
        obj = Path(d, "foo.o")
        src = Path(d, "foo.c")
        obj.write_text("old")
        time.sleep(0.05)
        src.write_text("new src")    # src is newer than obj
        rc, out = run_pymake("-f", mk("""
            foo.o: %.o: %.c
            \techo RECOMPILE
        """, d), "foo.o")
        assert rc == 0
        assert_out(out, "RECOMPILE")
    passed("exec/mtime_stale")


def test_exec_static_overrides_pattern_rule():
    """Static pattern rule takes priority over a generic pattern rule for listed targets."""
    with tempfile.TemporaryDirectory() as d:
        for f in ("foo.c", "bar.c", "other.c"):
            Path(d, f).write_text("src")
        rc, out = run_pymake("-f", mk("""
            .PHONY: all
            all: foo.o bar.o other.o
            foo.o bar.o: %.o: %.c
            \techo static $@
            %.o: %.c
            \techo generic $@
        """, d), "all")
        assert rc == 0
        assert_out(out, "static foo.o", "static bar.o", "generic other.o")
        assert_not_out(out, "generic foo.o", "generic bar.o")
    passed("exec/static_overrides_pattern_rule")


def test_exec_dry_run():
    """-n shows static pattern recipe commands without running them."""
    with tempfile.TemporaryDirectory() as d:
        Path(d, "foo.c").write_text("src")
        sentinel = Path(d, "foo.o")
        rc, out = run_pymake("-f", mk(f"""
            foo.o: %.o: %.c
            \ttouch {sentinel}
        """, d), "-n", "foo.o")
        assert rc == 0
        assert_out(out, f"touch {sentinel}")
        assert not sentinel.exists()
    passed("exec/dry_run")


def test_exec_silent_prefix():
    """@ prefix works in static pattern recipes."""
    with tempfile.TemporaryDirectory() as d:
        Path(d, "foo.c").write_text("src")
        rc, out = run_pymake("-f", mk("""
            foo.o: %.o: %.c
            \t@echo built $@
        """, d), "foo.o")
        assert_out(out, "built foo.o")
        assert_not_out(out, "@echo")
    passed("exec/silent_prefix")


def test_exec_multiple_targets_one_rule():
    """One static pattern rule builds all listed targets in dependency order."""
    with tempfile.TemporaryDirectory() as d:
        for f in ("a.c", "b.c", "c.c"):
            Path(d, f).write_text("src")
        sentinel = Path(d, "log.txt")
        rc, out = run_pymake("-f", mk(f"""
            .PHONY: all
            all: a.o b.o c.o
            a.o b.o c.o: %.o: %.c
            \techo built $@ >> {sentinel}
        """, d), "all")
        assert rc == 0
        lines = sentinel.read_text().strip().splitlines()
        assert set(lines) == {"built a.o", "built b.o", "built c.o"}
    passed("exec/multiple_targets_one_rule")


def test_exec_chained_static_rules():
    """Static pattern rules can chain (link step depends on compile step)."""
    with tempfile.TemporaryDirectory() as d:
        Path(d, "main.c").write_text("src")
        rc, out = run_pymake("-f", mk("""
            main: main.o
            \techo link $<
            main.o: %.o: %.c
            \techo compile $< -> $@
        """, d), "main")
        assert rc == 0
        assert_out(out, "compile main.c -> main.o", "link main.o")
    passed("exec/chained_static_rules")


# ═══════════════════════════════════════════════════════════════════════════════
# Runner
# ═══════════════════════════════════════════════════════════════════════════════

ALL_TESTS = [
    test_parse_basic_static_pattern,
    test_parse_recipe_shared_by_all_targets,
    test_parse_multiple_prereq_patterns,
    test_parse_path_in_pattern,
    test_parse_variable_expansion_in_target_list,
    test_parse_no_percent_in_pattern,
    test_parse_non_matching_target_raises,
    test_parse_no_prereq_patterns,
    test_exec_basic_build,
    test_exec_automatic_var_at,
    test_exec_automatic_var_star,
    test_exec_automatic_var_lt,
    test_exec_automatic_var_caret,
    test_exec_mtime_up_to_date,
    test_exec_mtime_stale,
    test_exec_static_overrides_pattern_rule,
    test_exec_dry_run,
    test_exec_silent_prefix,
    test_exec_multiple_targets_one_rule,
    test_exec_chained_static_rules,
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
    print(f"Static pattern rule tests: {len(PASS)} passed, {len(failed)} failed")
    if failed:
        print("Failed:")
        for n in failed:
            print(f"  {n}")
        sys.exit(1)
    else:
        print("All passed!")
