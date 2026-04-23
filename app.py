import streamlit as st
import sqlite3
import requests
import tempfile
import os
from datetime import datetime

def scramble_id(real_id: int, prefix: str = "") -> str:
    """Simple reversible scrambler: 11 → T10011K"""
    if real_id is None or real_id <= 0:
        return ""

    salted = real_id + 10000          # makes small numbers look longer
    base = f"{salted:05d}"            # always 5 digits
    letters = "ABCDEFGHJKLMNPQRSTUVWXYZ"
    letter = letters[real_id % len(letters)]

    return f"{prefix}{base}{letter}"


def unscramble_id(scrambled: str, prefix: str = "") -> int:
    """Reverse the scrambler"""
    if not scrambled:
        return 0

    # Remove prefix if present (e.g. "T10011K" → "10011K")
    if prefix and scrambled.startswith(prefix):
        scrambled = scrambled[len(prefix):]

    # Keep only digits, remove the final letter
    digits = ''.join(c for c in scrambled if c.isdigit())
    if not digits:
        return 0

    salted = int(digits)
    real_id = salted - 10000
    return real_id if real_id > 0 else 0

# ====================== PAGE CONFIG ======================
st.set_page_config(
    layout="wide",
    page_title="CourtTag Report Viewer",
    page_icon="https://raw.githubusercontent.com/CourtTag/courttag-assets/main/CourtTag_Icon_512x512.png"
    #page_icon="🏀"   # Basketball emoji — works reliably everywhere
)

# Manual HTML favicon fallback (most reliable for browser tab)
st.markdown(
    f"""
    <link rel="icon" href="https://raw.githubusercontent.com/CourtTag/courttag-assets/main/CourtTag_Icon_512x512.png" type="image/png">
    """,
    unsafe_allow_html=True
)

# Shot Quality mapping used in the Shot Quality table
SHOT_QUALITY = [
    ("A / Paint - Close", "A"),
    ("B / Paint - Mid", "B"),
    ("C / Mid-Range", "C"),
    ("D / Long 2 / 3PT", "D"),
    # Add any other qualities you use in your desktop app
]

VERSION = "1.0"
BUILD = "b1"
CT_BUILD = "b47"


#1.0b1 First Version, slowly adding reports from CT App.. games listing working so far


# Hide almost all Streamlit default junk + reduce top spacing
st.markdown("""
    <style>
        #MainMenu {visibility: hidden;}
        footer {visibility: hidden;}
        header {visibility: hidden;}
        .block-container {
            padding-top: 0.5rem !important;
            padding-bottom: 1rem !important;
        }
        h1, h2, h3, h4 {
            margin-top: 0.3rem !important;
            margin-bottom: 0.6rem !important;
            font-family: Arial, sans-serif;
        }
        p {
            font-family: Arial, sans-serif;    
        }
    </style>
""", unsafe_allow_html=True)

# ====================== GITHUB DB LOADING (Private Repo + Token) ======================
full_code = st.query_params.get("code")
if not full_code:
    st.error("Missing code in the URL.")
    st.stop()

coach_code = full_code.split('-')[0] if '-' in full_code else full_code
db_filename = f"{coach_code}.db"

# Get GitHub config from secrets
try:
    GITHUB_TOKEN = st.secrets["github"]["token"]
    GITHUB_OWNER = st.secrets["github"]["owner"]
    GITHUB_REPO = st.secrets["github"]["repo"]
except Exception as e:
    st.error(f"GitHub secrets not configured: {e}")
    st.stop()

# Use authenticated request
url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{db_filename}"

headers = {
    "Authorization": f"token {GITHUB_TOKEN}",
    "Accept": "application/vnd.github.v3.raw"
}

#st.info(f"Downloading database: {db_filename} from GitHub...")

try:
    response = requests.get(url, headers=headers, timeout=15)

    if response.status_code != 200:
        st.error(f"Failed to download DB. HTTP {response.status_code}")
        st.stop()

    db_bytes = response.content

    # Load into SQLite
    fd, tmp_path = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    with open(tmp_path, 'wb') as f:
        f.write(db_bytes)

    conn = sqlite3.connect(tmp_path)
    memory_conn = sqlite3.connect(":memory:")
    conn.backup(memory_conn)
    conn.close()
    os.unlink(tmp_path)
    conn = memory_conn

    #st.success(f"✅ Database loaded successfully ({len(db_bytes):,} bytes)")

except Exception as e:
    st.error(f"Database load failed: {e}")
    st.stop()



# ====================== CLEAN GAME REPORT ======================
def generate_team_report(conn, team_id, full_code):
    c = conn.cursor()
    c.row_factory = lambda cursor, row: tuple(0 if val is None else val for val in row)

    c.execute("SELECT name, season FROM teams WHERE id = ?", (team_id,))
    team_info = c.fetchone()
    if not team_info:
        return "<h2>Team not found.</h2>"
    team_name, season = team_info

    # Games query
    c.execute("""
        SELECT g.id, g.date, g.name,
               CASE WHEN g.home_team_id = ? THEN t_guest.name ELSE t_home.name END AS opponent,
               CASE WHEN g.home_team_id = ? THEN 'home' ELSE 'guest' END AS team_side
        FROM games g
        LEFT JOIN teams t_home ON g.home_team_id = t_home.id
        LEFT JOIN teams t_guest ON g.guest_team_id = t_guest.id
        WHERE (g.home_team_id = ? OR g.guest_team_id = ?)
          AND g.is_complete = 1
        ORDER BY g.date ASC
    """, (team_id, team_id, team_id, team_id))
    games = c.fetchall()

    if not games:
        return f"<h2>Team Report: {team_name}</h2><p>No complete games yet.</p>"

    # Initialize totals
    num_games = len(games)
    total_opts = total_pts = total_poss = 0
    total_2pm = total_2pa = total_3pm = total_3pa = total_ftm = total_fta = 0
    total_orb = total_dreb = total_treb = 0
    total_opp_oreb = total_opp_dreb = total_opp_treb = 0
    total_pfl = total_ast = total_to = total_stl = total_blk = total_chg = total_dfl = 0

    wins = losses = ties = 0

    game_table = """
    <div style="overflow-x: auto; width: 100%; padding: 10px 0;">   
        <table border="2" cellpadding="2" cellspacing="0" style="width: max-content; min-width: 100%; border-color: white; border-collapse: collapse; font-family: Arial, sans-serif;">
            <tr style="background:#f2c74a; font-weight:bold; text-align:center;">
                <th style="text-align:left;">Date</th>
                <th style="text-align:left;">Game Name</th>
                <th style="text-align:left;">Opponent</th>
                <th>OPTS</th><th>PTS</th><th>POSS</th><th>PPP</th>
                <th>2PTM</th><th>2PTA</th><th>2PT%</th>
                <th>3PTM</th><th>3PTA</th><th>3PT%</th>
                <th>eFG%</th>
                <th>FTM</th><th>FTA</th><th>FT%</th>
                <th>FTF</th>
                <th>OREB%</th><th>DREB%</th><th>TREB%</th>
                <th>PFL</th><th>AST</th><th>TO</th>
                <th>STL</th><th>BLK</th><th>CHG</th><th>DFL</th>
                <th>TOV%</th><th>ATR</th>
            </tr>
    """

    row_colour = "#FFEDB8;"

    for g_id, date, g_name, opponent, team_side in games:
        # Team points
        c.execute("""SELECT SUM(CASE WHEN e.type LIKE '2PM%' THEN 2 WHEN e.type LIKE '3PM%' THEN 3 WHEN e.type LIKE 'FTM%' THEN 1 ELSE 0 END) FROM events e JOIN videos v ON e.video_id = v.id WHERE v.game_id = ? AND ((e.player_id IN (SELECT player_id FROM game_rosters WHERE game_id = ? AND side = ?)) OR (e.player_id IS NULL AND e.team_side = ?))""", (g_id, g_id, team_side, team_side))
        team_pts = c.fetchone()[0] or 0

        # Opponent
        opp_side = 'guest' if team_side == 'home' else 'home'
        c.execute("""SELECT SUM(CASE WHEN e.type LIKE '2PM%' THEN 2 WHEN e.type LIKE '3PM%' THEN 3 WHEN e.type LIKE 'FTM%' THEN 1 ELSE 0 END), SUM(CASE WHEN e.type LIKE 'ORB%' THEN 1 ELSE 0 END), SUM(CASE WHEN e.type LIKE 'DRB%' THEN 1 ELSE 0 END) FROM events e JOIN videos v ON e.video_id = v.id WHERE v.game_id = ? AND ((e.player_id IN (SELECT player_id FROM game_rosters WHERE game_id = ? AND side = ?)) OR (e.player_id IS NULL AND e.team_side = ?))""", (g_id, g_id, opp_side, opp_side))
        opp_pts, opp_oreb, opp_dreb = c.fetchone() or (0, 0, 0)
        opp_treb = opp_oreb + opp_dreb

        # Detailed stats
        c.execute("""SELECT SUM(CASE WHEN e.type LIKE '2PM%' THEN 1 ELSE 0 END), SUM(CASE WHEN e.type LIKE '2P%' AND e.type NOT LIKE '2PM%' THEN 1 ELSE 0 END), SUM(CASE WHEN e.type LIKE '3PM%' THEN 1 ELSE 0 END), SUM(CASE WHEN e.type LIKE '3P%' AND e.type NOT LIKE '3PM%' THEN 1 ELSE 0 END), SUM(CASE WHEN e.type LIKE 'FTM%' THEN 1 ELSE 0 END), SUM(CASE WHEN e.type LIKE 'FT%' AND e.type NOT LIKE 'FTM%' THEN 1 ELSE 0 END), SUM(CASE WHEN e.type LIKE 'ORB%' THEN 1 ELSE 0 END), SUM(CASE WHEN e.type LIKE 'DRB%' THEN 1 ELSE 0 END), SUM(CASE WHEN e.type LIKE 'PFL%' THEN 1 ELSE 0 END), SUM(CASE WHEN e.type LIKE 'AST%' THEN 1 ELSE 0 END), SUM(CASE WHEN e.type LIKE 'TOV%' THEN 1 ELSE 0 END), SUM(CASE WHEN e.type LIKE 'STL%' THEN 1 ELSE 0 END), SUM(CASE WHEN e.type LIKE 'BLK%' THEN 1 ELSE 0 END), SUM(CASE WHEN e.type LIKE 'CHG%' THEN 1 ELSE 0 END), SUM(CASE WHEN e.type LIKE 'DFL%' THEN 1 ELSE 0 END) FROM events e JOIN videos v ON e.video_id = v.id WHERE v.game_id = ? AND ((e.player_id IN (SELECT player_id FROM game_rosters WHERE game_id = ? AND side = ?)) OR (e.player_id IS NULL AND e.team_side = ?))""", (g_id, g_id, team_side, team_side))
        stats = c.fetchone() or (0,)*15
        twopm, twopa_miss, threepm, threepa_miss, ftm, fta_miss, oreb, dreb, pfl, ast, tov, stl, blk, chg, dfl = stats

        fga_2p = twopa_miss + twopm
        fga_3p = threepa_miss + threepm
        fta_total = fta_miss + ftm
        fga_total = fga_2p + fga_3p

        poss = round(fga_2p + fga_3p + 0.44 * fta_total - oreb + tov) if fga_total else 0
        ppp = round(team_pts / poss, 2) if poss > 0 else 0.0

        p2_pct = round(twopm / fga_2p * 100, 1) if fga_2p > 0 else 0.0
        p3_pct = round(threepm / fga_3p * 100, 1) if fga_3p > 0 else 0.0
        ft_pct = round(ftm / fta_total * 100, 1) if fta_total > 0 else 0.0

        efg_pct = round((twopm + threepm + 0.5 * threepm) / fga_total * 100, 1) if fga_total > 0 else 0.0

        treb = oreb + dreb
        oreb_pct = round(oreb / (oreb + opp_dreb) * 100, 1) if (oreb + opp_dreb) > 0 else 0.0
        dreb_pct = round(dreb / (dreb + opp_oreb) * 100, 1) if (dreb + opp_oreb) > 0 else 0.0
        treb_pct = round(treb / (treb + opp_treb) * 100, 1) if (treb + opp_treb) > 0 else 0.0

        atr = round(ast / tov, 2) if tov > 0 else 0.0
        tov_pct = round(tov / poss * 100, 1) if poss > 0 else 0.0

        game_link = scramble_id(g_id, "G")

        game_table += f"""
        <tr style="background:{row_colour};">
            <td style="text-align:left;">{date}</td>
            <td><a href="?code={full_code}&g={game_link}" style="color:#0066cc; text-decoration:underline;">{g_name}</a></td>
            <td style="text-align:left;">{opponent}</td>
            <td style="text-align:center;">{opp_pts}</td>
            <td style="text-align:center;">{team_pts}</td>
            <td style="text-align:center;">{poss}</td>
            <td style="text-align:center;">{ppp:.2f}</td>
            <td style="text-align:center;">{twopm}</td>
            <td style="text-align:center;">{fga_2p}</td>
            <td style="text-align:center;">{p2_pct:.1f}%</td>
            <td style="text-align:center;">{threepm}</td>
            <td style="text-align:center;">{fga_3p}</td>
            <td style="text-align:center;">{p3_pct:.1f}%</td>
            <td style="text-align:center;">{efg_pct:.1f}%</td>
            <td style="text-align:center;">{ftm}</td>
            <td style="text-align:center;">{fta_total}</td>
            <td style="text-align:center;">{ft_pct:.1f}%</td>
            <td style="text-align:center;">{(fta_total / fga_total if fga_total else 0):.2f}</td>
            <td style="text-align:center;">{oreb_pct:.1f}%</td>
            <td style="text-align:center;">{dreb_pct:.1f}%</td>
            <td style="text-align:center;">{treb_pct:.1f}%</td>
            <td style="text-align:center;">{pfl}</td>
            <td style="text-align:center;">{ast}</td>
            <td style="text-align:center;">{tov}</td>
            <td style="text-align:center;">{stl}</td>
            <td style="text-align:center;">{blk}</td>
            <td style="text-align:center;">{chg}</td>
            <td style="text-align:center;">{dfl}</td>
            <td style="text-align:center;">{tov_pct:.1f}%</td>
            <td style="text-align:center;">{atr:.2f}</td>
        </tr>
        """
        row_colour = "#FFFFFF;" if row_colour == "#FFEDB8;" else "#FFEDB8;"

        # Accumulate totals
        total_opts += opp_pts
        total_pts += team_pts
        total_poss += poss
        total_2pm += twopm
        total_2pa += fga_2p
        total_3pm += threepm
        total_3pa += fga_3p
        total_ftm += ftm
        total_fta += fta_total
        total_orb += oreb
        total_dreb += dreb
        total_treb += treb
        total_opp_oreb += opp_oreb
        total_opp_dreb += opp_dreb
        total_opp_treb += opp_treb
        total_pfl += pfl
        total_ast += ast
        total_to += tov
        total_stl += stl
        total_blk += blk
        total_chg += chg
        total_dfl += dfl

        if team_pts > opp_pts:
            wins += 1
        elif team_pts < opp_pts:
            losses += 1
        else:
            ties += 1

    # === TOTALS AND AVERAGES ROWS ===
    total_2p_pct = round(total_2pm / total_2pa * 100, 1) if total_2pa > 0 else 0.0
    total_3p_pct = round(total_3pm / total_3pa * 100, 1) if total_3pa > 0 else 0.0
    total_ft_pct = round(total_ftm / total_fta * 100, 1) if total_fta > 0 else 0.0
    avg_ppp = round(total_pts / total_poss, 2) if total_poss > 0 else 0.0

    total_efg_pct = round((total_2pm + total_3pm + 0.5 * total_3pm) / (total_2pa + total_3pa) * 100, 1) if (total_2pa + total_3pa) > 0 else 0.0
    total_ftf = round(total_fta / (total_2pa + total_3pa), 2) if (total_2pa + total_3pa) > 0 else 0.0

    total_oreb_pct = round(total_orb / (total_orb + total_opp_dreb) * 100, 1) if (total_orb + total_opp_dreb) > 0 else 0.0
    total_dreb_pct = round(total_dreb / (total_dreb + total_opp_oreb) * 100, 1) if (total_dreb + total_opp_oreb) > 0 else 0.0
    total_treb_pct = round(total_treb / (total_treb + total_opp_treb) * 100, 1) if (total_treb + total_opp_treb) > 0 else 0.0

    overall_tov_pct = round(total_to / total_poss * 100, 1) if total_poss > 0 else 0.0
    avg_atr = round(total_ast / total_to, 2) if total_to > 0 else 0.0

    # Totals row
    game_table += f"""
        <tr style="font-weight:bold; background:#f2c74a; text-align:center;">
            <td style="text-align:left;">Games Played:</td>
            <td style="text-align: center;">{num_games}</td>
            <td style="text-align:right;">Totals:</td>
            <td>{total_opts}</td>
            <td>{total_pts}</td>
            <td>{total_poss}</td>
            <td>{avg_ppp:.2f}</td>
            <td>{total_2pm}</td>
            <td>{total_2pa}</td>
            <td>{total_2p_pct:.1f}%</td>
            <td>{total_3pm}</td>
            <td>{total_3pa}</td>
            <td>{total_3p_pct:.1f}%</td>
            <td>{total_efg_pct:.1f}%</td>
            <td>{total_ftm}</td>
            <td>{total_fta}</td>
            <td>{total_ft_pct:.1f}%</td>
            <td>{total_ftf:.2f}</td>
            <td>{total_oreb_pct:.1f}%</td>
            <td>{total_dreb_pct:.1f}%</td>
            <td>{total_treb_pct:.1f}%</td>
            <td>{total_pfl}</td>
            <td>{total_ast}</td>
            <td>{total_to}</td>
            <td>{total_stl}</td>
            <td>{total_blk}</td>
            <td>{total_chg}</td>
            <td>{total_dfl}</td>
            <td>{overall_tov_pct:.1f}%</td>
            <td>{avg_atr:.2f}</td>
        </tr>
    """

    # Averages row
    game_table += f"""
        <tr style="font-weight:bold; background:#FFEDB8; text-align:center;">
            <td style="text-align:left;">Win-Loss-Tie:</td>
            <td style="text-align:center;">{wins}-{losses}-{ties}</td>
            <td style="text-align:right;">Averages:</td>
            <td>{round(total_opts / num_games, 1) if num_games > 0 else 0}</td>
            <td>{round(total_pts / num_games, 1) if num_games > 0 else 0}</td>
            <td>{round(total_poss / num_games, 1) if num_games > 0 else 0}</td>
            <td>{avg_ppp:.2f}</td>
            <td>{round(total_2pm / num_games, 1) if num_games > 0 else 0}</td>
            <td>{round(total_2pa / num_games, 1) if num_games > 0 else 0}</td>
            <td>-</td>
            <td>{round(total_3pm / num_games, 1) if num_games > 0 else 0}</td>
            <td>{round(total_3pa / num_games, 1) if num_games > 0 else 0}</td>
            <td>-</td>
            <td>-</td>
            <td>{round(total_ftm / num_games, 1) if num_games > 0 else 0}</td>
            <td>{round(total_fta / num_games, 1) if num_games > 0 else 0}</td>
            <td>-</td>
            <td>-</td>
            <td>{round(total_orb / num_games, 1) if num_games > 0 else 0}</td>
            <td>{round(total_dreb / num_games, 1) if num_games > 0 else 0}</td>
            <td>{round(total_treb / num_games, 1) if num_games > 0 else 0}</td>
            <td>{round(total_pfl / num_games, 1) if num_games > 0 else 0}</td>
            <td>{round(total_ast / num_games, 1) if num_games > 0 else 0}</td>
            <td>{round(total_to / num_games, 1) if num_games > 0 else 0}</td>
            <td>{round(total_stl / num_games, 1) if num_games > 0 else 0}</td>
            <td>{round(total_blk / num_games, 1) if num_games > 0 else 0}</td>
            <td>{round(total_chg / num_games, 1) if num_games > 0 else 0}</td>
            <td>{round(total_dfl / num_games, 1) if num_games > 0 else 0}</td>
            <td>-</td>
            <td>-</td>
        </tr>
    """

    game_table += "</table></div>"

    # === SHOT LOCATIONS ===
    c.execute("""
              SELECT e.location,
                     SUM(CASE WHEN e.type LIKE '2PM%' THEN 1 ELSE 0 END)                           as twopm,
                     SUM(CASE WHEN e.type LIKE '2P%' AND e.type NOT LIKE '2PM%' THEN 1 ELSE 0 END) as missed_2pa,
                     SUM(CASE WHEN e.type LIKE '3PM%' THEN 1 ELSE 0 END)                           as threepm,
                     SUM(CASE WHEN e.type LIKE '3P%' AND e.type NOT LIKE '3PM%' THEN 1 ELSE 0 END) as missed_3pa
              FROM events e
                       JOIN videos v ON e.video_id = v.id
                       JOIN games g ON v.game_id = g.id
              WHERE (g.home_team_id = ? OR g.guest_team_id = ?)
                AND e.location IS NOT NULL
                AND e.location != '-'
                  AND (e.type LIKE '2P%' OR e.type LIKE '3P%')
                  AND (
                    e.player_id IN (SELECT player_id FROM game_rosters WHERE game_id = g.id AND side = CASE WHEN g.home_team_id = ? THEN 'home' ELSE 'guest' END)
                    OR (e.player_id IS NULL AND e.team_side = CASE WHEN g.home_team_id = ? THEN 'home' ELSE 'guest' END)
                  )
                  AND g.is_complete = 1
              GROUP BY e.location
              ORDER BY e.location
              """, (team_id, team_id, team_id, team_id))
    loc_rows = c.fetchall()

    loc_table = """
    <table><tr><td valign='top'>
        <!-- Left: Shot Location Table -->
        <div style="flex: 1; overflow-x: auto;">
            <table border="2" cellpadding="4" cellspacing="0" style="border-color: white; border-collapse: collapse; font-family: Arial, sans-serif;">
                <tr style="background:#f2c74a; font-weight:bold; text-align:center;">
                    <th style="text-align:left;">Shot Location (FG Only)</th>
                    <th>2PTM</th><th>2PTA</th><th>2PT%</th>
                    <th>3PTM</th><th>3PTA</th><th>3PT%</th>
                </tr>
    """

    loc_total_2pm = loc_total_2pa = loc_total_3pm = loc_total_3pa = 0
    row_colour = "#FFFFFF;"

    for loc, twopm, missed_2pa, threepm, missed_3pa in loc_rows:
        twopa_total = missed_2pa + twopm
        threepa_total = missed_3pa + threepm
        p2 = (twopm / twopa_total * 100) if twopa_total > 0 else 0.0
        p3 = (threepm / threepa_total * 100) if threepa_total > 0 else 0.0

        loc_table += f"""
                <tr style="background:{row_colour}; text-align:center;">
                    <td style="text-align:left;">{loc}</td>
                    <td>{twopm}</td>
                    <td>{twopa_total}</td>
                    <td>{p2:.1f}%</td>
                    <td>{threepm}</td>
                    <td>{threepa_total}</td>
                    <td>{p3:.1f}%</td>
                </tr>
        """

        loc_total_2pm += twopm
        loc_total_2pa += twopa_total
        loc_total_3pm += threepm
        loc_total_3pa += threepa_total

        row_colour = "#FFEDB8;" if row_colour == "#FFFFFF;" else "#FFFFFF;"

    # Totals row
    loc_total_p2 = (loc_total_2pm / loc_total_2pa * 100) if loc_total_2pa > 0 else 0.0
    loc_total_p3 = (loc_total_3pm / loc_total_3pa * 100) if loc_total_3pa > 0 else 0.0

    loc_table += f"""
                <tr style="font-weight:bold; background:#f2c74a; text-align:center;">
                    <td style="text-align:right;">Total:</td>
                    <td>{loc_total_2pm}</td>
                    <td>{loc_total_2pa}</td>
                    <td>{loc_total_p2:.1f}%</td>
                    <td>{loc_total_3pm}</td>
                    <td>{loc_total_3pa}</td>
                    <td>{loc_total_p3:.1f}%</td>
                </tr>
            </table>
        </div>
    </td>
    <td valign='top'>
        <!-- Right: Shot Location Image -->
        <img src="https://raw.githubusercontent.com/CourtTag/courttag-assets/main/Shot_Location_Diagram_v1.svg" alt="Shot Location Diagram" style="width: 300px; height: 240px;">
    </td></tr></table>
    """

    # === SHOT QUALITY TABLE ===
    # Build display map from global SHOT_QUALITY
    quality_map = {}
    for full_name, code in SHOT_QUALITY:
        display_name = full_name.split('/', 1)[0].strip()
        quality_map[code] = display_name
    quality_map['None'] = 'Not Assigned'

    c.execute("""
        SELECT 
            COALESCE(e.shot_quality, 'None') AS quality,
            SUM(CASE WHEN e.type LIKE '2PM%' THEN 1 ELSE 0 END) AS twopm,
            SUM(CASE WHEN e.type LIKE '2PA%' THEN 1 ELSE 0 END) AS twopa_miss,
            SUM(CASE WHEN e.type LIKE '3PM%' THEN 1 ELSE 0 END) AS threepm,
            SUM(CASE WHEN e.type LIKE '3PA%' THEN 1 ELSE 0 END) AS threepa_miss,
            SUM(CASE WHEN e.type LIKE '2PM%' THEN 2 
                     WHEN e.type LIKE '3PM%' THEN 3 
                     ELSE 0 END) AS points
        FROM events e
        JOIN videos v ON e.video_id = v.id
        JOIN games g ON v.game_id = g.id
        WHERE (g.home_team_id = ? OR g.guest_team_id = ?)
          AND (e.type LIKE '2P%' OR e.type LIKE '3P%')
          AND g.is_complete = 1
          AND (
                e.player_id IN (SELECT player_id FROM game_rosters 
                                WHERE game_id = g.id 
                                AND side = CASE WHEN g.home_team_id = ? THEN 'home' ELSE 'guest' END)
             OR (e.player_id IS NULL AND e.team_side = CASE WHEN g.home_team_id = ? THEN 'home' ELSE 'guest' END)
          )
        GROUP BY COALESCE(e.shot_quality, 'None')
        ORDER BY 
            CASE 
                WHEN COALESCE(e.shot_quality, 'None') = 'A' THEN 1
                WHEN COALESCE(e.shot_quality, 'None') = 'B' THEN 2
                WHEN COALESCE(e.shot_quality, 'None') = 'C' THEN 3
                WHEN COALESCE(e.shot_quality, 'None') = 'D' THEN 4
                ELSE 5 
            END
    """, (team_id, team_id, team_id, team_id))

    quality_rows = c.fetchall()

    total_quality_points = sum(row[5] for row in quality_rows) if quality_rows else 0
    total_fg_attempts = sum((row[1] or 0) + (row[2] or 0) + (row[3] or 0) + (row[4] or 0) for row in quality_rows)

    shot_quality_table = """
    <div style="overflow-x: auto;">
        <table border="2" cellpadding="4" cellspacing="0" style="width: max-content; border-color: white; border-collapse: collapse; font-family: Arial, sans-serif;">
            <tr style="background:#f2c74a; font-weight:bold; text-align:center;">
                <th style="text-align:left;">Shot Quality</th>
                <th>2PTM</th>
                <th>2PTA</th>
                <th>2PT%</th>
                <th>3PTM</th>
                <th>3PTA</th>
                <th>3PT%</th>
                <th>FGM</th>
                <th>FGA</th>
                <th>eFG%</th>
                <th>PTS</th>
                <th>PPS</th>
                <th>TPT%</th>
                <th>TST%</th>
            </tr>
    """

    row_colour = "#FFFFFF;"

    total_2pm = total_2pa = total_3pm = total_3pa = total_fgm = total_fga = total_pts = 0

    for qual, twopm, twopa_miss, threepm, threepa_miss, pts in quality_rows:
        twopm = twopm or 0
        twopa_miss = twopa_miss or 0
        threepm = threepm or 0
        threepa_miss = threepa_miss or 0
        pts = pts or 0

        twopa = twopm + twopa_miss
        threepa = threepm + threepa_miss
        fgm = twopm + threepm
        fga = twopa + threepa

        p2 = round((twopm / twopa * 100), 1) if twopa > 0 else 0.0
        p3 = round((threepm / threepa * 100), 1) if threepa > 0 else 0.0
        efg = round(((fgm + 0.5 * threepm) / fga * 100), 1) if fga > 0 else 0.0

        pps = round(pts / fga, 2) if fga > 0 else 0.00
        tpt_pct = round((pts / total_quality_points * 100), 1) if total_quality_points > 0 else 0.0
        tst_pct = round((fga / total_fg_attempts * 100), 1) if total_fg_attempts > 0 else 0.0

        display_qual = quality_map.get(qual, qual)

        shot_quality_table += f"""
        <tr style="background:{row_colour};">
            <td style="text-align:left; font-weight:bold;">{display_qual}</td>
            <td style="text-align:center;">{twopm}</td>
            <td style="text-align:center;">{twopa}</td>
            <td style="text-align:center;">{p2:.1f}%</td>
            <td style="text-align:center;">{threepm}</td>
            <td style="text-align:center;">{threepa}</td>
            <td style="text-align:center;">{p3:.1f}%</td>
            <td style="text-align:center;">{fgm}</td>
            <td style="text-align:center;">{fga}</td>
            <td style="text-align:center;">{efg:.1f}%</td>
            <td style="text-align:center;">{pts}</td>
            <td style="text-align:center;">{pps:.2f}</td>
            <td style="text-align:center;">{tpt_pct:.1f}%</td>
            <td style="text-align:center;">{tst_pct:.1f}%</td>
        </tr>
        """

        total_2pm += twopm
        total_2pa += twopa
        total_3pm += threepm
        total_3pa += threepa
        total_fgm += fgm
        total_fga += fga
        total_pts += pts

        row_colour = "#FFEDB8;" if row_colour == "#FFFFFF;" else "#FFFFFF;"

    # Totals row
    total_p2 = round((total_2pm / total_2pa * 100), 1) if total_2pa > 0 else 0.0
    total_p3 = round((total_3pm / total_3pa * 100), 1) if total_3pa > 0 else 0.0
    total_efg = round(((total_fgm + 0.5 * total_3pm) / total_fga * 100), 1) if total_fga > 0 else 0.0
    total_pps = round(total_pts / total_fga, 2) if total_fga > 0 else 0.00

    shot_quality_table += f"""
        <tr style="font-weight:bold; background:#f2c74a; text-align:center;">
            <td style="text-align:left;">Total:</td>
            <td>{total_2pm}</td>
            <td>{total_2pa}</td>
            <td>{total_p2:.1f}%</td>
            <td>{total_3pm}</td>
            <td>{total_3pa}</td>
            <td>{total_p3:.1f}%</td>
            <td>{total_fgm}</td>
            <td>{total_fga}</td>
            <td>{total_efg:.1f}%</td>
            <td>{total_pts}</td>
            <td>{total_pps:.2f}</td>
            <td>100%</td>
            <td>100%</td>
        </tr>
    </table>
    </div>
    """

    # === PLAYER STATS TABLE ===
    player_table = """
    <div style="overflow-x: auto;">
        <table border="2" cellpadding="4" cellspacing="0" style="width: max-content; min-width: 100%; border-color: white; border-collapse: collapse; font-family: Arial, sans-serif;">
            <tr style="background:#f2c74a; font-weight:bold; text-align:center;">
                <th style="text-align:left;">P#</th>
                <th style="text-align:left;">Player Name</th>
                <th style="text-align:center;">GP</th>
                <th style="text-align:center;">2PTM</th>
                <th style="text-align:center;">2PTA</th>
                <th style="text-align:center;">2PT%</th>
                <th style="text-align:center;">3PTM</th>
                <th style="text-align:center;">3PTA</th>
                <th style="text-align:center;">3PT%</th>
                <th style="text-align:center;">eFG%</th>
                <th style="text-align:center;">FTM</th>
                <th style="text-align:center;">FTA</th>
                <th style="text-align:center;">FT%</th>
                <th style="text-align:center;">FTF</th>
                <th style="text-align:center;">PTS</th>
                <th style="text-align:center;">PPG</th>
                <th style="text-align:center;">OREB</th>
                <th style="text-align:center;">DREB</th>
                <th style="text-align:center;">PFL</th>
                <th style="text-align:center;">AST</th>
                <th style="text-align:center;">TO</th>
                <th style="text-align:center;">STL</th>
                <th style="text-align:center;">BLK</th>
                <th style="text-align:center;">CHG</th>
                <th style="text-align:center;">DFL</th>
                <th style="text-align:center;">ATR</th>
            </tr>
    """

    c.execute("""
        SELECT p.id, p.number, p.name
        FROM players p
        WHERE p.team_id = ?
        ORDER BY CAST(p.number AS INTEGER)
    """, (team_id,))
    players = c.fetchall()

    row_colour = "#FFFFFF;"

    for p_id, num_str, p_name in players:
        padded_num = str(num_str)

        # Games Played
        c.execute("""
            SELECT COUNT(DISTINCT v.game_id)
            FROM events e
            JOIN videos v ON e.video_id = v.id
            JOIN games g ON v.game_id = g.id
            WHERE e.player_id = ?
              AND g.is_complete = 1
        """, (p_id,))
        gp = c.fetchone()[0] or 0

        if gp == 0:
            continue

        # Player stats
        c.execute("""
            SELECT 
                SUM(CASE WHEN e.type LIKE '2PM%' THEN 1 ELSE 0 END) as twopm,
                SUM(CASE WHEN e.type LIKE '2P%' AND e.type NOT LIKE '2PM%' THEN 1 ELSE 0 END) as twopa_miss,
                SUM(CASE WHEN e.type LIKE '3PM%' THEN 1 ELSE 0 END) as threepm,
                SUM(CASE WHEN e.type LIKE '3P%' AND e.type NOT LIKE '3PM%' THEN 1 ELSE 0 END) as threepa_miss,
                SUM(CASE WHEN e.type LIKE 'FTM%' THEN 1 ELSE 0 END) as ftm,
                SUM(CASE WHEN e.type LIKE 'FT%' AND e.type NOT LIKE 'FTM%' THEN 1 ELSE 0 END) as fta_miss,
                SUM(CASE WHEN e.type LIKE 'ORB%' THEN 1 ELSE 0 END) as oreb,
                SUM(CASE WHEN e.type LIKE 'DRB%' THEN 1 ELSE 0 END) as dreb,
                SUM(CASE WHEN e.type LIKE 'PFL%' THEN 1 ELSE 0 END) as pfl,
                SUM(CASE WHEN e.type LIKE 'AST%' THEN 1 ELSE 0 END) as ast,
                SUM(CASE WHEN e.type LIKE 'TOV%' THEN 1 ELSE 0 END) as tov,
                SUM(CASE WHEN e.type LIKE 'STL%' THEN 1 ELSE 0 END) as stl,
                SUM(CASE WHEN e.type LIKE 'BLK%' THEN 1 ELSE 0 END) as blk,
                SUM(CASE WHEN e.type LIKE 'CHG%' THEN 1 ELSE 0 END) as chg,
                SUM(CASE WHEN e.type LIKE 'DFL%' THEN 1 ELSE 0 END) as dfl
            FROM events e
            JOIN videos v ON e.video_id = v.id
            JOIN games g ON v.game_id = g.id
            WHERE e.player_id = ?
              AND g.is_complete = 1
        """, (p_id,))

        player_totals = c.fetchone() or (0,) * 15
        twopm, twopa_miss, threepm, threepa_miss, ftm, fta_miss, oreb, dreb, pfl, ast, tov, stl, blk, chg, dfl = player_totals

        fga_2p = twopa_miss + twopm
        fga_3p = threepa_miss + threepm
        fta_total = fta_miss + ftm
        fga_total = fga_2p + fga_3p

        fgm = twopm + threepm
        total_pts = (twopm * 2) + (threepm * 3) + ftm

        p2_pct = round((twopm / fga_2p * 100), 1) if fga_2p > 0 else 0.0
        p3_pct = round((threepm / fga_3p * 100), 1) if fga_3p > 0 else 0.0
        ft_pct = round((ftm / fta_total * 100), 1) if fta_total > 0 else 0.0

        fga_total_for_ftf = fga_2p + fga_3p
        ftf = round((fta_total / fga_total_for_ftf), 2) if fga_total_for_ftf > 0 else 0.0

        avg_ppg = round(total_pts / gp, 1) if gp > 0 else 0.0

        efg_pct = round(((fgm + 0.5 * threepm) / fga_total * 100), 1) if fga_total > 0 else 0.0

        atr = round((ast / tov), 2) if tov > 0 else 0.0

        def fmt(val):
            if gp == 0:
                return "0 (0.0)"
            avg = val / gp
            return f"{val} ({avg:.1f})" if val > 0 else "0 (0.0)"

        player_link = scramble_id(p_id, "P")

        player_table += f"""
        <tr style="background:{row_colour};">
            <td style="text-align:left;">{padded_num}</td>
            <td><a href="?code={full_code}&p={player_link}" style="color:#0066cc; text-decoration:underline;">{p_name}</a></td>
            <td style="text-align:center;">{gp}</td>
            <td style="text-align:center;">{twopm}</td>
            <td style="text-align:center;">{fga_2p}</td>
            <td style="text-align:center;">{p2_pct:.1f}%</td>
            <td style="text-align:center;">{threepm}</td>
            <td style="text-align:center;">{fga_3p}</td>
            <td style="text-align:center;">{p3_pct:.1f}%</td>
            <td style="text-align:center;">{efg_pct:.1f}%</td>
            <td style="text-align:center;">{ftm}</td>
            <td style="text-align:center;">{fta_total}</td>
            <td style="text-align:center;">{ft_pct:.1f}%</td>
            <td style="text-align:center;">{ftf:.2f}</td>
            <td style="text-align:center;">{total_pts}</td>
            <td style="text-align:center;">{avg_ppg:.1f}</td>
            <td style="text-align:center;">{fmt(oreb)}</td>
            <td style="text-align:center;">{fmt(dreb)}</td>
            <td style="text-align:center;">{fmt(pfl)}</td>
            <td style="text-align:center;">{fmt(ast)}</td>
            <td style="text-align:center;">{fmt(tov)}</td>
            <td style="text-align:center;">{fmt(stl)}</td>
            <td style="text-align:center;">{fmt(blk)}</td>
            <td style="text-align:center;">{fmt(chg)}</td>
            <td style="text-align:center;">{fmt(dfl)}</td>
            <td style="text-align:center;">{atr:.2f}</td>
        </tr>
        """

        row_colour = "#FFEDB8;" if row_colour == "#FFFFFF;" else "#FFFFFF;"

    player_table += "</table></div>"


    report = f"<h2 style='font-family: Arial, sans-serif;'>"
    report += "<img src='https://raw.githubusercontent.com/CourtTag/courttag-assets/main/CourtTag_Icon_BW.svg' style='width: 80px; height: 80px; vertical-align: middle; margin-right: 4px;'>"
    report += f"Team Report: {season} - {team_name}</h2>"
    report += "<h3 style='font-family: Arial, sans-serif;'>Games Played Summary:</h3>"
    report += game_table
    #report += "<h3 style='font-family: Arial, sans-serif;'>Shot Quality</h3>"
    report += "<br>"
    report += shot_quality_table
    report += "<br>"
    report += loc_table
    report += "<h3 style='font-family: Arial, sans-serif;'>Player Stats - Total (Per Game Avg)</h3>"
    report += player_table

    return report

def generate_quarter_table(conn, game_id: int) -> str:
    """Standalone Period by Period table - exact match to desktop formatting and calcs"""
    c = conn.cursor()
    c.row_factory = lambda cursor, row: tuple(0 if val is None else val for val in row)

    # Get game info
    c.execute("""
        SELECT name, date, location, format, home_team_id, guest_team_id 
        FROM games WHERE id = ?
    """, (game_id,))
    game_row = c.fetchone()
    if not game_row:
        return "<p>Game not found.</p>"

    name, date, location, format_, home_id, guest_id = game_row
    format_ = (format_ or "Q").upper()

    # Define periods
    if format_ == "H":
        period_order = ["H1", "H2", "OT1", "OT2"]
        period_labels = {"H1": "1st Half", "H2": "2nd Half", "OT1": "OT1", "OT2": "OT2"}
    else:
        period_order = ["Q1", "Q2", "Q3", "Q4", "OT1", "OT2"]
        period_labels = {"Q1": "Q1", "Q2": "Q2", "Q3": "Q3", "Q4": "Q4", "OT1": "OT1", "OT2": "OT2"}

    # Get events (same query style as your current game report)
    query = """
        SELECT 
            e.type, e.quarter, e.team_side,
            COALESCE(gr.side, e.team_side) AS effective_side
        FROM events e
        JOIN videos v ON e.video_id = v.id
        LEFT JOIN game_rosters gr 
            ON gr.game_id = v.game_id AND gr.player_id = e.player_id
        WHERE v.game_id = ?
    """
    c.execute(query, (game_id,))
    events = c.fetchall()

    stat_keys = ['2PTM', '2PTA', '3PTM', '3PTA', 'FTM', 'FTA', 'ORB', 'DREB',
                 'PFL', 'AST', 'TO', 'STL', 'BLK', 'CHG', 'DFL']

    def normalize_period(q):
        if not q: return "Q1"
        q = q.strip().upper()
        if "1ST HALF" in q or q in ("H1", "1"): return "H1"
        if "2ND HALF" in q or q in ("H2", "2"): return "H2"
        if q.startswith("Q"): return q[:2]
        if q.startswith("OT"): return q[:3]
        return q

    # Build quarter stats for both teams
    home_quarters = {p: {k: 0 for k in stat_keys} for p in period_order}
    guest_quarters = {p: {k: 0 for k in stat_keys} for p in period_order}

    for e_type, quarter, t_side, effective_side in events:
        if not e_type:
            continue
        side = effective_side if effective_side else t_side
        q_norm = normalize_period(quarter)
        if side == 'home':
            target = home_quarters.get(q_norm)
        elif side == 'guest':
            target = guest_quarters.get(q_norm)
        else:
            continue
        if not target:
            continue

        if e_type.startswith("2PM"):   target['2PTM'] += 1
        elif e_type.startswith("2PA"): target['2PTA'] += 1
        elif e_type.startswith("3PM"): target['3PTM'] += 1
        elif e_type.startswith("3PA"): target['3PTA'] += 1
        elif e_type.startswith("FTM"): target['FTM'] += 1
        elif e_type.startswith("FTA"): target['FTA'] += 1
        elif e_type.startswith("ORB"): target['ORB'] += 1
        elif e_type.startswith("DRB"): target['DREB'] += 1
        elif e_type.startswith("PFL"): target['PFL'] += 1
        elif e_type.startswith("AST"): target['AST'] += 1
        elif e_type.startswith("TOV"): target['TO'] += 1
        elif e_type.startswith("STL"): target['STL'] += 1
        elif e_type.startswith("BLK"): target['BLK'] += 1
        elif e_type.startswith("CHG"): target['CHG'] += 1
        elif e_type.startswith("DFL"): target['DFL'] += 1

    # Build the HTML table
    quarter_table = """
    <table border="2" cellpadding="4" cellspacing="0" style="width: max-content; border-color: white; border-collapse: collapse; font-family: Arial, sans-serif;">
        <tr style='font-weight:bold; background:#75c875;'>
            <th>Period</th>
            <th>Team</th>
            <th style='text-align: center;'>PTS</th>
            <th style='text-align: center;'>2PTM</th><th style='text-align: center;'>2PTA</th><th style='text-align: center;'>2PT%</th>
            <th style='text-align: center;'>3PTM</th><th style='text-align: center;'>3PTA</th><th style='text-align: center;'>3PT%</th>
            <th style='text-align: center;'>eFG%</th>
            <th style='text-align: center;'>SHOTS</th>
            <th style='text-align: center;'>FTM</th><th style='text-align: center;'>FTA</th><th style='text-align: center;'>FT%</th>
            <th style='text-align: center;'>FTF</th>
            <th style='text-align: center;'>ORB</th><th style='text-align: center;'>DREB</th><th style='text-align: center;'>TREB</th>
            <th style='text-align: center;'>PFL</th><th style='text-align: center;'>AST</th><th style='text-align: center;'>TO</th>
            <th style='text-align: center;'>STL</th><th style='text-align: center;'>BLK</th>
            <th style='text-align: center;'>CHG</th><th style='text-align: center;'>DFL</th>
            <th style='text-align: center;'>SCORE</th>
        </tr>
    """

    home_cum_pts = guest_cum_pts = 0

    for period_key in period_order:
        h = home_quarters.get(period_key, {k: 0 for k in stat_keys})
        g = guest_quarters.get(period_key, {k: 0 for k in stat_keys})

        # Skip completely empty periods
        if all(v == 0 for v in h.values()) and all(v == 0 for v in g.values()):
            continue

        # Home calculations
        h_fga = h['2PTM'] + h['2PTA']
        h_three_a = h['3PTM'] + h['3PTA']
        h_fga_total = h_fga + h_three_a
        h_fta = h['FTM'] + h['FTA']
        h_pts = (h['2PTM'] * 2) + (h['3PTM'] * 3) + h['FTM']
        h_p2 = round(h['2PTM'] / h_fga * 100, 1) if h_fga > 0 else 0.0
        h_p3 = round(h['3PTM'] / h_three_a * 100, 1) if h_three_a > 0 else 0.0
        h_efg = round(((h['2PTM'] + h['3PTM']) + 0.5 * h['3PTM']) / h_fga_total * 100, 1) if h_fga_total > 0 else 0.0
        h_ft = round(h['FTM'] / h_fta * 100, 1) if h_fta > 0 else 0.0
        h_ftf = round(h_fta / h_fga_total, 2) if h_fga_total > 0 else 0.0
        h_treb = h['ORB'] + h['DREB']

        # Guest calculations
        g_fga = g['2PTM'] + g['2PTA']
        g_three_a = g['3PTM'] + g['3PTA']
        g_fga_total = g_fga + g_three_a
        g_fta = g['FTM'] + g['FTA']
        g_pts = (g['2PTM'] * 2) + (g['3PTM'] * 3) + g['FTM']
        g_p2 = round(g['2PTM'] / g_fga * 100, 1) if g_fga > 0 else 0.0
        g_p3 = round(g['3PTM'] / g_three_a * 100, 1) if g_three_a > 0 else 0.0
        g_efg = round(((g['2PTM'] + g['3PTM']) + 0.5 * g['3PTM']) / g_fga_total * 100, 1) if g_fga_total > 0 else 0.0
        g_ft = round(g['FTM'] / g_fta * 100, 1) if g_fta > 0 else 0.0
        g_ftf = round(g_fta / g_fga_total, 2) if g_fga_total > 0 else 0.0
        g_treb = g['ORB'] + g['DREB']

        home_cum_pts += h_pts
        guest_cum_pts += g_pts
        display_period = period_labels.get(period_key, period_key)

        quarter_table += f"""
        <tr>
            <td rowspan='2'>{display_period}</td>
            <td>Home</td>
            <td style='text-align: center;'>{h_pts}</td>
            <td style='text-align: center;'>{h['2PTM']}</td><td style='text-align: center;'>{h_fga}</td><td style='text-align: center;'>{h_p2:.1f}%</td>
            <td style='text-align: center;'>{h['3PTM']}</td><td style='text-align: center;'>{h_three_a}</td><td style='text-align: center;'>{h_p3:.1f}%</td>
            <td style='text-align: center;'>{h_efg:.1f}%</td>
            <td style='text-align: center;'>{h_fga_total}</td>
            <td style='text-align: center;'>{h['FTM']}</td><td style='text-align: center;'>{h_fta}</td><td style='text-align: center;'>{h_ft:.1f}%</td>
            <td style='text-align: center;'>{h_ftf:.2f}</td>
            <td style='text-align: center;'>{h['ORB']}</td><td style='text-align: center;'>{h['DREB']}</td><td style='text-align: center;'>{h_treb}</td>
            <td style='text-align: center;'>{h['PFL']}</td><td style='text-align: center;'>{h['AST']}</td><td style='text-align: center;'>{h['TO']}</td>
            <td style='text-align: center;'>{h['STL']}</td><td style='text-align: center;'>{h['BLK']}</td><td style='text-align: center;'>{h['CHG']}</td><td style='text-align: center;'>{h['DFL']}</td>
            <td style='text-align: center;'>{home_cum_pts}</td>
        </tr>
        <tr style='background:#DCFFDC;'>
            <td>Guest</td>
            <td style='text-align: center;'>{g_pts}</td>
            <td style='text-align: center;'>{g['2PTM']}</td><td style='text-align: center;'>{g_fga}</td><td style='text-align: center;'>{g_p2:.1f}%</td>
            <td style='text-align: center;'>{g['3PTM']}</td><td style='text-align: center;'>{g_three_a}</td><td style='text-align: center;'>{g_p3:.1f}%</td>
            <td style='text-align: center;'>{g_efg:.1f}%</td>
            <td style='text-align: center;'>{g_fga_total}</td>
            <td style='text-align: center;'>{g['FTM']}</td><td style='text-align: center;'>{g_fta}</td><td style='text-align: center;'>{g_ft:.1f}%</td>
            <td style='text-align: center;'>{g_ftf:.2f}</td>
            <td style='text-align: center;'>{g['ORB']}</td><td style='text-align: center;'>{g['DREB']}</td><td style='text-align: center;'>{g_treb}</td>
            <td style='text-align: center;'>{g['PFL']}</td><td style='text-align: center;'>{g['AST']}</td><td style='text-align: center;'>{g['TO']}</td>
            <td style='text-align: center;'>{g['STL']}</td><td style='text-align: center;'>{g['BLK']}</td><td style='text-align: center;'>{g['CHG']}</td><td style='text-align: center;'>{g['DFL']}</td>
            <td style='text-align: center;'>{guest_cum_pts}</td>
        </tr>
        """

    quarter_table += "</table><br>"
    return quarter_table


def generate_shot_quality_table_for_game(conn, game_id: int, team_id: int, side_label: str) -> str:
    """Standalone Shot Quality table for ONE specific team in ONE specific game
       Exact match to desktop logic and formatting (green theme)"""

    c = conn.cursor()
    c.row_factory = lambda cursor, row: tuple(0 if val is None else val for val in row)

    # Build display map from global SHOT_QUALITY
    quality_map = {}
    for full_name, code in SHOT_QUALITY:
        display_name = full_name.split('/', 1)[0].strip()
        quality_map[code] = display_name
    quality_map['None'] = 'Not Assigned'

    c.execute("""
        SELECT 
            COALESCE(e.shot_quality, 'None') AS quality,
            SUM(CASE WHEN e.type LIKE '2PM%' THEN 1 ELSE 0 END) AS twopm,
            SUM(CASE WHEN e.type LIKE '2PA%' THEN 1 ELSE 0 END) AS twopa_miss,
            SUM(CASE WHEN e.type LIKE '3PM%' THEN 1 ELSE 0 END) AS threepm,
            SUM(CASE WHEN e.type LIKE '3PA%' THEN 1 ELSE 0 END) AS threepa_miss,
            SUM(CASE WHEN e.type LIKE '2PM%' THEN 2 
                     WHEN e.type LIKE '3PM%' THEN 3 
                     ELSE 0 END) AS points
        FROM events e
        JOIN videos v ON e.video_id = v.id
        JOIN games g ON v.game_id = g.id
        WHERE g.id = ? 
          AND (g.home_team_id = ? OR g.guest_team_id = ?)
          AND (e.type LIKE '2P%' OR e.type LIKE '3P%')
          AND g.is_complete = 1
          AND (
                e.player_id IN (SELECT player_id FROM game_rosters 
                                WHERE game_id = g.id 
                                AND side = CASE WHEN g.home_team_id = ? THEN 'home' ELSE 'guest' END)
             OR (e.player_id IS NULL AND e.team_side = CASE WHEN g.home_team_id = ? THEN 'home' ELSE 'guest' END)
          )
        GROUP BY COALESCE(e.shot_quality, 'None')
        ORDER BY 
            CASE 
                WHEN COALESCE(e.shot_quality, 'None') = 'A' THEN 1
                WHEN COALESCE(e.shot_quality, 'None') = 'B' THEN 2
                WHEN COALESCE(e.shot_quality, 'None') = 'C' THEN 3
                WHEN COALESCE(e.shot_quality, 'None') = 'D' THEN 4
                ELSE 5 
            END
    """, (game_id, team_id, team_id, team_id, team_id))

    quality_rows = c.fetchall()

    total_quality_points = sum(row[5] for row in quality_rows) if quality_rows else 0
    total_fg_attempts = sum((row[1] or 0) + (row[2] or 0) + (row[3] or 0) + (row[4] or 0) for row in quality_rows)

    # <h4>{side_label} Shot Quality Summary</h4>
    html = f"""
    <div style="overflow-x: auto;">
        <table border="2" cellpadding="4" cellspacing="0" style="width: max-content; border-color: white; border-collapse: collapse; font-family: Arial, sans-serif;">
            <tr style="font-weight: bold; background: #75c875;">
                <th style="text-align: left;">Shot Quality</th>
                <th style="text-align: center;">2PTM</th>
                <th style="text-align: center;">2PTA</th>
                <th style="text-align: center;">2PT%</th>
                <th style="text-align: center;">3PTM</th>
                <th style="text-align: center;">3PTA</th>
                <th style="text-align: center;">3PT%</th>
                <th style="text-align: center;">FGM</th>
                <th style="text-align: center;">FGA</th>
                <th style="text-align: center;">eFG%</th>
                <th style="text-align: center;">PTS</th>
                <th style="text-align: center;">PPS</th>
                <th style="text-align: center;">TPT%</th>
                <th style="text-align: center;">TST%</th>
            </tr>
    """

    row_colour = "#FFFFFF;"

    total_2pm = total_2pa = total_3pm = total_3pa = total_fgm = total_fga = total_pts = 0

    for qual, twopm, twopa_miss, threepm, threepa_miss, pts in quality_rows:
        twopm = twopm or 0
        twopa_miss = twopa_miss or 0
        threepm = threepm or 0
        threepa_miss = threepa_miss or 0
        pts = pts or 0

        twopa = twopm + twopa_miss
        threepa = threepm + threepa_miss
        fgm = twopm + threepm
        fga = twopa + threepa

        p2 = round((twopm / twopa * 100), 1) if twopa > 0 else 0.0
        p3 = round((threepm / threepa * 100), 1) if threepa > 0 else 0.0
        efg = round(((fgm + 0.5 * threepm) / fga * 100), 1) if fga > 0 else 0.0

        pps = round(pts / fga, 2) if fga > 0 else 0.00
        tpt_pct = round((pts / total_quality_points * 100), 1) if total_quality_points > 0 else 0.0
        tst_pct = round((fga / total_fg_attempts * 100), 1) if total_fg_attempts > 0 else 0.0

        display_qual = quality_map.get(qual, qual)

        html += f"""
            <tr style="background:{row_colour};">
                <td style="text-align: left; font-weight: bold;">{display_qual}</td>
                <td style="text-align: center;">{twopm}</td>
                <td style="text-align: center;">{twopa}</td>
                <td style="text-align: center;">{p2:.1f}%</td>
                <td style="text-align: center;">{threepm}</td>
                <td style="text-align: center;">{threepa}</td>
                <td style="text-align: center;">{p3:.1f}%</td>
                <td style="text-align: center;">{fgm}</td>
                <td style="text-align: center;">{fga}</td>
                <td style="text-align: center;">{efg:.1f}%</td>
                <td style="text-align: center;">{pts}</td>
                <td style="text-align: center;">{pps:.2f}</td>
                <td style="text-align: center;">{tpt_pct:.1f}%</td>
                <td style="text-align: center;">{tst_pct:.1f}%</td>
            </tr>
        """

        total_2pm += twopm
        total_2pa += twopa
        total_3pm += threepm
        total_3pa += threepa
        total_fgm += fgm
        total_fga += fga
        total_pts += pts

        row_colour = "#DCFFDC;" if row_colour == "#FFFFFF;" else "#FFFFFF;"

    # Totals row
    total_p2 = round((total_2pm / total_2pa * 100), 1) if total_2pa > 0 else 0.0
    total_p3 = round((total_3pm / total_3pa * 100), 1) if total_3pa > 0 else 0.0
    total_efg = round(((total_fgm + 0.5 * total_3pm) / total_fga * 100), 1) if total_fga > 0 else 0.0
    total_pps = round(total_pts / total_fga, 2) if total_fga > 0 else 0.00

    html += f"""
            <tr style="font-weight:bold; background:#75c875;">
                <td style="text-align: left;">Total:</td>
                <td style="text-align: center;">{total_2pm}</td>
                <td style="text-align: center;">{total_2pa}</td>
                <td style="text-align: center;">{total_p2:.1f}%</td>
                <td style="text-align: center;">{total_3pm}</td>
                <td style="text-align: center;">{total_3pa}</td>
                <td style="text-align: center;">{total_p3:.1f}%</td>
                <td style="text-align: center;">{total_fgm}</td>
                <td style="text-align: center;">{total_fga}</td>
                <td style="text-align: center;">{total_efg:.1f}%</td>
                <td style="text-align: center;">{total_pts}</td>
                <td style="text-align: center;">{total_pps:.2f}</td>
                <td style="text-align: center;">100%</td>
                <td style="text-align: center;">100%</td>
            </tr>
        </table>
    </div>
    """
    return html

def generate_game_report(conn, game_id: int) -> str:
    """Clean Game Report with exact desktop possession formula"""
    if not game_id:
        return "<h2>Game not found.</h2>"

    c = conn.cursor()
    c.row_factory = lambda cursor, row: tuple(0 if val is None else val for val in row)

    # Game basic info
    c.execute("""
        SELECT name, date, location, format, home_team_id, guest_team_id 
        FROM games WHERE id = ?
    """, (game_id,))
    game_row = c.fetchone()
    if not game_row:
        return "<h2>Game not found.</h2>"

    name, date, location, format_, home_id, guest_id = game_row

    c.execute("SELECT name FROM teams WHERE id = ?", (home_id,))
    home_row = c.fetchone()
    home_name = home_row[0] if home_row and home_row[0] else f"Team {home_id}"

    c.execute("SELECT name FROM teams WHERE id = ?", (guest_id,))
    guest_row = c.fetchone()
    guest_name = guest_row[0] if guest_row and guest_row[0] else f"Team {guest_id}"

    format_ = (format_ or "Q").upper()

    report = f"""
    <h2>
    <img src='https://raw.githubusercontent.com/CourtTag/courttag-assets/main/CourtTag_Icon_BW.svg' style='width: 80px; height: 80px; vertical-align: middle; margin-right: 4px;'>
    Game Report: {name} - {home_name} (Home) vs {guest_name} (Guest)</h2>
    <p><b>Date:</b> {date} | <b>Location:</b> {location or '—'} | <b>Format:</b> {format_}</p>
    """

    # Events query
    query = """
        SELECT 
            e.type, 
            e.location, 
            e.quarter, 
            e.player_id, 
            e.team_side, 
            p.number, 
            p.name,
            COALESCE(gr.side, e.team_side) as effective_side
        FROM events e
        LEFT JOIN players p ON e.player_id = p.id
        LEFT JOIN game_rosters gr ON e.player_id = gr.player_id AND gr.game_id = ?
        JOIN videos v ON e.video_id = v.id
        WHERE v.game_id = ?
          AND e.type IS NOT NULL
    """
    c.execute(query, (game_id, game_id))
    events = c.fetchall()

    if format_ == "H":
        period_order = ["H1", "H2", "OT1", "OT2"]
        period_labels = {"H1": "1st Half", "H2": "2nd Half", "OT1": "OT1", "OT2": "OT2"}
    else:
        period_order = ["Q1", "Q2", "Q3", "Q4", "OT1", "OT2"]
        period_labels = {"Q1": "Q1", "Q2": "Q2", "Q3": "Q3", "Q4": "Q4", "OT1": "OT1", "OT2": "OT2"}

    stat_keys = ['2PTM', '2PTA', '3PTM', '3PTA', 'FTM', 'FTA', 'ORB', 'DREB', 'PFL', 'AST', 'TO', 'STL', 'BLK', 'CHG', 'DFL']

    def normalize_period(q):
        if not q: return "Q1"
        q = q.strip().upper()
        if "1ST HALF" in q or q in ("H1", "1"): return "H1"
        if "2ND HALF" in q or q in ("H2", "2"): return "H2"
        if q.startswith("Q"): return q[:2]
        if q.startswith("OT"): return q[:3]
        return q

    def team_stats(events, target_side, team_name):
        player_stats = {}
        location_counts = {}
        quarter_stats = {p: {k: 0 for k in stat_keys} for p in period_order}

        for event in events:
            e_type, loc, q, p_id, t_side, num, p_name, effective_side = event
            if effective_side != target_side:
                continue

            key = p_id if p_id else 'unknown'
            if key not in player_stats:
                player_stats[key] = {k: 0 for k in stat_keys}
                player_stats[key]['number'] = num if num else ''
                player_stats[key]['name'] = p_name if p_name else 'Unknown'

            stats = player_stats[key]
            q_norm = normalize_period(q)
            if q_norm in quarter_stats:
                q_stats = quarter_stats[q_norm]
            else:
                continue

            # Exact desktop accumulation - no extra fallback
            if e_type.startswith("2PM"):
                stats['2PTM'] += 1
                q_stats['2PTM'] += 1
            elif e_type.startswith("2PA"):
                stats['2PTA'] += 1
                q_stats['2PTA'] += 1
            elif e_type.startswith("3PM"):
                stats['3PTM'] += 1
                q_stats['3PTM'] += 1
            elif e_type.startswith("3PA"):
                stats['3PTA'] += 1
                q_stats['3PTA'] += 1
            elif e_type.startswith("FTM"):
                stats['FTM'] += 1
                q_stats['FTM'] += 1
            elif e_type.startswith("FTA"):
                stats['FTA'] += 1
                q_stats['FTA'] += 1
            elif e_type.startswith("ORB"):
                stats['ORB'] += 1
                q_stats['ORB'] += 1
            elif e_type.startswith("DRB"):
                stats['DREB'] += 1
                q_stats['DREB'] += 1
            elif e_type.startswith("PFL"):
                stats['PFL'] += 1
                q_stats['PFL'] += 1
            elif e_type.startswith("AST"):
                stats['AST'] += 1
                q_stats['AST'] += 1
            elif e_type.startswith("TOV"):
                stats['TO'] += 1
                q_stats['TO'] += 1
            elif e_type.startswith("STL"):
                stats['STL'] += 1
                q_stats['STL'] += 1
            elif e_type.startswith("BLK"):
                stats['BLK'] += 1
                q_stats['BLK'] += 1
            elif e_type.startswith("CHG"):
                stats['CHG'] += 1
                q_stats['CHG'] += 1
            elif e_type.startswith("DFL"):
                stats['DFL'] += 1
                q_stats['DFL'] += 1

            if loc and loc != '-' and e_type.startswith(('2PA', '2PM', '3PA', '3PM')):
                if loc not in location_counts:
                    location_counts[loc] = {}
                short_code = e_type[:3]
                location_counts[loc][short_code] = location_counts[loc].get(short_code, 0) + 1

        # Build player table
        player_table = """
        <table border="2" cellpadding="4" cellspacing="0" style="width: max-content; border-color: white; border-collapse: collapse; font-family: Arial, sans-serif;">
            <tr style='font-weight:bold; background:#75c875;'>
                <th>P#</th><th>Player Name</th>
                <th style='text-align: center;'>2PTM</th><th style='text-align: center;'>2PTA</th><th style='text-align: center;'>2PT%</th>
                <th style='text-align: center;'>3PTM</th><th style='text-align: center;'>3PTA</th><th style='text-align: center;'>3PT%</th>
                <th style='text-align: center;'>eFG%</th>
                <th style='text-align: center;'>FTM</th><th style='text-align: center;'>FTA</th><th style='text-align: center;'>FT%</th>
                <th style='text-align: center;'>FTF</th>
                <th style='text-align: center;'>PTS</th>
                <th style='text-align: center;'>ORB</th><th style='text-align: center;'>DREB</th><th style='text-align: center;'>TREB</th>
                <th style='text-align: center;'>PFL</th><th style='text-align: center;'>AST</th><th style='text-align: center;'>TO</th>
                <th style='text-align: center;'>STL</th><th style='text-align: center;'>BLK</th>
                <th style='text-align: center;'>CHG</th><th style='text-align: center;'>DFL</th>
                <th style='text-align: center;'>ATR</th>
            </tr>
        """
        row_colour = "#FFFFFF;"
        for key, stats in sorted(player_stats.items(), key=lambda x: (int(x[1]['number'] or 999) if str(x[1]['number']).strip().isdigit() else 999, x[1]['name'])):
            num = str(stats['number']) if stats['number'] is not None else ''
            fga_2pt = stats['2PTM'] + stats['2PTA']
            fga_3pt = stats['3PTM'] + stats['3PTA']
            fga_total = fga_2pt + fga_3pt
            fta_total = stats['FTM'] + stats['FTA']

            p2 = (stats['2PTM'] / fga_2pt * 100) if fga_2pt > 0 else 0.0
            p3 = (stats['3PTM'] / fga_3pt * 100) if fga_3pt > 0 else 0.0
            ft = (stats['FTM'] / fta_total * 100) if fta_total > 0 else 0.0
            ftf = (fta_total / fga_total) if fga_total > 0 else 0.0
            efg_pct = ((stats['2PTM'] + stats['3PTM']) + 0.5 * stats['3PTM']) / fga_total * 100 if fga_total > 0 else 0.0
            pts = (stats['2PTM'] * 2) + (stats['3PTM'] * 3) + stats['FTM']
            treb = stats['ORB'] + stats['DREB']
            atr = (stats['AST'] / stats['TO']) if stats['TO'] > 0 else 0.0

            player_table += f"""
            <tr style='background:{row_colour}'>
                <td style='text-align: center;'>{num}</td>
                <td>{stats['name']}</td>
                <td style='text-align: center;'>{stats['2PTM']}</td><td style='text-align: center;'>{fga_2pt}</td><td style='text-align: center;'>{p2:.1f}%</td>
                <td style='text-align: center;'>{stats['3PTM']}</td><td style='text-align: center;'>{fga_3pt}</td><td style='text-align: center;'>{p3:.1f}%</td>
                <td style='text-align: center;'>{efg_pct:.1f}%</td>
                <td style='text-align: center;'>{stats['FTM']}</td><td style='text-align: center;'>{fta_total}</td><td style='text-align: center;'>{ft:.1f}%</td>
                <td style='text-align: center;'>{ftf:.2f}</td>
                <td style='text-align: center;'>{pts}</td>
                <td style='text-align: center;'>{stats['ORB']}</td><td style='text-align: center;'>{stats['DREB']}</td><td style='text-align: center;'>{treb}</td>
                <td style='text-align: center;'>{stats['PFL']}</td><td style='text-align: center;'>{stats['AST']}</td><td style='text-align: center;'>{stats['TO']}</td>
                <td style='text-align: center;'>{stats['STL']}</td><td style='text-align: center;'>{stats['BLK']}</td>
                <td style='text-align: center;'>{stats['CHG']}</td><td style='text-align: center;'>{stats['DFL']}</td>
                <td style='text-align: center;'>{atr:.2f}</td>
            </tr>
            """
            row_colour = "#DCFFDC;" if row_colour == "#FFFFFF;" else "#FFFFFF;"

        # Exact desktop totals + possession
        total = {k: sum(s[k] for s in player_stats.values()) for k in stat_keys}
        total_fga = total['2PTM'] + total['2PTA']
        total_three_a = total['3PTM'] + total['3PTA']
        total_fta = total['FTM'] + total['FTA']
        pts_total = (total['2PTM'] * 2) + (total['3PTM'] * 3) + total['FTM']
        total_treb = total['ORB'] + total['DREB']
        total_oreb = total['ORB']

        pos = round((total_three_a + total_fga) + (total_fta * 0.44) - total_oreb + total['TO'])
        ppp = round(pts_total / pos, 2) if pos > 0 else 0.00
        tov_ratio = round((total['TO'] / pos * 100), 1) if pos > 0 else 0.0

        total_efg = ((total['2PTM'] + total['3PTM']) + 0.5 * total['3PTM']) / (total_fga + total_three_a) * 100 if (total_fga + total_three_a) > 0 else 0.0
        total_ftf = (total_fta / (total_fga + total_three_a)) if (total_fga + total_three_a) > 0 else 0.0

        # Shot Location table
        loc_table = """
        <table border='0' cellpadding='0'><tr><td valign='top'>
                <table border="2" cellpadding="4" cellspacing="0" style="width: max-content; border-color: white; border-collapse: collapse; font-family: Arial, sans-serif;">
                    <tr style='font-weight:bold; background:#75c875;'>
                        <th style='text-align: left;'>Shot Location (FG Only)</th>
                        <th style='text-align: center;'>2PTM</th><th style='text-align: center;'>2PTA</th><th style='text-align: center;'>2PT%</th>
                        <th style='text-align: center;'>3PTM</th><th style='text-align: center;'>3PTA</th><th style='text-align: center;'>3PT%</th>
                    </tr>
        """
        row_colour = "#FFFFFF;"
        t2pm = t2pa = t3pm = t3pa = 0
        for loc in sorted(location_counts.keys()):
            counts = location_counts.get(loc, {})
            twopm = counts.get('2PM', 0)
            twopa = counts.get('2PA', 0) + twopm
            threepm = counts.get('3PM', 0)
            threepa = counts.get('3PA', 0) + threepm

            p2_loc = twopm / twopa * 100 if twopa > 0 else 0.0
            p3_loc = threepm / threepa * 100 if threepa > 0 else 0.0

            loc_table += f"""
            <tr style='background:{row_colour}'>
                <td>{loc}</td>
                <td style='text-align: center;'>{twopm}</td><td style='text-align: center;'>{twopa}</td><td style='text-align: center;'>{p2_loc:.1f}%</td>
                <td style='text-align: center;'>{threepm}</td><td style='text-align: center;'>{threepa}</td><td style='text-align: center;'>{p3_loc:.1f}%</td>
            </tr>
            """
            row_colour = "#DCFFDC;" if row_colour == "#FFFFFF;" else "#FFFFFF;"
            t2pm += twopm
            t2pa += twopa
            t3pm += threepm
            t3pa += threepa

        total_p2 = t2pm / t2pa * 100 if t2pa > 0 else 0.0
        total_p3 = t3pm / t3pa * 100 if t3pa > 0 else 0.0

        loc_table += f"""
                    <tr style='font-weight:bold; background:#75c875;'>
                        <td style='text-align: right;'>Total:</td>
                        <td style='text-align: center;'>{t2pm}</td><td style='text-align: center;'>{t2pa}</td><td style='text-align: center;'>{total_p2:.1f}%</td>
                        <td style='text-align: center;'>{t3pm}</td><td style='text-align: center;'>{t3pa}</td><td style='text-align: center;'>{total_p3:.1f}%</td>
                    </tr>
                </table>
        </td><td valign='top'>
            <img src="https://raw.githubusercontent.com/CourtTag/courttag-assets/main/Shot_Location_Diagram_v1.svg" alt="Shot Location Diagram" style="width: 300px; height: 240px;">
        </td></tr></table>
        """

        return (player_table, loc_table, quarter_stats, pos, ppp, tov_ratio,
                total_oreb, total['DREB'], total_treb, total_efg, total_ftf,
                (total['AST'] / total['TO']) if total['TO'] > 0 else 0.0)

    # Run for both teams
    home_player_table, home_loc_table, home_quarters, home_pos, home_ppp, home_tov_ratio, home_oreb, home_dreb, home_treb, home_efg, home_ftf, home_atr = team_stats(events, 'home', home_name)
    guest_player_table, guest_loc_table, guest_quarters, guest_pos, guest_ppp, guest_tov_ratio, guest_oreb, guest_dreb, guest_treb, guest_efg, guest_ftf, guest_atr = team_stats(events, 'guest', guest_name)

    # Rebounding percentages
    home_oreb_pct = round((home_oreb / (home_oreb + guest_dreb) * 100), 1) if (home_oreb + guest_dreb) > 0 else 0.0
    home_dreb_pct = round((home_dreb / (home_dreb + guest_oreb) * 100), 1) if (home_dreb + guest_oreb) > 0 else 0.0
    home_reb_pct = round((home_treb / (home_treb + guest_treb) * 100), 1) if (home_treb + guest_treb) > 0 else 0.0

    guest_oreb_pct = round((guest_oreb / (guest_oreb + home_dreb) * 100), 1) if (guest_oreb + home_dreb) > 0 else 0.0
    guest_dreb_pct = round((guest_dreb / (guest_dreb + home_oreb) * 100), 1) if (guest_dreb + home_oreb) > 0 else 0.0
    guest_reb_pct = round((guest_treb / (guest_treb + home_treb) * 100), 1) if (guest_treb + home_treb) > 0 else 0.0

    # Generate the game's quarter table
    quarter_table_html = generate_quarter_table(conn, game_id)
    report += quarter_table_html

    # Home Advanced Stats
    report += f"<h3>Home Team Advanced Stats: {home_name}</h3>"
    report += f"""
    <table border="2" cellpadding="4" cellspacing="0" style="width: max-content; border-color: white; border-collapse: collapse; font-family: Arial, sans-serif;">
        <tr style='background:#75c875; font-weight:bold;'>
            <th>POS</th><th>PPP</th><th>eFG%</th><th>TOR</th><th>OREB%</th><th>DREB%</th><th>TREB%</th><th>FTF</th><th>ATR</th>
        </tr>
        <tr style='background:#DCFFDC;'>
            <td style='text-align:center;'>{home_pos}</td>
            <td style='text-align:center;'>{home_ppp:.2f}</td>
            <td style='text-align:center;'>{home_efg:.1f}%</td>
            <td style='text-align:center;'>{home_tov_ratio}%</td>
            <td style='text-align:center;'>{home_oreb_pct}%</td>
            <td style='text-align:center;'>{home_dreb_pct}%</td>
            <td style='text-align:center;'>{home_reb_pct}%</td>
            <td style='text-align:center;'>{home_ftf:.2f}</td>
            <td style='text-align:center;'>{home_atr:.2f}</td>
        </tr>
    </table>
    """
    report += home_player_table

    home_shot_quality = generate_shot_quality_table_for_game(conn, game_id, home_id, "Home")
    report += home_shot_quality
    report += home_loc_table

    # Guest Advanced Stats
    report += f"<br><h3>Guest Team Advanced Stats: {guest_name}</h3>"
    report += f"""
    <table border="2" cellpadding="4" cellspacing="0" style="width: max-content; border-color: white; border-collapse: collapse; font-family: Arial, sans-serif;">
        <tr style='background:#75c875; font-weight:bold;'>
            <th>POS</th><th>PPP</th><th>eFG%</th><th>TOR</th><th>OREB%</th><th>DREB%</th><th>TREB%</th><th>FTF</th><th>ATR</th>
        </tr>
        <tr style='background:#DCFFDC;'>
            <td style='text-align:center;'>{guest_pos}</td>
            <td style='text-align:center;'>{guest_ppp:.2f}</td>
            <td style='text-align:center;'>{guest_efg:.1f}%</td>
            <td style='text-align:center;'>{guest_tov_ratio}%</td>
            <td style='text-align:center;'>{guest_oreb_pct}%</td>
            <td style='text-align:center;'>{guest_dreb_pct}%</td>
            <td style='text-align:center;'>{guest_reb_pct}%</td>
            <td style='text-align:center;'>{guest_ftf:.2f}</td>
            <td style='text-align:center;'>{guest_atr:.2f}</td>
        </tr>
    </table>
    """
    report += guest_player_table

    guest_shot_quality = generate_shot_quality_table_for_game(conn, game_id, guest_id, "Guest")
    report += guest_shot_quality
    report += guest_loc_table

    report += f'<p style="margin-top:40px; font-size:18px;"><a href="?code={st.query_params.get("code", "")}" style="color:#0066cc;">← Back to Team Report</a></p>'

    return report

def generate_player_report(conn, player_id: int) -> str:
    """Full Player Report for Web Viewer"""
    if not player_id:
        return "<h2>Player not found.</h2>"

    c = conn.cursor()
    c.row_factory = lambda cursor, row: tuple(0 if val is None else val for val in row)

    # Player basic info
    c.execute("""
        SELECT p.name, p.number, t.name AS team_name, t.season
        FROM players p
        JOIN teams t ON p.team_id = t.id
        WHERE p.id = ?
    """, (player_id,))
    player_info = c.fetchone()
    if not player_info:
        return "<h2>Player not found.</h2>"

    p_name, p_number, team_name, season = player_info
    padded_num = str(p_number)

    report = f"<h2>"
    report += f"<img src='https://raw.githubusercontent.com/CourtTag/courttag-assets/main/CourtTag_Icon_BW.svg' style='width: 80px; height: 80px; vertical-align: middle; margin-right: 4px;'>"
    report += f"Player Report: "
    if season:
        report += f"{season} - {team_name} - #{padded_num} - {p_name}"
    else:
        report += f"{team_name} - #{padded_num} - {p_name}"

    report += f"</h2>"

    # ====================== GAMES PLAYED TABLE ======================
    c.execute("""
        SELECT g.date, g.name, 
               CASE WHEN g.home_team_id = p.team_id THEN t_guest.name ELSE t_home.name END AS opponent,
               g.id
        FROM games g
        JOIN game_rosters gr ON gr.game_id = g.id
        JOIN players p ON gr.player_id = p.id
        LEFT JOIN teams t_home ON g.home_team_id = t_home.id
        LEFT JOIN teams t_guest ON g.guest_team_id = t_guest.id
        WHERE p.id = ?
          AND g.is_complete = 1
        ORDER BY g.date ASC
    """, (player_id,))
    games = c.fetchall()

    num_games = len(games)
    if num_games == 0:
        report += "<p><i>No complete games played by this player yet.</i></p>"
        report += f'<p><a href="?code={st.query_params.get("code", "")}" style="color:#0066cc; font-size:18px;">← Back to Team Report</a></p>'
        return report

    game_table = """
    <h3>Player Game-by-Game Stats</h3>
    <div style="overflow-x: auto;">
        <table border="2" cellpadding="4" cellspacing="0" style="width: max-content; min-width: 100%; border-color: white; border-collapse: collapse; font-family: Arial, sans-serif;">
            <tr style="background:#d0e0ff; font-weight:bold; text-align:center;">
                <th style="text-align:left;">Game Date</th>
                <th style="text-align:left;">Name</th>
                <th style="text-align:left;">Opponent</th>
                <th>2PTM</th><th>2PTA</th><th>2PT%</th>
                <th>3PTM</th><th>3PTA</th><th>3PT%</th>
                <th>eFG%</th>
                <th>FTM</th><th>FTA</th><th>FT%</th>
                <th>FTF</th>
                <th>PTS</th><th>ORB</th><th>DREB</th><th>TREB</th>
                <th>PFL</th><th>AST</th><th>TO</th><th>STL</th><th>BLK</th><th>CHG</th><th>DFL</th>
                <th>ATR</th>
            </tr>
    """

    row_colour = "#FFFFFF;"
    total_pts = total_2pm = total_2pa = total_3pm = total_3pa = total_ftm = total_fta = 0
    total_orb = total_dreb = total_treb = total_pfl = total_ast = total_tov = total_stl = total_blk = total_chg = total_dfl = 0

    for g_date, g_name, opponent, g_id in games:
        c.execute("""
            SELECT 
                SUM(CASE WHEN e.type LIKE '2PM%' THEN 2 WHEN e.type LIKE '3PM%' THEN 3 WHEN e.type LIKE 'FTM%' THEN 1 ELSE 0 END) as pts,
                SUM(CASE WHEN e.type LIKE '2PM%' THEN 1 ELSE 0 END) as twopm,
                SUM(CASE WHEN e.type LIKE '2P%' AND e.type NOT LIKE '2PM%' THEN 1 ELSE 0 END) as missed_2pa,
                SUM(CASE WHEN e.type LIKE '3PM%' THEN 1 ELSE 0 END) as threepm,
                SUM(CASE WHEN e.type LIKE '3P%' AND e.type NOT LIKE '3PM%' THEN 1 ELSE 0 END) as missed_3pa,
                SUM(CASE WHEN e.type LIKE 'FTM%' THEN 1 ELSE 0 END) as ftm,
                SUM(CASE WHEN e.type LIKE 'FT%' AND e.type NOT LIKE 'FTM%' THEN 1 ELSE 0 END) as missed_fta,
                SUM(CASE WHEN e.type LIKE 'ORB%' THEN 1 ELSE 0 END) as oreb,
                SUM(CASE WHEN e.type LIKE 'DRB%' THEN 1 ELSE 0 END) as dreb,
                SUM(CASE WHEN e.type LIKE 'PFL%' THEN 1 ELSE 0 END) as pfl,
                SUM(CASE WHEN e.type LIKE 'AST%' THEN 1 ELSE 0 END) as ast,
                SUM(CASE WHEN e.type LIKE 'TOV%' THEN 1 ELSE 0 END) as tov,
                SUM(CASE WHEN e.type LIKE 'STL%' THEN 1 ELSE 0 END) as stl,
                SUM(CASE WHEN e.type LIKE 'BLK%' THEN 1 ELSE 0 END) as blk,
                SUM(CASE WHEN e.type LIKE 'CHG%' THEN 1 ELSE 0 END) as chg,
                SUM(CASE WHEN e.type LIKE 'DFL%' THEN 1 ELSE 0 END) as dfl
            FROM events e
            JOIN videos v ON e.video_id = v.id
            WHERE e.player_id = ? AND v.game_id = ?
        """, (player_id, g_id))

        stats = c.fetchone() or (0,) * 16
        stats = tuple(0 if x is None else x for x in stats)

        pts, twopm, missed_2pa, threepm, missed_3pa, ftm, missed_fta, oreb, dreb, pfl, ast, tov, stl, blk, chg, dfl = stats

        fga_2pt = twopm + missed_2pa
        fga_3pt = threepm + missed_3pa
        fga_total = fga_2pt + fga_3pt
        fta_total = ftm + missed_fta

        p2_pct = round((twopm / fga_2pt * 100), 1) if fga_2pt > 0 else 0.0
        p3_pct = round((threepm / fga_3pt * 100), 1) if fga_3pt > 0 else 0.0
        ft_pct = round((ftm / fta_total * 100), 1) if fta_total > 0 else 0.0
        ftf = round((fta_total / fga_total), 2) if fga_total > 0 else 0.0

        efg_pct = round((twopm + threepm + 0.5 * threepm) / fga_total * 100, 1) if fga_total > 0 else 0.0
        treb = oreb + dreb
        atr = round((ast / tov), 2) if tov > 0 else 0.0

        game_table += f"""
        <tr style="background:{row_colour}; text-align:center;">
            <td style="text-align:left;">{g_date}</td>
            <td style="text-align:left;">{g_name or ""}</td>
            <td style="text-align:left;">{opponent}</td>
            <td>{twopm}</td><td>{fga_2pt}</td><td>{p2_pct:.1f}%</td>
            <td>{threepm}</td><td>{fga_3pt}</td><td>{p3_pct:.1f}%</td>
            <td>{efg_pct:.1f}%</td>
            <td>{ftm}</td><td>{fta_total}</td><td>{ft_pct:.1f}%</td>
            <td>{ftf:.2f}</td>
            <td>{pts}</td><td>{oreb}</td><td>{dreb}</td><td>{treb}</td>
            <td>{pfl}</td><td>{ast}</td><td>{tov}</td><td>{stl}</td><td>{blk}</td><td>{chg}</td><td>{dfl}</td>
            <td>{atr:.2f}</td>
        </tr>
        """

        if row_colour == "#FFFFFF;":
            row_colour = "#e9f0f0;"
        else:
            row_colour = "#FFFFFF;"

        # Accumulate
        total_pts += pts
        total_2pm += twopm
        total_2pa += fga_2pt
        total_3pm += threepm
        total_3pa += fga_3pt
        total_ftm += ftm
        total_fta += fta_total
        total_orb += oreb
        total_dreb += dreb
        total_treb += treb
        total_pfl += pfl
        total_ast += ast
        total_tov += tov
        total_stl += stl
        total_blk += blk
        total_chg += chg
        total_dfl += dfl

    # Totals row
    total_fga_all = total_2pa + total_3pa
    total_fgm_all = total_2pm + total_3pm
    total_p2 = round(total_2pm / total_2pa * 100, 1) if total_2pa > 0 else 0.0
    total_p3 = round(total_3pm / total_3pa * 100, 1) if total_3pa > 0 else 0.0
    total_ft = round(total_ftm / total_fta * 100, 1) if total_fta > 0 else 0.0
    total_ftf = round(total_fta / total_fga_all, 2) if total_fga_all > 0 else 0.0
    total_efg_pct = round((total_fgm_all + 0.5 * total_3pm) / total_fga_all * 100, 1) if total_fga_all > 0 else 0.0
    avg_atr = round(total_ast / total_tov, 2) if total_tov > 0 else 0.0

    game_table += f"""
        <tr style="font-weight:bold; background:#d0e0ff; text-align:center;">
            <td style="text-align:left;">Games Played:</td>
            <td style="text-align:center;">{num_games}</td><td style="text-align:right;">Totals:</td>
            <td>{total_2pm}</td><td>{total_2pa}</td><td>{total_p2:.1f}%</td>
            <td>{total_3pm}</td><td>{total_3pa}</td><td>{total_p3:.1f}%</td>
            <td>{total_efg_pct:.1f}%</td>
            <td>{total_ftm}</td><td>{total_fta}</td><td>{total_ft:.1f}%</td>
            <td>{total_ftf:.2f}</td>
            <td>{total_pts}</td><td>{total_orb}</td><td>{total_dreb}</td><td>{total_treb}</td>
            <td>{total_pfl}</td><td>{total_ast}</td><td>{total_tov}</td>
            <td>{total_stl}</td><td>{total_blk}</td><td>{total_chg}</td><td>{total_dfl}</td>
            <td>{avg_atr:.2f}</td>
        </tr>
    </table>
    </div>
    """

    # ====================== SHOT LOCATIONS ======================
    c.execute("""
        SELECT e.location,
               SUM(CASE WHEN e.type LIKE '2PM%' THEN 1 ELSE 0 END) as twopm,
               SUM(CASE WHEN e.type LIKE '2P%' AND e.type NOT LIKE '2PM%' THEN 1 ELSE 0 END) as missed_2pa,
               SUM(CASE WHEN e.type LIKE '3PM%' THEN 1 ELSE 0 END) as threepm,
               SUM(CASE WHEN e.type LIKE '3P%' AND e.type NOT LIKE '3PM%' THEN 1 ELSE 0 END) as missed_3pa
        FROM events e
        JOIN videos v ON e.video_id = v.id
        JOIN games g ON v.game_id = g.id
        WHERE e.player_id = ?
          AND e.location IS NOT NULL
          AND e.location != '-'
          AND (e.type LIKE '2P%' OR e.type LIKE '3P%')
          AND g.is_complete = 1
        GROUP BY e.location
        ORDER BY e.location
    """, (player_id,))
    loc_rows = c.fetchall()

    loc_table = """
    <table cellpadding="4" cellspacing="0"><tr><td valign='top'>
         <table border="2" cellpadding="4" cellspacing="0" style="border-color: white; border-collapse: collapse; font-family: Arial, sans-serif;">
             <tr style="background:#d0e0ff; font-weight:bold; text-align:center;">
                 <th style="text-align:left;">Shot Location (FG Only)</th>
                 <th>2PTM</th><th>2PTA</th><th>2PT%</th>
                 <th>3PTM</th><th>3PTA</th><th>3PT%</th>
             </tr>
    """

    loc_total_2pm = loc_total_2pa = loc_total_3pm = loc_total_3pa = 0
    row_colour = "#FFFFFF;"

    for loc, twopm, missed_2pa, threepm, missed_3pa in loc_rows:
        twopa = twopm + missed_2pa
        threepa = threepm + missed_3pa
        p2 = round((twopm / twopa * 100), 1) if twopa > 0 else 0.0
        p3 = round((threepm / threepa * 100), 1) if threepa > 0 else 0.0

        loc_table += f"""
            <tr style="background:{row_colour}; text-align:center;">
                <td style="text-align:left;">{loc}</td>
                <td>{twopm}</td>
                <td>{twopa}</td>
                <td>{p2:.1f}%</td>
                <td>{threepm}</td>
                <td>{threepa}</td>
                <td>{p3:.1f}%</td>
            </tr>
        """

        loc_total_2pm += twopm
        loc_total_2pa += twopa
        loc_total_3pm += threepm
        loc_total_3pa += threepa

        row_colour = "#e9f0f0;" if row_colour == "#FFFFFF;" else "#FFFFFF;"

    loc_total_p2 = round((loc_total_2pm / loc_total_2pa * 100), 1) if loc_total_2pa > 0 else 0.0
    loc_total_p3 = round((loc_total_3pm / loc_total_3pa * 100), 1) if loc_total_3pa > 0 else 0.0

    loc_table += f"""
            <tr style="font-weight:bold; background:#d0e0ff; text-align:center;">
                <td style="text-align:right;">Total:</td>
                <td>{loc_total_2pm}</td>
                <td>{loc_total_2pa}</td>
                <td>{loc_total_p2:.1f}%</td>
                <td>{loc_total_3pm}</td>
                <td>{loc_total_3pa}</td>
                <td>{loc_total_p3:.1f}%</td>
            </tr>
        </table>
    </td><td valign='top' align='left'>
        <!-- Shot Location Image -->
          <img src="https://raw.githubusercontent.com/CourtTag/courttag-assets/main/Shot_Location_Diagram_v1.svg" alt="Shot Location Diagram" style="width: 300px; height: 240px;">
    </td></tr></table>
    """

    # ====================== SHOT QUALITY ======================
    quality_map = {}
    for full_name, code in SHOT_QUALITY:
        display_name = full_name.split('/', 1)[0].strip()
        quality_map[code] = display_name
    quality_map['None'] = 'Not Assigned'

    c.execute("""
        SELECT 
            COALESCE(e.shot_quality, 'None') AS quality,
            SUM(CASE WHEN e.type LIKE '2PM%' THEN 1 ELSE 0 END) AS twopm,
            SUM(CASE WHEN e.type LIKE '2PA%' THEN 1 ELSE 0 END) AS twopa_miss,
            SUM(CASE WHEN e.type LIKE '3PM%' THEN 1 ELSE 0 END) AS threepm,
            SUM(CASE WHEN e.type LIKE '3PA%' THEN 1 ELSE 0 END) AS threepa_miss,
            SUM(CASE WHEN e.type LIKE '2PM%' THEN 2 
                     WHEN e.type LIKE '3PM%' THEN 3 
                     ELSE 0 END) AS points
        FROM events e
        JOIN videos v ON e.video_id = v.id
        JOIN games g ON v.game_id = g.id
        WHERE e.player_id = ?
          AND (e.type LIKE '2P%' OR e.type LIKE '3P%')
          AND g.is_complete = 1
        GROUP BY COALESCE(e.shot_quality, 'None')
        ORDER BY 
            CASE 
                WHEN COALESCE(e.shot_quality, 'None') = 'A' THEN 1
                WHEN COALESCE(e.shot_quality, 'None') = 'B' THEN 2
                WHEN COALESCE(e.shot_quality, 'None') = 'C' THEN 3
                WHEN COALESCE(e.shot_quality, 'None') = 'D' THEN 4
                ELSE 5 
            END
    """, (player_id,))

    quality_rows = c.fetchall()

    # Calculate totals for percentages
    total_quality_points = sum(row[5] for row in quality_rows) if quality_rows else 0
    total_fg_attempts = sum((row[1] or 0) + (row[2] or 0) + (row[3] or 0) + (row[4] or 0) for row in quality_rows)

    player_shot_quality_table = """
    <div style="overflow-x: auto;">
        <table border="2" cellpadding="4" cellspacing="0" style="width: max-content; border-color: white; border-collapse: collapse; font-family: Arial, sans-serif;">
            <tr style="background:#d0e0ff; font-weight:bold; text-align:center;">
                <th style="text-align:left;">Shot Quality</th>
                <th>2PTM</th><th>2PTA</th><th>2PT%</th>
                <th>3PTM</th><th>3PTA</th><th>3PT%</th>
                <th>FGM</th><th>FGA</th><th>eFG%</th>
                <th>PTS</th><th>PPS</th>
                <th>TPT%</th><th>TST%</th>
            </tr>
    """

    row_colour = "#FFFFFF;"
    total_2pm = total_2pa = total_3pm = total_3pa = total_fgm = total_fga = total_pts = 0

    for qual, twopm, twopa_miss, threepm, threepa_miss, pts in quality_rows:
        twopm = twopm or 0
        twopa_miss = twopa_miss or 0
        threepm = threepm or 0
        threepa_miss = threepa_miss or 0
        pts = pts or 0

        twopa = twopm + twopa_miss
        threepa = threepm + threepa_miss
        fgm = twopm + threepm
        fga = twopa + threepa

        p2 = round((twopm / twopa * 100), 1) if twopa > 0 else 0.0
        p3 = round((threepm / threepa * 100), 1) if threepa > 0 else 0.0
        efg = round(((fgm + 0.5 * threepm) / fga * 100), 1) if fga > 0 else 0.0
        pps = round(pts / fga, 2) if fga > 0 else 0.00

        tpt_pct = round((pts / total_quality_points * 100), 1) if total_quality_points > 0 else 0.0
        tst_pct = round((fga / total_fg_attempts * 100), 1) if total_fg_attempts > 0 else 0.0

        display_qual = quality_map.get(qual, qual)

        player_shot_quality_table += f"""
        <tr style="background:{row_colour};">
            <td style="text-align:left; font-weight:bold;">{display_qual}</td>
            <td style="text-align:center;">{twopm}</td>
            <td style="text-align:center;">{twopa}</td>
            <td style="text-align:center;">{p2:.1f}%</td>
            <td style="text-align:center;">{threepm}</td>
            <td style="text-align:center;">{threepa}</td>
            <td style="text-align:center;">{p3:.1f}%</td>
            <td style="text-align:center;">{fgm}</td>
            <td style="text-align:center;">{fga}</td>
            <td style="text-align:center;">{efg:.1f}%</td>
            <td style="text-align:center;">{pts}</td>
            <td style="text-align:center;">{pps:.2f}</td>
            <td style="text-align:center;">{tpt_pct:.1f}%</td>
            <td style="text-align:center;">{tst_pct:.1f}%</td>
        </tr>
        """

        total_2pm += twopm
        total_2pa += twopa
        total_3pm += threepm
        total_3pa += threepa
        total_fgm += fgm
        total_fga += fga
        total_pts += pts

        row_colour = "#e9f0f0;" if row_colour == "#FFFFFF;" else "#FFFFFF;"

    # Totals row
    total_p2 = round((total_2pm / total_2pa * 100), 1) if total_2pa > 0 else 0.0
    total_p3 = round((total_3pm / total_3pa * 100), 1) if total_3pa > 0 else 0.0
    total_efg = round(((total_fgm + 0.5 * total_3pm) / total_fga * 100), 1) if total_fga > 0 else 0.0
    total_pps = round(total_pts / total_fga, 2) if total_fga > 0 else 0.00
    total_tpt_pct = 100.0
    total_tst_pct = 100.0

    player_shot_quality_table += f"""
        <tr style="font-weight:bold; background:#d0e0ff; text-align:center;">
            <td style="text-align:left;">Total:</td>
            <td>{total_2pm}</td>
            <td>{total_2pa}</td>
            <td>{total_p2:.1f}%</td>
            <td>{total_3pm}</td>
            <td>{total_3pa}</td>
            <td>{total_p3:.1f}%</td>
            <td>{total_fgm}</td>
            <td>{total_fga}</td>
            <td>{total_efg:.1f}%</td>
            <td>{total_pts}</td>
            <td>{total_pps:.2f}</td>
            <td>{total_tpt_pct:.1f}%</td>
            <td>{total_tst_pct:.1f}%</td>
        </tr>
    </table>
    </div>
    """
    report += game_table
    report += "<br>"
    report += player_shot_quality_table
    report += "<br>"
    report += loc_table

    # Back link
    report += f'<p><a href="?code={st.query_params.get("code", "")}" style="color:#0066cc; font-size:18px;">← Back to Team Report</a></p>'

    return report


# ====================== GITHUB DB LOADING (Clean) ======================
full_code = st.query_params.get("code")
game_code = st.query_params.get("g")
player_code = st.query_params.get("p")

if not full_code:
    st.error("Missing code in the URL.")
    st.stop()

coach_code = full_code.split('-')[0] if '-' in full_code else full_code
db_filename = f"{coach_code}.db"

# Get GitHub config from secrets
try:
    GITHUB_TOKEN = st.secrets["github"]["token"]
    GITHUB_OWNER = st.secrets["github"]["owner"]
    GITHUB_REPO = st.secrets["github"]["repo"]
except Exception as e:
    st.error("GitHub configuration error. Please contact the developer.")
    st.stop()

# Authenticated request
url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{db_filename}"

headers = {
    "Authorization": f"token {GITHUB_TOKEN}",
    "Accept": "application/vnd.github.v3.raw"
}

try:
    response = requests.get(url, headers=headers, timeout=15)

    if response.status_code != 200:
        st.error("Could not load the database. Please check the share code.")
        st.stop()

    db_bytes = response.content

    # Load into SQLite
    fd, tmp_path = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    with open(tmp_path, 'wb') as f:
        f.write(db_bytes)

    conn = sqlite3.connect(tmp_path)
    memory_conn = sqlite3.connect(":memory:")
    conn.backup(memory_conn)
    conn.close()
    os.unlink(tmp_path)
    conn = memory_conn

except Exception as e:
    st.error(f"Database load failed: {e}")
    st.stop()

# ====================== REPORT ROUTING - Simple Scramble for Team ======================
full_code = st.query_params.get("code")
game_code = st.query_params.get("g")
player_code = st.query_params.get("p")

if not full_code:
    st.error("Missing code in the URL.")
    st.stop()

# Parse: DLNND51SC4-T11  → coach_code and scrambled_team
if '-T' in full_code:
    coach_code, scrambled_team = full_code.split('-T', 1)
else:
    coach_code = full_code
    scrambled_team = None

# Resolve team_id
c = conn.cursor()
team_id = None

if scrambled_team:
    team_id = unscramble_id(scrambled_team, "T")

if not team_id or team_id <= 0:
    # Fallback
    c.execute("SELECT id FROM teams LIMIT 1")
    row = c.fetchone()
    team_id = row[0] if row else None

if not team_id:
    st.error("No teams found in this database.")
    st.stop()

# Route reports
if player_code:
    player_id = unscramble_id(player_code, "P")
    if player_id <= 0:
        st.error(f"Invalid player code: {player_code}")
        st.stop()
    report_html = generate_player_report(conn, player_id)
    st.html(report_html)

elif game_code:
    game_id = unscramble_id(game_code, "G")
    if game_id <= 0:
        st.error(f"Invalid game code: {game_code}")
        st.stop()
    report_html = generate_game_report(conn, game_id)
    st.html(report_html)

else:
    report_html = generate_team_report(conn, team_id, full_code)
    st.html(report_html)

st.html(f'<img src="https://raw.githubusercontent.com/CourtTag/courttag-assets/main/CourtTag_Promo_QR.png" width="200" alt="CourtTag Logo">')
st.caption(f"Powered by CourtTag Web Viewer • Code: {full_code} • {datetime.now().strftime('%Y-%m-%d %H:%M')}")
