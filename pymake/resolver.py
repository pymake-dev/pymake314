from __future__ import annotations
"""
Resolve build order for a target by doing a topological sort of the
dependency graph. Detects cycles and matches pattern rules.

Double-colon rules: each :: block for the same target is independent —
we resolve and return them all, in order, as separate entries.
"""
import os
from pathlib import Path
from .model import Makefile, Rule


def _match_pattern(pattern: str, target: str) -> str | None:
    """If `target` matches `pattern` (which contains one %), return the stem."""
    if '%' not in pattern:
        return None
    prefix, suffix = pattern.split('%', 1)
    if target.startswith(prefix) and target.endswith(suffix):
        stem = target[len(prefix): len(target) - len(suffix) if suffix else len(target)]
        return stem
    return None


def _find_rule(target: str, mf: Makefile) -> Rule | None:
    """
    Find the single-colon rule for a target, trying explicit rules first,
    then pattern rules.  Does NOT look at double_colon_rules — those are
    handled separately by find_double_colon_rules().
    """
    if target in mf.rules:
        return mf.rules[target]
    for rule in mf.pattern_rules:
        if rule.is_double_colon:
            continue   # pattern :: rules matched elsewhere
        stem = _match_pattern(rule.target, target)
        if stem is not None:
            from .model import Rule as R
            prereqs    = [p.replace('%', stem) for p in rule.prerequisites]
            oo_prereqs = [p.replace('%', stem) for p in rule.order_only_prerequisites]
            recipe     = [cmd.replace('%', stem) for cmd in rule.recipe]
            concrete = R(
                target=target,
                prerequisites=prereqs,
                order_only_prerequisites=oo_prereqs,
                recipe=recipe,
                is_phony=False,
                is_pattern=False,
                is_double_colon=False,
            )
            mf.rules[target] = concrete
            return concrete
    return None


def _find_double_colon_rules(target: str, mf: Makefile) -> list[Rule]:
    """Return all :: Rule blocks for target (may be empty)."""
    return list(mf.double_colon_rules.get(target, []))


def _exists(path: str, basedir: str) -> bool:
    """Check existence relative to basedir if the path is not absolute."""
    p = Path(path)
    if p.is_absolute():
        return p.exists()
    return (Path(basedir) / p).exists()


def resolve(target: str, mf: Makefile) -> list[str]:
    """
    Return an ordered list of (target, rule_index) pairs to build, where
    rule_index is None for single-colon rules and an int for :: rules.

    For simplicity the public return value is still list[str] of target
    names — the executor re-looks up the rules.  Double-colon targets
    appear once per :: block, suffixed internally so the executor can tell
    them apart.

    Actually: we return plain target strings.  The executor handles ::
    by checking double_colon_rules directly.
    """
    order: list[str] = []
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(t: str) -> None:
        if t in visited:
            return
        if t in visiting:
            raise RuntimeError(f"Circular dependency detected: '{t}' is part of a cycle")
        visiting.add(t)

        dc_rules = _find_double_colon_rules(t, mf)
        if dc_rules:
            # Resolve prerequisites of every :: block
            for rule in dc_rules:
                for prereq in rule.prerequisites:
                    visit(prereq)
                for prereq in rule.order_only_prerequisites:
                    visit(prereq)
        else:
            rule = _find_rule(t, mf)
            if rule is None:
                if not _exists(t, mf.basedir):
                    raise RuntimeError(
                        f"No rule to make target '{t}' and file does not exist"
                    )
                visiting.discard(t)
                visited.add(t)
                return
            for prereq in rule.prerequisites:
                visit(prereq)
            for prereq in rule.order_only_prerequisites:
                visit(prereq)

        visiting.discard(t)
        visited.add(t)
        order.append(t)

    visit(target)
    return order
