"""
Build the project database for the coaches' site.

Step 1  Load Statcast pitches (2018-2020 regular seasons + 2020 postseason) into a
        local SQLite warehouse: data/warehouse.db  (large: keep it out of git)
Step 2  Build tables with SQL:
          plate_appearances   one row per PA, with times through the order (tto)
          pitcher_games       one row per pitcher per game  -> bullpen coach, pitching coach
          starter_games       one row per start: first two times through vs. third -> manager
          pitcher_splits      each pitcher vs. left/right-handed batters  -> matchups
          batter_splits       each hitter vs. left/right-handed pitchers  -> matchups
          league_platoon / league_tto / league_reliever   league baselines
          leverage_by_state   how much each game state can swing win probability
          players             player id -> name
Step 3  Validation checks: data quality, known facts, and whether the SQL reproduces
        the pandas results from snell_vs_peers.py
Step 4  Export the small tables the website reads: site_data/coaches.db
        (plus site_data/DATA_DICTIONARY.md describing every table and column)

Setup:  pip install pybaseball pandas        (sqlite3 is built into Python)
Run:    python build_database.py              first run: a few minutes (reads pybaseball's cache)
        python build_database.py --skip-load  rebuild the tables without reloading the pitches
"""
import argparse
import sqlite3
import time
from pathlib import Path

import pandas as pd
from pybaseball import cache, playerid_reverse_lookup, statcast

cache.enable()

WAREHOUSE = Path("data/warehouse.db")
SITE_DB = Path("site_data/coaches.db")
OUTPUT = Path("output")                     # where the earlier scripts wrote their CSVs

# Same date ranges and monthly pulls as the earlier scripts, so they come from the cache.
REGULAR_SEASONS = {
    2018: ("2018-03-29", "2018-10-01"),
    2019: ("2019-03-20", "2019-09-29"),
    2020: ("2020-07-23", "2020-09-27"),
}
POSTSEASON = ("2020-09-29", "2020-10-27")
KEEP_GAME_TYPES = ["R", "F", "D", "L", "W"]   # regular season, wild card, DS, LCS, World Series

# Facts used to check the data (official box score, 2020 World Series Game 6).
GAME6_SNELL = {"date": "2020-10-27", "name": "Snell, Blake",
               "pitches": 73, "batters_faced": 18, "k": 9, "h": 2, "bb": 0}
GAMES_PER_SEASON = {2018: 2431, 2019: 2429, 2020: 898}

# ---------------------------------------------------------------- pitches table

INT_COLS = ["game_pk", "inning", "at_bat_number", "pitch_number", "pitcher", "batter",
            "outs_when_up", "bat_score", "fld_score", "post_bat_score"]
FLOAT_COLS = ["release_speed", "release_spin_rate", "launch_speed", "woba_value", "woba_denom",
              "estimated_woba_using_speedangle", "home_win_exp", "delta_home_win_exp"]
TEXT_COLS = ["game_type", "home_team", "away_team", "inning_topbot", "player_name", "stand",
             "p_throws", "events", "description", "type", "pitch_type"]
RUNNER_COLS = ["on_1b", "on_2b", "on_3b"]          # stored as 0/1: was a runner there?

PITCHES_SCHEMA = """
CREATE TABLE pitches (
    game_pk INTEGER, game_date TEXT, game_type TEXT, home_team TEXT, away_team TEXT,
    inning INTEGER, inning_topbot TEXT, at_bat_number INTEGER, pitch_number INTEGER,
    pitcher INTEGER, player_name TEXT, batter INTEGER, stand TEXT, p_throws TEXT,
    outs_when_up INTEGER, on_1b INTEGER, on_2b INTEGER, on_3b INTEGER,
    bat_score INTEGER, fld_score INTEGER, post_bat_score INTEGER,
    events TEXT, description TEXT, type TEXT, pitch_type TEXT,
    release_speed REAL, release_spin_rate REAL, launch_speed REAL,
    woba_value REAL, woba_denom REAL, estimated_woba_using_speedangle REAL,
    home_win_exp REAL, delta_home_win_exp REAL
)"""
PITCH_COLUMNS = ["game_pk", "game_date", "game_type", "home_team", "away_team",
                 "inning", "inning_topbot", "at_bat_number", "pitch_number",
                 "pitcher", "player_name", "batter", "stand", "p_throws",
                 "outs_when_up", "on_1b", "on_2b", "on_3b",
                 "bat_score", "fld_score", "post_bat_score",
                 "events", "description", "type", "pitch_type",
                 "release_speed", "release_spin_rate", "launch_speed",
                 "woba_value", "woba_denom", "estimated_woba_using_speedangle",
                 "home_win_exp", "delta_home_win_exp"]


def month_chunks(start: str, end: str):
    s, e = pd.Timestamp(start), pd.Timestamp(end)
    while s <= e:
        m_end = min(s + pd.offsets.MonthEnd(0), e)
        yield s.strftime("%Y-%m-%d"), m_end.strftime("%Y-%m-%d")
        s = m_end + pd.Timedelta(days=1)


def clean(df: pd.DataFrame) -> pd.DataFrame:
    """Convert pybaseball's nullable pandas types into plain values SQLite accepts."""
    out = pd.DataFrame(index=df.index)
    out["game_date"] = pd.to_datetime(df["game_date"]).dt.strftime("%Y-%m-%d")
    for c in INT_COLS:
        s = pd.to_numeric(df[c], errors="coerce")
        out[c] = (s.astype("int64") if s.notna().all()
                  else s.astype("Int64").astype(object).where(s.notna(), None))
    for c in FLOAT_COLS:
        out[c] = pd.to_numeric(df[c], errors="coerce").astype("float64")
    for c in TEXT_COLS:
        out[c] = df[c].astype(object).where(df[c].notna(), None)
    for c in RUNNER_COLS:
        out[c] = df[c].notna().astype(int)
    return (out[PITCH_COLUMNS]
            .drop_duplicates(["game_pk", "at_bat_number", "pitch_number"]))


def load_pitches(con):
    print("\nStep 1. Loading pitches into the warehouse ...")
    con.execute("DROP TABLE IF EXISTS pitches")
    con.execute(PITCHES_SCHEMA)
    chunks = [c for v in REGULAR_SEASONS.values() for c in month_chunks(*v)] + [POSTSEASON]
    total = 0
    for s, e in chunks:
        df = statcast(s, e)
        if df is None or df.empty:
            continue
        df = df[df["game_type"].isin(KEEP_GAME_TYPES)]
        if df.empty:
            continue
        rows = clean(df)
        rows.to_sql("pitches", con, if_exists="append", index=False, chunksize=20000)
        total += len(rows)
        print(f"  {s} to {e}: {len(rows):>8,} pitches")
    con.execute("CREATE INDEX idx_pitches_pa ON pitches (game_pk, at_bat_number, pitch_number)")
    con.commit()
    print(f"  total: {total:,} pitches")


# ---------------------------------------------------------------- SQL tables

def sql_list(items) -> str:
    return ", ".join(f"'{x}'" for x in sorted(items))


K = sql_list({"strikeout", "strikeout_double_play"})
HITS = sql_list({"single", "double", "triple", "home_run"})
WHIFFS = sql_list({"swinging_strike", "swinging_strike_blocked", "missed_bunt"})
SWINGS = sql_list({"swinging_strike", "swinging_strike_blocked", "missed_bunt", "foul",
                   "foul_tip", "foul_bunt", "hit_into_play", "hit_into_play_no_out",
                   "hit_into_play_score"})

# Counting stats summed over plate appearances. Rates are sums divided at the end,
# so any group (a game, a season, a split) can be added up correctly.
METRIC_SUMS = f"""
    SUM(CASE WHEN woba_denom = 1 THEN 1 ELSE 0 END)               AS pa,
    SUM(CASE WHEN woba_denom = 1 AND events IN ({K}) THEN 1 ELSE 0 END) AS k,
    SUM(CASE WHEN events = 'walk' THEN 1 ELSE 0 END)              AS bb,
    SUM(CASE WHEN events IN ({HITS}) THEN 1 ELSE 0 END)           AS h,
    SUM(COALESCE(xwoba, 0))                                       AS xwoba_sum,
    SUM(CASE WHEN woba_denom = 1 THEN woba_value ELSE 0 END)      AS woba_sum,
    SUM(whiffs)                                                   AS whiffs,
    SUM(swings)                                                   AS swings,
    SUM(batted_ball)                                              AS bbe,
    SUM(CASE WHEN batted_ball = 1 AND launch_speed >= 95 THEN 1 ELSE 0 END) AS hard_hit,
    SUM(CASE WHEN batted_ball = 1 THEN launch_speed ELSE 0 END)   AS ev_sum"""

# Readable rates computed from METRIC_SUMS (used on the smaller summary tables).
METRIC_RATES = """
    ROUND(xwoba_sum / NULLIF(pa, 0), 3)          AS xwoba,
    ROUND(woba_sum / NULLIF(pa, 0), 3)           AS woba,
    ROUND(1.0 * k / NULLIF(pa, 0), 3)            AS k_pct,
    ROUND(1.0 * bb / NULLIF(pa, 0), 3)           AS bb_pct,
    ROUND(1.0 * whiffs / NULLIF(swings, 0), 3)   AS whiff_pct,
    ROUND(1.0 * hard_hit / NULLIF(bbe, 0), 3)    AS hard_hit_pct,
    ROUND(ev_sum / NULLIF(bbe, 0), 1)            AS avg_ev"""

TEAM = "CASE WHEN inning_topbot = 'Top' THEN home_team ELSE away_team END"
OPPONENT = "CASE WHEN inning_topbot = 'Top' THEN away_team ELSE home_team END"

BUILD_STEPS = [
    ("plate_appearances", f"""
DROP TABLE IF EXISTS plate_appearances;

-- Pitch-level totals for each plate appearance, plus its first and last pitch.
CREATE TEMP TABLE pa_rollup AS
SELECT game_pk, at_bat_number,
       MIN(pitch_number) AS first_p,
       MAX(pitch_number) AS last_p,
       COUNT(*)          AS n_pitches,
       -- Statcast's win-probability change is per pitch, so a PA's change is the sum
       -- (this catches wild pitches, steals and other events in the middle of a PA)
       SUM(delta_home_win_exp) AS delta_wp,
       SUM(CASE WHEN description IN ({WHIFFS}) THEN 1 ELSE 0 END) AS whiffs,
       SUM(CASE WHEN description IN ({SWINGS}) THEN 1 ELSE 0 END) AS swings,
       SUM(CASE WHEN pitch_type = 'FF' AND release_speed IS NOT NULL THEN 1 ELSE 0 END) AS ff_velo_n,
       SUM(CASE WHEN pitch_type = 'FF' THEN COALESCE(release_speed, 0) ELSE 0 END) AS ff_velo_sum,
       SUM(CASE WHEN pitch_type = 'FF' AND release_spin_rate IS NOT NULL THEN 1 ELSE 0 END) AS ff_spin_n,
       SUM(CASE WHEN pitch_type = 'FF' THEN COALESCE(release_spin_rate, 0) ELSE 0 END) AS ff_spin_sum
FROM pitches
GROUP BY game_pk, at_bat_number;

-- Game state comes from the first pitch; the result comes from the last pitch.
CREATE TEMP TABLE pa_base AS
SELECT f.game_pk, f.game_date, CAST(substr(f.game_date, 1, 4) AS INTEGER) AS season,
       f.game_type, f.home_team, f.away_team, f.inning, f.inning_topbot, f.at_bat_number,
       l.pitcher, l.player_name AS pitcher_name, l.p_throws, f.batter, f.stand,
       f.outs_when_up, f.on_1b, f.on_2b, f.on_3b, f.bat_score, f.fld_score,
       l.post_bat_score - f.bat_score AS runs_scored,
       l.events, l.woba_value, l.woba_denom,
       CASE WHEN l.woba_denom = 1
            THEN COALESCE(l.estimated_woba_using_speedangle, l.woba_value) END AS xwoba,
       CASE WHEN l.type = 'X' THEN 1 ELSE 0 END AS in_play,
       CASE WHEN l.type = 'X' AND l.launch_speed IS NOT NULL THEN 1 ELSE 0 END AS batted_ball,
       CASE WHEN l.type = 'X' THEN l.launch_speed END AS launch_speed,
       f.home_win_exp, r.delta_wp AS delta_home_win_exp,
       r.n_pitches, r.whiffs, r.swings, r.ff_velo_n, r.ff_velo_sum, r.ff_spin_n, r.ff_spin_sum
FROM pa_rollup r
JOIN pitches f ON f.game_pk = r.game_pk AND f.at_bat_number = r.at_bat_number
              AND f.pitch_number = r.first_p
JOIN pitches l ON l.game_pk = r.game_pk AND l.at_bat_number = r.at_bat_number
              AND l.pitch_number = r.last_p;

-- The starter is whoever pitched to the first batter of each half.
CREATE TEMP TABLE starters AS
SELECT game_pk, inning_topbot, pitcher AS starter
FROM (SELECT game_pk, inning_topbot, pitcher,
             ROW_NUMBER() OVER (PARTITION BY game_pk, inning_topbot
                                ORDER BY at_bat_number) AS rn
      FROM pa_base)
WHERE rn = 1;

-- Times through the order: the nth time this pitcher has faced this batter in this game.
CREATE TABLE plate_appearances AS
SELECT *, MIN(tto_raw, 4) AS tto
FROM (SELECT p.*,
             CASE WHEN p.pitcher = s.starter THEN 1 ELSE 0 END AS is_starter,
             ROW_NUMBER() OVER (PARTITION BY p.game_pk, p.pitcher, p.batter
                                ORDER BY p.at_bat_number) AS tto_raw
      FROM pa_base p
      JOIN starters s ON s.game_pk = p.game_pk AND s.inning_topbot = p.inning_topbot);

CREATE INDEX idx_pa_game ON plate_appearances (game_pk, at_bat_number);
DROP TABLE pa_rollup; DROP TABLE pa_base; DROP TABLE starters;
"""),

    ("pitcher_games", f"""
DROP TABLE IF EXISTS pitcher_games;
CREATE TABLE pitcher_games AS
SELECT g.*,
       ROUND(ff_velo_sum / NULLIF(ff_velo_n, 0), 1) AS ff_velo,
       ROUND(ff_spin_sum / NULLIF(ff_spin_n, 0), 0) AS ff_spin,
       ROUND(1.0 * whiffs / NULLIF(swings, 0), 3)   AS whiff_pct,
       ROUND(xwoba_sum / NULLIF(pa, 0), 3)          AS xwoba,
       -- days off since his previous appearance that season (0 = pitched the day before)
       MAX(CAST(julianday(game_date) - julianday(
               LAG(game_date) OVER (PARTITION BY pitcher, season
                                    ORDER BY game_date, game_pk)) AS INTEGER) - 1, 0) AS days_rest
FROM (SELECT game_pk, MIN(game_date) AS game_date, MIN(season) AS season,
             MIN(game_type) AS game_type, pitcher, MAX(pitcher_name) AS pitcher_name,
             MAX(p_throws) AS throws, MAX({TEAM}) AS team, MAX({OPPONENT}) AS opponent,
             MAX(is_starter) AS is_starter, MIN(inning) AS first_inning,
             COUNT(*) AS batters_faced, SUM(n_pitches) AS pitches,
             SUM(runs_scored) AS runs_while_pitching,
             SUM(ff_velo_n) AS ff_velo_n, SUM(ff_velo_sum) AS ff_velo_sum,
             SUM(ff_spin_n) AS ff_spin_n, SUM(ff_spin_sum) AS ff_spin_sum,
             {METRIC_SUMS}
      FROM plate_appearances
      GROUP BY game_pk, pitcher) g;
CREATE INDEX idx_pg_pitcher ON pitcher_games (pitcher, game_date);
"""),

    ("starter_games", f"""
DROP TABLE IF EXISTS starter_games;
CREATE TABLE starter_games AS
SELECT s.*,
       ROUND(1.0 * early_k / NULLIF(early_pa, 0), 3)       AS early_k_pct,
       ROUND(early_woba_sum / NULLIF(early_pa, 0), 3)      AS early_woba,
       ROUND(1.0 * early_whiffs / NULLIF(early_swings, 0), 3) AS early_whiff_pct,
       ROUND(third_woba_sum / NULLIF(third_pa, 0), 3)      AS third_woba
FROM (SELECT game_pk, MIN(game_date) AS game_date, MIN(season) AS season,
             MIN(game_type) AS game_type, pitcher, MAX(pitcher_name) AS pitcher_name,
             MAX(p_throws) AS throws, MAX({TEAM}) AS team, MAX({OPPONENT}) AS opponent,
             SUM(n_pitches) AS pitches,
             -- first two times through the order: what the manager knows when deciding
             SUM(CASE WHEN tto <= 2 AND woba_denom = 1 THEN 1 ELSE 0 END) AS early_pa,
             SUM(CASE WHEN tto <= 2 AND woba_denom = 1 AND events IN ({K}) THEN 1 ELSE 0 END) AS early_k,
             SUM(CASE WHEN tto <= 2 AND woba_denom = 1 THEN woba_value ELSE 0 END) AS early_woba_sum,
             SUM(CASE WHEN tto <= 2 THEN COALESCE(xwoba, 0) ELSE 0 END) AS early_xwoba_sum,
             SUM(CASE WHEN tto <= 2 THEN n_pitches ELSE 0 END) AS early_pitches,
             SUM(CASE WHEN tto <= 2 THEN whiffs ELSE 0 END) AS early_whiffs,
             SUM(CASE WHEN tto <= 2 THEN swings ELSE 0 END) AS early_swings,
             -- third time through: what happened if he stayed in
             SUM(CASE WHEN tto = 3 AND woba_denom = 1 THEN 1 ELSE 0 END) AS third_pa,
             SUM(CASE WHEN tto = 3 AND woba_denom = 1 AND events IN ({K}) THEN 1 ELSE 0 END) AS third_k,
             SUM(CASE WHEN tto = 3 AND woba_denom = 1 THEN woba_value ELSE 0 END) AS third_woba_sum,
             SUM(CASE WHEN tto = 3 AND woba_denom = 1 THEN woba_value * woba_value ELSE 0 END) AS third_woba_sq,
             SUM(CASE WHEN tto = 3 THEN COALESCE(xwoba, 0) ELSE 0 END) AS third_xwoba_sum
      FROM plate_appearances
      WHERE is_starter = 1
      GROUP BY game_pk, pitcher) s;
CREATE INDEX idx_sg_pitcher ON starter_games (pitcher);
"""),

    ("splits and baselines", f"""
DROP TABLE IF EXISTS pitcher_splits;
CREATE TABLE pitcher_splits AS
SELECT *, {METRIC_RATES}
FROM (SELECT pitcher, MAX(pitcher_name) AS pitcher_name, MAX(p_throws) AS throws,
             stand AS batter_side, {METRIC_SUMS}
      FROM plate_appearances WHERE game_type = 'R'
      GROUP BY pitcher, stand);

DROP TABLE IF EXISTS batter_splits;
CREATE TABLE batter_splits AS
SELECT *, {METRIC_RATES}
FROM (SELECT batter, MAX(stand) AS bats, p_throws AS pitcher_hand, {METRIC_SUMS}
      FROM plate_appearances WHERE game_type = 'R'
      GROUP BY batter, p_throws);

DROP TABLE IF EXISTS league_platoon;
CREATE TABLE league_platoon AS
SELECT *, {METRIC_RATES}
FROM (SELECT stand AS batter_side, p_throws AS pitcher_hand, {METRIC_SUMS}
      FROM plate_appearances WHERE game_type = 'R'
      GROUP BY stand, p_throws);

DROP TABLE IF EXISTS league_tto;
CREATE TABLE league_tto AS
SELECT *, {METRIC_RATES}
FROM (SELECT season, tto, {METRIC_SUMS}
      FROM plate_appearances WHERE game_type = 'R' AND is_starter = 1
      GROUP BY season, tto);

DROP TABLE IF EXISTS league_reliever;
CREATE TABLE league_reliever AS
SELECT *, {METRIC_RATES}
FROM (SELECT season, {METRIC_SUMS}
      FROM plate_appearances WHERE game_type = 'R' AND is_starter = 0
      GROUP BY season);
"""),

    ("leverage_by_state", """
DROP TABLE IF EXISTS leverage_by_state;
CREATE TABLE leverage_by_state AS
WITH s AS (
    SELECT inning, inning_topbot AS half, outs_when_up AS outs,
           CAST(on_1b AS TEXT) || CAST(on_2b AS TEXT) || CAST(on_3b AS TEXT) AS bases,
           MAX(-4, MIN(4, bat_score - fld_score)) AS diff,
           ABS(delta_home_win_exp) AS abs_dwp
    FROM plate_appearances
    WHERE game_type = 'R' AND inning <= 9 AND delta_home_win_exp IS NOT NULL
)
SELECT inning, half, outs, bases, diff,
       COUNT(*) AS n_pa,
       ROUND(AVG(abs_dwp) / (SELECT AVG(abs_dwp) FROM s), 3) AS li
FROM s
GROUP BY inning, half, outs, bases, diff;
"""),
]


def flip_name(name) -> str:
    """'Snell, Blake' -> 'Blake Snell'"""
    if not isinstance(name, str) or ", " not in name:
        return name
    last, first = name.split(", ", 1)
    return f"{first} {last}"


def build_players(con):
    pitchers = pd.read_sql("SELECT pitcher AS player_id, MAX(player_name) AS statcast_name "
                           "FROM pitches GROUP BY pitcher", con)
    pitchers["name"] = pitchers["statcast_name"].map(flip_name)
    batters = pd.read_sql("SELECT DISTINCT batter AS player_id FROM plate_appearances", con)
    ids = sorted(set(batters["player_id"]) - set(pitchers["player_id"]))
    names = {}
    if ids:
        try:
            lk = playerid_reverse_lookup(ids, key_type="mlbam")
            names = {int(r.key_mlbam): f"{r.name_first} {r.name_last}".title()
                     for r in lk.itertuples()}
        except Exception as exc:                        # name lookup is a nice-to-have
            print(f"  (hitter name lookup failed: {exc}; those hitters will show as IDs)")
    hitters = pd.DataFrame({"player_id": ids,
                            "name": [names.get(i, f"ID {i}") for i in ids]})
    players = pd.concat([pitchers[["player_id", "name"]], hitters], ignore_index=True)
    con.execute("DROP TABLE IF EXISTS players")
    con.execute("CREATE TABLE players (player_id INTEGER PRIMARY KEY, name TEXT)")
    players.to_sql("players", con, if_exists="append", index=False)
    con.commit()


def build_tables(con):
    print("\nStep 2. Building tables with SQL ...")
    for label, sql in BUILD_STEPS:
        t = time.time()
        con.executescript(sql)
        print(f"  {label:<22} {time.time() - t:6.1f}s")
    t = time.time()
    build_players(con)
    print(f"  {'players':<22} {time.time() - t:6.1f}s")


# ---------------------------------------------------------------- Step 3: checks

def one(con, sql, params=()):
    return con.execute(sql, params).fetchone()


def run_checks(con) -> bool:
    print("\nStep 3. Validation checks")
    results = []

    def add(name, status, detail):
        results.append((name, status, detail))

    # 1. Each pitch appears once.
    dupes = one(con, """SELECT COUNT(*) FROM (SELECT 1 FROM pitches
                        GROUP BY game_pk, at_bat_number, pitch_number HAVING COUNT(*) > 1)""")[0]
    add("Each pitch appears once", "PASS" if dupes == 0 else "FAIL", f"{dupes} duplicated pitches")

    # 2. Each plate appearance has at most one result.
    multi = one(con, """SELECT COUNT(*) FROM (SELECT 1 FROM pitches WHERE events IS NOT NULL
                        GROUP BY game_pk, at_bat_number HAVING COUNT(*) > 1)""")[0]
    add("Each PA has one result", "PASS" if multi == 0 else "WARN", f"{multi} PAs with 2+ results")

    # 3. Every game has two starters (one per team).
    odd = one(con, """SELECT COUNT(*) FROM (SELECT game_pk FROM starter_games
                      GROUP BY game_pk HAVING COUNT(*) != 2)""")[0]
    add("Every game has two starters", "PASS" if odd == 0 else "WARN", f"{odd} games without exactly 2")

    # 4. Regular-season game counts look complete.
    counts = dict(con.execute("""SELECT season, COUNT(DISTINCT game_pk) FROM plate_appearances
                                 WHERE game_type = 'R' GROUP BY season""").fetchall())
    off = {y: counts.get(y, 0) for y, n in GAMES_PER_SEASON.items() if abs(counts.get(y, 0) - n) > 10}
    detail = ", ".join(f"{y}: {counts.get(y, 0)} (expected ~{n})" for y, n in GAMES_PER_SEASON.items())
    add("Regular-season games complete", "PASS" if not off else "WARN", detail)

    # 5. Times through the order is between 1 and 4.
    bad = one(con, "SELECT COUNT(*) FROM plate_appearances WHERE tto NOT BETWEEN 1 AND 4")[0]
    add("tto is between 1 and 4", "PASS" if bad == 0 else "FAIL", f"{bad} bad rows")

    # 6. Exit velocity is recorded on (almost) every ball in play.
    ip, bb = one(con, "SELECT SUM(in_play), SUM(batted_ball) FROM plate_appearances")
    share = bb / ip if ip else 0
    add("Exit velocity on balls in play", "PASS" if share >= 0.97 else "WARN", f"{share:.1%} recorded")

    # 7. Leverage index averages 1.0 across all plate appearances (true by definition).
    avg_li = one(con, "SELECT SUM(li * n_pa) / SUM(n_pa) FROM leverage_by_state")[0] or 0
    add("Leverage averages 1.0", "PASS" if abs(avg_li - 1) < 0.01 else "FAIL", f"weighted mean {avg_li:.3f}")

    # 8. Known facts: Snell's line in 2020 World Series Game 6.
    f = GAME6_SNELL
    row = one(con, """SELECT pitches, batters_faced, k, h, bb FROM pitcher_games
                      WHERE game_date = ? AND pitcher_name = ?""", (f["date"], f["name"]))
    expected = (f["pitches"], f["batters_faced"], f["k"], f["h"], f["bb"])
    if row is None:
        add("Snell, WS Game 6 box score", "FAIL", "game not found")
    else:
        add("Snell, WS Game 6 box score", "PASS" if tuple(row) == expected else "FAIL",
            f"pitches/BF/K/H/BB = {'/'.join(map(str, row))} (box score {'/'.join(map(str, expected))})")

    # 9. Win probability chains from one plate appearance to the next (postseason games).
    err = one(con, """SELECT AVG(ABS(home_win_exp + delta_home_win_exp - next_wp)) FROM (
                        SELECT home_win_exp, delta_home_win_exp,
                               LEAD(home_win_exp) OVER (PARTITION BY game_pk ORDER BY at_bat_number) AS next_wp
                        FROM plate_appearances WHERE game_type != 'R')
                      WHERE next_wp IS NOT NULL AND delta_home_win_exp IS NOT NULL""")[0]
    if err is None:
        add("Win probability chains PA to PA", "WARN", "no win-probability data")
    else:
        add("Win probability chains PA to PA", "PASS" if err < 0.002 else "WARN", f"mean gap {err:.4f}")

    # 10. The SQL reproduces the pandas results from snell_vs_peers.py.
    path = OUTPUT / "snell_like_starts.csv"
    if not path.exists():
        add("SQL matches pandas (snell_vs_peers)", "SKIP", f"{path} not found")
    else:
        pandas_rows = pd.read_csv(path, index_col=0)
        groups = {
            "All starts that completed 2x through": "1 = 1",
            "Snell-like and <= 85 pitches": ("early_k * 1.0 / early_pa >= 0.35 "
                                             "AND early_woba_sum / early_pa <= 0.200 "
                                             "AND early_pitches <= 85"),
        }
        details, ok = [], True
        for label, cond in groups.items():
            match = pandas_rows[pandas_rows.index.str.startswith(label.split(" (")[0])]
            if match.empty:
                continue
            p_starts, p_woba = int(match["starts"].iloc[0]), float(match["3rd_wOBA"].iloc[0])
            s_starts, s_woba = one(con, f"""SELECT COUNT(*), SUM(third_woba_sum) / SUM(third_pa)
                                            FROM starter_games
                                            WHERE game_type = 'R' AND early_pa >= 18 AND {cond}""")
            ok &= abs(s_starts - p_starts) <= 0.01 * p_starts and abs((s_woba or 0) - p_woba) <= 0.003
            details.append(f"{label}: {s_starts} starts / {s_woba:.3f} (pandas {p_starts} / {p_woba:.3f})")
        add("SQL matches pandas (snell_vs_peers)", "PASS" if ok else "FAIL", "; ".join(details))

    width = max(len(r[0]) for r in results)
    for name, status, detail in results:
        print(f"  [{status}] {name:<{width}}  {detail}")
    fails = sum(r[1] == "FAIL" for r in results)
    print(f"  {fails} failed, {sum(r[1] == 'WARN' for r in results)} warnings")
    return fails == 0


# ---------------------------------------------------------------- Step 4: site database

SITE_TABLES = {
    "starter_games": ("SELECT * FROM starter_games",
                      "One row per start. early_* = first two times through the order (what the "
                      "manager knows when deciding); third_* = how the third time through went. "
                      "Manager page: 'should I pull him?'"),
    "pitcher_games": ("SELECT * FROM pitcher_games",
                      "One row per pitcher per game: pitches, results, four-seam velocity and spin, "
                      "whiff rate, days of rest. Bullpen board and pitching coach page."),
    "pitcher_splits": ("SELECT * FROM pitcher_splits",
                       "Each pitcher vs. left- and right-handed batters, 2018-2020 regular seasons."),
    "batter_splits": ("SELECT * FROM batter_splits",
                      "Each hitter vs. left- and right-handed pitchers, 2018-2020 regular seasons."),
    "league_platoon": ("SELECT * FROM league_platoon",
                       "League averages for each batter-side / pitcher-hand matchup."),
    "league_tto": ("SELECT * FROM league_tto",
                   "League starters by season and time through the order."),
    "league_reliever": ("SELECT * FROM league_reliever", "League relievers by season."),
    "leverage_by_state": ("SELECT * FROM leverage_by_state",
                          "Leverage index (LI) by inning, half, outs, runners (1st-2nd-3rd) and the "
                          "batting team's score margin (capped at +/-4). 1.0 = average, 2.0+ = high."),
    "postseason_pa": ("""SELECT game_pk, game_date, game_type, home_team, away_team, inning,
                                inning_topbot, at_bat_number, pitcher, batter, stand, p_throws,
                                outs_when_up, on_1b, on_2b, on_3b, bat_score, fld_score, runs_scored,
                                events, woba_value, xwoba, tto, is_starter,
                                home_win_exp, delta_home_win_exp
                         FROM plate_appearances WHERE game_type != 'R'""",
                      "Every 2020 postseason plate appearance. home_win_exp = home team's win "
                      "probability before the PA; delta_home_win_exp = change over the whole PA. "
                      "Game pages."),
    "players": ("SELECT * FROM players", "Player id -> name."),
}
SITE_INDEXES = [
    "CREATE INDEX site.idx_sg_pitcher ON starter_games (pitcher)",
    "CREATE INDEX site.idx_pg_pitcher ON pitcher_games (pitcher, game_date)",
    "CREATE INDEX site.idx_ps_pitcher ON pitcher_splits (pitcher)",
    "CREATE INDEX site.idx_bs_batter ON batter_splits (batter)",
    "CREATE INDEX site.idx_post_game ON postseason_pa (game_pk, at_bat_number)",
]


def export_site(con):
    print(f"\nStep 4. Exporting the site database to {SITE_DB} ...")
    SITE_DB.parent.mkdir(parents=True, exist_ok=True)
    if SITE_DB.exists():
        SITE_DB.unlink()
    con.execute("ATTACH DATABASE ? AS site", (str(SITE_DB),))
    for name, (sql, _) in SITE_TABLES.items():
        con.execute(f"CREATE TABLE site.{name} AS {sql}")
    for sql in SITE_INDEXES:
        con.execute(sql)
    con.commit()
    con.execute("DETACH DATABASE site")

    site = sqlite3.connect(SITE_DB)
    site.execute("VACUUM")
    lines = ["# Data dictionary: site_data/coaches.db", "",
             "Built by build_database.py from Statcast (2018-2020 regular seasons, 2020 postseason).",
             "Counting columns ending in _sum are totals; divide by pa (or the matching count) "
             "to get a rate, so any group of rows can be added up correctly.", ""]
    for name, (_, desc) in SITE_TABLES.items():
        n = site.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
        cols = [r[1] for r in site.execute(f"PRAGMA table_info({name})")]
        print(f"  {name:<18} {n:>9,} rows")
        lines += [f"## {name} ({n:,} rows)", "", desc, "", "Columns: " + ", ".join(cols), ""]
    site.close()
    (SITE_DB.parent / "DATA_DICTIONARY.md").write_text("\n".join(lines))
    print(f"  size: {SITE_DB.stat().st_size / 1e6:.1f} MB  (data dictionary: "
          f"{SITE_DB.parent / 'DATA_DICTIONARY.md'})")


# ---------------------------------------------------------------- main

def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("--skip-load", action="store_true",
                        help="reuse the pitches already in the warehouse")
    args = parser.parse_args()

    start = time.time()
    WAREHOUSE.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(WAREHOUSE)
    if args.skip_load:
        if not one(con, "SELECT name FROM sqlite_master WHERE name = 'pitches'"):
            raise SystemExit("No pitches table yet: run without --skip-load first.")
        print("Step 1. Skipped (reusing pitches already in the warehouse)")
    else:
        load_pitches(con)
    build_tables(con)
    ok = run_checks(con)
    export_site(con)
    con.close()

    print(f"\nDone in {(time.time() - start) / 60:.1f} min.")
    print(f"Warehouse: {WAREHOUSE} ({WAREHOUSE.stat().st_size / 1e6:.0f} MB). "
          "Add data/ to .gitignore; commit site_data/ instead.")
    if not ok:
        print("Some checks FAILED: look at them before building pages on this data.")


if __name__ == "__main__":
    main()
