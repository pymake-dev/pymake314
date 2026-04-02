from __future__ import annotations
"""
Parse a Makefile into a Makefile model.

Supports:
  - Variables: VAR = value, VAR := value, VAR ?= value, VAR += value
  - Variable references: $(VAR) and ${VAR}
  - Rules with prerequisites and tab-indented recipes
  - .PHONY declarations
  - Pattern rules with %
  - Line continuation with backslash
  - Comments (#)
  - include directives (best-effort)
  - Automatic variables in recipes: $@, $<, $^, $*, $(@D), $(@F)
"""
import os
import re
from pathlib import Path
from .model import Makefile, Rule


# Variable assignment operators
_VAR_RE = re.compile(r'^([A-Za-z_][A-Za-z0-9_]*)[ \t]*(=|:=|::=|!=|\?=|\+=)[ \t]*(.*)')
# Rule line: target(s) : prerequisites
_RULE_RE = re.compile(r'^([^:#=\t][^:=]*)(::?)(?!=)(.*)')
# Detect second colon in prereqs field — marks a static pattern rule
_STATIC_PAT_RE = re.compile(r'(?<!:):(?![:=])')


def _join_continuations(lines: list[str]) -> list[str]:
    """Merge lines ending with backslash."""
    result = []
    buf = []
    for line in lines:
        if line.endswith('\\\n'):
            buf.append(line[:-2])
        elif line.endswith('\\'):
            buf.append(line[:-1])
        else:
            if buf:
                buf.append(line)
                result.append(' '.join(buf))
                buf = []
            else:
                result.append(line)
    if buf:
        result.append(' '.join(buf))
    return result


def _strip_comment(line: str) -> str:
    """Remove inline comments, preserving # inside variable values."""
    # A '#' not preceded by a backslash starts a comment
    i = 0
    while i < len(line):
        if line[i] == '\\':
            i += 2
            continue
        if line[i] == '#':
            return line[:i].rstrip()
        i += 1
    return line


def expand_variables(text: str, variables: dict[str, str], auto: dict[str, str] | None = None) -> str:
    """Recursively expand $(VAR) and ${VAR} references."""
    env = dict(variables)
    if auto:
        env.update(auto)

    max_passes = 20
    for _ in range(max_passes):
        expanded = _expand_once(text, env)
        if expanded == text:
            break
        text = expanded
    return text


def _expand_once(text: str, env: dict[str, str]) -> str:
    result = []
    i = 0
    while i < len(text):
        if text[i] == '$' and i + 1 < len(text):
            nxt = text[i + 1]
            if nxt == '$':
                result.append('$')
                i += 2
            elif nxt == '@':
                result.append(env.get('@', '$@'))
                i += 2
            elif nxt == '<':
                result.append(env.get('<', '$<'))
                i += 2
            elif nxt == '^':
                result.append(env.get('^', '$^'))
                i += 2
            elif nxt == '*':
                result.append(env.get('*', '$*'))
                i += 2
            elif nxt == '|':
                result.append(env.get('|', '$|'))
                i += 2
            elif nxt in '(':
                end = text.find(')', i + 2)
                if end == -1:
                    result.append(text[i:])
                    i = len(text)
                else:
                    key = text[i + 2:end]
                    # Handle $(VAR:pat=repl) substitution references
                    if ':' in key and '=' in key.split(':', 1)[1]:
                        varname, subst = key.split(':', 1)
                        pat, repl = subst.split('=', 1)
                        pat, repl = pat.strip(), repl.strip()
                        val = env.get(varname.strip(), '')
                        if '%' in pat:
                            # Pattern subst: $(VAR:%.c=%.o)
                            ppfx, psfx = pat.split('%', 1)
                            rpfx, rsfx = (repl.split('%', 1) if '%' in repl else ('', repl))
                            def _pr(w, a=ppfx, b=psfx, c=rpfx, d=rsfx):
                                if w.startswith(a) and w.endswith(b):
                                    stem = w[len(a): len(w)-len(b) if b else len(w)]
                                    return c + stem + d
                                return w
                            val = ' '.join(_pr(w) for w in val.split())
                        else:
                            # Suffix subst: $(VAR:.c=.o) — replace suffix per word
                            val = ' '.join(
                                w[:-len(pat)] + repl if (pat and w.endswith(pat)) else w
                                for w in val.split())
                        result.append(val)
                    else:
                        result.append(env.get(key.strip(), ''))
                    i = end + 1
            elif nxt == '{':
                end = text.find('}', i + 2)
                if end == -1:
                    result.append(text[i:])
                    i = len(text)
                else:
                    key = text[i + 2:end].strip()
                    result.append(env.get(key, ''))
                    i = end + 1
            else:
                result.append(text[i])
                i += 1
        else:
            result.append(text[i])
            i += 1
    return ''.join(result)



def _split_order_only(prereqs_raw: str) -> tuple[list[str], list[str]]:
    """
    Split a prerequisites string on the first bare '|' into
    (normal_prereqs, order_only_prereqs).
    Returns two word lists; order_only is empty if '|' is absent.
    """
    if '|' in prereqs_raw:
        normal_part, _, oo_part = prereqs_raw.partition('|')
        return normal_part.split(), oo_part.split()
    return prereqs_raw.split(), []


def _static_stem(pattern: str, target: str) -> str | None:
    """Extract the % stem from `target` given `pattern`. Returns None if no match."""
    if '%' not in pattern:
        return target if target == pattern else None
    prefix, suffix = pattern.split('%', 1)
    if target.startswith(prefix) and (not suffix or target.endswith(suffix)):
        return target[len(prefix): len(target) - len(suffix) if suffix else len(target)]
    return None


def parse(makefile_path: str | Path, variables: dict[str, str] | None = None) -> Makefile:
    """Parse a Makefile and return a Makefile model."""
    path = Path(makefile_path)
    if not path.exists():
        raise FileNotFoundError(f"Makefile not found: {path}")

    mf = Makefile(basedir=str(path.resolve().parent))
    # Seed with environment variables and any overrides
    mf.variables.update({k: v for k, v in os.environ.items()})
    if variables:
        mf.variables.update(variables)

    raw_lines = path.read_text(encoding='utf-8', errors='replace').splitlines(keepends=True)
    lines = _join_continuations(raw_lines)

    current_rules: list[Rule] = []  # all rules sharing the current recipe block

    for raw_line in lines:
        # Preserve tabs for recipe detection before stripping
        is_recipe = raw_line.startswith('\t')
        line = raw_line.rstrip('\n\r')
        stripped = _strip_comment(line)

        # Recipe line — belongs to all rules in current group
        if is_recipe and current_rules:
            cmd = stripped.lstrip('\t')
            if cmd:
                for _r in current_rules:
                    _r.recipe.append(cmd)
            continue

        # Blank / comment-only
        if not stripped.strip():
            if not is_recipe:
                current_rules = []
            continue

        stripped = stripped.strip()

        # include directive
        if stripped.startswith('include ') or stripped.startswith('-include '):
            silent = stripped.startswith('-')
            inc_path = Path(path.parent / expand_variables(stripped.split(None, 1)[1], mf.variables))
            if inc_path.exists():
                inc_mf = parse(inc_path, mf.variables)
                mf.variables.update(inc_mf.variables)
                mf.rules.update(inc_mf.rules)
                mf.pattern_rules.extend(inc_mf.pattern_rules)
                mf.phony_targets.update(inc_mf.phony_targets)
            elif not silent:
                raise FileNotFoundError(f"include not found: {inc_path}")
            continue

        # Variable export / unexport (ignore directive, but parse var)
        if stripped.startswith(('export ', 'unexport ')):
            stripped = stripped.split(None, 1)[1] if ' ' in stripped else ''
            if not stripped:
                continue

        # Variable assignment
        m = _VAR_RE.match(stripped)
        if m:
            name, op, value = m.group(1), m.group(2), m.group(3)
            current_rules = []
            value = expand_variables(value, mf.variables) if op in (':=', '::=') else value
            if op in ('=', ':=', '::='):
                mf.variables[name] = value
            elif op == '?=':
                mf.variables.setdefault(name, value)
            elif op == '+=':
                existing = mf.variables.get(name, '')
                mf.variables[name] = (existing + ' ' + value).strip() if existing else value
            elif op == '!=':
                import subprocess
                result = subprocess.run(value, shell=True, capture_output=True, text=True)
                mf.variables[name] = result.stdout.strip()
            continue

        # Rule line
        m = _RULE_RE.match(stripped)
        if m:
            targets_raw = expand_variables(m.group(1).strip(), mf.variables)
            sep = m.group(2)                 # ':' or '::'
            prereqs_raw = expand_variables((m.group(3) or '').strip(), mf.variables)
            targets = targets_raw.split()
            is_double_colon = (sep == '::')

            # .PHONY
            if '.PHONY' in targets:
                mf.phony_targets.update(prereqs_raw.split())
                current_rules = []
                continue

            # .DEFAULT, .SUFFIXES, etc. — skip special targets silently
            if any(t.startswith('.') and t.isupper() for t in targets):
                current_rules = []
                continue

            # ── Static pattern rule detection ─────────────────────────────
            # A static pattern rule has a second bare ':' inside prereqs_raw:
            #   targets : target-pattern : prereq-patterns
            # e.g.  foo.o bar.o : %.o : %.c common.h
            if _STATIC_PAT_RE.search(prereqs_raw):
                target_pat, _, prereq_pats_raw = prereqs_raw.partition(':')
                target_pat = target_pat.strip()
                _sp_normal, _sp_oo = _split_order_only(prereq_pats_raw.strip())
                prereq_pats = _sp_normal
                oo_pats = _sp_oo
                static_rules: list[Rule] = []
                for target in targets:
                    # Extract stem by matching target against target_pat
                    stem = _static_stem(target_pat, target)
                    if stem is None:
                        raise ValueError(
                            f"Static pattern rule: target '{target}' does not match "
                            f"pattern '{target_pat}'"
                        )
                    # Expand prereq patterns with the stem
                    concrete_prereqs = [p.replace('%', stem) for p in prereq_pats]
                    concrete_oo = [p.replace('%', stem) for p in oo_pats]
                    rule = mf.rules.get(target)
                    if rule is None:
                        rule = Rule(
                            target=target,
                            prerequisites=concrete_prereqs,
                            order_only_prerequisites=concrete_oo,
                            is_phony=target in mf.phony_targets,
                            is_pattern=False,
                            is_double_colon=False,
                        )
                        mf.rules[target] = rule
                    else:
                        rule.prerequisites.extend(concrete_prereqs)
                        rule.order_only_prerequisites.extend(concrete_oo)
                    if mf.default_target is None and not target.startswith('.'):
                        mf.default_target = target
                    static_rules.append(rule)
                current_rules = static_rules
                continue
            # ── End static pattern rule ───────────────────────────────────

            prereqs, order_only = _split_order_only(prereqs_raw)

            for target in targets:
                is_pattern = '%' in target

                # Validate: a target cannot mix single- and double-colon rules
                if is_double_colon and target in mf.rules:
                    raise ValueError(
                        f"Target '{target}' has both single-colon and double-colon rules"
                    )
                if not is_double_colon and target in mf.double_colon_rules:
                    raise ValueError(
                        f"Target '{target}' has both single-colon and double-colon rules"
                    )

                if is_double_colon:
                    # Each :: block is a separate Rule stored in a list
                    rule = Rule(
                        target=target,
                        prerequisites=prereqs,
                        order_only_prerequisites=order_only,
                        is_phony=False,
                        is_pattern=is_pattern,
                        is_double_colon=True,
                    )
                    mf.double_colon_rules.setdefault(target, []).append(rule)
                    if is_pattern:
                        mf.pattern_rules.append(rule)
                else:
                    rule = mf.rules.get(target)
                    if rule is None:
                        rule = Rule(
                            target=target,
                            prerequisites=prereqs,
                            order_only_prerequisites=order_only,
                            is_phony=target in mf.phony_targets,
                            is_pattern=is_pattern,
                            is_double_colon=False,
                        )
                        mf.rules[target] = rule
                        if is_pattern:
                            mf.pattern_rules.append(rule)
                    else:
                        # Second single-colon line for same target: merge prereqs
                        rule.prerequisites.extend(prereqs)
                        rule.order_only_prerequisites.extend(order_only)

                if mf.default_target is None and not is_pattern and not target.startswith('.'):
                    mf.default_target = target

                current_rules = [rule]
            continue

    # Mark phony targets
    for name in mf.phony_targets:
        if name in mf.rules:
            mf.rules[name].is_phony = True

    return mf
