"""Collect claims from every file that makes one.

Each extractor is best-effort and silent on failure: a malformed Dockerfile is
somebody else's problem, and crashing on it would make the tool unusable on
exactly the messy repositories that need it most.

Line numbers are recovered by searching the raw text after parsing. It is less
elegant than a parser that tracks positions, but `json` and `tomllib` do not
expose them, and a finding you cannot click is a finding you will not fix.
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path
from typing import Any, Callable, Iterable

from .model import Claim, Kind, Role

#: Base images whose tag encodes a language runtime.
_LANGUAGE_IMAGES = {
    "python": Kind.PYTHON,
    "node": Kind.NODE,
    "nodejs": Kind.NODE,
}

_SKIP_DIRS = {
    ".git", "node_modules", "venv", ".venv", "dist", "build", "__pycache__",
    ".tox", ".mypy_cache", ".pytest_cache", "vendor", "target", "site-packages",
}


def _line_of(text: str, needle: str, default: int = 1) -> int:
    """1-indexed line containing ``needle``."""
    index = text.find(needle)
    if index < 0:
        return default
    return text.count("\n", 0, index) + 1


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


# --------------------------------------------------------------------------- #
# Individual file formats
# --------------------------------------------------------------------------- #

def from_version_files(root: Path) -> list[Claim]:
    """`.python-version`, `.nvmrc`, `.node-version`, `runtime.txt`, `.tool-versions`."""
    claims: list[Claim] = []

    simple = {
        ".python-version": Kind.PYTHON,
        ".nvmrc": Kind.NODE,
        ".node-version": Kind.NODE,
    }
    for name, kind in simple.items():
        text = _read(root / name)
        if not text:
            continue
        value = text.strip().splitlines()[0].strip() if text.strip() else ""
        if value:
            claims.append(
                Claim(kind, value, Role.PINNED, root / name, 1, f"{name} = {value}")
            )

    # Heroku's runtime.txt, e.g. `python-3.11.6`.
    text = _read(root / "runtime.txt")
    if text:
        match = re.match(r"\s*(python|nodejs|node)-([\d.]+)", text, re.IGNORECASE)
        if match:
            kind = Kind.PYTHON if match.group(1).lower() == "python" else Kind.NODE
            claims.append(
                Claim(kind, match.group(2), Role.PINNED, root / "runtime.txt", 1,
                      f"runtime.txt = {text.strip()}")
            )

    # asdf / mise, e.g. `python 3.11.6` one per line.
    text = _read(root / ".tool-versions")
    if text:
        for number, line in enumerate(text.splitlines(), start=1):
            parts = line.split()
            if len(parts) < 2:
                continue
            kind = {"python": Kind.PYTHON, "nodejs": Kind.NODE, "node": Kind.NODE}.get(
                parts[0].lower()
            )
            if kind:
                claims.append(
                    Claim(kind, parts[1], Role.PINNED, root / ".tool-versions", number,
                          f".tool-versions = {line.strip()}")
                )
    return claims


def from_package_json(root: Path) -> list[Claim]:
    """`engines`, `packageManager`, `version`, `license` from package.json."""
    path = root / "package.json"
    text = _read(path)
    if text is None:
        return []
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []

    claims: list[Claim] = []
    engines = data.get("engines") or {}

    if isinstance(engines, dict) and engines.get("node"):
        value = str(engines["node"])
        claims.append(
            Claim(Kind.NODE, value, Role.DECLARED, path, _line_of(text, '"node"'),
                  f'engines.node = "{value}"')
        )

    if data.get("packageManager"):
        manager = str(data["packageManager"]).split("@")[0]
        claims.append(
            Claim(Kind.PACKAGE_MANAGER, manager, Role.PINNED, path,
                  _line_of(text, '"packageManager"'),
                  f'packageManager = "{data["packageManager"]}"')
        )

    if data.get("version"):
        claims.append(
            Claim(Kind.VERSION, str(data["version"]), Role.PINNED, path,
                  _line_of(text, '"version"'), f'package.json version = {data["version"]}')
        )

    license_value = data.get("license")
    if isinstance(license_value, str):
        claims.append(
            Claim(Kind.LICENSE, license_value.strip(), Role.DECLARED, path,
                  _line_of(text, '"license"'), f'package.json license = "{license_value}"')
        )
    return claims


def from_pyproject(root: Path) -> list[Claim]:
    """`requires-python`, `version`, `license`, and tool target versions."""
    path = root / "pyproject.toml"
    text = _read(path)
    if text is None:
        return []
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return []

    claims: list[Claim] = []
    project = data.get("project") or {}

    if project.get("requires-python"):
        value = str(project["requires-python"])
        claims.append(
            Claim(Kind.PYTHON, value, Role.DECLARED, path, _line_of(text, "requires-python"),
                  f'requires-python = "{value}"')
        )

    if project.get("version"):
        claims.append(
            Claim(Kind.VERSION, str(project["version"]), Role.PINNED, path,
                  _line_of(text, "version ="), f'pyproject version = {project["version"]}')
        )

    license_value = project.get("license")
    if isinstance(license_value, str):
        claims.append(
            Claim(Kind.LICENSE, license_value.strip(), Role.DECLARED, path,
                  _line_of(text, "license"), f'pyproject license = "{license_value}"')
        )
    elif isinstance(license_value, dict) and isinstance(license_value.get("text"), str):
        claims.append(
            Claim(Kind.LICENSE, license_value["text"].strip(), Role.DECLARED, path,
                  _line_of(text, "license"), f'pyproject license = "{license_value["text"]}"')
        )

    # Tooling that pins the Python it targets. These drift from requires-python
    # constantly, and the failure is confusing: code that type-checks but will
    # not run.
    tools = data.get("tool") or {}
    for tool, key in (("ruff", "target-version"), ("black", "target-version"),
                      ("mypy", "python_version")):
        section = tools.get(tool) or {}
        raw = section.get(key)
        values = raw if isinstance(raw, list) else [raw] if raw else []
        for value in values:
            version = re.sub(r"^py", "", str(value))
            if len(version) >= 2 and version.isdigit():
                version = f"{version[0]}.{version[1:]}"  # py311 → 3.11
            claims.append(
                Claim(Kind.PYTHON, version, Role.DECLARED, path,
                      _line_of(text, f"[tool.{tool}]"),
                      f"tool.{tool}.{key} = \"{value}\"", note=tool)
            )
    return claims


def from_dockerfiles(root: Path) -> list[Claim]:
    """Base-image runtimes and EXPOSEd ports."""
    claims: list[Claim] = []
    for path in _walk(root, lambda p: p.name.lower().startswith("dockerfile")):
        text = _read(path)
        if text is None:
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            from_match = re.match(r"\s*FROM\s+(?:--\S+\s+)*([^\s:]+):?([^\s]*)", line, re.I)
            if from_match:
                image = from_match.group(1).rsplit("/", 1)[-1].lower()
                kind = _LANGUAGE_IMAGES.get(image)
                tag = from_match.group(2)
                if kind and tag and tag[0].isdigit():
                    claims.append(
                        Claim(kind, tag.split("-")[0], Role.PINNED, path, number,
                              f"FROM {from_match.group(1)}:{tag}")
                    )
            expose = re.match(r"\s*EXPOSE\s+(\d+)", line, re.I)
            if expose:
                claims.append(
                    Claim(Kind.PORT, expose.group(1), Role.PINNED, path, number,
                          f"EXPOSE {expose.group(1)}")
                )
    return claims


def from_workflows(root: Path) -> list[Claim]:
    """Runtime versions from GitHub Actions `setup-python` / `setup-node`.

    Parsed with a targeted scan rather than a YAML library: the shapes that
    matter (`python-version: "3.11"`, `python-version: [3.10, 3.13]`, and a
    `matrix.python` list) are narrow, and the alternative is a dependency.
    Anything unrecognised is skipped rather than guessed at.
    """
    claims: list[Claim] = []
    workflows = root / ".github" / "workflows"
    if not workflows.is_dir():
        return claims

    key_kinds = {"python-version": Kind.PYTHON, "node-version": Kind.NODE}

    for path in sorted(workflows.glob("*.y*ml")):
        text = _read(path)
        if text is None:
            continue
        lines = text.splitlines()
        # Matrix definitions the version keys may point at, e.g. `python: [...]`.
        matrix: dict[str, list[tuple[str, int]]] = {}

        for number, line in enumerate(lines, start=1):
            inline = re.match(r"\s*(python|node|node-version|python-version)\s*:\s*\[(.+)\]", line)
            if inline:
                name = inline.group(1)
                values = [
                    (v.strip().strip("\"'"), number)
                    for v in inline.group(2).split(",")
                    if v.strip()
                ]
                matrix[name] = values

        for number, line in enumerate(lines, start=1):
            for key, kind in key_kinds.items():
                match = re.match(rf"\s*{key}\s*:\s*(.+)", line)
                if not match:
                    continue
                value = match.group(1).strip().strip("\"'")

                if value.startswith("["):
                    for item in re.findall(r"[\"']?([\d.]+)[\"']?", value):
                        claims.append(
                            Claim(kind, item, Role.TESTED, path, number,
                                  f"{key}: {value}", note=path.stem)
                        )
                    continue

                reference = re.match(r"\$\{\{\s*matrix\.([\w-]+)\s*\}\}", value)
                if reference:
                    for item, item_line in matrix.get(reference.group(1), []):
                        if re.match(r"^[\d.]+$", item):
                            claims.append(
                                Claim(kind, item, Role.TESTED, path, item_line,
                                      f"matrix {reference.group(1)}: {item}", note=path.stem)
                            )
                    continue

                if re.match(r"^[\d.]+$", value):
                    claims.append(
                        Claim(kind, value, Role.TESTED, path, number,
                              f"{key}: {value}", note=path.stem)
                    )
    return claims


def from_lockfiles(root: Path) -> list[Claim]:
    """Which package manager the repository actually uses."""
    lockfiles = {
        "package-lock.json": "npm",
        "yarn.lock": "yarn",
        "pnpm-lock.yaml": "pnpm",
        "bun.lockb": "bun",
        "bun.lock": "bun",
    }
    return [
        Claim(Kind.PACKAGE_MANAGER, manager, Role.PINNED, root / name, 1,
              f"{name} is present")
        for name, manager in lockfiles.items()
        if (root / name).exists()
    ]


#: Headings under which a version number is a real requirement rather than an
#: aside. Outside these (and the opening paragraph) a number in prose is usually
#: an example, a changelog entry or a comparison.
# No trailing \b: `\brequirement\b` does not match "Requirements", and
# `\binstall\b` does not match "Installation" - the two most common headings.
_REQUIREMENT_HEADING = re.compile(
    r"^\s{0,3}#{1,6}\s*.*\b(requirement|prerequisit|install|setup|getting started|"
    r"quick ?start|depend|environment|usage|running)",
    re.IGNORECASE,
)
_ANY_HEADING = re.compile(r"^\s{0,3}(?P<level>#{1,6})\s")

#: Language that turns a version number into a stated requirement.
_REQUIREMENT_WORD = re.compile(
    r"\b(require|requires|required|need|needs|minimum|min|at least|"
    r"supports?|targets?|built (?:for|with)|tested (?:on|with)|works? (?:on|with))\b",
    re.IGNORECASE,
)


def from_readme(root: Path) -> list[Claim]:
    """Prose claims such as "Python 3.11+" or "Node 18 or later".

    Prose is the noisiest source by far, so it is read conservatively. A README
    *about* version mismatches is full of example version mismatches — this tool's
    own README flagged three of its own illustrations before the filters below
    existed.

    A line is only read as a claim when it is:

    * outside a code fence (commands are not prose),
    * not a table row (tables usually document options, not requirements),
    * not quoted (a quoted version is being *mentioned*, not asserted), and
    * in the opening paragraph or under a requirements-ish heading.
    """
    for name in ("README.md", "readme.md", "README.markdown"):
        path = root / name
        text = _read(path)
        if text is None:
            continue

        claims: list[Claim] = []
        in_fence = False
        in_requirements = True  # the opening paragraph counts
        seen_section = False

        for number, line in enumerate(text.splitlines(), start=1):
            if re.match(r"\s*(```|~~~)", line):
                in_fence = not in_fence
                continue
            if in_fence:
                continue

            heading = _ANY_HEADING.match(line)
            if heading:
                # The document title is not a section - the paragraph beneath it
                # is still the opening blurb, which is where a project most often
                # states what it needs to run.
                if heading.group("level") == "#" and not seen_section:
                    continue
                seen_section = True
                in_requirements = bool(_REQUIREMENT_HEADING.match(line))
                continue
            if not in_requirements:
                continue
            if line.lstrip().startswith(("|", ">")):
                continue  # table rows and blockquotes are illustration, not claim

            # Strip quoted spans: `"Requires Python 3.8+"` is an example being
            # discussed, not this project's requirement.
            unquoted = re.sub(r"[\"'][^\"']*[\"']", " ", line)

            for kind, word in ((Kind.PYTHON, "python"), (Kind.NODE, "node")):
                match = re.search(
                    rf"\b{word}\s*(?:v|version\s*)?(\d+(?:\.\d+)?)\s*"
                    r"(\+|or later|or newer|or above)?",
                    unquoted, re.IGNORECASE,
                )
                if not match:
                    continue

                # A bare version in a sentence is usually narration - "Dockerfile
                # runs Python 3.9" describes a situation rather than declaring a
                # requirement. Insist on either a range marker or requirement
                # language nearby.
                lead = unquoted[max(0, match.start() - 40):match.start()]
                if not match.group(2) and not _REQUIREMENT_WORD.search(lead):
                    continue

                value = match.group(1)
                claims.append(
                    Claim(kind, f">={value}" if match.group(2) else value,
                          Role.DOCUMENTED, path, number, line.strip()[:90])
                )
        return claims
    return []


def from_license_file(root: Path) -> list[Claim]:
    """Identify the actual LICENSE, so metadata can be checked against it."""
    from .spdx import identify

    for name in ("LICENSE", "LICENSE.md", "LICENSE.txt", "LICENCE", "COPYING"):
        path = root / name
        text = _read(path)
        if text is None:
            continue
        identifier = identify(text)
        if identifier:
            return [Claim(Kind.LICENSE, identifier, Role.PINNED, path, 1,
                          f"{name} is {identifier}")]
    return []


def from_python_dunder(root: Path) -> list[Claim]:
    """`__version__` in a package, which drifts from pyproject constantly."""
    claims: list[Claim] = []
    for path in _walk(root, lambda p: p.name == "__init__.py"):
        text = _read(path)
        if text is None:
            continue
        match = re.search(r'^__version__\s*=\s*[\'"]([^\'"]+)[\'"]', text, re.MULTILINE)
        if match:
            claims.append(
                Claim(Kind.VERSION, match.group(1), Role.PINNED, path,
                      _line_of(text, "__version__"), f'__version__ = "{match.group(1)}"')
            )
    return claims


def _walk(root: Path, predicate: Callable[[Path], bool], limit: int = 400) -> Iterable[Path]:
    """Yield matching files, skipping dependency and build directories."""
    count = 0
    for path in sorted(root.rglob("*")):
        if count >= limit:
            return
        if not path.is_file() or any(part in _SKIP_DIRS for part in path.parts):
            continue
        if predicate(path):
            count += 1
            yield path


#: Every extractor, run in order.
EXTRACTORS: tuple[Callable[[Path], list[Claim]], ...] = (
    from_version_files,
    from_package_json,
    from_pyproject,
    from_dockerfiles,
    from_workflows,
    from_lockfiles,
    from_readme,
    from_license_file,
    from_python_dunder,
)


def collect(root: Path) -> list[Claim]:
    """Gather every claim the repository makes about itself."""
    claims: list[Claim] = []
    for extractor in EXTRACTORS:
        try:
            claims.extend(extractor(root))
        except Exception:  # noqa: BLE001 - one bad file must not kill the run
            continue
    return claims
