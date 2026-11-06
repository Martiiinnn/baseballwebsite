"""
How much should a reliever's numbers be pulled toward the league average?

Split-half reliability: for every reliever-season with enough plate appearances, randomly split his
games into two halves of about m plate appearances each and correlate the halves across pitchers.
If the halves correlate at r, the number of league-average events to add (the regression constant) is

    K = m x (1 - r) / r

and a shrunk estimate is (his events + league rate x K) / (his chances + K).
K should come out roughly the same for every m if the method fits.

Components (they become reliable at very different speeds, so each gets its own K):
    strikeout rate                         per plate appearance
    walk rate                              per plate appearance
    xwOBA on the other plate appearances   per non-strikeout, non-walk plate appearance
    overall xwOBA                          for comparison with the old single constant (300)

Note: uses 2018-2020 regular seasons, so the constants themselves are not limited by date.
Run:  python3 analysis/estimate_regression_constants.py
"""
import random
import sqlite3
import statistics as st
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "site_data" / "coaches.db"
W_BB = 0.69          # wOBA value of a walk, 2018-2020 (approx.)
REPEATS = 30


def load():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = con.execute("""SELECT pitcher, season, pa, k, bb, xwoba_sum FROM pitcher_games
                          WHERE game_type = 'R' AND is_starter = 0 AND pa > 0""").fetchall()
    by = {}
    for r in rows:
        by.setdefault((r["pitcher"], r["season"]), []).append(r)
    return by


def rates(games):
    pa = sum(g["pa"] for g in games)
    k = sum(g["k"] for g in games)
    bb = sum(g["bb"] for g in games)
    x = sum(g["xwoba_sum"] for g in games)
    rest = pa - k - bb
    return {"k": k / pa, "bb": bb / pa, "con": (x - W_BB * bb) / rest if rest else None,
            "xw": x / pa, "rest_share": rest / pa}


def corr(a, b):
    ma, mb = st.mean(a), st.mean(b)
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    return num / (sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b)) ** 0.5


def halves(games, m, rng):
    g = games[:]
    rng.shuffle(g)
    h1, h2, n1, n2 = [], [], 0, 0
    for x in g:
        if n1 < m:
            h1.append(x); n1 += x["pa"]
        elif n2 < m:
            h2.append(x); n2 += x["pa"]
    return (h1, h2) if n2 >= 0.9 * m else None


def main():
    by = load()
    rng = random.Random(7)
    print("Split-half reliability of reliever components, 2018-2020 regular seasons")
    print(f"{'m (PA per half)':>15} {'pitcher-seasons':>16} | {'K%: r':>6} {'K':>5} | {'BB%: r':>6} {'K':>5} | "
          f"{'contact: r':>10} {'K':>6} | {'xwOBA: r':>8} {'K':>5}")
    for m in (40, 60, 80, 100):
        acc = {key: [] for key in ("k", "bb", "con", "xw")}
        shares = []
        for _ in range(REPEATS):
            pairs = [p for p in (halves(gs, m, rng) for gs in by.values()
                                 if sum(g["pa"] for g in gs) >= 2 * m) if p]
            stats = [(rates(a), rates(b)) for a, b in pairs]
            shares += [s[0]["rest_share"] for s in stats]
            for key in acc:
                ok = [(a[key], b[key]) for a, b in stats if a[key] is not None and b[key] is not None]
                acc[key].append(corr([x for x, _ in ok], [y for _, y in ok]))
        out = []
        for key in ("k", "bb", "con", "xw"):
            r = st.mean(acc[key])
            size = m * st.mean(shares) if key == "con" else m      # contact events per half
            out.append((r, size * (1 - r) / r))
        print(f"{m:>15} {len(pairs):>16} | {out[0][0]:>6.2f} {out[0][1]:>5.0f} | {out[1][0]:>6.2f} {out[1][1]:>5.0f} | "
              f"{out[2][0]:>10.2f} {out[2][1]:>6.0f} | {out[3][0]:>8.2f} {out[3][1]:>5.0f}")
    print("\nUsed on the site (model.py): strikeouts 60, walks 130, contact 600 "
          "(contact quality is barely repeatable within a season, so its K is large and uncertain).")


if __name__ == "__main__":
    main()
