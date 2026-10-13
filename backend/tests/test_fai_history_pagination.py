"""Full history coverage without DB, credentials, network or equipment."""
from app.api.endpoints.fai_journal import EntryCurrentState, JournalEntry, paginate_history


def test_journal_response_preserves_request_marker_and_legacy_entries():
    from app.services.fai_audit import _parse

    request_id = "12345678-1234-1234-1234-123456789abc"
    prefix = ("2026-10-12T08:00:00Z | BLOCK | ok=True | aa:bb:cc:dd:ee:ff | Client | "
              "mode=full | source=payment | user=- | ")
    entry = JournalEntry(**_parse(prefix + f"request_id={request_id} | Coupure confirmée"))
    assert entry.model_dump()["request_id"] == request_id
    assert entry.message == "Coupure confirmée"
    assert JournalEntry(**_parse(prefix + "Ancien événement")).request_id is None


def test_all_event_types_and_duplicates_survive_pagination():
    actions = ["BLOCK", "UNBLOCK", "RETRY_OK", "ABANDON", "IDENT_KO", "ROUTER_BLOCK", "ROUTER_UNBLOCK"]
    entries = [dict(mac=str(i % 4), action=actions[i % 7], index=i) for i in range(8226)]
    # Same client is deliberately repeated: this is an event count, not clients.
    current = {
        "0": EntryCurrentState(client_blocked=True, enforced_by="router"),
        "1": EntryCurrentState(client_blocked=True),
        "2": EntryCurrentState(client_blocked=False),
    }
    collected = []
    for offset in range(0, 8226, 200):
        page, matching, counts = paginate_history(entries, current, situation=None, offset=offset, limit=200)
        assert matching == 8226 and sum(counts.values()) == 8226
        collected.extend(page)
    assert collected == entries
    assert len(page) == 26
    selected, matching, counts = paginate_history(entries, current, situation="gone", offset=0, limit=200)
    assert matching == counts["gone"] and all(e["mac"] == "3" for e in selected)
    assert paginate_history(entries, current, situation="cut", offset=10000, limit=200)[0] == []


def test_absent_inventory_is_never_reported_as_not_cut():
    entries = [dict(mac=None), dict(mac="missing")]
    page, matching, counts = paginate_history(entries, {}, situation=None, offset=0, limit=200)
    assert page == entries and matching == 2
    assert counts == dict(cut=0, not_cut=0, restored=0, gone=2)
