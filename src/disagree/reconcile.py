"""Decide which sets of claims cannot all be true.

The rules are deliberately asymmetric, because the roles mean different things:

* Two **pins** that name different release lines is always a bug. Only one of
  them can be what actually runs.
* A **tested** version outside a **declared** range is a bug: CI is exercising
  something the package says it does not support, or the package understates
  what it supports.
* Several **tested** versions is normal. A matrix is the point of a matrix.
* **Documented** prose that disagrees is a warning. It misleads people without
  breaking builds, and prose is the most likely thing to be loosely worded.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from .model import Claim, Disagreement, Kind, Level, Role
from .versions import Version, is_range, parse_version, satisfies

_RUNTIMES = (Kind.PYTHON, Kind.NODE)


def reconcile(claims: list[Claim]) -> list[Disagreement]:
    """Find every contradiction in a set of claims."""
    by_kind: dict[Kind, list[Claim]] = defaultdict(list)
    for claim in claims:
        by_kind[claim.kind].append(claim)

    found: list[Disagreement] = []
    for kind in _RUNTIMES:
        found.extend(_reconcile_runtime(kind, by_kind.get(kind, [])))
    found.extend(_reconcile_exact(Kind.VERSION, by_kind.get(Kind.VERSION, []),
                                  "package version"))
    found.extend(_reconcile_exact(Kind.LICENSE, by_kind.get(Kind.LICENSE, []),
                                  "license"))
    found.extend(_reconcile_package_manager(by_kind.get(Kind.PACKAGE_MANAGER, [])))
    return sorted(found, key=lambda d: (d.claims[0].source.as_posix(), d.line))


def _reconcile_runtime(kind: Kind, claims: list[Claim]) -> list[Disagreement]:
    if len(claims) < 2:
        return []

    name = kind.value.capitalize()
    found: list[Disagreement] = []

    pins = [(c, parse_version(c.value)) for c in claims if c.role is Role.PINNED]
    pins = [(c, v) for c, v in pins if v is not None]
    ranges = [c for c in claims if c.role is Role.DECLARED and is_range(c.value)]
    exact_declared = [
        (c, parse_version(c.value))
        for c in claims
        if c.role is Role.DECLARED and not is_range(c.value)
    ]
    pins.extend((c, v) for c, v in exact_declared if v is not None)
    tested = [(c, parse_version(c.value)) for c in claims if c.role is Role.TESTED]
    tested = [(c, v) for c, v in tested if v is not None]

    # 1. Pins that name different release lines.
    lines: dict[tuple[int, int | None], list[Claim]] = defaultdict(list)
    for claim, version in pins:
        lines[(version.major, version.minor)].append(claim)
    if len(lines) > 1 and not _all_compatible([v for _, v in pins]):
        involved = tuple(c for group in lines.values() for c in group)
        found.append(
            Disagreement(
                kind=kind,
                summary=f"{name} version is pinned to different values in different files",
                claims=involved,
                level=Level.ERROR,
                fix="Pick one and update the others; whichever file the runtime "
                    "actually reads is the one that wins at 3am.",
            )
        )

    # 2. Anything outside a declared range.
    for range_claim in ranges:
        violating = [
            claim
            for claim, version in [*pins, *tested]
            if satisfies(version, range_claim.value) is False
        ]
        if violating:
            found.append(
                Disagreement(
                    kind=kind,
                    summary=f"{name} versions in use fall outside the declared "
                            f"range {range_claim.value}",
                    claims=(range_claim, *violating),
                    level=Level.ERROR,
                    fix=f"Either widen {range_claim.value} or move off those versions.",
                )
            )

    # 3. Prose that contradicts the declared range.
    documented = [c for c in claims if c.role is Role.DOCUMENTED]
    for doc in documented:
        doc_version = parse_version(doc.value.lstrip(">=~^ "))
        if doc_version is None:
            continue
        for range_claim in ranges:
            if satisfies(doc_version, range_claim.value) is False:
                found.append(
                    Disagreement(
                        kind=kind,
                        summary=f"the README says {name} {doc.value} but the package "
                                f"declares {range_claim.value}",
                        claims=(doc, range_claim),
                        level=Level.WARNING,
                        fix="Update the README; it is the first thing a new "
                            "contributor follows.",
                    )
                )
    return found


def _all_compatible(versions: list[Version]) -> bool:
    """Whether every version names the same release line."""
    return all(a.same_line(b) for a in versions for b in versions)


def _reconcile_exact(kind: Kind, claims: list[Claim], label: str) -> list[Disagreement]:
    """Values that must match exactly across files."""
    if len(claims) < 2:
        return []

    normalised = defaultdict(list)
    for claim in claims:
        normalised[_normalise(kind, claim.value)].append(claim)
    if len(normalised) < 2:
        return []

    return [
        Disagreement(
            kind=kind,
            summary=f"{label} differs between files: "
                    + ", ".join(sorted(normalised)),
            claims=tuple(c for group in normalised.values() for c in group),
            level=Level.ERROR,
            fix="These are read by different tools, so whichever one your user "
                "hits decides what they see.",
        )
    ]


def _normalise(kind: Kind, value: str) -> str:
    if kind is Kind.LICENSE:
        return value.strip().upper().replace(" ", "-")
    return value.strip()


def _reconcile_package_manager(claims: list[Claim]) -> list[Disagreement]:
    if len(claims) < 2:
        return []
    managers = {c.value for c in claims}
    if len(managers) < 2:
        return []
    return [
        Disagreement(
            kind=Kind.PACKAGE_MANAGER,
            summary="more than one package manager is in play: "
                    + ", ".join(sorted(managers)),
            claims=tuple(claims),
            level=Level.ERROR,
            fix="Delete the stale lockfile. Two lockfiles means two different "
                "dependency trees, and CI installs whichever it finds first.",
        )
    ]
