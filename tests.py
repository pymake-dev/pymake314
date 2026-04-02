"""Tests for pymake."""
import os
import sys
import tempfile
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from pymake.parser import parse
from pymake.resolver import resolve
from pymake.executor import execute
from pymake import run, main

import contextlib

@contextlib.contextmanager
def cd(path):
    """Temporarily change directory, always restoring on exit."""
    old = os.getcwd()
    try:
        os.chdir(path)
        yield
    finally:
        # Restore, but if old dir was deleted (e.g. a tempdir) fall back to /tmp
        try:
            os.chdir(old)
        except OSError:
            os.chdir(tempfile.gettempdir())



def write_makefile(content: str, dir_: str) -> str:
    path = os.path.join(dir_, "Makefile")
    Path(path).write_text(textwrap.dedent(content))
    return path


# ── Parser tests ──────────────────────────────────────────────────────────────

def test_parse_variables():
    with tempfile.TemporaryDirectory() as d:
        mf = parse(write_makefile("""
            CC = gcc
            CFLAGS := -O2 -Wall
            DEBUG ?= 0
            CFLAGS += -g
        """, d))
    assert mf.variables["CC"] == "gcc"
    assert mf.variables["CFLAGS"] == "-O2 -Wall -g"
    assert mf.variables["DEBUG"] == "0"
    print("PASS test_parse_variables")


def test_parse_phony():
    with tempfile.TemporaryDirectory() as d:
        mf = parse(write_makefile("""
            .PHONY: all clean

            all:
            \techo hello

            clean:
            \trm -f *.o
        """, d))
    assert "all" in mf.phony_targets
    assert "clean" in mf.phony_targets
    assert mf.rules["all"].is_phony
    print("PASS test_parse_phony")


def test_parse_prereqs():
    with tempfile.TemporaryDirectory() as d:
        mf = parse(write_makefile("""
            main: main.o util.o
            \tgcc -o main main.o util.o
        """, d))
    rule = mf.rules["main"]
    assert rule.prerequisites == ["main.o", "util.o"]
    assert rule.recipe == ["gcc -o main main.o util.o"]
    print("PASS test_parse_prereqs")


def test_parse_default_target():
    with tempfile.TemporaryDirectory() as d:
        mf = parse(write_makefile("""
            first:
            \techo first
            second:
            \techo second
        """, d))
    assert mf.default_target == "first"
    print("PASS test_parse_default_target")


def test_variable_expansion():
    """Recursive = variables defer expansion; := variables expand immediately."""
    with tempfile.TemporaryDirectory() as d:
        mf = parse(write_makefile("""
            DIR = src
            FILE = main.c
            IMMEDIATE := src/main.c
            PATH_TO_FILE = $(DIR)/$(FILE)
        """, d))
    # Recursive var: stored unexpanded, but expands correctly when used
    from pymake.parser import expand_variables
    assert expand_variables("$(PATH_TO_FILE)", mf.variables) == "src/main.c"
    # Immediate := var: stored already expanded
    assert mf.variables["IMMEDIATE"] == "src/main.c"
    print("PASS test_variable_expansion")


def test_pattern_rule():
    with tempfile.TemporaryDirectory() as d:
        mf = parse(write_makefile("""
            %.o: %.c
            \tgcc -c $< -o $@
        """, d))
    assert len(mf.pattern_rules) == 1
    print("PASS test_pattern_rule")


# ── Resolver tests ─────────────────────────────────────────────────────────────

def test_resolve_simple():
    with tempfile.TemporaryDirectory() as d:
        mf = parse(write_makefile("""
            .PHONY: all build

            all: build
            \techo all

            build:
            \techo build
        """, d))
    order = resolve("all", mf)
    assert order.index("build") < order.index("all")
    print("PASS test_resolve_simple")


def test_resolve_cycle():
    with tempfile.TemporaryDirectory() as d:
        mf = parse(write_makefile("""
            .PHONY: a b
            a: b
            b: a
        """, d))
    try:
        resolve("a", mf)
        assert False, "Should have raised"
    except RuntimeError as e:
        assert "Circular" in str(e)
    print("PASS test_resolve_cycle")


def test_resolve_existing_file_leaf():
    with tempfile.TemporaryDirectory() as d:
        # Create a source file that acts as a leaf
        src = Path(d) / "main.c"
        src.write_text("int main(){}")
        mf = parse(write_makefile("""
            .PHONY: build
            build: main.c
            \techo building
        """, d))
        with cd(d):
            order = resolve("build", mf)
    assert "build" in order
    print("PASS test_resolve_existing_file_leaf")


# ── Executor tests ─────────────────────────────────────────────────────────────

def test_execute_dry_run():
    with tempfile.TemporaryDirectory() as d:
        mf = parse(write_makefile("""
            .PHONY: greet
            greet:
            \techo hello
        """, d))
        order = resolve("greet", mf)
        rc = execute(order, mf, dry_run=True)
        assert rc == 0
    print("PASS test_execute_dry_run")


def test_execute_runs_command():
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "out.txt"
        mf = parse(write_makefile(f"""
            .PHONY: write
            write:
            \techo hello > {out}
        """, d))
        order = resolve("write", mf)
        rc = execute(order, mf)
        assert rc == 0
        assert out.exists()
        assert "hello" in out.read_text()
    print("PASS test_execute_runs_command")


def test_execute_error_propagates():
    with tempfile.TemporaryDirectory() as d:
        mf = parse(write_makefile("""
            .PHONY: fail
            fail:
            \tfalse
        """, d))
        order = resolve("fail", mf)
        rc = execute(order, mf)
        assert rc != 0
    print("PASS test_execute_error_propagates")


def test_execute_ignore_errors():
    with tempfile.TemporaryDirectory() as d:
        mf = parse(write_makefile("""
            .PHONY: fail
            fail:
            \t-false
            \techo after
        """, d))
        order = resolve("fail", mf)
        rc = execute(order, mf)
        assert rc == 0
    print("PASS test_execute_ignore_errors")


# ── CLI tests ──────────────────────────────────────────────────────────────────

def test_cli_dry_run():
    with tempfile.TemporaryDirectory() as d:
        write_makefile("""
            .PHONY: hi
            hi:
            \techo hi
        """, d)
        with cd(d):
            rc = main(["-n", "hi"])
        assert rc == 0
    print("PASS test_cli_dry_run")


def test_cli_var_override():
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "out.txt"
        mf_path = write_makefile(f"""
            NAME = world
            .PHONY: greet
            greet:
            \techo $(NAME) > {out}
        """, d)
        rc = main(["-f", mf_path, "NAME=pymake", "greet"])
        assert rc == 0
        assert "pymake" in out.read_text()
    print("PASS test_cli_var_override")


def test_cli_directory_flag():
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "out.txt"
        write_makefile(f"""
            .PHONY: greet
            greet:
            \techo hi > {out}
        """, d)
        # Run from a completely different cwd — -C points pymake at the right dir
        with cd(tempfile.gettempdir()):
            rc = main(["-C", d, "greet"])
        assert rc == 0
        assert out.exists()
        assert "hi" in out.read_text()
    print("PASS test_cli_directory_flag")


def test_cli_print_database():
    with tempfile.TemporaryDirectory() as d:
        write_makefile("""
            FOO = bar
            .PHONY: all
            all:
            \techo all
        """, d)
        with cd(d):
            rc = main(["-p"])
        assert rc == 0
    print("PASS test_cli_print_database")


# ── Public API ─────────────────────────────────────────────────────────────────

def test_api_run():
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "result.txt"
        write_makefile(f"""
            .PHONY: all
            all:
            \techo done > {out}
        """, d)
        rc = run(makefile=Path(d) / "Makefile")
        assert rc == 0
        assert out.exists()
    print("PASS test_api_run")


if __name__ == "__main__":
    test_parse_variables()
    test_parse_phony()
    test_parse_prereqs()
    test_parse_default_target()
    test_variable_expansion()
    test_pattern_rule()
    test_resolve_simple()
    test_resolve_cycle()
    test_resolve_existing_file_leaf()
    test_execute_dry_run()
    test_execute_runs_command()
    test_execute_error_propagates()
    test_execute_ignore_errors()
    test_cli_dry_run()
    test_cli_var_override()
    test_cli_directory_flag()
    test_cli_print_database()
    test_api_run()
    print("\nAll tests passed!")
