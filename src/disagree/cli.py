"""Command line interface.

The report is built around showing *every* side of a contradiction. Saying
"Python version mismatch" would be useless; the user needs to see all four files
that disagree, so they can decide which one is right.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Sequence

from . import __version__
from .core import check
from .model import Kind, Level, Report

_COLOURS = {"error": "\033[31m", "warning": "\033[33m", "dim": "\033[2m",
            "bold": "\033[1m", "reset": "\033[0m"}


def _use_colour(stream) -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    return hasattr(stream, "isatty") and stream.isatty()


def _paint(text: str, colour: str, enabled: bool) -> str:
    return f"{_COLOURS[colour]}{text}{_COLOURS['reset']}" if enabled else text


def format_text(report: Report, colour: bool, show_claims: bool) -> str:
    lines: list[str] = []

    for disagreement in report.disagreements:
        tag = _paint(disagreement.level.value, disagreement.level.value, colour)
        title = _paint(disagreement.summary, "bold", colour)
        lines.append(f"{tag} {title}  [{disagreement.code}]")
        for claim in disagreement.claims:
            lines.append(f"    {claim.describe(report.root)}")
        if disagreement.fix:
            # ASCII only: the Windows console defaults to cp1252 and a stray arrow
            # crashes the whole run with UnicodeEncodeError.
            lines.append(_paint(f"    fix: {disagreement.fix}", "dim", colour))
        lines.append("")

    if show_claims:
        lines.append(_paint("Claims found:", "bold", colour))
        for kind in Kind:
            group = [c for c in report.claims if c.kind is kind]
            if not group:
                continue
            lines.append(f"  {kind.value}:")
            for claim in group:
                lines.append(
                    f"    {claim.value:<12} {_paint(claim.role.value, 'dim', colour)}"
                    f"  {claim.location(report.root)}"
                )
        lines.append("")

    sources = len({c.source for c in report.claims})
    if not report.disagreements:
        lines.append(
            f"No contradictions across {len(report.claims)} claims in {sources} files."
        )
    else:
        lines.append(
            f"{report.errors} error{'' if report.errors == 1 else 's'}, "
            f"{report.warnings} warning{'' if report.warnings == 1 else 's'} "
            f"from {len(report.claims)} claims in {sources} files."
        )
    return "\n".join(lines)


def format_json(report: Report) -> str:
    return json.dumps(
        {
            "version": __version__,
            "summary": {"errors": report.errors, "warnings": report.warnings},
            "disagreements": [
                {
                    "code": d.code,
                    "kind": d.kind.value,
                    "level": d.level.value,
                    "summary": d.summary,
                    "fix": d.fix,
                    "claims": [
                        {
                            "value": c.value,
                            "role": c.role.value,
                            "file": c.location(report.root).rsplit(":", 1)[0],
                            "line": c.line,
                            "raw": c.raw,
                        }
                        for c in d.claims
                    ],
                }
                for d in report.disagreements
            ],
        },
        indent=2,
    )


def format_github(report: Report) -> str:
    """Workflow commands, annotating the first file of each contradiction."""
    out: list[str] = []
    for disagreement in report.disagreements:
        first = disagreement.claims[0]
        others = "; ".join(c.describe(report.root) for c in disagreement.claims[1:])
        message = f"{disagreement.summary}. Also claimed at: {others}"
        out.append(
            f"::{disagreement.level.value} "
            f"file={first.location(report.root).rsplit(':', 1)[0]},line={first.line},"
            f"title=disagree ({disagreement.code})::{message}"
        )
    return "\n".join(out)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="disagree",
        description="Find where your repository contradicts itself.",
    )
    parser.add_argument("path", nargs="?", default=".", type=Path,
                        help="Repository root (default: .)")
    parser.add_argument("--format", choices=("text", "json", "github"), default="text",
                        help="Output format (default: text)")
    parser.add_argument("--strict", action="store_true",
                        help="Fail on warnings as well as errors")
    parser.add_argument("--show-claims", action="store_true",
                        help="List every claim found, even where they agree")
    parser.add_argument("--ignore", action="append", default=[], metavar="KIND",
                        help=f"Skip a claim kind: {', '.join(k.value for k in Kind)}")
    parser.add_argument("--version", action="version", version=f"disagree {__version__}")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    root = args.path.resolve()
    if not root.is_dir():
        print(f"disagree: {args.path} is not a directory", file=sys.stderr)
        return 2

    report = check(root)
    if args.ignore:
        ignored = {i.lower() for i in args.ignore}
        report.disagreements = [
            d for d in report.disagreements if d.kind.value not in ignored
        ]

    if args.format == "json":
        print(format_json(report))
    elif args.format == "github":
        output = format_github(report)
        if output:
            print(output)
        print(format_text(report, False, args.show_claims), file=sys.stderr)
    else:
        print(format_text(report, _use_colour(sys.stdout), args.show_claims))

    if report.errors:
        return 1
    if args.strict and report.warnings:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
