"""
Every number the README quotes about Game 6, reproduced from site_data/coaches.db.

1. Comparable starts: how many there were at each step (selection by the manager)
2. Regression to the mean: the same starts through two times vs. the third time
3. Uncertainty of the starter's number: three ways to compute it
4. How the answer moved as the method was fixed (v1 site -> v2 site)
5. Sensitivity of v2: starter filters x prior x contact regression constant
6. How noisy one inning is compared with the estimate

Run:  python3 analysis/game6_checks.py
"""
import math
import random
import statistics as st
import sys
from itertools import product
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import model as m          # noqa: E402
import queries as q        # noqa: E402

AS_OF, STARTER, RELIEVER, N = "2020-10-27", 605483, 623433, 3
FILTERS = (0.35, 0.200, 85)       # Snell through two times: 50% K, .100 wOBA, 73 pitches -> "about as dominant"


def comparable_rows(con, k_min, woba_max, pitch_max):
    return con.execute("""
        SELECT pitcher, early_pa, early_woba_sum, early_xwoba_sum, third_pa, third_woba_sum, third_woba_sq,
               third_xwoba_sum
        FROM starter_games
        WHERE game_type = 'R' AND early_pa >= 18 AND game_date < ?
          AND 1.0 * early_k / early_pa >= ? AND early_woba_sum / early_pa <= ? AND early_pitches <= ?""",
                       (AS_OF, k_min, woba_max, pitch_max)).fetchall()


def section(title):
    print(f"\n{'=' * 4} {title} {'=' * (90 - len(title))}")


def main():
    con = q.connect()
    li, _ = q.leverage(con, 6, "Bot", 1, 1, 0, 0, 1)
    pts = N / q.WOBA_SCALE * li / q.RUNS_PER_WIN * 100
    print(f"Leverage {li:.2f}; at this leverage over {N} batters, .010 of xwOBA = {pts * 0.010:.2f} points "
          f"(so .016 = 1 point)")

    # 1 + 2 -------------------------------------------------------------------------------------
    section("1-2. Comparable starts and regression to the mean")
    rows = comparable_rows(con, *FILTERS)
    allrows = comparable_rows(con, 0, 9, 999)
    for name, rs in (("about as dominant as Snell", rows), ("all starts with 18+ batters", allrows)):
        n = len(rs)
        epa = sum(r["early_pa"] for r in rs)
        tpa = sum(r["third_pa"] for r in rs)
        print(f"{name}: {n} starts by {len({r['pitcher'] for r in rs})} pitchers | reached a third time "
              f"{sum(r['third_pa'] > 0 for r in rs)} ({sum(r['third_pa'] > 0 for r in rs) / n:.1%}) | faced 3+ "
              f"{sum(r['third_pa'] >= 3 for r in rs)} ({sum(r['third_pa'] >= 3 for r in rs) / n:.1%}) | all 9: "
              f"{sum(r['third_pa'] >= 9 for r in rs)}")
        print(f"   times 1-2: wOBA {sum(r['early_woba_sum'] for r in rs) / epa:.3f}, "
              f"xwOBA {sum(r['early_xwoba_sum'] for r in rs) / epa:.3f}   ->   time 3: wOBA "
              f"{sum(r['third_woba_sum'] for r in rs) / tpa:.3f}, xwOBA {sum(r['third_xwoba_sum'] for r in rs) / tpa:.3f}")

    # 3 -----------------------------------------------------------------------------------------
    section("3. Uncertainty of the starter's third-time number (95% ranges)")
    tpa = sum(r["third_pa"] for r in rows)
    w = sum(r["third_woba_sum"] for r in rows) / tpa
    sd = math.sqrt(sum(r["third_woba_sq"] for r in rows) / tpa - w * w)
    print(f"wOBA {w:.3f}: plate appearances treated as independent (old site) ± {1.96 * sd / math.sqrt(tpa):.3f}")
    rng = random.Random(1)
    used = [r for r in rows if r["third_pa"] > 0]
    by_pitcher = {}
    for r in used:
        by_pitcher.setdefault(r["pitcher"], []).append(r)
    ids = list(by_pitcher)
    for label, draw in (("bootstrap by start", lambda: [rng.choice(used) for _ in used]),
                        ("bootstrap by pitcher", lambda: [r for p in (rng.choice(ids) for _ in ids) for r in by_pitcher[p]])):
        ests = sorted(sum(r["third_woba_sum"] for r in s) / sum(r["third_pa"] for r in s)
                      for s in (draw() for _ in range(3000)))
        print(f"wOBA {w:.3f}: {label:<22} {ests[75]:.3f} to {ests[2924]:.3f} (± {(ests[2924] - ests[75]) / 2:.3f})")
    s = m.comparable_starts(con, *FILTERS, AS_OF)
    print(f"xwOBA {s['xwoba']:.3f}: clustered by pitcher (site v2) ± {1.96 * s['se']:.3f}")

    # 4 -----------------------------------------------------------------------------------------
    section("4. How the answer moved (win-probability points for pulling Snell)")
    lg_old = con.execute("SELECT SUM(xwoba_sum) / SUM(pa) FROM league_reliever").fetchone()[0]
    base = q.pitcher_totals(con, RELIEVER, "game_type = 'R' AND season IN (2019, 2020) AND game_date < ?", (AS_OF,))
    where, params, _ = q.recent_window(con, AS_OF)
    rec = q.pitcher_totals(con, RELIEVER, where, params)

    def regress(rate, n, prior, k=300):
        return (rate * n + prior * k) / (n + k)

    full = regress(base["xwoba"], base["pa"], lg_old)
    form = regress(rec["xwoba"], rec["pa"], full)
    worst = rec["xwoba"]
    print(f"{'version':<58} {'scenarios':>24}  {'break-even / result':>22}")
    for label, starter in (("v1 site: starter wOBA, reliever xwOBA, one constant (300)", s["woba"]),
                           ("same, but starter in xwOBA too", s["xwoba"])):
        up, mid, dn = ((starter - x) * pts for x in (full, form, worst))
        print(f"{label:<58} {up:+5.1f} / {mid:+5.1f} / {dn:+5.1f}  {f'pull if >= {-dn / (up - dn):.0%} sure':>22}")
    rel = m.reliever(con, RELIEVER, AS_OF)
    d = m.decide(s, rel, li, N)
    # same components, without the opponent adjustment
    saved = m.opponent_factor
    m.opponent_factor = lambda *a, **k: 1.0
    rel_noadj = m.reliever(con, RELIEVER, AS_OF)
    d_noadj = m.decide(s, rel_noadj, li, N)
    m.opponent_factor = saved
    print(f"{'components, each with its own constant: unchanged / changed':<58} {d_noadj['stable']:+5.1f} / "
          f"{d_noadj['changed']:+5.1f}{'':>8}")
    note = f"P(changed) {d_noadj['p_changed']:.0%}, expected {d_noadj['expected']:+.1f}"
    print(f"{'+ change test on strikeouts (no opponent adjustment)':<58} {'':>24}  {note}")
    print(f"{'+ adjusted for the postseason hitters he faced (site v2)':<58} {d['stable']:+5.1f} / {d['changed']:+5.1f}"
          f"{'':>8}  P(changed) {d['p_changed']:.0%}, expected {d['expected']:+.1f}")
    print(f"\nSite v2 detail: Anderson strikeouts {rel['recent_k']} vs {rel['expected_k']:.1f} expected "
          f"(z {rel['z']:+.1f}); hitters faced: strikeout odds x{rel['opp']:.2f} vs average; Bayes factor {rel['bf']:.0f}")
    print(f"   unchanged xwOBA {rel['stable']:.3f} ± {1.96 * rel['stable_sd']:.3f}, changed {rel['changed']:.3f} ± "
          f"{1.96 * rel['changed_sd']:.3f}; break-even P(changed) {d['break_even']:.0%}; "
          f"chance pulling is better {d['p_pull_better']:.0%}")

    # 5 -----------------------------------------------------------------------------------------
    section("5. Sensitivity of v2")
    results = []
    k_saved, prior_saved = dict(m.K_REG), m.PRIOR_CHANGE
    for (k_min, woba_max, pitch_max), prior, k_con in product(
            product((0.30, 0.35, 0.40), (0.150, 0.200, 0.250), (80, 85, 95)), (0.02, 0.05, 0.10, 0.20), (300, 600, 1200)):
        m.K_REG["con"], m.PRIOR_CHANGE = k_con, prior
        st_ = m.comparable_starts(con, k_min, woba_max, pitch_max, AS_OF)
        rl = m.reliever(con, RELIEVER, AS_OF)
        dd = m.decide(st_, rl, li, N)
        results.append((prior, dd["expected"], dd["p_pull_better"]))
    m.K_REG.update(k_saved)
    m.PRIOR_CHANGE = prior_saved
    print(f"27 starter filters x 4 priors x 3 contact constants = {len(results)} settings")
    for prior in (0.02, 0.05, 0.10, 0.20):
        sub = [r for r in results if r[0] == prior]
        ev = [r[1] for r in sub]
        print(f"  prior {prior:>4.0%}: expected value {min(ev):+.1f} to {max(ev):+.1f} pts; pulling better in "
              f"{sum(e > 0 for e in ev)}/{len(ev)}; chance pulling is better {min(r[2] for r in sub):.0%}-{max(r[2] for r in sub):.0%}")
    ev = [r[1] for r in results]
    print(f"  all: median {st.median(ev):+.1f} pts, pulling better in {sum(e > 0 for e in ev)}/{len(ev)} settings")

    # 6 -----------------------------------------------------------------------------------------
    section("6. One inning vs. the estimate")
    print(f"Spread of one plate appearance's wOBA (these starts, third time): {sd:.3f}; of a 3-batter average: "
          f"{sd / math.sqrt(3):.3f}  vs. the starter estimate's ± {1.96 * s['se']:.3f}")


if __name__ == "__main__":
    main()
