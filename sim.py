"""
Game-state simulation of the rest of a half inning, for comparing pitching decisions.

    outcome probabilities   each pitcher vs. each hitter, for K, BB (incl. HBP), 1B, 2B, 3B, HR and OUT
                            (every other ball in play), combined with the odds-ratio method:
                                p(outcome) ∝ hitter's rate vs. this pitcher hand x pitcher's rate / league rate
                            The hitter's rate vs. a hand is his overall rate times the league's platoon effect.
    base-out transitions    what each outcome does to outs, runners and runs, from 2018-2020 regular-season
                            plate appearances (pa_transitions), including double plays and errors
    end of the half inning  the team's win probability at the start of the next half inning, by score
                            margin (win_expectancy, Statcast's model averaged over 2018-2020)

The half inning is solved exactly (every path, weighted by its probability), not sampled.

Rates are shrunk toward the league the same way as model.py: strikeouts by 60 plate appearances,
walks by 130, and the mix of results on the other plate appearances by 600.
"""
import math
import random
from functools import lru_cache

import model

CLASSES = ("K", "BB", "1B", "2B", "3B", "HR", "OUT")
BIP = ("1B", "2B", "3B", "HR", "OUT")          # results of the plate appearances that aren't K or BB
K_REG = {"K": 60, "BB": 130, "BIP": 600}
MAX_RUNS = 9


# ---------------------------------------------------------------- outcome probabilities

def counts(pa, k, bb, hbp, n1b, n2b, n3b, hr):
    c = {"K": k or 0, "BB": (bb or 0) + (hbp or 0), "1B": n1b or 0, "2B": n2b or 0, "3B": n3b or 0, "HR": hr or 0}
    c["OUT"] = (pa or 0) - sum(c.values())
    c["PA"] = pa or 0
    return c


def as_probs(c):
    return {e: c[e] / c["PA"] for e in CLASSES}


def shrink(c, prior):
    """Strikeouts and walks per plate appearance, then the mix of the rest, each pulled toward the prior."""
    pa = c["PA"]
    pk = (c["K"] + prior["K"] * K_REG["K"]) / (pa + K_REG["K"])
    pb = (c["BB"] + prior["BB"] * K_REG["BB"]) / (pa + K_REG["BB"])
    rest_n = sum(c[e] for e in BIP)
    prior_rest = sum(prior[e] for e in BIP)
    out = {"K": pk, "BB": pb}
    for e in BIP:
        share = (c[e] + prior[e] / prior_rest * K_REG["BIP"]) / (rest_n + K_REG["BIP"])
        out[e] = (1 - pk - pb) * share
    return out


def matchup(pitcher, hitter, league):
    """Odds-ratio (generalized log5) combination of a pitcher's and a hitter's outcome rates."""
    raw = {e: pitcher[e] * hitter[e] / league[e] for e in CLASSES}
    total = sum(raw.values())
    return {e: v / total for e, v in raw.items()}


def xwoba_like(p):
    """Rough wOBA of an outcome mix (2018-2020 weights), for reading the projections."""
    w = {"K": 0, "BB": 0.69, "1B": 0.88, "2B": 1.25, "3B": 1.58, "HR": 2.03, "OUT": 0}
    return sum(p[e] * w[e] for e in CLASSES)


# ---------------------------------------------------------------- tables from the database

SUMS = "SUM(pa), SUM(k), SUM(bb), SUM(hbp), SUM(n1b), SUM(n2b), SUM(n3b), SUM(hr)"


def pitcher_counts(con, where, params):
    return counts(*con.execute(f"SELECT {SUMS} FROM pitcher_games WHERE {where}", params).fetchone())


def league_probs(con, pitcher_hand=None, batter_side=None):
    where, params = ["1 = 1"], []
    if pitcher_hand:
        where.append("pitcher_hand = ?"); params.append(pitcher_hand)
    if batter_side:
        where.append("batter_side = ?"); params.append(batter_side)
    row = con.execute(f"SELECT {SUMS} FROM league_platoon WHERE {' AND '.join(where)}", params).fetchone()
    return as_probs(counts(*row))


def hitter_probs(con, batter, bats, pitcher_hand):
    """A hitter's outcome rates against this pitcher hand: his overall rates (both hands, shrunk toward
    the league), times the league's platoon effect for his side against this hand.

    Individual platoon splits are mostly noise at these sample sizes (The Book regresses them by
    roughly 1,000-2,200 plate appearances), so only the league's platoon effect is used."""
    row = con.execute(f"SELECT {SUMS} FROM batter_splits WHERE batter = ?", (batter,)).fetchone()
    overall = league_probs(con)
    platoon = league_probs(con, pitcher_hand, bats if bats in ("L", "R") else None)
    talent = shrink(counts(*row), overall) if row and row[0] else overall
    raw = {e: talent[e] * platoon[e] / overall[e] for e in CLASSES}
    total = sum(raw.values())
    return {e: v / total for e, v in raw.items()}


def load_transitions(con):
    """(outs, bases, outcome) -> [(probability, outs after, bases after, runs)]

    Keeps only transitions that add up (runners before + batter = runners after + runs + new outs);
    build_database.py checks that 99.99% do. That also guarantees every plate appearance moves the
    inning forward, so the half inning can be solved without loops."""
    raw = {}
    for outs, bases, e, outs2, bases2, runs, n in con.execute(
            "SELECT outs, bases, event_class, outs_after, bases_after, runs, n FROM pa_transitions"):
        if outs2 < 3 and bases.count("1") + 1 != bases2.count("1") + runs + outs2 - outs:
            continue
        raw.setdefault((outs, bases, e), []).append((n, outs2, bases2, runs))
    out = {}
    for key, rows in raw.items():
        total = sum(r[0] for r in rows)
        out[key] = [(n / total, o2, b2, r) for n, o2, b2, r in rows]
    return out


def start_of_half_wp(con, inning, half):
    """Batting team's win probability at the start of a half inning, by score margin (-8..+8)."""
    return {d: wp for d, wp in con.execute(
        """SELECT diff, wp FROM win_expectancy
           WHERE inning = ? AND half = ? AND outs = 0 AND bases = '000'""", (min(inning, 9), half))}


# ---------------------------------------------------------------- solving a half inning

class HalfInning:
    """The rest of a half inning from the fielding team's side.

    lineup      list of (batter id, bats) in batting order
    probs       {pitcher key: [outcome probabilities vs. each lineup spot]}
    end_value   function(runs allowed this half inning) -> fielding team's win probability
    """

    def __init__(self, transitions, lineup, probs, end_value, max_runs=MAX_RUNS):
        self.t = transitions
        self.lineup = lineup
        self.probs = probs
        self.end_value = end_value
        self.max_runs = max_runs        # this many runs in the half inning is treated as the end of it
        self._fixed = {}

    def _step(self, pitcher, outs, bases, runs, spot, cont):
        """Expected value of one plate appearance, then cont(outs, bases, runs, next spot)."""
        total = 0.0
        for e, pe in self.probs[pitcher][spot].items():
            if pe <= 0:
                continue
            for p, o2, b2, r in self.t[(outs, bases, e)]:
                runs2 = min(runs + r, self.max_runs)
                if o2 >= 3 or runs2 >= self.max_runs:
                    total += pe * p * self.end_value(runs2)
                else:
                    total += pe * p * cont(o2, b2, runs2, (spot + 1) % len(self.lineup))
        return total

    def fixed(self, pitcher):
        """Value if this pitcher finishes the half inning (solved once per pitcher, then reused)."""
        if pitcher not in self._fixed:
            @lru_cache(maxsize=None)
            def v(outs, bases, runs, spot):
                return self._step(pitcher, outs, bases, runs, spot, v)
            self._fixed[pitcher] = v
        return self._fixed[pitcher]

    def expected_runs(self, pitcher):
        """Expected runs to the end of the half inning (for validating against real run expectancy)."""
        counter = HalfInning(self.t, self.lineup, self.probs, lambda runs: runs, max_runs=20)
        f = counter.fixed(pitcher)
        return lambda outs, bases, spot: f(outs, bases, 0, spot)


def compare(inning, starter, relievers, weights):
    """The three strategies at the current state, plus the best timing.

    starter      HalfInning key of the current pitcher
    relievers    {name: HalfInning} for each version of the reliever (same lineup and starter)
    weights      {name: probability} for the versions (e.g. unchanged / changed)

    Strategies (values are the fielding team's win probability):
      pull_now      the reliever finishes the half inning (he must face three or finish it anyway)
      one_more      the starter faces one more hitter, then the staff picks the better option
      stay          the starter finishes the half inning
      best_timing   the starter may be pulled before any hitter (optimal stopping)
    """
    def mix(f):
        return sum(w * f(name) for name, w in weights.items())

    def state_values(outs, bases, runs, spot):
        pull = mix(lambda n: relievers[n].fixed("reliever")(outs, bases, runs, spot))
        stay = mix(lambda n: relievers[n].fixed(starter)(outs, bases, runs, spot))
        return pull, stay

    any_inning = next(iter(relievers.values()))

    def one_more(outs, bases, runs, spot):
        return any_inning._step(starter, outs, bases, runs, spot, lambda o, b, r, s: max(state_values(o, b, r, s)))

    @lru_cache(maxsize=None)
    def best(outs, bases, runs, spot):
        pull, _ = state_values(outs, bases, runs, spot)
        keep = any_inning._step(starter, outs, bases, runs, spot, best)
        return max(pull, keep)

    o, b, r, s = inning
    pull, stay = state_values(o, b, r, s)
    return {"pull_now": pull, "one_more": one_more(o, b, r, s), "stay": stay, "best_timing": best(o, b, r, s)}


# ---------------------------------------------------------------- uncertainty draws

def dirichlet(rng, alphas):
    g = [rng.gammavariate(max(a, 1e-6), 1.0) for a in alphas]
    total = sum(g)
    return [x / total for x in g]


def draw_rates(rng, c, prior):
    """One draw of true outcome rates with the same shrinkage as shrink(): strikeout rate and walk rate
    from Beta posteriors, then the mix of the other results from a Dirichlet. Draws average to shrink()."""
    pa = c["PA"]
    pk = rng.betavariate(c["K"] + prior["K"] * K_REG["K"], pa - c["K"] + (1 - prior["K"]) * K_REG["K"])
    pb = rng.betavariate(c["BB"] + prior["BB"] * K_REG["BB"], pa - c["BB"] + (1 - prior["BB"]) * K_REG["BB"])
    rest = max(1 - pk - pb, 0.05)
    prior_rest = sum(prior[e] for e in BIP)
    mix = dirichlet(rng, [c[e] + prior[e] / prior_rest * K_REG["BIP"] for e in BIP])
    out = {"K": pk, "BB": pb}
    out.update({e: rest * x for e, x in zip(BIP, mix)})
    return out


# ---------------------------------------------------------------- one decision, end to end

_STATIC = {}


def static(con):
    """Tables that never change while the site runs: transitions, league rates, win expectancy."""
    if not _STATIC:
        _STATIC["t"] = load_transitions(con)
        _STATIC["league"] = league_probs(con)
        _STATIC["platoon"] = {(h, b): league_probs(con, h, b) for h in "LR" for b in "LR"}
        _STATIC["we"] = {(i, h): start_of_half_wp(con, i, h) for i in range(1, 10) for h in ("Top", "Bot")}
    return _STATIC


def end_value_fn(we, inning, half, lead):
    """Fielding team's win probability once the half inning ends, given the runs it allowed.
    lead = fielding team's lead when the simulation starts."""
    def value(runs):
        margin = lead - runs
        if half == "Bot" and inning >= 9:                  # visitors pitching in the 9th or later
            return 1.0 if margin > 0 else (0.0 if margin < 0 else we[(9, "Top")][0])
        if half == "Top" and inning >= 9 and margin > 0:   # home team still ahead after the top half
            return 1.0
        nxt_inning, nxt_half = (inning + 1, "Top") if half == "Bot" else (inning, "Bot")
        return we[(min(nxt_inning, 9), nxt_half)][max(-8, min(8, margin))]
    return value


def opponent_lineup(con, team, day):
    """The opponent's batting order if this is a postseason game in the data (lineups are posted before
    the game), else None. Returns [(batter id, bats, name)]."""
    g = con.execute("""SELECT game_pk, home_team, away_team FROM postseason_pa
                       WHERE game_date = ? AND (home_team = ? OR away_team = ?) LIMIT 1""", (day, team, team)).fetchone()
    if not g:
        return None
    half = "Bot" if g["away_team"] == team else "Top"
    order = []
    for r in con.execute("""SELECT p.batter, p.stand, pl.name FROM postseason_pa p
                            LEFT JOIN players pl ON pl.player_id = p.batter
                            WHERE p.game_pk = ? AND p.inning_topbot = ? ORDER BY p.at_bat_number""", (g["game_pk"], half)):
        if r["batter"] not in [b for b, _, _ in order]:
            order.append((r["batter"], r["stand"], r["name"] or f"ID {r['batter']}"))
        if len(order) == 9:
            break
    return order if len(order) == 9 else None


def neutral(c, opp):
    """Recent strikeouts translated to what they would have been against average hitters (as model.py)."""
    if opp == 1.0 or not (0 < c["K"] < c["PA"]):
        return c
    k2 = c["PA"] * model.prob(model.odds(c["K"] / c["PA"]) / opp)
    scale = (c["PA"] - k2) / (c["PA"] - c["K"])
    out = {e: c[e] * scale for e in CLASSES if e != "K"}
    out.update({"K": k2, "PA": c["PA"]})
    return out


def _vs(talent, platoon, league):
    raw = {e: talent[e] * platoon[e] / league[e] for e in CLASSES}
    total = sum(raw.values())
    return {e: v / total for e, v in raw.items()}


def evaluate(con, rel, as_of, team, inning, half, outs, bases, lead, filters, due=None, n_draws=200, seed=5):
    """Pull now / one more hitter / let him finish, for the rest of the half inning.

    rel       model.reliever(...) for the reliever (P(changed), opponent factor, hand)
    filters   (k_min, woba_max, pitch_max) for the comparable starts
    due       batter id due up, if the opponent's lineup is known (postseason games)
    """
    S = static(con)
    league, t = S["league"], S["t"]
    k_min, woba_max, pitch_max = filters

    starts = con.execute("""
        SELECT pitcher, third_pa, third_k, third_bb, third_hbp, third_1b, third_2b, third_3b, third_hr
        FROM starter_games
        WHERE game_type = 'R' AND early_pa >= 18 AND game_date < ?
          AND 1.0 * early_k / early_pa >= ? AND early_woba_sum / early_pa <= ? AND early_pitches <= ?""",
                         (as_of, k_min, woba_max, pitch_max)).fetchall()
    if not starts or not sum(r["third_pa"] for r in starts):
        return None
    starter = as_probs(counts(*[sum(r[i] for r in starts) for i in range(1, 9)]))
    st = con.execute("""SELECT pitcher_name, throws FROM pitcher_games
                        WHERE team = ? AND game_date = ? AND is_starter = 1""", (team, as_of)).fetchone()
    s_hand = st["throws"] if st else None

    order = opponent_lineup(con, team, as_of)
    if order:
        lineup = [(b, bats) for b, bats, _ in order]
        ids = [b for b, _, _ in order]
        spot = ids.index(due) if due in ids else 0
        talent_counts = []
        for b, _ in lineup:
            row = con.execute(f"SELECT {SUMS} FROM batter_splits WHERE batter = ?", (b,)).fetchone()
            talent_counts.append(counts(*row) if row and row[0] else None)
    else:
        lineup, spot, talent_counts = [(None, None)], 0, [None]

    def hitter_rates(talents, hand):
        out = []
        for (b, bats), talent in zip(lineup, talents):
            if talent is None or hand not in ("L", "R") or bats not in ("L", "R"):
                out.append(talent or league)
            else:
                out.append(_vs(talent, S["platoon"][(hand, bats)], league))
        return out

    talents = [shrink(c, league) if c else None for c in talent_counts]

    (rw, rp), (hw, hp), _, _ = model.windows(con, as_of)
    pid = rel["pitcher"]
    recent = neutral(pitcher_counts(con, f"pitcher = ? AND {rw}", (pid, *rp)), rel["opp"])
    history = pitcher_counts(con, f"pitcher = ? AND {hw}", (pid, *hp))
    both = {e: history[e] + recent[e] for e in list(CLASSES) + ["PA"]}
    season = int(as_of[:4])
    rel_prior = as_probs(pitcher_counts(con, "game_type = 'R' AND is_starter = 0 AND season IN (?, ?) AND game_date < ?",
                                        (season - 1, season, as_of)))
    versions = {"unchanged": shrink(both, rel_prior), "changed": shrink(recent, rel_prior) if recent["PA"] else rel_prior}

    end_value = end_value_fn(S["we"], inning, half, lead)
    s_vs, r_vs = hitter_rates(talents, s_hand), hitter_rates(talents, rel["throws"])
    starter_probs = [matchup(starter, h, league) for h in s_vs]
    innings = {v: HalfInning(t, lineup, {"starter": starter_probs, "reliever": [matchup(p, h, league) for h in r_vs]},
                             end_value) for v, p in versions.items()}
    state = (outs, bases, 0, spot)
    P = rel["p_changed"]
    res = {name: compare(state, "starter", innings, w) for name, w in
           (("unchanged", {"unchanged": 1.0}), ("changed", {"changed": 1.0}),
            ("expected", {"unchanged": 1 - P, "changed": P}))}
    du = res["unchanged"]["pull_now"] - res["unchanged"]["stay"]
    dc = res["changed"]["pull_now"] - res["changed"]["stay"]
    if (du > 0) != (dc > 0):
        break_even, pull_if = du / (du - dc), ("below" if du > 0 else "above")
    else:
        break_even, pull_if = None, ("always" if du > 0 else "never")

    # uncertainty: comparable starts resampled by pitcher, rates drawn from their posteriors,
    # and the reliever's version drawn with P(changed)
    rng = random.Random(seed)
    by_pitcher = {}
    for r in starts:
        by_pitcher.setdefault(r["pitcher"], []).append(r)
    pids = list(by_pitcher)
    draws = []
    for _ in range(n_draws):
        sample = [r for p in (rng.choice(pids) for _ in pids) for r in by_pitcher[p]]
        s_draw = as_probs(counts(*[sum(r[i] for r in sample) for i in range(1, 9)]))
        r_draw = draw_rates(rng, recent if (rng.random() < P and recent["PA"]) else both, rel_prior)
        t_draw = [draw_rates(rng, c, league) if c else None for c in talent_counts]
        one = HalfInning(t, lineup, {"starter": [matchup(s_draw, h, league) for h in hitter_rates(t_draw, s_hand)],
                                     "reliever": [matchup(r_draw, h, league) for h in hitter_rates(t_draw, rel["throws"])]},
                         end_value)
        draws.append(one.fixed("reliever")(*state) - one.fixed("starter")(*state))
    draws.sort()
    n = len(draws)
    e = res["expected"]
    return {
        "strategies": e, "unchanged": res["unchanged"], "changed": res["changed"],
        "points": {k: 100 * (res[k]["pull_now"] - res[k]["stay"]) for k in res},
        "wait_value": 100 * (e["one_more"] - max(e["pull_now"], e["stay"])),
        # waiting only counts as best if it beats both simple choices by more than 0.05 points
        "best": ("one_more" if e["one_more"] > max(e["pull_now"], e["stay"]) + 0.0005
                 else ("pull_now" if e["pull_now"] > e["stay"] else "stay")),
        "break_even": break_even, "pull_if": pull_if, "p_changed": P,
        "p_pull_better": sum(d > 0 for d in draws) / n,
        "range90": (100 * draws[int(0.05 * n)], 100 * draws[int(0.95 * n)]),
        "lineup": order, "due_spot": spot, "starter_name": model.flip_name(st["pitcher_name"]) if st else None,
        "starter_hand": s_hand, "starts": len(starts), "starter_rates": starter,
        "reliever_rates": versions, "n_draws": n,
        "woba": {"starter": xwoba_like(starter), "unchanged": xwoba_like(versions["unchanged"]),
                 "changed": xwoba_like(versions["changed"])},
    }
