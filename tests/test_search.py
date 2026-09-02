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
    wanted = PullRequest("data-platform", 309).mention_pattern()

    assert wanted.found_in(text.encode()) is expected


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
            "https://kyberian.atlassian.net/browse/PLAT-4471",
            Issue(tracker=Tracker.JIRA, key="PLAT-4471", host="kyberian.atlassian.net"),
        ),
        ("payments", None),
        ("#309", None),
        ("PLAT-4471", None),
    ],
)
def test_parse_reference_reads_either_kind_and_leaves_the_shorthand_to_pull_requests(reference, expected):
    assert parse_reference(reference) == expected


@pytest.mark.parametrize(
    ("said", "expected"),
    [
        ("widgets#42", True),
        (" widgets#42 ", True),
        ("widgets#420", False),
        ("my-widgets#42", False),
    ],
)
def test_mentions_bounds_a_reference_against_the_ends_of_the_file(tmp_path, said, expected):
    path = tmp_path / "transcript.jsonl"
    path.write_text(said)

    assert mentions(path, PullRequest("widgets", 42).mention_pattern()) is expected


def test_mentions_reports_no_match_for_an_unreadable_file(tmp_path):
    assert mentions(tmp_path / "gone.jsonl", PullRequest("widgets", 42).mention_pattern()) is False


def test_mentions_reports_no_match_for_an_empty_transcript(tmp_path):
    path = tmp_path / "transcript.jsonl"
    path.write_bytes(b"")

    assert mentions(path, PullRequest("widgets", 42).mention_pattern()) is False


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
    transcript(tmp_path, "a-b", "picking up https://kyberian.atlassian.net/browse/PLAT-4471")
    transcript(tmp_path, "c-d", "nothing to do with it", cwd="/tmp/two")
    issue = Issue(tracker=Tracker.JIRA, key="PLAT-4471", host="kyberian.atlassian.net")

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


def test_pulls_mentioned_reads_every_pull_request_a_transcript_names(tmp_path):
    transcript(
        tmp_path,
        "one",
        "based on https://github.com/acme/widgets/pull/7, follows https://github.com/acme/gadgets/pull/9",
    )

    assert set(search.pulls_mentioned("one", tmp_path)) == {
        PullRequest("widgets", 7, "acme"),
        PullRequest("gadgets", 9, "acme"),
    }


def test_pulls_mentioned_leads_with_the_one_named_last(tmp_path):
    transcript(
        tmp_path, "one", "opened https://github.com/acme/widgets/pull/7 then https://github.com/acme/gadgets/pull/9"
    )

    assert search.pulls_mentioned("one", tmp_path)[0] == PullRequest("gadgets", 9, "acme")


def test_pulls_mentioned_names_each_pull_request_once_however_often_it_came_up(tmp_path):
    url = "https://github.com/acme/widgets/pull/7"
    transcript(tmp_path, "one", " ".join([url] * 40))

    assert search.pulls_mentioned("one", tmp_path) == [PullRequest("widgets", 7, "acme")]


def test_pulls_mentioned_reads_one_spelled_two_ways_as_one_pull_request(tmp_path):
    transcript(tmp_path, "one", "https://github.com/Acme/Widgets/pull/7 is https://github.com/acme/widgets/pull/7")

    assert len(search.pulls_mentioned("one", tmp_path)) == 1


def test_pulls_mentioned_refuses_the_shorthand_it_cannot_anchor(tmp_path):
    transcript(tmp_path, "one", "widgets#7 and PLAT-4471#3 and a diff hunk @@ -7,3 +7,3 @@")

    assert search.pulls_mentioned("one", tmp_path) == []


def test_pulls_mentioned_does_not_read_an_issue_as_a_pull_request(tmp_path):
    transcript(tmp_path, "one", "https://github.com/acme/widgets/issues/7")

    assert search.pulls_mentioned("one", tmp_path) == []


def test_pulls_mentioned_takes_the_whole_number(tmp_path):
    transcript(tmp_path, "one", "https://github.com/acme/widgets/pull/3090/files")

    assert search.pulls_mentioned("one", tmp_path) == [PullRequest("widgets", 3090, "acme")]


def test_pulls_mentioned_reads_a_subagent_transcript_too(tmp_path):
    transcript(tmp_path, "one", "nothing here")
    subagent_transcript(tmp_path, "one", "https://github.com/acme/widgets/pull/7")

    assert search.pulls_mentioned("one", tmp_path) == [PullRequest("widgets", 7, "acme")]


def test_pulls_mentioned_says_nothing_about_an_empty_transcript(tmp_path):
    path = transcript(tmp_path, "one", "https://github.com/acme/widgets/pull/7")
    path.write_bytes(b"")

    assert search.pulls_mentioned("one", tmp_path) == []


def test_pulls_mentioned_survives_a_transcript_it_cannot_read(tmp_path):
    path = transcript(tmp_path, "one", "https://github.com/acme/widgets/pull/7")
    path.chmod(0o000)

    try:
        assert search.pulls_mentioned("one", tmp_path) == []
    finally:
        path.chmod(0o644)


def test_pulls_mentioned_says_nothing_about_a_session_with_no_transcript(tmp_path):
    assert search.pulls_mentioned("nowhere", tmp_path) == []


def test_sessions_by_pull_leaves_out_the_sessions_that_named_nothing(tmp_path):
    transcript(tmp_path, "one", "https://github.com/acme/widgets/pull/7")
    transcript(tmp_path, "two", "just talking", cwd="/tmp/two")

    found = search.sessions_by_pull([session("one"), session("two", "/tmp/two")], tmp_path)

    assert found == {PullRequest("widgets", 7, "acme"): ["one"]}


def test_sessions_by_pull_reads_the_same_pass_the_other_way_round(tmp_path):
    transcript(tmp_path, "one", "https://github.com/acme/widgets/pull/7")
    transcript(tmp_path, "two", "also https://github.com/acme/widgets/pull/7", cwd="/tmp/two")

    found = search.sessions_by_pull([session("one"), session("two", "/tmp/two")], tmp_path)

    assert found == {PullRequest("widgets", 7, "acme"): ["one", "two"]}


def test_sessions_by_pull_gathers_the_spellings_case_alone_separates(tmp_path):
    transcript(tmp_path, "one", "https://github.com/Acme/Widgets/pull/7")
    transcript(tmp_path, "two", "https://github.com/acme/widgets/pull/7", cwd="/tmp/two")

    found = search.sessions_by_pull([session("one"), session("two", "/tmp/two")], tmp_path)

    assert found == {PullRequest("widgets", 7, "acme"): ["one", "two"]}


def test_a_pull_request_is_the_one_github_would_resolve_it_to():
    assert PullRequest("Widgets", 7, "Acme") == PullRequest("widgets", 7, "acme")
    assert len({PullRequest("Widgets", 7, "Acme"), PullRequest("widgets", 7, "acme")}) == 1


@pytest.mark.parametrize(
    "other",
    [PullRequest("gadgets", 7, "acme"), PullRequest("widgets", 8, "acme"), PullRequest("widgets", 7), "widgets#7"],
)
def test_a_pull_request_is_not_one_that_differs_by_more_than_case(other):
    assert PullRequest("widgets", 7, "acme") != other


def test_pull_request_url_is_where_a_browser_would_be_sent():
    assert PullRequest("widgets", 7, "acme").url == "https://github.com/acme/widgets/pull/7"


def test_pull_request_without_an_owner_has_no_url_to_send_anyone_to():
    """``prompt`` still answers, with the shorthand — which is not something to open."""
    reference = PullRequest("widgets", 7)

    assert reference.url == ""
    assert reference.prompt == "widgets#7"


def test_pulls_by_session_keeps_a_session_that_named_nothing(tmp_path):
    """Read-and-found-nothing is not the same as never-read, and only this map knows."""
    transcript(tmp_path, "one", "https://github.com/acme/widgets/pull/7")
    transcript(tmp_path, "two", "just talking", cwd="/tmp/two")

    found = search.pulls_by_session([session("one"), session("two", "/tmp/two")], tmp_path)

    assert found == {"one": [PullRequest("widgets", 7, "acme")], "two": []}
