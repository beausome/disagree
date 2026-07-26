"""Version and constraint comparison.

Deliberately small. Full PEP 440 / semver range semantics are not needed to
answer the only question this tool asks: *can all of these claims be true at
once?* What matters is comparing ``major.minor`` and deciding whether a pinned
version satisfies a declared range.

Being approximate here is a choice, and it errs toward silence: anything that
cannot be parsed confidently is dropped rather than reported, because a version
checker that cries wolf about exotic constraint syntax would be turned off within
a day.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

#: A bare version like 3.11, 18, or 20.10.0 (trailing junk such as `-slim` ok).
_VERSION = re.compile(r"^v?(\d+)(?:\.(\d+))?(?:\.(\d+))?")

#: A single comparator such as `>=3.10`, `^18.0.0`, `~=3.11`, `!=3.9`.
_COMPARATOR = re.compile(r"\s*(>=|<=|==|!=|~=|>|<|\^|~)?\s*v?(\d+(?:\.\d+)*)")


@dataclass(frozen=True)
class Version:
    """A major/minor pair. Patch is captured but never compared.

    Patch-level disagreement between a Dockerfile and a `.python-version` is
    noise; a major/minor difference is the bug people actually hit.
    """

    major: int
    minor: Optional[int] = None

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}" if self.minor is not None else str(self.major)

    @property
    def key(self) -> tuple[int, int]:
        return (self.major, self.minor if self.minor is not None else 0)

    def same_line(self, other: "Version") -> bool:
        """Whether two versions name the same release line.

        When one side omits the minor (``node 20`` vs ``node 20.11``) the major
        alone decides — the coarser claim is simply less specific, not wrong.
        """
        if self.major != other.major:
            return False
        if self.minor is None or other.minor is None:
            return True
        return self.minor == other.minor


def parse_version(text: str) -> Optional[Version]:
    """Parse a bare version. Returns ``None`` when there isn't one."""
    match = _VERSION.match(text.strip())
    if not match:
        return None
    major = int(match.group(1))
    minor = int(match.group(2)) if match.group(2) is not None else None
    return Version(major, minor)


@dataclass(frozen=True)
class Constraint:
    """A parsed range such as ``>=3.10`` or ``^18``."""

    op: str
    version: Version

    def allows(self, candidate: Version) -> bool:
        """Whether ``candidate`` satisfies this constraint."""
        a, b = candidate.key, self.version.key
        if self.op in (">=", "~=", "^", "~"):
            # ^ and ~ additionally cap the upper end; that is handled below.
            if a < b:
                return False
            if self.op == "^":
                return candidate.major == self.version.major
            if self.op in ("~", "~="):
                return candidate.major == self.version.major
            return True
        if self.op == ">":
            return a > b
        if self.op == "<=":
            return a <= b
        if self.op == "<":
            return a < b
        if self.op == "!=":
            return not candidate.same_line(self.version)
        return candidate.same_line(self.version)  # "==" or bare


def parse_constraints(text: str) -> list[Constraint]:
    """Parse a constraint expression into comparators.

    Handles the common forms across ecosystems: ``">=3.10"``, ``">=3.10,<4"``,
    ``"^18.0.0"``, ``">=18 <21"``, ``"3.11"``. Wildcards (``3.*``), ``||``
    alternatives and other exotica return an empty list, which callers treat as
    "no opinion" rather than as a conflict.
    """
    text = text.strip()
    if not text or "*" in text or "x" in text.lower() or "||" in text:
        return []

    constraints: list[Constraint] = []
    for part in re.split(r"[,\s]+", text):
        if not part:
            continue
        match = _COMPARATOR.match(part)
        if not match:
            return []
        version = parse_version(match.group(2))
        if version is None:
            return []
        constraints.append(Constraint(match.group(1) or "==", version))
    return constraints


def satisfies(candidate: Version, expression: str) -> Optional[bool]:
    """Whether ``candidate`` satisfies every comparator in ``expression``.

    Returns ``None`` when the expression could not be parsed, so the caller can
    stay quiet instead of guessing.
    """
    constraints = parse_constraints(expression)
    if not constraints:
        return None
    return all(c.allows(candidate) for c in constraints)


def is_range(expression: str) -> bool:
    """Whether an expression describes a range rather than a single version."""
    return any(c.op not in ("==",) for c in parse_constraints(expression))
