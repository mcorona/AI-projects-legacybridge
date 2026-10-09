"""Every attack in evals/attacks.yaml must end blocked or neutralized (Phase 2 DoD), and the
database must refuse the write attacks on its own, without the guard (defense in depth)."""

import psycopg
import pytest

from evals.attacks import call_tool, load_attacks, run_attack
from legacybridge.db import connect

ATTACKS = load_attacks()
SLOW = {a.id for a in ATTACKS if a.category == "expensive_query"}  # stopped by the 5 s timeout


def test_there_are_at_least_fifteen_attacks_across_many_categories():
    assert len(ATTACKS) >= 15
    assert len({a.id for a in ATTACKS}) == len(ATTACKS)
    assert len({a.category for a in ATTACKS}) >= 12


@pytest.mark.integration
@pytest.mark.parametrize("attack", ATTACKS, ids=lambda a: a.id)
def test_attack_is_blocked_or_neutralized(tools, attack):
    outcome = run_attack(tools, attack, check_database=False)
    assert outcome.passed, f"{attack.id}: {outcome.detail}"


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
            call_tool(tools, attack)
    admin.rollback()
    assert _fingerprint(admin) == before
