import json
from datetime import UTC, datetime

import pytest

from clownhead import pulls
from clownhead.issues import Unavailable
from clownhead.pulls import Checks, Status
from clownhead.search import PullRequest

WIDGETS = PullRequest("widgets", 7, "acme")


def gh_answering(monkeypatch, tmp_path, fake_gh, payload) -> None:
    body = json.dumps(payload).replace("'", "'\\''")
    monkeypatch.setenv("CLOWNHEAD_GH_BIN", str(fake_gh(tmp_path, f"echo '{body}'")))


def listed(number: int = 7, repo: str = "widgets", **overrides) -> dict:
    entry = {
        "number": number,
        "repository": {"name": repo, "nameWithOwner": f"acme/{repo}"},
        "title": "Add a thing",
        "url": f"https://github.com/acme/{repo}/pull/{number}",
        "isDraft": False,
        "createdAt": "2026-08-10T09:00:00Z",
        "updatedAt": "2026-08-12T09:00:00Z",
    }
    return {**entry, **overrides}


def check(name: str, conclusion: str | None = None, status: str = "COMPLETED") -> dict:
    return {"__typename": "CheckRun", "name": name, "conclusion": conclusion, "status": status}


def test_mine_reads_the_list_gh_printed(monkeypatch, tmp_path, fake_gh, a_pull):
    gh_answering(monkeypatch, tmp_path, fake_gh, [listed(), listed(9, "gadgets")])

    found = pulls.mine()

    assert [pull.reference for pull in found] == [WIDGETS, PullRequest("gadgets", 9, "acme")]
    assert found[0].title == "Add a thing"
    assert found[0].created_at == datetime(2026, 8, 10, 9, tzinfo=UTC)


def test_mine_asks_gh_for_the_author_and_only_what_is_open(monkeypatch, tmp_path, fake_gh, a_pull):
    seen = tmp_path / "argv"
    monkeypatch.setenv("CLOWNHEAD_GH_BIN", str(fake_gh(tmp_path, f'echo "$@" > {seen}; echo "[]"')))

    pulls.mine("someone", limit=5)

    argv = seen.read_text().split()
    assert argv[:5] == ["search", "prs", "--author=someone", "--state=open", "--limit=5"]


def test_mine_orders_the_newest_first(monkeypatch, tmp_path, fake_gh, a_pull):
    gh_answering(
        monkeypatch,
        tmp_path,
        fake_gh,
        [listed(1, createdAt="2026-01-01T00:00:00Z"), listed(2, createdAt="2026-06-01T00:00:00Z")],
    )

    assert [pull.reference.number for pull in pulls.mine()] == [2, 1]


def test_mine_drops_a_result_whose_url_names_no_pull_request(monkeypatch, tmp_path, fake_gh, a_pull):
    gh_answering(monkeypatch, tmp_path, fake_gh, [listed(), listed(url="https://example.invalid/nothing")])

    assert [pull.reference for pull in pulls.mine()] == [WIDGETS]


def test_mine_says_nothing_is_open_rather_than_failing(monkeypatch, tmp_path, fake_gh, a_pull):
    gh_answering(monkeypatch, tmp_path, fake_gh, [])

    assert pulls.mine() == []


@pytest.mark.parametrize(
    ("script", "expected"),
    [
        ('echo "gh: not logged in" >&2; exit 1', "not logged in"),
        ("exit 4", "gh exited 4"),
        ('echo "not json at all"', "not a pull request list"),
    ],
)
def test_mine_refuses_to_pass_off_a_failure_as_an_empty_list(monkeypatch, tmp_path, fake_gh, script, expected):
    monkeypatch.setenv("CLOWNHEAD_GH_BIN", str(fake_gh(tmp_path, script)))

    with pytest.raises(Unavailable, match=expected):
        pulls.mine()


def test_mine_says_so_when_there_is_no_gh_to_run(monkeypatch, tmp_path, fake_gh, a_pull):
    monkeypatch.setenv("CLOWNHEAD_GH_BIN", str(tmp_path / "nowhere"))

    with pytest.raises(Unavailable, match="not installed"):
        pulls.mine()


def test_mine_gives_up_on_a_gh_that_never_answers(monkeypatch, tmp_path, fake_gh, a_pull):
    monkeypatch.setattr(pulls, "GH_TIMEOUT", 0.1)
    monkeypatch.setenv("CLOWNHEAD_GH_BIN", str(fake_gh(tmp_path, "sleep 5")))

    with pytest.raises(Unavailable, match="did not answer"):
        pulls.mine()


def test_status_of_reads_the_checks_the_review_and_the_merge_state(monkeypatch, tmp_path, fake_gh, a_pull):
    gh_answering(
        monkeypatch,
        tmp_path,
        fake_gh,
        {
            "reviewDecision": "APPROVED",
            "mergeable": "MERGEABLE",
            "mergeStateStatus": "CLEAN",
            "statusCheckRollup": [check("lint", "SUCCESS"), check("test", "SUCCESS")],
        },
    )

    status = pulls.status_of(WIDGETS)

    assert status == Status(ran=True, review="APPROVED", merge_state="CLEAN")


def test_status_of_names_the_checks_that_went_red(monkeypatch, tmp_path, fake_gh, a_pull):
    gh_answering(
        monkeypatch,
        tmp_path,
        fake_gh,
        {"statusCheckRollup": [check("lint", "SUCCESS"), check("test", "FAILURE"), check("docs", "TIMED_OUT")]},
    )

    status = pulls.status_of(WIDGETS)

    assert status is not None
    assert status.checks is Checks.FAILING
    assert status.failing == ("docs", "test")


def test_status_of_counts_a_run_still_going_as_running(monkeypatch, tmp_path, fake_gh, a_pull):
    gh_answering(
        monkeypatch,
        tmp_path,
        fake_gh,
        {"statusCheckRollup": [check("lint", "SUCCESS"), check("test", None, status="IN_PROGRESS")]},
    )

    status = pulls.status_of(WIDGETS)

    assert status is not None
    assert status.checks is Checks.RUNNING
    assert status.running == ("test",)


def test_status_of_reads_the_older_commit_statuses_too(monkeypatch, tmp_path, fake_gh, a_pull):
    gh_answering(
        monkeypatch,
        tmp_path,
        fake_gh,
        {"statusCheckRollup": [{"__typename": "StatusContext", "context": "ci/legacy", "state": "FAILURE"}]},
    )

    status = pulls.status_of(WIDGETS)

    assert status is not None
    assert status.failing == ("ci/legacy",)


def test_status_of_says_none_where_nothing_ran(monkeypatch, tmp_path, fake_gh, a_pull):
    gh_answering(monkeypatch, tmp_path, fake_gh, {"reviewDecision": "", "statusCheckRollup": []})

    status = pulls.status_of(WIDGETS)

    assert status == Status(ran=False, review="NONE")


def test_status_of_answers_softly_where_gh_will_not(monkeypatch, tmp_path, fake_gh, a_pull):
    monkeypatch.setenv("CLOWNHEAD_GH_BIN", str(fake_gh(tmp_path, "exit 1")))

    assert pulls.status_of(WIDGETS) is None


def test_status_of_asks_nothing_without_an_owner_to_ask_about():
    assert pulls.status_of(PullRequest("widgets", 7)) is None


def test_statuses_keys_every_answer_by_its_pull_request(monkeypatch, tmp_path, fake_gh, a_pull):
    gh_answering(monkeypatch, tmp_path, fake_gh, {"reviewDecision": "APPROVED", "statusCheckRollup": []})
    listing = [a_pull(7), a_pull(9)]

    found = pulls.statuses(listing)

    assert set(found) == {a_pull(7).reference, a_pull(9).reference}


def test_statuses_asks_nothing_of_an_empty_list(monkeypatch, tmp_path):
    monkeypatch.setenv("CLOWNHEAD_GH_BIN", str(tmp_path / "nowhere"))

    assert pulls.statuses([]) == {}


def test_statuses_leaves_out_what_would_not_answer(monkeypatch, tmp_path, fake_gh, a_pull):
    monkeypatch.setenv("CLOWNHEAD_GH_BIN", str(fake_gh(tmp_path, "exit 1")))

    assert pulls.statuses([a_pull(7)]) == {}


def test_ranked_leads_with_what_the_author_has_to_act_on(a_pull):
    red = a_pull(1)
    approved = a_pull(2)
    waiting = a_pull(3)
    found = {
        red.reference: Status(failing=("test",)),
        approved.reference: Status(ran=True, review="APPROVED", merge_state="CLEAN"),
        waiting.reference: Status(ran=True, review="REVIEW_REQUIRED"),
    }

    assert [p.reference.number for p in pulls.ranked([waiting, approved, red], found)] == [1, 2, 3]


def test_ranked_counts_changes_requested_as_work_for_the_author(a_pull):
    objected = a_pull(1)
    found = {objected.reference: Status(ran=True, review="CHANGES_REQUESTED")}

    assert pulls._band(objected, found[objected.reference]) == 0


def test_ranked_sinks_a_draft_below_everything_that_is_not_one(a_pull):
    draft = a_pull(1, is_draft=True)
    waiting = a_pull(2)
    found = {draft.reference: Status(failing=("test",))}

    assert [p.reference.number for p in pulls.ranked([draft, waiting], found)] == [2, 1]


def test_ranked_puts_the_most_recently_touched_first_within_a_band(a_pull):
    older = a_pull(1, updated="2026-01-01T00:00:00Z")
    newer = a_pull(2, updated="2026-08-01T00:00:00Z")

    assert [p.reference.number for p in pulls.ranked([older, newer], {})] == [2, 1]
