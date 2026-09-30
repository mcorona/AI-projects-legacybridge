"""Reproduce el registro de una corrida real de `make demo` (DEMO_CAST) con las esperas comprimidas.

Uso:
    python -m scripts.replay_demo cast.json [--target 85] [--timeline out.json]

Todo lo que se imprime es la salida real de la corrida registrada; solo cambia el ritmo: las esperas
largas (el modelo pensando) se escalan para que el total dure ~`--target` segundos y se muestran con un
indicador. Pensado para grabar el video con VHS (docs/demo/demo.tape).
"""
from __future__ import annotations

import argparse
import json
import sys
import time

LINE_DELAY = 0.03      # desplazamiento visible línea a línea
LONG_GAP = 1.0         # a partir de aquí se considera espera del modelo
MIN_WAIT = 1.2         # una espera comprimida nunca baja de esto (se alcanza a ver el indicador)


def plan(events: list[dict], target: float) -> list[tuple[float, dict]]:
    """(retardo antes del evento, evento) con las esperas largas escaladas para acercarse a `target`."""
    gaps, prev = [], 0.0
    for e in events:
        gaps.append(max(0.0, e["t"] - prev))
        prev = e["t"]
    lines = sum(1 for e in events if "line" in e)
    long_total = sum(g for g in gaps if g >= LONG_GAP)
    budget = max(target - lines * LINE_DELAY, 1.0)
    scale = budget / long_total if long_total else 1.0
    out = []
    for g, e in zip(gaps, events):
        delay = max(MIN_WAIT, g * scale) if g >= LONG_GAP else (LINE_DELAY if "line" in e else 0.0)
        out.append((delay, e))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="replay_demo", description=__doc__.splitlines()[0])
    ap.add_argument("cast")
    ap.add_argument("--target", type=float, default=85.0)
    ap.add_argument("--timeline", help="escribe el inicio de cada escena en tiempo de reproducción")
    args = ap.parse_args(argv)
    cast = json.load(open(args.cast, encoding="utf-8"))
    t0, marks = time.perf_counter(), []
    for delay, e in plan(cast["events"], args.target):
        if delay >= MIN_WAIT:                       # el modelo está trabajando: indicador sin inventar salida
            end = time.perf_counter() + delay
            while time.perf_counter() < end:
                sys.stdout.write(f"\r  … model working ({delay:.0f}s shown, sped up)")
                sys.stdout.flush()
                time.sleep(0.25)
            sys.stdout.write("\r\033[K")
        else:
            time.sleep(delay)
        if "scene" in e:
            marks.append({"scene": e["scene"], "start": round(time.perf_counter() - t0, 2)})
        else:
            print(e["line"], flush=True)
    total = round(time.perf_counter() - t0, 2)
    if args.timeline:
        json.dump({"total": total, "scenes": marks, "original_total": cast["total"]},
                  open(args.timeline, "w", encoding="utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
