import json
from pathlib import Path

import pytest

from clownhead import search
from clownhead.issues import Issue, Tracker
from clownhead.models import Session
from clownhead.search import PullRequest, mentions, parse_pull_request, parse_reference, sessions_mentioning


@pytest.mark.parametrize(
    ("reference", "expected"),
    [
        ("https://github.com/acme/data-platform/pull/309", PullRequest("data-platform", 309, "acme")),
        ("http://www.github.com/acme/widgets/pull/7", PullRequest("widgets", 7, "acme")),
        ("github.com/acme/widgets/pull/7/files", PullRequest("widgets", 7, "acme")),
        ("GitHub.com/acme/widgets/pull/7", PullRequest("widgets", 7, "acme")),
        ("see https://github.com/acme/widgets/pull/12 for context", PullRequest("widgets", 12, "acme")),
        ("  https://github.com/acme/widgets/pull/12  ", PullRequest("widgets", 12, "acme")),
        ("acme/data-platform#309", PullRequest("data-platform", 309, "acme")),
        ("data-platform#309", PullRequest("data-platform", 309, None)),
    ],
)
def test_parse_pull_request_reads_urls_and_shorthand(reference, expected):
    assert parse_pull_request(reference) == expected


@pytest.mark.parametrize(
    "reference",
    ["", "   ", "payments", "4e020900", "#309", "input needed", "https://github.com/acme/widgets/issues/9", "~/dev#3"],
)
def test_parse_pull_request_refuses_what_does_not_name_one(reference):
    assert parse_pull_request(reference) is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("https://github.com/acme/data-platform/pull/309", True),
        ("https://github.com/acme/data-platform/pull/309/files#r1", True),
        ("data-platform#309", True),
        ("acme/data-platform#309", True),
        ("Data-Platform#309", True),
        ("data-platform/pull/3090", False),
        ("data-platform#3090", False),
        ("my-data-platform#309", False),
        ("data-platform#310", False),
        ("data.platform#309", False),
        ("309", False),
    ],
)
def test_mention_pattern_needs_the_repository_and_the_whole_number(text, expected):
    pattern = PullRequest("data-platform", 309).mention_pattern()

    assert bool(pattern.search(text.encode())) is expected


@pytest.mark.parametrize(
    ("reference", "expected"),
    [
        ("https://github.com/acme/widgets/pull/7", PullRequest("widgets", 7, "acme")),
        ("acme/widgets#7", PullRequest("widgets", 7, "acme")),
        ("widgets#7", PullRequest("widgets", 7, None)),
        (
            "https://github.com/acme/widgets/issues/7",
            Issue(tracker=Tracker.GITHUB, key="7", repo="widgets", owner="acme"),
        ),
        (
            "https://craft.atlassian.net/browse/PLAT-4471",
            Issue(tracker=Tracker.JIRA, key="PLAT-4471", host="craft.atlassian.net"),
        ),
        ("payments", None),
        ("#309", None),
        ("PLAT-4471", None),
    ],
)
def test_parse_reference_reads_either_kind_and_leaves_the_shorthand_to_pull_requests(reference, expected):
    assert parse_reference(reference) == expected


def test_mentions_finds_a_reference_split_across_a_chunk_boundary(monkeypatch, tmp_path):
    monkeypatch.setattr(search, "CHUNK_BYTES", 13)
    path = tmp_path / "transcript.jsonl"
    path.write_text(" " * 10 + "widgets#42" + " " * 10)

    assert mentions(path, PullRequest("widgets", 42).mention_pattern()) is True


def test_mentions_reports_no_match_for_an_unreadable_file(tmp_path):
    assert mentions(tmp_path / "gone.jsonl", PullRequest("widgets", 42).mention_pattern()) is False


def transcript(root: Path, session_id: str, said: str, cwd: str = "/tmp/one") -> Path:
    project = root / cwd.replace("/", "-")
    project.mkdir(parents=True, exist_ok=True)
    path = project / f"{session_id}.jsonl"
    path.write_text(json.dumps({"sessionId": session_id, "cwd": cwd, "type": "user", "message": {"content": said}}))
    return path


def subagent_transcript(root: Path, session_id: str, said: str, cwd: str = "/tmp/one") -> Path:
    nested = root / cwd.replace("/", "-") / session_id
    nested.mkdir(parents=True, exist_ok=True)
    path = nested / "sidechain.jsonl"
    path.write_text(json.dumps({"type": "assistant", "message": {"content": said}}))
    return path


def session(session_id: str, cwd: str = "/tmp/one") -> Session:
    return Session(session_id=session_id, cwd=Path(cwd))


def test_sessions_mentioning_keeps_only_the_ones_that_named_it(tmp_path):
    transcript(tmp_path, "a-b", "look at https://github.com/acme/widgets/pull/42 please")
    transcript(tmp_path, "c-d", "nothing to do with it", cwd="/tmp/two")
    transcript(tmp_path, "e-f", "widgets#420 is a different one", cwd="/tmp/three")

    found = sessions_mentioning(
        PullRequest("widgets", 42, "acme"),
        [session("a-b"), session("c-d", "/tmp/two"), session("e-f", "/tmp/three")],
        tmp_path,
    )

    assert found == {"a-b"}


def test_sessions_mentioning_counts_what_a_subagent_read(tmp_path):
    transcript(tmp_path, "a-b", "get on with it")
    subagent_transcript(tmp_path, "a-b", "acme/widgets#42 is the one")

    assert sessions_mentioning(PullRequest("widgets", 42), [session("a-b")], tmp_path) == {"a-b"}


def test_sessions_mentioning_tolerates_a_session_without_a_transcript(tmp_path):
    assert sessions_mentioning(PullRequest("widgets", 42), [session("a-b")], tmp_path) == set()


def test_sessions_mentioning_searches_for_an_issue_the_same_way(tmp_path):
    transcript(tmp_path, "a-b", "picking up https://craft.atlassian.net/browse/PLAT-4471")
    transcript(tmp_path, "c-d", "nothing to do with it", cwd="/tmp/two")
    issue = Issue(tracker=Tracker.JIRA, key="PLAT-4471", host="craft.atlassian.net")

    found = sessions_mentioning(issue, [session("a-b"), session("c-d", "/tmp/two")], tmp_path)

    assert found == {"a-b"}


def test_pull_request_renders_back_the_reference_it_was_given():
    assert str(PullRequest("data-platform", 309, "acme")) == "acme/data-platform#309"
    assert str(PullRequest("data-platform", 309)) == "data-platform#309"


@pytest.mark.parametrize(
    ("reference", "prompt", "base", "query"),
    [
        (
            PullRequest("widgets", 7, "acme"),
            "https://github.com/acme/widgets/pull/7",
            "pr-7",
            ["pr", "view", "7", "--repo", "acme/widgets"],
        ),
        (PullRequest("widgets", 7), "widgets#7", "pr-7", None),
    ],
)
def test_pull_request_names_itself_for_a_session_started_from_it(reference, prompt, base, query):
    assert reference.prompt == prompt
    assert reference.base_slug == base
    assert reference.title_query == query
