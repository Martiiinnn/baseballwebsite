# Pull or Stay

A decision tool for a baseball coaching staff: **should the starting pitcher stay in, or go to the bullpen?**
It is built around one famous call: Kevin Cash pulling Blake Snell in Game 6 of the 2020 World Series.

**Live site:** https://baseballwebsite-inky.vercel.app

![Game 6 Review: the Rays' win probability through the game, with the moment Snell was pulled marked](docs/game6-review.jpg)

## The question

Bottom of the 6th, Rays up 1–0, one out, runner on first. Snell had 9 strikeouts on 73 pitches. Next up were
Mookie Betts, Corey Seager and Justin Turner, each about to face him for the third time. Cash brought in Nick Anderson,
the Dodgers scored twice that inning and won the Series.

The pull is usually judged by what happened next. This project asks a different question: **using only what the
coaching staff could have known before the pitch, what was the decision worth?** The site turns the same analysis into
tools a manager, pitching coach and bullpen coach could use for any game from 2018–2020.

## The site

Each page puts its key numbers first. Explanations sit behind ⓘ tips so coaches can read the data and draw their own conclusions.

| Page | Built for | What it shows |
|---|---|---|
| **Game 6 Review** | everyone | Rays win probability batter by batter, the numbers around the pull, and the other three pages' view of that moment |
| **The Hook** | manager | The rest of the half inning, simulated: your chance to win if you pull now, let the starter face one more hitter and decide again, or let him finish, for the reliever unchanged, changed and expected. Any team, date, reliever and game situation; postseason games use the real lineup. Lists the data each input uses |
| **Mound Visit** | pitching coach | Today's starter vs. his season (velocity, spin, whiff rate, strikeouts, xwOBA) and how starts like this go the third time through |
| **Bullpen Board** | bullpen coach | Every recent reliever, sorted by projected xwOBA, with rest, workload, velocity drops and a strikeout warning when he may have changed |

<p>
  <img src="docs/the-hook.jpg" alt="The Hook for Game 6: +1.4 if Anderson is unchanged, −1.3 if he has changed, −1.0 expected; pull now 57.6%, let Snell finish 58.5%" width="49%">
  <img src="docs/bullpen-board.jpg" alt="Bullpen Board before Game 6, with Anderson flagged: strikeouts 9 vs 20 expected, 86% he has changed" width="49%">
</p>

## What the data says about Game 6

- **Snell's stuff was real.** His four-seam fastball averaged 96.3 mph (95.0 in the 2020 regular season) and hitters missed on
  46% of their swings (32% in the season). Through 73 pitches: 18 batters, 9 K, 2 H, 0 BB.
- **Dominant starts partly regress.** 476 regular-season starts (by 158 pitchers) were about as dominant through two times through
  the order. Those starts allowed a .182 xwOBA through two times and **.295 ± .015** the third time. All 8,079 starts: .306, then .336.
  Picking starts that were dominant early also picks some luck, so the third time looks closer to average.
- **Anderson's strikeouts had disappeared.** His velocity held (95.7 mph in the postseason vs. 95.2), but he struck out
  **9 of 57** postseason hitters. Against those hitters, who strike out less than average, his history predicted **19.7**
  (2.8 standard deviations more than he got). That moves the chance his talent had changed from a 10% starting point to **86%**.
- **The rest of the inning, simulated** against the Dodgers' lineup from Mookie Betts (see *Game-state simulation* below):

  | If Anderson is… | Rays' chance to win: pull now | Snell finishes the inning | Pulling now |
  |---|---|---|---|
  | unchanged (all his games) | 59.9% | 58.5% | **+1.4 points** |
  | changed (postseason only) | 57.2% | 58.5% | **−1.3 points** |
  | expected (86% changed) | 57.6% | 58.5% | **−1.0 points** |

  Pulling comes out ahead only if the chance he has changed is below **51%**. Counting the uncertainty in both pitchers' and the
  hitters' numbers, the chance pulling was better is **26%** (10,000 draws; 90% range −3.4 to +1.8 points). **Letting Snell face Betts and
  deciding again adds nothing**, whatever you believe about Anderson, and the best possible timing adds at most 0.1 point:
  Betts, a right-handed hitter, was Anderson's best matchup, so if you were going to pull Snell, it was before Betts. A simpler three-batter formula gives +1.9 / −1.9 / −1.3.
- **What actually happened:** the Rays' win probability went from 60% to 31% over those three batters. One inning is far noisier
  than the estimate: the wOBA of three batters has a spread of about .30, against ± .015 for the starter's number. That is
  why the decision is judged before the pitch, not after.

## How the answer changed after review

A review in November 2026 challenged the first version. Each fix is reproduced by `analysis/game6_checks.py`:

| Version | Pulling Snell, win-probability points | What changed |
|---|---|---|
| First site | +1.8 / +0.8 / −4.6 for three versions of Anderson; "pull if at least 72% sure he's at full strength" | |
| Starter in xwOBA too | +0.8 / −0.2 / −5.6; pull if at least 87% sure | The starter used actual wOBA, the reliever expected wOBA. Using the same measure on both sides moved the middle case from + to − |
| Each component with its own constant | +1.8 if unchanged, −2.2 if changed | One constant (300) for overall xwOBA was replaced by separate constants estimated from the data |
| Change test on strikeouts | 98% changed, expected −2.2 | "How sure are you?" is now estimated from his strikeouts instead of asked |
| Adjusted for the hitters he faced | 86% changed, expected −1.3 | Postseason hitters strike out less, which explains part of the drop |
| Game-state simulation | +1.4 if unchanged, −1.3 if changed, expected **−1.0**; facing Betts first adds nothing; pulling better in 20% of 200 draws | The fixed conversions (÷ 1.2, ÷ 10, three batters) were replaced by simulating the rest of the inning against the real lineup |
| Editorial review (current site) | Same point estimates; pulling better in **26%** of 10,000 draws | One run of 200 draws can land anywhere from 20% to 31%, so Game 6 now uses 10,000. Added every Rays reliever and Snell's own record (see *Review checks*) |

The 72% in the first version was never the chance Anderson was healthy. It was the weight between two scenario endpoints,
and the data never measured how he felt that night. The current version replaces it with a probability estimated from his
strikeouts and checked against two seasons of relievers.

## How it's built

```
Statcast (pybaseball) ──► build_database.py ──► data/warehouse.db ──► 10 checks ──► site_data/coaches.db ──► Flask site (Vercel)
 1,739,940 pitches          SQL tables:            SQLite, 346 MB                     12.6 MB, read-only        model.py
 2018–2020 regular seasons   plate appearances,
 + 2020 postseason           starts, pitcher games,
                             splits, leverage
```

**Data.** Every pitch from the 2018–2020 regular seasons and the 2020 postseason, from Baseball Savant via `pybaseball`,
loaded into SQLite. Plate-appearance and game tables are built in SQL. For example, times through the order is
`ROW_NUMBER() OVER (PARTITION BY game_pk, pitcher, batter ORDER BY at_bat_number)`.

Both pitchers are measured in **expected wOBA (xwOBA)** using only games before the date (`model.py`).

**Starter, third time through.** Earlier regular-season starts about as dominant through two times through the order
(strikeout rate no more than 15 points lower, wOBA no more than .100 higher, no more than 12 extra pitches), and the xwOBA
hitters posted the third time through. The ± is a 95% range for the average with standard errors clustered by pitcher,
because the same pitchers appear in many starts.

| Comparable starts | Starts | Reached a third time | Faced 3+ batters | Times 1–2 xwOBA | Time 3 xwOBA |
|---|---|---|---|---|---|
| About as dominant as Snell | 476 | 468 (98.3%) | 433 (91.0%) | .182 | .295 |
| All starts with 18+ batters | 8,079 | 7,739 (95.8%) | 6,809 (84.3%) | .306 | .336 |

Only starts the manager let continue show a third time, but 98% of these did, so that filter removes little. Its direction is
unclear: managers pull pitchers who look worse, but the outings that ended with a pull keep the plate appearances before it.

**Reliever.** Strikeout rate, walk rate and contact quality become reliable at very different sample sizes, so each is pulled
toward the league average separately and then recombined:

```
shrunk rate = (his events + league rate × K) / (his chances + K)
xwOBA       = walk rate × 0.69 + (1 − strikeout rate − walk rate) × xwOBA on the other plate appearances
```

K comes from split-half reliability (`analysis/estimate_regression_constants.py`): split each reliever-season's games into two
halves, correlate the halves across pitchers (r), and K = m × (1 − r) / r for halves of m plate appearances.

| Component | r with halves of 60 PA | K used |
|---|---|---|
| Strikeout rate | .50 | 60 plate appearances |
| Walk rate | .31 | 130 plate appearances |
| xwOBA on other plate appearances | .07 | 600 (large and uncertain: contact quality barely repeats within a season) |
| Overall xwOBA (old single constant: 300) | .21 | 175–330, depending on m |

**Has he changed?** Compare his recent strikeouts (this postseason, or the last 30 days) with what his earlier games predict.
In the postseason the prediction is adjusted for the hitters he faced, with each hitter's own strikeout rate against that
pitcher hand and the odds-ratio method:

```
odds(expected) = odds(his usual rate) × odds(hitter's rate) / odds(league rate)
Bayes factor   = P(recent strikeouts | fresh league-average talent) / P(recent strikeouts | his usual talent)
P(changed)     = 10% × BF / (10% × BF + 90%)
```

"Unchanged" uses all his games; "changed" uses only the recent ones; "expected" weights the two by P(changed).

**Win value of the pull.**
`points = batters × (starter xwOBA − reliever xwOBA) ÷ 1.2 × leverage ÷ 10 × 100`.
The difference becomes runs (÷ 1.2 wOBA scale), runs become wins (÷ 10 runs per win), scaled by how much this moment matters.
Three batters, because since 2020 a reliever must face at least three (or finish the inning).
**Leverage index:** the average absolute change in win probability for plate appearances in the same inning, half, outs, bases and
score margin (2018–2020 regular seasons), divided by the average for all plate appearances.

### Game-state simulation

`sim.py` solves the rest of the half inning exactly: every plate appearance and every base-out state, weighted by its chance.

1. **Outcome chances for each matchup.** Seven results: strikeout, walk (incl. hit by pitch), single, double, triple, home run, and
   out (every other ball in play). The pitcher's and the hitter's rates are combined with the odds-ratio method:
   `p(result) ∝ pitcher rate × hitter rate ÷ league rate`. Each hitter gets his overall rates plus the league's platoon effect,
   because individual platoon splits are mostly noise at these sample sizes.
2. **What each result does.** `pa_transitions` holds, for every combination of outs, runners and result, the outs, runners and runs
   that followed in 2018–2020, so double plays, sacrifice flies, errors and runners taking an extra base are all in it.
   `build_database.py` checks that 99.99% of these add up (runners + batter = runners after + runs + new outs).
3. **The end of the half inning.** The team's chance to win at the start of the next half inning for each score margin
   (`win_expectancy`, Statcast's model averaged over 2018–2020). After that, both teams are treated as average.
4. **Three choices.** Pull now; let the starter face one more hitter, then take the better choice; let him finish. Plus the best
   possible timing (pull before any hitter). The reliever's two versions are weighted by P(changed).
5. **Uncertainty.** Draws that resample the comparable starts by pitcher and draw each pitcher's and hitter's rates from
   their posteriors, with the reliever's version drawn by P(changed). The site uses 200 draws for speed, which leaves about
   ±3 points of sampling error on "chance pulling is better" (The Hook shows it). Game 6 uses 10,000 draws, precomputed by
   `analysis/game6_review_checks.py` into `site_data/game6_uncertainty.json`.

**Validation** (`analysis/game6_simulation.py`):

- With league-average players, the model reproduces real run expectancy for all 24 base-out states within 0.02 runs per inning on
  average. It runs about 0.1 run low with a runner on third, because it uses the same outcome chances in every situation.
- With league-average players, the Rays' chance to win at the moment of the pull comes out **59.9%, the same as Statcast's model**.
  Against the real Dodgers lineup it is 57.7%: the lineup was better than average, which Statcast's model doesn't know.

| Belief that Anderson changed | Pull now | Face Betts, then decide | Snell finishes | Facing Betts first, vs. the better of the other two |
|---|---|---|---|---|
| 0% | 59.9% | 59.2% | 58.5% | −0.7 points |
| 50% | 58.5% | 58.5% | 58.5% | 0.0 |
| 86% (the data's estimate) | 57.6% | 58.5% | 58.5% | 0.0 |

### Backtest of the change test

`analysis/backtest_reliever_change.py` puts a decision point before every relief appearance in the 2019–2020 regular seasons,
uses the reliever's last 50+ plate appearances as "recent", and predicts his next 50+. 2019 (5,743 points, 243 pitchers) chose the
10% prior; 2020 (94 points, 33 pitchers) is the out-of-sample test.

- In 2020, the change-aware prediction of strikeouts beat the unchanged one in 100% of bootstrap samples by pitcher
  (small gain: 0.001 to 0.007 log-loss per plate appearance). In 2019 the gain was tiny.
- Big recent swings carry forward:

  | Recent strikeouts vs. expected | Points (pitchers) | Next 50 PA: predicted K%, unchanged | Actual | Predicted xwOBA, unchanged | Actual |
  |---|---|---|---|---|---|
  | 2–3 standard deviations below | 143 (40) | .230 | .221 | .306 | .318 |
  | within 2 | 5,409 (246) | .254 | .256 | .298 | .302 |
  | 2+ above | 284 (60) | .271 | .292 | .291 | .280 |

- **Only one regular-season stretch in 2019–2020 fell more than 3 standard deviations below expectation**, so the backtest
  confirms the direction but not the size of an Anderson-level drop.

### Sensitivity

27 starter filters × 4 priors × 3 contact constants (`analysis/game6_checks.py`):

| Prior chance of a change | Expected value of pulling | Settings where pulling is better | Chance pulling is better |
|---|---|---|---|
| 2% | −1.4 to +1.0 | 35 of 81 | 32–60% |
| 5% | −2.2 to +0.2 | 9 of 81 | 19–44% |
| **10% (chosen by the backtest)** | **−2.7 to −0.3** | **0 of 81** | 11–35% |
| 20% | −2.9 to −0.6 | 0 of 81 | 7–30% |

The conclusion leans on the prior. With the prior the backtest chose, staying was better in every setting. With a very skeptical
prior it is a coin flip.

### Review checks

Added after an editorial review of the Game 6 article (`analysis/game6_review_checks.py`).

**Was Anderson the right reliever to compare?** The same simulation for every Rays reliever who had pitched in the 2020 World
Series. None projects better than leaving Snell in. Anderson is the only one whose usual level beats Snell's third time through,
and the one with the clearest warning sign:

| Reliever | Throws | Postseason K: actual / expected | Weight on a change | Pulling Snell if unchanged | Expected |
|---|---|---|---|---|---|
| Diego Castillo | R | 12 / 10.4 | 7% | −0.2 | −0.2 |
| Ryan Yarbrough | L | 7 / 11.3 | 7% | −0.2 | −0.3 |
| Aaron Loup | L | 7 / 4.6 | 9% | −0.3 | −0.3 |
| Pete Fairbanks | R | 15 / 11.6 | 5% | −0.4 | −0.4 |
| Nick Anderson | R | 9 / 19.7 | 86% | **+1.4** | −1.0 |
| John Curtiss | R | 9 / 9.6 | 9% | −1.0 | −1.0 |
| Ryan Thompson | R | 10 / 7.4 | 11% | −1.1 | −1.1 |
| Josh Fleming | L | 3 / 4.3 | 8% | −1.2 | −1.2 |
| Shane McClanahan | L | 4 / 5.0 | 10% | −1.4 | −1.4 |
| Ryan Sherriff | L | 1 / 1.3 | 8% | −1.6 | −1.5 |

**Does estimating Snell from other pitchers' starts matter?** His own 2018–2020 third-time record (232 hitters: 24.6%
strikeouts, .290 xwOBA, but 5.2% home runs) narrows the edge for staying. The direction holds; the size does not.

| Snell's third-time estimate | Rays' chance to win if he finishes | Pulling Snell, expected | Break-even weight on a change |
|---|---|---|---|
| Comparable starts (used throughout) | 58.5% | −1.0 | 51% |
| His own record, pulled toward comparable starts | 58.2% | −0.6 | 62% |
| His own record, raw | 57.7% | −0.1 | 82% |

**Is the chance stable?** 200 draws: 20.0% to 30.5% across 10 seeds. 2,000 draws: 23.8% to 26.7%. 10,000 draws: 25.9%.

### Data used for each input (Game 6)

The Hook lists these for any date. Everything for Game 6 comes from games before it.

| Input | Data used |
|---|---|
| Starter: comparable starts | regular-season starts through Sep 26, 2020 |
| Reliever: history | regular-season games, Mar 28, 2019 to Sep 25, 2020 (318 PA) |
| Reliever: recent | 2020 postseason, 9 games through Oct 24 (57 PA; Game 6 not included) |
| League averages | 2019–2020 regular-season games before Oct 27, 2020 |
| Hitters' strikeout rates | 2018–2020 regular seasons (all before the postseason) |
| Leverage | 2018–2020 regular seasons |

### Data checks

`build_database.py` runs these before exporting anything. All 12 pass.

| Check | Result |
|---|---|
| Each pitch appears once | 0 duplicates |
| Each plate appearance has one result | pass |
| Every game has two starters | pass |
| Regular-season games complete | all three seasons |
| Times through the order is between 1 and 4 | 0 bad rows |
| Exit velocity recorded on balls in play | ≥ 97% |
| Leverage index averages 1.0 | pass |
| Snell's Game 6 matches the box score | 73 pitches / 18 BF / 9 K / 2 H / 0 BB |
| Win probability chains from one plate appearance to the next | mean gap 0.0001 |
| SQL tables reproduce the pandas analysis | 8,079 starts / .349 and 476 starts / .311 (wOBA) |
| Transitions add up (runners + batter = runners after + runs + new outs) | 99.99% of 336,205 plate appearances |
| Run expectancy looks like baseball | 0 out, bases empty: 0.53 runs; 0 out, bases loaded: 2.40 |

## Limitations

- **The simulation stops at the end of the half inning.** After that both teams are treated as average, so bullpen depth for the
  later innings isn't counted. The starter's outcome chances don't change within the third time through (no fatigue curve), and
  the staff doesn't update its view of the reliever from his first hitters.
- **Same outcome chances in every situation.** Real hitters and pitchers change approach with a runner on third; the model runs
  about 0.1 run low there.
- **Lineups only for postseason games.** For regular-season dates The Hook simulates an average lineup.
- **The prior.** The backtest's log loss barely changes between priors in 2019, so 10% is a reasonable choice, not a precise one.
- **The test uses strikeouts only.** Anderson's whiff rate also fell (35% to 22%), which isn't used. "Changed" means a fresh draw
  from the league's reliever talent, one choice among several.
- **Not everything is limited by date yet.** Handedness splits, leverage and the regression constants use all of 2018–2020. For
  Game 6 that is all earlier data; for an earlier date, those inputs include later games. The Hook marks leverage in its data
  list, and the Bullpen Board's ⓘ notes it for the splits.
- **A retrospective rebuild.** Every game used is before the decision date, but the data was downloaded in 2026 and
  Statcast's expected stats are computed as Baseball Savant publishes them today, not necessarily as they read at the time.
- **Data ends in 2020.** Three seasons give only one Anderson-level stretch to test against, and the 2023 rule changes
  (pitch clock, shift limits) changed the run environment.
- **Player photos are current.** MLB serves each player's current headshot, so some appear in a later team's cap.

## Next steps

1. **2021–2025 data.** A bigger backtest for extreme strikeout drops, regular-season lineups for the simulation, and date-limited
   splits and leverage.
2. **Every pitching change, graded.** Run the simulation on each pitching change in the data to see how often managers' calls
   matched the model, and whether the model's calls would have won more games.
3. **Matchup matrix.** The next hitters vs. each available pitcher by handedness, zone and pitch type.

## Run it yourself

The site only needs the small database that's already in the repo:

```bash
python3 -m pip install -r requirements.txt
python3 app.py            # then open http://127.0.0.1:5001
```

The analysis scripts also run on that database (standard library only):

```bash
python3 analysis/estimate_regression_constants.py   # split-half reliability -> regression constants
python3 analysis/backtest_reliever_change.py        # the change test, 2019 -> 2020
python3 analysis/game6_checks.py                    # the Game 6 numbers before the simulation
python3 analysis/game6_simulation.py                # validation, the three choices and their uncertainty
python3 analysis/game6_review_checks.py             # other relievers, Snell's own record, 10,000 draws (~2 min)
```

To rebuild everything from Statcast (the first run takes a while; later runs read from pybaseball's cache):

```bash
python3 -m pip install pybaseball pandas matplotlib
python3 build_database.py              # warehouse + checks + site_data/coaches.db
python3 build_database.py --skip-load  # rebuild the tables without reloading pitches
```

The first version's analysis scripts, in order: `snell_vs_peers.py` → `snell_decision.py` → `snell_matchups.py`.
They write CSVs and a chart to `output/`. Tested with Python 3.9.

## Project structure

```
app.py               Flask routes for the four pages
model.py             projections, the change test and the simpler pull formula
sim.py               game-state simulation of the rest of the half inning
queries.py           data access: pitcher totals, leverage, the starter card, win probability
charts.py            win-probability chart, drawn as SVG on the server
mlb.py               team colours, logos, headshots and game-film links
templates/           page templates (Jinja)
public/static/       stylesheet (Vercel serves public/ from its CDN)
site_data/           coaches.db, DATA_DICTIONARY.md and game6_uncertainty.json (Game 6, 10,000 draws)
analysis/            regression constants, backtest, Game 6 checks and review checks
build_database.py    Statcast → SQLite warehouse → checks → site database
snell_*.py           the first version's Game 6 analysis
output/              CSVs and charts from the analysis scripts
docs/                screenshots for this README
```

## Credits

Pitch data: [Baseball Savant](https://baseballsavant.mlb.com) (Statcast) via [pybaseball](https://github.com/jldbc/pybaseball).
Player photos and team logos © MLB, loaded from MLB's servers. Built for the Penn State Sports Analytics Club project cohort.
Time-through-the-order background: Brill, Deshpande & Wyner, "A Bayesian analysis of the time through the order penalty in
baseball" (2023).
