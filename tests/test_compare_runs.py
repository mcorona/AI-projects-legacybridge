"""Comparativa entre corridas separadas y meta de la Fase 5."""
import json

from evals.compare_runs import fingerprint_mismatches, load_runs, meta_check, render

FP = {"git": {"commit": "abc"}, "datasets": {"dev": "d", "test": "t"}, "prompt": "p", "tools": "t",
      "dictionary": "x", "data": {"sha": "s"}, "guardrails": {"sha": "g"}, "models": {"local": "qwen"},
      "pricing": {"per_mtok": {"bedrock": {"input": 1.1, "output": 5.5}}}}


def agg(v):
    return {"mean": v, "min": v, "max": v, "n": 3}


def report(tmp_path, name, provider, acc, cost, fp=None, split="test"):
    metrics = {"execution_accuracy": agg(acc), "cost_per_query_usd": agg(cost), "swar": agg(0.05),
               "completion_rate": agg(0.9), "escalation_rate": agg(0.1 if provider == "cascade" else None)}
    p = tmp_path / f"{name}.json"
    p.write_text(json.dumps({"repeats": 3, "split": split, "fingerprint": fp or FP,
                             "summary": {provider: {"metrics": metrics}}}))
    return str(p)


def test_meta_met_and_rendered(tmp_path):
    runs = load_runs([report(tmp_path, "c", "cascade", 0.86, 0.004), report(tmp_path, "b", "bedrock", 0.88, 0.025)])
    m = meta_check(runs)
    assert m["met"] and m["accuracy_ratio"] == round(0.86 / 0.88, 4) and m["cost_ratio"] == 0.16
    md = render(runs)
    assert "Meta cumplida" in md and "cascade (×3)" in md and "Todas las corridas comparten" in md


def test_meta_not_met_on_cost(tmp_path):
    runs = load_runs([report(tmp_path, "c", "cascade", 0.88, 0.01), report(tmp_path, "b", "bedrock", 0.88, 0.025)])
    m = meta_check(runs)
    assert m["accuracy_ok"] and not m["cost_ok"] and not m["met"]


def test_meta_not_evaluable_without_bedrock(tmp_path):
    runs = load_runs([report(tmp_path, "l", "local", 0.8, 0.0)])
    assert meta_check(runs) == {"evaluable": False, "reason": "faltan corridas de cascada y/o Bedrock-only"}


def test_fingerprint_mismatch_is_flagged(tmp_path):
    other = {**FP, "prompt": "OTRO", "data": {"sha": "zzz"}}
    runs = load_runs([report(tmp_path, "l", "local", 0.8, 0.0), report(tmp_path, "b", "bedrock", 0.88, 0.025, other)])
    mism = fingerprint_mismatches(runs)
    assert any("`prompt`" in m for m in mism) and any("`data`" in m for m in mism)
    assert "NO comparable" in render(runs)
