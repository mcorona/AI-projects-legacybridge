"""CLI de revisión humana de propuestas de cambio (rol `lb_reviewer`). Nunca ejecuta la SQL propuesta.

Uso:
    python -m legacybridge.agent.review_cli list [--all]
    python -m legacybridge.agent.review_cli approve ID --reviewer "Ana DBA" [--note "..."]
    python -m legacybridge.agent.review_cli reject ID --reviewer "Ana DBA" --note "motivo"
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from legacybridge.agent.proposals import DEFAULT_REVIEWER_DSN


def _review(conn, pid: int, status: str, reviewer: str, note: str) -> bool:
    cur = conn.execute(
        "UPDATE ops.change_proposals SET status = %s, reviewed_by = %s, reviewed_at = now(), review_note = %s "
        "WHERE id = %s AND status = 'PENDING_REVIEW'", (status, reviewer, note, pid))
    if cur.rowcount:
        conn.execute("INSERT INTO ops.audit_log (actor, event, details) VALUES (%s, %s, %s)",
                     (reviewer, f"proposal_{status.lower()}", json.dumps({"id": pid, "note": note})))
    return bool(cur.rowcount)


def main(argv: list[str] | None = None) -> int:
    import psycopg

    ap = argparse.ArgumentParser(prog="proposals", description="Revisión de propuestas de cambio (nunca se ejecutan)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    ls = sub.add_parser("list")
    ls.add_argument("--all", action="store_true", help="incluye las ya revisadas")
    for name in ("approve", "reject"):
        p = sub.add_parser(name)
        p.add_argument("id", type=int)
        p.add_argument("--reviewer", required=True)
        p.add_argument("--note", default="", required=name == "reject")
    args = ap.parse_args(argv)
    dsn = os.environ.get("LB_REVIEWER_DSN", DEFAULT_REVIEWER_DSN)
    with psycopg.connect(dsn, connect_timeout=3) as conn:
        if args.cmd == "list":
            rows = conn.execute(
                "SELECT id, created_at::timestamp(0), status, kind, target_table, affected_rows_est, requested_by, "
                "confirmed_by, sql, reviewed_by FROM ops.change_proposals "
                + ("" if args.all else "WHERE status = 'PENDING_REVIEW' ") + "ORDER BY id").fetchall()
            if not rows:
                print("Sin propuestas" + ("" if args.all else " pendientes") + ".")
            for r in rows:
                print(f"#{r[0]} {r[1]} {r[2]:15} {r[3]:6} {r[4]:8} ~{r[5] if r[5] is not None else '?'} filas · "
                      f"pidió {r[6]}, confirmó {r[7]}" + (f", revisó {r[9]}" if r[9] else "") + f"\n    {r[8]}")
            return 0
        status = "APPROVED" if args.cmd == "approve" else "REJECTED"
        if not _review(conn, args.id, status, args.reviewer, args.note):
            print(f"La propuesta #{args.id} no existe o ya fue revisada.")
            return 1
        print(f"Propuesta #{args.id} {status}. " + ("Un DBA puede aplicarla fuera del agente; este sistema no la ejecuta."
                                                   if status == "APPROVED" else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
