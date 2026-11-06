"""
Pull or Stay: a decision tool for the coaching staff.

Pages (named in the dugout's own words)
  /game-6        Game 6 Review   the 2020 World Series Game 6 case study
  /hook          The Hook        pull the starter or not: third time through vs. a reliever
  /mound-visit   Mound Visit     is today's starter's stuff really better than usual?
  /bullpen       Bullpen Board   who in the bullpen is rested and throwing well?
Old addresses (/, /manager, /pitching-coach) redirect to the new ones.

Run locally:   python3 app.py        then open http://127.0.0.1:5001
Data:          site_data/coaches.db  (built by build_database.py)
"""
from datetime import date

from flask import Flask, g, redirect, render_template, request, url_for

import charts
import mlb
import queries as q

# Static files live in public/static so they work both locally and on Vercel,
# which serves anything under public/ from its CDN (and ignores Flask's static folder).
app = Flask(__name__, static_folder="public/static", static_url_path="/static")

# Team colours, logos, headshots and video links, usable in every template and macro.
app.jinja_env.globals.update(team_color=mlb.team_color, logo=mlb.logo,
                             headshot=mlb.headshot, game_film=mlb.game_film)

DEFAULT_TEAM = "TB"
DEFAULT_DATE = "2020-10-27"        # 2020 World Series Game 6
CASE_RELIEVER = 623433             # Nick Anderson
CASE_PULL_AB = 43                  # first batter after Snell left (Mookie Betts)


def db():
    if "con" not in g:
        g.con = q.connect()
    return g.con


@app.teardown_appcontext
def close_db(exc):
    con = g.pop("con", None)
    if con is not None:
        con.close()


def arg(name, default, cast=str, lo=None, hi=None):
    """Read a query-string value, falling back to the default if it's missing or invalid."""
    raw = request.args.get(name)
    if raw in (None, ""):
        return default
    try:
        value = cast(raw)
    except ValueError:
        return default
    if lo is not None and value < lo:
        return lo
    if hi is not None and value > hi:
        return hi
    return value


def team_and_date():
    con = db()
    teams = q.teams(con)
    first, last = q.date_range(con)
    team = arg("team", DEFAULT_TEAM)
    if team not in teams:
        team = DEFAULT_TEAM
    day = arg("date", DEFAULT_DATE)
    if not (first <= day <= last) or len(day) != 10:
        day = DEFAULT_DATE
    return team, day, teams, (first, last)


@app.context_processor
def helpers():
    def pct(v, digits=0):
        return "—" if v is None else f"{v * 100:.{digits}f}%"

    def woba(v):
        return "—" if v is None else f"{v:.3f}".lstrip("0")

    def num(v, digits=1):
        return "—" if v is None else f"{v:.{digits}f}"

    def signed(v, digits=1):
        return "—" if v is None else f"{v:+.{digits}f}".replace("-", "−")

    def date_label(v):
        """'2020-10-27' -> 'Oct 27, 2020'"""
        d = date.fromisoformat(str(v)[:10])
        return f"{d:%b} {d.day}, {d.year}"

    return {"pct": pct, "woba": woba, "num": num, "signed": signed, "date_label": date_label}


# ---------------------------------------------------------------- pages

@app.route("/game-6")
def game6_review():
    con = db()
    game = q.find_postseason_game(con, DEFAULT_DATE, DEFAULT_TEAM)
    wp = q.win_probability(con, game["game_pk"], DEFAULT_TEAM)
    pull = next(r for r in wp if r["ab"] == CASE_PULL_AB)
    inning_end = [r for r in wp if r["inning"] == pull["inning"] and r["half"] == pull["half"]][-1]
    swings = sorted(wp, key=lambda r: abs(r["change"]), reverse=True)[:5]

    card = q.starter_card(con, DEFAULT_TEAM, DEFAULT_DATE)
    lk = card["manager_link"]
    similar = q.comparable_starts(con, lk["k_min"], lk["woba_max"], lk["pitch_max"])
    everyone = q.comparable_starts(con, 0, 9, 999)
    rel = q.reliever_scenarios(con, CASE_RELIEVER, DEFAULT_DATE)
    rel_season = q.pitcher_totals(con, CASE_RELIEVER, "game_type = 'R' AND season = 2020")
    li, _ = q.leverage(con, 6, "Bot", 1, 1, 0, 0, 1)
    result = q.decision(similar["woba"], rel["scenarios"], li, 3)

    return render_template(
        "overview.html", active="game6", team=DEFAULT_TEAM, game_pk=game["game_pk"],
        rel_id=CASE_RELIEVER,
        chart=charts.win_prob_svg(wp, DEFAULT_TEAM, CASE_PULL_AB, "Snell pulled", height=380),
        pull=pull, inning_end=inning_end, swings=swings, final=wp[-1]["after"],
        card=card, similar=similar, everyone=everyone, rel=rel, rel_season=rel_season,
        li=li, li_label=q.leverage_label(li), result=result)


@app.route("/hook")
def hook():
    con = db()
    team, day, teams, span = team_and_date()
    submitted = "s" in request.args

    k_min = arg("k_min", 35.0, float, 0, 100) / 100
    woba_max = arg("woba_max", 0.200, float, 0, 2)
    pitch_max = arg("pitch_max", 85, int, 1, 200)
    n_batters = arg("batters", 3, int, 1, 9)
    inning = arg("inning", 6, int, 1, 9)
    half = arg("half", "Bot")
    half = half if half in ("Top", "Bot") else "Bot"
    outs = arg("outs", 1, int, 0, 2)
    lead = arg("lead", 1, int, -10, 10)
    if submitted:
        on1, on2, on3 = ("on1" in request.args, "on2" in request.args, "on3" in request.args)
    else:
        on1, on2, on3 = True, False, False     # Game 6: runner on first

    pen = q.bullpen(con, team, day)["relievers"]
    options = sorted(pen, key=lambda r: r["name"])
    rel_id = arg("reliever", CASE_RELIEVER if team == DEFAULT_TEAM else None, int)
    if rel_id not in {r["pitcher"] for r in pen}:
        rel_id = pen[0]["pitcher"] if pen else None

    starts = q.comparable_starts(con, k_min, woba_max, pitch_max)
    li, li_n = q.leverage(con, inning, half, outs, on1, on2, on3, lead)
    rel = q.reliever_scenarios(con, rel_id, day) if rel_id else None
    result = (q.decision(starts["woba"], rel["scenarios"], li, n_batters)
              if rel and starts["woba"] is not None and li is not None else None)
    rel_name = next((r["name"] for r in pen if r["pitcher"] == rel_id), None)

    return render_template(
        "manager.html", active="hook", teams=teams, span=span, team=team, day=day,
        form={"k_min": round(k_min * 100), "woba_max": woba_max, "pitch_max": pitch_max,
              "batters": n_batters, "inning": inning, "half": half, "outs": outs,
              "lead": lead, "on1": on1, "on2": on2, "on3": on3, "reliever": rel_id},
        options=options, starts=starts, li=li, li_n=li_n, li_label=q.leverage_label(li),
        rel=rel, rel_name=rel_name, result=result)


@app.route("/mound-visit")
def mound_visit():
    con = db()
    team, day, teams, span = team_and_date()
    card = q.starter_card(con, team, day)
    similar = None
    if card:
        lk = card["manager_link"]
        similar = q.comparable_starts(con, lk["k_min"], lk["woba_max"], lk["pitch_max"])
    return render_template("pitching.html", active="mound", teams=teams, span=span,
                           team=team, day=day, card=card, similar=similar)


@app.route("/bullpen")
def bullpen():
    con = db()
    team, day, teams, span = team_and_date()
    pen = q.bullpen(con, team, day)
    return render_template("bullpen.html", active="bullpen", teams=teams, span=span,
                           team=team, day=day, pen=pen)


# ---------------------------------------------------------------- old addresses

@app.route("/")
def home():
    return redirect(url_for("game6_review"))


@app.route("/manager")
def old_manager():
    return redirect(url_for("hook", **request.args.to_dict()), code=301)


@app.route("/pitching-coach")
def old_pitching_coach():
    return redirect(url_for("mound_visit", **request.args.to_dict()), code=301)


if __name__ == "__main__":
    # Port 5001 because macOS uses 5000 for AirPlay Receiver.
    app.run(debug=True, port=5001)
