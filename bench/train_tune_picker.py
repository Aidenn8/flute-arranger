"""Train the learned tune picker (melody.learned_tune) end to end.

    heat run .venv/bin/python -I bench/train_tune_picker.py render     # (a) ~45 min: excerpts -> audio -> our transcriber
    heat run .venv/bin/python -I bench/train_tune_picker.py simulate   # (b) only: error model + simulated set (~4 min)
    heat run .venv/bin/python -I bench/train_tune_picker.py train      # (b) + features + model -> models/tune_picker/ (~5 min)
    heat run .venv/bin/python -I bench/train_tune_picker.py all        # render, then train

Data (bench/tune_data.py, sources and licenses in bench/TRAINING.md): POP909 and
ASAP in data/ (download: bench/TRAINING.md), split by piece into train / val / test.
Then bench/tune_picker_judge.py compares rules and learned on the reference recordings.

1. render: RENDER_PLAN excerpts of EXCERPT_S seconds per split are rendered with
   the piano soundfont (pedal-held ends, random room) and transcribed by the
   app's own piano model (sheetswap.transcribe.piano), labels carried over by
   matching notes. Cached in data/tune_cache/real/ (resumable).
2. train: the transcriber's errors on the training excerpts are measured
   (tune_data.ErrorModel) and replayed on whole training pieces; together with
   the rendered training excerpts that is the training set. Features per note
   (melody.tune_features), a HistGradientBoostingClassifier, then the line step
   (melody.learned_tune) with its step cost chosen on the validation excerpts.
   Validation and test are always rendered + transcribed excerpts, never
   simulated ones. Writes models/tune_picker/{model.pkl, meta.json} and
   bench/tune_picker_results.md.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import zlib
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "bench"))

import tune_data as td  # noqa: E402

REAL = td.CACHE / "real"
WAV_DIR = td.CACHE / "wav"
EXCERPT_S = 90.0
# excerpts rendered + transcribed per split and dataset
RENDER_PLAN = {"train": {"pop909": 10, "asap": 12}, "val": {"pop909": 5, "asap": 6},
               "test": {"pop909": 3, "asap": 4}}
SEED = 7


def _safe(pid: str) -> str:
    return pid.replace("/", "__").replace(" ", "_").replace("@", "_at")


def render_plan(rng: np.random.Generator) -> list[td.Piece]:
    """The excerpts to render: per split, pieces drawn without replacement (one
    performance per ASAP piece), a window of EXCERPT_S from each."""
    pops = td.pop909_pieces(np.random.default_rng(SEED))
    asaps = td.asap_pieces({"train": 1, "val": 1, "test": 1})
    out = []
    for split, want in RENDER_PLAN.items():
        for name, pool in (("pop909", pops), ("asap", asaps)):
            cand = [p for p in pool if p.split == split and p.start.max() > EXCERPT_S + 10]
            pick = rng.choice(len(cand), min(want[name], len(cand)), replace=False)
            for k in pick:
                p = cand[int(k)]
                t0 = float(rng.uniform(0, p.start.max() - EXCERPT_S))
                out.append(p.window(t0, t0 + EXCERPT_S))
    return out


def cmd_render(first: int | None = None) -> None:
    rng = np.random.default_rng(SEED)
    plan = render_plan(rng)
    REAL.mkdir(parents=True, exist_ok=True)
    WAV_DIR.mkdir(parents=True, exist_ok=True)
    t_all = time.time()
    for i, p in enumerate(plan[:first]):
        f = REAL / f"{_safe(p.pid)}.json"
        if f.exists():
            continue
        t0 = time.time()
        wav = WAV_DIR / f"{_safe(p.pid)}.wav"
        trans = td.render_and_transcribe(p, wav, np.random.default_rng(SEED + i))
        wav.unlink(missing_ok=True)
        tp = td.transcribed_piece(trans, p)
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps(dict(source=p.to_json(), transcribed=tp.to_json())))
        tmp.replace(f)
        print(f"[{i + 1}/{len(plan)}] {p.split:5s} {p.pid}: {len(p)} notes -> {len(tp)} heard "
              f"({time.time() - t0:.0f} s)", flush=True)
    print(f"render done in {time.time() - t_all:.0f} s", flush=True)


def load_real() -> list[tuple[td.Piece, td.Piece]]:
    """(source excerpt, its transcription) pairs, velocities as heard on a real
    piano (tune_data.heard_velocities)."""
    out = []
    for f in sorted(REAL.glob("*.json")):
        d = json.loads(f.read_text())
        src, tr = td.Piece.from_json(d["source"]), td.Piece.from_json(d["transcribed"])
        seed = SEED + zlib.crc32(f.name.encode())
        out.append((src, td.heard_velocities(tr, src, np.random.default_rng(seed))))
    return out


# ---------------------------------------------------------------- features + labels

SIM = td.CACHE / "sim"
ERRORS = td.CACHE / "error_model.json"
MODEL_DIR = ROOT / "models" / "tune_picker"
RESULTS = ROOT / "bench" / "tune_picker_results.md"
CHUNK_S = 120.0            # simulated pieces are cut into windows this long
N_POP_TRAIN = 300          # POP909 training songs simulated (of ~640)
ASAP_PER_PIECE = 2         # ASAP training performances per piece
WORKERS = 4
# the line step's settings tried on the validation excerpts
LINE_GRID = [dict(bias=b, step=st, leap=lp, repeat=rp) for b in (-0.5, 0.0, 0.5, 1.0)
             for st in (0.0, 0.03, 0.06, 0.1) for lp in (0.0, 0.5) for rp in (0.0, 0.5, 1.0)]


def merged(p: td.Piece) -> td.Piece:
    """Same-key notes struck within SAME_ONSET made one, as tune_features does
    (labels: either one)."""
    from sheetswap.melody import SAME_ONSET
    keep, last = [], {}
    rows = []
    for i in np.lexsort((-p.pitch, p.start)):
        q = int(p.pitch[i])
        j = last.get(q)
        if j is not None and p.start[i] - rows[j][1] < SAME_ONSET:
            r = rows[j]
            rows[j] = (q, r[1], max(r[2], p.end[i]), max(r[3], p.vel[i]), max(r[4], p.label[i]),
                       max(r[5], p.weight[i]))
            continue
        last[q] = len(rows)
        rows.append((q, p.start[i], p.end[i], p.vel[i], p.label[i], p.weight[i]))
        keep.append(i)
    a = np.array(rows, float).reshape(-1, 6)
    return td.Piece(p.pid, p.source, p.split, a[:, 0].astype(int), a[:, 1], a[:, 2], a[:, 3],
                    a[:, 4].astype(int), a[:, 5], p.pedal)


def featurize(p: td.Piece) -> dict:
    """Features, labels and weights of piece p's notes (as tune_features orders them)."""
    from sheetswap.melody import tune_features
    from sheetswap.model import Note
    m = merged(p)
    notes = [Note(int(q), float(s), float(max(e, s + 1e-3)), int(round(v)))
             for q, s, e, v in zip(m.pitch, m.start, m.end, m.vel)]
    ns, X = tune_features(notes)
    order = np.lexsort((-m.pitch, m.start))
    assert [x.pitch for x in ns] == m.pitch[order].tolist(), p.pid
    return dict(X=X, y=m.label[order], w=m.weight[order], pid=p.pid, source=p.source,
                pitch=m.pitch[order], start=m.start[order])


def _sim_job(args) -> str:
    pid, piece_json, seed = args
    out = SIM / f"{_safe(pid)}.npz"
    if out.exists():
        return str(out)
    em = td.ErrorModel.load(ERRORS)
    p = td.Piece.from_json(piece_json)
    f = featurize(em.corrupt(p, np.random.default_rng(seed)))
    np.savez_compressed(out, X=f["X"], y=f["y"], w=f["w"], source=f["source"])
    return str(out)


def build_errors(real) -> td.ErrorModel:
    from sheetswap.model import Note
    pairs = []
    for src, tr in real:
        if src.split == "train":
            pairs.append((src, [Note(int(q), s, e, int(v)) for q, s, e, v in
                                zip(tr.pitch, tr.start, tr.end, tr.vel)]))
    em = td.ErrorModel.measure(pairs)
    em.save(ERRORS)
    return em


def build_sim() -> list[Path]:
    """Training pieces, cut into CHUNK_S windows, through the error model; features cached."""
    from concurrent.futures import ProcessPoolExecutor
    SIM.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(SEED + 1)
    pops = [p for p in td.pop909_pieces(np.random.default_rng(SEED)) if p.split == "train"]
    pops = [pops[int(k)] for k in rng.choice(len(pops), min(N_POP_TRAIN, len(pops)), replace=False)]
    asaps = td.asap_pieces({"train": ASAP_PER_PIECE, "val": 0, "test": 0})
    jobs = []
    for p in pops + asaps:
        t, end = 0.0, float(p.start.max())
        while t < end:
            w = p.window(t, t + CHUNK_S)
            if len(w) >= 30:
                jobs.append((w.pid, w.to_json(), int(rng.integers(1 << 30))))
            t += CHUNK_S
    print(f"simulating {len(jobs)} windows of {len(pops)} POP909 songs and {len(asaps)} ASAP performances",
          flush=True)
    t0, done = time.time(), []
    with ProcessPoolExecutor(WORKERS) as ex:
        for k, f in enumerate(ex.map(_sim_job, jobs, chunksize=4)):
            done.append(Path(f))
            if (k + 1) % 100 == 0:
                print(f"  {k + 1}/{len(jobs)} ({time.time() - t0:.0f} s)", flush=True)
    return done


def stack(parts: list[dict]):
    X = np.concatenate([d["X"] for d in parts])
    y = np.concatenate([d["y"] for d in parts]).astype(int)
    w = np.concatenate([d["w"] for d in parts]).astype(float)
    return X, y, w


# ---------------------------------------------------------------- scoring

def scores(picked: np.ndarray, y: np.ndarray, w: np.ndarray) -> dict:
    s = w > 0
    tp = int(np.sum(picked & (y == 1) & s))
    fp = int(np.sum(picked & (y == 0) & s))
    fn = int(np.sum(~picked & (y == 1) & s))
    pr = tp / max(tp + fp, 1)
    rc = tp / max(tp + fn, 1)
    return dict(tp=tp, fp=fp, fn=fn, precision=pr, recall=rc, f1=2 * pr * rc / max(pr + rc, 1e-9))


def picks(f: dict, prob: np.ndarray, cfg: dict) -> np.ndarray:
    """Which of f's notes the learned line takes."""
    from sheetswap.melody import line_from_probabilities
    from sheetswap.model import Note
    ns = [Note(int(q), float(s), float(s) + 1.0, 64) for q, s in zip(f["pitch"], f["start"])]
    line = line_from_probabilities(ns, prob, dict(cfg, hold_restruck=False))
    got = {(x.pitch, round(x.start, 6)) for x in line}
    return np.array([(int(q), round(float(s), 6)) in got for q, s in zip(f["pitch"], f["start"])])


def total(rows: list[dict]) -> dict:
    tp, fp, fn = (sum(r[k] for r in rows) for k in ("tp", "fp", "fn"))
    pr, rc = tp / max(tp + fp, 1), tp / max(tp + fn, 1)
    return dict(tp=tp, fp=fp, fn=fn, precision=pr, recall=rc, f1=2 * pr * rc / max(pr + rc, 1e-9))


def judge_split(clf, feats: list[dict], cfg: dict, probs=None) -> tuple[list[dict], list[dict]]:
    """Per excerpt: the rules' final tune and the learned line, scored."""
    from sheetswap.melody import FEATURES
    col = FEATURES.index("rule_tune")
    if probs is None:
        probs = [clf.predict_proba(f["X"])[:, 1] for f in feats]
    rules, learned = [], []
    for f, prob in zip(feats, probs):
        rules.append(dict(scores(f["X"][:, col] > 0.5, f["y"], f["w"]), pid=f["pid"], source=f["source"]))
        learned.append(dict(scores(picks(f, prob, cfg), f["y"], f["w"]), pid=f["pid"], source=f["source"]))
    return rules, learned


class WithoutRules:
    """A classifier that never sees the rules' picks (the rule_* and piece_voiced columns)."""

    @staticmethod
    def hide(X: np.ndarray) -> np.ndarray:
        from sheetswap.melody import FEATURES
        X = X.copy()
        for k, f in enumerate(FEATURES):
            if f.startswith("rule_") or f == "piece_voiced":
                X[:, k] = 0
        return X

    def __init__(self, clf):
        self.clf = clf

    def predict_proba(self, X):
        return self.clf.predict_proba(self.hide(X))


def fit(X, y, w, seed: int = 0):
    from sklearn.ensemble import HistGradientBoostingClassifier
    clf = HistGradientBoostingClassifier(max_iter=400, learning_rate=0.08, max_leaf_nodes=48,
                                         min_samples_leaf=60, l2_regularization=1.0,
                                         early_stopping=False, random_state=seed)
    s = w > 0
    clf.fit(X[s], y[s], sample_weight=w[s])
    return clf


def cmd_train() -> None:
    import pickle
    import sklearn
    from sheetswap.melody import FEATURES
    t_start = time.time()
    real = load_real()
    if not real:
        sys.exit("no rendered excerpts: run the render step first")
    em = build_errors(real)
    print("transcriber errors on the training excerpts:", json.dumps(em.summary()), flush=True)
    feats = {sp: [featurize(tr) for src, tr in real if tr.split == sp] for sp in ("train", "val", "test")}
    sim = [dict(np.load(f)) for f in build_sim()]
    print(f"features ready ({time.time() - t_start:.0f} s)", flush=True)

    # (name, training parts, may be saved); the last one is an ablation: the rules'
    # own picks hidden from the model, to see what it learns without them
    variants = [("real excerpts only", feats["train"], True),
                ("simulated + real", sim + feats["train"], True),
                ("simulated + real, without the rules' picks", sim + feats["train"], False)]
    best = None
    report = []
    for name, parts, eligible in variants:
        X, y, w = stack(parts)
        t0 = time.time()
        if eligible:
            clf = fit(X, y, w)
        else:
            clf = WithoutRules(fit(WithoutRules.hide(X), y, w))
        took = time.time() - t0
        # line step settings, on the validation excerpts
        tried = []
        probs = [clf.predict_proba(f["X"])[:, 1] for f in feats["val"]]
        for cfg in LINE_GRID:
            _, learned = judge_split(clf, feats["val"], cfg, probs)
            tried.append((total(learned)["f1"], cfg))
        f1, cfg = max(tried, key=lambda t: t[0])
        rules, learned = judge_split(clf, feats["val"], cfg)
        print(f"{name}: {len(y)} notes, fit {took:.0f} s; val F1 rules {total(rules)['f1']:.3f} "
              f"learned {total(learned)['f1']:.3f} with {cfg}", flush=True)
        report.append((name, len(y), took, total(rules), total(learned), cfg))
        if eligible and (best is None or f1 > best[0]):
            best = (f1, name, clf, cfg, took)
    f1, name, clf, cfg, took = best
    rules_v, learned_v = judge_split(clf, feats["val"], cfg)
    rules_t, learned_t = judge_split(clf, feats["test"], cfg)

    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    with open(MODEL_DIR / "model.pkl", "wb") as fh:
        pickle.dump(clf, fh)
    meta = dict(features=FEATURES, line=dict(cfg, hold_restruck=True), trained_on=name,
                sklearn=sklearn.__version__, date=time.strftime("%Y-%m-%d"),
                val=dict(rules=total(rules_v), learned=total(learned_v)),
                test=dict(rules=total(rules_t), learned=total(learned_t)),
                errors=em.summary(), fit_seconds=round(took, 1),
                total_seconds=round(time.time() - t_start, 1))
    (MODEL_DIR / "meta.json").write_text(json.dumps(meta, indent=1))
    write_results(report, name, cfg, em, rules_v, learned_v, rules_t, learned_t, clf, feats["val"])
    print(f"saved {MODEL_DIR} ({name}); test F1 rules {total(rules_t)['f1']:.3f} "
          f"learned {total(learned_t)['f1']:.3f}; {time.time() - t_start:.0f} s in all", flush=True)


def write_results(report, name, cfg, em, rules_v, learned_v, rules_t, learned_t, clf, val_feats) -> None:
    def row(r):
        return f"{r['precision']:.3f} | {r['recall']:.3f} | {r['f1']:.3f}"
    L = ["# Learned tune picker: training results", "",
         "Written by `bench/train_tune_picker.py`. Notes are our piano transcription of rendered",
         "excerpts (bench/TRAINING.md); a note is a tune note if it matches one of the source's tune",
         "notes (same key, onset within 60 ms). *Rules* = piano_melody + fill_tune (the app's",
         "picker), *learned* = melody.learned_tune. Scored per transcribed note: notes not scored",
         "(POP909's BRIDGE track, ASAP notes with no score note) are left out.", "",
         "## Transcriber errors measured on the training excerpts", "",
         "```", json.dumps(em.summary(), indent=1), "```", "",
         "## Training variants (validation excerpts)", "",
         "| trained on | notes | fit (s) | rules P / R / F1 | learned P / R / F1 | line step |",
         "|---|---|---|---|---|---|"]
    for n, k, took, r, l, c in report:
        L.append(f"| {n} | {k} | {took:.0f} | {row(r).replace(' | ', ' / ')} | "
                 f"{row(l).replace(' | ', ' / ')} | {c} |")
    for title, rr, ll in (("Validation excerpts", rules_v, learned_v), ("Test excerpts", rules_t, learned_t)):
        L += ["", f"## {title} (model: {name})", "",
              "| excerpt | rules P | R | F1 | learned P | R | F1 |", "|---|---|---|---|---|---|---|"]
        for r, l in zip(rr, ll):
            L.append(f"| {r['pid']} | {row(r)} | {row(l)} |")
        for src in ("pop909", "asap"):
            r2 = [r for r in rr if r["source"] == src]
            l2 = [r for r in ll if r["source"] == src]
            if r2:
                L.append(f"| *{src} notes* | {row(total(r2))} | {row(total(l2))} |")
        L.append(f"| **all notes** | {row(total(rr))} | {row(total(ll))} |")
    try:
        from sklearn.inspection import permutation_importance
        from sheetswap.melody import FEATURES
        X, y, w = stack(val_feats)
        imp = permutation_importance(clf, X, y, n_repeats=3, random_state=0, scoring="roc_auc")
        top = np.argsort(-imp.importances_mean)[:15]
        L += ["", "## Features that matter most (validation, permutation importance, ROC AUC drop)", ""]
        L += [f"- {FEATURES[k]}: {imp.importances_mean[k]:.4f}" for k in top]
    except Exception as exc:   # report only
        L += ["", f"(feature importance skipped: {exc})"]
    RESULTS.write_text("\n".join(L) + "\n")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["render", "simulate", "train", "all"])
    ap.add_argument("--first", type=int, default=None, help="render only the first N excerpts of the plan")
    a = ap.parse_args()
    if a.step in ("render", "all"):
        cmd_render(a.first)
    if a.step == "simulate":          # only the error model and the simulated training set
        em = build_errors(load_real())
        print(json.dumps(em.summary()), flush=True)
        build_sim()
    if a.step in ("train", "all"):
        cmd_train()


if __name__ == "__main__":
    main()
