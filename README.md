# disagree

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Zero dependencies](https://img.shields.io/badge/dependencies-none-brightgreen.svg)](pyproject.toml)

**Find where your repository contradicts itself.**

Linters check one file at a time. Nothing checks that your files agree with *each
other* — so contradictions pile up silently:

- `Dockerfile` runs Python 3.9, `pyproject.toml` says `requires-python = ">=3.11"`
- `.nvmrc` says 18, CI tests on 22, `package.json` engines says `>=20`
- `package.json` is version 3.1.0, `__init__.py` says 1.4.2
- `LICENSE` is Apache-2.0, the package metadata says MIT
- A `yarn.lock` and a `package-lock.json` sit side by side

Every file is individually valid, so every linter passes. The repository still
misleads whoever reads it next — usually a new contributor who follows the README
and hits a version error.

```console
$ disagree
error Python version is pinned to different values in different files  [python-mismatch]
    .python-version:1: .python-version = 3.9
    Dockerfile:1: FROM python:3.13-slim
    pyproject.toml:7 (ruff): tool.ruff.target-version = "py38"
    fix: Pick one and update the others; whichever file the runtime actually reads is the one that wins at 3am.

error package version differs between files: 1.4.2, 2.0.0, 3.1.0  [version-mismatch]
    package.json:1: package.json version = 3.1.0
    pyproject.toml:3: pyproject version = 2.0.0
    src/thing/__init__.py:1: __version__ = "1.4.2"

error more than one package manager is in play: npm, yarn  [package-manager-mismatch]
    package-lock.json:1: package-lock.json is present
    yarn.lock:1: yarn.lock is present

warning the README says Python >=3.8 but the package declares >=3.11  [python-mismatch]
    README.md:3: Requires Python 3.8 or later.
    pyproject.toml:4: requires-python = ">=3.11"

3 errors, 1 warning from 14 claims in 7 files.
```

Every side of a contradiction is shown. "Version mismatch" on its own would be
useless — you need to see all four files to decide which one is right.

## Install

```bash
pip install disagree
```

No runtime dependencies.

## Why it isn't just string comparison

A naive checker would flag a CI matrix testing 3.10 *and* 3.13 as a contradiction.
It isn't — that's the point of a matrix. Every claim carries a **role**, and the
rules differ:

| Role | Example | Rule |
|---|---|---|
| `pinned` | `.python-version`, `FROM python:3.11` | two pins on different release lines is an **error** |
| `declared` | `requires-python`, `engines.node` | anything outside the range is an **error** |
| `tested` | a CI matrix entry | several are normal; outside a declared range is an **error** |
| `documented` | "Requires Python 3.8+" in prose | disagreement is a **warning** — it misleads people, it doesn't break builds |

Only `major.minor` is compared. Patch-level drift between a Dockerfile and a
`.python-version` is noise; a minor-version difference is the bug people hit.
`node 20` and `node 20.11` agree — the coarser claim is less specific, not wrong.

## What it reads

| Source | Claims |
|---|---|
| `.python-version`, `.nvmrc`, `.node-version` | pinned runtime |
| `.tool-versions` (asdf/mise), `runtime.txt` (Heroku) | pinned runtime |
| `Dockerfile` | base-image runtime, `EXPOSE` ports |
| `package.json` | `engines`, `packageManager`, `version`, `license` |
| `pyproject.toml` | `requires-python`, `version`, `license`, ruff/black/mypy targets |
| `.github/workflows/*.yml` | `setup-python` / `setup-node` versions, matrices |
| `package-lock.json`, `yarn.lock`, `pnpm-lock.yaml`, `bun.lockb` | package manager |
| `__init__.py` | `__version__` |
| `LICENSE` | the actual license, identified from its text |
| `README.md` | prose version claims (code blocks excluded) |

## Usage

```bash
disagree                      # check the current directory
disagree path/to/repo
disagree --show-claims        # list every claim, even where they agree
disagree --ignore port        # skip a category
disagree --strict             # warnings fail too
disagree --format json
disagree --format github      # inline PR annotations
```

Exit codes: `0` clean, `1` contradictions, `2` usage error.

## In CI

Published on the [GitHub Marketplace](https://github.com/marketplace/actions/repo-disagree)
as **repo-disagree** — the listing name had to be unique and `disagree` was already
taken. The repository, the CLI command and the pip package are all still `disagree`;
only the Marketplace title differs.

```yaml
- name: Check the repo agrees with itself
  uses: beausome/disagree@v1
```

`v1` is a moving tag that follows the latest `v1.x` release, so you get fixes
without editing your workflow. Pin to an exact release (`@v0.1.3`) to freeze it.

The Marketplace "Use latest version" button generates an exact pin instead —
GitHub always writes the newest release tag and has no way to know a moving tag
exists. Either form works; `@v1` is the one that keeps working.

Inputs, all optional:

```yaml
- uses: beausome/disagree@v1
  with:
    path: .                 # repository root
    strict: "true"          # fail on warnings too
    ignore: |               # one claim kind per line
      port
```

Or run the CLI directly:

```yaml
- name: Check the repo agrees with itself
  run: pipx run disagree --format github
```

## Design notes

**Unparseable means silent.** Constraint expressions this tool can't confidently
parse (`3.*`, `>=1 || <2`) produce no claim at all, rather than a guess. A
consistency checker that invents contradictions gets uninstalled.

**Malformed files are skipped, not fatal.** A broken `package.json` shouldn't stop
the run — messy repositories are exactly the ones with contradictions worth
finding.

**Output is ASCII only.** A single `→` in the fix hint crashed the whole run on
the Windows console with `UnicodeEncodeError`. There's a test asserting the report
survives `cp1252` encoding.

## What it does not do

- **No network.** It never resolves what a version *actually* installs, only what
  your files claim.
- **No YAML dependency.** Workflow parsing is a targeted scan of the shapes that
  matter (`python-version:`, matrix lists). Exotic YAML is skipped rather than
  guessed at.
- **It doesn't tell you which file is right.** It can't know. It shows you every
  side and the decision is yours.

## Tests

```bash
pip install -e ".[dev]"
pytest
```

Every test builds a throwaway repository in a temp directory, so the suite is
offline and independent of this repo's own state.

## License

MIT — see [LICENSE](LICENSE).
