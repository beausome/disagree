"""disagree — find where your repository contradicts itself.

Linters check one file at a time. Nothing checks that your files agree with each
other, so contradictions accumulate silently: a Dockerfile on Python 3.9 under a
pyproject that requires 3.11, a README promising Node 18 while CI tests 22, two
lockfiles from two package managers. Every file is individually valid, so every
linter passes, and the repository still misleads whoever reads it next.
"""

from __future__ import annotations

__version__ = "0.1.3"

from .core import check
from .model import Claim, Disagreement, Kind, Level, Report, Role

__all__ = ["check", "Claim", "Disagreement", "Kind", "Level", "Report", "Role", "__version__"]
