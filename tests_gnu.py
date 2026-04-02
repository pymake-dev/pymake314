"""
GNU Make test suite — scenarios ported to Python.

Each test is labelled with the category and script name from the GNU Make
test suite (tests/scripts/<category>/<n>) so you can cross-reference the
original Perl source at:
  https://git.savannah.gnu.org/cgit/make.git/tree/tests/scripts

Categories covered
------------------
  misc/       — general integration tests
  features/   — specific Makefile features
  variables/  — variable flavours and expansion
  options/    — CLI flags
  targets/    — special targets (.PHONY, .DEFAULT, pattern rules)
  functions/  — $(subst), $(patsubst), $(wildcard), $(filter), etc.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from pymake import main  # used for a few direct API tests

PYMAKE = [sys.executable, "-m", "pymake"]

# ─── helpers ──────────────────────────────────────────────────────────────────

def mk(content: str, d: str, name: str = "Makefile") -> str:
    """Write a dedented Makefile into directory d and return its path."""
    path = Path(d) / name
    path.write_text(textwrap.dedent(content))
    return str(path)


def run_pymake(*args, cwd=None) -> tuple[int, str]:
    """
    Run pymake as a subprocess and return (returncode, combined stdout+stderr).
    All recipe output is captured because we spawn a real process.
    """
    result = subprocess.run(
        PYMAKE + list(args),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        cwd=cwd,
    )
    return result.returncode, result.stdout


def run_target(content: str, *targets, variables=None, extra_argv=None) -> tuple[int, str]:
    """
    Write `content` as a Makefile in a temp dir, run pymake on it.
    Returns (returncode, combined output).
    """
    d = tempfile.mkdtemp()
    try:
        mf = mk(content, d)
        argv = ["-f", mf]
        if extra_argv:
            argv += list(extra_argv)
        if variables:
            argv += [f"{k}={v}" for k, v in variables.items()]
        argv += list(targets)
        return run_pymake(*argv)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def assert_out(out: str, *fragments):
    for frag in fragments:
        assert frag in out, f"Expected {frag!r} in output:\n{out}"


def assert_not_out(out: str, *fragments):
    for frag in fragments:
        assert frag not in out, f"Did NOT expect {frag!r} in output:\n{out}"


PASS: list[str] = []

def passed(name: str):
    PASS.append(name)
    print(f"PASS {name}")


# ═══════════════════════════════════════════════════════════════════════════════
# misc/general1 — basic dependency rebuild simulation
# ═══════════════════════════════════════════════════════════════════════════════

def test_misc_general1_basic_rebuild():
    """Simulates building a product with dependencies (misc/general1)."""
    rc, out = run_target("""
        .PHONY: all
        OBJ = foo.o bar.o
        all: $(OBJ)
        \techo linking: $(OBJ)
        foo.o:
        \techo compiling foo.o
        bar.o:
        \techo compiling bar.o
    """, "all")
    assert rc == 0
    assert_out(out, "compiling foo.o", "compiling bar.o", "linking: foo.o bar.o")
    passed("misc/general1_basic_rebuild")


def test_misc_general1_clean():
    """Clean target runs independently (misc/general1)."""
    rc, out = run_target("""
        .PHONY: all clean
        all:
        \techo all
        clean:
        \techo cleaning up
    """, "clean")
    assert rc == 0
    assert_out(out, "cleaning up")
    assert_not_out(out, "echo all")
    passed("misc/general1_clean")


# ═══════════════════════════════════════════════════════════════════════════════
# misc/general2 — variables used in rules
# ═══════════════════════════════════════════════════════════════════════════════

def test_misc_general2_variable_in_recipe():
    """Variables in recipes expand correctly (misc/general2)."""
    rc, out = run_target("""
        .PHONY: all
        CC = echo
        CFLAGS = -Wall -O2
        all:
        \t$(CC) $(CFLAGS) hello.c
    """, "all")
    assert rc == 0
    assert_out(out, "-Wall -O2 hello.c")
    passed("misc/general2_variable_in_recipe")


# ═══════════════════════════════════════════════════════════════════════════════
# misc/general3 — default target is the first non-special rule
# ═══════════════════════════════════════════════════════════════════════════════

def test_misc_general3_default_target():
    """First non-special target is the default (misc/general3)."""
    rc, out = run_target("""
        .PHONY: first second
        first:
        \techo I am first
        second:
        \techo I am second
    """)
    assert rc == 0
    assert_out(out, "I am first")
    assert_not_out(out, "I am second")
    passed("misc/general3_default_target")


# ═══════════════════════════════════════════════════════════════════════════════
# features/errors — error handling in recipes
# ═══════════════════════════════════════════════════════════════════════════════

def test_features_errors_propagation():
    """Failed recipe stops the build (features/errors)."""
    rc, out = run_target("""
        .PHONY: fail
        fail:
        \tfalse
        \techo should not run
    """, "fail")
    assert rc != 0
    assert_not_out(out, "should not run")
    passed("features/errors_propagation")


def test_features_errors_dash_prefix():
    """The - prefix suppresses recipe error (features/errors)."""
    rc, out = run_target("""
        .PHONY: ok
        ok:
        \t-false
        \techo after error
    """, "ok")
    assert rc == 0
    assert_out(out, "after error")
    passed("features/errors_dash_prefix")


def test_features_errors_keep_going():
    """Keep-going (-k) continues building other targets past a failure (features/errors)."""
    rc, out = run_target("""
        .PHONY: all a b
        all: a b
        a:
        \tfalse
        b:
        \techo b ran
    """, "all", extra_argv=["-k"])
    assert_out(out, "b ran")
    passed("features/errors_keep_going")


# ═══════════════════════════════════════════════════════════════════════════════
# features/implicit_search — pattern rules
# ═══════════════════════════════════════════════════════════════════════════════

def test_features_pattern_rule_stem():
    """Pattern rule matches target and expands stem (features/implicit_search)."""
    with tempfile.TemporaryDirectory() as d:
        Path(d, "hello.src").write_text("source")
        rc, out = run_pymake("-f", mk("""
            %.out: %.src
            \techo building $@ from $<
        """, d), "hello.out")
        assert rc == 0
        assert_out(out, "building hello.out from hello.src")
    passed("features/pattern_rule_stem")


def test_features_pattern_rule_multiple():
    """Multiple targets matched by pattern rules (features/implicit_search)."""
    with tempfile.TemporaryDirectory() as d:
        for name in ("a.src", "b.src"):
            Path(d, name).write_text("src")
        rc, out = run_pymake("-f", mk("""
            .PHONY: all
            all: a.out b.out
            %.out: %.src
            \techo made $@
        """, d), "all")
        assert rc == 0
        assert_out(out, "made a.out", "made b.out")
    passed("features/pattern_rule_multiple")


# ═══════════════════════════════════════════════════════════════════════════════
# features/automatic_vars — $@, $<, $^, $*
# ═══════════════════════════════════════════════════════════════════════════════

def test_features_automatic_var_at():
    """$@ expands to the target name (features/automatic_vars)."""
    rc, out = run_target("""
        .PHONY: mytarget
        mytarget:
        \techo target=$@
    """, "mytarget")
    assert_out(out, "target=mytarget")
    passed("features/automatic_var_@")


def test_features_automatic_var_lt():
    """$< expands to the first prerequisite (features/automatic_vars)."""
    with tempfile.TemporaryDirectory() as d:
        Path(d, "dep1.txt").write_text("dep1")
        Path(d, "dep2.txt").write_text("dep2")
        rc, out = run_pymake("-f", mk("""
            .PHONY: all
            all: dep1.txt dep2.txt
            \techo first=$<
        """, d), "all")
        assert_out(out, "first=dep1.txt")
    passed("features/automatic_var_<")


def test_features_automatic_var_caret():
    """$^ expands to all prerequisites (features/automatic_vars)."""
    with tempfile.TemporaryDirectory() as d:
        for f in ("a.o", "b.o", "c.o"):
            Path(d, f).write_text("")
        rc, out = run_pymake("-f", mk("""
            .PHONY: all
            all: a.o b.o c.o
            \techo all=$^
        """, d), "all")
        assert_out(out, "all=a.o b.o c.o")
    passed("features/automatic_var_^")


def test_features_automatic_var_star():
    """$* expands to the stem in a pattern rule (features/automatic_vars)."""
    with tempfile.TemporaryDirectory() as d:
        Path(d, "widget.src").write_text("")
        rc, out = run_pymake("-f", mk("""
            %.out: %.src
            \techo stem=$*
        """, d), "widget.out")
        assert_out(out, "stem=widget")
    passed("features/automatic_var_*")


# ═══════════════════════════════════════════════════════════════════════════════
# features/phony — .PHONY targets
# ═══════════════════════════════════════════════════════════════════════════════

def test_features_phony_always_runs():
    """.PHONY target runs even when a file with that name exists (features/phony)."""
    with tempfile.TemporaryDirectory() as d:
        Path(d, "clean").write_text("I am a file named clean")
        rc, out = run_pymake("-f", mk("""
            .PHONY: clean
            clean:
            \techo cleaning
        """, d), "clean")
        assert rc == 0
        assert_out(out, "cleaning")
    passed("features/phony_always_runs")


def test_features_phony_file_up_to_date():
    """Non-phony target with an existing up-to-date file is skipped (features/phony)."""
    with tempfile.TemporaryDirectory() as d:
        Path(d, "output.txt").write_text("already built")
        rc, out = run_pymake("-f", mk("""
            output.txt:
            \techo should not rebuild
        """, d), "output.txt")
        assert rc == 0
        assert_not_out(out, "should not rebuild")
    passed("features/phony_file_up_to_date")


# ═══════════════════════════════════════════════════════════════════════════════
# features/include — include directive
# ═══════════════════════════════════════════════════════════════════════════════

def test_features_include():
    """include pulls in another Makefile's variables and rules (features/include)."""
    with tempfile.TemporaryDirectory() as d:
        Path(d, "vars.mk").write_text("GREETING = hello from include\n")
        rc, out = run_pymake("-f", mk("""
            include vars.mk
            .PHONY: greet
            greet:
            \techo $(GREETING)
        """, d), "greet")
        assert rc == 0
        assert_out(out, "hello from include")
    passed("features/include")


def test_features_include_silent_missing():
    """-include silently ignores missing files (features/include)."""
    rc, out = run_target("""
        -include does_not_exist.mk
        .PHONY: ok
        ok:
        \techo ok
    """, "ok")
    assert rc == 0
    assert_out(out, "ok")
    passed("features/include_silent_missing")


# ═══════════════════════════════════════════════════════════════════════════════
# features/mult_rules — multiple rules for the same target
# ═══════════════════════════════════════════════════════════════════════════════

def test_features_multiple_prereq_lines():
    """Two rule lines for the same target merge their prerequisites."""
    with tempfile.TemporaryDirectory() as d:
        for f in ("a.h", "b.h"):
            Path(d, f).write_text("")
        rc, out = run_pymake("-f", mk("""
            .PHONY: all
            all: a.h
            all: b.h
            \techo deps=$^
        """, d), "all")
        assert rc == 0
        assert_out(out, "a.h", "b.h")
    passed("features/multiple_prereq_lines")


# ═══════════════════════════════════════════════════════════════════════════════
# features/empty_recipe — targets with no recipe
# ═══════════════════════════════════════════════════════════════════════════════

def test_features_empty_recipe():
    """Target with no recipe still satisfies dependencies."""
    rc, out = run_target("""
        .PHONY: all dep
        all: dep
        \techo all done
        dep:
    """, "all")
    assert rc == 0
    assert_out(out, "all done")
    passed("features/empty_recipe")


# ═══════════════════════════════════════════════════════════════════════════════
# variables/flavors — =, :=, ?=, +=
# ═══════════════════════════════════════════════════════════════════════════════

def test_variables_recursive():
    """Recursive = variable re-evaluates at use time (variables/flavors)."""
    rc, out = run_target("""
        .PHONY: all
        X = original
        VAR = $(X)
        X = overridden
        all:
        \techo $(VAR)
    """, "all")
    assert_out(out, "overridden")
    passed("variables/recursive_=")


def test_variables_simply_expanded():
    """Simply-expanded := variable captures value at parse time (variables/flavors)."""
    rc, out = run_target("""
        .PHONY: all
        X = original
        VAR := $(X)
        X = overridden
        all:
        \techo $(VAR)
    """, "all")
    assert_out(out, "original")
    passed("variables/simply_expanded_:=")


def test_variables_conditional_assign_no_override():
    """?= does not overwrite an already-defined variable (variables/flavors)."""
    rc, out = run_target("""
        .PHONY: all
        VAR = already set
        VAR ?= default
        all:
        \techo $(VAR)
    """, "all")
    assert_out(out, "already set")
    passed("variables/conditional_?=_no_override")


def test_variables_conditional_assign_sets():
    """?= sets the variable when it has not been defined yet (variables/flavors)."""
    rc, out = run_target("""
        .PHONY: all
        VAR ?= default value
        all:
        \techo $(VAR)
    """, "all")
    assert_out(out, "default value")
    passed("variables/conditional_?=_sets")


def test_variables_append():
    """+= appends with a space (variables/flavors)."""
    rc, out = run_target("""
        .PHONY: all
        CFLAGS = -O2
        CFLAGS += -Wall
        CFLAGS += -g
        all:
        \techo $(CFLAGS)
    """, "all")
    assert_out(out, "-O2 -Wall -g")
    passed("variables/append_+=")


def test_variables_override_on_cmdline():
    """Command-line VAR=value overrides Makefile assignment (variables/override)."""
    rc, out = run_target("""
        .PHONY: all
        NAME = makefile
        all:
        \techo $(NAME)
    """, "all", variables={"NAME": "cmdline"})
    assert_out(out, "cmdline")
    passed("variables/override_cmdline")


def test_variables_nested_expansion():
    """Nested variable references expand correctly (variables/expansion)."""
    rc, out = run_target("""
        .PHONY: all
        DIR = src
        FILE = main.c
        all:
        \techo $(DIR)/$(FILE)
    """, "all")
    assert_out(out, "src/main.c")
    passed("variables/nested_expansion")


def test_variables_multiword_prereqs():
    """A variable holding multiple words expands into multiple prerequisites."""
    with tempfile.TemporaryDirectory() as d:
        for f in ("a.o", "b.o"):
            Path(d, f).write_text("")
        rc, out = run_pymake("-f", mk("""
            .PHONY: all
            OBJS = a.o b.o
            all: $(OBJS)
            \techo objs=$(OBJS)
        """, d), "all")
        assert_out(out, "objs=a.o b.o")
    passed("variables/multiword_prereqs")


def test_variables_subst_reference():
    """$(VAR:.c=.o) suffix substitution reference (variables/substitution)."""
    rc, out = run_target("""
        .PHONY: all
        SRCS = foo.c bar.c baz.c
        OBJS = $(SRCS:.c=.o)
        all:
        \techo $(OBJS)
    """, "all")
    assert_out(out, "foo.o bar.o baz.o")
    passed("variables/subst_reference")


def test_variables_pattern_subst_reference():
    """$(VAR:%.c=%.o) pattern substitution reference (variables/substitution)."""
    rc, out = run_target("""
        .PHONY: all
        SRCS = src/foo.c src/bar.c
        OBJS = $(SRCS:%.c=%.o)
        all:
        \techo $(OBJS)
    """, "all")
    assert_out(out, "src/foo.o src/bar.o")
    passed("variables/pattern_subst_reference")


def test_variables_double_dollar():
    """$$ in a recipe produces a literal $ passed to the shell (variables/quoting)."""
    rc, out = run_target("""
        .PHONY: all
        all:
        \techo $$$$
    """, "all")
    # $$$$ → shell sees $$, which expands to the PID — just check it ran
    assert rc == 0
    passed("variables/double_dollar")


# ═══════════════════════════════════════════════════════════════════════════════
# options/dash-n — dry run
# ═══════════════════════════════════════════════════════════════════════════════

def test_options_dry_run_no_execution():
    """-n prints commands but does not execute them (options/dash-n)."""
    with tempfile.TemporaryDirectory() as d:
        sentinel = Path(d) / "sentinel.txt"
        rc, out = run_pymake("-f", mk(f"""
            .PHONY: all
            all:
            \ttouch {sentinel}
        """, d), "-n", "all")
        assert rc == 0
        assert not sentinel.exists(), "Dry run should not create files"
        assert_out(out, f"touch {sentinel}")
    passed("options/dash-n_no_execution")


def test_options_dry_run_shows_commands():
    """-n still echoes all commands (options/dash-n)."""
    rc, out = run_target("""
        .PHONY: all
        all:
        \techo hello
        \techo world
    """, "-n", "all")
    assert_out(out, "echo hello", "echo world")
    passed("options/dash-n_shows_commands")


# ═══════════════════════════════════════════════════════════════════════════════
# options/dash-s — silent mode
# ═══════════════════════════════════════════════════════════════════════════════

def test_options_silent_suppresses_echo():
    """-s suppresses command echoing but still runs commands (options/dash-s)."""
    rc, out = run_target("""
        .PHONY: all
        all:
        \techo hello
    """, "-s", "all")
    assert rc == 0
    assert_out(out, "hello")           # command output still appears
    assert_not_out(out, "echo hello")  # but the command line itself is not echoed
    passed("options/dash-s_suppresses_echo")


def test_options_at_prefix_silent():
    """@ prefix suppresses echo for that line only (options/dash-s)."""
    rc, out = run_target("""
        .PHONY: all
        all:
        \t@echo silent
        \techo loud
    """, "all")
    assert_out(out, "silent", "echo loud", "loud")
    assert_not_out(out, "@echo silent")
    passed("options/at_prefix_silent")


# ═══════════════════════════════════════════════════════════════════════════════
# options/dash-C — directory change
# ═══════════════════════════════════════════════════════════════════════════════

def test_options_dash_C_changes_dir():
    """-C changes to the given directory before processing (options/dash-C)."""
    with tempfile.TemporaryDirectory() as d:
        out_file = Path(d) / "ran.txt"
        mk(f"""
            .PHONY: all
            all:
            \techo ran > {out_file}
        """, d)
        rc, out = run_pymake("-C", d, "all")
        assert rc == 0
        assert out_file.exists()
    passed("options/dash-C_changes_dir")


def test_options_dash_C_relative_makefile():
    """-C with a relative Makefile path resolves inside the target dir."""
    with tempfile.TemporaryDirectory() as d:
        out_file = Path(d) / "ran.txt"
        mk(f"""
            .PHONY: all
            all:
            \techo hi > {out_file}
        """, d)
        rc, out = run_pymake("-C", d)
        assert rc == 0
        assert out_file.exists()
    passed("options/dash-C_relative_makefile")


# ═══════════════════════════════════════════════════════════════════════════════
# options/dash-f — alternate Makefile name
# ═══════════════════════════════════════════════════════════════════════════════

def test_options_dash_f_alternate_makefile():
    """-f reads a named file instead of Makefile (options/dash-f)."""
    with tempfile.TemporaryDirectory() as d:
        alt = Path(d) / "build.mk"
        alt.write_text(".PHONY: all\nall:\n\techo from build.mk\n")
        rc, out = run_pymake("-f", str(alt), "all")
        assert rc == 0
        assert_out(out, "from build.mk")
    passed("options/dash-f_alternate_makefile")


# ═══════════════════════════════════════════════════════════════════════════════
# options/dash-i — ignore errors globally
# ═══════════════════════════════════════════════════════════════════════════════

def test_options_dash_i_continues():
    """-i ignores all recipe errors and continues (options/dash-i)."""
    rc, out = run_target("""
        .PHONY: all
        all:
        \tfalse
        \techo after error
    """, "-i", "all")
    assert rc == 0
    assert_out(out, "after error")
    passed("options/dash-i_continues")


# ═══════════════════════════════════════════════════════════════════════════════
# targets/PHONY — .PHONY special target
# ═══════════════════════════════════════════════════════════════════════════════

def test_targets_phony_ignores_file():
    """.PHONY target always rebuilds regardless of a same-named file."""
    with tempfile.TemporaryDirectory() as d:
        Path(d, "all").write_text("stale file")
        rc, out = run_pymake("-f", mk("""
            .PHONY: all
            all:
            \techo phony ran
        """, d), "all")
        assert rc == 0
        assert_out(out, "phony ran")
    passed("targets/PHONY_ignores_file")


def test_targets_phony_as_dependency():
    """A phony prereq forces the dependent target to always rebuild."""
    rc, out = run_target("""
        .PHONY: force
        output: force
        \techo rebuilding
        force:
    """, "output")
    assert rc == 0
    assert_out(out, "rebuilding")
    passed("targets/PHONY_as_dependency")


# ═══════════════════════════════════════════════════════════════════════════════
# targets/pattern — pattern rules and stems
# ═══════════════════════════════════════════════════════════════════════════════

def test_targets_pattern_chained():
    """Pattern rules can chain: .src → .o → final (targets/pattern)."""
    with tempfile.TemporaryDirectory() as d:
        Path(d, "main.c").write_text("src")
        rc, out = run_pymake("-f", mk("""
            main: main.o
            \techo link main from $<
            %.o: %.c
            \techo compile $< to $@
        """, d), "main")
        assert rc == 0
        assert_out(out, "compile main.c to main.o", "link main from main.o")
    passed("targets/pattern_chained")


# ═══════════════════════════════════════════════════════════════════════════════
# targets/order — dependency ordering
# ═══════════════════════════════════════════════════════════════════════════════

def test_targets_dep_order():
    """Prerequisites are always built before the target that depends on them."""
    with tempfile.TemporaryDirectory() as d:
        sentinel = Path(d) / "order.txt"
        rc, out = run_pymake("-f", mk(f"""
            .PHONY: all step1 step2
            all: step1 step2
            \techo all >> {sentinel}
            step1:
            \techo step1 >> {sentinel}
            step2:
            \techo step2 >> {sentinel}
        """, d), "all")
        assert rc == 0
        lines = sentinel.read_text().strip().splitlines()
        assert lines.index("step1") < lines.index("all")
        assert lines.index("step2") < lines.index("all")
    passed("targets/dep_order")


def test_targets_circular_dependency_detected():
    """Circular dependency raises an error with a useful message (targets/circular)."""
    with tempfile.TemporaryDirectory() as d:
        rc, out = run_pymake("-f", mk("""
            .PHONY: a b
            a: b
            b: a
        """, d), "a")
        assert rc != 0
        assert_out(out, "Circular")
    passed("targets/circular_dependency_detected")


# ═══════════════════════════════════════════════════════════════════════════════
# features/mtime — up-to-date file timestamp checks
# ═══════════════════════════════════════════════════════════════════════════════

def test_features_mtime_up_to_date():
    """Target file newer than all prereqs is considered up to date."""
    import time
    with tempfile.TemporaryDirectory() as d:
        prereq = Path(d) / "source.c"
        target = Path(d) / "output.o"
        prereq.write_text("old source")
        time.sleep(0.05)
        target.write_text("compiled")
        rc, out = run_pymake("-f", mk("""
            output.o: source.c
            \techo REBUILDING
        """, d), "output.o")
        assert rc == 0
        assert_not_out(out, "REBUILDING")
    passed("features/mtime_up_to_date")


def test_features_mtime_stale_target():
    """Target file older than a prereq triggers a rebuild."""
    import time
    with tempfile.TemporaryDirectory() as d:
        target = Path(d) / "output.o"
        prereq = Path(d) / "source.c"
        target.write_text("old compiled")
        time.sleep(0.05)
        prereq.write_text("new source")
        rc, out = run_pymake("-f", mk("""
            output.o: source.c
            \techo REBUILDING
        """, d), "output.o")
        assert rc == 0
        assert_out(out, "REBUILDING")
    passed("features/mtime_stale_target")


def test_features_mtime_missing_target():
    """Missing target always triggers a build."""
    with tempfile.TemporaryDirectory() as d:
        Path(d, "source.c").write_text("src")
        rc, out = run_pymake("-f", mk("""
            output.o: source.c
            \techo BUILDING
        """, d), "output.o")
        assert rc == 0
        assert_out(out, "BUILDING")
    passed("features/mtime_missing_target")


def test_features_mtime_chain_rebuild():
    """Rebuilds cascade: if A is stale, everything depending on A also rebuilds."""
    import time
    with tempfile.TemporaryDirectory() as d:
        binary = Path(d) / "main"
        obj    = Path(d) / "main.o"
        src    = Path(d) / "main.c"
        # Write oldest first so src ends up newest
        binary.write_text("old binary")
        time.sleep(0.05)
        obj.write_text("old obj")
        time.sleep(0.05)
        src.write_text("new source")   # src is newest → obj is stale → binary is stale
        rc, out = run_pymake("-f", mk("""
            main: main.o
            \techo link
            main.o: main.c
            \techo compile
        """, d), "main")
        assert rc == 0
        assert_out(out, "compile", "link")
    passed("features/mtime_chain_rebuild")


# ═══════════════════════════════════════════════════════════════════════════════
# features/line_continuation — backslash line continuation
# ═══════════════════════════════════════════════════════════════════════════════

def test_features_line_continuation_prereqs():
    """Backslash continuation joins prerequisite lines (features/line_continuation)."""
    with tempfile.TemporaryDirectory() as d:
        for f in ("a.h", "b.h", "c.h"):
            Path(d, f).write_text("")
        rc, out = run_pymake("-f", mk(r"""
            .PHONY: all
            all: a.h \
                 b.h \
                 c.h
            	echo deps=$^
        """, d), "all")
        assert rc == 0
        assert_out(out, "a.h", "b.h", "c.h")
    passed("features/line_continuation_prereqs")


def test_features_line_continuation_variable():
    """Backslash continuation works in variable definitions."""
    rc, out = run_target(r"""
        .PHONY: all
        CFLAGS = -Wall \
                 -O2 \
                 -g
        all:
        	echo $(CFLAGS)
    """, "all")
    assert rc == 0
    assert_out(out, "-Wall", "-O2", "-g")
    passed("features/line_continuation_variable")


# ═══════════════════════════════════════════════════════════════════════════════
# features/comments — inline comment stripping
# ═══════════════════════════════════════════════════════════════════════════════

def test_features_comments_inline():
    """Inline # comments are stripped from variable values (features/comments)."""
    rc, out = run_target("""
        .PHONY: all
        VAR = hello # this is a comment
        all:
        \techo $(VAR)
    """, "all")
    assert_out(out, "hello")
    assert_not_out(out, "this is a comment")
    passed("features/comments_inline")


# ═══════════════════════════════════════════════════════════════════════════════
# misc/error_messages — helpful error messages
# ═══════════════════════════════════════════════════════════════════════════════

def test_misc_no_rule_for_target():
    """Missing target with no rule and no matching file gives a non-zero exit."""
    with tempfile.TemporaryDirectory() as d:
        rc, out = run_pymake("-f", mk("""
            .PHONY: all
            all: missing_dep
            \techo all
        """, d), "all")
        assert rc != 0
    passed("misc/no_rule_for_target")


def test_misc_missing_makefile():
    """Invoking pymake with a nonexistent Makefile exits non-zero."""
    rc, out = run_pymake("-f", "/tmp/does_not_exist_xyz.mk")
    assert rc != 0
    passed("misc/missing_makefile_error")


# ═══════════════════════════════════════════════════════════════════════════════
# runner
# ═══════════════════════════════════════════════════════════════════════════════

ALL_TESTS = [
    test_misc_general1_basic_rebuild,
    test_misc_general1_clean,
    test_misc_general2_variable_in_recipe,
    test_misc_general3_default_target,
    test_features_errors_propagation,
    test_features_errors_dash_prefix,
    test_features_errors_keep_going,
    test_features_pattern_rule_stem,
    test_features_pattern_rule_multiple,
    test_features_automatic_var_at,
    test_features_automatic_var_lt,
    test_features_automatic_var_caret,
    test_features_automatic_var_star,
    test_features_phony_always_runs,
    test_features_phony_file_up_to_date,
    test_features_include,
    test_features_include_silent_missing,
    test_features_multiple_prereq_lines,
    test_features_empty_recipe,
    test_variables_recursive,
    test_variables_simply_expanded,
    test_variables_conditional_assign_no_override,
    test_variables_conditional_assign_sets,
    test_variables_append,
    test_variables_override_on_cmdline,
    test_variables_nested_expansion,
    test_variables_multiword_prereqs,
    test_variables_subst_reference,
    test_variables_pattern_subst_reference,
    test_variables_double_dollar,
    test_options_dry_run_no_execution,
    test_options_dry_run_shows_commands,
    test_options_silent_suppresses_echo,
    test_options_at_prefix_silent,
    test_options_dash_C_changes_dir,
    test_options_dash_C_relative_makefile,
    test_options_dash_f_alternate_makefile,
    test_options_dash_i_continues,
    test_targets_phony_ignores_file,
    test_targets_phony_as_dependency,
    test_targets_pattern_chained,
    test_targets_dep_order,
    test_targets_circular_dependency_detected,
    test_features_mtime_up_to_date,
    test_features_mtime_stale_target,
    test_features_mtime_missing_target,
    test_features_mtime_chain_rebuild,
    test_features_line_continuation_prereqs,
    test_features_line_continuation_variable,
    test_features_comments_inline,
    test_misc_no_rule_for_target,
    test_misc_missing_makefile,
]


if __name__ == "__main__":
    failed = []
    for test_fn in ALL_TESTS:
        try:
            test_fn()
        except Exception as e:
            import traceback
            print(f"FAIL {test_fn.__name__}: {e}")
            traceback.print_exc()
            failed.append(test_fn.__name__)

    print(f"\n{'='*60}")
    print(f"GNU Make compatibility: {len(PASS)} passed, {len(failed)} failed")
    if failed:
        print("Failed:")
        for name in failed:
            print(f"  {name}")
        sys.exit(1)
    else:
        print("All passed!")
