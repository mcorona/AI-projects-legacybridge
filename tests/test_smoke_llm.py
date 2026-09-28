"""Pruebas del criterio de aceptación del smoke test (sin llamar a proveedores)."""
import pytest

from scripts.smoke_llm import DEFAULT_PROVIDERS, evaluate, parse_args


@pytest.mark.parametrize("text,status", [
    ("SELECT 1", "OK"),
    ("\n\nSELECT 1", "OK"),
    ("```sql\nselect 1;\n```", "OK"),
    ("", "FAIL"),
    ("   \n", "FAIL"),
    ("<think>razonando</think>SELECT 1", "FAIL"),
    ("SELECT 1 </think>", "FAIL"),
    ("Claro: SELECT 1", "WARN"),
])
def test_evaluate(text, status):
    assert evaluate(text).status == status


def test_parse_args_defaults():
    a = parse_args([])
    assert a.providers == DEFAULT_PROVIDERS and a.max_tokens == 1024


@pytest.mark.parametrize("argv,expected", [
    (["--provider", "local"], ["local"]),
    (["-p", "local", "-p", "bedrock"], ["local", "bedrock"]),
    (["--provider", "local,omniroute"], ["local", "omniroute"]),
    (["bedrock", "local"], ["bedrock", "local"]),
    (["-p", "local", "local"], ["local"]),
])
def test_parse_args_providers(argv, expected):
    assert parse_args(argv).providers == expected
