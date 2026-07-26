"""Core types.

The central idea: every file that mentions a runtime version, a port, a license
or a package version is making a **claim**. Individually each file is valid, so
ordinary linters pass. The bugs live in the gaps *between* files, and finding
them means collecting claims from everywhere and reconciling them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional


class Kind(str, Enum):
    """What a claim is about."""

    PYTHON = "python"
    NODE = "node"
    VERSION = "version"
    LICENSE = "license"
    PACKAGE_MANAGER = "package-manager"
    PORT = "port"


class Role(str, Enum):
    """How binding a claim is.

    This distinction is what stops the tool being naive. A CI matrix testing 3.10
    *and* 3.13 is not a contradiction — it is deliberate. Two pins disagreeing is.
    """

    #: An allowed range: `requires-python = ">=3.10"`, `engines.node`.
    DECLARED = "declared"
    #: A single exact choice: `.python-version`, `FROM python:3.11`, `.nvmrc`.
    PINNED = "pinned"
    #: One of several deliberately exercised: a CI matrix entry.
    TESTED = "tested"
    #: Prose. Wrong prose misleads humans but breaks no build, so it is softer.
    DOCUMENTED = "documented"


class Level(str, Enum):
    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True)
class Claim:
    """One assertion made by one file.

    Attributes:
        kind: What the claim is about.
        value: Normalised value, e.g. ``">=3.10"`` or ``"3.11"`` or ``"MIT"``.
        role: How binding it is.
        source: File the claim came from.
        line: 1-indexed line, for a clickable location.
        raw: The original text, shown to the user so the report is recognisable.
        note: Optional extra context, e.g. which CI job.
    """

    kind: Kind
    value: str
    role: Role
    source: Path
    line: int
    raw: str
    note: Optional[str] = None

    def location(self, root: Optional[Path] = None) -> str:
        path = self.source
        if root is not None:
            try:
                path = self.source.relative_to(root)
            except ValueError:
                pass
        return f"{path.as_posix()}:{self.line}"

    def describe(self, root: Optional[Path] = None) -> str:
        where = self.location(root)
        note = f" ({self.note})" if self.note else ""
        return f"{where}{note}: {self.raw}"


@dataclass(frozen=True)
class Disagreement:
    """A set of claims that cannot all be true."""

    kind: Kind
    summary: str
    claims: tuple[Claim, ...]
    level: Level = Level.ERROR
    fix: Optional[str] = None

    @property
    def code(self) -> str:
        return f"{self.kind.value}-mismatch"

    @property
    def line(self) -> int:
        return min(c.line for c in self.claims) if self.claims else 1


@dataclass
class Report:
    """Everything found in one repository."""

    root: Path
    claims: list[Claim] = field(default_factory=list)
    disagreements: list[Disagreement] = field(default_factory=list)

    @property
    def errors(self) -> int:
        return sum(1 for d in self.disagreements if d.level is Level.ERROR)

    @property
    def warnings(self) -> int:
        return sum(1 for d in self.disagreements if d.level is Level.WARNING)
