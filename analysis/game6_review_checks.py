"""
Checks added after an editorial review of the Game 6 article (November 2026).

1. Snell's own record: his 2018-2020 third-time-through numbers instead of the comparable starts.
2. Other relievers: the same simulation for every Rays reliever who pitched in the 2020 World Series
   before Game 6, against leaving Snell in.
3. Uncertainty: is "the chance pulling was better" stable? 200, 2,000 and 10,000 draws, several seeds.
   Writes the 10,000-draw result to site_data/game6_uncertainty.json, which the site shows for Game 6.

Same inputs as analysis/game6_simulation.py and the site (sim.py, model.py).
Run:  python3 analysis/game6_review_checks.py      (about two minutes)
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "analysis"))
import game6_simulation as g6   # noqa: E402  (Game 6 constants, lineup, comparable starts)
import model as m               # noqa: E402
import queries as q             # noqa: E402
import sim                      # noqa: E402

OUT = ROOT / "site_data" / "game6_uncertainty.json"
WS_START = "2020-10-20"          # Game 1 of the 2020 World Series


def section(title):
    print(f"\n{'=' * 4} {title} {'=' * (90 - len(title))}")


def run(con, rel, due, n_draws, seed=5):
    """sim.evaluate for the Game 6 situation: bottom 6th, one out, runner on first, Rays up 1-0."""
    outs, bases, _ = g6.STATE
    return sim.evaluate(con, rel, g6.AS_OF, g6.TEAM, 6, "Bot", outs, bases, g6.LEAD, g6.FILTERS,
                        due=due, n_draws=n_draws, seed=seed)


def snell_own_record(con, rel):
    section("1. Snell's own third-time record instead of the comparable starts")
    row = con.execute("""
        SELECT COUNT(*), SUM(third_pa), SUM(third_k), SUM(third_bb), SUM(third_hbp), SUM(third_1b), SUM(third_2b),
               SUM(third_3b), SUM(third_hr), SUM(third_xwoba_sum)
        FROM starter_games WHERE pitcher = ? AND game_type = 'R' AND game_date < ?""",
                      (g6.SNELL, g6.AS_OF)).fetchone()
    starts, pa = row[0], row[1]
    print(f"   {starts} regular-season starts, {pa} hitters faced a third time: strikeouts {row[2] / pa:.1%}, "
          f"home runs {row[8] / pa:.1%}, xwOBA {row[9] / pa:.3f}")

    # the same inputs as game6_simulation.py
    t = sim.load_transitions(con)
    lineup, start = g6.lineup_from(con, g6.PULL_AB)
    wp_next = sim.start_of_half_wp(con, 7, "Top")

    def end_value(runs):
        return wp_next[max(-8, min(8, g6.LEAD - runs))]

    league = sim.league_probs(con)
    hitters_vs = {hand: [sim.hitter_probs(con, b, bats, hand) for b, bats in lineup] for hand in ("L", "R")}
    (rw, rp), (hw, hp), _, _ = m.windows(con, g6.AS_OF)
    recent = g6.neutral(sim.pitcher_counts(con, f"pitcher = ? AND {rw}", (g6.ANDERSON, *rp)), rel["opp"])
    history = sim.pitcher_counts(con, f"pitcher = ? AND {hw}", (g6.ANDERSON, *hp))
    both = {e: history[e] + recent[e] for e in list(sim.CLASSES) + ["PA"]}
    season = int(g6.AS_OF[:4])
    rel_prior = sim.as_probs(sim.pitcher_counts(
        con, "game_type = 'R' AND is_starter = 0 AND season IN (?, ?) AND game_date < ?",
        (season - 1, season, g6.AS_OF)))
    anderson = {"unchanged": sim.shrink(both, rel_prior), "changed": sim.shrink(recent, rel_prior)}

    def probs_for(rates, hand):
        return [sim.matchup(rates, h, league) for h in hitters_vs[hand]]

    own = sim.counts(*row[1:9])
    comparable = g6.snell_third_time(con)
    versions = (("comparable starts (article)", comparable),
                ("own record, pulled toward comparable starts", sim.shrink(own, comparable)),
                ("own record, raw", sim.as_probs(own)))
    P = rel["p_changed"]
    print(f"   {'Snell estimate':<45} {'wOBA-like':>9}  {'finishes':>8}  pulling: unchanged / changed / expected"
          f"  break-even")
    for name, rates in versions:
        innings = {k: sim.HalfInning(t, lineup, {"snell": probs_for(rates, "L"), "reliever": probs_for(p, "R")},
                                     end_value)
                   for k, p in anderson.items()}
        res = {label: sim.compare(g6.STATE + (start,), "snell", innings, w)
               for label, w in (("u", {"unchanged": 1.0}), ("c", {"changed": 1.0}),
                                ("e", {"unchanged": 1 - P, "changed": P}))}
        du = res["u"]["pull_now"] - res["u"]["stay"]
        dc = res["c"]["pull_now"] - res["c"]["stay"]
        de = res["e"]["pull_now"] - res["e"]["stay"]
        be = f"{du / (du - dc):.0%}" if (du > 0) != (dc > 0) else "none"
        print(f"   {name:<45} {sim.xwoba_like(rates):>9.3f}  {res['e']['stay']:>8.1%}  "
              f"{100 * du:+.1f} / {100 * dc:+.1f} / {100 * de:+.1f} points{'':>10}{be}")


def other_relievers(con, due):
    section("2. Every Rays reliever who pitched in the World Series before Game 6, against leaving Snell in")
    ids = [r[0] for r in con.execute("""
        SELECT DISTINCT pitcher FROM postseason_pa
        WHERE game_date >= ? AND game_date < ? AND is_starter = 0
          AND ((inning_topbot = 'Top' AND home_team = ?) OR (inning_topbot = 'Bot' AND away_team = ?))""",
                                     (WS_START, g6.AS_OF, g6.TEAM, g6.TEAM))]
    rows = []
    for pid in ids:
        rel = m.reliever(con, pid, g6.AS_OF)
        res = run(con, rel, due, 200) if rel else None      # the points below don't depend on the draws
        if res:
            rows.append((res["points"]["expected"], rel, res))
    print(f"   {'reliever':<18} throws  postseason K: actual / expected  weight on a change  "
          f"pulling Snell: if unchanged / expected (points)")
    for exp, rel, res in sorted(rows, key=lambda x: -x[0]):
        print(f"   {rel['name']:<18} {rel['throws']:^6}  {rel['recent_k']:>16} / {rel['expected_k']:<13.1f}"
              f"  {rel['p_changed']:>13.0%}        {res['points']['unchanged']:+.1f} / {exp:+.1f}")
    print("   Negative = leaving Snell in is better. Rest and later innings are not counted.")


def uncertainty(con, rel, due):
    section("3. Uncertainty: the chance pulling was better, by number of draws")
    print(f"   {'draws':>6} {'runs':>5}   chance pulling was better        90% range, first run   seconds")
    final = None
    for n, seeds in ((200, range(1, 11)), (2000, (5, 6, 7)), (10000, (5, 11))):
        t0 = time.time()
        res = [run(con, rel, due, n, s) for s in seeds]
        ps = [r["p_pull_better"] for r in res]
        lo, hi = res[0]["range90"]
        print(f"   {n:>6,} {len(ps):>5}   {min(ps):.1%} to {max(ps):.1%} (mean {sum(ps) / len(ps):.1%})"
              f"   {lo:+.1f} to {hi:+.1f} points   {time.time() - t0:>7.0f}")
        final = res[0]
    print("   One run of 200 draws has a sampling error of about 3 points on its own, so it can land anywhere"
          " from about 20% to 31%.")
    keep = {"n_draws": final["n_draws"], "p_pull_better": round(final["p_pull_better"], 4),
            "range90": [round(final["range90"][0], 2), round(final["range90"][1], 2)],
            "made_by": "analysis/game6_review_checks.py (seed 5)"}
    OUT.write_text(json.dumps(keep, indent=2) + "\n")
    print(f"   wrote {OUT.relative_to(ROOT)}: chance pulling was better {keep['p_pull_better']:.1%}, "
          f"90% range {keep['range90'][0]:+.1f} to {keep['range90'][1]:+.1f}")


def main():
    con = q.connect()
    lineup, start = g6.lineup_from(con, g6.PULL_AB)
    due = lineup[start][0]                   # Mookie Betts
    rel = m.reliever(con, g6.ANDERSON, g6.AS_OF)
    snell_own_record(con, rel)
    other_relievers(con, due)
    uncertainty(con, rel, due)               # slowest, so last


if __name__ == "__main__":
    main()
