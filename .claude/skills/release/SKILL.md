---
name: release
description: Cut a clownhead release — bump the version in the three places that carry it, run the checks, commit, tag, push, and write the GitHub release notes. Use when asked to "make a release", "cut a release", "bump the version", "tag a version", or "publish" this project.
---

# Releasing clownhead

clownhead is installed straight from git (`uv tool install git+https://github.com/rooterkyberian/clownhead`),
so there is no package index step.
A release is a version bump, a tag, and notes worth reading.

## 1. Start from a green main

History on `main` is linear and committed to directly — no branch, no PR.

```bash
git switch main && git pull --ff-only
git status --short
```

Anything uncommitted is either part of this release or should be dropped before it starts.

Then run the checks:

```bash
mise run check
```

That is lint, typecheck and tests.
`mise` installs its own toolchain and can fail on a sandboxed network before it ever reaches the project;
when it does, run the same four commands directly rather than releasing unchecked:

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
```

## 2. Pick the number

Pre-1.0, so the minor is where breakage goes:

- **minor** (`0.2.0` → `0.3.0`) — a command or flag removed or renamed,
  a default flipped,
  anything that changes what an existing invocation does.
- **patch** (`0.3.0` → `0.3.1`) — fixes and additions that leave every existing invocation alone.

`git log --oneline $(git describe --tags --abbrev=0)..HEAD` is what is being released.

## 3. Bump all three

The version lives in three files and they must move together:

- `pyproject.toml` — `[project] version`
- `src/clownhead/__init__.py` — `__version__`, which is what `clownhead --version` answers
- `uv.lock` — refreshed with `uv lock` (any `uv run` also does it)

`__version__` is the number a problem report quotes,
and the issue form asks for it up front;
leaving it behind the package version has the reporter naming a release that is not the one they are running.

While the version is open, check that nothing the release contradicts is still being claimed:
the README's command table and settings paragraph,
the `pyproject` description,
and the repository description GitHub shows (`gh repo view --json description`),
which is a copy of the `pyproject` one and drifts silently because nothing builds from it.

## 4. Commit

The bump rides with the change it releases when that change is not yet committed;
when it is, the bump is its own `chore: release X.Y.Z`.

Commit messages here are prose, not a changelog:
the subject says what now happens,
the body says why it is worth doing and what it replaced.
Look at `git log` before writing one.

```bash
git push origin main
```

## 5. Tag and publish

```bash
git tag -a vX.Y.Z -m "clownhead X.Y.Z"
git push origin vX.Y.Z
gh release create vX.Y.Z --title "clownhead X.Y.Z" --notes "..."
```

The tag is also what a user pins to — `uv tool install git+https://github.com/rooterkyberian/clownhead@vX.Y.Z` —
so it is pushed before the release that names it.

Notes are written for someone deciding whether to upgrade, in the README's voice:
what changed, what it is for, and what it replaced.
Anything breaking says so plainly and says what to use instead,
since the alternative is a user finding out from a command that no longer exists.
