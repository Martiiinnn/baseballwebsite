"""
Every number the site shows comes from a function in this file.
All of them read site_data/coaches.db (built by build_database.py) and use only
information available BEFORE the date being looked at, like a coach would have.
"""
import math
import sqlite3
from datetime import date, timedelta
from pathlib import Path

DB_PATH = Path(__file__).parent / "site_data" / "coaches.db"

# Same assumptions as snell_decision.py
WOBA_SCALE = 1.2        # wOBA points -> runs per plate appearance
RUNS_PER_WIN = 10       # at average leverage, about 10 runs = 1 win
K_REG = 300             # plate appearances of league-average "prior" mixed into small samples
MIN_EARLY_PA = 18       # a start counts once the starter has faced 18 batters (two times through)

K_EVENTS = ("strikeout", "strikeout_double_play")


def connect():
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con


# ---------------------------------------------------------------- small helpers

def ratio(num, den, digits=3):
    return round(num / den, digits) if den else None


def regress(rate, n, prior, k=K_REG):
    """Blend a small sample with a prior: the fewer plate appearances, the closer to the prior."""
    if not n or rate is None:
        return prior
    return (rate * n + prior * k) / (n + k)


def flip_name(name):
    """'Snell, Blake' -> 'Blake Snell'"""
    if name and ", " in name:
        last, first = name.split(", ", 1)
        return f"{first} {last}"
    return name


def day_before(d, days):
    return (date.fromisoformat(d) - timedelta(days=days)).isoformat()


def leverage_label(li):
    if li is None:
        return "unknown"
    return "high" if li >= 2 else ("medium" if li >= 0.85 else "low")


ROUND_NAMES = {"R": "regular season", "F": "Wild Card", "D": "Division Series",
               "L": "LCS", "W": "World Series"}


# ---------------------------------------------------------------- lists for the forms

def teams(con):
    return [r[0] for r in con.execute("SELECT DISTINCT team FROM pitcher_games ORDER BY team")]


def date_range(con):
    return tuple(con.execute("SELECT MIN(game_date), MAX(game_date) FROM pitcher_games").fetchone())


def player_name(con, player_id):
    row = con.execute("SELECT name FROM players WHERE player_id = ?", (player_id,)).fetchone()
    return row[0] if row else f"ID {player_id}"


# ---------------------------------------------------------------- pitcher totals

TOTALS = """SUM(pa) AS pa, SUM(k) AS k, SUM(xwoba_sum) AS xwoba_sum, SUM(whiffs) AS whiffs,
            SUM(swings) AS swings, SUM(ff_velo_n) AS ff_n, SUM(ff_velo_sum) AS ff_sum,
            SUM(ff_spin_n) AS spin_n, SUM(ff_spin_sum) AS spin_sum, SUM(pitches) AS pitches,
            COUNT(*) AS games"""


def summarize(row):
    """Turn summed counts into the rates the pages show."""
    if row is None or not row["games"]:
        return None
    return {
        "games": row["games"],
        "pa": row["pa"] or 0,
        "pitches": row["pitches"] or 0,
        "velo": ratio(row["ff_sum"] or 0, row["ff_n"], 1),
        "ff_n": row["ff_n"] or 0,
        "spin": ratio(row["spin_sum"] or 0, row["spin_n"], 0),
        "whiff": ratio(row["whiffs"] or 0, row["swings"]),
        "swings": row["swings"] or 0,
        "k_pct": ratio(row["k"] or 0, row["pa"]),
        "xwoba": ratio(row["xwoba_sum"] or 0, row["pa"]),
    }


def pitcher_totals(con, pitcher, where, params=()):
    row = con.execute(f"SELECT {TOTALS} FROM pitcher_games WHERE pitcher = ? AND {where}",
                      (pitcher, *params)).fetchone()
    return summarize(row)


def league_reliever_xwoba(con):
    r = con.execute("SELECT SUM(xwoba_sum), SUM(pa) FROM league_reliever").fetchone()
    return r[0] / r[1]


def recent_window(con, as_of):
    """'This postseason' once the postseason has started, otherwise 'last 30 days'."""
    season = int(as_of[:4])
    post_start = con.execute("""SELECT MIN(game_date) FROM pitcher_games
                                WHERE season = ? AND game_type != 'R'""", (season,)).fetchone()[0]
    if post_start and as_of > post_start:
        return ("season = ? AND game_type != 'R' AND game_date < ?", (season, as_of),
                "this postseason")
    return ("game_date >= ? AND game_date < ?", (day_before(as_of, 30), as_of), "over the last 30 days")


def reliever_scenarios(con, pitcher, as_of):
    """Three estimates of a reliever's expected xwOBA allowed, using only games before as_of."""
    season = int(as_of[:4])
    league = league_reliever_xwoba(con)
    base = pitcher_totals(con, pitcher,
                          "game_type = 'R' AND season IN (?, ?) AND game_date < ?",
                          (season - 1, season, as_of))
    where, params, label = recent_window(con, as_of)
    recent = pitcher_totals(con, pitcher, where, params)

    base_x, base_n = (base["xwoba"], base["pa"]) if base else (None, 0)
    rec_x, rec_n = (recent["xwoba"], recent["pa"]) if recent else (None, 0)
    full = regress(base_x, base_n, league)
    form = regress(rec_x, rec_n, full)
    worst = rec_x if rec_n else full
    return {
        "recent_label": label,
        "base": base, "recent": recent, "league": league,
        "scenarios": [
            {"key": "full", "name": "Full strength", "xwoba": full,
             "basis": (f"{season - 1}-{season} regular seasons ({base_n} PA, xwOBA "
                       f"{base_x:.3f}) blended with the league reliever average")
             if base_n else "no recent regular-season data: league reliever average"},
            {"key": "form", "name": "Recent form", "xwoba": form,
             "basis": (f"form {label} ({rec_n} PA, xwOBA {rec_x:.3f}) blended with full strength")
             if rec_n else f"no appearances {label}"},
            {"key": "worst", "name": "Recent form is real", "xwoba": worst,
             "basis": f"form {label}, taken at face value" if rec_n else f"no appearances {label}"},
        ],
    }


# ---------------------------------------------------------------- manager: should I pull him?

def comparable_starts(con, k_min, woba_max, pitch_max):
    """Regular-season starts 2018-2020 at least this dominant through two times, and how
    hitters did the third time through."""
    r = con.execute(f"""
        SELECT COUNT(*) AS starts,
               SUM(CASE WHEN third_pa > 0 THEN 1 ELSE 0 END) AS reached,
               SUM(third_pa) AS pa, SUM(third_woba_sum) AS w, SUM(third_woba_sq) AS w2,
               SUM(third_k) AS k
        FROM starter_games
        WHERE game_type = 'R' AND early_pa >= {MIN_EARLY_PA}
          AND 1.0 * early_k / early_pa >= ?
          AND early_woba_sum / early_pa <= ?
          AND early_pitches <= ?""", (k_min, woba_max, pitch_max)).fetchone()
    if not r["pa"]:
        return {"starts": r["starts"] or 0, "pa": 0, "woba": None, "ci": None}
    w = r["w"] / r["pa"]
    sd = math.sqrt(max(r["w2"] / r["pa"] - w * w, 0))
    return {"starts": r["starts"], "reached": r["reached"], "pa": r["pa"], "woba": w,
            "ci": 1.96 * sd / math.sqrt(r["pa"]), "k_pct": r["k"] / r["pa"]}


def leverage(con, inning, pitching_half, outs, on1, on2, on3, our_lead):
    """Leverage index for the game state, from 2018-2020 regular seasons.
    The batting team's margin is minus our lead."""
    bases = f"{int(on1)}{int(on2)}{int(on3)}"
    diff = max(-4, min(4, -our_lead))
    row = con.execute("""SELECT li, n_pa FROM leverage_by_state
                         WHERE inning = ? AND half = ? AND outs = ? AND bases = ? AND diff = ?""",
                      (min(int(inning), 9), pitching_half, outs, bases, diff)).fetchone()
    return (row["li"], row["n_pa"]) if row else (None, 0)


def decision(starter_woba, scenarios, li, n_batters):
    """Win-probability change (in points) from pulling the starter, for each reliever scenario."""
    rows = []
    for s in scenarios:
        runs = n_batters * (starter_woba - s["xwoba"]) / WOBA_SCALE
        dwp = runs * li / RUNS_PER_WIN if li is not None else None
        rows.append({**s, "gap": starter_woba - s["xwoba"], "runs": runs,
                     "points": dwp * 100 if dwp is not None else None})
    up = rows[0]["points"]
    down = rows[-1]["points"]
    if up is None:
        verdict, need = "unknown", None
    elif up > 0 and down > 0:
        verdict, need = "pull", None
    elif up <= 0 and down <= 0:
        verdict, need = "stay", None
    else:
        # Pulling is worth it only if you're confident enough the reliever is at full strength.
        need = -down / (up - down)
        verdict = "depends"
    return {"rows": rows, "verdict": verdict, "confidence_needed": need}


# ---------------------------------------------------------------- pitching coach: today's starter

def team_game(con, team, on_or_before):
    """The team's most recent game on or before the date."""
    return con.execute("""SELECT game_pk, game_date, game_type, opponent FROM pitcher_games
                          WHERE team = ? AND game_date <= ? AND is_starter = 1
                          ORDER BY game_date DESC, game_pk DESC LIMIT 1""",
                       (team, on_or_before)).fetchone()


def starter_card(con, team, on_or_before):
    game = team_game(con, team, on_or_before)
    if game is None:
        return None
    today = con.execute("""SELECT * FROM pitcher_games
                           WHERE game_pk = ? AND team = ? AND is_starter = 1""",
                        (game["game_pk"], team)).fetchone()
    early = con.execute("SELECT * FROM starter_games WHERE game_pk = ? AND pitcher = ?",
                        (game["game_pk"], today["pitcher"])).fetchone()
    season = today["season"]
    season_tot = pitcher_totals(con, today["pitcher"],
                                "game_type = 'R' AND season = ? AND game_date < ?",
                                (season, game["game_date"]))
    today_tot = pitcher_totals(con, today["pitcher"], "game_pk = ?", (game["game_pk"],))
    tto = con.execute("""SELECT SUM(early_pa) AS epa, SUM(early_woba_sum) AS ew,
                                SUM(third_pa) AS tpa, SUM(third_woba_sum) AS tw, COUNT(*) AS starts
                         FROM starter_games
                         WHERE pitcher = ? AND game_type = 'R' AND season = ? AND game_date < ?""",
                      (today["pitcher"], season, game["game_date"])).fetchone()

    def compare(key, better_if_higher, threshold):
        a, b = today_tot.get(key), (season_tot or {}).get(key)
        if a is None or b is None:
            return None
        d = a - b
        if abs(d) < threshold:
            label = "about usual"
        else:
            label = "better than usual" if (d > 0) == better_if_higher else "worse than usual"
        return {"today": a, "season": b, "diff": d, "label": label}

    early_k = ratio(early["early_k"], early["early_pa"]) if early else None
    early_w = ratio(early["early_woba_sum"], early["early_pa"]) if early else None
    rows = [
        ("Velocity (mph)", compare("velo", True, 0.5), "{:.1f}".format),
        ("Spin (rpm)", compare("spin", True, 50), "{:.0f}".format),
        ("Whiff rate", compare("whiff", True, 0.04), "{:.0%}".format),
        ("Strikeout rate", compare("k_pct", True, 0.04), "{:.0%}".format),
        ("Expected wOBA", compare("xwoba", False, 0.025),
         lambda v: f"{v:.3f}".lstrip("0")),
    ]
    measured = [c for _, c, _ in rows if c]
    return {
        "game": game, "round": ROUND_NAMES.get(game["game_type"], game["game_type"]),
        "pitcher": today["pitcher"], "name": flip_name(today["pitcher_name"]),
        "throws": today["throws"], "season": season,
        "today": today_tot, "season_tot": season_tot,
        "early": early,
        "early_k": early_k, "early_woba": early_w,
        "rows": rows,
        "measured": len(measured),
        "better": sum(c["label"] == "better than usual" for c in measured),
        "tto": {"starts": tto["starts"],
                "early": ratio(tto["ew"] or 0, tto["epa"]),
                "third": ratio(tto["tw"] or 0, tto["tpa"]), "third_pa": tto["tpa"] or 0},
        # thresholds for the manager tool: "starts at least roughly this dominant"
        "manager_link": {"k_min": round(max(0.0, (early_k or 0) - 0.15), 2),
                         "woba_max": round((early_w or 0) + 0.10, 3),
                         "pitch_max": int((early["early_pitches"] if early else 0) + 12)},
    }


# ---------------------------------------------------------------- bullpen coach: who's ready?

def bullpen(con, team, as_of):
    """Relievers who pitched for the team in the 45 days before as_of, best options first."""
    ids = [r[0] for r in con.execute("""SELECT DISTINCT pitcher FROM pitcher_games
                                        WHERE team = ? AND is_starter = 0
                                          AND game_date >= ? AND game_date < ?""",
                                     (team, day_before(as_of, 45), as_of))]
    season = int(as_of[:4])
    where, params, label = recent_window(con, as_of)
    out = []
    for pid in ids:
        info = con.execute("""SELECT pitcher_name, throws, MAX(game_date) AS last FROM pitcher_games
                              WHERE pitcher = ? AND game_date < ?""", (pid, as_of)).fetchone()
        rest = (date.fromisoformat(as_of) - date.fromisoformat(info["last"])).days - 1
        last3 = con.execute("""SELECT COALESCE(SUM(pitches), 0), COUNT(*) FROM pitcher_games
                               WHERE pitcher = ? AND game_date >= ? AND game_date < ?""",
                            (pid, day_before(as_of, 3), as_of)).fetchone()
        apps7 = con.execute("""SELECT COUNT(*) FROM pitcher_games
                               WHERE pitcher = ? AND game_date >= ? AND game_date < ?""",
                            (pid, day_before(as_of, 7), as_of)).fetchone()[0]
        season_tot = pitcher_totals(con, pid, "game_type = 'R' AND season = ? AND game_date < ?",
                                    (season, as_of))
        recent = pitcher_totals(con, pid, where, params)
        sc = reliever_scenarios(con, pid, as_of)
        splits = {r["batter_side"]: r["xwoba"] for r in con.execute(
            "SELECT batter_side, xwoba, pa FROM pitcher_splits WHERE pitcher = ? AND pa >= 30", (pid,))}

        notes = []
        if rest == 0:
            notes.append(("warn", "Pitched yesterday"))
        if last3[1] >= 2:
            notes.append(("warn", f"{last3[1]} outings in the last 3 days"))
        if last3[0] >= 35:
            notes.append(("warn", f"{last3[0]} pitches in the last 3 days"))
        if recent and season_tot:
            if (recent["velo"] and season_tot["velo"] and recent["ff_n"] >= 20
                    and recent["velo"] - season_tot["velo"] <= -1.0):
                notes.append(("warn", f"Velocity down {season_tot['velo'] - recent['velo']:.1f} mph"))
            if (recent["whiff"] is not None and season_tot["whiff"] is not None
                    and recent["swings"] >= 25 and recent["whiff"] - season_tot["whiff"] <= -0.08):
                notes.append(("warn", f"Whiff rate down {100 * (season_tot['whiff'] - recent['whiff']):.0f} pts"))
        if not notes and rest >= 1:
            notes.append(("ok", "Rested, no warning signs"))

        out.append({
            "pitcher": pid, "name": flip_name(info["pitcher_name"]), "throws": info["throws"],
            "last": info["last"], "rest": rest, "pitches3": last3[0], "apps7": apps7,
            "last_label": "{0:%b} {0.day}".format(date.fromisoformat(info["last"])),
            "season": season_tot, "recent": recent,
            "expected": sc["scenarios"][1]["xwoba"],
            "vs_l": splits.get("L"), "vs_r": splits.get("R"),
            "notes": notes,
        })
    out.sort(key=lambda r: r["expected"])
    return {"relievers": out, "recent_label": label, "season": season}


# ---------------------------------------------------------------- game: win probability

def find_postseason_game(con, game_date, team):
    return con.execute("""SELECT DISTINCT game_pk, home_team, away_team, game_type
                          FROM postseason_pa WHERE game_date = ? AND (home_team = ? OR away_team = ?)""",
                       (game_date, team, team)).fetchone()


def win_probability(con, game_pk, team):
    """One row per plate appearance with the team's win probability before and after it."""
    rows = con.execute("""
        SELECT a.at_bat_number, a.inning, a.inning_topbot, a.events, a.home_team,
               a.home_win_exp, a.delta_home_win_exp, a.bat_score, a.fld_score,
               b.name AS batter, p.name AS pitcher
        FROM postseason_pa a
        LEFT JOIN players b ON b.player_id = a.batter
        LEFT JOIN players p ON p.player_id = a.pitcher
        WHERE a.game_pk = ? ORDER BY a.at_bat_number""", (game_pk,)).fetchall()
    out = []
    for r in rows:
        before = r["home_win_exp"] if r["home_team"] == team else 1 - r["home_win_exp"]
        change = r["delta_home_win_exp"] or 0
        change = change if r["home_team"] == team else -change
        out.append({"ab": r["at_bat_number"], "inning": r["inning"], "half": r["inning_topbot"],
                    "event": (r["events"] or "").replace("_", " "),
                    "batter": r["batter"], "pitcher": r["pitcher"],
                    "before": before, "after": before + change, "change": change})
    return out
