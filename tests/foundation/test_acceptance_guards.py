import pytest

from scripts.live_acceptance import assert_reply_facts


@pytest.mark.parametrize(
    "answer",
    [
        "bottle最后看到于04:00:01，证据ev123。",  # Missing location was seen in the live run.
        "bottle在左侧，最后看到于04:00:01，证据ev123。",
        "bottle在中间，最后看到于04:00:02，证据ev123。",
        "bottle在中间，最后看到于04:00:01，证据invented。",
    ],
)
def test_live_acceptance_rejects_unbacked_answer_anchors(answer):
    facts = {
        "found": True,
        "category": "bottle",
        "last_seen_at": "2026-09-06T20:00:01+00:00",
        "regions": ["center"],
        "evidence_id": "ev123",
    }
    with pytest.raises(AssertionError):
        assert_reply_facts(answer, "find_object", [facts], "Asia/Shanghai")
    assert_reply_facts(
        "bottle在中间，最后看到于04:00:01，证据ev123。",
        "find_object",
        [facts],
        "Asia/Shanghai",
    )


def test_live_acceptance_rejects_positive_claim_when_history_unknown():
    with pytest.raises(AssertionError):
        assert_reply_facts("bottle在中间。", "find_object", [{"found": False}], "Asia/Shanghai")


def test_live_acceptance_requires_event_evidence():
    facts = {
        "events": [
            {
                "category": "bottle",
                "kind": "appeared",
                "confirmed_at": "2026-09-06T20:00:01+00:00",
                "evidence_id": "event-evidence",
            }
        ]
    }
    with pytest.raises(AssertionError):
        assert_reply_facts("bottle于04:00:01出现。", "search_events", [facts], "Asia/Shanghai")
    with pytest.raises(AssertionError, match="invented a missing"):
        assert_reply_facts(
            "bottle于04:00:01出现，证据event-evidence。缺失事件：持续未检测到。",
            "search_events",
            [facts],
            "Asia/Shanghai",
        )
