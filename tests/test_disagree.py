"""Extraction, reconciliation and the role rules.

Every test builds a throwaway repository, so the suite is offline, deterministic
and independent of this repo's own contents.

The rules under test are the reason the tool isn't a naive string comparison:

* several **tested** versions is a matrix, not a bug
* a **pinned** version outside a **declared** range is a bug
* two **pins** naming different release lines is a bug
* **documented** prose that disagrees is a warning, not an error
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from disagree.core import check
from disagree.model import Kind, Level, Role
from disagree.reconcile import reconcile
from disagree.sources import collect
from disagree.versions import Version, parse_constraints, parse_version, satisfies


def build(tmp_path: Path, files: dict[str, str]) -> Path:
    for name, content in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return tmp_path


def kinds(report) -> list[str]:
    return [d.kind.value for d in report.disagreements]


def claims_of(root: Path, kind: Kind):
    return [c for c in collect(root) if c.kind is kind]


MIT = "MIT License\n\nPermission is hereby granted, free of charge, to any person"
APACHE = "Apache License\nVersion 2.0, January 2004\n"


# --------------------------------------------------------------------------- #
# Version comparison
# --------------------------------------------------------------------------- #

class TestVersions:
    @pytest.mark.parametrize(
        "text, expected",
        [("3.11", (3, 11)), ("v18", (18, None)), ("20.10.0", (20, 10)),
         ("3.13-slim", (3, 13)), ("18.x", (18, None))],
    )
    def test_parses_common_forms(self, text, expected):
        version = parse_version(text)
        assert (version.major, version.minor) == expected

    @pytest.mark.parametrize(
        "candidate, expression, expected",
        [
            ("3.11", ">=3.10", True),
            ("3.9", ">=3.10", False),
            ("3.13", ">=3.10,<4", True),
            ("4.1", ">=3.10,<4", False),
            ("18.2", "^18.0.0", True),
            ("19.0", "^18.0.0", False),
            ("20", ">=18", True),
            ("3.11", "3.11", True),
            ("3.12", "3.11", False),
        ],
    )
    def test_satisfies(self, candidate, expression, expected):
        assert satisfies(parse_version(candidate), expression) is expected

    def test_unparseable_constraints_stay_silent(self):
        """Better to say nothing than to invent a contradiction."""
        assert satisfies(Version(3, 11), ">=3.10 || <2") is None
        assert satisfies(Version(3, 11), "3.*") is None
        assert parse_constraints("nonsense") == []

    def test_missing_minor_means_less_specific_not_wrong(self):
        assert Version(20, None).same_line(Version(20, 11))
        assert not Version(20, 1).same_line(Version(20, 2))


# --------------------------------------------------------------------------- #
# Extraction
# --------------------------------------------------------------------------- #

class TestExtraction:
    def test_reads_pinned_runtime_files(self, tmp_path):
        root = build(tmp_path, {".python-version": "3.11\n", ".nvmrc": "v20\n"})
        found = {(c.kind, c.value, c.role) for c in collect(root)}
        assert (Kind.PYTHON, "3.11", Role.PINNED) in found
        assert (Kind.NODE, "v20", Role.PINNED) in found

    def test_reads_dockerfile_base_image_and_ports(self, tmp_path):
        root = build(tmp_path, {"Dockerfile": "FROM python:3.12-slim\nEXPOSE 8000\n"})
        found = {(c.kind, c.value) for c in collect(root)}
        assert (Kind.PYTHON, "3.12") in found
        assert (Kind.PORT, "8000") in found

    def test_ignores_non_language_base_images(self, tmp_path):
        root = build(tmp_path, {"Dockerfile": "FROM nginx:1.25\n"})
        assert claims_of(root, Kind.PYTHON) == []
        assert claims_of(root, Kind.NODE) == []

    def test_reads_package_json(self, tmp_path):
        root = build(tmp_path, {
            "package.json": json.dumps({
                "version": "1.2.3", "license": "MIT",
                "engines": {"node": ">=20"}, "packageManager": "pnpm@9.1.0",
            })
        })
        found = {(c.kind, c.value, c.role) for c in collect(root)}
        assert (Kind.NODE, ">=20", Role.DECLARED) in found
        assert (Kind.VERSION, "1.2.3", Role.PINNED) in found
        assert (Kind.PACKAGE_MANAGER, "pnpm", Role.PINNED) in found

    def test_reads_pyproject_including_tool_targets(self, tmp_path):
        root = build(tmp_path, {
            "pyproject.toml": '[project]\nname = "x"\nversion = "0.2.0"\n'
                              'requires-python = ">=3.11"\n\n'
                              '[tool.ruff]\ntarget-version = "py311"\n'
        })
        python = {(c.value, c.role) for c in claims_of(root, Kind.PYTHON)}
        assert (">=3.11", Role.DECLARED) in python
        assert ("3.11", Role.DECLARED) in python  # py311 normalised

    def test_reads_ci_matrix(self, tmp_path):
        root = build(tmp_path, {
            ".github/workflows/ci.yml":
                "jobs:\n  test:\n    strategy:\n      matrix:\n"
                '        python-version: ["3.10", "3.13"]\n'
                "    steps:\n      - uses: actions/setup-python@v5\n"
                "        with:\n          python-version: ${{ matrix.python-version }}\n"
        })
        tested = {c.value for c in claims_of(root, Kind.PYTHON) if c.role is Role.TESTED}
        assert tested == {"3.10", "3.13"}

    def test_reads_version_from_dunder(self, tmp_path):
        root = build(tmp_path, {"src/pkg/__init__.py": '__version__ = "9.9.9"\n'})
        assert [c.value for c in claims_of(root, Kind.VERSION)] == ["9.9.9"]

    def test_reads_prose_from_readme_but_not_code_blocks(self, tmp_path):
        root = build(tmp_path, {
            "README.md": "Requires Python 3.11 or later.\n\n```bash\npython3.7 app.py\n```\n"
        })
        documented = [c for c in claims_of(root, Kind.PYTHON) if c.role is Role.DOCUMENTED]
        assert [c.value for c in documented] == [">=3.11"]

    def test_identifies_the_actual_license_file(self, tmp_path):
        root = build(tmp_path, {"LICENSE": APACHE})
        assert [c.value for c in claims_of(root, Kind.LICENSE)] == ["Apache-2.0"]

    def test_survives_malformed_files(self, tmp_path):
        """A broken config must not take down the whole run."""
        root = build(tmp_path, {
            "package.json": "{not json",
            "pyproject.toml": "[project\nbroken",
            ".python-version": "3.11\n",
        })
        assert [c.value for c in claims_of(root, Kind.PYTHON)] == ["3.11"]

    def test_skips_dependency_directories(self, tmp_path):
        root = build(tmp_path, {
            "node_modules/pkg/Dockerfile": "FROM python:3.6\n",
            ".python-version": "3.12\n",
        })
        assert [c.value for c in claims_of(root, Kind.PYTHON)] == ["3.12"]


# --------------------------------------------------------------------------- #
# The role rules
# --------------------------------------------------------------------------- #

class TestReconciliation:
    def test_a_ci_matrix_is_not_a_contradiction(self, tmp_path):
        """Testing 3.10 and 3.13 is the point of a matrix."""
        root = build(tmp_path, {
            "pyproject.toml": '[project]\nname="x"\nrequires-python = ">=3.10"\n',
            ".github/workflows/ci.yml":
                '        python-version: ["3.10", "3.13"]\n'
                "          python-version: ${{ matrix.python-version }}\n",
        })
        assert check(root).disagreements == []

    def test_flags_a_pin_outside_the_declared_range(self, tmp_path):
        root = build(tmp_path, {
            "pyproject.toml": '[project]\nname="x"\nrequires-python = ">=3.11"\n',
            ".python-version": "3.9\n",
        })
        report = check(root)
        assert "python" in kinds(report)
        assert report.errors >= 1

    def test_flags_two_pins_on_different_release_lines(self, tmp_path):
        root = build(tmp_path, {".python-version": "3.9\n", "Dockerfile": "FROM python:3.13\n"})
        report = check(root)
        assert kinds(report) == ["python"]
        # Every side of the contradiction must be shown, not just one.
        assert len(report.disagreements[0].claims) == 2

    def test_a_coarser_pin_is_not_a_contradiction(self, tmp_path):
        """`node 20` and `node 20.11` name the same line."""
        root = build(tmp_path, {".nvmrc": "20\n", "Dockerfile": "FROM node:20.11\n"})
        assert check(root).disagreements == []

    def test_readme_prose_is_a_warning_not_an_error(self, tmp_path):
        root = build(tmp_path, {
            "pyproject.toml": '[project]\nname="x"\nrequires-python = ">=3.11"\n',
            "README.md": "Requires Python 3.8 or later.\n",
        })
        report = check(root)
        assert report.errors == 0
        assert report.warnings == 1
        assert report.disagreements[0].level is Level.WARNING

    def test_flags_package_version_drift(self, tmp_path):
        root = build(tmp_path, {
            "pyproject.toml": '[project]\nname="x"\nversion = "2.0.0"\n',
            "src/pkg/__init__.py": '__version__ = "1.4.2"\n',
        })
        report = check(root)
        assert kinds(report) == ["version"]
        assert "1.4.2" in report.disagreements[0].summary

    def test_flags_license_drift(self, tmp_path):
        root = build(tmp_path, {
            "LICENSE": APACHE,
            "package.json": json.dumps({"license": "MIT"}),
        })
        assert kinds(check(tmp_path)) == ["license"]

    def test_license_comparison_is_case_insensitive(self, tmp_path):
        root = build(tmp_path, {
            "LICENSE": MIT,
            "package.json": json.dumps({"license": "mit"}),
        })
        assert check(root).disagreements == []

    def test_flags_two_lockfiles(self, tmp_path):
        root = build(tmp_path, {"package-lock.json": "{}", "yarn.lock": ""})
        report = check(root)
        assert kinds(report) == ["package-manager"]
        assert report.errors == 1

    def test_one_lockfile_is_fine(self, tmp_path):
        root = build(tmp_path, {"package-lock.json": "{}"})
        assert check(root).disagreements == []

    def test_a_single_claim_can_never_contradict(self, tmp_path):
        root = build(tmp_path, {".python-version": "3.11\n"})
        assert check(root).disagreements == []

    def test_agreeing_files_produce_nothing(self, tmp_path):
        root = build(tmp_path, {
            "pyproject.toml": '[project]\nname="x"\nversion="1.0.0"\n'
                              'requires-python = ">=3.11"\nlicense = "MIT"\n',
            ".python-version": "3.11\n",
            "Dockerfile": "FROM python:3.11-slim\n",
            "LICENSE": MIT,
            "src/pkg/__init__.py": '__version__ = "1.0.0"\n',
            "README.md": "Requires Python 3.11 or later.\n",
        })
        assert check(root).disagreements == []

    def test_finds_every_category_at_once(self, tmp_path):
        root = build(tmp_path, {
            "pyproject.toml": '[project]\nname="x"\nversion="2.0.0"\n'
                              'requires-python = ">=3.11"\nlicense = "MIT"\n',
            ".python-version": "3.9\n",
            "src/pkg/__init__.py": '__version__ = "1.4.2"\n',
            "LICENSE": APACHE,
            "package-lock.json": "{}",
            "yarn.lock": "",
        })
        found = set(kinds(check(root)))
        assert {"python", "version", "license", "package-manager"} <= found


class TestReporting:
    def test_claims_carry_a_clickable_location(self, tmp_path):
        root = build(tmp_path, {".python-version": "3.9\n", "Dockerfile": "FROM python:3.13\n"})
        claim = check(root).disagreements[0].claims[0]
        assert ":" in claim.location(root)
        assert not claim.location(root).startswith(str(root))

    def test_output_is_ascii_only(self, tmp_path):
        """A stray arrow crashed the Windows console with UnicodeEncodeError."""
        from disagree.cli import format_text

        root = build(tmp_path, {".python-version": "3.9\n", "Dockerfile": "FROM python:3.13\n"})
        text = format_text(check(root), colour=False, show_claims=True)
        text.encode("cp1252")  # raises if any character cannot be rendered

    def test_json_output_is_valid(self, tmp_path):
        from disagree.cli import format_json

        root = build(tmp_path, {".python-version": "3.9\n", "Dockerfile": "FROM python:3.13\n"})
        payload = json.loads(format_json(check(root)))
        assert payload["summary"]["errors"] == 1
        assert payload["disagreements"][0]["claims"]

    def test_exit_codes(self, tmp_path):
        from disagree.cli import main

        clean = build(tmp_path / "clean", {".python-version": "3.11\n"})
        assert main([str(clean)]) == 0

        broken = build(tmp_path / "broken",
                       {".python-version": "3.9\n", "Dockerfile": "FROM python:3.13\n"})
        assert main([str(broken)]) == 1
        assert main([str(broken), "--ignore", "python"]) == 0
        assert main(["/nonexistent-path-xyz"]) == 2

    def test_strict_promotes_warnings(self, tmp_path):
        from disagree.cli import main

        root = build(tmp_path, {
            "pyproject.toml": '[project]\nname="x"\nrequires-python = ">=3.11"\n',
            "README.md": "Requires Python 3.8 or later.\n",
        })
        assert main([str(root)]) == 0
        assert main([str(root), "--strict"]) == 1


class TestReadmeNoise:
    """A README *about* version mismatches is full of example mismatches.

    These filters exist because this tool's own README tripped its own prose
    check three times before they did.
    """

    def test_ignores_table_rows(self, tmp_path):
        root = build(tmp_path, {
            "pyproject.toml": '[project]\nname="x"\nrequires-python = ">=3.11"\n',
            "README.md": "## Requirements\n\n| role | example |\n"
                         "|---|---|\n| documented | Python 3.8+ in prose |\n",
        })
        assert check(root).disagreements == []

    def test_ignores_quoted_examples(self, tmp_path):
        root = build(tmp_path, {
            "pyproject.toml": '[project]\nname="x"\nrequires-python = ">=3.11"\n',
            "README.md": '## Install\n\nA line saying "Requires Python 3.8+" is an example.\n',
        })
        assert check(root).disagreements == []

    def test_ignores_prose_outside_requirement_sections(self, tmp_path):
        root = build(tmp_path, {
            "pyproject.toml": '[project]\nname="x"\nrequires-python = ">=3.11"\n',
            "README.md": "# Tool\n\n## Changelog\n\nDropped Python 3.8 support.\n",
        })
        assert check(root).disagreements == []

    def test_still_reads_a_real_requirement(self, tmp_path):
        root = build(tmp_path, {
            "pyproject.toml": '[project]\nname="x"\nrequires-python = ">=3.11"\n',
            "README.md": "# Tool\n\n## Requirements\n\nPython 3.8 or later.\n",
        })
        assert kinds(check(root)) == ["python"]

    def test_still_reads_the_opening_paragraph(self, tmp_path):
        root = build(tmp_path, {
            "pyproject.toml": '[project]\nname="x"\nrequires-python = ">=3.11"\n',
            "README.md": "# Tool\n\nNeeds Python 3.8 or later to run.\n",
        })
        assert kinds(check(root)) == ["python"]

    def test_ignores_narration_without_requirement_language(self, tmp_path):
        """"Dockerfile runs Python 3.9" describes a situation, not a requirement."""
        root = build(tmp_path, {
            "pyproject.toml": '[project]\nname="x"\nrequires-python = ">=3.11"\n',
            "README.md": "# Tool\n\nThe Dockerfile runs Python 3.9 in that example.\n",
        })
        assert check(root).disagreements == []

    @pytest.mark.parametrize(
        "sentence",
        ["Requires Python 3.8.", "Needs Python 3.8 to run.", "Python 3.8+",
         "Minimum Python 3.8.", "Tested on Python 3.8."],
    )
    def test_reads_real_requirement_phrasings(self, tmp_path, sentence):
        root = build(tmp_path, {
            "pyproject.toml": '[project]\nname="x"\nrequires-python = ">=3.11"\n',
            "README.md": f"# Tool\n\n{sentence}\n",
        })
        assert kinds(check(root)) == ["python"]
