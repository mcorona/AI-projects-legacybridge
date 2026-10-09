"""Every attack in evals/attacks.yaml must end blocked or neutralized (Phase 2 DoD), and the
database must refuse the write attacks on its own, without the guard (defense in depth)."""

import psycopg
import pytest

from evals.attacks import Attack, Expected, load_attacks
from legacybridge import policy
from legacybridge.db import connect

ATTACKS = load_attacks()
SLOW = {a.id for a in ATTACKS if a.category == "expensive_query"}  # stopped by the 5 s timeout


def run(tools, attack: Attack) -> dict:
    i = attack.input
    if attack.tool == "run_query":
        return tools.run_query(i["sql"], i.get("max_rows", policy.DEFAULT_ROWS))
    if attack.tool == "describe_table":
        return tools.describe_table(i["name"])
    if attack.tool == "sample_rows":
        return tools.sample_rows(i["name"], i["n"])
    if attack.tool == "propose_write":
        return tools.propose_write(i["table"], i["update"], i["where"], i.get("reason"))
    return tools.explain_business_term(i["term"])


def test_there_are_at_least_fifteen_attacks_across_many_categories():
    assert len(ATTACKS) >= 15
    assert len({a.id for a in ATTACKS}) == len(ATTACKS)
    assert len({a.category for a in ATTACKS}) >= 12


@pytest.mark.integration
@pytest.mark.parametrize("attack", ATTACKS, ids=lambda a: a.id)
def test_attack_is_blocked_or_neutralized(tools, attack):
    result = run(tools, attack)
    if attack.expected == Expected.BLOCKED:
        assert result["ok"] is False, f"{attack.id} got through: {result}"
        assert (result["stage"], result["reason"]) == (attack.stage, attack.reason), result
        return
    assert result["ok"] is True, result
    check = attack.check
    if "max_rows" in check:
        assert result["row_count"] <= check["max_rows"]
    if "truncated" in check:
        assert result["truncated"] is check["truncated"]
    if "warning_contains" in check:
        assert any(check["warning_contains"] in w for w in result["warnings"]), result["warnings"]
    if "row_contains" in check:
        cells = [str(cell) for row in result["rows"] for cell in row]
        assert any(check["row_contains"] in cell for cell in cells), "hostile text must be verbatim"
    assert result["data_notice"] == policy.DATA_NOTICE


@pytest.mark.integration
@pytest.mark.parametrize("attack", [a for a in ATTACKS if a.db_refuses], ids=lambda a: a.id)
def test_database_refuses_the_attack_even_without_the_guard(db_available, attack):
    if not db_available:
        pytest.skip("legacy Postgres is not reachable")
    with connect("reader", autocommit=True) as conn, pytest.raises(psycopg.Error):
        conn.execute(attack.input["sql"])


def _fingerprint(conn) -> tuple:
    tables = ("clientes", "productos", "faccab", "facdet", "invart", "invexi", "invmov", "invprv")
    sums = [
        conn.execute(
            f"SELECT md5(string_agg(t::text, '|' ORDER BY t::text)) FROM {t} t"
        ).fetchone()[0]
        for t in tables
    ]
    queued = conn.execute("SELECT count(*) FROM lb_ops.write_requests").fetchone()[0]
    return (*sums, queued)


@pytest.mark.integration
def test_no_attack_changes_business_data_or_queues_anything(admin, tools):
    before = _fingerprint(admin)
    for attack in ATTACKS:
        if attack.id not in SLOW:
            run(tools, attack)
    admin.rollback()
    assert _fingerprint(admin) == before
