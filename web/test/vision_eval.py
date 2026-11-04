"""Benchmark of small vision models on Mac screens (Nov 2, 2026).

Goal: pick the model that answers "look at my screen" (on demand, not continuously) and writes the short
"glance" cached when the front app changes.

Screens: web/test/vision/screens/*.png, synthetic (screens.html + render.mjs), 2880x1800 like a Retina screenshot,
downscaled here to --max-side px (1280 by default). For each screen: a question graded by keywords, then the
one-sentence glance (not graded, latency only). Each model runs in its own process (memory freed).

    uv run --no-project --python 3.12 --with mlx-vlm --with torchvision python web/test/vision_eval.py
    ... vision_eval.py --only qwen35_2b --max-side 1600
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCREENS = HERE / "vision" / "screens"

MODELS = {
    "qwen35_2b": "mlx-community/Qwen3.5-2B-MLX-8bit",
    "qwen35_4b": "mlx-community/Qwen3.5-4B-OptiQ-4bit",
    "qwen3vl_2b": "mlx-community/Qwen3-VL-2B-Instruct-4bit",
    "qwen3vl_4b": "mlx-community/Qwen3-VL-4B-Instruct-4bit",
    "minicpmv46": "mlx-community/MiniCPM-V-4.6-4bit",
    "lfm25vl_16b": "mlx-community/LFM2.5-VL-1.6B-4bit",
    "lfm3vl_2b": "mlx-community/LFM3-VL-2B-OptiQ-4bit",
    "fastvlm_05b": "mlx-community/FastVLM-0.5B-bf16",
    "gemma3_4b": "mlx-community/gemma-3-4b-it-qat-4bit",
}

# (screen, question, keyword groups: each group must appear, one word from the group is enough)
CASES = [
    ("code", "What's wrong with my code?", [["reponse", "typo", "misspel", "spelled", "spelling"]]),
    ("terminal", "Why did my build fail?", [["react-dom"]]),
    ("email", "Who sent this email and what are they asking?", [["claire"], ["thursday", "jeudi"]]),
    ("chart", "Which month has the highest bar on this chart?", [["apr"]]),
    ("dialog", "What does this popup say?", [["malware", "verify", "not opened", "could not"]]),
    ("youtube", "What's the title of the second video in the first row?", [["black hole"]]),
    ("save_button", "Where is the Save button on the screen?", [["bottom", "lower"], ["right"]]),
    ("status", "Which service is offline?", [["voice"]]),
    ("photo", "What is in this picture?", [["boat", "sail"]]),
    ("french", "De quoi parle cet article ?", [["tgv", "train"], ["turin"]]),
    ("sheet", "What's the total in the spreadsheet?", [["12 480", "12480", "12,480", "12.480", "12 480"]]),
    ("calendar", "What do I have on Wednesday afternoon?", [["marc"]]),
]

SYSTEM = ("You are a voice assistant looking at a screenshot of the user's Mac screen. Answer in one or two short "
          "sentences, in the language of the question.")
GLANCE = "In one short sentence: which app is in front and what is the user doing?"


def passed(text: str, groups: list[list[str]]) -> bool:
    t = text.lower().replace("’", "'")
    return all(any(k in t for k in g) for g in groups)


def run_model(key: str, max_side: int) -> None:
    """In the child process: one JSON line per measurement on stdout."""
    import mlx.core as mx
    from PIL import Image
    from mlx_vlm import generate, load
    from mlx_vlm.prompt_utils import apply_chat_template

    repo = MODELS[key]
    t0 = time.perf_counter()
    model, processor = load(repo)
    config = model.config
    print(json.dumps({"kind": "load", "model": key, "seconds": round(time.perf_counter() - t0, 2)}), flush=True)

    tmp = Path(tempfile.mkdtemp(prefix="vision_eval_"))
    def shrink(name: str) -> str:
        out = tmp / f"{name}.png"
        if not out.exists():
            im = Image.open(SCREENS / f"{name}.png").convert("RGB")
            im.thumbnail((max_side, max_side))
            im.save(out)
        return str(out)

    def ask(image: str, question: str, max_tokens: int):
        messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": question}]
        try:
            prompt = apply_chat_template(processor, config, messages, num_images=1, enable_thinking=False)
        except Exception:  # template without a system role
            prompt = apply_chat_template(processor, config, f"{SYSTEM}\n\n{question}", num_images=1, enable_thinking=False)
        start = time.perf_counter()
        r = generate(model, processor, prompt, image=[image], max_tokens=max_tokens, temperature=0.0,
                     enable_thinking=False, verbose=False)
        return r, time.perf_counter() - start

    ask(shrink("status"), GLANCE, 8)  # warm-up (kernel compilation), not counted
    for name, question, groups in CASES:
        img = shrink(name)
        for kind, q, n in (("question", question, 120), ("glance", GLANCE, 48)):
            try:
                r, seconds = ask(img, q, n)
                text = r.text.strip()
                row = {"kind": kind, "model": key, "case": name, "seconds": round(seconds, 2),
                       "prompt_tokens": r.prompt_tokens, "prompt_tps": round(r.prompt_tps, 1),
                       "gen_tokens": r.generation_tokens, "gen_tps": round(r.generation_tps, 1),
                       "peak_gb": round(r.peak_memory, 2), "text": text}
                if kind == "question":
                    row["ok"] = passed(text, groups)
            except Exception as e:  # noqa: BLE001
                row = {"kind": kind, "model": key, "case": name, "error": f"{type(e).__name__}: {e}"[:300]}
            print(json.dumps(row, ensure_ascii=False), flush=True)
        mx.clear_cache()


def report(rows: list[dict], max_side: int) -> str:
    lines = [f"Banc d'essai vision, {time.strftime('%Y-%m-%d %H:%M')}, captures réduites à {max_side} px, "
             "température 0, Mac M3 Air 24 Go, stack Voice Island lancé (LLM, voix), Docker supprimé.", ""]
    lines.append(f"{'modèle':<13} {'justes':>7} {'question méd.':>13} {'aperçu méd.':>11} {'tokens img':>10} "
                 f"{'lecture tok/s':>13} {'pic Go':>6} {'chargement':>10}")
    for key in MODELS:
        mine = [r for r in rows if r.get("model") == key]
        if not mine:
            continue
        load_s = next((r["seconds"] for r in mine if r["kind"] == "load"), None)
        err = next((r for r in mine if r["kind"] == "error"), None)
        q = [r for r in mine if r["kind"] == "question" and "seconds" in r]
        g = [r for r in mine if r["kind"] == "glance" and "seconds" in r]
        if not q:
            lines.append(f"{key:<13} échec : {(err or {}).get('error', 'aucune réponse')[:110]}")
            continue
        med = lambda xs: sorted(xs)[len(xs) // 2]  # noqa: E731
        lines.append(f"{key:<13} {sum(r['ok'] for r in q):>3}/{len(q):<3} {med([r['seconds'] for r in q]):>11.2f} s "
                     f"{med([r['seconds'] for r in g]) if g else 0:>9.2f} s {med([r['prompt_tokens'] for r in q]):>10} "
                     f"{med([r['prompt_tps'] for r in q]):>13.0f} {max(r['peak_gb'] for r in q):>6.1f} "
                     f"{load_s or 0:>8.1f} s")
    lines += ["", "Détail (✓ / ✗ = question notée par mots-clés ; « aperçu » non noté) :"]
    for key in MODELS:
        mine = [r for r in rows if r.get("model") == key and r["kind"] in ("question", "glance")]
        if not mine:
            continue
        lines += ["", f"== {key} ({MODELS[key]})"]
        for r in mine:
            if "error" in r:
                lines.append(f"  {r['case']:<12} {r['kind']:<8} ERREUR {r['error'][:150]}")
                continue
            mark = ("✓" if r["ok"] else "✗") if r["kind"] == "question" else "·"
            lines.append(f"  {mark} {r['case']:<12} {r['seconds']:>5.2f} s  {r['text'][:170].replace(chr(10), ' ')}")
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", help="(interne) lance un seul modèle dans ce processus")
    ap.add_argument("--only", nargs="*", help="clés de MODELS à tester")
    ap.add_argument("--max-side", type=int, default=1280)
    ap.add_argument("--out", default=str(HERE / f"vision_eval_{time.strftime('%Y-%m-%d')}"))
    a = ap.parse_args()
    if a.model:
        run_model(a.model, a.max_side)
        return
    rows: list[dict] = []
    raw = Path(a.out + ".jsonl")
    raw.write_text("")
    for key in a.only or list(MODELS):
        print(f"--- {key}", file=sys.stderr, flush=True)
        p = subprocess.Popen([sys.executable, __file__, "--model", key, "--max-side", str(a.max_side)],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=os.environ.copy())
        for line in p.stdout:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            rows.append(row)
            with raw.open("a") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
            if row["kind"] == "question":
                print(f"  {row['case']:<12} {'✓' if row.get('ok') else '✗'} {row.get('seconds', '-')} s", file=sys.stderr,
                      flush=True)
        err = p.stderr.read()
        if p.wait() != 0:
            rows.append({"kind": "error", "model": key, "error": err.strip().splitlines()[-1] if err.strip() else "?"})
            print(f"  ÉCHEC {rows[-1]['error'][:200]}", file=sys.stderr, flush=True)
    text = report(rows, a.max_side)
    Path(a.out + ".txt").write_text(text)
    print(text)


if __name__ == "__main__":
    main()
