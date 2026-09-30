"""Genera el video demo (MP4, 1280×720) a partir del registro de una corrida real de `make demo`.

Uso (requiere Pillow, que no es dependencia del proyecto; usa un entorno aparte):
    DEMO_CAST=/tmp/cast.json make demo
    python scripts/make_video.py /tmp/cast.json docs/demo/legacybridge-demo.mp4 [--target 66]

Cada cuadro se dibuja con la salida REAL registrada (misma fuente de verdad que `make demo-replay`);
solo se comprimen las esperas del modelo, y el rótulo inicial lo declara. Los subtítulos son la narración
de docs/demo/SCRIPT.md. Se optó por dibujar los cuadros en lugar de grabar la terminal porque VHS se
congelaba a mitad de la grabación.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.replay_demo import MIN_WAIT, plan  # noqa: E402

W, H = 1280, 720
BG, BAR, BAND = "#1e1e2e", "#181825", "#11111b"
FG, DIM = "#cdd6f4", "#6c7086"
MONO = ImageFont.truetype("/System/Library/Fonts/Menlo.ttc", 15)
MONO_B = ImageFont.truetype("/System/Library/Fonts/Menlo.ttc", 15, index=1)
SANS = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 27)
SANS_B = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 50, index=1)
SANS_M = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 30)
TOP, LINE_H, BAND_H, LEFT = 46, 20, 118, 26
ROWS = (H - TOP - BAND_H - 10) // LINE_H
MIN_SCENE = 7.0      # segundos mínimos en pantalla por escena (la 3 dura 0.02 s en la corrida real)
TITLE_S, END_S = 5.0, 7.0

NARRATION = {
    0: "Legacy ERPs have cryptic names, no foreign keys, dates as text and NULL flags. "
       "Generic text-to-SQL gets them confidently wrong.",
    1: "Inactive customers: a naive query counts 'N' and misses NULLs. The agent read the business rule "
       "— and shows the exact SQL and rows it used.",
    2: "Valid orders only, text dates parsed, and pesos never added to dollars.",
    3: "Ask for passwords and it's refused before the model is even called.",
    4: "This order note tries to hijack the agent. It's stripped before the model reads it; "
       "the audit copy keeps it.",
    5: "A person's tax ID is personal data — masked on output. Company IDs stay visible.",
    6: "Asked to delete data, it drafts the statement and estimates the impact — then waits for a human. "
       "Nothing is ever executed.",
    7: "95% execution accuracy on a held-out test set, measured on result sets, at zero model cost.",
}


def color(line: str) -> tuple[str, ImageFont.FreeTypeFont]:
    s = line.strip()
    if s.startswith("[") and "/" in s[:6]:
        return "#f9e2af", MONO_B
    if s.startswith("Q:"):
        return "#89dceb", MONO
    if s.startswith("A:"):
        return "#a6e3a1", MONO
    if s.startswith(("evidence:", "rows")):
        return "#a6adc8", MONO
    if s.startswith("outcome="):
        return "#cba6f7", MONO
    if s.startswith(("→", "SQL:")):
        return "#fab387", MONO
    if s.startswith("─"):
        return "#45475a", MONO
    return FG, MONO


def subtitle(draw: ImageDraw.ImageDraw, text: str) -> None:
    draw.rectangle([0, H - BAND_H, W, H], fill=BAND)
    lines = textwrap.wrap(text, 78)[:3]
    y = H - BAND_H + (BAND_H - len(lines) * 34) // 2
    for ln in lines:
        w = draw.textlength(ln, font=SANS)
        draw.text(((W - w) / 2, y), ln, font=SANS, fill="#ffffff")
        y += 34


def terminal(lines: list[str], spinner: str | None, scene: int) -> Image.Image:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 32], fill=BAR)
    for i, c in enumerate(("#f38ba8", "#f9e2af", "#a6e3a1")):
        d.ellipse([14 + i * 20, 10, 26 + i * 20, 22], fill=c)
    d.text((90, 8), "legacybridge — make demo-replay   ·   replay of a real run, model waits sped up",
           font=MONO, fill=DIM)
    view = lines[-(ROWS - (1 if spinner else 0)):]
    y, prev = TOP, (FG, MONO)
    for ln in view:
        fg, font = color(ln)
        # una línea envuelta (sangría sin prefijo propio) continúa el bloque anterior y hereda su color
        if (fg, font) == (FG, MONO) and ln.startswith("  ") and ln.strip() and prev[0] not in (FG, "#f9e2af"):
            fg, font = prev
        prev = (fg, font) if ln.strip() else (FG, MONO)
        d.text((LEFT, y), ln, font=font, fill=fg)
        y += LINE_H
    if spinner:
        d.text((LEFT, y), spinner, font=MONO, fill="#f9e2af")
    subtitle(d, NARRATION.get(scene, ""))
    return img


def card(title: str, lines: list[str], narration: str) -> Image.Image:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    y = 170
    w = d.textlength(title, font=SANS_B)
    d.text(((W - w) / 2, y), title, font=SANS_B, fill="#f5e0dc")
    y += 90
    for ln in lines:
        w = d.textlength(ln, font=SANS_M)
        d.text(((W - w) / 2, y), ln, font=SANS_M, fill=FG)
        y += 46
    subtitle(d, narration)
    return img


def states(cast: dict, target: float) -> list[tuple[list[str], str | None, int, float]]:
    """(líneas, indicador, escena, duración) a partir del plan de reproducción."""
    out, lines, scene, scene_t, t = [], [], 0, 0.0, 0.0

    def push(spin, dur):
        nonlocal t
        out.append((list(lines), spin, scene, dur))
        t += dur

    for delay, e in plan(cast["events"], target):
        if delay >= MIN_WAIT:
            steps = max(1, int(delay / 0.5))
            for i in range(steps):
                push(f"  … model working{'.' * (i % 4)}", delay / steps)
        elif delay and out:
            lst, spin, sc, dur = out[-1]
            out[-1] = (lst, spin, sc, dur + delay)
            t += delay
        if "scene" in e:
            if scene and t - scene_t < MIN_SCENE:          # la escena anterior se alcanza a leer
                push(None, MIN_SCENE - (t - scene_t))
            scene, scene_t = e["scene"], t
        else:
            lines.append(e["line"])
            push(None, 0.0)
    push(None, 3.0)
    return [s for s in out if s[3] > 0]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="make_video", description=__doc__.splitlines()[0])
    ap.add_argument("cast")
    ap.add_argument("out")
    ap.add_argument("--target", type=float, default=66.0)
    args = ap.parse_args(argv)
    cast = json.load(open(args.cast, encoding="utf-8"))
    tmp = Path(tempfile.mkdtemp(prefix="lbvideo-"))
    frames = [(card("LegacyBridge", ["An AI agent for legacy ERP data, through MCP",
                                     "Replay of a real run · local Qwen3.6 · synthetic data",
                                     "Model waits sped up"], NARRATION[0]), TITLE_S)]
    frames += [(terminal(lines, spin, sc), dur) for lines, spin, sc, dur in states(cast, args.target)]
    frames.append((card("Results on a held-out test set", ["95.4% execution accuracy · 3.8% silent wrong answers",
                                                          "30 attack prompts: 0 leaks, 0 writes · $0 model cost",
                                                          "github.com/mcorona/AI-projects-legacybridge"],
                        NARRATION[7]), END_S))
    lst = []
    for i, (img, dur) in enumerate(frames):
        f = tmp / f"f{i:05d}.png"
        img.save(f)
        lst.append(f"file '{f}'\nduration {dur:.3f}")
    lst.append(f"file '{tmp / f'f{len(frames) - 1:05d}.png'}'")     # el concat exige repetir el último
    (tmp / "list.txt").write_text("\n".join(lst))
    total = sum(d for _, d in frames)
    # -t: la última entrada repetida del concat hereda una duración extra; se corta al total exacto
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(tmp / "list.txt"),
                    "-t", f"{total:.3f}", "-vf", "fps=25,format=yuv420p", "-c:v", "libx264", "-preset", "medium",
                    "-crf", "20", "-movflags", "+faststart", args.out], check=True)
    shutil.rmtree(tmp)
    print(f"{args.out}: {len(frames)} cuadros distintos, {total:.1f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
