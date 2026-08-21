import pytest

from clownhead.issues import Issue, Tracker, fetch_title, parse_issue, slug


def github(number: str, repo: str = "widgets", owner: str = "acme") -> Issue:
    return Issue(tracker=Tracker.GITHUB, key=number, repo=repo, owner=owner)


def jira(key: str = "PLAT-4471", host: str = "craft.atlassian.net") -> Issue:
    return Issue(tracker=Tracker.JIRA, key=key, host=host)


@pytest.mark.parametrize(
    ("reference", "expected"),
    [
        ("https://github.com/acme/widgets/issues/9", github("9")),
        ("http://www.github.com/acme/widgets/issues/9", github("9")),
        ("github.com/acme/widgets/issues/9", github("9")),
        ("GitHub.com/acme/data-platform/issues/309", github("309", "data-platform")),
        ("https://github.com/acme/widgets/issues/9#issuecomment-12345", github("9")),
        ("see https://github.com/acme/widgets/issues/9 for context", github("9")),
        ("  https://github.com/acme/widgets/issues/9  ", github("9")),
    ],
)
def test_parse_issue_reads_github_urls(reference, expected):
    assert parse_issue(reference) == expected


@pytest.mark.parametrize(
    ("reference", "expected"),
    [
        ("https://craft.atlassian.net/browse/PLAT-4471", jira()),
        ("craft.atlassian.net/browse/PLAT-4471", jira()),
        ("https://jira.example.com/browse/PLAT-4471", jira(host="jira.example.com")),
        (
            "https://craft.atlassian.net/jira/software/projects/PLAT/boards/1?selectedIssue=PLAT-4471",
            jira(),
        ),
        ("https://craft.atlassian.net/browse/plat-4471", jira()),
    ],
)
def test_parse_issue_reads_jira_urls(reference, expected):
    assert parse_issue(reference) == expected


@pytest.mark.parametrize(
    "reference",
    [
        "",
        "   ",
        "payments",
        "#309",
        "widgets#309",
        "https://github.com/acme/widgets/pull/309",
        "PLAT-4471",
        "UTF-8",
        "SHA-256",
        "ISO-8601",
    ],
)
def test_parse_issue_refuses_what_is_not_an_issue_url(reference):
    assert parse_issue(reference) is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("https://github.com/acme/widgets/issues/9", True),
        ("widgets#9", True),
        ("Widgets#9", True),
        ("widgets/issues/90", False),
        ("widgets#90", False),
        ("my-widgets#9", False),
        ("widgets#8", False),
        ("widgets/pull/9", False),
        ("9", False),
    ],
)
def test_github_mention_pattern_needs_the_repository_and_the_whole_number(text, expected):
    assert bool(github("9").mention_pattern().search(text.encode())) is expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("PLAT-4471", True),
        ("https://craft.atlassian.net/browse/PLAT-4471", True),
        ("~/dev/api/.claude/worktrees/plat-4471", True),
        ("PLAT-44710", False),
        ("PLAT-447", False),
        ("XPLAT-4471", False),
        ("4471", False),
    ],
)
def test_jira_mention_pattern_matches_the_whole_key_only(text, expected):
    assert bool(jira().mention_pattern().search(text.encode())) is expected


@pytest.mark.parametrize(
    ("reference", "expected"),
    [
        (github("2"), "https://github.com/acme/widgets/issues/2"),
        (jira(), "https://craft.atlassian.net/browse/PLAT-4471"),
    ],
)
def test_prompt_is_the_url_a_session_is_started_with(reference, expected):
    assert reference.prompt == expected


@pytest.mark.parametrize(
    ("reference", "rendered", "base"),
    [
        (github("2"), "acme/widgets#2", "issue-2"),
        (Issue(tracker=Tracker.GITHUB, key="2", repo="widgets"), "widgets#2", "issue-2"),
        (jira(), "PLAT-4471", "plat-4471"),
    ],
)
def test_issue_renders_and_names_itself(reference, rendered, base):
    assert str(reference) == rendered
    assert reference.base_slug == base


@pytest.mark.parametrize(
    ("base", "title", "expected"),
    [
        ("issue-2", None, "issue-2"),
        ("issue-2", "", "issue-2"),
        ("issue-2", "Add a thing", "issue-2-add-a-thing"),
        ("issue-2", "  Spaced  out  ", "issue-2-spaced-out"),
        ("issue-2", "feat: don't drop the (parens)!", "issue-2-feat-don-t-drop-the-parens"),
        ("issue-2", "!!!", "issue-2"),
        ("issue-2", "Ünïcödé wörds", "issue-2-unicode-words"),
        ("plat-4471", "Rate limit the scan queue", "plat-4471-rate-limit-the-scan-queue"),
    ],
)
def test_slug_joins_the_base_to_as_much_of_the_title_as_fits(base, title, expected):
    assert slug(base, title) == expected


def test_slug_cuts_a_long_title_on_a_word_boundary():
    name = slug("issue-2", "add ability to open new claude session when given issue url")

    assert name == "issue-2-add-ability-to-open-new-claude-session"
    assert len(name) <= 48


def test_slug_keeps_the_base_when_the_first_word_alone_would_not_fit():
    assert slug("issue-2", "supercalifragilisticexpialidociousandthensome") == "issue-2"


def test_fetch_title_returns_what_gh_printed(monkeypatch, tmp_path, fake_gh):
    monkeypatch.setenv("CLOWNHEAD_GH_BIN", str(fake_gh(tmp_path, 'echo "Add a thing"')))

    assert fetch_title(github("2").title_query) == "Add a thing"


def test_fetch_title_passes_the_reference_to_gh(monkeypatch, tmp_path, fake_gh):
    seen = tmp_path / "argv"
    monkeypatch.setenv("CLOWNHEAD_GH_BIN", str(fake_gh(tmp_path, f'echo "$@" > {seen}')))

    fetch_title(github("2").title_query)

    assert seen.read_text().split() == [
        "issue",
        "view",
        "2",
        "--repo",
        "acme/widgets",
        "--json",
        "title",
        "--jq",
        ".title",
    ]


@pytest.mark.parametrize("script", ["exit 1", "echo"])
def test_fetch_title_gives_up_quietly_when_gh_says_nothing(monkeypatch, tmp_path, fake_gh, script):
    monkeypatch.setenv("CLOWNHEAD_GH_BIN", str(fake_gh(tmp_path, script)))

    assert fetch_title(github("2").title_query) is None


def test_fetch_title_gives_up_quietly_without_a_gh_to_run(monkeypatch, tmp_path, fake_gh):
    monkeypatch.setenv("CLOWNHEAD_GH_BIN", str(tmp_path / "nowhere"))

    assert fetch_title(github("2").title_query) is None


@pytest.mark.parametrize(
    "reference",
    [jira(), Issue(tracker=Tracker.GITHUB, key="2", repo="widgets")],
)
def test_title_query_is_none_for_what_gh_cannot_be_asked(reference):
    assert reference.title_query is None


def test_fetch_title_asks_nothing_without_a_query():
    assert fetch_title(None) is None
