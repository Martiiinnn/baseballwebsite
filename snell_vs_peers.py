"""
Was Blake Snell's 2020 World Series Game 6 start "special" enough to leave him in?

Part A: Snell in Game 6 vs. Snell's 2020 regular season, split by times through
        the order (wOBA, K%, whiff rate, four-seam velocity).
Part B: "Snell-like" starts. Take every regular-season start where the starter
        completed two times through the order (18+ PA), group the starts by how
        dominant he was over those first two times, then see how hitters did the
        THIRD time through.

The groups are built only from what happened BEFORE the third time through,
i.e. the information Kevin Cash had when he made the call. Grouping by what
happened afterwards would bake hindsight into the answer.

Setup:  pip install pybaseball pandas
Run:    python snell_vs_peers.py
Output: tables in the terminal + CSVs in ./output/

The first run downloads three seasons of Statcast data (roughly 10 minutes for
the default 2018-2020). Later runs read from pybaseball's cache.
"""
from pathlib import Path

import numpy as np
import pandas as pd
from pybaseball import cache, statcast, statcast_pitcher

cache.enable()

GAME_DATE = "2020-10-27"                       # 2020 World Series Game 6
SNELL_NAME = "Snell, Blake"
SNELL_SEASON = ("2020-07-23", "2020-09-27")    # Snell's 2020 regular season

# Seasons used to find comparable starts (same era as the 2020 World Series).
SEASONS = {
    2018: ("2018-03-29", "2018-10-01"),
    2019: ("2019-03-20", "2019-09-29"),
    2020: ("2020-07-23", "2020-09-27"),
}

# Definition of a "Snell-like" start, measured over the first two times through.
# Adjust these after you see Snell's own Game 6 numbers in Part A.
SNELL_LIKE_MIN_K = 0.35        # strikeout rate at least 35%
SNELL_LIKE_MAX_WOBA = 0.200    # wOBA allowed at most .200
SNELL_LIKE_MAX_PITCHES = 85    # extra filter: pitch count still low at that point

OUT = Path("output")

KEEP = ["game_pk", "game_date", "game_type", "pitcher", "player_name", "batter",
        "at_bat_number", "pitch_number", "inning_topbot", "events", "description",
        "pitch_type", "release_speed", "woba_value", "woba_denom"]

STRIKEOUTS = {"strikeout", "strikeout_double_play"}
WHIFFS = {"swinging_strike", "swinging_strike_blocked", "missed_bunt"}
SWINGS = WHIFFS | {"foul", "foul_tip", "foul_bunt", "hit_into_play",
                   "hit_into_play_no_out", "hit_into_play_score"}


# ---------------------------------------------------------------- helpers

def add_tto(df: pd.DataFrame) -> pd.DataFrame:
    """Add 'tto': the nth time this batter has faced this pitcher in this game (4 = 4+)."""
    pa = (df[["game_pk", "pitcher", "batter", "at_bat_number"]]
          .drop_duplicates()
          .sort_values(["game_pk", "pitcher", "at_bat_number"]))
    pa["tto"] = (pa.groupby(["game_pk", "pitcher", "batter"]).cumcount() + 1).clip(upper=4)
    return df.merge(pa, on=["game_pk", "pitcher", "batter", "at_bat_number"], how="left")


def starters_only(df: pd.DataFrame) -> pd.DataFrame:
    """Keep only pitches thrown by each team's starting pitcher."""
    first = (df.sort_values(["game_pk", "at_bat_number", "pitch_number"])
               .groupby(["game_pk", "inning_topbot"])["pitcher"].first()
               .rename("starter").reset_index())
    df = df.merge(first, on=["game_pk", "inning_topbot"])
    return df[df["pitcher"] == df["starter"]].drop(columns="starter")


def tto_label(t) -> str:
    return "4+" if t >= 4 else str(int(t))


def pa_metrics(df: pd.DataFrame) -> dict:
    pa = df[df["woba_denom"] == 1]
    n = len(pa)
    return {
        "PA": n,
        "wOBA": round(pa["woba_value"].sum() / n, 3) if n else np.nan,
        "K%": round(pa["events"].isin(STRIKEOUTS).mean(), 3) if n else np.nan,
    }


def pitch_metrics(df: pd.DataFrame) -> dict:
    swings = df["description"].isin(SWINGS).sum()
    ff = df.loc[df["pitch_type"] == "FF", "release_speed"]
    return {
        "pitches": len(df),
        "whiff%": round(df["description"].isin(WHIFFS).sum() / swings, 3) if swings else np.nan,
        "4-seam_velo": round(ff.mean(), 1) if len(ff) else np.nan,
    }


def start_summaries(df: pd.DataFrame) -> pd.DataFrame:
    """One row per (game, starter): totals over the first two times through and the third."""
    pa = df["woba_denom"] == 1
    early, third = df["tto"] <= 2, df["tto"] == 3
    k = df["events"].isin(STRIKEOUTS)
    wv, wd = df["woba_value"].fillna(0), df["woba_denom"].fillna(0)
    parts = pd.DataFrame({
        "game_pk": df["game_pk"], "pitcher": df["pitcher"],
        "early_PA": early & pa,
        "early_K": early & pa & k,
        # Only plate appearances that count toward wOBA (woba_denom = 1). Intentional walks,
        # sacrifice bunts and a few unscored events carry a woba_value but no denominator.
        "early_woba_num": wv.where(early & pa, 0),
        "early_woba_den": wd.where(early & pa, 0),
        "early_pitches": early,
        "early_whiffs": early & df["description"].isin(WHIFFS),
        "early_swings": early & df["description"].isin(SWINGS),
        "third_PA": third & pa,
        "third_K": third & pa & k,
        "third_woba_num": wv.where(third & pa, 0),
        "third_woba_sq": (wv ** 2).where(third & pa, 0),
    })
    totals = parts.groupby(["game_pk", "pitcher"]).sum().reset_index()
    meta = (df.groupby(["game_pk", "pitcher"])
              .agg(game_date=("game_date", "first"), player_name=("player_name", "first"))
              .reset_index())
    out = meta.merge(totals, on=["game_pk", "pitcher"])
    out["early_K%"] = out["early_K"] / out["early_PA"]
    out["early_wOBA"] = out["early_woba_num"] / out["early_woba_den"]
    out["early_whiff%"] = out["early_whiffs"] / out["early_swings"]
    return out


def summarize(starts: pd.DataFrame) -> dict:
    """Pooled numbers for a group of starts."""
    if starts.empty:
        return {"starts": 0}
    reached = starts[starts["third_PA"] > 0]
    pa3 = int(reached["third_PA"].sum())
    row = {
        "starts": len(starts),
        "reached_3rd": f"{len(reached) / len(starts):.0%}",
        "early_wOBA": round(starts["early_woba_num"].sum() / starts["early_woba_den"].sum(), 3),
        "early_K%": round(starts["early_K"].sum() / starts["early_PA"].sum(), 3),
        "3rd_PA": pa3,
    }
    if pa3:
        w3 = reached["third_woba_num"].sum() / pa3
        sd = np.sqrt(max(reached["third_woba_sq"].sum() / pa3 - w3 ** 2, 0))
        row.update({
            "3rd_wOBA": round(w3, 3),
            "3rd_wOBA_95%": f"+/-{1.96 * sd / np.sqrt(pa3):.3f}",
            "3rd_K%": round(reached["third_K"].sum() / pa3, 3),
        })
    return row


def month_chunks(start: str, end: str):
    """Split a date range by month so a full season never sits in memory at once."""
    s, e = pd.Timestamp(start), pd.Timestamp(end)
    while s <= e:
        m_end = min(s + pd.offsets.MonthEnd(0), e)
        yield s.strftime("%Y-%m-%d"), m_end.strftime("%Y-%m-%d")
        s = m_end + pd.Timedelta(days=1)


# ---------------------------------------------------------------- Part A

def part_a():
    print("\n=== Part A. Snell: Game 6 vs. his 2020 regular season ===")
    game = statcast(GAME_DATE, GAME_DATE)
    game = game[game["game_type"] == "W"]
    snell_g = add_tto(game[game["player_name"] == SNELL_NAME])
    if snell_g.empty:
        raise SystemExit("Couldn't find Snell in the Game 6 data.")
    snell_id = int(snell_g["pitcher"].iloc[0])

    season = statcast_pitcher(*SNELL_SEASON, snell_id)
    season = add_tto(season[season["game_type"] == "R"])

    rows = []
    for label, d in [("Game 6", snell_g), ("2020 season", season)]:
        for t, g in d.groupby("tto"):
            rows.append({"when": label, "times_through": tto_label(t),
                         **pa_metrics(g), **pitch_metrics(g)})
        rows.append({"when": label, "times_through": "all",
                     **pa_metrics(d), **pitch_metrics(d)})
    table = pd.DataFrame(rows)
    print(table.to_string(index=False))
    table.to_csv(OUT / "snell_game6_vs_season.csv", index=False)

    g6 = start_summaries(snell_g).iloc[0]
    print(f"\nGame 6, first two times through: {int(g6['early_PA'])} PA, "
          f"K% {g6['early_K%']:.3f}, wOBA {g6['early_wOBA']:.3f}, "
          f"whiff% {g6['early_whiff%']:.3f}, {int(g6['early_pitches'])} pitches")
    if g6["third_PA"] == 0:
        print("He was pulled before facing anyone a third time.")
    return g6


# ---------------------------------------------------------------- Part B

def collect_starts() -> pd.DataFrame:
    frames = []
    for year, (s, e) in SEASONS.items():
        for ms, me in month_chunks(s, e):
            print(f"  pulling {ms} to {me} ...")
            df = statcast(ms, me)
            if df is None or df.empty:
                continue
            df = df[df["game_type"] == "R"][KEEP]
            if df.empty:
                continue
            frames.append(start_summaries(add_tto(starters_only(df))).assign(season=year))
    starts = pd.concat(frames, ignore_index=True)
    starts.to_csv(OUT / "starter_games.csv", index=False)
    return starts


def part_b(g6):
    print(f"\n=== Part B. Comparable starts, {min(SEASONS)}-{max(SEASONS)} regular seasons ===")
    starts = collect_starts()
    done2 = starts[starts["early_PA"] >= 18].copy()   # completed two times through
    print(f"\n{len(starts)} starts in total, {len(done2)} completed two times through the order.")

    done2["early_K%_group"] = pd.cut(done2["early_K%"], [0, .15, .25, .35, 1.01], right=False,
                                     labels=["<15%", "15-25%", "25-35%", "35%+"])
    done2["early_wOBA_group"] = pd.cut(done2["early_wOBA"], [0, .2, .275, .35, 5], right=False,
                                       labels=["<.200", ".200-.275", ".275-.350", ".350+"])

    tables = {}
    for col, title in [("early_K%_group", "Grouped by strikeout rate over the first two times through"),
                       ("early_wOBA_group", "Grouped by wOBA allowed over the first two times through")]:
        t = pd.DataFrame({grp: summarize(sub) for grp, sub in done2.groupby(col, observed=True)}).T
        t.index.name = col
        print(f"\n{title}:")
        print(t.to_string())
        tables[col] = t

    like = done2[(done2["early_K%"] >= SNELL_LIKE_MIN_K) & (done2["early_wOBA"] <= SNELL_LIKE_MAX_WOBA)]
    fresh = like[like["early_pitches"] <= SNELL_LIKE_MAX_PITCHES]
    compare = pd.DataFrame({
        "All starts that completed 2x through": summarize(done2),
        f"Snell-like (K% >= {SNELL_LIKE_MIN_K:.0%}, wOBA <= {SNELL_LIKE_MAX_WOBA:.3f})": summarize(like),
        f"Snell-like and <= {SNELL_LIKE_MAX_PITCHES} pitches": summarize(fresh),
    }).T
    print("\nHow did hitters do the third time through?")
    print(compare.to_string())
    print(f"\nFor reference, Snell in Game 6 before being pulled: K% {g6['early_K%']:.3f}, "
          f"wOBA {g6['early_wOBA']:.3f}, {int(g6['early_pitches'])} pitches.")

    pd.concat(tables.values(), keys=tables.keys()).to_csv(OUT / "third_time_by_early_group.csv")
    compare.to_csv(OUT / "snell_like_starts.csv")
    like.sort_values("game_date").to_csv(OUT / "snell_like_start_list.csv", index=False)

    print("\nHow to read this:")
    print(" - early_wOBA vs 3rd_wOBA: how much a dominant start fades. Some of the fade is")
    print("   regression to the mean (early dominance is partly luck), some is hitters adjusting.")
    print(" - 3rd_wOBA_95%: rough uncertainty band. Overlapping bands = can't tell the groups apart.")
    print(" - reached_3rd: share of starts where the manager let him face a 3rd time.")
    print("   Low shares mean the 3rd-time numbers come from a hand-picked subset.")
    print(" - Not adjusted for who was batting (Snell was about to face Betts and Seager).")


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    g6 = part_a()
    part_b(g6)
