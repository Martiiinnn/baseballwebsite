"""
The pull decision, version 2 (after the November 2026 review).

Both pitchers are measured the same way, in expected wOBA (xwOBA), using only games before the date.

Starter, third time through
    Regular-season starts at least roughly as dominant through two times through the order,
    and the xwOBA hitters posted the third time through. Uncertainty: standard error clustered
    by pitcher (the same pitchers appear in many starts).

Reliever
    Each component is shrunk toward the league separately, because they become reliable at very
    different sample sizes (split-half reliability, see analysis/estimate_regression_constants.py):
        strikeout rate  60 PA    walk rate  130 PA    xwOBA on other plate appearances  600
    xwOBA = walk rate x 0.69 + (1 - strikeout rate - walk rate) x xwOBA on other plate appearances

    Has he changed? Compare his recent strikeouts with what his history predicts (adjusted for the
    hitters he faced in the postseason). The Bayes factor says how much more likely the recent
    strikeouts are if his talent changed (a fresh draw from the league) than if it did not.
    P(changed) = 10% x BF / (10% x BF + 90%); the 10% prior was chosen by the backtest
    (analysis/backtest_reliever_change.py).
        unchanged   all his games, shrunk toward the league
        changed     only the recent games, shrunk toward the league
        expected    P(changed) x changed + (1 - P(changed)) x unchanged

Decision
    points = batters x (starter xwOBA - reliever xwOBA) / 1.2 x leverage / 10 x 100
    Still the rule-of-thumb conversion; a game-state simulation is the planned replacement.
"""
import math
from math import lgamma

from queries import MIN_EARLY_PA, RUNS_PER_WIN, WOBA_SCALE, day_before, flip_name

K_REG = {"k": 60, "bb": 130, "con": 600}   # regression constants (split-half reliability)
W_BB = 0.69                                # wOBA value of a walk, 2018-2020 (approx.)
SD_CON = 0.43                              # spread of xwOBA on a single non-K, non-BB plate appearance
PRIOR_CHANGE = 0.10                        # chosen by the backtest on 2019, tested on 2020
EXCLUDED = ("intent_walk", "sac_bunt", "sac_bunt_double_play", "catcher_interf", "truncated_pa")


# ---------------------------------------------------------------- small helpers

def odds(p):
    return p / (1 - p)


def prob(o):
    return o / (1 + o)


def normal_cdf(z):
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def _betaln(a, b):
    return lgamma(a) + lgamma(b) - lgamma(a + b)


def _log_ml(x, n, mean, conc):
    """Beta-binomial log marginal likelihood (the binomial coefficient cancels in the Bayes factor)."""
    a, b = mean * conc, (1 - mean) * conc
    return _betaln(x + a, n - x + b) - _betaln(a, b)


# ---------------------------------------------------------------- components

EMPTY = {"pa": 0, "k": 0.0, "bb": 0, "rest": 0, "conx": 0.0, "games": 0, "first": None, "last": None}


def components(con, where, params):
    r = con.execute(f"""SELECT SUM(pa), SUM(k), SUM(bb), SUM(xwoba_sum), COUNT(*), MIN(game_date), MAX(game_date)
                        FROM pitcher_games WHERE {where}""", params).fetchone()
    if not r[0]:
        return dict(EMPTY)
    pa, k, bb, x = r[0], r[1], r[2], r[3]
    return {"pa": pa, "k": k, "bb": bb, "rest": pa - k - bb, "conx": x - W_BB * bb,
            "games": r[4], "first": r[5], "last": r[6]}


def combine(a, b):
    out = {key: a[key] + b[key] for key in ("pa", "k", "bb", "rest", "conx", "games")}
    dates = [d for d in (a["first"], a["last"], b["first"], b["last"]) if d]
    out["first"], out["last"] = (min(dates), max(dates)) if dates else (None, None)
    return out


def shrink(s, lg):
    """Each component pulled toward the league: (events + league x K) / (chances + K)."""
    return {
        "k": (s["k"] + lg["k"] * K_REG["k"]) / (s["pa"] + K_REG["k"]),
        "bb": (s["bb"] + lg["bb"] * K_REG["bb"]) / (s["pa"] + K_REG["bb"]),
        "con": (s["conx"] + lg["con"] * K_REG["con"]) / (s["rest"] + K_REG["con"]),
    }


def xwoba(p):
    return W_BB * p["bb"] + (1 - p["k"] - p["bb"]) * p["con"]


def xwoba_sd(p, s):
    """Standard error of the projected xwOBA (delta method over the three components)."""
    var_k = p["k"] * (1 - p["k"]) / (s["pa"] + K_REG["k"])
    var_bb = p["bb"] * (1 - p["bb"]) / (s["pa"] + K_REG["bb"])
    var_con = SD_CON ** 2 / (s["rest"] + K_REG["con"])
    return math.sqrt(p["con"] ** 2 * var_k + (W_BB - p["con"]) ** 2 * var_bb
                     + (1 - p["k"] - p["bb"]) ** 2 * var_con)


def league(con, as_of, starters=False):
    """League averages from regular-season games before the date (this season and last)."""
    season = int(as_of[:4])
    lg = components(con, "game_type = 'R' AND season IN (?, ?) AND game_date < ? AND is_starter = ?",
                    (season - 1, season, as_of, int(starters)))
    if lg["pa"] < 5000:      # the first days of 2018: fall back to all of 2018
        lg = components(con, "game_type = 'R' AND season = 2018 AND is_starter = ?", (int(starters),))
    return {"k": lg["k"] / lg["pa"], "bb": lg["bb"] / lg["pa"], "con": lg["conx"] / lg["rest"],
            "seasons": f"{max(season - 1, 2018)}–{season}" if season > 2018 else "2018"}


# ---------------------------------------------------------------- reliever

def windows(con, as_of):
    """Recent window (this postseason, or the last 30 days) and the history before it."""
    season = int(as_of[:4])
    post_start = con.execute("""SELECT MIN(game_date) FROM pitcher_games
                                WHERE season = ? AND game_type != 'R'""", (season,)).fetchone()[0]
    if post_start and as_of > post_start:
        recent = ("season = ? AND game_type != 'R' AND game_date < ?", (season, as_of))
        history = ("game_type = 'R' AND season IN (?, ?) AND game_date < ?", (season - 1, season, as_of))
        return recent, history, "this postseason", True
    start = day_before(as_of, 30)
    recent = ("game_date >= ? AND game_date < ?", (start, as_of))
    history = ("game_type = 'R' AND season IN (?, ?) AND game_date < ?", (season - 1, season, start))
    return recent, history, "the last 30 days", False


def opponent_factor(con, pitcher, as_of, throws):
    """How strikeout-prone the postseason hitters he faced were, as an odds ratio vs. the league
    (1.0 = average; below 1 = harder to strike out). Uses each hitter's 2018-2020 regular-season
    strikeout rate against this pitcher hand, shrunk toward the league. Postseason only."""
    base = {r["batter_side"]: r["k"] / r["pa"] for r in con.execute(
        "SELECT batter_side, k, pa FROM league_platoon WHERE pitcher_hand = ?", (throws,))}
    placeholders = ",".join("?" * len(EXCLUDED))
    rows = con.execute(f"""
        SELECT p.stand, b.k, b.pa FROM postseason_pa p
        LEFT JOIN batter_splits b ON b.batter = p.batter AND b.pitcher_hand = ? AND b.bats = p.stand
        WHERE p.pitcher = ? AND p.game_date >= ? AND p.game_date < ?
          AND p.events IS NOT NULL AND p.events NOT IN ({placeholders})""",
                       (throws, pitcher, as_of[:4] + "-01-01", as_of, *EXCLUDED)).fetchall()
    logs = []
    for r in rows:
        lg = base.get(r["stand"])
        if not lg:
            continue
        bk = ((r["k"] or 0) + lg * K_REG["k"]) / ((r["pa"] or 0) + K_REG["k"])
        logs.append(math.log(odds(bk) / odds(lg)))
    return math.exp(sum(logs) / len(logs)) if logs else 1.0


def reliever(con, pitcher, as_of):
    """Projection of a reliever's xwOBA allowed, with the 'has he changed?' test."""
    info = con.execute("SELECT MAX(pitcher_name), MAX(throws) FROM pitcher_games WHERE pitcher = ?",
                       (pitcher,)).fetchone()
    (rw, rp), (hw, hp), label, post = windows(con, as_of)
    rec = components(con, f"pitcher = ? AND {rw}", (pitcher, *rp))
    hist = components(con, f"pitcher = ? AND {hw}", (pitcher, *hp))
    lg = league(con, as_of)
    opp = opponent_factor(con, pitcher, as_of, info[1]) if post and rec["pa"] else 1.0

    # Recent strikeouts, translated to what they would have been against average hitters
    rec_neutral = dict(rec)
    if opp != 1.0 and 0 < rec["k"] < rec["pa"]:
        rec_neutral["k"] = rec["pa"] * prob(odds(rec["k"] / rec["pa"]) / opp)

    hist_p = shrink(hist, lg)
    conc = hist["pa"] + K_REG["k"]
    p_same = prob(odds(hist_p["k"]) * opp)       # his usual strikeout rate, against these hitters
    p_chg = prob(odds(lg["k"]) * opp)            # a fresh league-average reliever, against these hitters
    if rec["pa"]:
        bf = math.exp(_log_ml(rec["k"], rec["pa"], p_chg, K_REG["k"]) - _log_ml(rec["k"], rec["pa"], p_same, conc))
        expected_k = rec["pa"] * p_same
        z = (rec["k"] - expected_k) / math.sqrt(rec["pa"] * p_same * (1 - p_same) * (1 + rec["pa"] / conc))
    else:
        bf, expected_k, z = 1.0, 0.0, 0.0
    p_changed = PRIOR_CHANGE * bf / (PRIOR_CHANGE * bf + 1 - PRIOR_CHANGE)

    both = combine(hist, rec_neutral)
    stable_p, changed_p = shrink(both, lg), shrink(rec_neutral, lg)
    stable, changed = xwoba(stable_p), xwoba(changed_p)
    return {
        "pitcher": pitcher, "name": flip_name(info[0]), "throws": info[1],
        "label": label, "postseason": post, "league": lg,
        "recent": rec, "history": hist, "opp": opp,
        "recent_k": rec["k"], "expected_k": expected_k, "z": z, "bf": bf, "p_changed": p_changed,
        "stable": stable, "stable_sd": xwoba_sd(stable_p, both), "stable_p": stable_p,
        "changed": changed, "changed_sd": xwoba_sd(changed_p, rec_neutral), "changed_p": changed_p,
        "expected": p_changed * changed + (1 - p_changed) * stable,
    }


# ---------------------------------------------------------------- starter

def comparable_starts(con, k_min, woba_max, pitch_max, as_of):
    """Regular-season starts before the date that were at least this dominant through two times
    through the order, and how hitters did the third time through."""
    rows = con.execute(f"""
        SELECT pitcher, game_date, early_pa, early_woba_sum, early_xwoba_sum,
               third_pa, third_woba_sum, third_xwoba_sum
        FROM starter_games
        WHERE game_type = 'R' AND early_pa >= {MIN_EARLY_PA} AND game_date < ?
          AND 1.0 * early_k / early_pa >= ? AND early_woba_sum / early_pa <= ? AND early_pitches <= ?""",
                       (as_of, k_min, woba_max, pitch_max)).fetchall()
    n = len(rows)
    out = {"starts": n, "reached": sum(r["third_pa"] > 0 for r in rows),
           "three": sum(r["third_pa"] >= 3 for r in rows), "pa": sum(r["third_pa"] for r in rows),
           "pitchers": len({r["pitcher"] for r in rows}), "last": max((r["game_date"] for r in rows), default=None),
           "xwoba": None, "woba": None, "se": None, "early_woba": None, "early_xwoba": None}
    if not out["pa"]:
        return out
    epa = sum(r["early_pa"] for r in rows)
    out["early_woba"] = sum(r["early_woba_sum"] for r in rows) / epa
    out["early_xwoba"] = sum(r["early_xwoba_sum"] for r in rows) / epa
    out["woba"] = sum(r["third_woba_sum"] for r in rows) / out["pa"]
    m = sum(r["third_xwoba_sum"] for r in rows) / out["pa"]
    out["xwoba"] = m
    # standard error clustered by pitcher
    resid = {}
    for r in rows:
        resid[r["pitcher"]] = resid.get(r["pitcher"], 0.0) + r["third_xwoba_sum"] - m * r["third_pa"]
    g = len(resid)
    out["se"] = math.sqrt(sum(v * v for v in resid.values()) * g / max(g - 1, 1)) / out["pa"]
    return out


# ---------------------------------------------------------------- decision

def decide(starter, rel, li, n_batters):
    """Win-probability change from pulling, if he is unchanged / if he has changed / expected."""
    if starter["xwoba"] is None or li is None:
        return None
    pts = n_batters / WOBA_SCALE * li / RUNS_PER_WIN * 100
    s, se = starter["xwoba"], starter["se"] or 0.0
    v_stable = (s - rel["stable"]) * pts
    v_changed = (s - rel["changed"]) * pts
    p = rel["p_changed"]

    def better(r, sd):
        return normal_cdf((s - r) / math.sqrt(se ** 2 + sd ** 2))

    if (v_stable > 0) != (v_changed > 0):
        break_even = v_stable / (v_stable - v_changed)       # P(changed) where pulling and staying tie
        pull_if = "below" if v_stable > 0 else "above"
    else:
        break_even, pull_if = None, ("always" if v_stable > 0 else "never")
    return {
        "stable": v_stable, "changed": v_changed, "expected": (1 - p) * v_stable + p * v_changed,
        "p_changed": p, "break_even": break_even, "pull_if": pull_if,
        "p_pull_better": (1 - p) * better(rel["stable"], rel["stable_sd"]) + p * better(rel["changed"], rel["changed_sd"]),
        "per_010": pts * 0.010,
    }


def data_used(starter, rel, as_of):
    """Every input and the latest game it uses, so a reader can check nothing comes from after the date."""
    h, r = rel["history"], rel["recent"]
    rows = [
        ("Starter: comparable starts", f"regular-season starts through {starter['last'] or '—'}", True),
        ("Reliever: history", f"regular-season games {h['first'] or '—'} to {h['last'] or '—'} ({h['pa']} PA)", True),
        ("Reliever: recent", f"{rel['label']}: {r['games']} games through {r['last'] or '—'} ({r['pa']} PA)", True),
        ("League averages", f"regular-season games {rel['league']['seasons']} before {as_of}", True),
    ]
    if rel["postseason"]:
        rows.append(("Hitters' strikeout rates", "2018–2020 regular seasons (all before the postseason)", True))
    rows.append(("Leverage", "2018–2020 regular seasons (not limited by date)", as_of >= "2020-09-28"))
    return rows


# ---------------------------------------------------------------- bullpen board

def bullpen(con, team, as_of):
    """Relievers who pitched for the team in the 45 days before the date, best expected xwOBA first."""
    from datetime import date
    from queries import pitcher_totals, recent_window
    ids = [r[0] for r in con.execute("""SELECT DISTINCT pitcher FROM pitcher_games
                                        WHERE team = ? AND is_starter = 0 AND game_date >= ? AND game_date < ?""",
                                     (team, day_before(as_of, 45), as_of))]
    season = int(as_of[:4])
    where, params, label = recent_window(con, as_of)
    out = []
    for pid in ids:
        last = con.execute("SELECT MAX(game_date) FROM pitcher_games WHERE pitcher = ? AND game_date < ?",
                           (pid, as_of)).fetchone()[0]
        rest = (date.fromisoformat(as_of) - date.fromisoformat(last)).days - 1
        last3 = con.execute("""SELECT COALESCE(SUM(pitches), 0), COUNT(*) FROM pitcher_games
                               WHERE pitcher = ? AND game_date >= ? AND game_date < ?""",
                            (pid, day_before(as_of, 3), as_of)).fetchone()
        apps7 = con.execute("""SELECT COUNT(*) FROM pitcher_games
                               WHERE pitcher = ? AND game_date >= ? AND game_date < ?""",
                            (pid, day_before(as_of, 7), as_of)).fetchone()[0]
        season_tot = pitcher_totals(con, pid, "game_type = 'R' AND season = ? AND game_date < ?", (season, as_of))
        recent = pitcher_totals(con, pid, where, params)
        rel = reliever(con, pid, as_of)
        splits = {r["batter_side"]: r["xwoba"] for r in con.execute(
            "SELECT batter_side, xwoba FROM pitcher_splits WHERE pitcher = ? AND pa >= 30", (pid,))}

        notes = []
        if rest == 0:
            notes.append(("warn", "Pitched yesterday"))
        if last3[1] >= 2:
            notes.append(("warn", f"{last3[1]} outings in the last 3 days"))
        if last3[0] >= 35:
            notes.append(("warn", f"{last3[0]} pitches in the last 3 days"))
        if (recent and season_tot and recent["velo"] and season_tot["velo"] and recent["ff_n"] >= 20
                and recent["velo"] - season_tot["velo"] <= -1.0):
            notes.append(("warn", f"Velocity down {season_tot['velo'] - recent['velo']:.1f} mph"))
        if rel["p_changed"] >= 0.5 and rel["recent_k"] < rel["expected_k"]:
            notes.append(("warn", f"Strikeouts {rel['recent_k']} vs {rel['expected_k']:.0f} expected "
                                  f"({rel['p_changed']:.0%} he has changed)"))
        if not notes and rest >= 1:
            notes.append(("ok", "Rested, no warning signs"))

        out.append({
            "pitcher": pid, "name": rel["name"], "throws": rel["throws"],
            "rest": rest, "pitches3": last3[0], "apps7": apps7,
            "last_label": "{0:%b} {0.day}".format(date.fromisoformat(last)),
            "season": season_tot, "recent": recent,
            "expected": rel["expected"], "p_changed": rel["p_changed"],
            "vs_l": splits.get("L"), "vs_r": splits.get("R"),
            "notes": notes,
        })
    out.sort(key=lambda r: r["expected"])
    return {"relievers": out, "recent_label": label, "season": season}
