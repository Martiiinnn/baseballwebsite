"""
Backtest: does a sudden strikeout drop tell us a reliever has changed?

Game 6 question: Nick Anderson struck out 42.8% of hitters in the 2019-20 regular seasons but only
9 of 57 (15.8%) in the 2020 postseason. Was that noise, or a sign he was no longer the same pitcher?

The test, using only data before each decision point:
  1. history  = the reliever's earlier games (up to two seasons back)
  2. recent   = his last block of about 50 plate appearances
  3. Bayes factor for strikeouts: how much more likely the recent strikeouts are if his talent
     changed (a fresh draw from the league) than if it stayed at his history-based estimate
  4. P(changed) = prior x Bayes factor / (prior x Bayes factor + 1 - prior)

Then predict the NEXT block of about 50 plate appearances three ways and score them:
  stable   all his games, each component shrunk toward the league average
  changed  only the recent block, shrunk toward the league average
  mixture  P(changed) x changed + (1 - P(changed)) x stable

Components and their regression constants (estimated by split-half reliability, see README):
  strikeout rate 60 PA, walk rate 130 PA, xwOBA on other plate appearances 600.

A decision point before every relief appearance (rolling windows within a season).
Walk-forward: decision points in 2019 choose the prior; 2020 is the out-of-sample test.
Only regular-season games. Data: site_data/coaches.db (pitcher_games).

Run:  python3 analysis/backtest_reliever_change.py
Out:  output/backtest_reliever_change.csv  (one row per decision point)
"""
import csv
import math
import random
import sqlite3
from math import lgamma
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "site_data" / "coaches.db"
OUT = ROOT / "output" / "backtest_reliever_change.csv"

BLOCK = 50                                   # plate appearances per block
MIN_HISTORY = 100                            # history needed before a decision point
K_REG = {"k": 60, "bb": 130, "con": 600}     # regression constants
W_BB = 0.69                                  # wOBA value of a walk, 2018-2020 (approx.)
PRIORS = (0.01, 0.02, 0.05, 0.10, 0.20, 0.30)


# ---------------------------------------------------------------- helpers

def add(a, b):
    return {key: a[key] + b[key] for key in a}


def empty():
    return {"pa": 0, "k": 0, "bb": 0, "rest": 0, "conx": 0.0}


def shrink(s, lg):
    """Each component shrunk toward the league: (events + league x K) / (chances + K)."""
    return {
        "k": (s["k"] + lg["k"] * K_REG["k"]) / (s["pa"] + K_REG["k"]),
        "bb": (s["bb"] + lg["bb"] * K_REG["bb"]) / (s["pa"] + K_REG["bb"]),
        "con": (s["conx"] + lg["con"] * K_REG["con"]) / (s["rest"] + K_REG["con"]),
    }


def xwoba(p):
    return W_BB * p["bb"] + (1 - p["k"] - p["bb"]) * p["con"]


def betaln(a, b):
    return lgamma(a) + lgamma(b) - lgamma(a + b)


def log_ml(x, n, mean, conc):
    """Beta-binomial log marginal likelihood (without the binomial coefficient, which cancels)."""
    a, b = mean * conc, (1 - mean) * conc
    return betaln(x + a, n - x + b) - betaln(a, b)


def k_logloss(p, k, n):
    p = min(max(p, 1e-6), 1 - 1e-6)
    return -(k * math.log(p) + (n - k) * math.log(1 - p))


# ---------------------------------------------------------------- data

def load():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    games = con.execute("""
        SELECT pitcher, pitcher_name, season, game_date, game_pk, pa, k, bb, xwoba_sum
        FROM pitcher_games
        WHERE game_type = 'R' AND is_starter = 0 AND pa > 0
        ORDER BY pitcher, game_date, game_pk""").fetchall()
    league = {}
    for season in (2018, 2019, 2020):
        r = con.execute("""SELECT SUM(pa), SUM(k), SUM(bb), SUM(xwoba_sum) FROM pitcher_games
                           WHERE game_type = 'R' AND is_starter = 0 AND season = ?""", (season,)).fetchone()
        league[season] = {"pa": r[0], "k": r[1], "bb": r[2], "x": r[3]}
    return games, league


def league_before(league, season):
    """League reliever averages from seasons before this one (no peeking at the current season)."""
    seasons = [s for s in league if s < season]
    pa = sum(league[s]["pa"] for s in seasons)
    k = sum(league[s]["k"] for s in seasons)
    bb = sum(league[s]["bb"] for s in seasons)
    x = sum(league[s]["x"] for s in seasons)
    return {"k": k / pa, "bb": bb / pa, "con": (x - W_BB * bb) / (pa - k - bb)}


def as_stats(g):
    return {"pa": g["pa"], "k": g["k"], "bb": g["bb"], "rest": g["pa"] - g["k"] - g["bb"],
            "conx": g["xwoba_sum"] - W_BB * g["bb"]}


def decision_points(games, league):
    """One decision point before every relief appearance (rolling windows, same season):
         recent  = his last games adding up to at least BLOCK plate appearances
         future  = this game and the next ones, adding up to at least BLOCK plate appearances
         history = everything before the recent window, up to two seasons back
    Neighbouring points overlap, so uncertainty is measured with a bootstrap by pitcher."""
    by_pitcher = {}
    for g in games:
        by_pitcher.setdefault(g["pitcher"], []).append(g)
    points = []
    for pid, gs in by_pitcher.items():
        for j, g in enumerate(gs):
            season = g["season"]
            if season < 2019:
                continue
            # recent window: walk backward within the season
            rec, i = empty(), j - 1
            while i >= 0 and gs[i]["season"] == season and rec["pa"] < BLOCK:
                rec = add(rec, as_stats(gs[i]))
                i -= 1
            if rec["pa"] < BLOCK:
                continue
            # future window: walk forward within the season
            fut, f = empty(), j
            while f < len(gs) and gs[f]["season"] == season and fut["pa"] < BLOCK:
                fut = add(fut, as_stats(gs[f]))
                f += 1
            if fut["pa"] < BLOCK:
                continue
            hist = empty()
            for h in gs[:i + 1]:
                if h["season"] >= season - 2:
                    hist = add(hist, as_stats(h))
            if hist["pa"] < MIN_HISTORY:
                continue
            points.append({"pitcher": pid, "name": g["pitcher_name"], "season": season, "date": g["game_date"],
                           "hist": hist, "recent": rec, "future": fut, "lg": league_before(league, season)})
    return points


# ---------------------------------------------------------------- the test

def score(p, prior):
    lg, hist, rec, fut = p["lg"], p["hist"], p["recent"], p["future"]
    hist_p = shrink(hist, lg)
    conc = hist["pa"] + K_REG["k"]
    # Bayes factor on strikeouts: changed (fresh league draw) vs. unchanged (his history)
    bf = math.exp(log_ml(rec["k"], rec["pa"], lg["k"], K_REG["k"]) - log_ml(rec["k"], rec["pa"], hist_p["k"], conc))
    pc = prior * bf / (prior * bf + 1 - prior)
    stable = shrink(add(hist, rec), lg)
    changed = shrink(rec, lg)
    mix = {key: pc * changed[key] + (1 - pc) * stable[key] for key in stable}
    exp_k = rec["pa"] * hist_p["k"]
    pd = pc if rec["k"] < exp_k else 0.0          # decline-only: ignore sudden strikeout jumps
    mix_down = {key: pd * changed[key] + (1 - pd) * stable[key] for key in stable}
    z = (rec["k"] - exp_k) / math.sqrt(rec["pa"] * hist_p["k"] * (1 - hist_p["k"]) * (1 + rec["pa"] / conc))
    fx = (fut["conx"] + W_BB * fut["bb"]) / fut["pa"]
    return {"bf": bf, "p_changed": pc, "z": z, "stable": stable, "changed": changed, "mix": mix, "mix_down": mix_down,
            "future_k": fut["k"] / fut["pa"], "future_xwoba": fx, "hist_k": hist_p["k"]}


def evaluate(points, prior):
    tot = {name: [0.0, 0.0] for name in ("stable", "mix", "mix_down", "changed", "league")}
    n_pa = 0
    for p in points:
        s = score(p, prior)
        fut = p["future"]
        n_pa += fut["pa"]
        preds = {"stable": s["stable"], "mix": s["mix"], "mix_down": s["mix_down"], "changed": s["changed"],
                 "league": p["lg"]}
        for name, pr in preds.items():
            tot[name][0] += k_logloss(pr["k"], fut["k"], fut["pa"])
            tot[name][1] += fut["pa"] * (xwoba(pr) - s["future_xwoba"]) ** 2
    return {name: (v[0] / n_pa, math.sqrt(v[1] / n_pa)) for name, v in tot.items()}


def flagged_table(points, prior, label):
    print(f"\n{label}: decision points grouped by how far recent strikeouts fell below expectation")
    print("  z of recent K      points pitchers  stable K%  mixture K%  actual next K% |  stable xwOBA  mixture  actual next")
    groups = [("z <= -3", lambda z: z <= -3), ("-3 < z <= -2", lambda z: -3 < z <= -2),
              ("-2 < z < 2", lambda z: -2 < z < 2), ("z >= 2", lambda z: z >= 2)]
    for name, test in groups:
        rows = [(p, score(p, prior)) for p in points]
        rows = [(p, s) for p, s in rows if test(s["z"])]
        if not rows:
            continue
        w = sum(p["future"]["pa"] for p, _ in rows)
        m = lambda f: sum(f(p, s) * p["future"]["pa"] for p, s in rows) / w
        print(f"  {name:18} {len(rows):5} {len({p['pitcher'] for p, _ in rows}):8}    {m(lambda p, s: s['stable']['k']):.3f}      {m(lambda p, s: s['mix']['k']):.3f}"
              f"        {m(lambda p, s: s['future_k']):.3f}      |    {m(lambda p, s: xwoba(s['stable'])):.3f}      "
              f"{m(lambda p, s: xwoba(s['mix'])):.3f}     {m(lambda p, s: s['future_xwoba']):.3f}")


def bootstrap_gain(points, prior, model="mix", reps=2000, seed=3):
    """Cluster bootstrap by pitcher: how sure are we the mixture beats 'stable' on strikeouts?"""
    by = {}
    for p in points:
        s = score(p, prior)
        f = p["future"]
        gain = k_logloss(s["stable"]["k"], f["k"], f["pa"]) - k_logloss(s[model]["k"], f["k"], f["pa"])
        by.setdefault(p["pitcher"], []).append((gain, f["pa"]))
    ids = list(by)
    rng = random.Random(seed)
    wins, vals = 0, []
    for _ in range(reps):
        sample = [x for pid in (rng.choice(ids) for _ in ids) for x in by[pid]]
        g = sum(x[0] for x in sample) / sum(x[1] for x in sample)
        vals.append(g)
        wins += g > 0
    vals.sort()
    return wins / reps, vals[int(0.05 * reps)], vals[int(0.95 * reps)]


def main():
    games, league = load()
    points = decision_points(games, league)
    train = [p for p in points if p["season"] == 2019]
    test = [p for p in points if p["season"] == 2020]
    print(f"Decision points: {len(train)} in 2019 (choose the prior), {len(test)} in 2020 (test). "
          f"Blocks of {BLOCK} PA, history >= {MIN_HISTORY} PA.")

    print("\n2019: strikeout log loss per PA (lower is better) and xwOBA RMSE for each prior")
    best = None
    for prior in PRIORS:
        r = evaluate(train, prior)
        print(f"  prior {prior:.0%}: mixture {r['mix'][0]:.4f} / {r['mix'][1]:.4f}   "
              f"(stable {r['stable'][0]:.4f} / {r['stable'][1]:.4f})")
        if best is None or r["mix"][0] < best[1]:
            best = (prior, r["mix"][0])
    prior = best[0]
    print(f"  -> chosen prior: {prior:.0%}")

    r = evaluate(test, prior)
    print(f"\n2020 out of sample (prior {prior:.0%}):")
    for name in ("league", "changed", "stable", "mix", "mix_down"):
        print(f"  {name:8} strikeout log loss {r[name][0]:.4f}   xwOBA RMSE {r[name][1]:.4f}")
    for model in ("mix", "mix_down"):
        share, lo, hi = bootstrap_gain(test, prior, model)
        print(f"  {model} beats stable on strikeouts in {share:.0%} of bootstrap samples by pitcher "
              f"(gain per PA, 90%: {lo:+.4f} to {hi:+.4f})")

    flagged_table(train + test, prior, "2019-2020")

    OUT.parent.mkdir(exist_ok=True)
    with OUT.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["pitcher", "name", "season", "decision_date", "hist_pa", "recent_pa", "recent_k",
                    "expected_k_rate", "z", "bayes_factor", "p_changed", "stable_k", "mixture_k",
                    "future_pa", "future_k_rate", "stable_xwoba", "mixture_xwoba", "future_xwoba"])
        for p in points:
            s = score(p, prior)
            w.writerow([p["pitcher"], p["name"], p["season"], p["date"], p["hist"]["pa"], p["recent"]["pa"],
                        p["recent"]["k"], round(s["hist_k"], 3), round(s["z"], 2), round(s["bf"], 2),
                        round(s["p_changed"], 3), round(s["stable"]["k"], 3), round(s["mix"]["k"], 3),
                        p["future"]["pa"], round(s["future_k"], 3), round(xwoba(s["stable"]), 3),
                        round(xwoba(s["mix"]), 3), round(s["future_xwoba"], 3)])
    print(f"\nWrote {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
