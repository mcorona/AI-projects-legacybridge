"""El reproductor del demo solo cambia el ritmo, nunca el contenido."""
from scripts.replay_demo import LINE_DELAY, MIN_WAIT, plan

EVENTS = [{"t": 0.0, "line": "title"}, {"t": 0.1, "scene": 1}, {"t": 0.1, "line": "Q: pregunta"},
          {"t": 20.1, "line": "A: 7"}, {"t": 20.2, "scene": 2}, {"t": 50.2, "line": "A: MXN | USD"}]


def test_plan_keeps_every_event_in_order():
    p = plan(EVENTS, target=10)
    assert [e for _, e in p] == EVENTS


def test_long_waits_are_scaled_toward_target_and_short_gaps_kept_small():
    p = plan(EVENTS, target=10)
    long_delays = [d for d, _ in p if d >= MIN_WAIT]
    assert len(long_delays) == 2 and abs(sum(long_delays) - (10 - 4 * LINE_DELAY)) < 0.01
    assert long_delays[0] < long_delays[1]              # conserva la proporción de las esperas reales
    assert all(d in (0.0, LINE_DELAY) for d, _ in p if d < MIN_WAIT)
