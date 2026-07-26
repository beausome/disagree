"""Identify a license from its text.

Only needs to be good enough to tell MIT from Apache from GPL, which is a far
easier problem than full SPDX matching and needs no data files.
"""

from __future__ import annotations

_SIGNATURES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("AGPL-3.0", ("gnu affero general public license",)),
    ("LGPL-3.0", ("gnu lesser general public license",)),
    ("GPL-3.0", ("gnu general public license", "version 3")),
    ("GPL-2.0", ("gnu general public license", "version 2")),
    ("Apache-2.0", ("apache license", "version 2.0")),
    ("MPL-2.0", ("mozilla public license", "2.0")),
    ("BSD-3-Clause", ("redistributions of source code", "neither the name")),
    ("BSD-2-Clause", ("redistributions of source code",)),
    ("Unlicense", ("this is free and unencumbered software",)),
    ("ISC", ("permission to use, copy, modify, and/or distribute",)),
    ("MIT", ("permission is hereby granted, free of charge",)),
)


def identify(text: str) -> str | None:
    """Return an SPDX-ish identifier, or ``None`` if unrecognised."""
    lowered = " ".join(text.lower().split())
    for identifier, markers in _SIGNATURES:
        if all(marker in lowered for marker in markers):
            return identifier
    return None
