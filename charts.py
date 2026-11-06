"""Charts drawn as inline SVG on the server: no JavaScript or extra packages needed.
Colors come from CSS variables, so the chart follows the page's light/dark theme."""
from markupsafe import Markup, escape

W, DEFAULT_H = 960, 300
LEFT, RIGHT, TOP, BOTTOM = 52, 16, 20, 44


def win_prob_svg(rows, team, mark_ab=None, mark_label="", height=DEFAULT_H):
    """Step line of the team's win probability, one step per plate appearance.
    Hovering a step shows who batted, what happened and how the odds moved."""
    if not rows:
        return Markup("")
    H = height
    n = len(rows)
    p = [r["before"] for r in rows] + [rows[-1]["after"]]
    pw, ph = W - LEFT - RIGHT, H - TOP - BOTTOM

    def x(i):
        return LEFT + pw * i / n

    def y(v):
        return TOP + ph * (1 - max(0.0, min(1.0, v)))

    parts = [f'<svg class="chart" viewBox="0 0 {W} {H}" role="img" '
             f'aria-label="{escape(team)} win probability by plate appearance">']

    # gridlines and y labels
    for v in (0, 0.25, 0.5, 0.75, 1):
        parts.append(f'<line class="grid" x1="{LEFT}" x2="{W - RIGHT}" y1="{y(v):.1f}" y2="{y(v):.1f}"/>')
        parts.append(f'<text class="tick" x="{LEFT - 8}" y="{y(v) + 4:.1f}" text-anchor="end">{v:.0%}</text>')

    # inning labels at the first plate appearance of each inning
    seen = set()
    for i, r in enumerate(rows):
        if r["inning"] not in seen:
            seen.add(r["inning"])
            parts.append(f'<line class="inning" x1="{x(i):.1f}" x2="{x(i):.1f}" '
                         f'y1="{TOP}" y2="{H - BOTTOM}"/>')
            parts.append(f'<text class="tick" x="{x(i) + 4:.1f}" y="{H - BOTTOM + 18}">{r["inning"]}</text>')
    parts.append(f'<text class="axis-title" x="{LEFT + pw / 2:.1f}" y="{H - 6}" '
                 f'text-anchor="middle">Inning</text>')

    # the line
    d = f"M{x(0):.1f},{y(p[0]):.1f}"
    for i in range(n):
        d += f" H{x(i + 1):.1f} V{y(p[i + 1]):.1f}"
    parts.append(f'<path class="line" d="{d}"/>')

    # the decision marker
    if mark_ab is not None:
        idx = next((i for i, r in enumerate(rows) if r["ab"] == mark_ab), None)
        if idx is not None:
            mx, my = x(idx), y(p[idx])
            below = p[idx] > 0.5
            ty = my + 46 if below else my - 40
            parts.append(f'<line class="marker-rule" x1="{mx:.1f}" x2="{mx:.1f}" '
                         f'y1="{TOP}" y2="{H - BOTTOM}"/>')
            parts.append(f'<line class="leader" x1="{mx:.1f}" y1="{my:.1f}" x2="{mx - 14:.1f}" y2="{ty - 14:.1f}"/>')
            parts.append(f'<circle class="marker" cx="{mx:.1f}" cy="{my:.1f}" r="6"/>')
            label = escape(mark_label)
            parts.append(f'<text class="annotation" x="{mx - 18:.1f}" y="{ty - 10:.1f}" text-anchor="end">'
                         f'<tspan x="{mx - 18:.1f}">{label}</tspan>'
                         f'<tspan x="{mx - 18:.1f}" dy="1.25em">{escape(team)} {p[idx]:.0%} to win</tspan></text>')

    # hover targets, wider than the line itself
    for i, r in enumerate(rows):
        half = "Top" if r["half"] == "Top" else "Bottom"
        tip = (f"{half} {r['inning']} · {r['batter'] or 'Batter'} vs {r['pitcher'] or 'pitcher'}: "
               f"{r['event'] or 'no result'} · {team} {r['before']:.0%} → {r['after']:.0%}")
        parts.append(f'<rect class="hit" x="{x(i):.1f}" y="{TOP}" width="{max(pw / n, 1):.1f}" '
                     f'height="{ph}"><title>{escape(tip)}</title></rect>')

    parts.append("</svg>")
    return Markup("".join(parts))
