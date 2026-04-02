from __future__ import annotations
"""Data model for a parsed Makefile."""
import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Rule:
    target: str
    prerequisites: list[str] = field(default_factory=list)
    order_only_prerequisites: list[str] = field(default_factory=list)
    recipe: list[str] = field(default_factory=list)
    is_phony: bool = False
    is_pattern: bool = False       # True when target contains '%'
    is_double_colon: bool = False  # True for :: rules


@dataclass
class Makefile:
    variables: dict[str, str] = field(default_factory=dict)
    rules: dict[str, Rule] = field(default_factory=dict)       # target -> single-colon Rule
    double_colon_rules: dict[str, list[Rule]] = field(default_factory=dict)  # target -> [Rule, ...]
    pattern_rules: list[Rule] = field(default_factory=list)
    default_target: Optional[str] = None
    phony_targets: set[str] = field(default_factory=set)
    basedir: str = field(default_factory=os.getcwd)
