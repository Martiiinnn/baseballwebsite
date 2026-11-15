"""
Game 6, bottom of the 6th: pull Snell now, let him face Betts and decide again, or let him finish?

Uses sim.py (outcome probabilities -> base-out transitions -> win probability at the end of the half
inning) with the same inputs as the site (model.py and sim.evaluate):
  Snell      outcome mix the third time through in the 476 comparable starts (before the date)
  Anderson   unchanged (all his games) and changed (postseason only), with P(changed) from model.py
  Hitters    the Dodgers' lineup in order from Betts, each hitter's rates vs. LHP or RHP

Validation first:
  1. League-average pitcher vs. league-average hitters: does the model reproduce real run expectancy?
  2. League-average pitcher vs. this lineup at this moment: does it land near Statcast's win probability?

Run:  python3 analysis/game6_simulation.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import model as m          # noqa: E402
import queries as q        # noqa: E402
import sim                 # noqa: E402

AS_OF, GAME, TEAM = "2020-10-27", 635886, "TB"
SNELL, ANDERSON, PULL_AB = 605483, 623433, 43
FILTERS = (0.35, 0.200, 85)
STATE = (1, "100", 0)                      # one out, runner on first, no runs yet this half inning
LEAD = 1                                   # Rays up 1-0


def lineup_from(con, pull_ab):
    rows = con.execute("""SELECT at_bat_number, batter, stand FROM postseason_pa
                          WHERE game_pk = ? AND inning_topbot = 'Bot' ORDER BY at_bat_number""", (GAME,)).fetchall()
    order = []
    for r in rows:
        if r["batter"] not in [b for b, _ in order]:
            order.append((r["batter"], r["stand"]))
        if len(order) == 9:
            break
    due = next(r["batter"] for r in rows if r["at_bat_number"] == pull_ab)
    start = [b for b, _ in order].index(due)
    return order, start


def snell_third_time(con):
    row = con.execute("""
        SELECT SUM(third_pa), SUM(third_k), SUM(third_bb), SUM(third_hbp), SUM(third_1b), SUM(third_2b),
               SUM(third_3b), SUM(third_hr)
        FROM starter_games
        WHERE game_type = 'R' AND early_pa >= 18 AND game_date < ?
          AND 1.0 * early_k / early_pa >= ? AND early_woba_sum / early_pa <= ? AND early_pitches <= ?""",
                      (AS_OF, *FILTERS)).fetchone()
    return sim.as_probs(sim.counts(*row))


def neutral(c, opp):
    """Translate strikeouts to what they would have been against average hitters (as model.py does)."""
    if opp == 1.0 or not (0 < c["K"] < c["PA"]):
        return c
    k2 = c["PA"] * m.prob(m.odds(c["K"] / c["PA"]) / opp)
    scale = (c["PA"] - k2) / (c["PA"] - c["K"])
    out = {e: c[e] * scale for e in sim.CLASSES if e != "K"}
    out.update({"K": k2, "PA": c["PA"]})
    return out


def main():
    con = q.connect()
    t = sim.load_transitions(con)
    missing = [(o, b, e) for o in range(3) for b in ("000", "100", "010", "001", "110", "101", "011", "111")
               for e in sim.CLASSES if (o, b, e) not in t]
    print(f"Transition table: {len(t)} of 168 (outs, runners, result) combinations"
          + (f"; missing {missing}" if missing else ""))
    league = sim.league_probs(con)

    # ---------------------------------------------------------------- validation 1: run expectancy
    avg = sim.HalfInning(t, [(0, "R")], {"avg": [league]}, lambda r: 0)
    er = avg.expected_runs("avg")
    print("\n1. Run expectancy, league-average pitcher vs. league-average hitters (model vs. 2018-2020 innings 1-8)")
    errs = []
    rows = con.execute("SELECT outs, bases, re FROM run_expectancy ORDER BY outs, bases").fetchall()
    for outs in range(3):
        line = []
        for r in [x for x in rows if x["outs"] == outs]:
            model_re = er(outs, r["bases"], 0)
            errs.append(model_re - r["re"])
            line.append(f"{r['bases']}: {model_re:.2f}/{r['re']:.2f}")
        print(f"   {outs} out  " + "  ".join(line))
    print(f"   mean error {sum(errs) / len(errs):+.3f} runs, largest {max(errs, key=abs):+.3f}")

    # ---------------------------------------------------------------- inputs for Game 6
    lineup, start = lineup_from(con, PULL_AB)
    names = {b: q.player_name(con, b) for b, _ in lineup}
    wp_next = sim.start_of_half_wp(con, 7, "Top")

    def end_value(runs):                       # Rays win probability at the start of the top of the 7th
        return wp_next[max(-8, min(8, LEAD - runs))]

    hitters_vs = {hand: [sim.hitter_probs(con, b, bats, hand) for b, bats in lineup] for hand in ("L", "R")}
    snell = snell_third_time(con)
    rel = m.reliever(con, ANDERSON, AS_OF)
    (rw, rp), (hw, hp), _, _ = m.windows(con, AS_OF)
    recent = neutral(sim.pitcher_counts(con, f"pitcher = ? AND {rw}", (ANDERSON, *rp)), rel["opp"])
    history = sim.pitcher_counts(con, f"pitcher = ? AND {hw}", (ANDERSON, *hp))
    both = {e: history[e] + recent[e] for e in list(sim.CLASSES) + ["PA"]}
    season = int(AS_OF[:4])
    rel_prior = sim.as_probs(sim.pitcher_counts(
        con, "game_type = 'R' AND is_starter = 0 AND season IN (?, ?) AND game_date < ?", (season - 1, season, AS_OF)))
    versions = {"unchanged": sim.shrink(both, rel_prior), "changed": sim.shrink(recent, rel_prior)}

    print("\nInputs (rates per plate appearance vs. an average lineup):")
    print(f"   {'':<22}" + "".join(f"{e:>7}" for e in sim.CLASSES) + "   wOBA-like")
    for name, p in (("league", league), ("Snell, third time", snell),
                    ("Anderson unchanged", versions["unchanged"]), ("Anderson changed", versions["changed"])):
        print(f"   {name:<22}" + "".join(f"{p[e]:>7.3f}" for e in sim.CLASSES) + f"   {sim.xwoba_like(p):.3f}")
    print(f"   P(Anderson changed) = {rel['p_changed']:.0%} (model.py)")
    print("   Lineup from the pull: " + ", ".join(f"{names[b]} ({s})" for b, s in lineup[start:] + lineup[:start]))

    def probs_for(pitcher_rates, hand):
        return [sim.matchup(pitcher_rates, h, league) for h in hitters_vs[hand]]

    # ---------------------------------------------------------------- validation 2: win probability now
    avg_game = sim.HalfInning(t, lineup, {"avg": probs_for(league, "L")}, end_value)
    wp_avg = avg_game.fixed("avg")(*STATE, start)
    statcast = con.execute("SELECT home_win_exp FROM postseason_pa WHERE game_pk = ? AND at_bat_number = ?",
                           (GAME, PULL_AB)).fetchone()[0]
    flat = sim.HalfInning(t, lineup, {"avg": [league] * len(lineup)}, end_value)
    wp_flat = flat.fixed("avg")(*STATE, start)
    print(f"\n2. Rays win probability at the pull (Statcast's model, which assumes average teams: {1 - statcast:.1%})")
    print(f"   league-average pitcher vs. league-average hitters: {wp_flat:.1%}   <- checks the machinery")
    print(f"   league-average lefty vs. this Dodgers lineup:      {wp_avg:.1%}   <- the lineup is better than average")

    # ---------------------------------------------------------------- the three strategies
    snell_probs = probs_for(snell, "L")
    innings = {name: sim.HalfInning(t, lineup, {"snell": snell_probs, "reliever": probs_for(p, "R")}, end_value)
               for name, p in versions.items()}
    print("\n3. Rays win probability by strategy (bottom 6th, 1 out, runner on first, up 1-0)")
    results = {}
    for label, weights in (("if Anderson is unchanged", {"unchanged": 1.0}),
                           ("if Anderson has changed", {"changed": 1.0}),
                           (f"expected ({rel['p_changed']:.0%} changed)",
                            {"unchanged": 1 - rel["p_changed"], "changed": rel["p_changed"]})):
        v = sim.compare(STATE + (start,), "snell", innings, weights)
        results[label] = v
        print(f"   {label:<26} pull now {v['pull_now']:.1%} | one more hitter, then decide {v['one_more']:.1%} | "
              f"Snell finishes {v['stay']:.1%} | best timing {v['best_timing']:.1%}")
    print("\n   In win-probability points:")
    for label, v in results.items():
        print(f"   {label:<26} pulling now vs. staying {100 * (v['pull_now'] - v['stay']):+.1f} | "
              f"value of waiting one hitter {100 * (v['one_more'] - max(v['pull_now'], v['stay'])):+.2f} | "
              f"best timing vs. pulling now {100 * (v['best_timing'] - v['pull_now']):+.1f}")
    u, c = results["if Anderson is unchanged"], results["if Anderson has changed"]
    du, dc = u["pull_now"] - u["stay"], c["pull_now"] - c["stay"]
    if (du > 0) != (dc > 0):
        print(f"\n   Pulling now beats letting Snell finish only if P(changed) is below {du / (du - dc):.0%}")

    print("\n   Across beliefs about Anderson (README table):")
    print("   P(changed) | pull now | face Betts, then decide | Snell finishes | best timing | facing Betts first vs. the better of the other two")
    for P in (0.0, 0.2, 0.4, 0.5, 0.6, rel["p_changed"], 1.0):
        v = sim.compare(STATE + (start,), "snell", innings, {"unchanged": 1 - P, "changed": P})
        print(f"   {P:>9.0%} |  {v['pull_now']:.1%}  |         {v['one_more']:.1%}          |     {v['stay']:.1%}     |"
              f"    {v['best_timing']:.1%}    | {100 * (v['one_more'] - max(v['pull_now'], v['stay'])):+.2f} points")

    # ---------------------------------------------------------------- uncertainty (the site's own function)
    card = q.starter_card(con, TEAM, AS_OF)
    lk = card["manager_link"]
    due = lineup[start][0]
    r = sim.evaluate(con, rel, AS_OF, TEAM, 6, "Bot", STATE[0], STATE[1], LEAD,
                     (lk["k_min"], lk["woba_max"], lk["pitch_max"]), due=due)
    print(f"\n4. Uncertainty ({r['n_draws']} draws, as on the site: comparable starts resampled by pitcher, Anderson's and the")
    print("   hitters' rates drawn from their posteriors, and Anderson's version drawn with P(changed))")
    print(f"   pulling now vs. Snell finishing: expected {r['points']['expected']:+.1f} points, 90% range "
          f"{r['range90'][0]:+.1f} to {r['range90'][1]:+.1f}; chance pulling was better {r['p_pull_better']:.0%}")
    print("   200 draws leave about 3 points of sampling error on that chance; analysis/game6_review_checks.py repeats it")
    print("   with 10,000 draws (26%), which is the number the site shows for Game 6.")


if __name__ == "__main__":
    main()
