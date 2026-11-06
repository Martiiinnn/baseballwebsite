"""Team colours, team logos, player headshots and game-film links.

Logos and headshots are loaded straight from MLB's own image servers (nothing is copied into
this repo) and credited in the footer. Every team code below was checked to load both logo
versions. An unknown player id gets MLB's generic silhouette instead of a broken image.
"""

# Statcast team code -> MLB team id (used in MLB's image and page addresses)
TEAM_IDS = {
    "AZ": 109, "ATL": 144, "BAL": 110, "BOS": 111, "CHC": 112, "CWS": 145, "CIN": 113,
    "CLE": 114, "COL": 115, "DET": 116, "HOU": 117, "KC": 118, "LAA": 108, "LAD": 119,
    "MIA": 146, "MIL": 158, "MIN": 142, "NYM": 121, "NYY": 147, "ATH": 133, "PHI": 143,
    "PIT": 134, "SD": 135, "SF": 137, "SEA": 136, "STL": 138, "TB": 139, "TEX": 140,
    "TOR": 141, "WSH": 120,
}

# Each team's main colour. It is used on one element only: the header bar.
TEAM_COLORS = {
    "AZ": "#A71930", "ATL": "#13274F", "BAL": "#DF4601", "BOS": "#BD3039", "CHC": "#0E3386",
    "CWS": "#27251F", "CIN": "#C6011F", "CLE": "#00385D", "COL": "#333366", "DET": "#0C2340",
    "HOU": "#002D62", "KC": "#004687", "LAA": "#BA0021", "LAD": "#005A9C", "MIA": "#00A3E0",
    "MIL": "#12284B", "MIN": "#002B5C", "NYM": "#002D72", "NYY": "#0C2340", "ATH": "#003831",
    "PHI": "#E81828", "PIT": "#27251F", "SD": "#2F241D", "SF": "#FD5A1E", "SEA": "#0C2C56",
    "STL": "#C41E3A", "TB": "#092C5C", "TEX": "#003278", "TOR": "#134A8E", "WSH": "#AB0003",
}
NEUTRAL = "#27251F"
WHITE, BLACK = "#ffffff", "#0b0b0b"


def _luminance(hex_color):
    """Relative luminance (WCAG) of a colour like '#092C5C'."""
    channels = [int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    lin = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def _contrast(a, b):
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def team_color(team):
    """Header colours for a team: its main colour, plus white or near-black text,
    whichever is easier to read on it (e.g. black on the Giants' orange)."""
    bg = TEAM_COLORS.get(team, NEUTRAL)
    fg = WHITE if _contrast(bg, WHITE) >= _contrast(bg, BLACK) else BLACK
    return {"bg": bg, "fg": fg, "on": "dark" if fg == WHITE else "light"}


def logo(team, on="light"):
    """Team cap logo. on='light' is drawn for light backgrounds, on='dark' for dark ones."""
    team_id = TEAM_IDS.get(team)
    if team_id is None:
        return None
    return f"https://www.mlbstatic.com/team-logos/team-cap-on-{on}/{team_id}.svg"


def headshot(player_id):
    """Player headshot (120 px wide, sharp at up to 60 px on retina screens)."""
    return ("https://img.mlbstatic.com/mlb-photos/image/upload/"
            "d_people:generic:headshot:67:current.png/w_120,q_auto:best/v1/people/"
            f"{int(player_id)}/headshot/67/current")


def game_film(game_pk):
    """Baseball Savant's pitch-by-pitch page for a game, with a video clip for each pitch."""
    return f"https://baseballsavant.mlb.com/gamefeed?gamePk={int(game_pk)}"
