"""Top-level entry point."""

from __future__ import annotations

from pathlib import Path

from .model import Report
from .reconcile import reconcile
from .sources import collect


def check(root: Path) -> Report:
    """Collect every claim in a repository and reconcile them.

    Args:
        root: Repository root.

    Returns:
        A :class:`Report` with the claims found and the contradictions between
        them.
    """
    claims = collect(root)
    return Report(root=root, claims=claims, disagreements=reconcile(claims))
