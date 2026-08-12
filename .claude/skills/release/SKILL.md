---
name: release
description: Cut a clownhead release — bump the version in the three places that carry it, run the checks, commit, tag, push, and write the GitHub release notes. Use when asked to "make a release", "cut a release", "bump the version", "tag a version", or "publish" this project.
---

# Releasing clownhead

A release is a version bump, a tag, and notes worth reading.
Pushing the tag does the rest:
`.github/workflows/publish.yml` builds the sdist and wheel and uploads them to PyPI as
[clownhead](https://pypi.org/project/clownhead/),
which is where `uv tool install clownhead` gets them.
Nothing here uploads by hand, and there is no token to hold;
the workflow authenticates to PyPI as a trusted publisher through its `pypi` environment.

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
`tests/test_version.py` fails when the two disagree,
and the publish workflow refuses a tag that names a third number,
so `vX.Y.Z` and both files move together or nothing ships.

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

Two workflows trigger on `v*`, so the push is the release.

`publish.yml` re-runs the suite against the tagged commit, checks the tag against `__version__`,
builds the sdist and wheel, and then stops.
The `pypi` environment requires a review, so the upload waits for approval on the run page
(`gh run view --web` on the `Publish` run).
That pause is the last chance to abort, and the build job is already green by the time it appears,
so approving is a decision made with the checks in hand.
`gh run watch`, then <https://pypi.org/project/clownhead/>, says whether it landed.
A run that fails or goes unapproved leaves PyPI untouched,
so the tag can be deleted and pushed again once the cause is fixed;
a successful one is permanent, since a version can be yanked but never re-uploaded.

`pages.yml` publishes <https://rooterkyberian.github.io/clownhead/> from the same commit.
Its front page is `overrides/home.html`,
so a release that changes the tagline or the install command should change the hero in the same commit.
`gh api repos/rooterkyberian/clownhead/pages/builds/latest` says whether that one landed.

The version is what a user pins to (`uv tool install clownhead==X.Y.Z`),
and the tag is what an install from source pins to
(`uv tool install git+https://github.com/rooterkyberian/clownhead@vX.Y.Z`),
so the tag goes up before the release that names it.

Notes are written for someone deciding whether to upgrade, in the README's voice:
what changed, what it is for, and what it replaced.
Anything breaking says so plainly and says what to use instead,
since the alternative is a user finding out from a command that no longer exists.
