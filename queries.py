"""
Data access for the site: lists for the forms, pitcher totals, leverage, the starter card and
the win-probability chart. The projections and the pull decision are in model.py.
All of it reads site_data/coaches.db (built by build_database.py).
"""
import sqlite3
from datetime import date, timedelta
from pathlib import Path

DB_PATH = Path(__file__).parent / "site_data" / "coaches.db"

# Same assumptions as snell_decision.py (the projections themselves live in model.py)
WOBA_SCALE = 1.2        # wOBA points -> runs per plate appearance
RUNS_PER_WIN = 10       # at average leverage, about 10 runs = 1 win
MIN_EARLY_PA = 18       # a start counts once the starter has faced 18 batters (two times through)

K_EVENTS = ("strikeout", "strikeout_double_play")


def connect():
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con


# ---------------------------------------------------------------- small helpers

def ratio(num, den, digits=3):
    return round(num / den, digits) if den else None


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


def recent_window(con, as_of):
    """'This postseason' once the postseason has started, otherwise 'last 30 days'."""
    season = int(as_of[:4])
    post_start = con.execute("""SELECT MIN(game_date) FROM pitcher_games
                                WHERE season = ? AND game_type != 'R'""", (season,)).fetchone()[0]
    if post_start and as_of > post_start:
        return ("season = ? AND game_type != 'R' AND game_date < ?", (season, as_of),
                "this postseason")
    return ("game_date >= ? AND game_date < ?", (day_before(as_of, 30), as_of), "over the last 30 days")


# ---------------------------------------------------------------- manager: how much is at stake?

def leverage(con, inning, pitching_half, outs, on1, on2, on3, our_lead):
    """Leverage index for the game state, from 2018-2020 regular seasons.
    The batting team's margin is minus our lead."""
    bases = f"{int(on1)}{int(on2)}{int(on3)}"
    diff = max(-4, min(4, -our_lead))
    row = con.execute("""SELECT li, n_pa FROM leverage_by_state
                         WHERE inning = ? AND half = ? AND outs = ? AND bases = ? AND diff = ?""",
                      (min(int(inning), 9), pitching_half, outs, bases, diff)).fetchone()
    return (row["li"], row["n_pa"]) if row else (None, 0)


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
    tto = con.execute("""SELECT SUM(early_pa) AS epa, SUM(early_xwoba_sum) AS ew,
                                SUM(third_pa) AS tpa, SUM(third_xwoba_sum) AS tw, COUNT(*) AS starts
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

def relievers_used(con, team, as_of):
    """Relievers the team used in the 45 days before the date (for The Hook's dropdown)."""
    rows = con.execute("""SELECT pitcher, MAX(pitcher_name) AS name, MAX(throws) AS throws FROM pitcher_games
                          WHERE team = ? AND is_starter = 0 AND game_date >= ? AND game_date < ?
                          GROUP BY pitcher""", (team, day_before(as_of, 45), as_of)).fetchall()
    return sorted(({"pitcher": r["pitcher"], "name": flip_name(r["name"]), "throws": r["throws"]} for r in rows),
                  key=lambda r: r["name"])


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
