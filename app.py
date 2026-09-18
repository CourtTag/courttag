# ─────────────────────────────────────────────────────────────
# Standard library
# ─────────────────────────────────────────────────────────────
import sys
import os
import time
import platform
import re
import shutil
import subprocess
import csv
import sqlite3
import warnings
import ctypes
import winreg
import webbrowser
from datetime import datetime
from pathlib import Path
import json
#from pcloud import PyCloud
#import tkinter as tk
#from tkinter import scrolledtext
import requests
import base64

# ─────────────────────────────────────────────────────────────
# Third-party general
# ─────────────────────────────────────────────────────────────
import ffmpeg
from send2trash import send2trash
from PIL import Image, ImageOps, ImageEnhance, ImageQt

from collections import defaultdict

# ── VLC dependency check ─────────────────────────────────────────────────────
try:
    import vlc
    # Quick test to make sure libvlc loads
    instance = vlc.Instance()
    if instance is None:
        raise Exception("VLC instance creation failed")
except Exception as e:
    # Need QApplication to show QMessageBox
    app = QApplication(sys.argv)
    reply = QMessageBox.question(
        None,
        "VLC Required",
        "CourtTag requires VLC media player to play videos.\n\n"
        "Would you like to open the VLC download page?",
        QMessageBox.Yes | QMessageBox.No
    )
    if reply == QMessageBox.Yes:
        from PyQt5.QtCore import QUrl
        from PyQt5.QtGui import QDesktopServices

        QDesktopServices.openUrl(QUrl("https://www.videolan.org/vlc/"))
    sys.exit(1)

# If we reach here, VLC is available → continue normal startup
#print("python-vlc version:", vlc.__version__)  # should match 3.0.21203
#print("libvlc version:", vlc.libvlc_get_version())  # e.g. b'3.0.23 Vetinari' or similar

# ─────────────────────────────────────────────────────────────
# PyQt5 ────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QSlider, QLabel, QTableWidget, QTableWidgetItem,
    QListWidget, QListWidgetItem, QFileDialog, QMessageBox,
    QFrame, QTabWidget, QDialog, QLineEdit, QComboBox,
    QDateEdit, QGroupBox, QTextEdit, QInputDialog, QGridLayout,
    QHeaderView, QScrollArea, QStatusBar, QToolBar, QShortcut,
    QAction, QSizePolicy, QAbstractItemView, QMenu, QCheckBox,
    QFormLayout, QDialogButtonBox, QSpinBox, QProgressDialog,
    QButtonGroup, QRadioButton, QSplashScreen, QTextBrowser
)
from PyQt5.QtCore import Qt, QTimer, QDate, QMimeData, QSize, QUrl, QFile, QIODevice, pyqtSignal
from PyQt5.QtGui import (
    QPalette, QColor, QPixmap, QImage, QIcon, QFont,
    QKeySequence, QDesktopServices
)
from PyQt5.QtPrintSupport import QPrinter, QPrintDialog

import resources




warnings.filterwarnings("ignore", category=DeprecationWarning, module="sqlite3")

# ─────────────────────────────────────────────────────────────
# CENTRALIZED EVENT TYPES — SINGLE SOURCE OF TRUTH
# ─────────────────────────────────────────────────────────────
EVENT_TYPES = [
    # (full_display_name,          short_code,  is_shot, points_if_made, category)
    ("2PA 2PT Shot Attempt",      "2PA",       True,    0,             "shot"),
    ("2PM 2PT Shot Made",         "2PM",       True,    2,             "shot"),
    ("3PA 3PT Shot Attempt",      "3PA",       True,    0,             "shot"),
    ("3PM 3PT Shot Made",         "3PM",       True,    3,             "shot"),
    ("FTA Freethrow Attempt",     "FTA",       True,    0,             "shot"),
    ("FTM Freethrow Made",        "FTM",       True,    1,             "shot"),
    ("AST Assist",                "AST",       False,   0,             "other"),
    ("BLK Blocked Shot",          "BLK",       False,   0,             "defense"),
    ("CHG Charge Drawn",          "CHG",       False,   0,             "defense"),
    ("DRB Defensive Rebound",     "DRB",       False,   0,             "rebound"),
    ("ORB Offensive Rebound",     "ORB",       False,   0,             "rebound"),
    ("PFL Personal Foul",         "PFL",       False,   0,             "foul"),
    ("STL Steal",                 "STL",       False,   0,             "defense"),
    ("TOV Turnover",              "TOV",       False,   0,             "other"),
    ("DFL Deflection",            "DFL",       False, 0,               "defense"),
]

# ─────────────────────────────────────────────────────────────
# SHOT QUALITY — SINGLE SOURCE OF TRUTH (A/B/C/D)
# ─────────────────────────────────────────────────────────────
SHOT_QUALITY = [
    ("A - Elite / Big Advantage Shot",      "A"),
    ("B - Strong / Very Good Shot",         "B"),
    ("C - Average / Mid Advantage Shot",    "C"),
    ("D - Poor / Low Quality Shot",         "D"),
]

# Helper functions (add these right below the list)
def get_event_types_for_combo():
    """Returns the list exactly as EventEditorDialog expects"""
    return [name for name, *_ in EVENT_TYPES]

def get_short_code(full_name: str) -> str:
    for name, code, *_ in EVENT_TYPES:
        if name == full_name:
            return code
    return full_name[:3]  # fallback

def is_shot_event(full_name: str) -> bool:
    for name, _, is_shot, *_ in EVENT_TYPES:
        if name == full_name:
            return is_shot
    return False

def get_points_value(full_name: str) -> int:
    for name, _, _, points, *_ in EVENT_TYPES:
        if name == full_name:
            return points
    return 0

def is_defensive_event(full_name: str) -> bool:
    """Used for future DFL, STL, BLK, etc."""
    return get_short_code(full_name) in ("DFL", "STL", "BLK", "CHG")

def count_event_type(events, short_code_target: str) -> int:
    """Count how many times a specific short code appears in a list of event types"""
    return sum(1 for e_type in events if get_short_code(e_type) == short_code_target)

def get_shot_quality_options():
    """Returns list of full display names for combo/radio use"""
    return [name for name, code in SHOT_QUALITY]

def get_shot_quality_code(full_name: str) -> str:
    for name, code in SHOT_QUALITY:
        if name == full_name:
            return code
    return full_name[0] if full_name else ""  # fallback to first letter

def get_shot_quality_display(code: str) -> str:
    """Returns full display name from code (A → full text)"""
    for name, c in SHOT_QUALITY:
        if c == code:
            return name
    return code or ""

# ====================== SCRAMBLE / UNSCRAMBLE FUNCTIONS ======================
# Put these OUTSIDE the class, near the top of the file
def scramble_id(real_id: int, prefix: str = "") -> str:
    """Simple reversible scrambler: 11 → T10011K"""
    if real_id is None or real_id <= 0:
        return ""

    salted = real_id + 10000
    base = f"{salted:05d}"
    letters = "ABCDEFGHJKLMNPQRSTUVWXYZ"
    letter = letters[real_id % len(letters)]

    return f"{prefix}{base}{letter}"


def unscramble_id(scrambled: str, prefix: str = "") -> int:
    """Reverse the scrambler"""
    if not scrambled:
        return 0

    if prefix and scrambled.startswith(prefix):
        scrambled = scrambled[len(prefix):]

    digits = ''.join(c for c in scrambled if c.isdigit())
    if not digits:
        return 0

    salted = int(digits)
    real_id = salted - 10000
    return real_id if real_id > 0 else 0

VERSION = "1.0"
BUILD = "b51"
#1.0b51 Added PTS, PPS, TPT%, TST%, ABR% to Shot Location Tables
#1.0b50 Added a new player metric, A-B Shot Quality Ratio (ABR), (# of A+B quality shots) / (total # of shots).
 #50 Adding to Player Stats, Team (overall) stats, broken down by game as well
#1.0b49 Added a default version check on load, allowing the user to uncheck the check on load function via Default settings
 #b49 Also altered the Tag Event popup to a Radio Event Selector to reduce clicking combo lists when tagging.
 #b49 Changed QTextEdit to QTextBrowser to allow hyperlinks.
 #b49 Added YouTube Game URL to games table/forms for sharing highlighted event links on game reports for PC and Viewer
#1.0b48 Adjusted Team Filters for Players and Games Tab so they pre-filter based on Season Changes.  Also pre-select default Season on team select class.
#1.0b47 Adding a Share DB/Team Report tool using Streamlit app and pCloud to allow users to share team-specific date via website viewer
 #b47 also tweaked the POS Calc on Game report to not overcount shot types to match the Stream Viewer
 #b47 removed all pCloud share dependence, now using GitHub for Sharing DB and current_version checks
#1.0b46a Fixed Team/Player/Game Report Player Stats: 2PTM/2PTA columns now show pure 2PT only (not combined FGM/FGA). eFG% unchanged.
#1.0b46 Added Shot Quality to Shot Event Types and retooled the all reports a tighter format.
#1.0b45 Added eFG% to all reports: eFG% = (FGM + 0.5 x 3PM) / FGA.  Also added Free Throw Factor, FGF = FTA / FGA) to reports.
 #b45 added Assist/TO Ratio to Player Stats on all Reports
 #b45 added OREB%, DREB% and REB% for Game Reports and Team Reports
#1.0b44 Added File > Default to allow to preset Season for tab/list filtering.
#1.0b43 Added TOV % to Game Reports for Home and Guest Teams, added TOV% to the Team Report Games Summary as well.  Added playback speed control to video player.
 #b43 Also added a quarter_marks timestamp pre-selection deliminated field to the videos table and a tool in the Event Tagger Player.
 #b43 Also added home and guest team names to the video player/event window title.
 #b43 Also reverted to showing 2PT M/A instead of FG for reports (because industry includes 3PT shots in FG/FGM)
#1.0b42 Added running score totals (by event time) for home and guest team on View Scores Event Popup.  Retooled Foul Popup to include total player and by quarter running totals H-G.
#1.0b41 Altered Event Types Hard Coding to centralize and allow for the addition of DFL Deflection type events. Moved Game Report by Quarter up to top of report.
#1.0b40 Added a Save and Add Function to Players, allowing addition of a player to that same team. Also fixed false team tag on foul event popup
#1.0b39 Incorporated CourtTag.net website link (forwards to pCloud Public share).  Changes event to tagging (titles/buttons)
#1.0b38 widened and allow maximize team-player-game reports
#1.0b37 adjusted team-player-game reports borders and highlight row colours for consistency
#1.0b36 retooled the User Guice to now be topics (including descriptions) and use resource files for HTML content.
#1.0b35 Added a pCloud current_version.txt check function code=XZybOd5ZfqYzMMLsde4H8Qcl7IwnLBGAyhmk, also added pCloud Public share folder link
#1.0b34 pCloud Link to User Guide (HTML from .txt file) added to Help. Also, moved Descriptions to separate HTML file folder (will do resources.py later)
#1.0b33 Added % of Total Shots Taken for each location at Season Shot Summary Report
#1.0b32 Added is_highlight to events, for video clip filter creation, tweaked video game clip generator also
#1.0b31 Added "View" Main Menu for "Season Summary Reporting" and created report.
#1.0b30 Altered Game Clips a bit to have the folder open. Cleaned up png warning issues in resources. Cleaned up imports
#1.0b29 Changed tab list tables to alternating row colours and font colours for win/lose/tie
#1.0b28 Fix Quarters Event Bug (halves in event add/edit) and updated Game report by Quarter/Half
#1.0b27 Remove 0 from player number (only use leading 0 to sort), fixed all sorting/viewing reports
#1.0b26 Changed resource images (Shot_Location_Diagram_v1.svg + ), added Powered by CourtTag to reports
#1.0b25 Added gamesheet icon (if exist) to the games table listing.
#1.0b24 Added gamesheet image opener to the games table listing.
#1.0b23 Added gamesheet image import/editor.
#1.0b22 Added more detailed descriptions for event types
#1.0b21 Removed Add/Delete Video from the GameEditorDialog, will leave it only in the GameVideosDialog
 # 0b21 Also added a filecheck and find (relink) function to the game video listing to allow users to find lost (or renamed) game videos files
#1.0b20 Added right click game edit and video + event edit clickmenu
#1.0b19 Added File > Export all Data to CSV.
#1.0b18 Added W-L-T-PPG-OPPG to Teams Tab Table Listing
#1.0b17 Looking to fix player number alignment and allow 0 vs 00 display numbers
#1.0b16 Added # of Event and Quarter Display to Game Video Listing and fixed game video record duplication
#1.0b15 Altered the gameEditorDialog to only allow team selection (popups) on add and fix player roster duplication issue
#1.0b14 Rebuilding how team add/edit works to remove the add player duplication bug in the TeamEditorDialog
#1.0b13 improved game roster or player delete functions to keep the events (as null-home or null-guest) so they still appear
#1.0b12 adding DB backup folder default
#1.0b11 adding Home and Guest total scores to the Game Listing Table, added colours for win/loss on games + team filter
#1.0b10 adding video merge to File Menu, including re-encoding
#1.0b9 deletes individual clips after merge, added text labels to clips
#1.0b8 Added a BuyMeaCoffee.com/CourtTag link to helpmenu, also fixed a text cellwrap issue on report tables
#1.0b5 included event list by quarter background colors.  added View Foul to Event list editor
#1.0b4 included print report and copy report functions, as well as a Search Navigation tag

FONT_PATH = r"C:\Windows\Fonts\arialbd.ttf"  # or arial.ttf if bold not loading
# You can make this configurable later (settings or dialog)

DB_NAME = "CourtTag_data.db"
DB_VERSION = "1.0"  #for DB compatibility later

# ── Move DB to user-writable location in AppData\Roaming ──────────────────────
#C:\Users\YourUsername\AppData\Roaming\CourtTag\CourtTag_data.db
DB_FOLDER = Path(os.getenv('APPDATA')) / "CourtTag"
DB_FOLDER.mkdir(parents=True, exist_ok=True)          # create folder if missing
DB_NAME = DB_FOLDER / "CourtTag_data.db"
PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))
GAMESHEET_DIR = os.path.join(PROJECT_ROOT, "courttag", "gamesheets")
os.makedirs(GAMESHEET_DIR, exist_ok=True)

def open_folder(folder_path):
    path_str = str(folder_path)
    #print(f"DEBUG: open_folder called with {path_str}")

    if not os.path.isdir(path_str):
        print("Folder missing!")
        return

    try:
        if sys.platform == 'win32':
            # Use /select for highlighting the folder itself if needed; remove if unwanted
            subprocess.Popen(['explorer.exe', '/select,', path_str])
            # Or plain:
            # subprocess.Popen(['explorer.exe', path_str])
            print("DEBUG: explorer.exe launched")
        # ... other platforms ...
    except Exception as e:
        print(f"Launch error: {e}")
        # Ultimate fallback
        try:
            os.startfile(path_str)
        except:
            pass


class SortableNumericItem(QTableWidgetItem):
    def __init__(self, display_text, sort_value=None):
        super().__init__(display_text)
        if sort_value is not None:
            self.setData(Qt.UserRole, sort_value)

    def __lt__(self, other):
        if isinstance(other, SortableNumericItem):
            self_val = self.data(Qt.UserRole)
            other_val = other.data(Qt.UserRole)
            if self_val is not None and other_val is not None:
                return self_val < other_val
        return super().__lt__(other)

def get_gamesheets_folder() -> Path:
    folder = Path.home() / "Documents" / "CourtTag" / "Gamesheets"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def sanitize_filename(text: str) -> str:
    """Create safe filename from game name/date/etc."""
    text = text.lower()
    text = re.sub(r'[^a-z0-9_-]', '_', text)          # only keep safe chars
    text = re.sub(r'_+', '_', text)                   # collapse multiple _
    text = text.strip('_')
    return text[:80] if len(text) > 80 else text      # reasonable length

def init_db():
    # Initialize database and perform safe schema migrations
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()

    # Create tables if they don't exist
    c.execute('''CREATE TABLE IF NOT EXISTS teams
                     (id INTEGER PRIMARY KEY, name TEXT NOT NULL, season TEXT NOT NULL)''')

    c.execute('''CREATE TABLE IF NOT EXISTS players
                     (id INTEGER PRIMARY KEY, team_id INTEGER, number TEXT,
                      name TEXT, FOREIGN KEY (team_id) REFERENCES teams(id))''')

    c.execute('''CREATE TABLE IF NOT EXISTS games
                     (id INTEGER PRIMARY KEY, name TEXT NOT NULL, date DATE, location TEXT,
                      format TEXT, home_team_id INTEGER, guest_team_id INTEGER,
                      is_complete INTEGER DEFAULT 0,
                      gamesheet_path TEXT,
                      FOREIGN KEY (home_team_id) REFERENCES teams(id),
                      FOREIGN KEY (guest_team_id) REFERENCES teams(id))''')

    c.execute('''CREATE TABLE IF NOT EXISTS game_rosters
                     (id INTEGER PRIMARY KEY, game_id INTEGER, player_id INTEGER, side TEXT,
                      FOREIGN KEY (game_id) REFERENCES games(id),
                      FOREIGN KEY (player_id) REFERENCES players(id))''')

    c.execute('''CREATE TABLE IF NOT EXISTS videos
                     (id INTEGER PRIMARY KEY, game_id INTEGER, filename TEXT NOT NULL,
                      length_seconds INTEGER, record_date DATE, quarter_marks TEXT,
                      FOREIGN KEY (game_id) REFERENCES games(id))''')

    c.execute('''CREATE TABLE IF NOT EXISTS events
                     (id INTEGER PRIMARY KEY, video_id INTEGER, time_ms INTEGER, quarter TEXT,
                      player_id INTEGER, team_side TEXT, type TEXT NOT NULL, location TEXT,
                      shot_quality TEXT, is_highlight INTEGER DEFAULT 0,
                      FOREIGN KEY (video_id) REFERENCES videos(id),
                      FOREIGN KEY (player_id) REFERENCES players(id))''')

    # ── Safe column additions (idempotent) ─────────────────────────────────────
    migrations = [
        ("ALTER TABLE events ADD COLUMN shot_quality TEXT", "shot_quality"),
        ("ALTER TABLE events ADD COLUMN is_highlight INTEGER DEFAULT 0", "is_highlight"),
        ("ALTER TABLE videos ADD COLUMN quarter_marks TEXT", "quarter_marks"),
        ("ALTER TABLE games ADD COLUMN is_complete INTEGER DEFAULT 0", "is_complete"),
        ("ALTER TABLE games ADD COLUMN gamesheet_path TEXT", "gamesheet_path"),
        ("ALTER TABLE games ADD COLUMN youtube_url TEXT", ""),
    ]

    for sql, column_name in migrations:
        try:
            c.execute(sql)
            print(f"Added column: {column_name}")
        except sqlite3.OperationalError as e:
            if "duplicate column name" in str(e).lower():
                pass  # already exists - silent
            else:
                print(f"Warning during migration for {column_name}: {e}")
        except Exception as e:
            print(f"Error during migration for {column_name}: {e}")

    # Defaults table
    c.execute('''CREATE TABLE IF NOT EXISTS defaults 
                     (key TEXT PRIMARY KEY, value TEXT)''')
    # ── NEW: Default for automatic update check on load (yes for fresh DBs) ──
    c.execute("SELECT 1 FROM defaults WHERE key = 'check_for_updates_on_load'")
    if not c.fetchone():
        c.execute("INSERT INTO defaults (key, value) VALUES ('check_for_updates_on_load', 'yes')")

    # === NEW: A-B Ratio minimum shots default ===
    c.execute("SELECT 1 FROM defaults WHERE key = 'abr_minimum'")
    if not c.fetchone():
        c.execute("INSERT INTO defaults (key, value) VALUES ('abr_minimum', '10')")

    conn.commit()
    conn.close()



class PlayerInputDialog(QDialog):
    playerSaved = pyqtSignal()  # emitted every time we successfully save a player

    def __init__(self, parent=None, player_id=None):
        super().__init__(parent)
        self.player_id = player_id
        self.selected_team_id = None

        self.setWindowTitle("Edit Player" if player_id else "Add Player")
        self.setFixedSize(400, 140)

        layout = QVBoxLayout(self)

        # ── Team selection ──
        team_lay = QHBoxLayout()
        team_lay.addWidget(QLabel("Team:"))

        if player_id:
            # Edit mode: read-only label
            self.team_display = QLabel()
            self.team_display.setStyleSheet("background: #f0f0f0; border: 1px solid #ccc; padding: 4px;")
            team_lay.addWidget(self.team_display, stretch=1)
        else:
            # Add mode: read-only line edit + Select button
            self.team_display = QLineEdit()
            self.team_display.setReadOnly(True)
            self.team_display.setPlaceholderText("Click 'Select' to choose a team")
            team_lay.addWidget(self.team_display, stretch=1)

            select_btn = QPushButton("Select")
            select_btn.clicked.connect(self.select_team)
            team_lay.addWidget(select_btn)

        layout.addLayout(team_lay)

        # ── Jersey + Name ──
        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("Jersey:"))

        self.number_edit = QLineEdit()
        self.number_edit.setFixedWidth(50)
        self.number_edit.setAlignment(Qt.AlignCenter)
        name_row.addWidget(self.number_edit)

        name_row.addWidget(QLabel("Name:"))

        self.name_edit = QLineEdit()
        name_row.addWidget(self.name_edit, stretch=1)

        layout.addLayout(name_row)

        # ── Buttons ──
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self.save_btn = QPushButton("Save + Close")
        self.save_btn.setDefault(True)  # Enter key triggers this
        self.save_btn.setAutoDefault(True)
        self.save_btn.clicked.connect(self.on_save_close)

        self.save_add_btn = QPushButton("Save + Add Another")
        self.save_add_btn.setDefault(False)  # prevent it from stealing default
        self.save_add_btn.setAutoDefault(False)
        self.save_add_btn.clicked.connect(self.on_save_add_another)

        btn_layout.addWidget(self.save_btn)
        btn_layout.addSpacing(10)
        btn_layout.addWidget(self.save_add_btn)

        layout.addLayout(btn_layout)

        # ── Initial state ──
        self.save_add_btn.setEnabled(False)
        if player_id:
            self.save_add_btn.setVisible(False)  # hide in edit mode

        # Validation signals
        self.number_edit.textChanged.connect(self.validate_form)
        self.name_edit.textChanged.connect(self.validate_form)

        # Load existing player if editing
        if player_id:
            self.load_player()
        else:
            self.validate_form()  # initial validation for add mode

    def load_player(self):
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("""
            SELECT p.number, p.name, p.team_id, t.name AS team_name
            FROM players p
            LEFT JOIN teams t ON p.team_id = t.id
            WHERE p.id = ?
        """, (self.player_id,))
        row = c.fetchone()
        conn.close()

        if row:
            number, name, team_id, team_name = row
            self.number_edit.setText(number or "")
            self.name_edit.setText(name or "")
            self.selected_team_id = team_id
            self.team_display.setText(team_name or "Unknown Team")
            self.team_display.setEnabled(False)  # no team change in edit
            self.validate_form()

    def select_team(self):
        dialog = TeamSelectorDialog(self)
        if dialog.exec() == QDialog.Accepted:
            team_id = dialog.get_selected_team_id()
            team_name = dialog.get_selected_team_name()
            if team_id:
                self.selected_team_id = team_id
                self.team_display.setText(team_name or "")
                self.validate_form()

    def validate_form(self):
        number = self.number_edit.text().strip()
        team_selected = bool(self.selected_team_id)
        is_valid = bool(number) and (self.player_id or team_selected)

        self.save_btn.setEnabled(is_valid)
        self.save_add_btn.setEnabled(is_valid and not self.player_id)

        # Red border feedback
        self.number_edit.setStyleSheet("border: 1px solid red;" if not number else "border: 1px solid #ccc;")
        if not self.player_id:
            self.team_display.setStyleSheet(
                "border: 1px solid red;" if not team_selected else "border: 1px solid #ccc;")

    def save_to_database(self):
        """Save or update depending on whether we're in add or edit mode"""
        number = self.number_edit.text().strip()
        name = self.name_edit.text().strip() or None
        team_id = self.selected_team_id

        if not number:
            QMessageBox.warning(self, "Error", "Jersey number is required.")
            return False

        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        try:
            if self.player_id:
                # ── EDIT MODE ── Update existing record
                c.execute("""
                    UPDATE players
                    SET team_id = ?,
                        number  = ?,
                        name    = ?
                    WHERE id = ?
                """, (team_id, number, name, self.player_id))
            else:
                # ── ADD MODE ── Insert new record
                c.execute("""
                    INSERT INTO players (team_id, number, name)
                    VALUES (?, ?, ?)
                """, (team_id, number, name))

            conn.commit()
            return True

        except sqlite3.IntegrityError as e:
            # Catch common constraint violations (e.g. unique jersey per team if you have that)
            conn.rollback()
            QMessageBox.critical(self, "Database Error",
                                 f"Cannot save: possible duplicate jersey number or constraint violation.\n{str(e)}")
            return False
        except Exception as e:
            conn.rollback()
            QMessageBox.critical(self, "Database Error", f"Could not save player:\n{str(e)}")
            return False
        finally:
            conn.close()

    def clear_for_next_player(self):
        self.number_edit.clear()
        self.name_edit.clear()
        self.number_edit.setFocus()
        self.number_edit.selectAll()
        self.validate_form()

    def on_save_close(self):
        if not self.number_edit.text().strip():
            QMessageBox.warning(self, "Error", "Player number is required.")
            self.number_edit.setFocus()
            return

        if not self.player_id and not self.selected_team_id:
            QMessageBox.warning(self, "Error", "Please select a team.")
            return

        if self.save_to_database():
            self.playerSaved.emit()  # ← tell main window to refresh
            #Optional: different message for add vs edit
            # if self.player_id:
            #    QMessageBox.information(self, "Success", "Player updated.")
            # else:
            #     QMessageBox.information(self, "Success", "Player added.")
            self.accept()  # close dialog

    def on_save_add_another(self):
        if not self.number_edit.text().strip():
            QMessageBox.warning(self, "Error", "Player number is required.")
            self.number_edit.setFocus()
            return

        if not self.selected_team_id:
            QMessageBox.warning(self, "Error", "Please select a team.")
            return

        if self.save_to_database():
            self.playerSaved.emit()  # ← tell main window to refresh
            self.clear_for_next_player()

            # Optional: less intrusive feedback (no modal dialog spam)
            # You can replace with a QLabel status message in the dialog if desired
            # QMessageBox.information(
            #     self,
            #     "Saved",
            #     "Player added — ready for the next one",
            #     QMessageBox.Ok,
            #     QMessageBox.NoButton  # makes it slightly less blocking
            # )

            # Explicitly prevent any accept/close
            # (not normally needed, but harmless safety net)

            self.setResult(QDialog.Rejected)  # make sure result is not Accepted

    def get_number(self):
        return self.number_edit.text().strip()

    def get_name(self):
        return self.name_edit.text().strip()

    def get_team_id(self):
        return self.selected_team_id

class TeamSelectorDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Select Team")
        self.setFixedSize(400, 350)

        layout = QVBoxLayout(self)

        # Season filter
        layout.addWidget(QLabel("Season:"))
        self.season_combo = QComboBox()
        self.season_combo.addItem("All Seasons")
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT DISTINCT season FROM teams WHERE season IS NOT NULL ORDER BY season DESC")
        for (season,) in c.fetchall():
            self.season_combo.addItem(season)
        conn.close()
        layout.addWidget(self.season_combo)

        # Team list
        layout.addWidget(QLabel("Teams:"))
        self.team_list = QListWidget()
        self.team_list.setAlternatingRowColors(True)
        layout.addWidget(self.team_list, stretch=1)

        # Buttons
        btn_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btn_box.accepted.connect(self.accept)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

        # Load teams on open + when season changes
        self.season_combo.currentIndexChanged.connect(self.load_teams)
        self.load_teams()  # initial load

        # Pre-select default season
        QTimer.singleShot(10, self.preselect_default_season)

    def load_teams(self):
        self.team_list.clear()

        season = self.season_combo.currentText()
        if season == "All Seasons":
            season = None

        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()

        query = "SELECT id, name, season FROM teams"
        params = []
        if season:
            query += " WHERE season = ?"
            params = [season]

        query += " ORDER BY name"

        c.execute(query, params)
        for tid, name, s in c.fetchall():
            item = QListWidgetItem(f"{name} ({s or 'No Season'})")
            item.setData(Qt.UserRole, tid)
            self.team_list.addItem(item)

        conn.close()

    def preselect_default_season(self):
        """Pre-select default season - robust parent search"""
        parent = self.parent()
        main_window = None

        while parent is not None:
            if hasattr(parent, 'get_default_season'):
                main_window = parent
                break
            parent = getattr(parent, 'parent', lambda: None)()

        if not main_window:
            return

        default_season = main_window.get_default_season()
        if not default_season:
            return

        index = self.season_combo.findText(default_season, Qt.MatchExactly)
        if index >= 0:
            self.season_combo.setCurrentIndex(index)
            self.load_teams()

    def get_selected_team_id(self):
        item = self.team_list.currentItem()
        if item:
            return item.data(Qt.UserRole)
        return None

    def get_selected_team_name(self):
        item = self.team_list.currentItem()
        if item:
            return item.text().split(" (")[0]
        return None

class TeamEditorDialog(QDialog):
    def __init__(self, parent=None, team_id=None):
        super().__init__(parent)
        self.team_id = team_id
        self.setWindowTitle("Edit Team" if team_id else "Add Team")
        self.setGeometry(200, 200, 250, 120)  # smaller without roster

        layout = QVBoxLayout(self)

        # Team Name
        layout.addWidget(QLabel("Team Name"))
        self.name_edit = QLineEdit()
        layout.addWidget(self.name_edit)

        # Season
        layout.addWidget(QLabel("Season"))
        self.season_combo = QComboBox()
        self.season_combo.setEditable(True)
        self.season_combo.setInsertPolicy(QComboBox.InsertAtTop)
        self.season_combo.setDuplicatesEnabled(False)
        self.season_combo.setPlaceholderText("e.g. 2025-26 Fall-Winter or type new")
        layout.addWidget(self.season_combo)

        self.populate_season_combo()

        # ←←← IMPROVED: Pre-select Default Season safely after population (only on Add)
        if not self.team_id:  # Only when adding a new team
            QTimer.singleShot(0, self.preselect_default_season_in_dialog)

        # Save button - make it self.save_btn immediately
        self.save_btn = QPushButton("Save")
        self.save_btn.clicked.connect(self.save_team)
        layout.addWidget(self.save_btn)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        layout.addWidget(cancel_btn)

        # Connect validation
        self.name_edit.textChanged.connect(self.validate_form)
        self.validate_form()  # initial check

        # Populate seasons
        self.populate_season_combo()

        # Load existing data if editing
        if team_id:
            self.load_team()

    def preselect_default_season_in_dialog(self):
        """Pre-select the Default Season when adding a new team."""
        if not hasattr(self.parent(), 'get_default_season'):
            return

        default = self.parent().get_default_season()
        if not default:
            return  # No default set → leave blank

        # Look for the default season in the combo
        index = self.season_combo.findText(default, Qt.MatchExactly)
        if index >= 0:
            self.season_combo.setCurrentIndex(index)
        else:
            # If the season is not in the list yet, add it at the top and select it
            self.season_combo.insertItem(0, default)
            self.season_combo.setCurrentIndex(0)

    def validate_form(self):
        name = self.name_edit.text().strip()
        self.save_btn.setEnabled(bool(name))  # enable only if name filled
        self.name_edit.setStyleSheet("border: 1px solid red;" if not name else "")

    def populate_season_combo(self):
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("""
            SELECT DISTINCT season 
            FROM teams 
            WHERE season IS NOT NULL AND season != '' 
            ORDER BY season DESC
        """)
        seasons = [row[0] for row in c.fetchall() if row[0]]
        conn.close()

        self.season_combo.clear()
        self.season_combo.addItems(seasons)

    def load_team(self):
        if not self.team_id:
            return

        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT name, season FROM teams WHERE id = ?", (self.team_id,))
        row = c.fetchone()
        conn.close()

        if row:
            self.name_edit.setText(row[0] or "")
            index = self.season_combo.findText(row[1])
            if index >= 0:
                self.season_combo.setCurrentIndex(index)
            else:
                self.season_combo.setCurrentText(row[1] or "")

    def save_team(self):
        name = self.name_edit.text().strip()
        season = self.season_combo.currentText().strip()  # can be empty

        if not name:
            QMessageBox.warning(self, "Missing Team Name", "Team name cannot be blank.")
            self.name_edit.setFocus()
            return

        # Season is optional — no check/warning needed

        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()

        try:
            if self.team_id:
                # Edit existing team
                c.execute("""
                    UPDATE teams
                    SET name = ?, season = ?
                    WHERE id = ?
                """, (name, season if season else None, self.team_id))
            else:
                # Add new team
                c.execute("""
                    INSERT INTO teams (name, season)
                    VALUES (?, ?)
                """, (name, season if season else None))

            conn.commit()

            QMessageBox.information(self, "Saved", "Team saved successfully.")

            # Notify parent to refresh season combo
            if self.parent():
                self.parent().refresh_all_season_filters()
                self.parent().refresh_team_combos()

            self.accept()

        except Exception as e:
            conn.rollback()
            QMessageBox.critical(self, "Save Failed", str(e))
        finally:
            conn.close()

    def preselect_default_season(self):
        """Pre-select the user's Default Season if one is set"""
        if not hasattr(self.parent(), 'get_default_season'):
            return

        default_season = self.parent().get_default_season()
        if not default_season:
            return  # No default set → leave "All Seasons" selected

        # Find and select the default season
        index = self.season_combo.findText(default_season, Qt.MatchExactly)
        if index >= 0:
            self.season_combo.setCurrentIndex(index)
            # Trigger load_teams so the list updates immediately
            self.load_teams()
        # else: default season not in list yet (rare) → do nothing

    def load_teams(self):
        self.team_list.clear()

        season = self.season_combo.currentText()
        if season == "All Seasons":
            season = None

        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()

        query = "SELECT id, name, season FROM teams"
        params = []
        if season:
            query += " WHERE season = ?"
            params = [season]

        query += " ORDER BY name"

        c.execute(query, params)
        for tid, name, s in c.fetchall():
            item = QListWidgetItem(f"{name} ({s or 'No Season'})")
            item.setData(Qt.UserRole, tid)
            self.team_list.addItem(item)

        conn.close()

    def get_selected_team_id(self):
        item = self.team_list.currentItem()
        if item:
            return item.data(Qt.UserRole)
        return None

    def get_selected_team_name(self):
        item = self.team_list.currentItem()
        if item:
            return item.text().split(" (")[0]  # remove season part
        return None

class GameEditorDialog(QDialog):
    def __init__(self, parent=None, game_id=None):
        super().__init__(parent)
        self.game_id = game_id

        self.selected_home_team_id = None
        self.selected_guest_team_id = None

        self.gamesheet_path = None  # absolute path during editing
        self.gamesheet_rel_path = None  # what we'll store in DB

        self.original_home_roster = set()
        self.original_guest_roster = set()

        # Load original rosters if editing
        if game_id:
            conn = sqlite3.connect(DB_NAME)
            c = conn.cursor()
            c.execute("SELECT player_id FROM game_rosters WHERE game_id = ? AND side = 'home'", (game_id,))
            for (pid,) in c.fetchall():
                self.original_home_roster.add(pid)
            c.execute("SELECT player_id FROM game_rosters WHERE game_id = ? AND side = 'guest'", (game_id,))
            for (pid,) in c.fetchall():
                self.original_guest_roster.add(pid)
            conn.close()

        self.setWindowTitle("Edit Game" if game_id else "Add Game")
        self.setGeometry(200, 200, 950, 680)  # your original size

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(12, 12, 12, 12)

        # ── Top info section ────────────────────────────────────────────────────
        info_layout = QGridLayout()
        info_layout.setSpacing(10)
        info_layout.setContentsMargins(0, 0, 0, 0)
        info_layout.setVerticalSpacing(8)
        info_layout.setHorizontalSpacing(16)

        row = 0

        # Row 0: Game Name + Game Location
        info_layout.addWidget(QLabel("Game Name"), row, 0, alignment=Qt.AlignLeft | Qt.AlignVCenter)
        self.name_edit = QLineEdit()
        info_layout.addWidget(self.name_edit, row, 1)

        info_layout.addWidget(QLabel("Game Location"), row, 2, alignment=Qt.AlignLeft | Qt.AlignVCenter)
        self.location_combo = QComboBox()
        self.location_combo.setEditable(True)
        self.location_combo.setInsertPolicy(QComboBox.InsertAtTop)
        self.location_combo.setDuplicatesEnabled(False)
        self.location_combo.setPlaceholderText("e.g. Main Gym, Away @ Westview, Tournament Court 3")
        info_layout.addWidget(self.location_combo, row, 3)
        row += 1

        # Row 1: Game Date + Game Format
        info_layout.addWidget(QLabel("Game Date"), row, 0, alignment=Qt.AlignLeft | Qt.AlignVCenter)
        self.date_edit = QDateEdit()
        self.date_edit.setDate(QDate.currentDate())
        self.date_edit.setCalendarPopup(True)
        info_layout.addWidget(self.date_edit, row, 1)

        info_layout.addWidget(QLabel("Game Format"), row, 2, alignment=Qt.AlignLeft | Qt.AlignVCenter)
        self.format_combo = QComboBox()
        self.format_combo.addItems(["Q - Quarters", "H - Halves", "P - Periods"])
        info_layout.addWidget(self.format_combo, row, 3)
        row += 1

        # Row 2: Home Team + Guest Team
        info_layout.addWidget(QLabel("Home Team"), row, 0, alignment=Qt.AlignLeft | Qt.AlignVCenter)
        self.home_team_display = QLineEdit()
        self.home_team_display.setReadOnly(True)
        info_layout.addWidget(self.home_team_display, row, 1)

        if not game_id:  # Add mode only
            self.home_select_btn = QPushButton("Select")
            self.home_select_btn.clicked.connect(self.select_home_team)
            info_layout.addWidget(self.home_select_btn, row, 1, alignment=Qt.AlignRight)

        info_layout.addWidget(QLabel("Guest Team"), row, 2, alignment=Qt.AlignLeft | Qt.AlignVCenter)
        self.guest_team_display = QLineEdit()
        self.guest_team_display.setReadOnly(True)
        info_layout.addWidget(self.guest_team_display, row, 3)

        if not game_id:
            self.guest_select_btn = QPushButton("Select")
            self.guest_select_btn.clicked.connect(self.select_guest_team)
            info_layout.addWidget(self.guest_select_btn, row, 3, alignment=Qt.AlignRight)

        row += 1

        # Allow input fields to expand horizontally
        info_layout.setColumnStretch(1, 1)
        info_layout.setColumnStretch(3, 1)

        layout.addLayout(info_layout)

        # ── Rosters ─────────────────────────────────────────────────────────────
        roster_layout = QHBoxLayout()
        roster_layout.setSpacing(16)

        # Home Team group
        home_group = QGroupBox("Home Team")
        home_l = QVBoxLayout()
        home_l.setSpacing(6)
        home_l.addWidget(QLabel("Player Roster"))
        self.home_player_list = QListWidget()  # available
        home_l.addWidget(self.home_player_list)
        home_l.addWidget(QLabel("Game Roster"))
        self.home_game_list = QListWidget()  # assigned
        home_l.addWidget(self.home_game_list)
        home_btn_l = QHBoxLayout()
        home_btn_l.addStretch()
        to_game = QPushButton(">>")
        to_game.setFixedWidth(60)
        to_game.clicked.connect(lambda: self.move_roster(self.home_player_list, self.home_game_list))
        home_btn_l.addWidget(to_game)
        from_game = QPushButton("<<")
        from_game.setFixedWidth(60)
        from_game.clicked.connect(lambda: self.move_roster(self.home_game_list, self.home_player_list))
        home_btn_l.addWidget(from_game)
        home_btn_l.addStretch()
        home_l.addLayout(home_btn_l)
        home_group.setLayout(home_l)
        roster_layout.addWidget(home_group, stretch=1)

        # Guest Team group
        guest_group = QGroupBox("Guest Team")
        guest_l = QVBoxLayout()
        guest_l.setSpacing(6)
        guest_l.addWidget(QLabel("Player Roster"))
        self.guest_player_list = QListWidget()  # available
        guest_l.addWidget(self.guest_player_list)
        guest_l.addWidget(QLabel("Game Roster"))
        self.guest_game_list = QListWidget()  # assigned
        guest_l.addWidget(self.guest_game_list)
        guest_btn_l = QHBoxLayout()
        guest_btn_l.addStretch()
        to_game = QPushButton(">>")
        to_game.setFixedWidth(60)
        to_game.clicked.connect(lambda: self.move_roster(self.guest_player_list, self.guest_game_list))
        guest_btn_l.addWidget(to_game)
        from_game = QPushButton("<<")
        from_game.setFixedWidth(60)
        from_game.clicked.connect(lambda: self.move_roster(self.guest_game_list, self.guest_player_list))
        guest_btn_l.addWidget(from_game)
        guest_btn_l.addStretch()
        guest_l.addLayout(guest_btn_l)
        guest_group.setLayout(guest_l)
        roster_layout.addWidget(guest_group, stretch=1)

        layout.addLayout(roster_layout, stretch=1)

        # Gamesheet section
        gamesheet_group = QGroupBox("Gamesheet Photo")
        gs_layout = QVBoxLayout()

        self.gamesheet_status = QLabel("No gamesheet attached")
        self.gamesheet_status.setWordWrap(True)
        gs_layout.addWidget(self.gamesheet_status)

        gs_btn_layout = QHBoxLayout()
        self.btn_add_gamesheet = QPushButton("Add / Change Gamesheet...")
        self.btn_add_gamesheet.clicked.connect(self.handle_gamesheet)
        gs_btn_layout.addWidget(self.btn_add_gamesheet)

        self.btn_clear_gamesheet = QPushButton("Remove")
        self.btn_clear_gamesheet.clicked.connect(self.clear_gamesheet)
        self.btn_clear_gamesheet.setEnabled(False)
        gs_btn_layout.addWidget(self.btn_clear_gamesheet)

        gs_layout.addLayout(gs_btn_layout)
        gamesheet_group.setLayout(gs_layout)
        layout.addWidget(gamesheet_group)

        # ── NEW: YouTube Full Game Video Field ───────────────────────────────
        youtube_layout = QHBoxLayout()
        youtube_layout.setSpacing(8)

        youtube_label = QLabel("YouTube Full Game Video:")
        youtube_label.setFixedWidth(220)
        youtube_layout.addWidget(youtube_label)

        self.youtube_url_edit = QLineEdit()
        self.youtube_url_edit.setPlaceholderText("https://youtu.be/abc123... or full watch?v= link")
        youtube_layout.addWidget(self.youtube_url_edit)

        layout.addLayout(youtube_layout)


        # Checkbox centered full-width
        complete_layout = QHBoxLayout()
        complete_layout.setContentsMargins(0, 8, 0, 8)
        complete_layout.setAlignment(Qt.AlignCenter)

        self.complete_checkbox = QCheckBox("Event Tagging Complete (Include in Stats & Reports)")
        self.complete_checkbox.setChecked(False)  # or load from DB
        self.complete_checkbox.setStyleSheet("""
            QCheckBox {
                font-size: 12px;
                font-weight: bold;
                spacing: 10px;
            }
            QCheckBox::indicator {
                width: 20px;
                height: 20px;
            }
        """)

        complete_layout.addWidget(self.complete_checkbox)
        layout.addLayout(complete_layout)

        bttn_layout = QHBoxLayout()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        bttn_layout.addWidget(cancel_btn)

        # ── Save ────────────────────────────────────────────────────────────────
        save_btn = QPushButton("Save")
        #save_btn.setFixedHeight(42)
        save_btn.clicked.connect(self.save_game)
        bttn_layout.addWidget(save_btn)
        layout.addLayout(bttn_layout)

        # Final setup
        self.populate_location_combo()
        if game_id:
            self.load_game_data(game_id)
            self.load_available_players_for_home()  # ← add this
            self.load_available_players_for_guest()  # ← add this
        else:
            # Add mode: start empty
            self.home_player_list.clear()
            self.guest_player_list.clear()
            #self.home_game_list.clear()
            #self.guest_game_list.clear()

    def select_home_team(self):
        dialog = TeamSelectorDialog(self)
        if dialog.exec_() == QDialog.Accepted:
            team_id = dialog.get_selected_team_id()
            team_name = dialog.get_selected_team_name()
            if team_id:
                # Check against currently selected guest team (from display or stored ID)
                current_guest_id = self.selected_guest_team_id if hasattr(self, 'selected_guest_team_id') else None
                if team_id == current_guest_id:
                    QMessageBox.warning(self, "Invalid Selection", "Home and Guest cannot be the same team.")
                    return
                self.selected_home_team_id = team_id  # store it
                self.home_team_display.setText(team_name)
                self.load_available_players_for_home()

    def select_guest_team(self):
        dialog = TeamSelectorDialog(self)
        if dialog.exec_() == QDialog.Accepted:
            team_id = dialog.get_selected_team_id()
            team_name = dialog.get_selected_team_name()
            if team_id:
                current_home_id = self.selected_home_team_id if hasattr(self, 'selected_home_team_id') else None
                if team_id == current_home_id:
                    QMessageBox.warning(self, "Invalid Selection", "Home and Guest cannot be the same team.")
                    return
                self.selected_guest_team_id = team_id
                self.guest_team_display.setText(team_name)
                self.load_available_players_for_guest()

    def load_available_players_for_home(self):
        self.home_player_list.clear()

        # Get home_team_id from stored ID (set during selection or load)
        home_team_id = self.selected_home_team_id

        if not home_team_id:
            return

        # Get assigned players to exclude
        assigned = set()
        for i in range(self.home_game_list.count()):
            pid = self.home_game_list.item(i).data(Qt.UserRole)
            if pid:
                assigned.add(pid)

        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("""
            SELECT p.id, p.number, p.name
            FROM players p
            WHERE p.team_id = ?
            ORDER BY CAST(p.number AS INTEGER), p.name
        """, (home_team_id,))
        for pid, number, name in c.fetchall():
            if pid in assigned:
                continue
            display_num = number if number else "?"
            item = QListWidgetItem(f"#{display_num} - {name}")
            item.setData(Qt.UserRole, pid)
            self.home_player_list.addItem(item)
        conn.close()

    def load_available_players_for_guest(self):
        self.guest_player_list.clear()

        guest_team_id = self.selected_guest_team_id

        if not guest_team_id:
            return

        assigned = set()
        for i in range(self.guest_game_list.count()):
            pid = self.guest_game_list.item(i).data(Qt.UserRole)
            if pid:
                assigned.add(pid)

        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("""
            SELECT p.id, p.number, p.name
            FROM players p
            WHERE p.team_id = ?
            ORDER BY CAST(p.number AS INTEGER), p.name
        """, (guest_team_id,))
        for pid, number, name in c.fetchall():
            if pid in assigned:
                continue
            display_num = number if number else "?"
            item = QListWidgetItem(f"#{display_num} - {name}")
            item.setData(Qt.UserRole, pid)
            self.guest_player_list.addItem(item)
        conn.close()

    def populate_location_combo(self):
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("""
            SELECT DISTINCT location 
            FROM games 
            WHERE location IS NOT NULL AND location != '' 
            ORDER BY location ASC
        """)
        locations = [row[0].strip() for row in c.fetchall() if row[0]]
        conn.close()

        self.location_combo.clear()
        self.location_combo.addItems(locations)

    def load_teams(self):
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT id, name FROM teams")
        for row in c.fetchall():
            self.home_combo.addItem(row[1], row[0])
            self.guest_combo.addItem(row[1], row[0])
        conn.close()

    def load_game_data(self, game_id):
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()

        # Load game details
        c.execute("""
            SELECT name, date, location, format, home_team_id, guest_team_id, is_complete, gamesheet_path, youtube_url
            FROM games
            WHERE id = ?
        """, (game_id,))
        row = c.fetchone()

        if row:
            name, date_str, location, format_, home_team_id, guest_team_id, is_complete, gs_path, youtube_url = row

            self.name_edit.setText(name or "")
            self.date_edit.setDate(QDate.fromString(date_str, "yyyy-MM-dd"))

            # Location combo
            index = self.location_combo.findText(location or "")
            if index >= 0:
                self.location_combo.setCurrentIndex(index)
            else:
                self.location_combo.setCurrentText(location or "")

            # Format combo
            format_map = {"Q": "Q - Quarters", "H": "H - Halves", "P": "P - Periods"}
            format_text = format_map.get(format_, format_)
            self.format_combo.setCurrentText(format_text)

            self.complete_checkbox.setChecked(bool(is_complete))
            self.youtube_url_edit.setText(youtube_url or "")

            # Load team names into read-only display fields
            c.execute("SELECT name FROM teams WHERE id = ?", (home_team_id,))
            home_row = c.fetchone()
            home_name = home_row[0] if home_row else "Unknown"
            self.home_team_display.setText(home_name)

            c.execute("SELECT name FROM teams WHERE id = ?", (guest_team_id,))
            guest_row = c.fetchone()
            guest_name = guest_row[0] if guest_row else "Unknown"
            self.guest_team_display.setText(guest_name)

            if gs_path:
                self.gamesheet_rel_path = gs_path
                abs_path = (get_gamesheets_folder() / gs_path).resolve()
                if abs_path.exists():
                    self.gamesheet_status.setText(f"Current: {gs_path}")
                    self.btn_clear_gamesheet.setEnabled(True)
                else:
                    self.gamesheet_status.setText(f"Missing file: {gs_path}")
            else:
                self.gamesheet_status.setText("No gamesheet attached")

            # Store team IDs for later use (available players, etc.)
            self.selected_home_team_id = home_team_id
            self.selected_guest_team_id = guest_team_id

        else:
            self.complete_checkbox.setChecked(False)

        # Load assigned game rosters
        c.execute("SELECT player_id, side FROM game_rosters WHERE game_id = ?", (game_id,))
        roster_rows = c.fetchall()

        selected_home = {pid for pid, side in roster_rows if side == 'home'}
        selected_guest = {pid for pid, side in roster_rows if side == 'guest'}

        # Home assigned
        self.home_game_list.clear()
        for pid in selected_home:
            c.execute("SELECT number, name FROM players WHERE id = ?", (pid,))
            p = c.fetchone()
            if p:
                display_num = p[0]
                #padded = str().zfill(2)
                item = QListWidgetItem(f"#{display_num} - {p[1]}")
                item.setData(Qt.UserRole, pid)
                self.home_game_list.addItem(item)

        # Guest assigned
        self.guest_game_list.clear()
        for pid in selected_guest:
            c.execute("SELECT number, name FROM players WHERE id = ?", (pid,))
            p = c.fetchone()
            if p:
                display_num = p[0]
                #padded = str(p[0]).zfill(2)
                item = QListWidgetItem(f"#{display_num} - {p[1]}")
                item.setData(Qt.UserRole, pid)
                self.guest_game_list.addItem(item)

        # Load available players (team roster minus assigned)
        # Home available
        self.home_player_list.clear()
        if self.selected_home_team_id:
            c.execute("""
                SELECT p.id, p.number, p.name
                FROM players p
                WHERE p.team_id = ?
                ORDER BY CAST(p.number AS INTEGER), p.name
            """, (self.selected_home_team_id,))
            for pid, number, name in c.fetchall():
                if pid in selected_home:
                    continue
                display_num = number if number else "?"
                #padded = str(number or '??').zfill(2)
                item = QListWidgetItem(f"#{display_num} - {name}")
                item.setData(Qt.UserRole, pid)
                self.home_player_list.addItem(item)

        # Guest available
        self.guest_player_list.clear()
        if self.selected_guest_team_id:
            c.execute("""
                SELECT p.id, p.number, p.name
                FROM players p
                WHERE p.team_id = ?
                ORDER BY CAST(p.number AS INTEGER), p.name
            """, (self.selected_guest_team_id,))
            for pid, number, name in c.fetchall():
                if pid in selected_guest:
                    continue
                display_num = number if number else "?"
                #padded = str(number or '??').zfill(2)
                item = QListWidgetItem(f"#{display_num} - {name}")
                item.setData(Qt.UserRole, pid)
                self.guest_player_list.addItem(item)

        conn.close()

    def handle_gamesheet(self):
        game_name = self.name_edit.text().strip() or "Unnamed_Game"

        if self.date_edit.date().isValid():
            date_str = self.date_edit.date().toString('yyyy-MM-dd')
            base_name = f"{date_str}_{game_name}"
        else:
            # Fallback if no valid date
            date_str = datetime.datetime.now().strftime("%Y-%m-%d")
            base_name = f"{date_str}_{game_name}"

        # Optional: start file dialog in Pictures or last used folder
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Gamesheet Image",
            str(Path.home() / "Pictures"),
            "Images (*.png *.jpg *.jpeg *.bmp *.tiff);;All Files (*.*)"
        )
        if not path:
            return

        editor = GamesheetEditorDialog(self, path, game_name_hint=game_name)
        if editor.exec_() != QDialog.Accepted:
            return

        #print("[DEBUG] Editor accepted — getting image")
        img = editor.get_edited_image()
        if not img:
            print("[DEBUG] No image returned from editor")
            return
        print(f"[DEBUG] Got image from editor — size={img.size}, mode={img.mode}")

        # Generate clean filename — no extension yet
        timestamp = datetime.now().strftime("%H%M")

        clean_base = f"{base_name}_{timestamp}".replace(" ", "_")  # replace spaces with underscores

        final_name = sanitize_filename(clean_base) + ".png"

        # Make unique if needed
        save_path = get_gamesheets_folder() / final_name
        counter = 1
        while save_path.exists():
            final_name = f"{sanitize_filename(clean_base)}_{counter:03d}.png"
            save_path = get_gamesheets_folder() / final_name
            counter += 1

        try:
            # Save as PNG (lossless, no quality param needed)
            img.save(save_path, "PNG")

            # Store relative path (use whichever variant fits your DB/needs)
            self.gamesheet_rel_path = str(final_name)
            # Alternative: self.gamesheet_rel_path = str(save_path.relative_to(PROJECT_ROOT))

            self.gamesheet_status.setText(f"Saved: {final_name}")
            self.btn_clear_gamesheet.setEnabled(True)

            # Optional: quiet success message (uncomment if desired)
            # QMessageBox.information(self, "Saved", f"Gamesheet saved as:\n{final_name}")

        except Exception as e:
            print(f"[ERROR] Save failed: {e}")
            QMessageBox.critical(self, "Save Failed", str(e))

    def clear_gamesheet(self):
        reply = QMessageBox.question(
            self, "Remove Gamesheet?",
            "Remove the gamesheet link?\n(File stays on disk.)",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            self.gamesheet_rel_path = None
            self.gamesheet_status.setText("No gamesheet attached")
            self.btn_clear_gamesheet.setEnabled(False)

    def move_roster(self, src, dst):
        selected = src.selectedItems()
        for item in selected:
            dst.addItem(item.clone())
            src.takeItem(src.row(item))

    def save_game(self):
        name = self.name_edit.text().strip()
        date = self.date_edit.date().toString("yyyy-MM-dd")
        location = self.location_combo.currentText().strip()
        format_ = self.format_combo.currentText()[0]
        is_complete = 1 if self.complete_checkbox.isChecked() else 0
        youtube_url = self.youtube_url_edit.text().strip()

        # Get team IDs (from stored attributes)
        home_team_id = self.selected_home_team_id
        guest_team_id = self.selected_guest_team_id

        if not name or not home_team_id or not guest_team_id:
            QMessageBox.warning(self, "Error", "Game name, home team, and guest team required.")
            return

        if home_team_id == guest_team_id:
            QMessageBox.warning(self, "Invalid Teams", "Home and Guest teams cannot be the same.")
            return

        # No gamesheet prompt here — assume user already edited/saved via separate button
        # self.gamesheet_rel_path should already be set from handle_gamesheet() if they did

        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()

        try:
            if self.game_id:
                # UPDATE – looks correct
                c.execute("""
                    UPDATE games
                    SET name = ?, date = ?, location = ?, format = ?, 
                        home_team_id = ?, guest_team_id = ?, is_complete = ?, gamesheet_path = ?, youtube_url = ?
                    WHERE id = ?
                """, (name, date, location, format_, home_team_id, guest_team_id, is_complete, self.gamesheet_rel_path, youtube_url,
                      self.game_id))
                game_id = self.game_id
            else:
                # INSERT – fixed: no =? in column list
                c.execute("""
                    INSERT INTO games
                    (name, date, location, format, home_team_id, guest_team_id, is_complete, gamesheet_path, youtube_url)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (name, date, location, format_, home_team_id, guest_team_id, is_complete, self.gamesheet_rel_path, youtube_url))
                game_id = c.lastrowid

            conn.commit()

            # Collect current rosters
            current_home = set()
            for i in range(self.home_game_list.count()):
                pid = self.home_game_list.item(i).data(Qt.UserRole)
                if pid:
                    current_home.add(pid)

            current_guest = set()
            for i in range(self.guest_game_list.count()):
                pid = self.guest_game_list.item(i).data(Qt.UserRole)
                if pid:
                    current_guest.add(pid)

            # Detect removed players
            removed_home = self.original_home_roster - current_home
            removed_guest = self.original_guest_roster - current_guest
            all_removed = removed_home | removed_guest

            reassigned_total = 0
            if all_removed:
                affected = 0
                for pid in all_removed:
                    c.execute(
                        "SELECT COUNT(*) FROM events e JOIN videos v ON e.video_id = v.id WHERE v.game_id = ? AND e.player_id = ?",
                        (game_id, pid))
                    affected += c.fetchone()[0]

                if affected > 0:
                    reply = QMessageBox.question(
                        self,
                        "Roster Changes Affect Events",
                        f"Removing {len(all_removed)} player(s) will affect {affected} event(s).\n\n"
                        "Events will become team-level (player_id = NULL, team_side set).\n"
                        "Continue?",
                        QMessageBox.Yes | QMessageBox.No
                    )
                    if reply == QMessageBox.No:
                        raise Exception("Save canceled by user")

                # Reassign events (single connection)
                for pid in all_removed:
                    # Get player's side from original roster (before removal)
                    c.execute("""
                        SELECT side FROM game_rosters 
                        WHERE game_id = ? AND player_id = ?
                    """, (game_id, pid))
                    row = c.fetchone()
                    player_side = row[0] if row else None

                    if player_side:
                        c.execute("""
                            UPDATE events 
                            SET player_id = NULL,
                                team_side = COALESCE(team_side, ?)
                            WHERE player_id = ? 
                              AND video_id IN (SELECT id FROM videos WHERE game_id = ?)
                        """, (player_side, pid, game_id))
                    else:
                        c.execute("""
                            UPDATE events 
                            SET player_id = NULL
                            WHERE player_id = ? 
                              AND video_id IN (SELECT id FROM videos WHERE game_id = ?)
                        """, (pid, game_id))

                    reassigned_total += c.rowcount

            # Clear old rosters
            c.execute("DELETE FROM game_rosters WHERE game_id = ?", (game_id,))

            # Insert new rosters
            for pid in current_home:
                c.execute("INSERT INTO game_rosters (game_id, player_id, side) VALUES (?, ?, 'home')",
                          (game_id, pid))

            for pid in current_guest:
                c.execute("INSERT INTO game_rosters (game_id, player_id, side) VALUES (?, ?, 'guest')",
                          (game_id, pid))

            conn.commit()

            msg = "Game saved successfully."
            if reassigned_total > 0:
                msg += f"\n{reassigned_total} event(s) reassigned to team-level."
            QMessageBox.information(self, "Saved", msg)

            self.accept()

        except Exception as e:
            conn.rollback()
            QMessageBox.critical(self, "Save Failed", str(e))
        finally:
            conn.close()

class GameVideosDialog(QDialog):
    def __init__(self, parent, game_id):
        super().__init__(parent)
        self.game_id = game_id
        self.setWindowTitle("Game Videos")

        # Fetch game name for context
        game_name = "Unknown Game"
        try:
            conn = sqlite3.connect(DB_NAME)
            c = conn.cursor()
            c.execute("SELECT name, location FROM games WHERE id = ?", (game_id,))
            row = c.fetchone()
            if row:
                game_name = row[0]
                game_location = row[1]
            conn.close()
        except Exception as e:
            print("GameVideosDialog fetch error:", str(e))

        # Set meaningful title
        self.setWindowTitle(f"Game Videos – {game_name} - {game_location}")
        self.setGeometry(200, 200, 600, 500)


        layout = QVBoxLayout(self)

        self.video_table = QTableWidget()
        self.video_table.setColumnCount(5)
        self.video_table.setHorizontalHeaderLabels(["Video Name", "Duration", "Quarters/Halves", "Events", "File"])
        self.video_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.video_table.setSelectionMode(QTableWidget.SingleSelection)
        self.video_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)  # filename expands
        self.video_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.video_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.video_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.video_table.setColumnWidth(4, 80)  # narrow button column
        self.video_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Fixed)
        self.video_table.verticalHeader().setVisible(False)
        layout.addWidget(self.video_table)


        layout.addWidget(self.video_table)
        btn_layout = QHBoxLayout()
        add_btn = QPushButton("Add Video")
        add_btn.clicked.connect(self.add_video)
        btn_layout.addWidget(add_btn)
        view_btn = QPushButton("Tag/Edit Events")
        view_btn.setStyleSheet("""
                    QPushButton {
                        background-color: #DCFFDC;      /* your light green */
                        border: 1px solid #000000;      /* slightly darker green border */
                        border-radius: 6px;
                        padding: 6px 16px;
                    }
                    QPushButton:hover {
                        background-color: #BDFCBD;      /* darker green on hover */
                    }
                """)

        view_btn.clicked.connect(self.view_video)
        btn_layout.addWidget(view_btn)
        delete_btn = QPushButton("Delete Video + Events")
        delete_btn.clicked.connect(self.delete_video)
        btn_layout.addWidget(delete_btn)

        # Close button on the far right
        close_btn = QPushButton("Close")
        close_btn.setFixedWidth(100)
        close_btn.clicked.connect(self.close)
        btn_layout.addWidget(close_btn)

        layout.addLayout(btn_layout)

        # Load videos immediately (now with argument)
        if self.game_id:
            self.load_videos(self.game_id)
        else:
            # Add mode or no game → empty table
            self.video_table.setRowCount(0)

    def load_videos(self, game_id):
        self.video_table.setRowCount(0)

        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()

        c.execute("""
            SELECT v.id, v.filename, v.length_seconds, v.record_date
            FROM videos v
            WHERE v.game_id = ?
            ORDER BY v.record_date, v.id
        """, (game_id,))

        video_rows = c.fetchall()

        for row_idx, (vid_id, filename, length_sec, record_date) in enumerate(video_rows):
            self.video_table.insertRow(row_idx)

            # ── Simple existence check on the stored filename ─────────────────────
            from pathlib import Path
            file_exists = Path(filename).is_file()  # checks exactly what's in DB

            # Column 0: Filename (left-aligned by default)
            display_name = os.path.basename(filename)
            if not file_exists:
                display_name += "  [missing]"

            filename_item = QTableWidgetItem(display_name)
            filename_item.setData(Qt.UserRole, vid_id)  # store video_id

            if not file_exists:
                filename_item.setForeground(QColor("crimson"))  # bright red
                # Optional: make it more obvious
                filename_item.setToolTip(f"File not found at:\n{filename}")
                # Optional strikethrough:
                # font = filename_item.font()
                # font.setStrikeOut(True)
                # filename_item.setFont(font)
            # else: no need to set color — it will use default

            self.video_table.setItem(row_idx, 0, filename_item)

            # Column 1: Duration (center)
            if length_sec is not None:
                total_sec = int(round(length_sec))
                minutes = total_sec // 60
                seconds = total_sec % 60
                duration_str = f"{minutes:02d}:{seconds:02d}"
            else:
                duration_str = "Unknown"
            duration_item = QTableWidgetItem(duration_str)
            duration_item.setTextAlignment(Qt.AlignCenter)
            self.video_table.setItem(row_idx, 1, duration_item)

            # Column 2: Quarters with events (center)
            c.execute("""
                SELECT DISTINCT quarter
                FROM events
                WHERE video_id = ?
                ORDER BY quarter
            """, (vid_id,))
            quarters = [q[0] for q in c.fetchall() if q[0]]
            quarters_str = ", ".join(sorted(set(quarters))) if quarters else "-"
            quarters_item = QTableWidgetItem(quarters_str)
            quarters_item.setTextAlignment(Qt.AlignCenter)
            self.video_table.setItem(row_idx, 2, quarters_item)

            # Column 3: Total events count (center)
            c.execute("SELECT COUNT(*) FROM events WHERE video_id = ?", (vid_id,))
            event_count = c.fetchone()[0]
            events_item = QTableWidgetItem(str(event_count))
            events_item.setTextAlignment(Qt.AlignCenter)
            self.video_table.setItem(row_idx, 3, events_item)

            # ── NEW: Column 4 - Relink button (only if missing) ──────────────────
            btn_widget = QWidget()
            btn_layout = QHBoxLayout(btn_widget)
            btn_layout.setContentsMargins(2, 2, 2, 2)
            btn_layout.setSpacing(0)

            btn_layout.addStretch()  # ← pushes content to the right

            if not file_exists:
                relink_btn = QPushButton("Find")
                relink_btn.setFixedWidth(70)
                relink_btn.setStyleSheet("""
                            QPushButton { 
                                background: #ff4444; 
                                color: white; 
                                border: none; 
                                border-radius: 4px; 
                                padding: 4px; 
                            }
                            QPushButton:hover { background: #cc0000; }
                        """)
                # Connect directly with lambda capturing vid_id and current filename
                relink_btn.clicked.connect(lambda checked, v=vid_id, old=filename: self.relink_video(v, old))
                btn_layout.addWidget(relink_btn)
            else:
                # Show green checkmark for existing / good files
                check_label = QLabel("✓")
                check_label.setStyleSheet("""
                                color: #28a745;          /* success green */
                                font-weight: bold;
                                font-size: 16px;
                            """)
                check_label.setAlignment(Qt.AlignRight)
                btn_layout.addWidget(check_label)
                pass

            btn_layout.addStretch()
            self.video_table.setCellWidget(row_idx, 4, btn_widget)

        conn.close()

        # Auto-resize columns after loading
        self.video_table.resizeColumnsToContents()
        self.video_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.video_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.video_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.video_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)

    def relink_video(self, video_id: int, old_filename: str):
        """Let user pick a new file location for this video entry"""
        dialog = QFileDialog(self)
        dialog.setFileMode(QFileDialog.ExistingFile)
        dialog.setNameFilter("Video files (*.mp4 *.mov *.avi *.mkv)")
        dialog.setDirectory(str(Path(old_filename).parent))  # start in old folder if possible

        if dialog.exec_():
            new_file = dialog.selectedFiles()[0]
            if not new_file:
                return

            new_path = Path(new_file).resolve()

            if not new_path.is_file():
                QMessageBox.warning(self, "Invalid file", "Selected path is not a valid file.")
                return

            # Optional: basic sanity check (size, extension, etc.)
            # but for now we trust the user

            conn = sqlite3.connect(DB_NAME)
            c = conn.cursor()
            try:
                c.execute("UPDATE videos SET filename = ? WHERE id = ?", (str(new_path), video_id))
                conn.commit()
                QMessageBox.information(self, "Success", "Video path updated.\nNew location:\n" + str(new_path))
            except Exception as e:
                conn.rollback()
                QMessageBox.critical(self, "Database error", str(e))
            finally:
                conn.close()

            # Refresh the whole list (simplest)
            self.load_videos(self.game_id)
            # Alternative: only refresh the one row — more complex, needs row_idx lookup

    def add_video(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Select Video File", "",
                                                   "Video Files (*.mp4 *.mov *.avi *.mkv)")
        if not file_path:
            return

        instance = vlc.Instance()
        media = instance.media_new(file_path)
        media.parse()
        length_ms = media.get_duration()
        length_sec = length_ms // 1000 if length_ms > 0 else 0
        mod_time = os.path.getmtime(file_path)
        record_date = datetime.fromtimestamp(mod_time).date().isoformat()

        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("""
                  INSERT INTO videos (game_id, filename, length_seconds, record_date)
                  VALUES (?, ?, ?, ?)
                  """, (self.game_id, file_path, length_sec, record_date))
        video_id = c.lastrowid
        conn.commit()
        conn.close()

        item = QListWidgetItem(os.path.basename(file_path))
        item.setData(Qt.UserRole, video_id)  # ← now integer!
        self.load_videos(self.game_id)


    def add_video(self):
        files = QFileDialog.getOpenFileNames(self, "Add Game Video", "", "Video Files (*.mp4 *.mov *.avi *.mkv)")
        if not files[0]:
            return

        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()

        try:
            for file_path in files[0]:
                # Optional: probe duration
                try:
                    probe = ffmpeg.probe(file_path)
                    duration_sec = float(probe['format']['duration'])
                except:
                    duration_sec = None

                c.execute("""
                    INSERT INTO videos (game_id, filename, length_seconds, record_date)
                    VALUES (?, ?, ?, ?)
                """, (self.game_id, file_path, duration_sec, datetime.now().date()))

            conn.commit()

            # Refresh the entire table (easiest, most reliable)
            self.load_videos(self.game_id)

            QMessageBox.information(self, "Added", "Video(s) added successfully.")

        except Exception as e:
            conn.rollback()
            QMessageBox.critical(self, "Error", f"Failed to add video:\n{str(e)}")
        finally:
            conn.close()

        #self.video_table.resizeColumnsToContents()

    def delete_video(self):

        selected = self.video_table.currentRow()
        if selected < 0:
            return  # or warning

        item = self.video_table.item(selected, 0)  # column 0 has filename + UserRole

        if not item:
            return

        data = item.data(Qt.UserRole)

        if isinstance(data, int):
            video_id = data
        elif isinstance(data, tuple) and len(data) >= 1:
            filename = data[0]
            conn = sqlite3.connect(DB_NAME)
            c = conn.cursor()
            c.execute("SELECT id FROM videos WHERE game_id = ? AND filename = ?",
                      (self.game_id, filename))
            row = c.fetchone()
            if not row:
                # Not in DB yet → just remove from list
                self.video_list.takeItem(self.video_list.row(item))
                conn.close()
                return
            video_id = row[0]
            conn.close()
        else:
            return

        reply = QMessageBox.question(self, "Delete", "Delete video and events?",
                                     QMessageBox.Yes | QMessageBox.No)
        if reply != QMessageBox.Yes:
            return

        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("DELETE FROM events WHERE video_id = ?", (video_id,))
        c.execute("DELETE FROM videos WHERE id = ?", (video_id,))
        conn.commit()
        conn.close()

        self.load_videos(self.game_id)  # ← add self.game_id
        QMessageBox.information(self, "Deleted", "Video and events removed.")
        #self.video_table.resizeColumnsToContents()


    def view_video(self):

        selected = self.video_table.currentRow()
        if selected < 0:
            return  # or warning

        item = self.video_table.item(selected, 0)  # column 0 has filename + UserRole

        if not item:
            return

        data = item.data(Qt.UserRole)

        # Handle both cases: integer ID (from DB) or tuple (just added, not saved yet)
        if isinstance(data, int):
            video_id = data
        elif isinstance(data, tuple) and len(data) >= 1:
            # For newly added (not saved) videos → we don't have DB id yet
            filename = data[0]
            conn = sqlite3.connect(DB_NAME)
            c = conn.cursor()
            c.execute("SELECT id FROM videos WHERE game_id = ? AND filename = ?",
                      (self.game_id, filename))
            row = c.fetchone()
            conn.close()
            if not row:
                QMessageBox.warning(self, "Not Found",
                                    "This video hasn't been saved to the database yet.\n"
                                    "Save the game first, then try again.")
                return
            video_id = row[0]
        else:
            QMessageBox.warning(self, "Error", "Invalid video data.")
            return

        # Now we have a real video_id → proceed
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT filename FROM videos WHERE id = ?", (video_id,))
        file_path = c.fetchone()[0]
        conn.close()

        player = VideoPlayerEventDialog(self, file_path, self.game_id, video_id)
        player.setWindowFlags(
            player.windowFlags()  # Keep existing flags (including Qt.Dialog)
            | Qt.WindowMaximizeButtonHint
            | Qt.WindowMinimizeButtonHint  # Optional but usually wanted for symmetry
        )
        player.exec_()


class EventEditorDialog(QDialog):
    last_quarter = "Q1"

    QUARTER_COLORS = {
        "Q1": "#e6f3ff",  # very light blue
        "Q2": "#fff0e6",  # very light orange
        "Q3": "#f0fff0",  # very light green
        "Q4": "#ffe6e6",  # very light red/pink
        "H1": "#e6f3ff",  # if using halves
        "H2": "#f0fff0",
        "OT": "#f5f5f5",  # overtime or neutral gray
        None: "#ffffff"  # default white
    }

    def __init__(self, parent, game_id, time_sec, video_id, event_id=None):
        super().__init__(parent)
        self.event_id = event_id
        self.video_id = video_id
        self.game_id = game_id

        # Load format right here
        self.game_format = self._load_game_format()

        # ── NEW ────────────────────────────────────────────────
        self.highlight_checkbox = None  # will create later

        #print(f"EventEditorDialog init - game_id: {game_id}, video_id: {video_id}")

        self.setWindowTitle("Event Tagger")
        self.setGeometry(240, 240, 400, 320)   # ← Use this

        # Main vertical layout for the whole dialog
        layout = QVBoxLayout(self)

        # ── Horizontal layout for Time + Quarter ───────────────────────────────
        time_quarter_layout = QHBoxLayout()

        # Left side: Time
        time_group = QVBoxLayout()
        time_group.addWidget(QLabel("Event Time:"))
        m = int(time_sec // 60)
        s = int(time_sec % 60)
        self.time_edit = QLineEdit(f"{m:02d}:{s:02d}")
        self.time_edit.setReadOnly(True)
        time_group.addWidget(self.time_edit)
        time_quarter_layout.addLayout(time_group)

        # Right side: Period (dynamic label + items)
        quarter_group = QVBoxLayout()

        # Dynamic label: "Quarter:", "Half:", or "Period:"
        if self.game_format == "H":
            period_label_text = "Half:"
            base_periods = ["H1", "H2"]
        elif self.game_format == "Q":
            period_label_text = "Quarter:"
            base_periods = ["Q1", "Q2", "Q3", "Q4"]
        else:
            # fallback / future-proof
            period_label_text = "Period:"
            base_periods = ["P1", "P2", "P3", "P4"]

        quarter_group.addWidget(QLabel(period_label_text))

        self.quarter_combo = QComboBox()

        # Add regulation periods
        self.quarter_combo.addItems(base_periods)

        # Always allow overtime (most common formats support it)
        self.quarter_combo.addItems(["OT1", "OT2"])
        # You can add OT3, OT4 later if needed:  self.quarter_combo.addItems([f"OT{i}" for i in range(1,5)])

        # Try to restore last used value — only if it exists in the new list
        last = EventEditorDialog.last_quarter
        if last in [self.quarter_combo.itemText(i) for i in range(self.quarter_combo.count())]:
            self.quarter_combo.setCurrentText(last)
        else:
            self.quarter_combo.setCurrentIndex(0)  # default to first period

        quarter_group.addWidget(self.quarter_combo)
        time_quarter_layout.addLayout(quarter_group)

        # Optional: make both columns take similar space
        time_quarter_layout.setStretch(0, 1)
        time_quarter_layout.setStretch(1, 1)

        # Add the time+quarter row to the main layout
        layout.addLayout(time_quarter_layout)

        player_l = QHBoxLayout()
        player_l.setSpacing(8)

        player_l.addWidget(QLabel("Player:   "))
        self.player_combo = QComboBox()
        player_l.addWidget(self.player_combo)
        player_l.setStretch(1, 1)
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("""SELECT p.id, p.number, p.name, gr.side 
                     FROM game_rosters gr JOIN players p ON gr.player_id = p.id 
                     WHERE gr.game_id = ? ORDER BY gr.side, CAST(p.number AS INTEGER)""", (game_id,))
        for row in c.fetchall():
            prefix = "H" if row[3] == 'home' else "G"
            #padded_num = str(row[1]).zfill(2)
            display_num = row[1]
            self.player_combo.addItem(f"{prefix} - #{display_num} {row[2]}", row[0])
        self.player_combo.addItem("H -", None)
        self.player_combo.addItem("G -", None)
        conn.close()

        layout.addLayout(player_l)

        # ── Clean Two-Column Layout (Left: Shots | Right: Non-Shots) ─────
        event_columns = QHBoxLayout()
        event_columns.setSpacing(40)

        # Left Column - Shot Events (6 items)
        left_col = QVBoxLayout()
        left_col.setSpacing(6)
        self.shot_radios = {}

        # Right Column - Non-Shot Events (8 items)
        right_col = QVBoxLayout()
        right_col.setSpacing(6)
        self.nonshot_radios = {}

        self.event_type_group = QButtonGroup(self)
        self.event_type_group.setExclusive(True)

        all_types = get_event_types_for_combo()

        left_col.addWidget(QLabel("Type:"))

        for et in all_types:
            radio = QRadioButton(et)
            radio.setToolTip(et)
            radio.setMinimumHeight(26)

            self.event_type_group.addButton(radio)

            if is_shot_event(et):
                self.shot_radios[et] = radio
                left_col.addWidget(radio)
            else:
                self.nonshot_radios[et] = radio
                right_col.addWidget(radio)

        # === KEY CHANGE: Add spacer at bottom of shorter column ===
        left_col.addStretch(1)  # This pushes the 6 items to the top

        # Add both columns
        event_columns.addLayout(left_col)
        event_columns.addLayout(right_col)
        event_columns.setStretch(0, 1)
        event_columns.setStretch(1, 1)

        layout.addLayout(event_columns)

        # Connect selection change
        self.event_type_group.buttonClicked.connect(self.on_event_type_changed)

        # ── Compact SINGLE ROW: Shot Location + ? + Combo ─────────────────────
        loc_l = QHBoxLayout()
        loc_l.setSpacing(8)

        self.location_label = QLabel("Shot Location:")
        loc_l.addWidget(self.location_label)

        self.location_combo = QComboBox()
        self.location_options = [
            "-", "FT - Freethrow",
            "OCL - Outside Corner - Left", "OCR - Outside Corner - Right",
            "OPT - Outside Point - Top", "OWL - Outside Wing - Left", "OWR - Outside Wing - Right",
            "PTH - Paint - High", "PTL - Paint - Low",
            "SCL - Short Corner - Left", "SCR - Short Corner - Right",
            "SWL - Short Wing - Left", "SWR - Short Wing - Right"
        ]
        self.location_combo.addItems(self.location_options)
        #self.location_combo.setMinimumWidth(320)

        loc_l.addWidget(self.location_combo)
        #loc_l.setStretch(0, 1)
        loc_l.setStretch(1, 1)

        help_btn = QPushButton("?")
        help_btn.setFixedWidth(28)
        help_btn.setFixedHeight(28)
        help_btn.clicked.connect(self.show_location_image)
        self.help_btn = help_btn
        loc_l.addWidget(help_btn)

        layout.addLayout(loc_l)

        # ── Shot Quality Radio Buttons ─────────────────────────────────────
        self.shot_quality_label = QLabel("Shot Quality:   ")
        self.shot_quality_label.setVisible(False)

        self.shot_quality_group = QButtonGroup(self)
        self.shot_quality_group.setExclusive(True)
        self.quality_radios = {}  # ← Create the dict FIRST

        quality_hbox = QHBoxLayout()
        quality_hbox.addWidget(self.shot_quality_label)

        for full_name, code in SHOT_QUALITY:
            radio = QRadioButton(code)
            radio.setToolTip(full_name)
            self.shot_quality_group.addButton(radio)
            quality_hbox.addWidget(radio)
            self.quality_radios[code] = radio

        quality_hbox.addStretch()
        layout.addLayout(quality_hbox)

        highlight_l = QHBoxLayout()
        highlight_l.setSpacing(8)

        self.highlight_checkbox = QCheckBox("Highlight Event")
        self.highlight_checkbox.setStyleSheet("font-weight: bold")
        self.highlight_checkbox.setChecked(False)  # default

        highlight_l.addStretch()
        highlight_l.addWidget(self.highlight_checkbox)
        highlight_l.addStretch()
        layout.addLayout(highlight_l)

        layout.addSpacing(10)  # nice separation before buttons

        # ── Buttons Row ─────────────────────────────────────────────────────
        btn_layout = QHBoxLayout()
        #btn_layout.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)  # closes dialog without saving
        btn_layout.addWidget(cancel_btn)

        save_btn = QPushButton("Save + Close")
        save_btn.clicked.connect(self.save_event)
        save_btn.setDefault(True)  # Enter key activates it
        btn_layout.addWidget(save_btn)

        layout.addLayout(btn_layout)

        # Final toggle to set correct visibility
        self.toggle_location()
        self.adjustSize()

        # ==================== LOAD EXISTING EVENT DATA WHEN EDITING ====================
        if self.event_id:
            conn = sqlite3.connect(DB_NAME)
            c = conn.cursor()
            c.execute("""SELECT type, location, shot_quality, is_highlight 
                                 FROM events WHERE id = ?""", (self.event_id,))
            row = c.fetchone()
            conn.close()

            if row:
                saved_type, saved_location, saved_shot_quality, saved_highlight = row

                # 1. Restore Event Type → this will trigger toggle_location()
                restored = False
                for radio_dict in (self.shot_radios, self.nonshot_radios):
                    for text, radio in radio_dict.items():
                        if text == saved_type:
                            radio.setChecked(True)
                            # Small delay so toggle finishes first
                            self.toggle_location(saved_type)
                            restored = True
                            break
                    if restored:
                        break

                # 2. Restore Location
                if saved_location and self.location_combo.isEnabled():
                    idx = self.location_combo.findText(saved_location)
                    if idx >= 0:
                        self.location_combo.setCurrentIndex(idx)

                # 3. Restore Shot Quality — Do this LAST and force it
                if saved_shot_quality and saved_shot_quality in self.quality_radios:
                    # Make sure visibility is on
                    self.toggle_shot_quality_visibility(True)

                    # Force select the correct radio
                    self.quality_radios[saved_shot_quality].setChecked(True)

                # 4. Restore Highlight
                if hasattr(self, 'highlight_checkbox'):
                    self.highlight_checkbox.setChecked(bool(saved_highlight))

    def on_event_type_changed(self, button):
        """Called when user clicks any event radio"""
        selected = button.text().strip()
        # Optional: remember last used type
        # EventEditorDialog.last_event_type = selected
        self.toggle_location(selected)

    def get_selected_event_type(self):
        """Helper to get currently selected event type text"""
        btn = self.event_type_group.checkedButton()
        return btn.text().strip() if btn else ""

    def _load_game_format(self):
        if not self.game_id:
            return "Q"
        try:
            conn = sqlite3.connect(DB_NAME)
            c = conn.cursor()
            c.execute("SELECT format FROM games WHERE id = ?", (self.game_id,))
            row = c.fetchone()
            conn.close()
            if row and row[0]:
                return row[0].upper()
            return "Q"
        except Exception:
            return "Q"  # silent fallback on error

    def toggle_location(self, selected=None):
        """Handles visibility of Shot Location and Shot Quality"""
        if selected is None:
            selected = self.get_selected_event_type()

        if not selected:
            self.toggle_shot_quality_visibility(False)
            return

        is_shot = is_shot_event(selected)
        short_code = get_short_code(selected)

        # Enable location combo only for 2P/3P field goals (not FT)
        enable_location = is_shot and short_code.startswith(('2P', '3P'))

        self.location_combo.setEnabled(enable_location)
        self.location_combo.clear()
        self.location_combo.addItem("-")

        if not enable_location:
            self.location_combo.setCurrentIndex(0)
            self.toggle_shot_quality_visibility(False)
            return

        # Populate locations
        if short_code.startswith('3P'):
            locations = [
                "OCL - Outside Corner - Left", "OCR - Outside Corner - Right",
                "OPT - Outside Point - Top",
                "OWL - Outside Wing - Left", "OWR - Outside Wing - Right"
            ]
        else:  # 2P
            locations = [
                "FT - Freethrow",
                "PTH - Paint - High", "PTL - Paint - Low",
                "SWL - Short Wing - Left", "SWR - Short Wing - Right",
                "SCL - Short Corner - Left", "SCR - Short Corner - Right"
            ]

        self.location_combo.addItems(locations)
        self.location_combo.setCurrentIndex(0)

        self.toggle_shot_quality_visibility(True)

    def toggle_shot_quality_visibility(self, show: bool):
        """Show or hide Shot Location + Shot Quality and resize window accordingly"""
        # --- Shot Quality ---
        self.shot_quality_label.setVisible(show)

        for radio in self.quality_radios.values():
            radio.setVisible(show)

        if not show:
            # Clear selection when hiding
            self.shot_quality_group.setExclusive(False)
            for radio in self.quality_radios.values():
                radio.setChecked(False)
            self.shot_quality_group.setExclusive(True)

        # --- Shot Location (label + combo + help button) ---
        if hasattr(self, 'location_label'):
            self.location_label.setVisible(show)
            self.location_combo.setVisible(show)

        if hasattr(self, 'help_btn'):
            self.help_btn.setVisible(show)

        # --- Auto-resize the dialog ---
        self.adjustSize()  # This is the key line
        # Optional: prevent it from getting too small
        #self.setMinimumHeight(280)  # adjust based on your layout
        QTimer.singleShot(10, self.adjustSize)  # small delay helps
        QTimer.singleShot(30, lambda: self.resize(self.minimumSizeHint()))

    def show_location_image(self):
        popup = QDialog(self)
        popup.setWindowTitle("Shot Location Guide")
        popup.setGeometry(480, 180, 460, 320)

        layout = QVBoxLayout(popup)
        layout.setContentsMargins(10, 10, 10, 10)

        img_label = QLabel()
        #pixmap = QPixmap("court_diagram.png")
        pixmap = QPixmap(":/Shot_Location_Diagram_v1.svg")
        if pixmap.isNull():
            img_label.setText("Image not found.\nCheck your resources.py file")
            img_label.setStyleSheet("color: red; font-size: 16px;")
        else:
            scaled = pixmap.scaled(420, 420, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            img_label.setPixmap(scaled)
        img_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(img_label, stretch=4)

        #legend_edit = QTextEdit()
        legend_edit = QTextBrowser()
        legend_edit.setOpenExternalLinks(True)  # ← Critical
        legend_edit.setReadOnly(True)
        legend_edit.setFixedHeight(240)
        legend_edit.setStyleSheet("font-size: 14px; background-color: #f8f8f8; border: 1px solid #ddd;")
        legend_html = """
        <h3 style="text-align:center; margin:2px 0;">Shot Location Legend</h3>
        <table border="1" align="center" width="100%" cellpadding="2" cellspacing="2" style="width:100%; border-collapse:collapse;">
            <tr style="background-color:#e0e0e0; font-weight:bold;">
                <td style="text-align:center;">Code</td><td>Description</td>
                <td style="text-align:center;">Code</td><td>Description</td>
            </tr>
            <tr><td style="text-align:center;">PTL</td><td>Paint - Low</td><td style="text-align:center;">OCL</td><td>Outside Corner - Left</td></tr>
            <tr><td style="text-align:center;">PTH</td><td>Paint - High</td><td style="text-align:center;">OCR</td><td>Outside Corner - Right</td></tr>
            <tr><td style="text-align:center;">FT</td><td>Freethrow</td><td style="text-align:center;">OWL</td><td>Outside Wing - Left</td></tr>
            <tr><td style="text-align:center;">SCL</td><td>Short Corner - Left</td><td style="text-align:center;">OWR</td><td>Outside Wing - Right</td></tr>
            <tr><td style="text-align:center;">SCR</td><td>Short Corner - Right</td><td style="text-align:center;">OPT</td><td>Outside Point - Top</td></tr>
            <tr><td style="text-align:center;">SWL</td><td>Short Wing - Left</td><td></td><td></td></tr>
            <tr><td style="text-align:center;">SWR</td><td>Short Wing - Right</td><td></td><td></td></tr>
        </table>
        """
        legend_edit.setHtml(legend_html)
        layout.addWidget(legend_edit, stretch=1)

        close_btn = QPushButton("Close")
        close_btn.setFixedWidth(140)
        close_btn.clicked.connect(popup.close)
        layout.addWidget(close_btn, alignment=Qt.AlignRight)

        popup.exec_()

    def save_event(self):
        # ── Time conversion ─────────────────────────────────────────────────────
        time_str = self.time_edit.text()
        try:
            m, s = map(int, time_str.split(':'))
            time_ms = (m * 60 + s) * 1000
        except:
            QMessageBox.warning(self, "Error", "Invalid time format")
            return

        quarter = self.quarter_combo.currentText()
        EventEditorDialog.last_quarter = quarter

        player_id = self.player_combo.currentData()

        # === UPDATED: Get Event Type from Radio Buttons ===
        type_ = self.get_selected_event_type()  # ← This is the key change

        if not type_:
            QMessageBox.warning(self, "Error", "Please select an Event Type")
            return

        # Location (only for enabled 2P/3P shots)
        location = self.location_combo.currentText() if self.location_combo.isEnabled() else None

        # ── Shot Quality (A/B/C/D) ─────────────────────────────────────
        shot_quality = None
        for code, radio in self.quality_radios.items():
            if radio.isChecked():
                shot_quality = code
                break

        # Determine team_side when no player is selected
        team_side = None
        if player_id is None:
            text = self.player_combo.currentText()
            team_side = 'home' if text.startswith("H") else 'guest'

        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()

        if self.event_id:
            # UPDATE existing event
            c.execute("""UPDATE events 
                         SET time_ms = ?, 
                             quarter = ?, 
                             player_id = ?, 
                             team_side = ?, 
                             type = ?, 
                             location = ?, 
                             shot_quality = ?, 
                             is_highlight = ? 
                         WHERE id = ?""",
                      (time_ms, quarter, player_id, team_side, type_,
                       location, shot_quality,
                       1 if self.highlight_checkbox.isChecked() else 0,
                       self.event_id))
        else:
            # INSERT new event
            c.execute("""INSERT INTO events 
                         (video_id, time_ms, quarter, player_id, team_side, type, 
                          location, shot_quality, is_highlight) 
                         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                      (self.video_id, time_ms, quarter, player_id, team_side, type_,
                       location, shot_quality,
                       1 if self.highlight_checkbox.isChecked() else 0))

        conn.commit()
        conn.close()
        self.accept()

class   VideoPlayerEventDialog(QDialog):
    def __init__(self, parent, file_path, game_id, video_id):
        super().__init__(parent)
        self.game_id = game_id
        self.video_id = video_id

        # Fetch game name + home vs guest teams for a meaningful title
        try:
            conn = sqlite3.connect(DB_NAME)
            c = conn.cursor()

            c.execute("""
                        SELECT g.name, 
                               t_home.name AS home_team, 
                               t_guest.name AS guest_team,
                               g.format as game_format
                        FROM games g
                        LEFT JOIN teams t_home ON g.home_team_id = t_home.id
                        LEFT JOIN teams t_guest ON g.guest_team_id = t_guest.id
                        WHERE g.id = ?
                    """, (game_id,))

            row = c.fetchone()
            if row:
                game_name = row[0] or "Unknown Game"
                home_team = row[1] or "Home"
                guest_team = row[2] or "Guest"
                self.game_format = (row[3] or "Q").upper()
                #title = f"{home_team} vs {guest_team} – {game_name}"
                title = f"{home_team} (Home) vs {guest_team} (Guest) – {game_name}"
            else:
                title = "Video Player & Event Tagger"
                self.game_format = "Q"

            conn.close()

        except Exception as e:
            print("VideoPlayerEventDialog title fetch error:", str(e))
            title = "Video Player & Event Tagger"

        # Set the final meaningful title
        self.setWindowTitle(title)

        #self.setWindowTitle("Video Player & Event Tagger")
        self.setGeometry(30, 40, 1200, 940)

        layout = QVBoxLayout(self)

        # Video display area
        self.video_frame = QFrame()
        self.video_frame.setMinimumSize(640, 360)
        self.video_frame.setFrameShape(QFrame.Box)
        palette = self.video_frame.palette()
        palette.setColor(QPalette.Window, QColor(0, 0, 0))
        self.video_frame.setPalette(palette)
        self.video_frame.setAutoFillBackground(True)
        layout.addWidget(self.video_frame, stretch=1)

        # VLC setup
        vlc_args = [
            "--no-xlib", "--avcodec-hw=none", "--file-caching=2500",
            "--network-caching=2500", "--avcodec-threads=2",
            "--drop-late-frames", "--quiet"
        ]
        self.instance = vlc.Instance(*vlc_args)
        self.player = self.instance.media_player_new()

        if platform.system() == "Windows":
            self.player.set_hwnd(int(self.video_frame.winId()))
        elif platform.system() == "Linux":
            self.player.set_xwindow(self.video_frame.winId())
        elif platform.system() == "Darwin":
            self.player.set_nsobject(int(self.video_frame.winId()))

        # Playback controls
        controls = QHBoxLayout()
        controls.setSpacing(10)

        self.close_btn = QPushButton("Close")
        self.close_btn.setFixedWidth(100)
        self.close_btn.clicked.connect(self.close)
        controls.addWidget(self.close_btn)

        controls.addStretch()

        skip_label = QLabel("Skip:")
        skip_label.setStyleSheet("font-weight: bold; padding-right: 1px;")
        controls.addWidget(skip_label)

        for sec, label in [(-30, "<< 30s"), (-15, "<< 15s"), (-5, "<< 5s"),
                           (5, "5s >>"), (15, "15s >>"), (30, "30s >>")]:
            btn = QPushButton(label)
            btn.setFixedWidth(88)
            btn.clicked.connect(lambda checked, s=sec: self.skip_seconds(s))
            controls.addWidget(btn)

        controls.addStretch()

        # Right side: Speed + Play
        right_group = QHBoxLayout()
        right_group.setSpacing(8)

        speed_label = QLabel("Speed:")
        speed_label.setStyleSheet("font-weight: bold; padding-right: 1px;")
        right_group.addWidget(speed_label)

        self.speed_combo = QComboBox()
        self.speed_combo.addItems(["0.5×", "1.0×", "1.25×", "1.5×"])
        self.speed_combo.setCurrentText("1.0×")
        self.speed_combo.setFixedWidth(92)
        self.speed_combo.setStyleSheet("""
            QComboBox {
                border: 1px solid #999999;
                border-radius: 4px;
                padding: 5px 8px;
                background-color: white;
            }
            QComboBox:hover {
                border: 1px solid #666666;
                background-color: #f8f8f8;
            }
            QComboBox::drop-down {
                border: none;
                width: 20px;
            }
            QComboBox QAbstractItemView {
                background-color: white;
                selection-background-color: #d0e0ff;
                selection-color: black;
            }
        """)
        self.speed_combo.currentTextChanged.connect(self.change_playback_speed)
        right_group.addWidget(self.speed_combo)

        self.play_btn = QPushButton("Play ▶️ ")
        self.play_btn.setFixedWidth(120)
        self.play_btn.setStyleSheet("""
            QPushButton {
                background-color: #d0e0ff;
                border: 1px solid #000000;
                border-radius: 6px;
                padding: 6px 16px;
            }
        """)
        self.play_btn.clicked.connect(self.toggle_playback)
        right_group.addWidget(self.play_btn)

        controls.addLayout(right_group)
        layout.addLayout(controls)

        # Time slider
        time_layout = QHBoxLayout()
        self.time_label = QLabel("00:00 / 00:00")
        self.time_label.setFixedWidth(140)
        time_layout.addWidget(self.time_label)

        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 0)
        self.slider.sliderMoved.connect(self.seek_to)
        time_layout.addWidget(self.slider)
        layout.addLayout(time_layout)

        # ── Event list + Period Starts + navigation + action buttons ───────────────────────
        events_layout = QHBoxLayout()
        events_layout.setSpacing(12)

        # Event list (left side) - narrowed
        self.events_list = QListWidget()
        self.events_list.setMaximumHeight(160)
        self.events_list.itemClicked.connect(self.jump_to_event)
        self.events_list.setMinimumWidth(300)
        events_layout.addWidget(self.events_list, stretch=4)

        # Force highlight whenever selection changes (covers Search, Scores, Fouls, clicks)
        self.events_list.itemSelectionChanged.connect(self.highlight_selected_event)

        # Navigation panel (Search Events) - moved to middle-left
        nav_panel = QWidget()
        nav_panel.setMaximumWidth(260)  # ← This is the key line
        nav_panel.setMinimumWidth(240)  # prevents it from getting too narro

        nav_layout = QVBoxLayout(nav_panel)
        nav_layout.setContentsMargins(8, 0, 8, 0)
        nav_layout.setSpacing(6)

        search_label = QLabel("Search Event")
        search_label.setStyleSheet("font-size: 12px; font-weight: bold; padding: 3px;")
        search_label.setAlignment(Qt.AlignCenter)
        nav_layout.addWidget(search_label)

        # Type navigation
        type_row = QHBoxLayout()
        type_row.addWidget(QLabel("Type:"))
        self.nav_type_combo = QComboBox()
        self.nav_type_combo.addItem("— any —")
        self.nav_type_combo.setMinimumWidth(180)
        type_row.addWidget(self.nav_type_combo)
        nav_layout.addLayout(type_row)

        type_btn_row = QHBoxLayout()
        type_btn_row.addStretch()
        self.btn_prev_type = QPushButton("◀ Prev")
        self.btn_next_type = QPushButton("Next ▶")
        self.btn_prev_type.setMinimumWidth(80)
        self.btn_next_type.setMinimumWidth(80)
        type_btn_row.addWidget(self.btn_prev_type)
        type_btn_row.addWidget(self.btn_next_type)
        nav_layout.addLayout(type_btn_row)

        nav_layout.addSpacing(12)

        # Player navigation
        player_row = QHBoxLayout()
        player_row.addWidget(QLabel("Player:"))
        self.nav_player_combo = QComboBox()
        self.nav_player_combo.addItem("— any —")
        self.nav_player_combo.setMinimumWidth(180)
        player_row.addWidget(self.nav_player_combo)
        nav_layout.addLayout(player_row)

        player_btn_row = QHBoxLayout()
        player_btn_row.addStretch()
        self.btn_prev_player = QPushButton("◀ Prev")
        self.btn_next_player = QPushButton("Next ▶")
        self.btn_prev_player.setMinimumWidth(80)
        self.btn_next_player.setMinimumWidth(80)
        player_btn_row.addWidget(self.btn_prev_player)
        player_btn_row.addWidget(self.btn_next_player)
        nav_layout.addLayout(player_btn_row)

        nav_layout.addStretch()

        # Navigation connections
        self.btn_next_type.clicked.connect(lambda: self.jump_next("type", True))
        self.btn_prev_type.clicked.connect(lambda: self.jump_next("type", False))
        self.btn_next_player.clicked.connect(lambda: self.jump_next("player", True))
        self.btn_prev_player.clicked.connect(lambda: self.jump_next("player", False))

        events_layout.addWidget(nav_panel, stretch=3)

        # Spacer between Search and Quarter Starts
        events_layout.addSpacing(24)  # ← This is the new spacer you wanted

        # === Quarter Starts Panel - moved to right of Search ===
        self.period_panel = QWidget()
        self.period_panel.setMaximumWidth(220)  # Keeps Quarter Starts compact
        self.period_panel.setMinimumWidth(180)

        period_layout = QVBoxLayout(self.period_panel)
        period_layout.setContentsMargins(8, 6, 8, 6)
        period_layout.setSpacing(6)

        # Dynamic title based on game format
        if self.game_format == "Q":
            title_format_text = "Quarter"
        elif self.game_format == "H":
            title_format_text = "Half"
        else:
            title_format_text = "Period"

        self.period_title = QLabel(title_format_text + " Markers")
        self.period_title.setStyleSheet("font-weight: bold; font-size: 12px;")
        self.period_title.setAlignment(Qt.AlignCenter)
        period_layout.addWidget(self.period_title)

        self.period_combo = QComboBox()
        self.period_combo.setMinimumWidth(140)
        period_layout.addWidget(self.period_combo)
        self.period_combo.currentTextChanged.connect(self.update_set_button_text)

        # Dynamic "Set Marker" button that updates based on selected item
        self.set_period_btn = QPushButton("Set Marker for Selected -")
        self.set_period_btn.clicked.connect(self.set_current_period_start)
        period_layout.addWidget(self.set_period_btn)

        self.clear_periods_btn = QPushButton("Clear All Markers")
        self.clear_periods_btn.clicked.connect(self.clear_quarter_marks)
        period_layout.addWidget(self.clear_periods_btn)

        period_layout.addStretch()
        events_layout.addWidget(self.period_panel, stretch=2)

        # Action buttons (right side)
        event_btn_l = QVBoxLayout()
        event_btn_l.setSpacing(6)

        add_event = QPushButton("Tag Event")
        add_event.setFixedWidth(120)
        add_event.setStyleSheet("""
                    QPushButton {
                        background-color: #DCFFDC;
                        border: 1px solid #000000;
                        border-radius: 6px;
                        padding: 6px 16px;
                    }
                    QPushButton:hover {
                        background-color: #BDFCBD;
                    }
                """)
        add_event.clicked.connect(self.add_event)
        event_btn_l.addWidget(add_event)

        edit_event = QPushButton("Edit Event")
        edit_event.setFixedWidth(120)
        edit_event.clicked.connect(self.edit_event)
        event_btn_l.addWidget(edit_event)

        delete_event = QPushButton("Delete Event")
        delete_event.setFixedWidth(120)
        delete_event.clicked.connect(self.delete_event)
        event_btn_l.addWidget(delete_event)

        view_scores_btn = QPushButton("View Scores")
        view_scores_btn.setFixedWidth(120)
        view_scores_btn.clicked.connect(self.show_scores_popup)
        event_btn_l.addWidget(view_scores_btn)

        view_fouls_btn = QPushButton("View Fouls")
        view_fouls_btn.setFixedWidth(120)
        view_fouls_btn.clicked.connect(self.show_fouls_popup)
        event_btn_l.addWidget(view_fouls_btn)

        event_btn_l.addStretch()
        events_layout.addLayout(event_btn_l, stretch=0)

        layout.addLayout(events_layout)

        # Video loading & timers
        self.total_seconds = 0
        self.load_video(file_path)

        self.update_timer = QTimer(self)
        self.update_timer.timeout.connect(self.update_ui)
        self.update_timer.start(33)

        self.setFocusPolicy(Qt.StrongFocus)

    def update_set_button_text(self):
        """Update the Set button to show the currently selected period"""
        if not hasattr(self, 'period_combo') or not hasattr(self, 'set_period_btn'):
            return

        current_text = self.period_combo.currentText()
        if current_text:
            # Extract clean period code (e.g. "Q2" from "Q2: 12:45" or just "Q2")
            period_code = current_text.split(":")[0].strip()
            self.set_period_btn.setText(f"Set Marker for {period_code}")
        else:
            self.set_period_btn.setText("Set Marker for Selected —")

    def set_current_period_start(self):
        period = self.period_combo.currentData()  # Get the raw period (Q1, Q2, etc.)
        if not period:
            QMessageBox.warning(self, "No Period", "Please select a period first.")
            return

        current_ms = max(0, self.player.get_time())

        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()

        c.execute("SELECT quarter_marks FROM videos WHERE id = ?", (self.video_id,))
        row = c.fetchone()
        marks = row[0] if row and row[0] else ""

        mark_list = [m for m in marks.split(",") if m and not m.startswith(period + ":")]
        mark_list.append(f"{period}:{current_ms}")
        new_marks = ",".join(mark_list)

        c.execute("UPDATE videos SET quarter_marks = ? WHERE id = ?", (new_marks, self.video_id))
        conn.commit()
        conn.close()

        # Refresh combo to show updated timestamp
        self.load_period_options()
        self.update_set_button_text()  # ← Add this line

        QMessageBox.information(self, "Saved", f"{period} start set to {self.format_ms(current_ms)}")

    def clear_quarter_marks(self):
        reply = QMessageBox.question(self, "Clear",
                                    "Clear all period start times for this video?",
                                    QMessageBox.Yes | QMessageBox.No)
        if reply == QMessageBox.Yes:
            conn = sqlite3.connect(DB_NAME)
            c = conn.cursor()
            c.execute("UPDATE videos SET quarter_marks = NULL WHERE id = ?", (self.video_id,))
            conn.commit()
            conn.close()
            QMessageBox.information(self, "Cleared", "All period start times cleared for this video.")

    def get_quarter_from_time(self, time_ms):
        """Auto-detect current period based on saved quarter_marks for this video"""
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT quarter_marks FROM videos WHERE id = ?", (self.video_id,))
        row = c.fetchone()
        conn.close()

        marks_str = row[0] if row and row[0] else ""

        if not marks_str:
            # No marks set for this video yet → fallback to game default
            fallback = "Q1" if self.game_format == "Q" else "H1"
            print(f"DEBUG: No quarter_marks set. Using fallback: {fallback}")
            return fallback

        mark_list = []
        for item in marks_str.split(","):
            if ":" in item:
                p, ts = item.split(":", 1)
                try:
                    mark_list.append((p.strip(), int(ts)))
                except ValueError:
                    continue

        if not mark_list:
            return "Q1" if self.game_format == "Q" else "H1"

        # Sort by start time and find the latest period that has started
        mark_list.sort(key=lambda x: x[1])
        current = mark_list[0][0]

        for period, start_ms in mark_list:
            if time_ms >= start_ms:
                current = period
            else:
                break

        #print(f"DEBUG: Current time {self.format_ms(time_ms)} → detected period: {current}")
        return current

    def format_ms(self, ms):
        s = ms // 1000
        m, sec = divmod(s, 60)
        return f"{m:02d}:{sec:02d}"

    # ====================== UPDATED add_event ======================
    def add_event(self):
        if self.player.is_playing():
            self.player.pause()
            self.play_btn.setText("Play ▶ ")

        pos_ms = max(0, self.player.get_time())
        auto_quarter = self.get_quarter_from_time(pos_ms)

        dialog = EventEditorDialog(self, self.game_id, pos_ms / 1000, self.video_id)

        if hasattr(dialog, 'quarter_combo'):
            idx = dialog.quarter_combo.findText(auto_quarter)
            if idx >= 0:
                dialog.quarter_combo.setCurrentIndex(idx)

        if dialog.exec_() == QDialog.Accepted:
            self.load_events()
            self.refresh_nav_combos()

    # ====================== PERIOD STARTS METHODS ======================

    def load_period_options(self):
        """Load periods with timestamps and update dynamic button text"""
        if not hasattr(self, 'period_combo'):
            return

        self.period_combo.clear()

        # Determine periods and title based on game format
        if self.game_format == "Q":
            periods = ["Q1", "Q2", "Q3", "Q4", "OT1", "OT2"]
            self.period_title.setText("Quarter Markers")
        elif self.game_format == "H":
            periods = ["H1", "H2", "OT1", "OT2"]
            self.period_title.setText("Half Markers")
        else:
            periods = ["Q1", "Q2", "Q3", "Q4", "OT1", "OT2"]
            self.period_title.setText("Period Markers")

        # Get current saved markers
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT quarter_marks FROM videos WHERE id = ?", (self.video_id,))
        row = c.fetchone()
        conn.close()

        marks_dict = {}
        if row and row[0]:
            for item in row[0].split(","):
                if ":" in item:
                    p, ts = item.split(":", 1)
                    try:
                        marks_dict[p.strip()] = int(ts)
                    except:
                        pass

        # Add items to combo box
        for period in periods:
            if period in marks_dict:
                timestamp = self.format_ms(marks_dict[period])
                display_text = f"{period}: {timestamp}"
            else:
                display_text = f"{period}: not set"

            self.period_combo.addItem(display_text, period)  # Store raw period as userData

        # Update the Set Marker button text based on current selection
        self.update_set_button_text()

        # Select first item by default
        if self.period_combo.count() > 0:
            self.period_combo.setCurrentIndex(0)

    def set_current_period_start(self):
        """Save current time and refresh display"""
        # Get the raw period from userData (this was the bug)
        period = self.period_combo.currentData()

        if not period:
            QMessageBox.warning(self, "No Period", "Please select a period first.")
            return

        current_ms = max(0, self.player.get_time())

        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()

        c.execute("SELECT quarter_marks FROM videos WHERE id = ?", (self.video_id,))
        row = c.fetchone()
        marks = row[0] if row and row[0] else ""

        # Remove old entry for this period and add new one
        mark_list = [m for m in marks.split(",") if m and not m.startswith(period + ":")]
        mark_list.append(f"{period}:{current_ms}")
        new_marks = ",".join(mark_list)

        c.execute("UPDATE videos SET quarter_marks = ? WHERE id = ?", (new_marks, self.video_id))
        conn.commit()
        conn.close()

        # Refresh the list to show updated timestamp
        self.load_period_options()

        QMessageBox.information(self, "Saved", f"{period} markers set to {self.format_ms(current_ms)}")

    def clear_quarter_marks(self):
        reply = QMessageBox.question(self, "Clear All",
                                     "Clear all period start times for this video?\n\n"
                                     "This cannot be undone.",
                                     QMessageBox.Yes | QMessageBox.No)
        if reply == QMessageBox.Yes:
            conn = sqlite3.connect(DB_NAME)
            c = conn.cursor()
            c.execute("UPDATE videos SET quarter_marks = NULL WHERE id = ?", (self.video_id,))
            conn.commit()
            conn.close()

            # Refresh the combo box to show "not set" for all periods
            self.load_period_options()

            QMessageBox.information(self, "Cleared", "All period start times have been cleared for this video.")

    def toggle_playback(self):
        if self.player.is_playing():
            self.player.pause()
            self.play_btn.setText("Play ▶ ")
        else:
            self.player.play()
            self.play_btn.setText("Pause ⏸")

    def change_playback_speed(self):
        try:
            speed_text = self.speed_combo.currentText()
            speed = float(speed_text.replace('×', '').strip())
            self.player.set_rate(speed)
        except:
            self.player.set_rate(1.0)

    def skip_seconds(self, seconds):
        current_ms = max(0, self.player.get_time())
        new_ms = max(0, min(current_ms + (seconds * 1000), self.player.get_length()))
        self.player.set_time(new_ms)
        self.update_ui()

    def seek_to(self, seconds):
        if seconds is not None:
            self.player.set_time(int(seconds * 1000))
            self.update_ui()

    def update_ui(self):
        current_ms = max(0, self.player.get_time())
        current_sec = current_ms // 1000
        self.slider.setValue(current_sec)
        self.update_time_label()
        if self.player.is_playing():
            self.play_btn.setText("Pause ⏸ ")
        else:
            self.play_btn.setText("Play ▶ ")

    def update_time_label(self):
        current = max(0, self.player.get_time() // 1000)
        total = self.total_seconds
        cur_m, cur_s = divmod(current, 60)
        tot_m, tot_s = divmod(total, 60)
        self.time_label.setText(f"{cur_m:02d}:{cur_s:02d} / {tot_m:02d}:{tot_s:02d}")

    # ====================== EXISTING METHODS (unchanged) ======================
    # ... [Keep all your existing methods: show_scores_popup, show_fouls_popup,
    # refresh_nav_combos, jump_next, load_events, jump_to_event, edit_event,
    # delete_event, get_full_event_type, get_event_code, etc.] ...

    # (Paste all your remaining methods from the original class here)

    def show_scores_popup(self):
        popup = QDialog(self)
        popup.setWindowTitle("Scoring Events (Made Shots Only)")
        popup.setGeometry(220, 160, 1150, 650)
        popup.setModal(True)

        layout = QVBoxLayout(popup)
        layout.setContentsMargins(12, 12, 12, 12)

        # ── Filter row ───────────────────────────────────────────────────────
        filter_layout = QHBoxLayout()
        filter_layout.setSpacing(16)

        filter_layout.addWidget(QLabel("Player:"))
        self.filter_player_combo = QComboBox()
        self.filter_player_combo.addItem("All Players")
        filter_layout.addWidget(self.filter_player_combo)

        filter_layout.addWidget(QLabel("Type:"))
        self.filter_type_combo = QComboBox()
        self.filter_type_combo.addItem("All Types")
        filter_layout.addWidget(self.filter_type_combo)

        filter_layout.addWidget(QLabel("Location:"))
        self.filter_location_combo = QComboBox()
        self.filter_location_combo.addItem("All Locations")
        filter_layout.addWidget(self.filter_location_combo)

        reset_btn = QPushButton("Clear Filters")
        reset_btn.setFixedWidth(120)
        reset_btn.clicked.connect(self._reset_filters_and_refresh)
        filter_layout.addWidget(reset_btn)
        filter_layout.addStretch()

        layout.addLayout(filter_layout)

        # ── Table ────────────────────────────────────────────────────────────
        table = QTableWidget(0, 8)
        table.setHorizontalHeaderLabels([
            "Time", "Quarter", "Player", "Type", "Location",
            "Home Score", "Guest Score", "Event ID"
        ])
        table.setColumnHidden(7, True)  # Event ID

        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.SingleSelection)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setAlternatingRowColors(True)

        # Column sizing
        table.horizontalHeader().setStretchLastSection(False)  # We'll control it manually

        table.setColumnWidth(0, 90)  # Time
        table.setColumnWidth(1, 80)  # Quarter
        table.setColumnWidth(2, 220)  # Player
        table.setColumnWidth(3, 110)  # Type
        table.setColumnWidth(4, 150)  # Location - starting width (will stretch)
        table.setColumnWidth(5, 95)  # Home Score - narrow fixed
        table.setColumnWidth(6, 95)  # Guest Score - narrow fixed

        # Make Location column stretch to fill remaining space
        table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)

        # Keep other columns at fixed/resizable as needed
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Fixed)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Fixed)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Fixed)
        table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Fixed)
        table.horizontalHeader().setSectionResizeMode(5, QHeaderView.Fixed)
        table.horizontalHeader().setSectionResizeMode(6, QHeaderView.Fixed)

        layout.addWidget(table, stretch=1)

        self.table = table

        # ── Load all scoring events (unchanged) ─────────────────────────────────────
        scoring_full_names = {
            "2PM 2PT Shot Made": "2PTM",
            "3PM 3PT Shot Made": "3PTM",
            "FTM Freethrow Made": "FTM"
        }
        scoring_types = list(scoring_full_names.keys())

        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        query = """
                SELECT e.id, e.time_ms, e.quarter, e.type,
                       p.number, p.name, gr.side, e.team_side, e.location
                FROM events e
                         LEFT JOIN players p ON e.player_id = p.id
                         LEFT JOIN game_rosters gr ON e.player_id = gr.player_id
                            AND gr.game_id = ?
                WHERE e.video_id = ? 
                  AND e.type IN (?, ?, ?)
                ORDER BY e.time_ms
                """

        c.execute(query, (self.game_id, self.video_id, *scoring_types))
        self.all_scoring_rows = c.fetchall()
        conn.close()

        if not self.all_scoring_rows:
            no_events_label = QLabel("No made shots (2PM, 3PM, FTM) recorded for this video yet.")
            no_events_label.setAlignment(Qt.AlignCenter)
            no_events_label.setStyleSheet("font-size: 16px; color: #666; padding: 60px;")
            layout.addWidget(no_events_label, stretch=1)
        else:
            # Populate filters (unchanged - keeping your original sorting logic)
            players = set()
            types = set()
            locations = set()

            for row in self.all_scoring_rows:
                _, _, _, event_type, num, name, side, team_side, location = row

                if num is not None:
                    prefix = "H" if side == 'home' else "G"
                    p_str = f"{prefix}{str(num)}"
                    if name:
                        p_str += f" - {name}"
                    players.add(p_str)
                else:
                    prefix = "H" if team_side == 'home' else "G"
                    p_str = f"{prefix} -"
                    if name:
                        p_str += f" {name}"
                    players.add(p_str)

                short_type = scoring_full_names.get(event_type, event_type[:4])
                types.add(short_type)

                loc = location if location and location != "-" else "-"
                locations.add(loc)

            def player_sort_key(display_str):
                if not display_str or ' - ' not in display_str:
                    return (1, 999999, display_str)
                prefix_num_part = display_str.split(' - ', 1)[0].strip()
                if len(prefix_num_part) < 2:
                    return (1, 999999, display_str)
                side_char = prefix_num_part[0].upper()
                num_str = prefix_num_part[1:]
                side_value = 0 if side_char == 'H' else 1 if side_char == 'G' else 2
                try:
                    num_value = int(num_str)
                except ValueError:
                    num_value = 999999
                return (side_value, num_value, display_str)

            sorted_players = sorted(players, key=player_sort_key)
            self.filter_player_combo.addItems(sorted_players)
            self.filter_type_combo.addItems(sorted(types))
            self.filter_location_combo.addItems(sorted(locations))

            self.filter_player_combo.currentIndexChanged.connect(self._refresh_scoring_table)
            self.filter_type_combo.currentIndexChanged.connect(self._refresh_scoring_table)
            self.filter_location_combo.currentIndexChanged.connect(self._refresh_scoring_table)

            self._refresh_scoring_table()

            # Double-click handler (Event ID is column 7)
            def on_row_double_clicked(row, column):
                id_item = self.table.item(row, 7)
                if not id_item:
                    return
                try:
                    target_id = int(id_item.text())
                except ValueError:
                    return

                for i in range(self.events_list.count()):
                    item = self.events_list.item(i)
                    if item.data(Qt.UserRole + 1) == target_id:
                        self.events_list.setCurrentRow(i)
                        self.events_list.scrollToItem(item)
                        self.jump_to_event(item)
                        break
                self.highlight_selected_event()  # ← Add this line
                popup.accept()

            self.table.cellDoubleClicked.connect(on_row_double_clicked)

        # Close button
        close_btn = QPushButton("Close")
        close_btn.setFixedWidth(140)
        close_btn.clicked.connect(popup.accept)
        layout.addWidget(close_btn, alignment=Qt.AlignRight)

        popup.exec_()

    def show_fouls_popup(self):
        """Personal Fouls Report - Running team fouls per quarter + dash for other quarters"""
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()

        c.execute("""
            SELECT 
                e.id,
                e.time_ms,
                e.quarter,
                p.number,
                p.name,
                COALESCE(gr.side, e.team_side) AS effective_side
            FROM events e
            LEFT JOIN players p ON e.player_id = p.id
            LEFT JOIN game_rosters gr 
                   ON e.player_id = gr.player_id 
                  AND gr.game_id = ?
            WHERE e.video_id = ?
              AND e.type = 'PFL Personal Foul'
            ORDER BY e.time_ms
        """, (self.game_id, self.video_id))

        fouls = c.fetchall()
        conn.close()

        if not fouls:
            QMessageBox.information(self, "No Fouls", "No personal fouls found in this video.")
            return

        dialog = QDialog(self)
        dialog.setWindowTitle("Personal Fouls Report")
        dialog.setGeometry(250, 150, 920, 620)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(12, 12, 12, 12)

        # Table setup
        table = QTableWidget()
        table.setColumnCount(6)
        table.setHorizontalHeaderLabels(["Time", "Player", "Q1", "Q2", "Q3", "Q4"])

        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.SingleSelection)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setAlternatingRowColors(True)
        table.setSortingEnabled(True)

        # Column widths
        table.setColumnWidth(0, 95)  # Time
        table.setColumnWidth(1, 265)  # Player
        for i in range(2, 6):
            table.setColumnWidth(i, 92)

        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        for i in range(2, 6):
            table.horizontalHeader().setSectionResizeMode(i, QHeaderView.Fixed)

        # Running counters
        quarter_fouls = {q: {'home': 0, 'guest': 0} for q in ['Q1', 'Q2', 'Q3', 'Q4']}
        player_foul_count = {}

        # Build table
        for event_id, time_ms, quarter, num, name, side in fouls:
            # Time formatting
            m = time_ms // 60000
            s = (time_ms % 60000) // 1000
            time_str = f"{m:02d}:{s:02d}"

            # Player with running personal foul count
            prefix = "H" if side == 'home' else "G"
            player_key = f"{prefix}{num or '??'}"
            full_key = f"{player_key}-{name or 'Unknown'}"

            player_foul_count[full_key] = player_foul_count.get(full_key, 0) + 1
            current_count = player_foul_count[full_key]

            player_str = f"{player_key} - {name or 'Unknown'} ({current_count})"

            # Update running count for this quarter
            q_key = str(quarter) if quarter else "Q1"
            if q_key not in quarter_fouls:
                quarter_fouls[q_key] = {'home': 0, 'guest': 0}

            team = 'home' if side == 'home' else 'guest'
            quarter_fouls[q_key][team] += 1

            # Add row
            row_idx = table.rowCount()
            table.insertRow(row_idx)

            table.setItem(row_idx, 0, QTableWidgetItem(time_str))
            table.setItem(row_idx, 1, QTableWidgetItem(player_str))

            # Store event ID for jumping
            table.item(row_idx, 0).setData(Qt.UserRole, event_id)

            # Fill quarter columns
            for col in range(2, 6):
                q_header = table.horizontalHeaderItem(col).text()

                if q_header == q_key:
                    # This is the quarter where the foul happened → show running total
                    h_count = quarter_fouls[q_key]['home']
                    g_count = quarter_fouls[q_key]['guest']
                    cell_text = f"H{h_count} - G{g_count}"
                else:
                    # Other quarters → just dash
                    cell_text = "-"

                table.setItem(row_idx, col, QTableWidgetItem(cell_text))

        # Center align the quarter columns
        for row in range(table.rowCount()):
            for col in range(2, 6):
                item = table.item(row, col)
                if item:
                    item.setTextAlignment(Qt.AlignCenter)

        layout.addWidget(table, stretch=1)

        # Double-click to jump to event
        def on_double_clicked(index):
            if index.isValid():
                item = table.item(index.row(), 0)
                if item and item.data(Qt.UserRole):
                    event_id = item.data(Qt.UserRole)
                    for i in range(self.events_list.count()):
                        list_item = self.events_list.item(i)
                        if list_item.data(Qt.UserRole + 1) == event_id:
                            self.events_list.setCurrentRow(i)
                            self.events_list.scrollToItem(list_item)
                            self.jump_to_event(list_item)
                            break
                    self.highlight_selected_event()  # ← Add this line
                    dialog.accept()

        table.doubleClicked.connect(on_double_clicked)

        # Close button
        close_btn = QPushButton("Close")
        close_btn.setFixedWidth(120)
        close_btn.clicked.connect(dialog.accept)
        layout.addWidget(close_btn, alignment=Qt.AlignRight)

        dialog.exec_()

    def jump_to_event_from_fouls(self, table, index):
        """Called from double-click in fouls popup"""
        event_id = table.item(index.row(), 0).data(Qt.UserRole)
        if not event_id:
            return

        # Close the fouls popup
        table.window().close()  # closes the QDialog containing the table

        # Find and select the matching row in main events_list
        for i in range(self.events_list.count()):
            item = self.events_list.item(i)
            if item.data(Qt.UserRole + 1) == event_id:  # event ID stored here
                self.events_list.setCurrentItem(item)
                self.events_list.scrollToItem(item, QAbstractItemView.PositionAtCenter)
                # Optional: highlight/flash
                item.setSelected(True)
                break

    def highlight_selected_event(self):
        #Force solid blue highlight using stylesheet (more reliable for QListWidget)
        #print(f"highlight_selected_event was called - current item: {self.events_list.currentRow()}")

        # Reset all items to original quarter colors
        for i in range(self.events_list.count()):
            item = self.events_list.item(i)
            if item:
                text = item.text().strip()
                quarter = None
                if " - " in text:
                    parts = [p.strip() for p in text.split(" - ")]
                    if len(parts) >= 2:
                        quarter = parts[1]

                bg_color = EventEditorDialog.QUARTER_COLORS.get(quarter, "#ffffff")
                item.setBackground(QColor(bg_color))

                if quarter in ["Q2", "Q4", "H2"]:
                    item.setForeground(QColor("#333333"))
                else:
                    item.setForeground(QColor("#000000"))

        # Apply strong blue highlight using stylesheet (this is more reliable)
        current = self.events_list.currentItem()
        if current:
            # Use stylesheet for the selected item
            current.setSelected(True)

            # Force blue background with stylesheet
            self.events_list.setStyleSheet("""
                QListWidget::item:selected {
                    background-color: #1565c0;
                    color: white;
                    font-weight: bold;
                }
            """)

            #print(f"Applied blue highlight to row {self.events_list.row(current)} using stylesheet")

    def _reset_filters_and_refresh(self):
        """Reset all filters to 'All' and refresh table"""
        self.filter_player_combo.setCurrentIndex(0)
        self.filter_type_combo.setCurrentIndex(0)
        self.filter_location_combo.setCurrentIndex(0)
        self._refresh_scoring_table()

    def _refresh_scoring_table(self):
        if not hasattr(self, 'all_scoring_rows') or not self.all_scoring_rows:
            return

        player_filter = self.filter_player_combo.currentText()
        type_filter = self.filter_type_combo.currentText()
        location_filter = self.filter_location_combo.currentText()

        self.table.setRowCount(0)

        home_score = 0
        guest_score = 0

        for row in self.all_scoring_rows:
            event_id, time_ms, quarter, event_type, num, name, side, team_side, location = row

            # Rebuild player display string (must match exactly what’s in the filter combo)
            if num is not None:
                prefix = "H" if side == 'home' else "G"
                player_str = f"{prefix}{str(num)}"
                if name:
                    player_str += f" - {name}"
            else:
                prefix = "H" if team_side == 'home' else "G"
                player_str = f"{prefix} -"
                if name:
                    player_str += f" {name}"

            # Short type for display + filtering
            short_type = {
                "2PM 2PT Shot Made": "2PTM",
                "3PM 3PT Shot Made": "3PTM",
                "FTM Freethrow Made": "FTM"
            }.get(event_type, event_type[:4])

            loc = location if location and location != "-" else "-"

            # === Apply filters ===
            if player_filter != "All Players" and player_str != player_filter:
                continue
            if type_filter != "All Types" and short_type != type_filter:
                continue
            if location_filter != "All Locations" and loc != location_filter:
                continue

            # === Calculate points and update cumulative score ===
            if "3PM" in event_type:
                points = 3
            elif "2PM" in event_type:
                points = 2
            else:  # FTM
                points = 1

            if (side == 'home') or (team_side == 'home' and side is None):
                home_score += points
            else:
                guest_score += points

            # === Add the row ===
            current_row = self.table.rowCount()
            self.table.insertRow(current_row)

            # Convert milliseconds to MM:SS format
            minutes = time_ms // 60000
            seconds = (time_ms % 60000) // 1000
            time_str = f"{minutes:02d}:{seconds:02d}"
            self.table.setItem(current_row, 0, QTableWidgetItem(time_str))  # Time
            self.table.setItem(current_row, 1, QTableWidgetItem(str(quarter)))  # Quarter
            self.table.setItem(current_row, 2, QTableWidgetItem(player_str))  # Player
            self.table.setItem(current_row, 3, QTableWidgetItem(short_type))  # Type
            self.table.setItem(current_row, 4, QTableWidgetItem(loc))  # Location
            self.table.setItem(current_row, 5, QTableWidgetItem(str(home_score)))  # Home Score  ← NEW
            self.table.setItem(current_row, 6, QTableWidgetItem(str(guest_score)))  # Guest Score ← NEW
            self.table.setItem(current_row, 7, QTableWidgetItem(str(event_id)))  # Event ID

        # Center-align the score columns
        for row in range(self.table.rowCount()):
            for col in (5, 6):
                item = self.table.item(row, col)
                if item:
                    item.setTextAlignment(Qt.AlignCenter)


    def get_full_event_type(self, short_code):
        """Convert short code → full description with separator for easy splitting"""
        mapping = {
            "2PA": "2PA - 2PT Shot Attempt",
            "2PM": "2PM - 2PT Shot Made",
            "3PA": "3PA - 3PT Shot Attempt",
            "3PM": "3PM - 3PT Shot Made",
            "AST": "AST - Assist",
            "BLK": "BLK - Blocked Shot",
            "CHG": "CHG - Charge Drawn",
            "DREB": "DREB - Defensive Rebound",
            "FTA": "FTA - Freethrow Attempt",
            "FTM": "FTM - Freethrow Made",
            "OREB": "OREB - Offensive Rebound",
            "PFL": "PFL - Personal Foul",
            "STL": "STL - Steal",
            "TOV": "TOV - Turnover",
        }
        return mapping.get(short_code, short_code)

    # ───────────────────────────────────────────────────────────────
    # Refresh combos – now uses full descriptions for types
    # ───────────────────────────────────────────────────────────────

    def refresh_nav_combos(self):
        if not hasattr(self, 'nav_type_combo'):
            return

        self.nav_type_combo.clear()
        self.nav_type_combo.addItem("— any —")

        self.nav_player_combo.clear()
        self.nav_player_combo.addItem("— any —")

        types_set = set()
        players_set = set()

        # Player name mapping (your existing logic – kept as-is)
        player_id_to_name = {}
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("""
            SELECT p.id, p.number, p.name, gr.side
            FROM game_rosters gr
            JOIN players p ON gr.player_id = p.id
            WHERE gr.game_id = ?
            ORDER BY gr.side, p.number
        """, (self.game_id,))
        for pid, num, name, side in c.fetchall():
            prefix = "H" if side == 'home' else "G"
            display = f"{prefix}{num} - {name or 'Unknown'}"
            player_id_to_name[pid] = display
        conn.close()

        # Scan event list items
        for i in range(self.events_list.count()):
            item = self.events_list.item(i)
            text = item.text().strip()
            parts = [p.strip() for p in text.split(" - ")]
            if len(parts) < 4:
                continue

            #timestamp, quarter, player_short, event_code = parts[:4]
            #added player name to list
            timestamp, quarter, player_short, player_name, event_code = parts[:5]

            # Player – map to full name if possible
            if player_short and player_short not in ["H-", "G-"]:
                for full_display in player_id_to_name.values():
                    if full_display.startswith(player_short + " -"):
                        players_set.add(full_display)
                        break
                else:
                    players_set.add(player_short)

            # Type – use full description
            full_type = self.get_full_event_type(event_code)
            types_set.add(full_type)

        self.nav_type_combo.addItems(sorted(types_set))

        #self.nav_player_combo.addItems(sorted(players_set))

        def player_sort_key(display_str):
            if not display_str or ' - ' not in display_str:
                return 999999, display_str  # tuple: (number for sort, original for tie-breaker)

            # Get the part before " - " (e.g. "H5", "G10", "H00")
            prefix_num = display_str.split(' - ', 1)[0].strip()

            # Skip H/G prefix and get the number part
            if len(prefix_num) < 2:
                return 999999, display_str

            num_str = prefix_num[1:]  # remove H or G

            try:
                num_int = int(num_str)
                return num_int, display_str  # primary sort: numeric, secondary: original string (preserves 0 vs 00)
            except ValueError:
                return 999999, display_str  # non-numeric → end

        # Then sort using this key
        sorted_players = sorted(players_set, key=player_sort_key)
        self.nav_player_combo.addItems(sorted_players)

        has_events = self.events_list.count() > 0
        for btn in [self.btn_prev_type, self.btn_next_type,
                    self.btn_prev_player, self.btn_next_player]:
            btn.setEnabled(has_events)

    # ───────────────────────────────────────────────────────────────
    # Jump logic – extract short code from full type description
    # ───────────────────────────────────────────────────────────────

    def jump_next(self, mode, forward=True):
        current_row = self.events_list.currentRow()
        total = self.events_list.count()
        if total == 0:
            return
        if current_row < 0:
            current_row = 0 if forward else total - 1

        start = current_row + (1 if forward else -1)
        step = 1 if forward else -1
        end = total if forward else -1

        target = None  # Use None to distinguish "any" from specific

        if mode == "type":
            selected = self.nav_type_combo.currentText().strip()
            if selected == "— any —":
                target = None
            else:
                # Try to extract short code before " - "
                if " - " in selected:
                    target = selected.split(" - ", 1)[0].strip()
                else:
                    # Fallback: assume first word/group is the code (works if no separator)
                    target = selected.split(" ", 1)[0].strip()
        else:  # player
            selected = self.nav_player_combo.currentText().strip()
            if selected == "— any —":
                target = None
            else:
                parts = selected.split(" - ", 1)
                target = parts[0].strip() if parts else selected

        i = start
        while (forward and i < end) or (not forward and i > end):
            item = self.events_list.item(i)
            if item:
                text = item.text().strip()
                parts = [p.strip() for p in text.split(" - ")]
                if len(parts) >= 5:
                    value = parts[4] if mode == "type" else parts[2]

                    if target is None:  # "any" mode - match everything
                        self.events_list.setCurrentRow(i)
                        self.events_list.scrollToItem(item)
                        self.jump_to_event(item)
                        return
                    elif value == target:  # specific match
                        self.events_list.setCurrentRow(i)
                        self.events_list.scrollToItem(item)
                        self.jump_to_event(item)
                        return
            i += step

        # Optional: feedback if no more matches
        # QMessageBox.information(self, "Navigation", "No more matching events found.")

        # After jumping, ensure highlight is applied
        self.highlight_selected_event()

    # ───────────────────────────────────────────────────────────────
    # Remaining methods (unchanged)
    # ───────────────────────────────────────────────────────────────

    def toggle_maximize(self):
        if self.isMaximized():
            self.showNormal()
        else:
            screen = QApplication.primaryScreen().availableGeometry()
            margin = 30
            self.setGeometry(
                screen.x() + margin,
                screen.y() + margin,
                screen.width() - 2 * margin,
                screen.height() - 2 * margin
            )

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Space:
            self.toggle_playback()
        elif event.key() == Qt.Key_Left:
            self.skip_seconds(-5)
        elif event.key() == Qt.Key_Right:
            self.skip_seconds(5)

    def load_video(self, file_path):
        media = self.instance.media_new(file_path)
        self.player.set_media(media)
        self.player.play()
        time.sleep(0.6)
        self.player.pause()

        self.total_seconds = self.player.get_length() // 1000
        self.slider.setRange(0, self.total_seconds)
        self.update_time_label()
        self.play_btn.setText("Play ▶ ")

        self.load_events()
        self.refresh_nav_combos()
        self.load_period_options()           # This must be here

        # Reset speed to normal when loading a new video
        self.speed_combo.setCurrentText("1.0×")
        self.player.set_rate(1.0)

        # Debug print so we can see it's working
        #print(f"DEBUG: load_video finished - Game format = {self.game_format}, Periods in combo = {self.period_combo.count() if hasattr(self, 'period_combo') else 'MISSING'}")

    def toggle_playback(self):
        if self.player.is_playing():
            self.player.pause()
            self.play_btn.setText("Play ▶ ")
        else:
            self.player.play()
            self.play_btn.setText("Pause ⏸")

    def change_playback_speed(self):
        """Change VLC playback speed when user selects from dropdown"""
        speed_text = self.speed_combo.currentText()
        # Remove the '×' symbol and convert to float
        speed = float(speed_text.replace('×', '').strip())
        self.player.set_rate(speed)

    def toggle_speed(self):
        """Toggle between 0.5x (half speed) and 1.0x (normal speed)"""
        if self.speed_btn.isChecked():
            self.player.set_rate(0.5)
            self.speed_btn.setText("1×")
        else:
            self.player.set_rate(1.0)
            self.speed_btn.setText("0.5×")

    def skip_seconds(self, seconds):
        current_ms = max(0, self.player.get_time())
        new_ms = max(0, min(current_ms + (seconds * 1000), self.player.get_length()))
        self.player.set_time(new_ms)
        self.update_ui()

    def seek_to(self, seconds):
        self.player.set_time(seconds * 1000)
        self.update_ui()

    def update_ui(self):
        current_ms = max(0, self.player.get_time())
        current_sec = current_ms // 1000
        self.slider.setValue(current_sec)
        self.update_time_label()
        if self.player.is_playing():
            self.play_btn.setText("Pause ⏸ ")
        else:
            self.play_btn.setText("Play ▶ ")

    def update_time_label(self):
        current = max(0, self.player.get_time() // 1000)
        total = self.total_seconds
        cur_m, cur_s = divmod(current, 60)
        tot_m, tot_s = divmod(total, 60)
        self.time_label.setText(f"{cur_m:02d}:{cur_s:02d} / {tot_m:02d}:{tot_s:02d}")

    def get_event_code(self, event_type):
        event_codes = {
            "2PA 2PT Shot Attempt": "2PA",
            "2PM 2PT Shot Made": "2PM",
            "3PA 3PT Shot Attempt": "3PA",
            "3PM 3PT Shot Made": "3PM",
            "AST Assist": "AST",
            "BLK Blocked Shot": "BLK",
            "CHG Charge Drawn": "CHG",
            "DRB Defensive Rebound": "DREB",
            "FTA Freethrow Attempt": "FTA",
            "FTM Freethrow Made": "FTM",
            "ORB Offensive Rebound": "OREB",
            "PFL Personal Foul": "PFL",
            "STL Steal": "STL",
            "TOV Turnover": "TOV"
        }
        return event_codes.get(event_type, event_type.split()[0] if event_type else "")

    def load_events(self):
        self.events_list.clear()
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()

        query = """
                SELECT e.id, e.time_ms, e.quarter, e.type, e.location, 
                       p.number, gr.side, e.team_side, p.name, e.is_highlight,
                       e.shot_quality                    -- NEW: Added shot_quality
                FROM events e
                         LEFT JOIN players p ON e.player_id = p.id
                         LEFT JOIN game_rosters gr ON e.player_id = gr.player_id
                    AND gr.game_id = (SELECT game_id FROM videos WHERE id = e.video_id)
                WHERE e.video_id = ?
                ORDER BY e.time_ms
                """
        c.execute(query, (self.video_id,))
        rows = c.fetchall()

        for row in rows:
            pos_ms = row[1]
            pos_sec = pos_ms // 1000
            m = pos_sec // 60
            s = pos_sec % 60
            timestamp = f"{m:02d}:{s:02d}"
            quarter = row[2]
            p_name = row[8] or ""
            is_highlight = row[9] or False
            shot_quality = row[10] or ""  # ← NEW: Get shot quality

            # Player display logic (unchanged)
            if row[5] is not None:
                prefix = "H" if row[6] == 'home' else "G"
                display_num = str(row[5])
                player = f"{prefix}{display_num}"
            else:
                prefix = "H" if row[7] == 'home' else "G"
                player = f"{prefix}-"

            if p_name:
                parts = p_name.split()
                if len(parts) >= 2:
                    short = f"{parts[0]} {parts[-1][0]}."
                else:
                    short = p_name[:15] + "…" if len(p_name) > 16 else p_name
                player += f" - {short}"
            else:
                player += f" - {p_name}"

            event_code = self.get_event_code(row[3])
            location = row[4] if row[4] and row[4] != "-" else ""

            # ── Build display string with Shot Quality at the end ─────────────────
            display_parts = [timestamp, quarter, player, event_code]

            if location:
                display_parts.append(location[:4])

            # NEW: Add Shot Quality letter (A/B/C/D) at the very end if it exists
            if shot_quality:
                display_parts.append(shot_quality)

            display = " - ".join(display_parts)

            item = QListWidgetItem(display)
            item.setData(Qt.UserRole, pos_ms)
            item.setData(Qt.UserRole + 1, row[0])  # event id

            # Quarter background color (unchanged)
            bg_color = EventEditorDialog.QUARTER_COLORS.get(quarter, "#ffffff")
            item.setBackground(QColor(bg_color))

            if quarter in ["Q2", "Q4", "H2"]:
                item.setForeground(QColor("#333333"))
            else:
                item.setForeground(QColor("#000000"))

            # Highlight styling (unchanged)
            if is_highlight:
                font = item.font()
                font.setBold(True)
                item.setFont(font)

            self.events_list.addItem(item)

        conn.close()
        self.refresh_nav_combos()

    def jump_to_event(self, item):
        if self.player.is_playing():
            self.player.pause()
            self.play_btn.setText("Play ▶ ")

        pos_ms = item.data(Qt.UserRole)
        if pos_ms is not None:
            self.player.set_time(pos_ms)
            self.update_ui()

        # Longer delay to let Qt fully process the selection change
        QTimer.singleShot(50, self.highlight_selected_event)

    def edit_event(self):
        item = self.events_list.currentItem()
        if not item:
            return
        event_id = item.data(Qt.UserRole + 1)
        pos_ms = item.data(Qt.UserRole)

        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("""SELECT time_ms, quarter, player_id, team_side, type, 
                            location, is_highlight 
                     FROM events WHERE id = ?""", (event_id,))
        row = c.fetchone()
        conn.close()

        if row:
            time_sec = row[0] / 1000
            event_type = row[4]  # ← THIS WAS MISSING

            dialog = EventEditorDialog(self, self.game_id, time_sec,
                                       self.video_id, event_id=event_id)

            dialog.time_edit.setReadOnly(True)
            dialog.quarter_combo.setCurrentText(row[1])

            # Player restore
            if row[2]:  # player_id
                for i in range(dialog.player_combo.count()):
                    if dialog.player_combo.itemData(i) == row[2]:
                        dialog.player_combo.setCurrentIndex(i)
                        break
            else:
                prefix = "H" if row[3] == 'home' else 'G'
                dialog.player_combo.setCurrentText(f"{prefix} -")

            # === RESTORE EVENT TYPE (Radio Buttons) ===
            found = False
            for radio_dict in (dialog.shot_radios, dialog.nonshot_radios):
                for text, radio in radio_dict.items():
                    if text == event_type:
                        radio.setChecked(True)
                        dialog.toggle_location(event_type)
                        found = True
                        break
                if found:
                    break

            # Location (only if enabled)
            if row[5]:  # location
                if dialog.location_combo.isEnabled():
                    idx = dialog.location_combo.findText(row[5])
                    if idx >= 0:
                        dialog.location_combo.setCurrentIndex(idx)

            # Highlight checkbox
            if hasattr(dialog, 'highlight_checkbox'):
                dialog.highlight_checkbox.setChecked(bool(row[6]))

            # Shot Quality is already handled inside toggle_location() + restore in the dialog

            if dialog.exec_() == QDialog.Accepted:
                self.load_events()
                self.refresh_nav_combos()

    def delete_event(self):
        item = self.events_list.currentItem()
        if item:
            event_id = item.data(Qt.UserRole + 1)
            reply = QMessageBox.question(self, "Delete", "Delete event?", QMessageBox.Yes | QMessageBox.No)
            if reply == QMessageBox.Yes:
                conn = sqlite3.connect(DB_NAME)
                c = conn.cursor()
                c.execute("DELETE FROM events WHERE id = ?", (event_id,))
                conn.commit()
                conn.close()
                self.load_events()
                self.refresh_nav_combos()

class GamesheetEditorDialog(QDialog):
    def __init__(self, parent=None, source_path: str = None, game_name_hint: str = ""):
        super().__init__(parent)
        self.setWindowTitle("CourtTag Gamesheet Editor")
        self.setMinimumSize(900, 700)
        self.resize(1100, 850)

        self.original_image = None
        self.current_image = None
        self.rotation_deg = 0
        self.game_name_hint = game_name_hint
        self.saved_relative_path = None  # set on successful save

        self._setup_ui()
        self._setup_toolbar()
        self._setup_shortcuts()

        if source_path:
            self.load_image(source_path)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setStyleSheet("QScrollArea { background: #1e1e1e; border: none; }")

        self.preview_label = QLabel()
        self.preview_label.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.preview_label.setStyleSheet("background: #1e1e1e;")
        self.scroll_area.setWidget(self.preview_label)

        layout.addWidget(self.scroll_area, stretch=1)

        self.status_bar = QStatusBar()
        self.status_bar.showMessage("No gamesheet loaded")
        layout.addWidget(self.status_bar)

    def _setup_toolbar(self):
        toolbar = QToolBar("Edit Tools")
        toolbar.setMovable(False)
        toolbar.setIconSize(QSize(24, 24))
        toolbar.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)

        # Open
        act_open = QAction("Open", self)
        act_open.setShortcut("Ctrl+O")
        act_open.triggered.connect(self.open_image)
        toolbar.addAction(act_open)

        toolbar.addSeparator()

        # Rotate
        act_rot_cw = QAction("Rotate ↻ (R)", self)
        act_rot_cw.triggered.connect(self.rotate_cw)
        toolbar.addAction(act_rot_cw)

        act_rot_ccw = QAction("Rotate ↺ (Shift+R)", self)
        act_rot_ccw.triggered.connect(self.rotate_ccw)
        toolbar.addAction(act_rot_ccw)

        # Grayscale toggle
        self.act_grayscale = QAction("Grayscale (G)", self)
        self.act_grayscale.setCheckable(True)
        self.act_grayscale.setShortcut("G")
        self.act_grayscale.triggered.connect(self._apply_edits)
        toolbar.addAction(self.act_grayscale)

        # Contrast & Crop
        act_contrast = QAction("Boost Contrast (C)", self)
        act_contrast.setShortcut("C")
        act_contrast.triggered.connect(self.boost_contrast)
        toolbar.addAction(act_contrast)

        act_crop = QAction("Trim Borders (T)", self)
        act_crop.setShortcut("T")
        act_crop.triggered.connect(self.quick_crop)
        toolbar.addAction(act_crop)

        toolbar.addSeparator()

        # Reset
        act_reset = QAction("Reset", self)
        act_reset.triggered.connect(self.reset_edits)
        toolbar.addAction(act_reset)

        toolbar.addSeparator()

        # Save / Cancel
        act_save = QAction("Apply Changes + Save", self)
        act_save.setShortcut("Ctrl+S")
        act_save.triggered.connect(self.accept)
        toolbar.addAction(act_save)

        act_cancel = QAction("Cancel", self)
        act_cancel.triggered.connect(self.reject)
        toolbar.addAction(act_cancel)

        self.layout().insertWidget(0, toolbar)

    def _setup_shortcuts(self):
        QShortcut(QKeySequence("Escape"), self, self.reject)

    def load_image(self, path: str):
        try:
            self.original_image = Image.open(path).convert("RGB")
            self.current_image = self.original_image.copy()
            self.rotation_deg = 0
            self.act_grayscale.setChecked(False)
            self.display_image()
            self.status_bar.showMessage(f"Loaded: {os.path.basename(path)} | Edits: none")
        except Exception as e:
            QMessageBox.critical(self, "Load Error", f"Could not load image:\n{e}")

    def open_image(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Select CourtTag Gamesheet",
            "", "Image Files (*.png *.jpg *.jpeg *.bmp *.tif *.tiff);;All Files (*.*)"
        )
        if file_path:
            self.load_image(file_path)

    def display_image(self):
        if not self.current_image:
            self.preview_label.setText("No image loaded")
            return

        img = self.current_image.convert("RGBA")
        data = img.tobytes("raw", "RGBA")
        qimg = QImage(data, img.width, img.height, QImage.Format_RGBA8888)
        pixmap = QPixmap.fromImage(qimg)
        scaled = pixmap.scaled(self.preview_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.preview_label.setPixmap(scaled)
        self.preview_label.setScaledContents(False)

    def rotate_cw(self):
        self.rotation_deg = (self.rotation_deg + 90) % 360
        self._apply_edits()

    def rotate_ccw(self):
        self.rotation_deg = (self.rotation_deg - 90) % 360
        self._apply_edits()

    def boost_contrast(self):
        if not self.current_image: return
        enhancer = ImageEnhance.Contrast(self.current_image)
        self.current_image = enhancer.enhance(1.8)
        self.display_image()
        self._update_status()

    def quick_crop(self):
        if not self.current_image: return
        w, h = self.current_image.size
        margin = 80
        if w <= 2 * margin or h <= 2 * margin:
            QMessageBox.information(self, "Info", "Image too small to crop.")
            return
        box = (margin, margin, w - margin, h - margin)
        self.current_image = self.current_image.crop(box)
        self.display_image()
        self._update_status()

    def reset_edits(self):
        if not self.original_image: return
        self.current_image = self.original_image.copy()
        self.rotation_deg = 0
        self.act_grayscale.setChecked(False)
        self.display_image()
        self._update_status()

    def _apply_edits(self):
        if not self.original_image: return
        img = self.original_image.rotate(self.rotation_deg, expand=True, resample=Image.Resampling.BICUBIC)
        if self.act_grayscale.isChecked():
            img = img.convert('L')
        self.current_image = img
        self.display_image()
        self._update_status()

    def _update_status(self):
        edits = []
        if self.rotation_deg != 0:
            edits.append(f"rotated {self.rotation_deg}°")
        if self.act_grayscale.isChecked():
            edits.append("grayscale")
        self.status_bar.showMessage(f"Edits: {', '.join(edits) or 'none'}")

    def suggest_filename(self):
        base = self.game_name_hint.strip().replace(" ", "_").replace("/", "-") if self.game_name_hint else "edited_gamesheet"
        timestamp = datetime.now().strftime("%Y%m%d_%H%M")
        return f"{base}_{timestamp}.png"

    def accept(self):
        if not self.current_image:
            QMessageBox.warning(self, "No Image", "No edited gamesheet loaded.")
            return

        super().accept()

    def get_edited_image(self):
        return self.current_image if self.current_image else None

    def get_saved_relative_path(self):
        return self.saved_relative_path


class GamesheetViewDialog(QWidget):
    """Non-modal gamesheet viewer with mouse-wheel zoom + standard scrollbars"""

    def __init__(self, parent=None, image_path: str = None, game_name: str = "", game_location: str = ""):
        super().__init__(parent)

        # Top-level, independent window
        self.setWindowFlags(Qt.Window | Qt.WindowTitleHint | Qt.WindowCloseButtonHint )

        self.setWindowTitle(f"Gamesheet Viewer - {game_name} - {game_location}".strip(" - "))

        self.resize(1000, 800)
        self.setMinimumSize(600, 400)

        self.image_path = image_path
        self.original_pixmap = None
        self.current_scale = 1.0

        self._setup_ui()
        self._load_image()

        # Mouse tracking for zoom
        self.setMouseTracking(True)
        self.preview_label.setMouseTracking(True)
        self.scroll.setMouseTracking(True)

        self.preview_label.setFocusPolicy(Qt.StrongFocus)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Scroll area with always-on scrollbars when needed
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setStyleSheet("QScrollArea { background: #1e1e1e; border: none; }")

        # Always show scrollbars when content overflows
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)

        self.preview_label = QLabel()
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setStyleSheet("background: #1e1e1e;")
        self.preview_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)

        self.scroll.setWidget(self.preview_label)
        layout.addWidget(self.scroll)

        # Status bar for zoom level
        self.status = QStatusBar()
        self.status.showMessage("Zoom: 100%")
        layout.addWidget(self.status)

        # Only keep wheel zoom
        self.preview_label.wheelEvent = self.wheelEvent

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self.raise_()
        self.activateWindow()

    def _load_image(self):
        if not self.image_path or not os.path.exists(self.image_path):
            self.preview_label.setText("No gamesheet available or file missing.")
            return

        try:
            img = Image.open(self.image_path)
            img = img.convert("RGBA")
            data = img.tobytes("raw", "RGBA")
            qimg = QImage(data, img.width, img.height, QImage.Format_RGBA8888)
            self.original_pixmap = QPixmap.fromImage(qimg)
            self.update_image()
        except Exception as e:
            self.preview_label.setText(f"Failed to load:\n{str(e)}")

    def update_image(self):
        if not self.original_pixmap:
            return

        # Calculate scaled size (QSize * float → QSize with integer truncation)
        scaled_size = self.original_pixmap.size() * self.current_scale

        scaled = self.original_pixmap.scaled(
            scaled_size,
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation
        )

        self.preview_label.setPixmap(scaled)

        # Force label size so scrollbars appear when zoomed in
        self.preview_label.setFixedSize(scaled_size)

        # Alignment: center if fits, top-left if larger
        if scaled_size.width() < self.scroll.viewport().width():
            self.preview_label.setAlignment(Qt.AlignCenter)
        else:
            self.preview_label.setAlignment(Qt.AlignTop | Qt.AlignLeft)

        self.status.showMessage(f"Zoom: {int(self.current_scale * 100)}%")

    # ── Mouse wheel zoom only ────────────────────────────────────────────────
    def wheelEvent(self, event):
        #print("Wheel event received in viewer")
        if not self.original_pixmap:
            return

        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        old_scale = self.current_scale
        self.current_scale *= factor
        self.current_scale = max(0.25, min(self.current_scale, 6.0))

        # Get mouse position relative to label
        mouse_pos = event.pos()
        label_pos = self.preview_label.mapFromGlobal(self.mapToGlobal(mouse_pos))

        # Keep zoom centered on cursor
        hbar = self.scroll.horizontalScrollBar()
        vbar = self.scroll.verticalScrollBar()

        hbar.setValue(int(hbar.value() + label_pos.x() * (factor - 1)))
        vbar.setValue(int(vbar.value() + label_pos.y() * (factor - 1)))

        self.update_image()

class CoachesStatTracker(QMainWindow):
    def __init__(self):
        super().__init__()
        #self.setWindowTitle("Coaches Stat Tracker")
        #self.setWindowIcon(QIcon("CourtTag_Icon.ico"))  # .ico (best)
        self.setWindowIcon(QIcon(":/CourtTag_Icon.ico"))

        self.setWindowTitle(f"CourtTag v{VERSION}")
        self.setGeometry(100, 100, 1200, 800)

        self.tab_widget = QTabWidget()
        self.setCentralWidget(self.tab_widget)

        self.games_tab = QWidget()
        self.teams_tab = QWidget()
        self.players_tab = QWidget()


        self.tab_widget.addTab(self.teams_tab, "Teams")
        self.tab_widget.addTab(self.players_tab, "Players")
        self.tab_widget.addTab(self.games_tab, "Games")

        self.setup_games_tab()
        self.setup_teams_tab()
        self.setup_players_tab()

        # ── Add the menu bar here ───────────────────────────────────────────────
        menubar = self.menuBar()                           # Get/create the menu bar

        # Optional: Add other menus first (File, Edit, etc.) if you plan to expand
        file_menu = menubar.addMenu("&File")

        #Set Defaults
        set_defaults_act = QAction("Set &Defaults...", self)
        set_defaults_act.setShortcut("Ctrl+D")  # optional
        set_defaults_act.triggered.connect(self.show_set_defaults_dialog)
        file_menu.addAction(set_defaults_act)  # ← use file_menu (lowercase), not self.file_menu

        #Merge Video Action
        merge_action = file_menu.addAction("Merge Videos...")
        merge_action.setShortcut("Ctrl+M")  # optional
        merge_action.triggered.connect(self.merge_videos_dialog)
        #file_menu.addAction(merge_action)

        # Backup action
        backup_action = file_menu.addAction("&Backup Database...")
        backup_action.setShortcut("Ctrl+B")  # optional
        backup_action.triggered.connect(self.backup_database)

        # Restore action
        restore_action = file_menu.addAction("&Restore Database...")
        restore_action.setShortcut("Ctrl+R")  # optional
        restore_action.triggered.connect(self.restore_database)

        # Optional: separator and Exit
        file_menu.addSeparator()

        export_action = file_menu.addAction("&Export ALL Records to CSV")
        export_action.triggered.connect(self.export_all_records_to_csv)

        # Optional: separator before Exit
        file_menu.addSeparator()

        file_menu.addAction("Exit", self.close)

        # Create or get "View" menu (add it if not existing)
        view_menu = menubar.addMenu("&View")  # & for shortcut Alt+V

        # Add the report action
        season_report_action = view_menu.addAction("Season Shot Location Summary")
        season_report_action.triggered.connect(self.show_season_summary_report)

        view_menu.addAction("Season Shot Quality Summary", self.show_season_shot_quality_report)

        # Help menu (last one by convention)
        help_menu = menubar.addMenu("&Help")

        # Add a Shared Public folder action
        public_action = help_menu.addAction("&Open CourtTag.net Shared Folder")
        public_action.triggered.connect(self.open_CourtTag_website)
        help_menu.addAction(public_action)

        # Add the Tip action
        youtube_action = help_menu.addAction("&Open YouTube.com/@CourtTag")
        youtube_action.triggered.connect(self.open_youtube)
        help_menu.addAction(youtube_action)

        # Add the Tip action
        tip_action = help_menu.addAction("&Tip CourtTag... ☕")
        tip_action.triggered.connect(self.open_bmac)
        help_menu.addAction(tip_action)

        help_menu.addSeparator()

        # Descriptions / Instructions
        #descriptions_action = help_menu.addAction("&Descriptions")
        #descriptions_action.triggered.connect(self.show_descriptions_dialog)

        # User Guide
        guide_action = help_menu.addAction("&User Guide")
        guide_action.triggered.connect(self.open_help)
        help_menu.addAction(guide_action)

        help_menu.addSeparator()

        # Optional extras you might want later
        # User Guide
        update_action = help_menu.addAction("&Check for Updates")
        update_action.triggered.connect(self.check_for_update)
        help_menu.addAction(update_action)

        # About Info
        about_action = help_menu.addAction("&About CourtTag")
        about_action.triggered.connect(self.show_about_dialog)

        # Optional: status bar reinforcement
        self.statusBar().addPermanentWidget(QLabel(f"CourtTag v{VERSION}{BUILD}"))

    def show_set_defaults_dialog(self):
        dialog = DefaultsDialog(self)
        dialog.exec_()
        # Refresh all tabs so the new default takes effect immediately
        self.refresh_all_season_filters()   # you already have this method

    def apply_default_season_to_all_tabs(self):
        """Refresh all three season combo boxes with the new default and reload their tables."""
        default_season = self.get_default_season()

        # Teams tab
        if hasattr(self, 'team_season_combo'):
            self.safe_update_season_combo(self.team_season_combo, default_season, self.load_teams)

        # Players tab
        if hasattr(self, 'player_season_combo'):
            self.safe_update_season_combo(self.player_season_combo, default_season, self.load_players)

        # Games tab
        if hasattr(self, 'game_season_combo'):
            self.safe_update_season_combo(self.game_season_combo, default_season, self.load_games)

    def safe_update_season_combo(self, combo: QComboBox, default_season, load_callback):
        """Safely set a season combo to the default (or 'All') and trigger reload."""
        if not combo:
            return

        target = default_season if default_season is not None else "All"

        for i in range(combo.count()):
            if combo.itemText(i) == target:
                combo.setCurrentIndex(i)
                try:
                    if load_callback:
                        load_callback()
                except Exception as e:
                    print(f"Warning: Could not refresh tab after default change: {e}")
                return

        # Fallback: if the season no longer exists, force "All"
        for i in range(combo.count()):
            if combo.itemText(i) == "All":
                combo.setCurrentIndex(i)
                try:
                    if load_callback:
                        load_callback()
                except Exception as e:
                    print(f"Warning: Could not reset to All: {e}")
                return

    def merge_videos_dialog(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Merge Videos")
        dialog.setMinimumWidth(650)

        layout = QVBoxLayout(dialog)
        layout.setSpacing(12)
        layout.setContentsMargins(16, 16, 16, 16)

        # ── Instructions ───────────────────────────────────────────────────────
        layout.addWidget(QLabel(
            "<b>Select videos to merge (in desired order):</b><br>"
            "Drag & drop or use buttons. Order matters — top is first in the output."
        ))

        # List widget for selected files
        self.merge_list = QListWidget()
        self.merge_list.setDragDropMode(QAbstractItemView.InternalMove)  # allow reordering
        self.merge_list.setAcceptDrops(True)
        self.merge_list.setAlternatingRowColors(True)
        self.merge_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        layout.addWidget(self.merge_list, stretch=1)

        # Buttons row
        btn_lay = QHBoxLayout()
        add_btn = QPushButton("Add Video Files...")
        add_btn.clicked.connect(self.add_merge_files)
        btn_lay.addWidget(add_btn)

        remove_btn = QPushButton("Remove Selected")
        remove_btn.clicked.connect(
            lambda: [self.merge_list.takeItem(i) for i in reversed(range(self.merge_list.count())) if
                     self.merge_list.item(i).isSelected()])
        btn_lay.addWidget(remove_btn)

        clear_btn = QPushButton("Clear All")
        clear_btn.clicked.connect(self.merge_list.clear)
        btn_lay.addWidget(clear_btn)

        layout.addLayout(btn_lay)

        # ── Output settings ────────────────────────────────────────────────────
        out_lay = QHBoxLayout()
        out_lay.addWidget(QLabel("Output file:"))

        # Re-encode option
        reencode_lay = QHBoxLayout()
        reencode_cb = QCheckBox("Re-encode merged video to smaller size (H.264, good quality)")
        reencode_cb.setChecked(False)  # default: fast copy
        reencode_cb.setToolTip(
            "Makes the final file smaller for easier sharing/email.\n"
            "Takes longer than direct copy (2–10×), but quality remains very good for game footage."
        )
        reencode_lay.addWidget(reencode_cb)
        layout.addLayout(reencode_lay)

        default_out = str(
            Path.home() / "Documents" / "CourtTag" / "Merged" / f"merged_{datetime.now().strftime('%Y%m%d_%H%M%S')}.mp4")
        self.out_edit = QLineEdit(default_out)
        out_lay.addWidget(self.out_edit, stretch=1)

        browse_out = QPushButton("Browse...")
        browse_out.clicked.connect(self.browse_merge_output)
        out_lay.addWidget(browse_out)

        layout.addLayout(out_lay)

        # Sort options
        sort_lay = QHBoxLayout()
        sort_lay.addWidget(QLabel("Auto-sort by:"))
        self.sort_combo = QComboBox()
        self.sort_combo.addItems(
            ["No sort (manual order)", "Date created (oldest first)", "Date created (newest first)", "Filename"])
        sort_lay.addWidget(self.sort_combo)
        layout.addLayout(sort_lay)

        # Buttons
        btn_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btn_box.accepted.connect(dialog.accept)
        btn_box.rejected.connect(dialog.reject)
        layout.addWidget(btn_box)

        if dialog.exec_() == QDialog.Accepted:
            files = [self.merge_list.item(i).text() for i in range(self.merge_list.count())]
            if len(files) < 2:
                QMessageBox.warning(self, "Not enough files", "Please select at least 2 videos to merge.")
                return

            out_path = Path(self.out_edit.text().strip())
            if not out_path.suffix.lower().endswith('.mp4'):
                out_path = out_path.with_suffix('.mp4')

            # REMOVE or COMMENT OUT these lines:
            # if not out_path.parent.exists():
            #     QMessageBox.warning(self, "Invalid folder", "Output folder does not exist.")
            #     return

            # Optional auto-sort
            sort_mode = self.sort_combo.currentText()
            if sort_mode != "No sort (manual order)":
                files = self.sort_merge_files(files, sort_mode)

            reencode = reencode_cb.isChecked()

            total_size_mb = 0
            for f in files:
                try:
                    total_size_mb += Path(f).stat().st_size / (1024 * 1024)
                except:
                    pass

            file_count = len(files)
            estimated_time = ""

            if reencode:
                # Re-encoding: very rough ~5–15 seconds per GB on modern CPU
                est_seconds = max(30, int(total_size_mb * 8))  # conservative
                estimated_time = f"Estimated time: {est_seconds // 60}–{(est_seconds // 60) + 2} minutes"
            else:
                # Stream copy: ~1–5 seconds per GB
                est_seconds = max(5, int(total_size_mb * 1.5))
                estimated_time = f"Estimated time: {est_seconds // 60}–{est_seconds // 60 + 1} minute(s)"

            # Then in the dialog label:
            lbl = QLabel(f"Merging in progress...\n{estimated_time}")

            self.perform_merge(files, out_path, reencode=reencode)

    def add_merge_files(self):
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "Select Video Files to Merge",
            str(Path.home() / "Documents" / "CourtTag" / "Clips"),
            "Video Files (*.mp4 *.mov *.avi *.mkv)"
        )
        for f in files:
            if f not in [self.merge_list.item(i).text() for i in range(self.merge_list.count())]:
                self.merge_list.addItem(f)

    def browse_merge_output(self):
        path = QFileDialog.getSaveFileName(
            self,
            "Select Output Merged Video",
            self.out_edit.text(),
            "MP4 Video (*.mp4)"
        )[0]
        if path:
            self.out_edit.setText(path)

    def sort_merge_files(self, files, mode):
        from pathlib import Path
        path_files = [Path(f) for f in files]

        if "Date created (oldest first)" in mode:
            path_files.sort(key=lambda p: p.stat().st_ctime)
        elif "Date created (newest first)" in mode:
            path_files.sort(key=lambda p: p.stat().st_ctime, reverse=True)
        elif "Filename" in mode:
            path_files.sort(key=lambda p: p.name.lower())

        return [str(p) for p in path_files]

    def open_bmac(self):
        webbrowser.open("https://www.buymeacoffee.com/courttag")

    def open_youtube(self):
        webbrowser.open("https://www.youtube.com/@CourtTag")

    def open_CourtTag_website(self):
        webbrowser.open("https://courttag.net")

    def open_help(self):
        dialog = HelpDialog(self)  # 'self' is the parent window
        dialog.exec_()

    def fetch_user_guide(self, direct_url):
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
            }
            response = requests.get(direct_url, headers=headers, timeout=15, allow_redirects=True)
            response.raise_for_status()
            return response.text
        except Exception as e:
            raise Exception(f"Fetch failed: {e}")

    def show_descriptions_dialog(self):
        """Opens a help window with app instructions (HTML formatted)"""
        dialog = QDialog(self)
        dialog.setWindowTitle("CourtTag – Descriptions")
        dialog.setGeometry(260, 180, 780, 820)

        layout = QVBoxLayout(dialog)
        #layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # Scrollable rich text area
        #text_edit = QTextEdit()
        text_edit = QTextBrowser()
        text_edit.setOpenExternalLinks(True)  # ← Critical
        text_edit.setReadOnly(True)
        text_edit.setHtml(self.get_decriptions_html())
        layout.addWidget(text_edit)

        # Close button – lower right corner
        button_layout = QHBoxLayout()
        button_layout.addStretch()  # pushes button to the right

        close_btn = QPushButton("Close")
        close_btn.setFixedWidth(120)
        close_btn.setStyleSheet("font-weight: bold;")  # optional emphasis
        close_btn.clicked.connect(dialog.accept)
        button_layout.addWidget(close_btn)

        layout.addLayout(button_layout)

        dialog.exec_()

    def check_for_update(self, silent=False):
        """Fetch remote version info from GitHub and compare with local."""
        try:
            GITHUB_OWNER = "CourtTag"
            GITHUB_REPO = "courttag-dbs"
            FILE_PATH = "current_version.txt"

            url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{FILE_PATH}"

            headers = {
                "Accept": "application/vnd.github.v3.raw",
                "Authorization": f"token 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"
            }

            response = requests.get(url, headers=headers, timeout=10)
            if response.status_code != 200:
                raise Exception(f"GitHub returned status {response.status_code}")

            raw_text = response.text

            if not raw_text:
                return  # silent fail

            # Parse the version file
            remote = {}
            for line in raw_text.strip().splitlines():
                line = line.strip()
                if '=' in line and not line.startswith('#'):
                    key, value = line.split('=', 1)
                    remote[key.strip().upper()] = value.strip()

            remote_version = remote.get('VERSION', '0.0')
            remote_build = remote.get('BUILD', 'b0')
            remote_db = remote.get('DB_VERSION', 'unknown')
            download_url = remote.get('DOWNLOAD_URL', '')
            whats_new = remote.get('WHATS_NEW', 'No details provided')

            # Compare versions
            version_newer = self._is_version_newer(remote_version, VERSION)
            build_newer = self._is_build_newer(remote_build, BUILD)
            db_changed = remote_db != DB_VERSION and remote_db != 'unknown'

            if version_newer or build_newer or db_changed:
                self._show_update_prompt(
                    remote_version, remote_build, remote_db,
                    download_url, whats_new,
                    version_newer, build_newer, db_changed
                )
            elif not silent:
                QMessageBox.information(
                    self,
                    "Up to Date",
                    f"You're running the latest version of CourtTag:\n"
                    f"• EXE Version {VERSION} (build {BUILD})\n"
                    f"• DB Version: {DB_VERSION}\n\n"
                    "No updates available at this time.",
                    QMessageBox.Ok
                )

        except Exception as e:
            print(f"Update check failed silently: {e}")
            # No popup for network/offline issues - common for users

    def _is_version_newer(self, remote: str, local: str) -> bool:
        """Compare major.minor style versions (fallback to string if no packaging)"""
        try:
            from packaging import version
            return version.parse(remote) > version.parse(local)
        except (ImportError, Exception):
            # Fallback: simple string compare (works well for 1.0 > 0.9, 1.10 > 1.9)
            return remote > local

    def _is_build_newer(self, remote: str, local: str) -> bool:
        """Assuming build like 'b33' → compare the number after 'b'"""
        if not (remote.startswith('b') and local.startswith('b')):
            return remote > local  # fallback
        try:
            return int(remote[1:]) > int(local[1:])
        except ValueError:
            return remote > local

    def _show_update_prompt(self, r_ver, r_build, r_db, dl_url, whats_new, v_newer, b_newer, db_change):
        title = "Update Available" if v_newer or b_newer else "Database Format Change Detected"

        msg_text = f"A newer version is available:\n"
        if v_newer:
            msg_text += f"• APP Version: {r_ver} (you have {VERSION})\n"
        if b_newer:
            msg_text += f"• APP Build: {r_build} (you have {BUILD})\n"
        if db_change:
            msg_text += f"• DB Version: {r_db} (you have {DB_VERSION}) – may require migration\n"

        msg_text += f"\nWhat's new:\n{whats_new[:300]}{'...' if len(whats_new) > 300 else ''}"
        msg_text += f"\nDo you want to download the newest version?"

        reply = QMessageBox.question(
            self,
            title,
            msg_text,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes
        )

        if reply == QMessageBox.Yes and dl_url:
            import webbrowser
            webbrowser.open(dl_url)



    def add_merge_files(self):
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "Select Video Files to Merge",
            str(Path.home() / "Documents" / "CourtTag" / "Clips"),
            "Video Files (*.mp4 *.mov *.avi *.mkv)"
        )
        for f in files:
            if f not in [self.merge_list.item(i).text() for i in range(self.merge_list.count())]:
                self.merge_list.addItem(f)

    def browse_merge_output(self):
        path = QFileDialog.getSaveFileName(
            self,
            "Select Output Merged Video",
            self.out_edit.text(),
            "MP4 Video (*.mp4)"
        )[0]
        if path:
            self.out_edit.setText(path)

    def sort_merge_files(self, files, mode):
        from pathlib import Path
        path_files = [Path(f) for f in files]

        if "Date created (oldest first)" in mode:
            path_files.sort(key=lambda p: p.stat().st_ctime)
        elif "Date created (newest first)" in mode:
            path_files.sort(key=lambda p: p.stat().st_ctime, reverse=True)
        elif "Filename" in mode:
            path_files.sort(key=lambda p: p.name.lower())

        return [str(p) for p in path_files]

    def perform_merge(self, input_files, output_path: Path, reencode=False):
        if len(input_files) < 2:
            return

        # Create concat list
        concat_txt = output_path.with_name("concat_merge.txt")
        with open(concat_txt, "w", encoding="utf-8") as f:
            for file in input_files:
                f.write(f"file '{Path(file).resolve().as_posix()}'\n")

        # Create output folder if needed
        output_dir = output_path.parent
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            QMessageBox.critical(
                self,
                "Folder Creation Failed",
                f"Could not create output folder:\n{output_dir}\n\nError: {str(e)}"
            )
            if concat_txt.exists():
                concat_txt.unlink()
            return

        # Progress dialog
        progress_dialog = QDialog(self)
        progress_dialog.setWindowTitle("Merging Videos")
        progress_dialog.setFixedSize(340, 140)
        progress_dialog.setModal(True)

        lay = QVBoxLayout(progress_dialog)
        lay.setContentsMargins(20, 20, 20, 20)

        lbl_text = "Merging in progress...\n(usually under 30 seconds)"
        if reencode:
            lbl_text = "Re-encoding in progress...\nThis will take several minutes depending on file size"

        lbl = QLabel(lbl_text)
        lbl.setAlignment(Qt.AlignCenter)
        lbl.setStyleSheet("font-size: 14px; font-weight: bold; color: #333;")
        lay.addWidget(lbl)

        progress_dialog.show()
        QApplication.processEvents()

        try:
            stream = ffmpeg.input(str(concat_txt), format='concat', safe=0)

            if reencode:
                # Re-encode: smaller H.264 file
                stream = ffmpeg.output(
                    stream,
                    str(output_path),
                    vcodec='libx264',
                    preset='veryfast',  # fast encoding
                    crf=23,  # good quality vs size balance
                    acodec='copy',  # audio unchanged
                    loglevel='error',
                    y=None
                )
                # Optional: update dialog text for longer operation
                lbl.setText("Re-encoding merged video\n(this may take several minutes)")
            else:
                # Fast stream copy
                stream = ffmpeg.output(
                    stream,
                    str(output_path),
                    c='copy',
                    loglevel='error',
                    y=None
                )

            start_time = time.time()

            ffmpeg.run(stream)

            progress_dialog.close()

            if output_path.exists():
                duration_sec = time.time() - start_time  # add start_time = time.time() before ffmpeg.run
                final_size_mb = output_path.stat().st_size / (1024 * 1024) if output_path.exists() else 0

                msg = f"Merged video saved to:\n{output_path.resolve()}\n\n"
                msg += f"Files merged: {len(input_files)}\n"
                msg += f"Time taken: {duration_sec:.1f} seconds\n"
                msg += f"Final file size: {final_size_mb:.1f} MB"
                if reencode:
                    msg += " (re-encoded for smaller size)"
                QMessageBox.information(self, "Merge Complete", msg)

        except ffmpeg.Error as e:
            progress_dialog.close()
            QMessageBox.critical(
                self,
                "Merge Failed",
                f"ffmpeg error:\n{e.stderr.decode('utf-8', errors='replace') or str(e)}"
            )
        except Exception as e:
            progress_dialog.close()
            QMessageBox.critical(self, "Error", str(e))
        finally:
            if concat_txt.exists():
                concat_txt.unlink()

    def backup_database(self):
        r"""Backup the current database — default folder is Documents\CourtTag\Backups"""
        if not DB_NAME.exists():
            QMessageBox.warning(self, "No Database", "No database file found to back up.")
            return

        backup_folder = self.get_backup_folder()

        # Suggest a timestamped filename in the backup folder
        default_name = f"CourtTag_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
        default_path = backup_folder / default_name

        backup_path, _ = QFileDialog.getSaveFileName(
            self,
            "Backup CourtTag Database",
            str(default_path),
            "SQLite Database (*.db);;All Files (*)"
        )

        if backup_path:
            try:
                shutil.copy(DB_NAME, backup_path)
                QMessageBox.information(
                    self,
                    "Backup Successful",
                    f"Database backed up to:\n{backup_path}"
                )
            except Exception as e:
                QMessageBox.critical(self, "Backup Failed", f"Could not copy database:\n{str(e)}")

    def restore_database(self):
        r"""Restore from a backup file — starts in Documents\CourtTag\Backups"""
        backup_folder = self.get_backup_folder()

        backup_path, _ = QFileDialog.getOpenFileName(
            self,
            "Restore Database Backup",
            str(backup_folder),
            "SQLite Database (*.db);;All Files (*)"
        )

        if backup_path:
            reply = QMessageBox.question(
                self,
                "Confirm Restore",
                "This will OVERWRITE the current database with the selected backup.\n"
                "All current data will be lost. Continue?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )
            if reply == QMessageBox.Yes:
                try:
                    shutil.copy(backup_path, DB_NAME)
                    QMessageBox.information(
                        self,
                        "Restore Successful",
                        "Database restored.\nRestart the app to use the restored data."
                    )
                    # Optional: restart the app automatically (uncomment if wanted)
                    # QApplication.quit()
                    # QProcess.startDetached(sys.executable, sys.argv)
                except Exception as e:
                    QMessageBox.critical(self, "Restore Failed", f"Could not restore:\n{str(e)}")

    def get_backup_folder(self):
        """Return the path to the Backups folder (create if missing)."""
        base_folder = Path.home() / "Documents" / "CourtTag" / "Backups"
        try:
            base_folder.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            print(f"Warning: Could not create backup folder {base_folder}: {e}")
            # Fallback to a temp location or app folder if needed
            base_folder = Path.home() / "CourtTag_Backups"
            base_folder.mkdir(parents=True, exist_ok=True)
        return base_folder

    def get_decriptions_html(self):
        BASE_DIR = Path(__file__).parent
        HTML_PATH = BASE_DIR / "CourtTag_Descriptions_HTML.html"  # same folder

        html_content = f"<html><head></head><body><p>Loading:{HTML_PATH}</p></body></html>"

        try:
            html_content = HTML_PATH.read_text(encoding="utf-8")
        except FileNotFoundError:
            html_content += f"<html><head></head><body><p>Could not find instructions file:{HTML_PATH}</p></body></html>"
        except Exception as e:
            html_content += f"<html><head></head><body><p>Error loading instructions:{str(e)}</p></body></html>"

        return html_content

    def refresh_all_season_filters(self):
        """
        Refresh ALL season dropdowns across tabs from DB.
        """
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT DISTINCT season FROM teams WHERE season IS NOT NULL ORDER BY season DESC")
        seasons = ["All"] + [s[0] for s in c.fetchall() if s[0]]
        conn.close()

        # Games tab combo
        if hasattr(self, 'game_season_combo'):
            current = self.game_season_combo.currentText()
            self.game_season_combo.clear()
            for s in seasons:
                self.game_season_combo.addItem(s)
            index = self.game_season_combo.findText(current)
            if index >= 0:
                self.game_season_combo.setCurrentIndex(index)

        # Teams tab combo
        if hasattr(self, 'team_season_combo'):
            current = self.team_season_combo.currentText()
            self.team_season_combo.clear()
            for s in seasons:
                self.team_season_combo.addItem(s)
            index = self.team_season_combo.findText(current)
            if index >= 0:
                self.team_season_combo.setCurrentIndex(index)

        # Players tab combo
        if hasattr(self, 'player_season_combo'):
            current = self.player_season_combo.currentText()
            self.player_season_combo.clear()
            for s in seasons:
                self.player_season_combo.addItem(s)
            index = self.player_season_combo.findText(current)
            if index >= 0:
                self.player_season_combo.setCurrentIndex(index)

    def update_team_combo_for_season(self, season_combo, team_combo, all_text="All"):
        """Update team dropdown based on selected season - with proper ID storage"""
        if not season_combo or not team_combo:
            return

        current_text = team_combo.currentText()

        team_combo.clear()
        team_combo.addItem(all_text)  # "All" has no data

        selected_season = season_combo.currentText().strip()

        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()

        if selected_season in ("All Seasons", "All", ""):
            c.execute("""
                SELECT id, name FROM teams 
                ORDER BY name
            """)
        else:
            c.execute("""
                SELECT id, name FROM teams 
                WHERE season = ? 
                ORDER BY name
            """, (selected_season,))

        for tid, name in c.fetchall():
            item = team_combo.addItem(name)  # add the name
            team_combo.setItemData(team_combo.count() - 1, tid)  # store team_id in UserRole

        conn.close()

        # Restore previous selection if possible
        idx = team_combo.findText(current_text)
        team_combo.setCurrentIndex(idx if idx >= 0 else 0)

    def refresh_team_combos(self):
        """Refresh both Player tab and Game tab team filter combos after adding/editing/deleting a team"""

        # ====================== PLAYER TAB ======================
        if hasattr(self, 'player_team_combo') and self.player_team_combo is not None:
            current_text = self.player_team_combo.currentText()

            self.player_team_combo.clear()
            self.player_team_combo.addItem("All")

            conn = sqlite3.connect(DB_NAME)
            c = conn.cursor()
            c.execute("SELECT name FROM teams ORDER BY name")
            for (name,) in c.fetchall():
                self.player_team_combo.addItem(name)
            conn.close()

            # Restore previous selection
            idx = self.player_team_combo.findText(current_text)
            self.player_team_combo.setCurrentIndex(idx if idx >= 0 else 0)

        # ====================== GAME TAB ======================
        if hasattr(self, 'team_filter_combo') and self.team_filter_combo is not None:
            current_text = self.team_filter_combo.currentText()

            self.team_filter_combo.clear()
            self.team_filter_combo.addItem("All Teams")

            conn = sqlite3.connect(DB_NAME)
            c = conn.cursor()
            c.execute("SELECT id, name FROM teams ORDER BY name")
            for tid, tname in c.fetchall():
                self.team_filter_combo.addItem(tname, tid)  # keep the UserData (id)
            conn.close()

            # Restore previous selection
            idx = self.team_filter_combo.findText(current_text)
            self.team_filter_combo.setCurrentIndex(idx if idx >= 0 else 0)

    def get_default_season(self):
        """Return the stored default season string or None."""
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT value FROM defaults WHERE key = 'default_season'")
        row = c.fetchone()
        conn.close()
        return row[0] if row and row[0] else None

    def on_player_season_changed(self):
        self.update_team_combo_for_season(
            self.player_season_combo,
            self.player_team_combo,
            "All"
        )
        self.load_players()

    def preselect_default_season_filter(self, season_combo, load_callback=None):
        """Pre-select default season on tab load"""
        default = self.get_default_season()
        if not default:
            return

        # Find and select default season
        index = season_combo.findText(default, Qt.MatchExactly)
        if index >= 0:
            season_combo.setCurrentIndex(index)
            # Trigger update
            if hasattr(self, 'on_player_season_changed'):
                self.on_player_season_changed()
            elif load_callback:
                load_callback()

    def show_game_context_menu(self, position):
        """Right-click → context menu on games table rows"""
        index = self.games_table.indexAt(position)
        if not index.isValid():
            return

        row = index.row()
        game_id_item = self.games_table.item(row, 0)
        if not game_id_item:
            return

        game_id = game_id_item.data(Qt.UserRole)
        if not game_id:
            return

        menu = QMenu(self)

        edit_action = menu.addAction("Edit Game")
        toggle_action = menu.addAction("Toggle Complete / Incomplete")
        create_clips_action = menu.addAction("Create Video Clips for this Game...")
        view_gamesheet_action = menu.addAction("View Gamesheet")
        menu.addSeparator()
        edit_events_action = menu.addAction("Edit Video Events / Clips")

        action = menu.exec_(self.games_table.viewport().mapToGlobal(position))

        if action == toggle_action:
            self.toggle_game_status_from_table(row, 0)
        elif action == create_clips_action:
            self.create_game_clips(game_id)
        elif action == edit_events_action:
            self.edit_videos()
        elif action == edit_action:
            self.edit_game()
        elif action == view_gamesheet_action:
            self.view_gamesheet_popup(game_id)  # ← NOW passing game_id here!

    def generate_clips(self, game_id, before_sec, after_sec, event_types=None,
                       team_filter="Both", player_id=None, sort_by_type=False,
                       merge=False, out_dir=Path("clips")):
        """Generate clips with all filters and options"""
        # Sanitize output folder name
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT name FROM games WHERE id = ?", (game_id,))
        game_row = c.fetchone()
        game_name = game_row[0] if game_row else f"game_{game_id}"
        conn.close()

        # Clean game name for folder/filename
        safe_game_name = "".join(c for c in game_name if c.isalnum() or c in " _-").strip().replace(" ", "_")

        # Default folder if none provided
        if not out_dir or str(out_dir) == "clips":
            out_dir = Path("clips") / safe_game_name

        # Check if folder already exists → overwrite prompt
        if out_dir.exists() and any(out_dir.iterdir()):
            reply = QMessageBox.question(
                self,
                "Overwrite Warning",
                f"Output folder '{out_dir}' already contains files.\nOverwrite existing clips?",
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
                QMessageBox.No
            )
            if reply == QMessageBox.Cancel:
                return
            elif reply == QMessageBox.No:
                # User said no → let them choose new folder
                new_dir = QFileDialog.getExistingDirectory(self, "Select New Output Folder", str(out_dir.parent))
                if not new_dir:
                    return
                out_dir = Path(new_dir)

        out_dir.mkdir(parents=True, exist_ok=True)

        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()

        # Get video file (first one for simplicity)
        c.execute("SELECT filename FROM videos WHERE game_id = ? LIMIT 1", (game_id,))
        video_file = c.fetchone()
        if not video_file or not Path(video_file[0]).is_file():
            QMessageBox.critical(self, "Error", "No valid video file found for this game.")
            conn.close()
            return
        video_path = Path(video_file[0])

        # Probe video duration once
        try:
            probe = ffmpeg.probe(str(video_path))
            duration = float(probe['format']['duration'])
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Cannot probe video:\n{str(e)}")
            conn.close()
            return

        # Build event query with filters
        query = """
                SELECT e.id, \
                       e.time_ms, \
                       e.type, \
                       e.quarter, \
                       e.player_id, \
                       p.number, \
                       p.name, \
                       gr.side
                FROM events e
                         JOIN videos v ON e.video_id = v.id
                         LEFT JOIN players p ON e.player_id = p.id
                         LEFT JOIN game_rosters gr ON gr.player_id = p.id AND gr.game_id = v.game_id
                WHERE v.game_id = ? \
                """
        params = [game_id]

        # Event types - exact IN (matches full strings from UI list)
        if event_types:
            placeholders = ','.join('?' for _ in event_types)
            type_filter = f" AND e.type IN ({placeholders})"
            params.extend(event_types)

        if team_filter != "Both":
            side = 'home' if team_filter == "Home Only" else 'guest'
            query += " AND (gr.side = ? OR e.team_side = ?)"
            params.extend([side, side])

        if player_id:
            query += " AND e.player_id = ?"
            params.append(player_id)

        # Sorting
        query += " ORDER BY "
        if sort_by_type:
            query += "e.type, e.time_ms"
        else:
            query += "e.time_ms"

        c.execute(query, params)

        print("Executed SQL:", c._last_executed)
        print("Params:", params)
        events = c.fetchall()
        print("Actual events:", len(events))
        if events:
            print("Event types found:", [e[2] for e in events])

        print("Executed SQL:", c._last_executed)
        print("Params:", params)
        events = c.fetchall()
        print("Actual events:", len(events))
        if events:
            print("Event types found:", [e[2] for e in events])

        events = c.fetchall()
        conn.close()

        if not events:
            QMessageBox.information(self, "No Events", "No matching events found.")
            return

        clips = []
        event_type_set = set()  # for filename

        for ev_id, time_ms, etype, quarter, pid, pnum, pname, side in events:
            t_sec = time_ms / 1000.0
            start = max(0, t_sec - before_sec)
            end = min(duration, t_sec + after_sec)
            clip_dur = end - start

            # NEW: Get Shot Quality letter only (just A, B, C, or D)
            shot_quality_text = ""
            if "2P" in etype or "3P" in etype:  # only for shot events
                conn = sqlite3.connect(DB_NAME)
                c = conn.cursor()
                c.execute("SELECT shot_quality FROM events WHERE id = ?", (ev_id,))
                q_row = c.fetchone()
                conn.close()

                if q_row and q_row[0]:
                    code = q_row[0].strip()
                    if code in ('A', 'B', 'C', 'D'):
                        shot_quality_text = f" ({code})"

            # Smart filename parts
            parts = [safe_game_name]
            if player_id:
                parts.append(f"{pnum or '00'}_{pname or 'Unknown'}")
            elif side:
                prefix = "H" if side == 'home' else "G"
                parts.append(f"{prefix}_{pnum or '00'}_{pname or 'Unknown'}")
            parts.append(etype.replace(" ", "_"))
            parts.append(f"{quarter}_{t_sec:06.1f}s")

            filename = out_dir / "_".join(parts) + ".mp4"
            clips.append((filename, start, clip_dur, etype, quarter, pnum, pname))

            event_type_set.add(etype)

            # Create individual clip
            try:
                stream = ffmpeg.input(str(video_path), ss=start, t=clip_dur)
                stream = ffmpeg.output(stream, str(filename), c='copy', loglevel="quiet")
                ffmpeg.run(stream)
            except Exception as e:
                print(f"Clip creation failed for {filename}: {e}")

        # Merge if requested
        if merge and clips:
            # Sort clips if by type
            if sort_by_type:
                clips.sort(key=lambda x: (x[3], x[1]))  # type, then time

            merged_path = out_dir / f"merged_{safe_game_name}_{datetime.now().strftime('%Y%m%d_%H%M')}.mp4"
            concat_file = out_dir / "concat_list.txt"

            with open(concat_file, "w", encoding="utf-8") as f:
                for clip_file, _, _, _, _, _, _ in clips:
                    f.write(f"file '{clip_file.absolute()}'\n")

            try:
                progress = QProgressDialog("Merging clips...", None, 0, 0, self)
                progress.setWindowTitle("Merging Clips")
                progress.setMinimumDuration(0)
                progress.setCancelButton(None)
                progress.show()
                QApplication.processEvents()

                stream = ffmpeg.input(str(concat_file), format='concat', safe=0)
                stream = ffmpeg.output(stream, str(merged_path), c='copy', y=None, loglevel='error')
                ffmpeg.run(stream)

                progress.close()
                QApplication.processEvents()

                QMessageBox.information(
                    self,
                    "Merge Complete",
                    f"Merged clip created:\n{merged_path.resolve()}\n\nTotal clips merged: {len(clips)}"
                )

            except ffmpeg.Error as e:
                progress.close()
                QMessageBox.critical(self, "Merge Failed",
                                     f"ffmpeg error:\n{e.stderr.decode('utf-8', errors='replace') or str(e)}")
            except Exception as e:
                progress.close()
                QMessageBox.critical(self, "Merge Failed", str(e))
            finally:
                if concat_file.exists():
                    concat_file.unlink()

        # Final success
        QMessageBox.information(
            self,
            "Clips Created",
            f"{len(clips)} clip(s) saved to:\n{out_dir.resolve()}\n\n"
            f"Merged file: {'Yes' if merge else 'No'}"
        )
        # After merge or individual clips success
        #print("DEBUG: Type of out_dir →", type(out_dir))
        #print("DEBUG: out_dir value   →", out_dir)
        #print("DEBUG: Exists?         →", out_dir.exists() if hasattr(out_dir, 'exists') else os.path.isdir(out_dir))
        #print("DEBUG: Is dir?         →", os.path.isdir(str(out_dir)))
        #print("DEBUG: Absolute path   →", os.path.abspath(str(out_dir)))

        open_folder(out_dir)

    def create_game_clips_selected(self):
        """Called when 'Create Video Clips' button is clicked — gets selected game and opens dialog"""
        selected = self.games_table.selectedItems()
        if not selected:
            QMessageBox.warning(self, "No Selection", "Please select a game row first.")
            return

        row = selected[0].row()
        game_id_item = self.games_table.item(row, 0)  # Date column (where game_id is stored in UserRole)
        if not game_id_item:
            QMessageBox.warning(self, "Error", "No game ID found for selected row.")
            return

        game_id = game_id_item.data(Qt.UserRole)
        if not game_id:
            QMessageBox.warning(self, "Error", "Team ID not found.")
            return

        #print("Create Clips button clicked - game_id:", game_id)
        self.create_game_clips(game_id)

    def create_game_clips(self, game_id):
        dialog = QDialog(self)
        dialog.setWindowTitle("Create Clips for Game")
        dialog.setFixedWidth(560)  # slightly wider to fit new filter

        lay = QVBoxLayout(dialog)
        lay.setSpacing(12)
        lay.setContentsMargins(16, 16, 16, 16)

        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT name FROM games WHERE id = ?", (game_id,))
        game_row = c.fetchone()
        game_name = game_row[0] if game_row else "Unknown"

        safe_game_name = "".join(c for c in game_name if c.isalnum() or c in " _-").strip().replace(" ", "_")

        lay.addWidget(QLabel(f"<b>Game:</b> {game_name}"))

        # ── Time before / after ─────────────────────────────────────────────
        time_lay = QHBoxLayout()
        time_lay.addWidget(QLabel("Seconds before event:"))
        before_spin = QSpinBox()
        before_spin.setValue(3)
        before_spin.setRange(0, 30)
        time_lay.addWidget(before_spin)
        time_lay.addWidget(QLabel("Seconds after event:"))
        after_spin = QSpinBox()
        after_spin.setValue(2)
        after_spin.setRange(0, 30)
        time_lay.addWidget(after_spin)
        lay.addLayout(time_lay)

        # ── Team & Player filters (unchanged) ───────────────────────────────
        team_lay = QHBoxLayout()
        team_lay.addWidget(QLabel("Team:"))
        team_combo = QComboBox()
        team_combo.addItems(["Both", "Home Only", "Guest Only"])
        team_lay.addWidget(team_combo)
        lay.addLayout(team_lay)

        player_lay = QHBoxLayout()
        player_lay.addWidget(QLabel("Player:"))
        player_combo = QComboBox()
        player_combo.addItem("All Players")

        c.execute("""
            SELECT p.id, p.number, p.name, gr.side
            FROM game_rosters gr
            JOIN players p ON gr.player_id = p.id
            WHERE gr.game_id = ?
            ORDER BY gr.side, CAST(p.number AS INTEGER)
        """, (game_id,))
        for pid, num, pname, side in c.fetchall():
            prefix = "H" if side == 'home' else "G"
            display = f"{prefix} #{str(num or '??'):>2} - {pname or 'Unknown'}"
            player_combo.addItem(display, pid)
        player_lay.addWidget(player_combo)
        lay.addLayout(player_lay)

        # ── Event types (unchanged) ─────────────────────────────────────────
        lay.addWidget(QLabel("Event types to include (leave all unchecked for all):"))
        type_list = QListWidget()
        type_list.setSelectionMode(QAbstractItemView.MultiSelection)

        EVENT_TYPES = [
            "2PA 2PT Shot Attempt", "2PM 2PT Shot Made",
            "3PA 3PT Shot Attempt", "3PM 3PT Shot Made",
            "FTA Freethrow Attempt", "FTM Freethrow Made",
            "AST Assist", "BLK Blocked Shot", "CHG Charge Drawn",
            "DRB Defensive Rebound", "ORB Offensive Rebound",
            "PFL Personal Foul", "STL Steal", "TOV Turnover"
        ]

        for et in EVENT_TYPES:
            item = QListWidgetItem(et)
            item.setData(Qt.UserRole, et)
            type_list.addItem(item)
        lay.addWidget(type_list)

        # ── Highlights + NEW Shot Quality Filter ────────────────────────────
        filter_lay = QVBoxLayout()
        filter_lay.setSpacing(8)

        # ── Shot Quality Filter ─────────────────────────────────────────────
        quality_lay = QHBoxLayout()
        quality_lay.addWidget(QLabel("Shot Quality:"))
        quality_combo = QComboBox()

        # Build options from the SINGLE SOURCE OF TRUTH
        quality_combo.addItem("All Qualities", None)  # default = all
        for full_name, code in SHOT_QUALITY:
            display_name = full_name.split('/', 1)[0].strip()  # "A - Elite"
            quality_combo.addItem(display_name, code)

        quality_combo.addItem("Not Assigned", "None")
        quality_lay.addWidget(quality_combo)
        filter_lay.addLayout(quality_lay)

        highlights_only_cb = QCheckBox("Show Highlight Events only")
        highlights_only_cb.setChecked(False)
        highlights_only_cb.setStyleSheet("font-weight: bold;")
        filter_lay.addWidget(highlights_only_cb)

        lay.addLayout(filter_lay)

        # ── Merge & Sort options (unchanged) ────────────────────────────────
        merge_lay = QVBoxLayout()
        merge_lay.setSpacing(8)
        merge_cb = QCheckBox("Merge all clips into one video")
        merge_lay.addWidget(merge_cb)
        label_cb = QCheckBox("Add text labels to clips (player, event, quarter/time)")
        label_cb.setChecked(True)
        merge_lay.addWidget(label_cb)

        sort_lay = QHBoxLayout()
        sort_lay.addWidget(QLabel("Sort order:"))
        sort_group = QButtonGroup()
        timeline_rb = QRadioButton("By video timeline only")
        type_rb = QRadioButton("By event type → timeline")
        timeline_rb.setChecked(True)
        sort_group.addButton(timeline_rb)
        sort_group.addButton(type_rb)
        sort_lay.addWidget(timeline_rb)
        sort_lay.addWidget(type_rb)
        merge_lay.addLayout(sort_lay)

        lay.addLayout(merge_lay)

        # ── Output folder ───────────────────────────────────────────────────
        out_lay = QHBoxLayout()
        default_out = str(Path.home() / "Documents" / "CourtTag" / "Clips" / safe_game_name)
        out_edit = QLineEdit(default_out)
        out_lay.addWidget(QLabel("Output folder:"))
        out_lay.addWidget(out_edit)
        browse_btn = QPushButton("Browse...")
        browse_btn.clicked.connect(lambda: self.browse_output_folder(out_edit))
        out_lay.addWidget(browse_btn)
        lay.addLayout(out_lay)

        # Buttons
        btn_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btn_box.accepted.connect(dialog.accept)
        btn_box.rejected.connect(dialog.reject)
        lay.addWidget(btn_box)

        if dialog.exec_() == QDialog.Accepted:
            before_sec = before_spin.value()
            after_sec = after_spin.value()
            selected_types = [item.data(Qt.UserRole) for item in type_list.selectedItems()] or None
            team_filter = team_combo.currentText()
            player_id = player_combo.currentData() if player_combo.currentIndex() > 0 else None
            sort_by_type = type_rb.isChecked()
            merge = merge_cb.isChecked()
            add_labels = label_cb.isChecked()
            highlights_only = highlights_only_cb.isChecked()

            # NEW: Get selected shot quality
            selected_quality = quality_combo.currentData()  # None = All, or "A", "B", ..., "None"

            preview_count = self.preview_clip_count(
                game_id, selected_types, team_filter, player_id,
                highlights_only, selected_quality
            )

            reply = QMessageBox.question(
                self, "Confirm",
                f"About to generate {preview_count} clip(s).\nProceed?",
                QMessageBox.Yes | QMessageBox.No
            )

            if reply == QMessageBox.Yes:
                self.generate_clips(
                    game_id, before_sec, after_sec, selected_types,
                    team_filter, player_id, sort_by_type, merge,
                    Path(out_edit.text()), add_labels=add_labels,
                    highlights_only=highlights_only,
                    shot_quality=selected_quality  # ← new parameter
                )

        conn.close()

    def preview_clip_count(self, game_id, event_types=None, team_filter="Both",
                           player_id=None, highlights_only=False, shot_quality=None):
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()

        query = """
            SELECT COUNT(e.id)
            FROM events e
            JOIN videos v ON e.video_id = v.id
            WHERE v.game_id = ?
        """
        params = [game_id]

        if event_types:
            placeholders = ','.join('?' for _ in event_types)
            query += f" AND e.type IN ({placeholders})"
            params.extend(event_types)

        if team_filter != "Both":
            side = 'home' if team_filter == "Home Only" else 'guest'
            query += " AND (e.team_side = ? OR e.player_id IN (SELECT player_id FROM game_rosters WHERE game_id = ? AND side = ?))"
            params.extend([side, game_id, side])

        if player_id:
            query += " AND e.player_id = ?"
            params.append(player_id)

        if highlights_only:
            query += " AND e.is_highlight = 1"

        # NEW: Shot Quality filter
        if shot_quality is not None:
            if shot_quality == "None":
                query += " AND (e.shot_quality IS NULL OR e.shot_quality = '')"
            else:
                query += " AND e.shot_quality = ?"
                params.append(shot_quality)

        c.execute(query, params)
        count = c.fetchone()[0]
        conn.close()
        return count

    def browse_output_folder(self, line_edit):
        folder = QFileDialog.getExistingDirectory(self, "Select Output Folder")
        if folder:
            line_edit.setText(folder)

    def generate_clips(self, game_id, before_sec, after_sec, event_types=None,
                       team_filter="Both", player_id=None, sort_by_type=False,
                       merge=False, out_dir=Path("clips"),
                       add_labels=False, highlights_only=False,
                       shot_quality=None):
        #Generate clips with all filters and options
        # Sanitize game name for folder/filename
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT name FROM games WHERE id = ?", (game_id,))
        game_row = c.fetchone()
        game_name = game_row[0] if game_row else f"game_{game_id}"
        conn.close()

        safe_game_name = "".join(c for c in game_name if c.isalnum() or c in " _-").strip().replace(" ", "_")

        # Default output folder
        if not out_dir or str(out_dir) == "clips":
            out_dir = Path("clips") / safe_game_name

        # Overwrite prompt only if there are existing .mp4 files
        #if out_dir.exists() and any(f.suffix.lower() == '.mp4' for f in out_dir.iterdir()):
        #    reply = QMessageBox.question(
        #        self,
        #         "Existing Video Clips Found",
        #         f"The folder '{out_dir.name}' already contains .mp4 files.\n\n"
        #         "Add new clips anyway?\n"
        #         "(New clips use unique timestamps and won't overwrite existing ones.)",
        #         QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
        #         QMessageBox.No
        #     )
        #     if reply == QMessageBox.Cancel:
        #         return
        #     elif reply == QMessageBox.No:
        #         # new_dir = QFileDialog.getExistingDirectory(
        #             self, "Choose Different Output Folder", str(out_dir.parent)
        #         )
        #         if not new_dir:
        #             return
        #         out_dir = Path(new_dir)

        out_dir.mkdir(parents=True, exist_ok=True)

        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()

        # Get video file (first one for simplicity)
        c.execute("SELECT filename FROM videos WHERE game_id = ? LIMIT 1", (game_id,))
        video_file = c.fetchone()
        if not video_file or not Path(video_file[0]).is_file():
            QMessageBox.critical(self, "Error", "No valid video file found for this game.")
            conn.close()
            return
        video_path = Path(video_file[0])

        # Probe video duration
        try:
            probe = ffmpeg.probe(str(video_path))
            duration = float(probe['format']['duration'])
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Cannot probe video:\n{str(e)}")
            conn.close()
            return

        # Build event query with filters
        query = """
            SELECT e.id, e.time_ms, e.type, e.quarter, e.player_id, p.number, p.name, gr.side
            FROM events e
                     JOIN videos v ON e.video_id = v.id
                     LEFT JOIN players p ON e.player_id = p.id
                     LEFT JOIN game_rosters gr ON gr.player_id = p.id AND gr.game_id = v.game_id
            WHERE v.game_id = ?
        """
        params = [game_id]

        # Event types - exact IN (matches full strings from UI list)
        if event_types:
            placeholders = ','.join('?' for _ in event_types)
            query += f" AND e.type IN ({placeholders})"
            params.extend(event_types)

        if team_filter != "Both":
            side = 'home' if team_filter == "Home Only" else 'guest'
            query += " AND (gr.side = ? OR e.team_side = ?)"
            params.extend([side, side])

        if player_id:
            query += " AND e.player_id = ?"
            params.append(player_id)

        # ── NEW: Highlights only ────────────────────────────────────────────────
        if highlights_only:
            query += " AND e.is_highlight = 1"

        # In the event query, add this block before the ORDER BY
        if shot_quality is not None:
            if shot_quality == "None":
                query += " AND (e.shot_quality IS NULL OR e.shot_quality = '')"
            else:
                query += " AND e.shot_quality = ?"
                params.append(shot_quality)

        # Sorting
        query += " ORDER BY "
        if sort_by_type:
            query += "e.type, e.time_ms"
        else:
            query += "e.time_ms"

        #print("Clip creation debug:")
        #print("  game_id:", game_id)
        #print("  event_types:", event_types or "All")
        #print("  query params:", params)

        c.execute(query, params)

        events = c.fetchall()
        #print("Raw query returned", len(events), "events")
        if events:
            pass #print("First few event types:", [e[2] for e in events[:5]])
        else:
            print("Zero events — running diagnostic queries...")
            c.execute("SELECT COUNT(*) FROM events e JOIN videos v ON e.video_id = v.id WHERE v.game_id = ?", (game_id,))
            total_any = c.fetchone()[0]
            print("Total events (no filters):", total_any)

            c.execute("SELECT DISTINCT type FROM events e JOIN videos v ON e.video_id = v.id WHERE v.game_id = ?", (game_id,))
            types = [r[0] for r in c.fetchall()]
            print("All distinct event types in game:", types)

            c.execute("SELECT COUNT(*) FROM events e JOIN videos v ON e.video_id = v.id WHERE v.game_id = ? AND e.type LIKE '2PM%'", (game_id,))
            ftm_count = c.fetchone()[0]
            print("Events starting with '2PM':", ftm_count)

        conn.close()

        if not events:
            QMessageBox.information(self, "No Events", "No matching events found for this game.")
            return

        # Prepare ffmpeg input
        clips = []
        for ev_id, time_ms, etype, quarter, pid, pnum, pname, side in events:
            t_sec = time_ms / 1000.0
            start = max(0, t_sec - before_sec)
            end = min(duration, t_sec + after_sec)
            clip_dur = end - start

            # Get Shot Quality letter (A, B, C, or D) if it exists
            shot_quality_text = ""
            if "2P" in etype or "3P" in etype:  # only for shot events
                conn_temp = sqlite3.connect(DB_NAME)
                c_temp = conn_temp.cursor()
                c_temp.execute("SELECT shot_quality FROM events WHERE id = ?", (ev_id,))
                q_row = c_temp.fetchone()
                conn_temp.close()

                if q_row and q_row[0]:
                    code = q_row[0].strip()
                    if code in ('A', 'B', 'C', 'D'):
                        shot_quality_text = f" ({code})"

            # Smart filename (unchanged)
            parts = [safe_game_name]
            if player_id:
                parts.append(f"{pnum or '00'}_{pname or 'Unknown'}")
            elif side:
                prefix = "H" if side == 'home' else "G"
                parts.append(f"{prefix}_{pnum or '00'}_{pname or 'Unknown'}")
            parts.append(etype.replace(" ", "_"))
            parts.append(f"{quarter}_{t_sec:06.1f}s")

            filename = out_dir / f"{'_'.join(parts)}.mp4"
            clips.append((filename, start, clip_dur, etype, quarter, pnum, pname))

            try:
                trimmed = ffmpeg.input(str(video_path), ss=start, t=clip_dur)
                video_stream = trimmed.video

                if add_labels:
                    player_label = f"{'H' if side == 'home' else 'G'} #{pnum or '??'} - {pname or 'Unknown'}"
                    minutes = int(t_sec // 60)
                    seconds = int(t_sec % 60)
                    time_str = f"{minutes:02d}:{seconds:02d}"

                    event_part = etype.split()[0]  # e.g. "2PM"

                    # Updated label with just the letter (A), (B), etc.
                    full_text = f"{player_label} - {event_part}{shot_quality_text} - {quarter} - {time_str}"

                    video_stream = video_stream.filter(
                        'drawtext',
                        fontfile=FONT_PATH.replace('\\', '/'),
                        text=full_text,
                        fontcolor='white',
                        fontsize=42,
                        box=1,
                        boxcolor='black@0.65',
                        boxborderw=10,
                        x='(w-text_w)/2',
                        y='h-140',
                    )

                stream = ffmpeg.output(
                    video_stream,
                    trimmed.audio,
                    str(filename),
                    vcodec='libx264',
                    preset='veryfast',
                    crf=23,
                    acodec='copy',
                    loglevel='quiet',
                    y=None
                )

                ffmpeg.run(stream)

            except ffmpeg.Error as e:
                print(f"ffmpeg error creating {filename}:")
                if e.stderr:
                    print(e.stderr.decode('utf-8', errors='replace'))
                else:
                    print(str(e))
            except Exception as e:
                print(f"Failed to create clip {filename}: {e}")

        # Merge if requested
        if merge and clips:
            # Always use fresh timestamp for merged file (prevents overwrite)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

            # Optional context prefix
            context_parts = [safe_game_name]

            # Add player or team context if applicable
            if player_id and pnum and pname:
                prefix = "H" if side == 'home' else "G"
                context_parts.append(f"{prefix}#{pnum or '??'}_{pname or 'Unknown'}")
            elif team_filter != "Both":
                context_parts.append(team_filter.replace(" ", "").lower())  # HomeOnly, GuestOnly, etc.

            # Build filename
            filename_base = "_".join(context_parts)
            merged_path = out_dir / f"merged_{filename_base}_{timestamp}.mp4"

            concat_file = out_dir / "concat_list.txt"

            with open(concat_file, "w", encoding="utf-8") as f:
                for clip_file, _, _, _, _, _, _ in clips:
                    f.write(f"file '{clip_file.absolute()}'\n")

            merge_success = False
            merged_message = ""

            try:
                progress = QProgressDialog("Merging clips...", None, 0, 0, self)
                progress.setWindowTitle("Merging Clips")
                progress.setMinimumDuration(0)
                progress.setCancelButton(None)
                progress.show()
                QApplication.processEvents()

                stream = ffmpeg.input(str(concat_file), format='concat', safe=0)
                stream = ffmpeg.output(stream, str(merged_path), c='copy', y=None, loglevel='error')
                ffmpeg.run(stream)

                # Basic validation that merge produced something usable
                if merged_path.exists() and merged_path.stat().st_size > 50000:  # > ~50 KB
                    merge_success = True
                    merged_message = (
                        f"Merged clip created:\n{merged_path.resolve()}\n\n"
                        f"Total clips merged: {len(clips)}\n\n"
                        f"Deleted {len(clips)} individual clips (moved to Recycle Bin/Trash)."
                    )
                else:
                    merged_message = "Merge ran but output file looks invalid (too small or missing)."

            except ffmpeg.Error as e:
                merged_message = f"ffmpeg error:\n{e.stderr.decode('utf-8', errors='replace') or str(e)}"
            except Exception as e:
                merged_message = str(e)
            finally:
                progress.close()
                QApplication.processEvents()

                if concat_file.exists():
                    concat_file.unlink()

                # ── DELETE INDIVIDUAL CLIPS ONLY IF MERGE SUCCEEDED ────────────────
                if merge_success:
                    deleted_count = 0
                    for clip_file, _, _, _, _, _, _ in clips:
                        if clip_file.exists():
                            try:
                                send2trash(str(clip_file))
                                deleted_count += 1
                                #print(f"Moved to trash: {clip_file.name}")
                            except Exception as del_err:
                                print(f"Could not move {clip_file.name} to trash: {del_err}")

                    #print("DEBUG: Trash-moving loop COMPLETED successfully")
                    #print("DEBUG: Now about to call open_folder()")
                    #print(f"DEBUG: out_dir = {out_dir!r} (type: {type(out_dir)})")
                    #print(f"DEBUG: Folder exists? {os.path.isdir(str(out_dir))}")

                    if deleted_count > 0:
                        merged_message += f"\n\nCleaned up: {deleted_count} clip(s) moved to Recycle Bin/Trash."
                    else:
                        merged_message += "\n\nNo individual clips were deleted (already missing?)."

                # Show result (success or failure)
                if merge_success:
                    QMessageBox.information(self, "Merge Complete", merged_message)
                else:
                    QMessageBox.warning(self, "Merge Issue", merged_message or "Merge did not complete successfully.")

        # Final success message (always shown, reflects what actually happened)
        msg = f"{len(clips)} individual clip(s) processed.\nSaved to:\n{out_dir.resolve()}"
        if merge:
            msg += f"\n\nMerged file: {'Yes' if merge_success else 'No (see previous message)'}"
        else:
            msg += "\n\nMerge: No"

        #QMessageBox.information(self, "Clips Created", msg)
        if os.path.isdir(out_dir):
            os.startfile(str(out_dir))  # should pop Explorer right to D:\Documents

    def toggle_game_status_from_table(self, row, column):
        """Double-click or context menu: flip is_complete flag"""
        game_id_item = self.games_table.item(row, 0)
        if not game_id_item:
            return

        game_id = game_id_item.data(Qt.UserRole)
        if not game_id:
            return

        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT is_complete FROM games WHERE id = ?", (game_id,))
        result = c.fetchone()
        if not result:
            conn.close()
            return

        current = result[0]
        new_value = 0 if current == 1 else 1

        c.execute("UPDATE games SET is_complete = ? WHERE id = ?", (new_value, game_id))
        conn.commit()
        conn.close()

        # Refresh table to show updated status/color
        self.load_games()

    def reassign_events_on_player_removal(self, game_id: int, player_id: int):
        """
        Reassign events when a player is removed from a game roster or deleted.
        - Sets player_id = NULL
        - Forces team_side to the player's roster side for this game
        - Returns number of events reassigned, or negative on cancel/error
        """
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()

        try:
            # Count events
            c.execute("""
                SELECT COUNT(*) 
                FROM events e
                JOIN videos v ON e.video_id = v.id
                WHERE v.game_id = ? AND e.player_id = ?
            """, (game_id, player_id))
            count = c.fetchone()[0]

            if count == 0:
                return 0

            # Get player's side from roster (required for reassignment)
            c.execute("""
                SELECT side FROM game_rosters 
                WHERE game_id = ? AND player_id = ?
            """, (game_id, player_id))
            roster_row = c.fetchone()
            if not roster_row:
                QMessageBox.warning(self, "No Roster Entry", "Player not found on roster — cannot reassign team_side.")
                return -3

            player_side = roster_row[0]  # 'home' or 'guest'

            reply = QMessageBox.question(
                self,
                "Reassign Events?",
                f"This player has {count} event(s) in this game (roster side: {player_side}).\n\n"
                "Removing will:\n"
                " • Set player_id = NULL\n"
                " • Set team_side = '{player_side}' (team-level event)\n"
                "Points preserved, personal attribution removed.\n\n"
                "Continue?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )

            if reply == QMessageBox.No:
                return -1

            # Reassign: force team_side to roster side
            c.execute("""
                UPDATE events 
                SET player_id = NULL,
                    team_side = ?
                WHERE player_id = ? 
                  AND video_id IN (SELECT id FROM videos WHERE game_id = ?)
            """, (player_side, player_id, game_id))

            conn.commit()
            print(f"Reassigned {count} events in game {game_id} to team-level (side: {player_side})")

            return count

        except Exception as e:
            conn.rollback()
            QMessageBox.critical(self, "Error", f"Failed to reassign:\n{str(e)}")
            return -2
        finally:
            conn.close()

    def show_about_dialog(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("About CourtTag")
        dialog.setMinimumSize(500, 450)  # give enough vertical space for image + text

        layout = QVBoxLayout()
        layout.setSpacing(16)
        layout.setContentsMargins(20, 20, 20, 20)

        # ── Splash image (same one as loading screen) ───────────────────────────
        try:
            #pixmap = QPixmap("CourtTag_Load_Screen.png")
            pixmap = QPixmap(":/CourtTag_Load_Screen_v1.png")
            if not pixmap.isNull():
                image_label = QLabel()
                # Scale it nicely — adjust 420×320 to match your image's natural proportions
                scaled = pixmap.scaled(
                    480, 256,
                    Qt.KeepAspectRatio,
                    Qt.SmoothTransformation
                )
                image_label.setPixmap(scaled)
                image_label.setAlignment(Qt.AlignCenter)
                # Optional: light border / shadow for polish
                #image_label.setStyleSheet("border: 1px solid #cccccc; border-radius: 6px;")
                layout.addWidget(image_label)
            else:
                # Fallback when image exists but is invalid/empty
                fallback = QLabel("CourtTag")
                fallback.setAlignment(Qt.AlignCenter)
                fallback.setStyleSheet("font-size: 28px; font-weight: bold; color: #555;")
                layout.addWidget(fallback)
        except Exception as e:
            # Fallback if file missing, permissions issue, etc.
            print(f"About image could not be loaded: {e}")  # console log only
            fallback = QLabel("CourtTag (splash unavailable)")
            fallback.setAlignment(Qt.AlignCenter)
            fallback.setStyleSheet("font-size: 20px; color: #777;")
            layout.addWidget(fallback)

        # Add a little breathing room
        layout.addSpacing(10)

        # ── Text content ────────────────────────────────────────────────────────
        text_label = QLabel(
            f"""<h3>CourtTag v{VERSION}{BUILD}</h3>
            <p>Basketball video tagging and stat tracker for coaches.</p>
            <p>Tag events frame-by-frame from game film,<br>
               generate team & player reports, calculate PPP, shot locations, and more.</p>
            <h4>CourtTag.net</h4>
            <p><b>Created by:</b> Ian Gruber (@ballcoachiggy)</p>
            <p>© {QDate.currentDate().year()} – All rights reserved.</p>
            <p>Built with PyQt5, python-vlc, SQLite</p>"""
        )
        text_label.setWordWrap(True)
        text_label.setAlignment(Qt.AlignCenter)
        text_label.setStyleSheet("font-size: 14px; line-height: 1.4;")
        layout.addWidget(text_label)

        # ── Close button ────────────────────────────────────────────────────────
        layout.addStretch()  # pushes button to bottom

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        close_btn = QPushButton("Close")
        close_btn.setFixedWidth(120)
        close_btn.clicked.connect(dialog.accept)
        btn_layout.addWidget(close_btn)
        layout.addLayout(btn_layout)

        dialog.setLayout(layout)
        dialog.exec_()

    def setup_games_tab(self):
        layout = QVBoxLayout(self.games_tab)

        # ── New: Season Filter Row ─────────────────────────────────────────────
        filter_layout = QHBoxLayout()
        filter_layout.addWidget(QLabel("Season:"))

        self.game_season_combo = QComboBox()
        self.game_season_combo.addItem("All")
        # Populate seasons
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT DISTINCT season FROM teams WHERE season IS NOT NULL AND season != '' ORDER BY season DESC")
        for (season,) in c.fetchall():
            self.game_season_combo.addItem(season)
        conn.close()
        filter_layout.addWidget(self.game_season_combo)

        # Connect signal
        self.game_season_combo.currentIndexChanged.connect(self.load_games)

        # ←←← Safe pre-selection for Games tab
        QTimer.singleShot(0, lambda: self.preselect_default_season_filter(
            self.game_season_combo, self.load_games))

        # Team filter combo (next to Season)
        team_filter_label = QLabel("Team:")
        team_filter_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        # Assuming you have a layout for filters, e.g. filter_lay.addWidget(team_filter_label)

        self.team_filter_combo = QComboBox()
        self.team_filter_combo.addItem("All Teams")
        # Populate with all teams (do this once, e.g. in init or setup)
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT id, name FROM teams ORDER BY name")
        for tid, tname in c.fetchall():
            self.team_filter_combo.addItem(tname, tid)  # store team_id in UserData
        conn.close()

        # Add to layout (adjust to your UI)
        filter_layout.addWidget(team_filter_label)
        filter_layout.addWidget(self.team_filter_combo)

        self.team_filter_combo.currentIndexChanged.connect(self.load_games)


        filter_layout.addStretch()

        layout.addLayout(filter_layout)

        # Table setup
        self.games_table = QTableWidget()
        self.games_table.setAlternatingRowColors(True)
        self.games_table.setStyleSheet("""
                            QTableWidget {
                                alternate-background-color: #DCFFDC ;
                                background-color: white;
                            }
                        """)

        self.games_table.setColumnCount(10  )
        self.games_table.setHorizontalHeaderLabels([
            "Date",
            "Game Name",
            "Location",
            "Home Team",
            "Guest Team",
            "Home Score",
            "Guest Score",
            "Event Tagging",
            "GS",
            "YT"
        ])
        self.games_table.setCursor(Qt.PointingHandCursor)

        # Status column styling
        self.games_table.setSortingEnabled(True)
        self.games_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.games_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.games_table.setAlternatingRowColors(True)  # Easier to read

        header = self.games_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Stretch)

        # But force these columns to fixed size (they won't stretch)
        header.setSectionResizeMode(5, QHeaderView.Fixed)  # Home Score
        header.setSectionResizeMode(6, QHeaderView.Fixed)  # Guest Score
        header.setSectionResizeMode(7, QHeaderView.Fixed)  # Status / Event Tagging
        header.setSectionResizeMode(8, QHeaderView.Fixed)  # GS column
        header.setSectionResizeMode(9, QHeaderView.Fixed)  # YT column

        # Now set the actual widths (these will be respected)
        self.games_table.setColumnWidth(2, 90)  # Home Score
        self.games_table.setColumnWidth(5, 90)  # Home Score
        self.games_table.setColumnWidth(6, 90)  # Guest Score
        self.games_table.setColumnWidth(7, 110)  # Status text like "Complete ✓"
        self.games_table.setColumnWidth(8, 48)  # GS emoji column
        self.games_table.setColumnWidth(9, 42)  # YT emoji column
        header.setSectionsClickable(True)

        # Enable right-click context menu
        self.games_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.games_table.customContextMenuRequested.connect(self.show_game_context_menu)


        # Enable double-click to toggle status
        #self.games_table.cellDoubleClicked.connect(self.toggle_game_status_from_table)
        # Changed to generate game report on double click
        self.games_table.cellDoubleClicked.connect(self.on_game_double_click)
        #self.games_table.cellClicked.connect(self.on_gamesheet_cell_clicked)
        self.games_table.cellClicked.connect(self.on_game_table_clicked)

        layout.addWidget(self.games_table)

        # Buttons row
        btn_layout = QHBoxLayout()
        add_btn = QPushButton("Add Game")
        add_btn.clicked.connect(self.add_game)
        btn_layout.addWidget(add_btn)

        edit_btn = QPushButton("Edit Game")
        edit_btn.clicked.connect(self.edit_game)
        btn_layout.addWidget(edit_btn)

        #gamesheet_btn = QPushButton("View Gamesheet")
        #gamesheet_btn.clicked.connect(self.view_selected_gamesheet)
        #btn_layout.addWidget(gamesheet_btn)

        delete_btn = QPushButton("Delete Game + Events")
        delete_btn.clicked.connect(self.delete_game)
        btn_layout.addWidget(delete_btn)

        videos_btn = QPushButton("Game Video Tagging")
        videos_btn.setStyleSheet("""
            QPushButton {
                background-color: #DCFFDC;      /* your light green */
                border: 1px solid #000000;      /* slightly darker green border */
                border-radius: 6px;
                padding: 6px 16px;
            }
            QPushButton:hover {
                background-color: #BDFCBD;      /* darker green on hover */
            }
        """)
        videos_btn.clicked.connect(self.edit_videos)
        btn_layout.addWidget(videos_btn)

        # NEW: Create Video Clips button
        clips_btn = QPushButton("Create Video Clips")
        clips_btn.clicked.connect(self.create_game_clips_selected)
        btn_layout.addWidget(clips_btn)

        report_btn = QPushButton("View Game Report")
        report_btn.clicked.connect(self.view_game_report)
        btn_layout.addWidget(report_btn)

        layout.addLayout(btn_layout)

        # Connect filter change to reload
        self.game_season_combo.currentIndexChanged.connect(self.load_games)

        # Load seasons into combo (call a new helper)
        self.populate_game_season_combo()

        # Load initial data
        self.load_games()

        # === FILTER CONNECTIONS (at the very end of setup_games_tab) ===
        self.game_season_combo.currentIndexChanged.connect(self.on_game_season_changed)
        self.team_filter_combo.currentIndexChanged.connect(self.load_games)

        # Pre-select default season
        QTimer.singleShot(0, lambda: self.preselect_default_season_filter(
            self.game_season_combo, self.load_games))

    def view_selected_gamesheet(self):
        """Open viewer for the currently selected game in table"""
        selected_rows = self.games_table.selectionModel().selectedRows()
        if not selected_rows:
            QMessageBox.information(self, "No Selection", "Select a game first.")
            return

        # Get game_id from first selected row (or loop if multi-select allowed)
        row = selected_rows[0].row()
        game_id_item = self.games_table.item(row, 0)
        game_id = game_id_item.data(Qt.UserRole) if game_id_item else None

        if not game_id:
            QMessageBox.warning(self, "Error", "Cannot find game ID.")
            return

        self.view_gamesheet_popup(game_id)

    def on_game_table_clicked(self, row: int, column: int):
        """Handle clicks on Gamesheet (col 8) and YouTube (col 9)"""
        if row < 0:
            return

        # Get game_id from ANY column in this row (more reliable)
        game_id = None
        for col in range(self.games_table.columnCount()):
            item = self.games_table.item(row, col)
            if item and item.data(Qt.UserRole):
                game_id = item.data(Qt.UserRole)
                break

        if not game_id:
            QMessageBox.warning(self, "Error", "Could not find game ID.")
            return

        # ====================== YOUTUBE COLUMN ======================
        if column == 9:
            conn = sqlite3.connect(DB_NAME)
            c = conn.cursor()
            c.execute("SELECT youtube_url FROM games WHERE id = ?", (game_id,))
            result = c.fetchone()
            conn.close()

            if result and result[0]:
                QDesktopServices.openUrl(QUrl(result[0]))
            #else:
            #    QMessageBox.information(self, "No YouTube Link",
            #                          "No YouTube URL has been saved for this game.")

        # ====================== GAMESHEET COLUMN ======================
        elif column == 8:
            sheet_item = self.games_table.item(row, 8)
            if sheet_item and sheet_item.text() in ("📸", "📋", "🖼️"):
                self.view_gamesheet_popup(game_id)
            else:
                # Optional: show message if no gamesheet
                pass

    def open_team_report(self, team_id):
        if not team_id:
            QMessageBox.warning(self, "Error", "No team selected.")
            return

        report = self.generate_team_report(team_id)
        if not report or "<h2>Team not found." in report:
            QMessageBox.warning(self, "Error", "Could not generate report for this team.")
            return

        dialog = QDialog(self)
        dialog.setWindowTitle("Team Report")
        dialog.setGeometry(150, 150, 1280, 800)  # slightly taller for long reports

        # This is the key block: Customize + pick buttons explicitly
        dialog.setWindowFlags(
            Qt.Dialog |  # Keep modal/dialog behavior
            Qt.CustomizeWindowHint |  # We control the hints now
            Qt.WindowTitleHint |  # Title bar text
            Qt.WindowCloseButtonHint |  # X button
            Qt.WindowMaximizeButtonHint |  # Maximize/restore
            Qt.WindowMinimizeButtonHint  # Minimize (optional — remove if unwanted)
        )

        # Extra insurance (some Qt versions/platforms need it)
        dialog.setWindowFlag(Qt.WindowContextHelpButtonHint, False)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        #text_edit = QTextEdit()
        text_edit = QTextBrowser()
        text_edit.setOpenExternalLinks(True)  # ← Critical

        text_edit.setHtml(report)
        text_edit.setReadOnly(True)
        layout.addWidget(text_edit, stretch=1)  # text takes main space

        # Button row: Copy left, Print right
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()  # push buttons to right

        # Share Report button
        share_btn = QPushButton("Share Report")
        share_btn.setFixedWidth(140)
        share_btn.clicked.connect(lambda: self.share_team_report(team_id))
        btn_layout.addWidget(share_btn)

        # Copy Report button
        copy_btn = QPushButton("Copy Report")
        copy_btn.setFixedWidth(140)
        copy_btn.clicked.connect(lambda: self.copy_report_to_clipboard(report))
        btn_layout.addWidget(copy_btn)

        # Print Report button
        print_btn = QPushButton("Print Report")
        print_btn.setFixedWidth(140)
        print_btn.clicked.connect(lambda: self.print_report(text_edit))
        btn_layout.addWidget(print_btn)

        # Close Report button
        close_btn = QPushButton("Close Report")
        close_btn.setFixedWidth(140)
        close_btn.clicked.connect(dialog.accept)
        btn_layout.addWidget(close_btn)

        layout.addLayout(btn_layout)

        dialog.exec_()



    def populate_game_season_combo(self):
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()

        # Season combo
        self.game_season_combo.clear()
        self.game_season_combo.addItem("All Seasons")
        c.execute("SELECT DISTINCT season FROM teams WHERE season IS NOT NULL AND season != '' ORDER BY season DESC")
        for (season,) in c.fetchall():
            self.game_season_combo.addItem(season)

        # Team combo starts empty - will be populated by season change
        self.team_filter_combo.clear()
        self.team_filter_combo.addItem("All Teams")

        conn.close()

        # Initial sync
        self.update_team_combo_for_season(self.game_season_combo, self.team_filter_combo, "All Teams")

    def setup_teams_tab(self):
        layout = QVBoxLayout(self.teams_tab)

        # Season Filter Row
        filter_layout = QHBoxLayout()
        filter_layout.addWidget(QLabel("Season:"))

        self.team_season_combo = QComboBox()
        self.team_season_combo.addItem("All")

        # Populate seasons
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT DISTINCT season FROM teams WHERE season IS NOT NULL AND season != '' ORDER BY season DESC")
        for (season,) in c.fetchall():
            self.team_season_combo.addItem(season)
        conn.close()

        filter_layout.addWidget(self.team_season_combo)
        filter_layout.addStretch()

        # Connect signal
        self.team_season_combo.currentIndexChanged.connect(self.load_teams)

        # ←←← CHANGED: Safe pre-selection that waits until the table exists
        QTimer.singleShot(0, lambda: self.preselect_default_season_filter(
            self.team_season_combo, self.load_teams))

        layout.addLayout(filter_layout)

        self.teams_table = QTableWidget()
        self.teams_table.setAlternatingRowColors(True)
        self.teams_table.setStyleSheet("""
                    QTableWidget {
                        alternate-background-color: #FFEDB8;
                        background-color: white;
                    }
                """)

        self.teams_table.setColumnCount(10)
        self.teams_table.setHorizontalHeaderLabels([
            "Season", "Team Name", "GP", "W", "L", "T", "PPG", "OPPG", "ABR", "Last Game"
        ])

        header = self.teams_table.horizontalHeader()

        # Add hover tooltip for Team ABR column
        abr_header_item = self.teams_table.horizontalHeaderItem(8)  # ABR is now column 8
        if abr_header_item:
            abr_header_item.setToolTip(
                "Team A-B Shot Quality Ratio\n"
                "(A + B shot attempts by team players) / Total team shot attempts\n"
                "Based on rostered players only"
            )

        # Header text alignment
        for col in range(2, 9):  # GP to OPPG (columns 2–8)
            header_item = self.teams_table.horizontalHeaderItem(col)
            if header_item:
                header_item.setTextAlignment(Qt.AlignCenter | Qt.AlignVCenter)

        # Last Game header right-aligned
        last_header = self.teams_table.horizontalHeaderItem(9)
        if last_header:
            last_header.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)

        # Column widths
        header.setSectionResizeMode(0, QHeaderView.Fixed)  # Season
        header.resizeSection(0, 145)  # wider

        header.setSectionResizeMode(1, QHeaderView.Fixed)  # Team Name
        header.resizeSection(1, 210)  # narrower

        # Let the rest stretch naturally
        for col in range(2, 10):
            header.setSectionResizeMode(col, QHeaderView.Stretch)

        self.teams_table.setCursor(Qt.PointingHandCursor)
        self.teams_table.setSortingEnabled(True)
        self.teams_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.teams_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.teams_table.cellDoubleClicked.connect(self.on_team_double_click)

        header.setSectionsClickable(True)

        layout.addWidget(self.teams_table)

        # Connect filter change to reload
        self.team_season_combo.currentIndexChanged.connect(self.load_teams)

        btn_layout = QHBoxLayout()
        add_btn = QPushButton("Add Team")
        add_btn.clicked.connect(self.add_team)
        btn_layout.addWidget(add_btn)

        edit_btn = QPushButton("Edit Team")
        edit_btn.clicked.connect(self.edit_team)
        btn_layout.addWidget(edit_btn)

        delete_btn = QPushButton("Delete Team + Players")
        delete_btn.clicked.connect(self.delete_team)
        btn_layout.addWidget(delete_btn)

        report_btn = QPushButton("View Team Report")
        report_btn.clicked.connect(self.view_team_report_via_button)
        btn_layout.addWidget(report_btn)

        # Share Team Report Button
        share_btn = QPushButton("Share Team Report")
        share_btn.clicked.connect(self.share_selected_team_report)
        btn_layout.addWidget(share_btn)

        layout.addLayout(btn_layout)

        # Populate combo
        self.populate_team_season_combo()

        self.load_teams()

    def view_team_report_via_button(self):
        selected = self.teams_table.selectedItems()
        if not selected:
            QMessageBox.warning(self, "No Selection", "Please select a team row first.")
            return

        row = selected[0].row()
        team_id_item = self.teams_table.item(row, 1)  # Team Name column (or wherever team_id is stored)
        if not team_id_item:
            return

        team_id = team_id_item.data(Qt.UserRole)
        self.open_team_report(team_id)

    def populate_team_season_combo(self):
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT DISTINCT season FROM teams WHERE season IS NOT NULL AND season != '' ORDER BY season DESC")
        seasons = [row[0].strip() for row in c.fetchall() if row[0]]
        conn.close()

        self.team_season_combo.clear()
        self.team_season_combo.addItem("All")
        self.team_season_combo.addItems(seasons)

    def setup_players_tab(self):
        layout = QVBoxLayout(self.players_tab)

        filter_layout = QHBoxLayout()
        filter_layout.addWidget(QLabel("Season:"))
        self.player_season_combo = QComboBox()
        self.player_season_combo.addItem("All")

        # Populate seasons
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT DISTINCT season FROM teams WHERE season IS NOT NULL AND season != '' ORDER BY season DESC")
        for (season,) in c.fetchall():
            self.player_season_combo.addItem(season)
        conn.close()

        filter_layout.addWidget(self.player_season_combo)

        filter_layout.addSpacing(30)
        filter_layout.addWidget(QLabel("Team:"))
        self.player_team_combo = QComboBox()
        self.player_team_combo.addItem("All")
        filter_layout.addWidget(self.player_team_combo)

        # NEW: Hide small sample ABR players
        self.hide_small_abr_cb = QCheckBox("Hide Minimum ABR Players")
        self.hide_small_abr_cb.setChecked(False)  # default OFF
        filter_layout.addWidget(self.hide_small_abr_cb)

        filter_layout.addStretch()
        layout.addLayout(filter_layout)

        self.players_table = QTableWidget()
        self.players_table.setAlternatingRowColors(True)
        self.players_table.setStyleSheet("""
            QTableWidget {
                alternate-background-color: #e1f0fa;  /* very light gray */
                background-color: white;
            }
        """)
        self.players_table.setColumnCount(9)
        self.players_table.setHorizontalHeaderLabels([
            "Season", "Team", "#", "Player Name", "GP", "PTS", "PPG", "ABR", "Last Played"
        ])
        self.players_table.setSortingEnabled(True)
        self.players_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.players_table.setSelectionMode(QTableWidget.SingleSelection)
        self.players_table.setEditTriggers(QTableWidget.NoEditTriggers)

        header = self.players_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Stretch)
        header.setSectionsClickable(True)

        for col in [2, 4, 5, 6, 7, 8]:
            header.setSectionResizeMode(col, QHeaderView.ResizeToContents)

        layout.addWidget(self.players_table)

        # ADD THIS LINE right here (or anywhere after creating the table):
        self.players_table.doubleClicked.connect(self.view_player_report)

        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)  # nice gap between buttons

        add_player_btn = QPushButton("Add Player")
        #add_player_btn.setStyleSheet("font-weight: bold;")  # optional: match other tabs
        add_player_btn.clicked.connect(self.add_player)
        btn_layout.addWidget(add_player_btn)

        edit_player_btn = QPushButton("Edit Player")
        edit_player_btn.clicked.connect(self.edit_player)
        btn_layout.addWidget(edit_player_btn)

        # Optional: Delete button later
        del_player_btn = QPushButton("Delete Player")
        del_player_btn.clicked.connect(self.delete_player)
        btn_layout.addWidget(del_player_btn)

        # Refresh button - wider
        refresh_btn = QPushButton("Refresh")
        #refresh_btn.setFixedWidth(160)  # ← your desired width (adjust 160 to taste)
        refresh_btn.clicked.connect(self.load_players)
        btn_layout.addWidget(refresh_btn)

        # View Player Report button - wider to fit text
        report_btn = QPushButton("View Player Report")
        #report_btn.setFixedWidth(220)  # ← wider for longer text
        report_btn.clicked.connect(self.view_player_report)
        btn_layout.addWidget(report_btn)

        layout.addLayout(btn_layout)

        self.populate_player_filters()
        self.load_players()

        # Final connections
        self.player_season_combo.currentIndexChanged.connect(self.on_player_season_changed)
        self.player_team_combo.currentIndexChanged.connect(self.load_players)

        # Pre-select default season
        QTimer.singleShot(0, lambda: self.preselect_default_season_filter(
            self.player_season_combo, self.load_players))

        self.hide_small_abr_cb.stateChanged.connect(self.load_players)


    def on_player_season_changed(self):
        """Season changed → update team filter + reload table"""
        self.update_team_combo_for_season(
            self.player_season_combo,
            self.player_team_combo,
            "All"
        )
        self.load_players()        # ← This should still filter by both season + team

    def on_game_season_changed(self):
        """Season changed on Games tab"""
        self.update_team_combo_for_season(
            self.game_season_combo,
            self.team_filter_combo,
            "All Teams"
        )
        self.load_games()          # ← This should filter by both

    def populate_player_filters(self):
        """Populate season and team filters for Players tab"""
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()

        # === SEASON COMBO ===
        self.player_season_combo.clear()
        self.player_season_combo.addItem("All Seasons")   # Use consistent text

        c.execute("SELECT DISTINCT season FROM teams WHERE season IS NOT NULL AND season != '' ORDER BY season DESC")
        for (season,) in c.fetchall():
            self.player_season_combo.addItem(season)

        # === TEAM COMBO (will be updated by season change) ===
        self.player_team_combo.clear()
        self.player_team_combo.addItem("All")

        conn.close()

        # Initial sync with default season
        self.update_team_combo_for_season(self.player_season_combo, self.player_team_combo, "All")

    def load_players(self):
        season_filter = self.player_season_combo.currentText().strip()
        team_filter_id = self.player_team_combo.currentData()

        # Get ABR minimum from defaults
        conn_defaults = sqlite3.connect(DB_NAME)
        c_def = conn_defaults.cursor()
        c_def.execute("SELECT value FROM defaults WHERE key = 'abr_minimum'")
        row = c_def.fetchone()
        abr_min = int(row[0]) if row and row[0] else 10
        conn_defaults.close()

        # NEW: Respect the hide checkbox
        hide_small_samples = self.hide_small_abr_cb.isChecked()

        sort_col = self.players_table.horizontalHeader().sortIndicatorSection()
        sort_order = self.players_table.horizontalHeader().sortIndicatorOrder()

        self.players_table.setSortingEnabled(False)
        self.players_table.setRowCount(0)
        self.players_table.setCursor(Qt.PointingHandCursor)

        self.players_table.setColumnCount(9)
        self.players_table.setHorizontalHeaderLabels([
            "Season", "Team", "#", "Player Name", "GP", "PTS", "PPG",
            "ABR", "Last Played"
        ])

        # Add hover tooltip for ABR column
        header = self.players_table.horizontalHeader()
        abr_header_item = self.players_table.horizontalHeaderItem(7)
        if abr_header_item:
            abr_header_item.setToolTip(
                "A-B Shot Quality Ratio\n(A + B shots) / Total shots\nGrey = below minimum sample size")

        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()

        query = """
                SELECT 
                    t.season,
                    t.name AS team_name,
                    p.number,
                    p.name AS player_name,
                    COUNT(DISTINCT g.id) AS gp,
                    COALESCE(SUM(CASE 
                        WHEN e.type LIKE '2PM%' THEN 2
                        WHEN e.type LIKE '3PM%' THEN 3
                        WHEN e.type LIKE 'FTM%' THEN 1 
                        ELSE 0 
                    END), 0) AS pts,
                    MAX(g.date) AS last_date,
                    p.id
                FROM players p
                JOIN teams t ON p.team_id = t.id
                LEFT JOIN game_rosters gr ON gr.player_id = p.id
                LEFT JOIN games g ON g.id = gr.game_id
                LEFT JOIN videos v ON v.game_id = g.id
                LEFT JOIN events e ON e.video_id = v.id AND e.player_id = p.id
                """

        params = []
        conditions = []

        if season_filter and season_filter not in ("All Seasons", "All", ""):
            conditions.append("t.season = ?")
            params.append(season_filter)

        if team_filter_id is not None and team_filter_id != "":
            conditions.append("t.id = ?")
            params.append(team_filter_id)

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        query += """
                    GROUP BY p.id, t.season, t.name, p.number, p.name
                    ORDER BY t.season DESC, t.name COLLATE NOCASE, CAST(p.number AS INTEGER)
                """

        try:
            c.execute(query, params)
            rows = c.fetchall()
        except sqlite3.Error as e:
            print("SQL Error in load_players:", e)
            rows = []

        conn.close()

        for row in rows:
            season, team, number, pname, gp, pts, last_date, pid = row
            ppg_value = pts / gp if gp > 0 else 0.0

            # A-B Ratio calculation
            abr_value = 0.0
            total_shots = 0

            conn_abr = sqlite3.connect(DB_NAME)
            c_abr = conn_abr.cursor()
            c_abr.execute("""
                SELECT COUNT(*) as total_shots,
                       SUM(CASE WHEN shot_quality IN ('A','B') THEN 1 ELSE 0 END) as ab_shots
                FROM events e
                JOIN videos v ON e.video_id = v.id
                JOIN games g ON v.game_id = g.id
                WHERE e.player_id = ? 
                  AND e.shot_quality IS NOT NULL
                  AND (e.type LIKE '2P%' OR e.type LIKE '3P%')
            """, (pid,))
            abr_row = c_abr.fetchone()
            conn_abr.close()

            if abr_row:
                total_shots = abr_row[0] or 0
                ab_shots = abr_row[1] or 0
                abr_value = (ab_shots / total_shots * 100) if total_shots > 0 else 0.0

            # === NEW: Skip small sample players if checkbox is checked ===
            if hide_small_samples and total_shots < abr_min:
                continue   # ← This was in the wrong place before

            # If we reach here, create the row
            r = self.players_table.rowCount()
            self.players_table.insertRow(r)

            gp_item  = SortableNumericItem(str(gp), float(gp))
            pts_item = SortableNumericItem(str(int(pts or 0)), float(pts or 0))
            ppg_item = SortableNumericItem(f"{ppg_value:.1f}", ppg_value)

            number_display = str(number) if number else ""
            number_sort = float(number) if number and str(number).isdigit() else float('inf')
            num_item = SortableNumericItem(number_display, number_sort)

            abr_text = f"{abr_value:.1f}%" if total_shots > 0 else "—"
            abr_item = SortableNumericItem(abr_text, abr_value)
            abr_item.setTextAlignment(Qt.AlignCenter | Qt.AlignVCenter)

            if total_shots < abr_min and total_shots > 0:
                abr_item.setForeground(QColor(160, 160, 160))

            items = [
                QTableWidgetItem(season or ""),
                QTableWidgetItem(team or ""),
                num_item,
                QTableWidgetItem(pname or ""),
                gp_item,
                pts_item,
                ppg_item,
                abr_item,
                QTableWidgetItem(last_date or "—")
            ]

            items[3].setData(Qt.UserRole, pid)

            for col in [2, 4, 5, 6, 7]:
                items[col].setTextAlignment(Qt.AlignCenter | Qt.AlignVCenter)

            items[8].setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)

            for col, item in enumerate(items):
                self.players_table.setItem(r, col, item)

        self.players_table.setSortingEnabled(True)

        # Default sort: Season (column 0) descending → then Team
        self.players_table.sortItems(0, Qt.DescendingOrder)

    def add_player(self):
        dialog = PlayerInputDialog(self)  # add mode (player_id=None)
        # Debug: confirm connection
        #print("[MAIN DEBUG] Connecting playerSaved signal to load_players")
        dialog.playerSaved.connect(self.load_players)

        if dialog.exec() != QDialog.Accepted:
            #print("[DEBUG] Add Player canceled or rejected")
            return

        # At this point the player has already been saved by the dialog
        # We only need to refresh the UI
        #QMessageBox.information(self, "Player Added", "Player added to team.")
        self.load_players()  # refresh main players list

    def edit_player(self):
        # Get selected player_id (your existing logic is fine)
        selected = self.players_table.currentRow()
        if selected < 0:
            QMessageBox.warning(self, "No Selection", "Please select a player to edit.")
            return

        item = self.players_table.item(selected, 3)  # assuming column 7 holds player_id with UserRole
        player_id = item.data(Qt.UserRole)
        if player_id is None:
            QMessageBox.warning(self, "Error", "No player ID found for selection.")
            return

        dialog = PlayerInputDialog(self, player_id=player_id)  # edit mode
        if dialog.exec() != QDialog.Accepted:
            #print("[DEBUG] Edit Player canceled or rejected")
            return

        # At this point the player has already been updated by the dialog
        #QMessageBox.information(self, "Player Updated", "Player updated successfully.")
        self.load_players()  # refresh main players list

    def delete_player(self):
        selected = self.players_table.currentRow()
        if selected < 0:
            QMessageBox.warning(self, "No Selection", "Please select a player to delete.")
            return

        # Get player_id from column 7 (hidden ID column)
        item = self.players_table.item(selected, 7)  # column 7 has UserRole with ID
        player_id = item.data(Qt.UserRole)

        if player_id is None:
            QMessageBox.warning(self, "Error", "No player ID found for selection.")
            return

        # Get player name for confirmation (from column 1 or 2)
        player_name = self.players_table.item(selected, 1).text()  # adjust column if name is elsewhere

        reply = QMessageBox.question(
            self,
            "Delete Player",
            f"Delete player '{player_name}'?\n\n"
            "This will remove them from all game rosters and reassign any events to team-level.\n"
            "Events and points will be preserved, but personal attribution removed.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        if reply == QMessageBox.No:
            return

        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()

        try:
            # 1. Find all games this player is rostered in
            c.execute("SELECT DISTINCT game_id FROM game_rosters WHERE player_id = ?", (player_id,))
            game_ids = [r[0] for r in c.fetchall()]
            #print(f"[DEBUG] Player in {len(game_ids)} games: {game_ids}")

            # 2. Reassign events in each game
            reassigned_total = 0
            for gid in game_ids:
                count = self.reassign_events_on_player_removal(gid, player_id)
                #print(f"[DEBUG] Reassigned {count} events in game {gid}")
                if count > 0:
                    reassigned_total += count
                elif count < 0:
                    raise Exception("Reassignment canceled by user")

            # 3. Delete roster entries
            c.execute("DELETE FROM game_rosters WHERE player_id = ?", (player_id,))
            roster_deleted = c.rowcount
            #print(f"[DEBUG] Deleted {roster_deleted} roster entries")

            # 4. Delete player
            c.execute("DELETE FROM players WHERE id = ?", (player_id,))
            player_deleted = c.rowcount
            #print(f"[DEBUG] Deleted {player_deleted} player record")

            conn.commit()
            #print("[DEBUG] Commit successful - player deleted")

            msg = f"Player '{player_name}' deleted.\n"
            msg += f"Removed from {roster_deleted} game roster(s).\n"
            msg += f"{reassigned_total} event(s) reassigned to team-level across {len(game_ids)} game(s)."
            QMessageBox.information(self, "Player Deleted", msg)

            # 5. Refresh the players table
            self.load_players()
            #print("[DEBUG] Players table refreshed")

        except Exception as e:
            conn.rollback()
            print(f"[DEBUG] Delete error: {e}")
            QMessageBox.critical(self, "Delete Failed", f"Could not delete player:\n{str(e)}")
        finally:
            conn.close()

    def view_player_report(self):
        selected = self.players_table.selectedItems()
        if not selected:
            QMessageBox.warning(self, "No selection", "Please select a player first.")
            return

        row = selected[0].row()

        player_id_item = self.players_table.item(row, 3)
        if not player_id_item or player_id_item.data(Qt.UserRole) is None:
            QMessageBox.warning(self, "Error", "Cannot retrieve player ID.")
            return

        player_id = player_id_item.data(Qt.UserRole)

        name = self.players_table.item(row, 3).text()
        number = self.players_table.item(row, 2).text()
        team = self.players_table.item(row, 1).text()

        try:
            report_html = self.generate_player_report(player_id)

            dialog = QDialog(self)
            dialog.setWindowTitle(f"Player Report — #{number} {name} ({team})")
            # Enable maximize (and minimize) buttons in title bar
            dialog.setWindowFlags(
                dialog.windowFlags()  # Keep existing flags (including Qt.Dialog)
                | Qt.WindowMaximizeButtonHint
                | Qt.WindowMinimizeButtonHint  # Optional but usually wanted for symmetry
            )
            dialog.setGeometry(180, 140, 1240, 800)

            layout = QVBoxLayout(dialog)
            layout.setContentsMargins(12, 12, 12, 12)
            layout.setSpacing(10)

            #text_edit = QTextEdit()
            text_edit = QTextBrowser()
            text_edit.setOpenExternalLinks(True)  # ← Critical
            text_edit.setHtml(report_html)

            text_edit.setReadOnly(True)
            layout.addWidget(text_edit, stretch=1)  # stretch text to fill space

            # Button row: Copy left, Print Middle, Close Right
            btn_layout = QHBoxLayout()
            btn_layout.addStretch()  # push buttons to right

            # Copy Report button
            copy_btn = QPushButton("Copy Report")
            copy_btn.setFixedWidth(140)
            copy_btn.clicked.connect(lambda: self.copy_report_to_clipboard(report_html))
            btn_layout.addWidget(copy_btn)

            # Print Report button
            print_btn = QPushButton("Print Report")
            print_btn.setFixedWidth(140)
            print_btn.clicked.connect(lambda: self.print_report(text_edit))
            btn_layout.addWidget(print_btn)

            close_btn = QPushButton("Close Report")
            close_btn.setFixedWidth(140)
            close_btn.clicked.connect(dialog.accept)
            btn_layout.addWidget(close_btn)

            layout.addLayout(btn_layout)

            dialog.exec_()

        except Exception as e:
            QMessageBox.critical(self, "Report Error", f"Failed to generate report:\n{str(e)}")

    def generate_player_report(self, player_id):
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()

        # Player basic info
        c.execute("""
            SELECT p.name, p.number, t.name AS team_name, t.season
            FROM players p
            JOIN teams t ON p.team_id = t.id
            WHERE p.id = ?
        """, (player_id,))
        player_info = c.fetchone()
        if not player_info:
            conn.close()
            return "<h2>Player not found.</h2>"

        p_name, p_number, team_name, season = player_info
        #padded_num = str(p_number).zfill(2)
        padded_num = str(p_number)

        if season:
            report = f"<h2>Player Report: {season} - {team_name} - #{padded_num} - {p_name}</h2>"
        else:
            report = f"<h2>Player Report: {team_name} - #{padded_num} - {p_name}</h2>"

        # List of games this player participated in
        #print("Executing games list query")
        c.execute("""
                  SELECT g.date,
                         g.name,
                         CASE WHEN g.home_team_id = p.team_id THEN t_guest.name ELSE t_home.name END AS opponent,
                         g.id
                  FROM games g
                           JOIN game_rosters gr ON gr.game_id = g.id
                           JOIN players p ON gr.player_id = p.id
                           LEFT JOIN teams t_home ON g.home_team_id = t_home.id
                           LEFT JOIN teams t_guest ON g.guest_team_id = t_guest.id
                  WHERE p.id = ?
                    AND g.is_complete = 1 -- ← ADD THIS LINE
                  ORDER BY g.date ASC
                  """, (player_id,))
        games = c.fetchall()

        num_games = len(games)
        if num_games == 0:
            conn.close()
            report += "<p><i>No games played by this player yet.</i></p>"
            return report

        game_rows = []

        # Initialize all totals
        total_pts = total_fgm = total_fga = total_2pm = total_2pa = total_3pm = total_3pa = 0
        total_ftm = total_fta = 0
        total_orb = total_dreb = total_treb = 0
        total_pfl = total_ast = total_tov = total_stl = total_blk = total_chg = total_dfl = 0
        total_ftf = 0.0
        total_ab_shots = 0  # ← NEW

        for g_date, g_name, opponent, g_id in games:
            c.execute("""
                              SELECT SUM(CASE WHEN e.type LIKE '2PM%' THEN 2 WHEN e.type LIKE '3PM%' THEN 3 WHEN e.type LIKE 'FTM%' THEN 1 ELSE 0 END) as pts,
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

            p2_pct = (twopm / fga_2pt * 100) if fga_2pt > 0 else 0.0
            p3_pct = (threepm / fga_3pt * 100) if fga_3pt > 0 else 0.0
            ft_pct = (ftm / fta_total * 100) if fta_total > 0 else 0.0
            ftf = (fta_total / fga_total) if fga_total > 0 else 0.0

            efg_pct = (twopm + threepm + 0.5 * threepm) / fga_total * 100 if fga_total > 0 else 0.0
            treb = oreb + dreb
            atr = (ast / tov) if tov > 0 else 0.0

            fgm = twopm
            fga = fga_2pt

            # Per-game ABR
            abr_game = 0.0
            if fga_total > 0:  # 2PA + 3PA for this game
                c.execute("""
                                SELECT SUM(CASE WHEN shot_quality IN ('A','B') THEN 1 ELSE 0 END)
                                FROM events 
                                WHERE player_id = ? 
                                  AND video_id IN (SELECT id FROM videos WHERE game_id = ?)
                            """, (player_id, g_id))
                ab_makes_this_game = c.fetchone()[0] or 0
                abr_game = (ab_makes_this_game / fga_total * 100)

            # Accumulate season ABR
            total_ab_shots += ab_makes_this_game  # ← NEW

            game_rows.append((g_date, g_name or "", opponent, fgm, fga, p2_pct, threepm, fga_3pt, p3_pct,
                              efg_pct, ftm, fta_total, ft_pct, ftf, pts, oreb, dreb, treb, pfl, ast, tov, stl,
                              blk, chg, dfl, atr, abr_game))

            # Accumulation
            total_pts += pts
            total_fgm += fgm
            total_fga += fga
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
            total_ftf += ftf  # optional, but safe
            #total_ab_shots +=

        total_treb = total_orb + total_dreb

        # Averages
        avg_fgm = total_fgm / num_games if num_games > 0 else 0
        avg_fga = total_fga / num_games if num_games > 0 else 0
        avg_3pm = total_3pm / num_games if num_games > 0 else 0
        avg_3pa = total_3pa / num_games if num_games > 0 else 0
        avg_ftm = total_ftm / num_games if num_games > 0 else 0
        avg_fta = total_fta / num_games if num_games > 0 else 0
        avg_pts = total_pts / num_games if num_games > 0 else 0
        avg_orb = total_orb / num_games if num_games > 0 else 0
        avg_dreb = total_dreb / num_games if num_games > 0 else 0
        avg_treb = total_treb / num_games if num_games > 0 else 0
        avg_pfl = total_pfl / num_games if num_games > 0 else 0
        avg_ast = total_ast / num_games if num_games > 0 else 0
        avg_tov = total_tov / num_games if num_games > 0 else 0
        avg_stl = total_stl / num_games if num_games > 0 else 0
        avg_blk = total_blk / num_games if num_games > 0 else 0
        avg_chg = total_chg / num_games if num_games > 0 else 0
        avg_dfl = total_dfl / num_games if num_games > 0 else 0
        avg_atr = (total_ast / total_tov) if total_tov > 0 else 0.0

        game_table = """
        <table cellpadding="2">
            <tr style="background:#d0e0ff; font-weight:bold; text-align:center;">
                <th style="text-align:left;">Game Date</th>
                <th style="text-align:left;">Name</th>
                <th style="text-align:left;">Opponent</th>
                <th style='text-align: center;'>2PTM</th><th style='text-align: center;'>2PTA</th><th style='text-align: center;'>2PT%</th>
                <th style='text-align: center;'>3PTM</th><th style='text-align: center;'>3PTA</th><th style='text-align: center;'>3PT%</th>
                <th style='text-align: center;'>eFG%</th>
                <th style='text-align: center;'>FTM</th><th style='text-align: center;'>FTA</th><th style='text-align: center;'>FT%</th>
                <th style='text-align: center;'>FTF</th>
                <th style='text-align: center;'>PTS</th><th style='text-align: center;'>ORB</th><th style='text-align: center;'>DREB</th><th style='text-align: center;'>TREB</th>
                <th style='text-align: center;'>PFL</th><th style='text-align: center;'>AST</th><th style='text-align: center;'>TO</th><th style='text-align: center;'>STL</th><th style='text-align: center;'>BLK</th><th style='text-align: center;'>CHG</th><th style='text-align: center;'>DFL</th>
                <th style='text-align: center;'>ATR</th>
                <th style='text-align: center;'>ABR</th>
            </tr>
        """

        row_colour = "#FFFFFF;"
        for row in game_rows:
            date, gname, opp, fgm, fga, p2_pct, threepm, threepa, p3_pct, efg_pct, ftm, fta, ft_pct, ftf, pts, orb, dreb, treb, pfl, ast, tov, stl, blk, chg, dfl, atr, abr_game = row
            game_table += f"""
            <tr style="text-align:center; background:{row_colour}">
                <td style="text-align:left;">{date}</td>
                <td style="text-align:left;">{gname}</td>
                <td style="text-align:left;">{opp}</td>
                <td style='text-align: center;'>{fgm}</td><td style='text-align: center;'>{fga}</td><td style='text-align: center;'>{p2_pct:.1f}%</td>
                <td style='text-align: center;'>{threepm}</td><td style='text-align: center;'>{threepa}</td><td style='text-align: center;'>{p3_pct:.1f}%</td>
                <td style='text-align: center;'>{efg_pct:.1f}%</td>
                <td style='text-align: center;'>{ftm}</td><td style='text-align: center;'>{fta}</td><td style='text-align: center;'>{ft_pct:.1f}%</td>
                <td style='text-align: center;'>{ftf:.2f}</td>
                <td style='text-align: center;'>{pts}</td><td style='text-align: center;'>{orb}</td><td style='text-align: center;'>{dreb}</td><td style='text-align: center;'>{treb}</td>
                <td style='text-align: center;'>{pfl}</td><td style='text-align: center;'>{ast}</td><td style='text-align: center;'>{tov}</td><td style='text-align: center;'>{stl}</td><td style='text-align: center;'>{blk}</td><td style='text-align: center;'>{chg}</td><td style='text-align: center;'>{dfl}</td>
                <td style='text-align: center;'>{atr:.2f}</td>
                <td style='text-align: center;'>{abr_game:.1f}%</td>
            </tr>
            """
            if row_colour == "#FFFFFF;":
                # change the row for next time around
                row_colour = "#e9f0f0;"
            else:
                row_colour = "#FFFFFF;"

        total_fga_all = total_2pa + total_3pa
        total_fgm_all = total_2pm + total_3pm
        total_p2 = (total_2pm / total_2pa * 100) if total_2pa > 0 else 0.0
        total_p3 = (total_3pm / total_3pa * 100) if total_3pa > 0 else 0.0
        total_ft = (total_ftm / total_fta * 100) if total_fta > 0 else 0.0

        total_fga = total_2pa + total_3pa
        total_ftf = (total_fta / total_fga) if total_fga > 0 else 0.0

        # Totals eFG% (consistent with CourtTag method)
        total_efg_pct = (total_fgm_all + 0.5 * total_3pm) / total_fga_all * 100 if total_fga_all > 0 else 0.0
        avg_atr = (total_ast / total_tov) if total_tov > 0 else 0.0

        # Season-wide ABR
        season_abr = 0.0
        if total_fga > 0:
            season_abr = (total_ab_shots / total_fga * 100)

        game_table += f"""
        <tr style="font-weight:bold; background:#d0e0ff; text-align:center;">
            <td style='text-align:left;'>Games Played:</td>
            <td style="text-align:center;">{num_games}</td><td style="text-align:right;">Totals:</td>
            <td style='text-align: center;'>{total_2pm}</td><td style='text-align: center;'>{total_2pa}</td><td style='text-align: center;'>{total_p2:.1f}%</td>
            <td style='text-align: center;'>{total_3pm}</td><td style='text-align: center;'>{total_3pa}</td><td style='text-align: center;'>{total_p3:.1f}%</td>
            <td style='text-align: center;'>{total_efg_pct:.1f}%</td>
            <td style='text-align: center;'>{total_ftm}</td><td style='text-align: center;'>{total_fta}</td><td style='text-align: center;'>{total_ft:.1f}%</td>
            <td style='text-align: center;'>{total_ftf:.2f}</td>
            <td style='text-align: center;'>{total_pts}</td><td style='text-align: center;'>{total_orb}</td><td style='text-align: center;'>{total_dreb}</td><td style='text-align: center;'>{total_treb}</td>
            <td style='text-align: center;'>{total_pfl}</td><td style='text-align: center;'>{total_ast}</td><td style='text-align: center;'>{total_tov}</td>
            <td style='text-align: center;'>{total_stl}</td><td style='text-align: center;'>{total_blk}</td><td style='text-align: center;'>{total_chg}</td><td style='text-align: center;'>{total_dfl}</td>
            <td style='text-align: center;'>{avg_atr:.2f}</td>
            <td style='text-align: center;'>{season_abr:.1f}%</td>
        </tr>
        <tr style="font-weight:bold; background:#e8f0ff; text-align:center;">
            <td></td><td></td><td style='text-align: right;'>Averages:</td>
            <td style='text-align: center;'>{total_2pm / num_games if num_games > 0 else 0:.1f}</td>
            <td style='text-align: center;'>{total_2pa / num_games if num_games > 0 else 0:.1f}</td>
            <td style='text-align: center;'>—</td>
            <td style='text-align: center;'>{avg_3pm:.1f}</td><td style='text-align: center;'>{avg_3pa:.1f}</td><td style='text-align: center;'>—</td>
            <td style='text-align: center;'>—</td>
            <td style='text-align: center;'>{avg_ftm:.1f}</td><td style='text-align: center;'>{avg_fta:.1f}</td>
            <td style='text-align: center;'>{round(total_orb / num_games, 1) if num_games > 0 else 0:.1f}</td>
            <td style='text-align: center;'>—</td>
            <td style='text-align: center;'>{avg_pts:.1f}</td><td style='text-align: center;'>{avg_orb:.1f}</td><td style='text-align: center;'>{avg_dreb:.1f}</td><td style='text-align: center;'>{avg_treb:.1f}</td>
            <td style='text-align: center;'>{avg_pfl:.1f}</td><td style='text-align: center;'>{avg_ast:.1f}</td><td style='text-align: center;'>{avg_tov:.1f}</td>
            <td style='text-align: center;'>{avg_stl:.1f}</td><td style='text-align: center;'>{avg_blk:.1f}</td><td style='text-align: center;'>{avg_chg:.1f}</td><td style='text-align: center;'>{avg_dfl:.1f}</td>
            <td style='text-align: center;'>-</td>
            <td style='text-align: center;'>-</td>
        </tr>
        </table>
        """

        report += "<h3>Player Game-by-Game Stats</h3>" + game_table

        # Shot Locations — FG only: 2PT + 3PT (complete games)
        c.execute("""
                  SELECT e.location,
                         SUM(CASE WHEN e.type LIKE '2PM%' THEN 1 ELSE 0 END)                           AS twopm,
                         SUM(CASE WHEN e.type LIKE '2P%' AND e.type NOT LIKE '2PM%' THEN 1 ELSE 0 END) AS missed_2pa,
                         SUM(CASE WHEN e.type LIKE '3PM%' THEN 1 ELSE 0 END)                           AS threepm,
                         SUM(CASE WHEN e.type LIKE '3P%' AND e.type NOT LIKE '3PM%' THEN 1 ELSE 0 END) AS missed_3pa,
                         SUM(CASE WHEN e.shot_quality IN ('A', 'B') THEN 1 ELSE 0 END)                 AS ab_shots
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

        loc_total_2pm = loc_total_2pa = loc_total_3pm = loc_total_3pa = loc_total_ab = 0
        parsed_locs = []
        for loc, twopm, missed_2pa, threepm, missed_3pa, ab_shots in loc_rows:
            twopm = twopm or 0
            missed_2pa = missed_2pa or 0
            threepm = threepm or 0
            missed_3pa = missed_3pa or 0
            ab_shots = ab_shots or 0
            twopa = twopm + missed_2pa
            threepa = threepm + missed_3pa
            fga = twopa + threepa
            pts = (2 * twopm) + (3 * threepm)
            parsed_locs.append((loc, twopm, twopa, threepm, threepa, fga, pts, ab_shots))
            loc_total_2pm += twopm
            loc_total_2pa += twopa
            loc_total_3pm += threepm
            loc_total_3pa += threepa
            loc_total_ab += ab_shots

        loc_total_fga = loc_total_2pa + loc_total_3pa
        loc_total_pts = (2 * loc_total_2pm) + (3 * loc_total_3pm)
        loc_total_p2 = (loc_total_2pm / loc_total_2pa * 100) if loc_total_2pa > 0 else 0.0
        loc_total_p3 = (loc_total_3pm / loc_total_3pa * 100) if loc_total_3pa > 0 else 0.0
        loc_total_pps = (loc_total_pts / loc_total_fga) if loc_total_fga > 0 else 0.0
        loc_total_abr = (loc_total_ab / loc_total_fga * 100) if loc_total_fga > 0 else 0.0

        loc_table = """
        <!-- add 2 column table for shot location image -->
        <table border="0" cellpadding="3"><tr><td valign="top">
        <table cellpadding="2">
            <tr style="background:#d0e0ff; font-weight:bold; text-align:center;">
                <th style="text-align:left;">Shot Location (FG Only)</th>
                <th style='text-align: center;'>2PTM</th><th style='text-align: center;'>2PTA</th><th style='text-align: center;'>2PT%</th>
                <th style='text-align: center;'>3PTM</th><th style='text-align: center;'>3PTA</th><th style='text-align: center;'>3PT%</th>
                <th style='text-align: center;'>PTS</th><th style='text-align: center;'>PPS</th>
                <th style='text-align: center;'>TPT%</th><th style='text-align: center;'>TST%</th>
                <th style='text-align: center;'>ABR%</th>
            </tr>
        """

        row_colour = "#FFFFFF;"
        for loc, twopm, twopa, threepm, threepa, fga, pts, ab_shots in parsed_locs:
            p2_pct = (twopm / twopa * 100) if twopa > 0 else 0.0
            p3_pct = (threepm / threepa * 100) if threepa > 0 else 0.0
            pps = (pts / fga) if fga > 0 else 0.0
            tpt = (pts / loc_total_pts * 100) if loc_total_pts > 0 else 0.0
            tst = (fga / loc_total_fga * 100) if loc_total_fga > 0 else 0.0
            abr = (ab_shots / fga * 100) if fga > 0 else 0.0

            loc_table += f"""
            <tr style="background:{row_colour};">
                <td style="text-align:left;">{loc}</td>
                <td style='text-align: center;'>{twopm}</td><td style='text-align: center;'>{twopa}</td><td style='text-align: center;'>{p2_pct:.1f}%</td>
                <td style='text-align: center;'>{threepm}</td><td style='text-align: center;'>{threepa}</td><td style='text-align: center;'>{p3_pct:.1f}%</td>
                <td style='text-align: center;'>{pts}</td><td style='text-align: center;'>{pps:.2f}</td>
                <td style='text-align: center;'>{tpt:.1f}%</td><td style='text-align: center;'>{tst:.1f}%</td>
                <td style='text-align: center;'>{abr:.1f}%</td>
            </tr>
            """

            if row_colour == "#FFFFFF;":
                row_colour = "#e9f0f0"
            else:
                row_colour = "#FFFFFF;"

        loc_table += f"""
        <tr style="font-weight:bold; background:#d0e0ff; text-align:center;">
            <td style='text-align:right;'>Total:</td>
            <td style='text-align: center;'>{loc_total_2pm}</td><td style='text-align: center;'>{loc_total_2pa}</td><td style='text-align: center;'>{loc_total_p2:.1f}%</td>
            <td style='text-align: center;'>{loc_total_3pm}</td><td style='text-align: center;'>{loc_total_3pa}</td><td style='text-align: center;'>{loc_total_p3:.1f}%</td>
            <td style='text-align: center;'>{loc_total_pts}</td><td style='text-align: center;'>{loc_total_pps:.2f}</td>
            <td style='text-align: center;'>100%</td><td style='text-align: center;'>100%</td>
            <td style='text-align: center;'>{loc_total_abr:.1f}%</td>
        </tr>
        </table>
        <!-- add 2 column table for shot location image -->
        </td><td valign="top">
         <img src=":/Shot_Location_Diagram_v1.svg"  width="300" height="240" alt="Shot Location Diagram">
        </td>
        </tr>
        </table>
        """

        # === PLAYER SHOT QUALITY TABLE (Correct blue colors + TST%) ===========
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

        total_quality_points = sum(row[5] for row in quality_rows) if quality_rows else 0
        total_fg_attempts = sum((row[1] or 0) + (row[2] or 0) + (row[3] or 0) + (row[4] or 0) for row in quality_rows)

        player_shot_quality_table = """
        <h4>Shot Quality Summary</h4>
        <table>
            <tr style='font-weight: bold; background:#d0e0ff;'>
                <th style='text-align: left;'>Shot Quality</th>
                <th style='text-align: center;'>2PTM</th>
                <th style='text-align: center;'>2PTA</th>
                <th style='text-align: center;'>2PT%</th>
                <th style='text-align: center;'>3PTM</th>
                <th style='text-align: center;'>3PTA</th>
                <th style='text-align: center;'>3PT%</th>
                <th style='text-align: center;'>FGM</th>
                <th style='text-align: center;'>FGA</th>
                <th style='text-align: center;'>eFG%</th>
                <th style='text-align: center;'>PTS</th>
                <th style='text-align: center;'>PPS</th>
                <th style='text-align: center;'>TPT%</th>
                <th style='text-align: center;'>TST%</th>
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
            <tr style='background:{row_colour};'>
                <td style='text-align: left; font-weight: bold;'>{display_qual}</td>
                <td style='text-align: center;'>{twopm}</td>
                <td style='text-align: center;'>{twopa}</td>
                <td style='text-align: center;'>{p2:.1f}%</td>
                <td style='text-align: center;'>{threepm}</td>
                <td style='text-align: center;'>{threepa}</td>
                <td style='text-align: center;'>{p3:.1f}%</td>
                <td style='text-align: center;'>{fgm}</td>
                <td style='text-align: center;'>{fga}</td>
                <td style='text-align: center;'>{efg:.1f}%</td>
                <td style='text-align: center;'>{pts}</td>
                <td style='text-align: center;'>{pps:.2f}</td>
                <td style='text-align: center;'>{tpt_pct:.1f}%</td>
                <td style='text-align: center;'>{tst_pct:.1f}%</td>
            </tr>
            """

            total_2pm += twopm
            total_2pa += twopa
            total_3pm += threepm
            total_3pa += threepa
            total_fgm += fgm
            total_fga += fga
            total_pts += pts

            row_colour = "#e6f0ff;" if row_colour == "#FFFFFF;" else "#FFFFFF;"

        # Totals row
        total_p2 = round((total_2pm / total_2pa * 100), 1) if total_2pa > 0 else 0.0
        total_p3 = round((total_3pm / total_3pa * 100), 1) if total_3pa > 0 else 0.0
        total_efg = round(((total_fgm + 0.5 * total_3pm) / total_fga * 100), 1) if total_fga > 0 else 0.0
        total_pps = round(total_pts / total_fga, 2) if total_fga > 0 else 0.00

        player_shot_quality_table += f"""
            <tr style='font-weight:bold; background:#d0e0ff;    '>
                <td style='text-align: left;'>Total:</td>
                <td style='text-align: center;'>{total_2pm}</td>
                <td style='text-align: center;'>{total_2pa}</td>
                <td style='text-align: center;'>{total_p2:.1f}%</td>
                <td style='text-align: center;'>{total_3pm}</td>
                <td style='text-align: center;'>{total_3pa}</td>
                <td style='text-align: center;'>{total_p3:.1f}%</td>
                <td style='text-align: center;'>{total_fgm}</td>
                <td style='text-align: center;'>{total_fga}</td>
                <td style='text-align: center;'>{total_efg:.1f}%</td>
                <td style='text-align: center;'>{total_pts}</td>
                <td style='text-align: center;'>{total_pps:.2f}</td>
                <td style='text-align: center;'>100%</td>
                <td style='text-align: center;'>100%</td>
            </tr>
        </table>
        """

        report += player_shot_quality_table
        report += loc_table

        report += f"<p>Powered by CourtTag v{VERSION} (c) 2026 </p>"

        conn.close()
        return report

    def add_team(self):
        dialog = TeamEditorDialog(self)
        if dialog.exec_() == QDialog.Accepted:
            self.load_teams()

    def edit_team(self):
        selected = self.teams_table.selectedItems()
        if not selected:
            QMessageBox.warning(self, "Error", "Select a team first.")
            return

        row = selected[0].row()
        team_id_item = self.teams_table.item(row, 1)  # ← CHANGE TO COLUMN 1 (Team Name)
        if not team_id_item:
            QMessageBox.warning(self, "Error", "No team data in selected row.")
            return

        team_id = team_id_item.data(Qt.UserRole)
        #print("Edit Team - team_id from column 1:", team_id)  # debug

        if not team_id:
            QMessageBox.warning(self, "Error", "Team ID not found.")
            return

        dialog = TeamEditorDialog(self, team_id=team_id)
        if dialog.exec_() == QDialog.Accepted:
            self.load_teams()

    def delete_team(self):
        selected = self.teams_table.selectedItems()
        if not selected:
            return
        row = selected[0].row()
        team_name = self.teams_table.item(row, 1).text()  # for nice message
        team_id = self.teams_table.item(row, 1).data(Qt.UserRole)  # adjust column if needed

        reply = QMessageBox.question(
            self,
            "Delete Team",
            f"Delete '{team_name}' and ALL related data (players, games, rosters, videos, events)?\nThis cannot be undone.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return

        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()

        try:
            c.execute("PRAGMA foreign_keys = ON;")  # enforce constraints

            # Delete deepest first
            c.execute("""
                      DELETE
                      FROM events
                      WHERE video_id IN (SELECT v.id
                                         FROM videos v
                                                  JOIN games g ON v.game_id = g.id
                                         WHERE g.home_team_id = ?
                                            OR g.guest_team_id = ?)
                      """, (team_id, team_id))

            c.execute("""
                      DELETE
                      FROM videos
                      WHERE game_id IN (SELECT id
                                        FROM games
                                        WHERE home_team_id = ?
                                           OR guest_team_id = ?)
                      """, (team_id, team_id))

            c.execute("""
                      DELETE
                      FROM game_rosters
                      WHERE game_id IN (SELECT id
                                        FROM games
                                        WHERE home_team_id = ?
                                           OR guest_team_id = ?)
                      """, (team_id, team_id))

            deleted_games = c.execute("""
                                      DELETE
                                      FROM games
                                      WHERE home_team_id = ?
                                         OR guest_team_id = ?
                                      """, (team_id, team_id)).rowcount

            deleted_players = c.execute("DELETE FROM players WHERE team_id = ?", (team_id,)).rowcount

            deleted_teams = c.execute("DELETE FROM teams WHERE id = ?", (team_id,)).rowcount

            conn.commit()

            msg = f"Deleted:\n- {deleted_teams} team\n- {deleted_players} players\n- {deleted_games} games"
            QMessageBox.information(self, "Success", msg)

            # Refresh season combos filters
            self.refresh_all_season_filters()
            self.refresh_team_combos()

            self.load_teams()

        except sqlite3.IntegrityError as e:
            conn.rollback()
            QMessageBox.critical(self, "Cannot Delete",
                                 f"Foreign key constraint failed:\n{str(e)}\n\n"
                                 "There are still records (games/rosters/videos/events) referencing this team.\n"
                                 "Manual cleanup may be needed in DB Browser.")
        except Exception as e:
            conn.rollback()
            QMessageBox.critical(self, "Delete Error", str(e))
        finally:
            conn.close()


    def add_game(self):
        dialog = GameEditorDialog(self)
        if dialog.exec_() == QDialog.Accepted:
            self.load_games()

    def edit_game(self):
        selected = self.games_table.selectedItems()
        if not selected:
            QMessageBox.warning(self, "Error", "Select a game first.")
            return
        row = selected[0].row()
        game_id = self.games_table.item(row, 0).data(Qt.UserRole)
        dialog = GameEditorDialog(self, game_id=game_id)
        if dialog.exec_() == QDialog.Accepted:
            self.load_games()

    def delete_game(self):
        selected = self.games_table.selectedItems()
        if not selected:
            QMessageBox.warning(self, "Error", "Select a game first.")
            return
        row = selected[0].row()
        game_id = self.games_table.item(row, 0).data(Qt.UserRole)

        reply = QMessageBox.question(self, "Delete Game",
                                     "Delete game, videos, events?",
                                     QMessageBox.Yes | QMessageBox.No)
        if reply == QMessageBox.Yes:
            conn = sqlite3.connect(DB_NAME)
            c = conn.cursor()
            c.execute("SELECT id FROM videos WHERE game_id = ?", (game_id,))
            video_ids = [r[0] for r in c.fetchall()]
            if video_ids:
                placeholders = ','.join('?' for _ in video_ids)
                c.execute(f"DELETE FROM events WHERE video_id IN ({placeholders})", video_ids)
            c.execute("DELETE FROM videos WHERE game_id = ?", (game_id,))
            c.execute("DELETE FROM game_rosters WHERE game_id = ?", (game_id,))
            c.execute("DELETE FROM games WHERE id = ?", (game_id,))
            conn.commit()
            conn.close()
            self.load_games()

    def edit_videos(self):
        selected = self.games_table.selectedItems()
        if not selected:
            QMessageBox.warning(self, "Error", "Select a game first.")
            return
        row = selected[0].row()
        game_id = self.games_table.item(row, 0).data(Qt.UserRole)
        dialog = GameVideosDialog(self, game_id)
        dialog.exec_()

    def get_game_scores(self, game_id: int):
        """
        Calculate home and guest scores for a game.
        - Uses player_id → game_rosters.side when available
        - Falls back to home for events without player_id or missing roster entry
        Returns (home_score, guest_score)
        """
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()

        try:
            query = """
                SELECT 
                    SUM(CASE 
                        WHEN COALESCE(gr.side, 'home') = 'home' THEN
                            CASE e.type
                                WHEN '2PM 2PT Shot Made' THEN 2
                                WHEN '3PM 3PT Shot Made' THEN 3
                                WHEN 'FTM Freethrow Made' THEN 1
                                ELSE 0
                            END
                        ELSE 0
                    END) AS home_score,

                    SUM(CASE 
                        WHEN COALESCE(gr.side, 'home') = 'guest' THEN
                            CASE e.type
                                WHEN '2PM 2PT Shot Made' THEN 2
                                WHEN '3PM 3PT Shot Made' THEN 3
                                WHEN 'FTM Freethrow Made' THEN 1
                                ELSE 0
                            END
                        ELSE 0
                    END) AS guest_score
                FROM events e
                JOIN videos v ON e.video_id = v.id
                LEFT JOIN game_rosters gr ON gr.game_id = v.game_id AND gr.player_id = e.player_id
                WHERE v.game_id = ?
            """
            c.execute(query, (game_id,))
            row = c.fetchone()

            home = int(row[0]) if row and row[0] is not None else 0
            guest = int(row[1]) if row and row[1] is not None else 0

            #print(f"Game {game_id} scores calculated: Home {home} - Guest {guest}")

            return home, guest

        except Exception as e:
            print(f"Score calc error for game {game_id}: {e}")
            return 0, 0
        finally:
            conn.close()

    def load_games(self):
        sort_col = self.games_table.horizontalHeader().sortIndicatorSection()
        sort_order = self.games_table.horizontalHeader().sortIndicatorOrder()

        # === FIXED FILTER LOGIC ===
        season_filter = self.game_season_combo.currentText().strip()
        team_filter_id = self.team_filter_combo.currentData()

        # Normalize "All" selections
        if season_filter in ("All Seasons", "All", ""):
            season_filter = None

        # "All Teams" selected
        if (self.team_filter_combo.currentIndex() == 0 or
                team_filter_id is None or
                str(team_filter_id).strip() == ""):
            team_filter_id = None

        self.games_table.setSortingEnabled(False)
        self.games_table.setRowCount(0)

        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()

        query = """
                    SELECT 
                        g.id, g.date, g.name, g.location, 
                        th.name AS home_team, tg.name AS guest_team,
                        g.is_complete, g.gamesheet_path, g.youtube_url, 
                        COALESCE((
                            SELECT SUM(CASE 
                                WHEN e.team_side = 'home' OR e.team_side IS NULL THEN
                                    CASE e.type
                                        WHEN '2PM 2PT Shot Made' THEN 2
                                        WHEN '3PM 3PT Shot Made' THEN 3
                                        WHEN 'FTM Freethrow Made' THEN 1
                                        ELSE 0
                                    END
                                ELSE 0
                            END)
                            FROM events e JOIN videos v ON e.video_id = v.id
                            WHERE v.game_id = g.id
                        ), 0) AS home_score,
                        COALESCE((
                            SELECT SUM(CASE 
                                WHEN e.team_side = 'guest' THEN
                                    CASE e.type
                                        WHEN '2PM 2PT Shot Made' THEN 2
                                        WHEN '3PM 3PT Shot Made' THEN 3
                                        WHEN 'FTM Freethrow Made' THEN 1
                                        ELSE 0
                                    END
                                ELSE 0
                            END)
                            FROM events e JOIN videos v ON e.video_id = v.id
                            WHERE v.game_id = g.id
                        ), 0) AS guest_score
                    FROM games g
                    LEFT JOIN teams th ON g.home_team_id = th.id
                    LEFT JOIN teams tg ON g.guest_team_id = tg.id
                """
        params = []
        where_clauses = []

        # Season filter
        if season_filter:
            where_clauses.append("(th.season = ? OR tg.season = ?)")
            params.extend([season_filter, season_filter])

        # Team filter
        if team_filter_id is not None:
            where_clauses.append("(g.home_team_id = ? OR g.guest_team_id = ?)")
            params.extend([team_filter_id, team_filter_id])

        if where_clauses:
            query += " WHERE " + " AND ".join(where_clauses)

        query += " ORDER BY g.date DESC"

        c.execute(query, params)
        rows = c.fetchall()
        conn.close()

        for row in rows:
            game_id, date, name, loc, home, guest, is_complete, sheet_path, youtube_url, home_score, guest_score = row

            r = self.games_table.rowCount()
            self.games_table.insertRow(r)

            # Calculate scores using your existing function
            home_score, guest_score = self.get_game_scores(game_id)

            # Column 0: Date (with game_id hidden in UserRole)
            date_item = QTableWidgetItem(date or "")
            date_item.setData(Qt.UserRole, game_id)
            self.games_table.setItem(r, 0, date_item)

            # Column 1: Game Name
            self.games_table.setItem(r, 1, QTableWidgetItem(name or ""))

            # Column 2: Location
            self.games_table.setItem(r, 2, QTableWidgetItem(loc or ""))

            # Column 3: Home Team
            h_team = QTableWidgetItem(home or "Unknown")
            self.games_table.setItem(r, 3, h_team)

            # Column 4: Guest Team
            g_team = QTableWidgetItem(guest or "Unknown")
            self.games_table.setItem(r, 4, g_team)

            # Column 5: Home Score
            h_item = QTableWidgetItem(str(home_score))
            h_item.setTextAlignment(Qt.AlignCenter)
            self.games_table.setItem(r, 5, h_item)

            # Column 6: Guest Score
            g_item = QTableWidgetItem(str(guest_score))
            g_item.setTextAlignment(Qt.AlignCenter)
            self.games_table.setItem(r, 6, g_item)

            # Status moves to column 7
            if is_complete:
                status_text = "Complete ✓"
                fg_color = QColor(0, 128, 0)  # dark green text
                bg_color = QColor(235, 255, 235)  # very light green background
            else:
                status_text = "In Progress"
                fg_color = QColor(180, 100, 0)  # orange text
                bg_color = QColor(255, 245, 220)  # light orange background

            status_item = QTableWidgetItem(status_text)
            status_item.setForeground(fg_color)
            #status_item.setBackground(bg_color)
            status_item.setTextAlignment(Qt.AlignCenter)
            status_item.setFont(QFont("Segoe UI", 10, QFont.Bold))  # optional: make it stand out

            self.games_table.setItem(r, 7, status_item)

            sheet_item = QTableWidgetItem()

            if sheet_path: #and Path(sheet_path).is_file():
                # Has gamesheet → show emoji + tooltip
                sheet_item.setText("📋")  # ← or 📄 or 📝
                sheet_item.setTextAlignment(Qt.AlignCenter)
                sheet_item.setToolTip(f"Open gamesheet:\n{Path(sheet_path).name}")
                sheet_item.setData(Qt.UserRole, str(sheet_path))  # store path for click
                sheet_item.setForeground(QColor("#006600"))  # optional: dark green = present
            else:
                # No sheet or file missing
                sheet_item.setText("—")  # em dash or empty ""
                sheet_item.setTextAlignment(Qt.AlignCenter)
                sheet_item.setForeground(QColor(160, 160, 160))  # gray = absent
                sheet_item.setToolTip("No gamesheet attached")

            self.games_table.setItem(r, 8, sheet_item)

            # === NEW: YouTube Column ===
            yt_item = QTableWidgetItem()
            yt_item.setText("▶️")
            yt_item.setTextAlignment(Qt.AlignCenter)
            if youtube_url:
                yt_item.setText("▶️")
                font = QFont("Segoe UI", 13, QFont.Bold)
                yt_item.setFont(font)
                # Add border + padding effect
                yt_item.setData(Qt.UserRole + 10, "yt_icon")  # optional marker
                yt_item.setForeground(QColor("#FFFFFF"))
                yt_item.setBackground(QColor("#FF0000"))  # YouTube Red
            else:
                yt_item.setText("—")

            self.games_table.setItem(r, 9, yt_item)

            # === CRITICAL: Store game_id on EVERY cell in this row ===
            for col in range(10):
                cell = self.games_table.item(r, col)
                if cell:
                    cell.setData(Qt.UserRole, game_id)

            if is_complete:
                if home_score > guest_score:
                    h_item.setForeground(QColor(0, 128, 0))  # green
                    h_team.setForeground(QColor(0, 128, 0))  #  green
                    #h_item.setFont(QFont("Segoe UI", 10, QFont.Bold))
                    #h_team.setFont(QFont("Segoe UI", 10, QFont.Bold))
                    g_item.setForeground(QColor(232, 2, 2))  # red
                    g_team.setForeground(QColor(232, 2, 2))  # red

                elif guest_score > home_score:
                    h_item.setForeground(QColor(232, 2, 2))
                    h_team.setForeground(QColor(232, 2, 2))
                    g_item.setForeground(QColor(0, 128, 0))
                    g_team.setForeground(QColor(0, 128, 0))
                    #g_item.setFont(QFont("Segoe UI", 10, QFont.Bold))
                    #_team.setFont(QFont("Segoe UI", 10, QFont.Bold))

                elif home_score == guest_score and home_score > 0:
                    h_item.setForeground(QColor(254, 161, 24))  # orange tie
                    h_team.setForeground(QColor(254, 161, 24))  # orange tie
                    g_item.setForeground(QColor(254, 161, 24))
                    g_team.setForeground(QColor(254, 161, 24))

        self.games_table.setSortingEnabled(True)



        # Restore previous sort (or default to date descending)
        if sort_col >= 0 and sort_col < self.games_table.columnCount():
            self.games_table.sortItems(sort_col, sort_order)
        else:
            self.games_table.sortItems(0, Qt.DescendingOrder)

    def load_teams(self):
        sort_col = self.teams_table.horizontalHeader().sortIndicatorSection()
        sort_order = self.teams_table.horizontalHeader().sortIndicatorOrder()

        season_filter = self.team_season_combo.currentText().strip()
        if season_filter == "All":
            season_filter = None

        self.teams_table.setSortingEnabled(False)
        self.teams_table.setRowCount(0)
        self.teams_table.setCursor(Qt.PointingHandCursor)

        conn = sqlite3.connect(DB_NAME)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()

        query = """
        WITH game_scores AS (
            SELECT 
                g.id AS game_id,
                g.date,
                g.is_complete,
                g.home_team_id,
                g.guest_team_id,
                COALESCE(SUM(
                    CASE 
                        -- Priority 1: Player is known → use player's team
                        WHEN p.team_id = g.home_team_id THEN
                            CASE 
                                WHEN e.type LIKE '2PM%' THEN 2
                                WHEN e.type LIKE '3PM%' THEN 3
                                WHEN e.type LIKE 'FTM%' THEN 1
                                ELSE 0
                            END

                        -- Priority 2: No player, but team_side indicates home
                        WHEN p.team_id IS NULL 
                             AND e.team_side IN ('home', 'Home', 'H', 'HOME', '1')  -- ← customize these values!
                        THEN
                            CASE 
                                WHEN e.type LIKE '2PM%' THEN 2
                                WHEN e.type LIKE '3PM%' THEN 3
                                WHEN e.type LIKE 'FTM%' THEN 1
                                ELSE 0
                            END

                        ELSE 0
                    END
                ), 0) AS home_points,

                COALESCE(SUM(
                    CASE 
                        -- Priority 1: Player is known → use player's team
                        WHEN p.team_id = g.guest_team_id THEN
                            CASE 
                                WHEN e.type LIKE '2PM%' THEN 2
                                WHEN e.type LIKE '3PM%' THEN 3
                                WHEN e.type LIKE 'FTM%' THEN 1
                                ELSE 0
                            END

                        -- Priority 2: No player, but team_side indicates guest/away
                        WHEN p.team_id IS NULL 
                             AND e.team_side IN ('guest', 'Guest', 'G', 'AWAY', 'A', 'away', '0')  -- ← customize these values!
                        THEN
                            CASE 
                                WHEN e.type LIKE '2PM%' THEN 2
                                WHEN e.type LIKE '3PM%' THEN 3
                                WHEN e.type LIKE 'FTM%' THEN 1
                                ELSE 0
                            END

                        ELSE 0
                    END
                ), 0) AS guest_points
            FROM games g
            LEFT JOIN videos v ON v.game_id = g.id
            LEFT JOIN events e ON e.video_id = v.id
            LEFT JOIN players p ON e.player_id = p.id
            WHERE g.is_complete = 1
            GROUP BY g.id, g.date, g.home_team_id, g.guest_team_id
        )
        SELECT 
            t.id,
            t.name,
            t.season,
            COUNT(DISTINCT gs.game_id) AS GP,
            SUM(CASE 
                WHEN gs.home_team_id = t.id AND gs.home_points > gs.guest_points THEN 1
                WHEN gs.guest_team_id = t.id AND gs.guest_points > gs.home_points THEN 1
                ELSE 0 
            END) AS W,
            SUM(CASE 
                WHEN gs.home_team_id = t.id AND gs.home_points < gs.guest_points THEN 1
                WHEN gs.guest_team_id = t.id AND gs.guest_points < gs.home_points THEN 1
                ELSE 0 
            END) AS L,
            SUM(CASE 
                WHEN gs.home_team_id = t.id AND gs.home_points = gs.guest_points THEN 1
                WHEN gs.guest_team_id = t.id AND gs.guest_points = gs.home_points THEN 1
                ELSE 0 
            END) AS T,
            ROUND(
                COALESCE(
                    SUM(CASE 
                        WHEN gs.home_team_id = t.id THEN gs.home_points
                        WHEN gs.guest_team_id = t.id THEN gs.guest_points 
                    END), 0) * 1.0 / 
                    NULLIF(COUNT(DISTINCT gs.game_id), 0), 1
            ) AS PPG,
            ROUND(
                COALESCE(
                    SUM(CASE 
                        WHEN gs.home_team_id = t.id THEN gs.guest_points
                        WHEN gs.guest_team_id = t.id THEN gs.home_points 
                    END), 0) * 1.0 / 
                    NULLIF(COUNT(DISTINCT gs.game_id), 0), 1
            ) AS OPPG,
            MAX(gs.date) AS last_game
        FROM teams t
        LEFT JOIN game_scores gs ON gs.home_team_id = t.id OR gs.guest_team_id = t.id
        """

        params = []
        if season_filter:
            query += " WHERE t.season = ?"
            params.append(season_filter)

        query += """
        GROUP BY t.id, t.name, t.season
        ORDER BY t.season DESC, t.name
        """

        try:
            c.execute(query, params)
            rows = c.fetchall()

            for row in rows:
                r = self.teams_table.rowCount()
                self.teams_table.insertRow(r)

                team_id = row['id']
                season = row['season'] or ""
                name = row['name'] or "Unnamed"
                gp = row['GP'] or 0
                w = row['W'] or 0
                l = row['L'] or 0
                t = row['T'] or 0
                ppg = row['PPG'] or 0.0
                oppg = row['OPPG'] or 0.0
                last_game = row['last_game'] if row['last_game'] else "No Games"

                # ==================== TEAM ABR CALCULATION ====================
                team_abr = 0.0
                if gp > 0:
                    conn_abr = sqlite3.connect(DB_NAME)
                    c_abr = conn_abr.cursor()
                    c_abr.execute("""
                        SELECT 
                            SUM(CASE WHEN e.shot_quality IN ('A','B') THEN 1 ELSE 0 END) as ab_shots,
                            COUNT(*) as total_shots
                        FROM events e
                        JOIN videos v ON e.video_id = v.id
                        JOIN games g ON v.game_id = g.id
                        LEFT JOIN game_rosters gr ON gr.game_id = g.id AND gr.player_id = e.player_id
                        WHERE (g.home_team_id = ? OR g.guest_team_id = ?)
                          AND g.is_complete = 1
                          AND (
                                (gr.side = CASE WHEN g.home_team_id = ? THEN 'home' ELSE 'guest' END)
                                OR 
                                (e.player_id IS NULL AND e.team_side = CASE WHEN g.home_team_id = ? THEN 'home' ELSE 'guest' END)
                          )
                          AND e.shot_quality IS NOT NULL
                          AND (e.type LIKE '2P%' OR e.type LIKE '3P%')
                    """, (team_id, team_id, team_id, team_id))
                    abr_row = c_abr.fetchone()
                    conn_abr.close()

                    if abr_row and abr_row[1] > 0:
                        ab_shots = abr_row[0] or 0
                        total_shots = abr_row[1]
                        team_abr = (ab_shots / total_shots * 100)

                abr_text = f"{team_abr:.1f}%" if gp > 0 else "—"
                abr_item = SortableNumericItem(abr_text, team_abr)
                abr_item.setTextAlignment(Qt.AlignCenter | Qt.AlignVCenter)
                # ============================================================


                # Populate columns
                self.teams_table.setItem(r, 0, QTableWidgetItem(season))
                team_name_item = QTableWidgetItem(name)
                team_name_item.setData(Qt.UserRole, team_id)
                self.teams_table.setItem(r, 1, team_name_item)

                self.teams_table.setItem(r, 2, QTableWidgetItem(str(gp)))
                self.teams_table.setItem(r, 3, QTableWidgetItem(str(w)))
                self.teams_table.setItem(r, 4, QTableWidgetItem(str(l)))
                self.teams_table.setItem(r, 5, QTableWidgetItem(str(t)))

                ppg_item = QTableWidgetItem(f"{ppg:.1f}")
                oppg_item = QTableWidgetItem(f"{oppg:.1f}")
                self.teams_table.setItem(r, 6, ppg_item)
                self.teams_table.setItem(r, 7, oppg_item)
                self.teams_table.setItem(r, 8, abr_item)  # ← NEW ABR column

                last_item = QTableWidgetItem(last_game)
                last_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.teams_table.setItem(r, 9, last_item)

                # Center numeric columns
                for col in [2, 3, 4, 5, 6, 7, 8]:
                    item = self.teams_table.item(r, col)
                    if item:
                        item.setTextAlignment(Qt.AlignCenter | Qt.AlignVCenter)

        except sqlite3.Error as e:
            from PyQt5.QtWidgets import QMessageBox
            QMessageBox.critical(self, "Database Error", f"Cannot load teams:\n{str(e)}")

        finally:
            conn.close()

        self.teams_table.setSortingEnabled(True)

        if sort_col >= 0 and sort_col < self.teams_table.columnCount():
            self.teams_table.sortItems(sort_col, sort_order)
        else:
            self.teams_table.sortItems(0, Qt.DescendingOrder)

    def get_team_name(self, team_id):
        if not team_id:
            return "Unknown"
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT name FROM teams WHERE id = ?", (team_id,))
        name = c.fetchone()
        conn.close()
        return name[0] if name else "Unknown"

    def show_game_report(self, game_id):
        report = self.generate_game_report(game_id)
        if not report:
            QMessageBox.warning(self, "No Report", "Could not generate report.")
            return

        dialog = QDialog(self)
        dialog.setWindowTitle("Game Report")
        dialog.setWindowFlags(
            dialog.windowFlags()  # Keep existing flags (including Qt.Dialog)
            | Qt.WindowMaximizeButtonHint
            | Qt.WindowMinimizeButtonHint  # Optional but usually wanted for symmetry
        )
        dialog.setGeometry(200, 200, 1200, 800)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        text_edit = QTextBrowser()
        text_edit.setOpenExternalLinks(True)  # ← This is important!

        #text_edit = QTextEdit()
        text_edit.setHtml(report)
        text_edit.setReadOnly(True)
        layout.addWidget(text_edit, stretch=1)

        # Button row: Copy left, Print right
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()  # push buttons to right

        # Copy Report button (new)
        copy_btn = QPushButton("Copy Report")
        copy_btn.setFixedWidth(140)
        copy_btn.clicked.connect(lambda: self.copy_report_to_clipboard(report))
        btn_layout.addWidget(copy_btn)

        # Print button
        print_btn = QPushButton("Print Report")
        print_btn.setFixedWidth(140)
        print_btn.clicked.connect(lambda: self.print_report(text_edit))
        btn_layout.addWidget(print_btn)

        close_btn = QPushButton("Close Report")
        close_btn.setFixedWidth(140)
        close_btn.clicked.connect(dialog.accept)
        btn_layout.addWidget(close_btn)

        layout.addLayout(btn_layout)

        dialog.exec_()

    def copy_report_to_clipboard(self, report_html):
        """Copy the report HTML to clipboard"""
        clipboard = QApplication.clipboard()
        clipboard.setText(report_html)  # copies as plain text (most apps can paste)

        # Optional: also copy as rich text (HTML) so formatting is preserved in Word/Email
        mime = QMimeData()
        mime.setHtml(report_html)
        mime.setText(report_html)  # fallback plain text
        clipboard.setMimeData(mime)

        QMessageBox.information(self, "Copied", "Report copied to clipboard!\nPaste into email, Word, or Notes.")

    def on_game_double_click(self, row, column):
        game_id = self.games_table.item(row, 0).data(Qt.UserRole)  # adjust column if needed
        if game_id:
            self.show_game_report(game_id)

    def view_game_report(self):
        selected = self.games_table.selectedItems()
        if not selected:
            QMessageBox.warning(self, "No Selection", "Please select a game row first.")
            return
        row = selected[0].row()
        game_id = self.games_table.item(row, 0).data(Qt.UserRole)
        if game_id:
            self.show_game_report(game_id)

    def print_report(self, text_edit):
        printer = QPrinter(QPrinter.HighResolution)
        dialog = QPrintDialog(printer, self)
        dialog.setWindowTitle("Print Report")
        if dialog.exec_() == QDialog.Accepted:
            text_edit.print_(printer)

    def on_team_double_click(self, row, column):
        team_id_item = self.teams_table.item(row, 1)  # same column as above
        if not team_id_item:
            return
        team_id = team_id_item.data(Qt.UserRole)
        self.open_team_report(team_id)

    def view_gamesheet_popup(self, game_id):
        """Open a non-modal gamesheet viewer popup for the selected game"""
        if game_id is None:
            QMessageBox.information(self, "Error", "No game selected.")
            return

        # Fetch gamesheet_path, name, and location from DB
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute(
            "SELECT gamesheet_path, name, location FROM games WHERE id = ?",
            (game_id,)
        )
        result = c.fetchone()
        conn.close()

        if result is None:
            QMessageBox.information(self, "Game Not Found", "No game record found.")
            return

        rel_path, game_name, game_location = result

        if not rel_path:
            QMessageBox.information(
                self,
                "No Gamesheet",
                "No gamesheet associated with this game."
            )
            return

        # Resolve full path with fallback
        full_path = str(Path.home() / "Documents" / "CourtTag" / "Gamesheets" / rel_path)

        try:
            os.startfile(full_path)  # Windows default viewer (Photos, etc.)
            # or: subprocess.call(["start", "", full_path], shell=True)
        except Exception as e:
            QMessageBox.warning(self, "Open Failed", f"Could not open in viewer:\n{e}")

    def view_team_report(self):
        selected = self.teams_table.selectedItems()
        if not selected:
            QMessageBox.warning(self, "Error", "Select a team first.")
            return
        row = selected[0].row()
        team_id = self.teams_table.item(row, 3).data(Qt.UserRole)
        report = self.generate_team_report(team_id)
        dialog = QDialog()
        dialog.setWindowTitle("Team Report")

        # This is the key block: Customize + pick buttons explicitly
        dialog.setWindowFlags(
            Qt.Dialog |  # Keep modal/dialog behavior
            Qt.CustomizeWindowHint |  # We control the hints now
            Qt.WindowTitleHint |  # Title bar text
            Qt.WindowCloseButtonHint |  # X button
            Qt.WindowMaximizeButtonHint |  # Maximize/restore
            Qt.WindowMinimizeButtonHint  # Minimize (optional — remove if unwanted)
        )

        # Extra insurance (some Qt versions/platforms need it)
        dialog.setWindowFlag(Qt.WindowContextHelpButtonHint, False)

        dialog.setGeometry(100, 100, 1400, 800)
        #text_edit = QTextEdit()
        text_edit = QTextBrowser()
        text_edit.setOpenExternalLinks(True)  # ← Critical
        text_edit.setHtml(report)
        text_edit.setReadOnly(True)
        layout = QVBoxLayout(dialog)
        layout.addWidget(text_edit)
        dialog.exec_()

    def generate_game_report(self, game_id):
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()

        c.execute("SELECT name, date, location, format, home_team_id, guest_team_id FROM games WHERE id = ?",
                  (game_id,))
        game_row = c.fetchone()
        if not game_row:
            conn.close()
            return "<h2>Game not found.</h2>"
        name, date, location, format_, home_id, guest_id = game_row
        home_name = self.get_team_name(home_id)
        guest_name = self.get_team_name(guest_id)

        format_ = (format_ or "Q").upper()

        # Define periods
        if format_ == "H":
            period_order = ["H1", "H2", "OT1", "OT2"]
            period_labels = {"H1": "1st Half", "H2": "2nd Half", "OT1": "OT1", "OT2": "OT2"}
        else:
            period_order = ["Q1", "Q2", "Q3", "Q4", "OT1", "OT2"]
            period_labels = {"Q1": "Q1", "Q2": "Q2", "Q3": "Q3", "Q4": "Q4", "OT1": "OT1", "OT2": "OT2"}

        report = f"""
        <h2>Game Report: {name} - {home_name} (Home) vs {guest_name} (Guest)</h2>
        <p><b>Date:</b> {date} | <b>Location:</b> {location or '—'} | <b>Format:</b> {format_}</p>
        """

        query = """
            SELECT 
                e.type, 
                e.location, 
                e.quarter, 
                e.player_id, 
                e.team_side, 
                p.number, 
                p.name,
                COALESCE(gr.side, e.team_side) AS effective_side,
                e.shot_quality
            FROM events e
            JOIN videos v ON e.video_id = v.id
            LEFT JOIN players p ON e.player_id = p.id
            LEFT JOIN game_rosters gr 
                ON gr.game_id = v.game_id 
               AND gr.player_id = e.player_id
            WHERE v.game_id = ?
        """
        c.execute(query, (game_id,))
        events = c.fetchall()


        # Count by type for the whole game
        two_pa_all = sum(1 for e in events if e[0] and str(e[0]).startswith("2PA"))
        three_pa_all = sum(1 for e in events if e[0] and str(e[0]).startswith("3PA"))

        # Count only Home team events (using effective_side)
        home_two_pa = sum(1 for e in events if e[7] == 'home' and e[0] and str(e[0]).startswith("2PA"))  # e[7] is effective_side
        home_three_pa = sum(1 for e in events if e[7] == 'home' and e[0] and str(e[0]).startswith("3PA"))

        conn.close()

        stat_keys = ['2PTM', '2PTA', '3PTM', '3PTA', 'FTM', 'FTA', 'ORB', 'DREB', 'PFL', 'AST', 'TO', 'STL', 'BLK',
                     'CHG', 'DFL', 'ABR']

        def normalize_period(q):
            if not q: return "Q1"
            q = q.strip().upper()
            if "1ST HALF" in q or q in ("H1", "1"): return "H1"
            if "2ND HALF" in q or q in ("H2", "2"): return "H2"
            if q.startswith("Q"): return q[:2]
            if q.startswith("OT"): return q[:3]
            return q  # fallback

        def team_stats(events, target_side, team_name):
            player_stats = {}
            location_counts = {}
            quarter_stats = {p: {k: 0 for k in stat_keys} for p in period_order}

            team_ab_shots = 0  # ← ADD
            team_fga_total = 0  # ← ADD

            for event in events:
                e_type, loc, q, p_id, t_side, num, p_name, effective_side, shot_quality = event
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
                if e_type.startswith(('2PM', '3PM')) and shot_quality in ('A', 'B'):
                    stats['ABR'] += 1
                    if q_norm in quarter_stats:
                        quarter_stats[q_norm]['ABR'] += 1

                if loc and loc != '-' and e_type.startswith(('2PA', '2PM', '3PA', '3PM')):
                    if loc not in location_counts:
                        location_counts[loc] = {}
                    short_code = e_type[:3]
                    location_counts[loc][short_code] = location_counts[loc].get(short_code, 0) + 1
                    if shot_quality in ('A', 'B'):
                        location_counts[loc]['AB'] = location_counts[loc].get('AB', 0) + 1

            def sort_key(item):
                key, stats = item
                if key == 'unknown':
                    return (999999, '')
                num_str = str(stats['number'] or '').strip()
                if num_str in ('0', '00', ''):
                    return (0, stats['name'])
                try:
                    num_val = int(num_str)
                    return (num_val + 1, stats['name'])
                except:
                    return (999998, stats['name'])

            sorted_players = sorted(player_stats.items(), key=sort_key)

            player_table = (
                "<table style='text-align:left; width:100%;'>"
                "<tr style='font-weight:bold; background:#75c875;'>"
                "<th>P#</th>"
                "<th>Player Name</th>"
                "<th style='text-align: center;'>2PTM</th>"
                "<th style='text-align: center;'>2PTA</th>"
                "<th style='text-align: center;'>2PT%</th>"
                "<th style='text-align: center;'>3PTM</th>"
                "<th style='text-align: center;'>3PTA</th>"
                "<th style='text-align: center;'>3PT%</th>"
                "<th style='text-align: center;'>eFG%</th>"
                "<th style='text-align: center;'>FTM</th>"
                "<th style='text-align: center;'>FTA</th>"
                "<th style='text-align: center;'>FT%</th>"
                "<th style='text-align: center;'>FTF</th>"
                "<th style='text-align: center;'>PTS</th>"
                "<th style='text-align: center;'>ORB</th>"
                "<th style='text-align: center;'>DREB</th>"
                "<th style='text-align: center;'>TREB</th>"
                "<th style='text-align: center;'>PFL</th>"
                "<th style='text-align: center;'>AST</th>"
                "<th style='text-align: center;'>TO</th>"
                "<th style='text-align: center;'>STL</th>"
                "<th style='text-align: center;'>BLK</th>"
                "<th style='text-align: center;'>CHG</th>"
                "<th style='text-align: center;'>DFL</th>"
                "<th style='text-align: center;'>ATR</th>"
                "<th style='text-align: center;'>ABR</th>"
                "</tr>"
            )
            row_colour = "#FFFFFF;"
            for key, stats in sorted_players:
                num_str = str(stats['number']) if stats['number'] is not None else ''
                num = num_str

                # Define all attempt totals clearly
                fga_2pt = stats['2PTM'] + stats['2PTA']  # Shown in the 2PTA column
                fga_3pt = stats['3PTM'] + stats['3PTA']
                fga_total = fga_2pt + fga_3pt  # Full FGA - used for eFG% and FTF

                fta_total = stats['FTM'] + stats['FTA']

                # Percentages
                p2 = (stats['2PTM'] / fga_2pt * 100) if fga_2pt > 0 else 0.0
                p3 = (stats['3PTM'] / fga_3pt * 100) if fga_3pt > 0 else 0.0
                ft = (stats['FTM'] / fta_total * 100) if fta_total > 0 else 0.0

                # eFG%
                fgm_total = stats['2PTM'] + stats['3PTM']
                efg_pct = (fgm_total + 0.5 * stats['3PTM']) / fga_total * 100 if fga_total > 0 else 0.0

                # Free Throw Factor (FTF) = FTA / FGA  → shown as ratio (e.g. 1.33)
                ftf = (fta_total / fga_total) if fga_total > 0 else 0.0

                pts = (stats['2PTM'] * 2) + (stats['3PTM'] * 3) + stats['FTM']
                treb = stats['ORB'] + stats['DREB']

                # ATR = Assist / Turnover Ratio
                atr = (stats['AST'] / stats['TO']) if stats['TO'] > 0 else 0.0

                # === PLAYER ABR FOR THIS GAME ===
                abr_game = 0.0
                ab_count = 0
                fga_total = fga_2pt + fga_3pt

                if fga_total > 0:
                    ab_count = sum(1 for e in events
                                   if e[3] == key                     # player_id
                                   and e[7] == target_side            # side
                                   and len(e) > 8                     # has shot_quality
                                   and e[8] in ('A', 'B'))            # shot_quality index
                    abr_game = (ab_count / fga_total * 100)
                    team_ab_shots += ab_count
                    team_fga_total += fga_total

                # =================================

                player_table += (
                    f"<tr style='background:{row_colour}'>"
                    f"<td style='text-align: center;'>{num}</td>"
                    f"<td>{stats['name']}</td>"
                    f"<td style='text-align: center;'>{stats['2PTM']}</td>"
                    f"<td style='text-align: center;'>{fga_2pt}</td>"
                    f"<td style='text-align: center;'>{p2:.1f}%</td>"
                    f"<td style='text-align: center;'>{stats['3PTM']}</td>"
                    f"<td style='text-align: center;'>{fga_3pt}</td>"
                    f"<td style='text-align: center;'>{p3:.1f}%</td>"
                    f"<td style='text-align: center;'>{efg_pct:.1f}%</td>"
                    f"<td style='text-align: center;'>{stats['FTM']}</td>"
                    f"<td style='text-align: center;'>{fta_total}</td>"
                    f"<td style='text-align: center;'>{ft:.1f}%</td>"
                    f"<td style='text-align: center;'>{ftf:.2f}</td>"
                    f"<td style='text-align: center;'>{pts}</td>"
                    f"<td style='text-align: center;'>{stats['ORB']}</td>"
                    f"<td style='text-align: center;'>{stats['DREB']}</td>"
                    f"<td style='text-align: center;'>{treb}</td>"
                    f"<td style='text-align: center;'>{stats['PFL']}</td>"
                    f"<td style='text-align: center;'>{stats['AST']}</td>"
                    f"<td style='text-align: center;'>{stats['TO']}</td>"
                    f"<td style='text-align: center;'>{stats['STL']}</td>"
                    f"<td style='text-align: center;'>{stats['BLK']}</td>"
                    f"<td style='text-align: center;'>{stats['CHG']}</td>"
                    f"<td style='text-align: center;'>{stats['DFL']}</td>"
                    f"<td style='text-align: center;'>{atr:.2f}</td>"
                    f"<td style='text-align: center;'>{abr_game:.1f}%</td>"
                    f"</tr>"
                )
                if row_colour == "#FFFFFF;":
                    row_colour = "#DCFFDC;"
                else:
                    row_colour = "#FFFFFF;"

            total = {k: sum(s[k] for s in player_stats.values()) for k in stat_keys}
            total_fga = total['2PTM'] + total['2PTA']
            total_three_a = total['3PTM'] + total['3PTA']
            total_fta = total['FTM'] + total['FTA']

            p2_total = total['2PTM'] / total_fga * 100 if total_fga > 0 else 0.0
            p3_total = total['3PTM'] / total_three_a * 100 if total_three_a > 0 else 0.0
            ft_total = total['FTM'] / total_fta * 100 if total_fta > 0 else 0.0
            pts_total = (total['2PTM'] * 2) + (total['3PTM'] * 3) + total['FTM']
            treb_total = total['ORB'] + total['DREB']

            # Calculate total eFG%
            total_fgm_efg = total['2PTM'] + total['3PTM']
            total_fga_efg = total_fga + total_three_a
            total_efg_pct = (total_fgm_efg + 0.5 * total['3PTM']) / total_fga_efg * 100 if total_fga_efg > 0 else 0.0

            total_fta = total['FTM'] + total['FTA']
            total_fga_efg = total_fga + total_three_a
            total_ftf = (total_fta / total_fga) if total_fga > 0 else 0.0

            # ATR for Totals row
            total_atr = (total['AST'] / total['TO']) if total['TO'] > 0 else 0.0

            team_abr = (team_ab_shots / team_fga_total * 100) if team_fga_total > 0 else 0.0

            player_table += (
                f"<tr style='font-weight:bold; background:#75c875;'>"
                f"<td colspan='2' style='text-align: right;'>Total:</td>"
                f"<td style='text-align: center;'>{total['2PTM']}</td>"
                f"<td style='text-align: center;'>{total_fga}</td>"
                f"<td style='text-align: center;'>{p2_total:.1f}%</td>"
                f"<td style='text-align: center;'>{total['3PTM']}</td>"
                f"<td style='text-align: center;'>{total_three_a}</td>"
                f"<td style='text-align: center;'>{p3_total:.1f}%</td>"
                f"<td style='text-align: center;'>{total_efg_pct:.1f}%</td>"
                f"<td style='text-align: center;'>{total['FTM']}</td>"
                f"<td style='text-align: center;'>{total_fta}</td>"
                f"<td style='text-align: center;'>{ft_total:.1f}%</td>"
                f"<td style='text-align: center;'>{total_ftf:.2f}</td>"
                f"<td style='text-align: center;'>{pts_total}</td>"
                f"<td style='text-align: center;'>{total['ORB']}</td>"
                f"<td style='text-align: center;'>{total['DREB']}</td>"
                f"<td style='text-align: center;'>{treb_total}</td>"
                f"<td style='text-align: center;'>{total['PFL']}</td>"
                f"<td style='text-align: center;'>{total['AST']}</td>"
                f"<td style='text-align: center;'>{total['TO']}</td>"
                f"<td style='text-align: center;'>{total['STL']}</td>"
                f"<td style='text-align: center;'>{total['BLK']}</td>"
                f"<td style='text-align: center;'>{total['CHG']}</td>"
                f"<td style='text-align: center;'>{total['DFL']}</td>"
                f"<td style='text-align: center;'>{total_atr:.2f}</td>"
                f"<td style='text-align: center;'>{team_abr:.1f}%</td>"
                f"</tr>"
                f"</table>"
            )

            # First pass: totals so TPT% / TST% / ABR% have a denominator
            parsed_locs = []
            total_2pm = total_3pm = total_2pa = total_3pa = total_ab = 0
            for loc in sorted(location_counts.keys()):
                counts = location_counts[loc]
                twopm = counts.get('2PM', 0)
                twopa = counts.get('2PA', 0) + twopm
                threepm = counts.get('3PM', 0)
                threepa = counts.get('3PA', 0) + threepm
                fga = twopa + threepa
                pts = (2 * twopm) + (3 * threepm)
                ab_shots = counts.get('AB', 0)
                parsed_locs.append((loc, twopm, twopa, threepm, threepa, fga, pts, ab_shots))
                total_2pm += twopm
                total_2pa += twopa
                total_3pm += threepm
                total_3pa += threepa
                total_ab += ab_shots

            total_fga = total_2pa + total_3pa
            total_pts = (2 * total_2pm) + (3 * total_3pm)
            total_p2 = total_2pm / total_2pa * 100 if total_2pa > 0 else 0.0
            total_p3 = total_3pm / total_3pa * 100 if total_3pa > 0 else 0.0
            total_pps = total_pts / total_fga if total_fga > 0 else 0.0
            total_abr = total_ab / total_fga * 100 if total_fga > 0 else 0.0

            loc_table = (
                f"<table border='0' cellpadding='3'><tr><td valign='top'>"  # 2 Table for shot map image side by side
                f"<table style='text-align:left; width:100%;'>"
                f"<tr style='font-weight:bold; background:#75c875;'>"
                f"<th style='text-align: left;'>Shot Location (FG Only)</th>"
                f"<th style='text-align: center;'>2PTM</th><th style='text-align: center;'>2PTA</th><th style='text-align: center;'>2PT%</th>"
                f"<th style='text-align: center;'>3PTM</th><th style='text-align: center;'>3PTA</th><th style='text-align: center;'>3PT%</th>"
                f"<th style='text-align: center;'>PTS</th><th style='text-align: center;'>PPS</th>"
                f"<th style='text-align: center;'>TPT%</th><th style='text-align: center;'>TST%</th>"
                f"<th style='text-align: center;'>ABR%</th>"
                f"</tr>"
                )

            row_colour = "#FFFFFF;"
            for loc, twopm, twopa, threepm, threepa, fga, pts, ab_shots in parsed_locs:
                p2_loc = twopm / twopa * 100 if twopa > 0 else 0.0
                p3_loc = threepm / threepa * 100 if threepa > 0 else 0.0
                pps = pts / fga if fga > 0 else 0.0
                tpt = pts / total_pts * 100 if total_pts > 0 else 0.0
                tst = fga / total_fga * 100 if total_fga > 0 else 0.0
                abr = ab_shots / fga * 100 if fga > 0 else 0.0

                loc_table += (
                    f"<tr style='background:{row_colour}'>"
                    f"<td>{loc}</td>"
                    f"<td style='text-align: center;'>{twopm}</td><td style='text-align: center;'>{twopa}</td><td style='text-align: center;'>{p2_loc:.1f}%</td>"
                    f"<td style='text-align: center;'>{threepm}</td><td style='text-align: center;'>{threepa}</td><td style='text-align: center;'>{p3_loc:.1f}%</td>"
                    f"<td style='text-align: center;'>{pts}</td><td style='text-align: center;'>{pps:.2f}</td>"
                    f"<td style='text-align: center;'>{tpt:.1f}%</td><td style='text-align: center;'>{tst:.1f}%</td>"
                    f"<td style='text-align: center;'>{abr:.1f}%</td>"
                    f"</tr>"
                )

                if row_colour == "#FFFFFF;":
                    row_colour = "#DCFFDC;"
                else:
                    row_colour = "#FFFFFF;"

            loc_table += (
                f"<tr style='font-weight:bold; background:#75c875;'><td style='text-align: right;'>Total:</td>"
                f"<td style='text-align: center;'>{total_2pm}</td><td style='text-align: center;'>{total_2pa}</td><td style='text-align: center;'>{total_p2:.1f}%</td>"
                f"<td style='text-align: center;'>{total_3pm}</td><td style='text-align: center;'>{total_3pa}</td><td style='text-align: center;'>{total_p3:.1f}%</td>"
                f"<td style='text-align: center;'>{total_pts}</td><td style='text-align: center;'>{total_pps:.2f}</td>"
                f"<td style='text-align: center;'>100%</td><td style='text-align: center;'>100%</td>"
                f"<td style='text-align: center;'>{total_abr:.1f}%</td>"
                f"</tr></table>"
                f"</td><td valign='top'>"
                f"<img src=':/Shot_Location_Diagram_v1.svg' width='300' height='240' alt='Shot Location Diagram'>"
                f"</td></tr></table>"
            )

            total_fga = total['2PTM'] + total['2PTA']
            total_three_a = total['3PTM'] + total['3PTA']
            total_fta = total['FTM'] + total['FTA']

            # Calculate possessions
            pos = round((total_three_a + total_fga) + (total_fta * 0.44) - total['ORB'] + total['TO'])


            # Calculate possessions
            pos = round((total_three_a + total_fga) + (total_fta * 0.44) - total['ORB'] + total['TO'])
            ppp = round(pts_total / pos, 2) if pos > 0 else 0.00

            tov_ratio = round((total['TO'] / pos * 100), 1) if pos > 0 else 0.0

            # Calculate total rebounds for this team
            total_treb = total['ORB'] + total['DREB']

            # Return all needed values (including loc_table and rebounding numbers)
            return (player_table,
                    loc_table,
                    quarter_stats,
                    pos,
                    ppp,
                    tov_ratio,
                    total['ORB'],
                    total['DREB'],
                    total_treb, total_efg_pct, total_ftf, total_atr)

        # Get stats for both teams - 11 values returned
        home_total_efg_pct = guest_total_efg_pct = 0
        home_total_ftf = guest_total_ftf = 0
        home_atr = guest_atr = 0

        home_player_table, home_loc_table, home_quarters, home_pos, home_ppp, home_tov_ratio, home_oreb, home_dreb, home_treb, home_total_efg_pct, home_total_ftf, home_atr = team_stats(
            events, 'home', home_name)
        guest_player_table, guest_loc_table, guest_quarters, guest_pos, guest_ppp, guest_tov_ratio, guest_oreb, guest_dreb, guest_treb, guest_total_efg_pct, guest_total_ftf, guest_atr = team_stats(
            events, 'guest', guest_name)

        # Calculate Rebounding Percentages
        home_oreb_pct = round((home_oreb / (home_oreb + guest_dreb) * 100), 1) if (home_oreb + guest_dreb) > 0 else 0.0
        home_dreb_pct = round((home_dreb / (home_dreb + guest_oreb) * 100), 1) if (home_dreb + guest_oreb) > 0 else 0.0
        home_reb_pct = round((home_treb / (home_treb + guest_treb) * 100), 1) if (home_treb + guest_treb) > 0 else 0.0

        guest_oreb_pct = round((guest_oreb / (guest_oreb + home_dreb) * 100), 1) if (
                                                                                                guest_oreb + home_dreb) > 0 else 0.0
        guest_dreb_pct = round((guest_dreb / (guest_dreb + home_oreb) * 100), 1) if (
                                                                                                guest_dreb + home_oreb) > 0 else 0.0
        guest_reb_pct = round((guest_treb / (guest_treb + home_treb) * 100), 1) if (guest_treb + home_treb) > 0 else 0.0

        period_title = "Quarter" if format_ == "Q" else "Half" if format_ == "H" else "Period"

        quarter_table = """
                    <table style='text-align:left; width:100%;'>
                        <tr style='font-weight:bold; background:#75c875;'>
                            <th>Period</th>
                            <th>Team</th>
                            <th style='text-align: center;'>PTS</th>
                            <th style='text-align: center;'>2PTM</th>
                            <th style='text-align: center;'>2PTA</th>
                            <th style='text-align: center;'>2PT%</th>
                            <th style='text-align: center;'>3PTM</th>
                            <th style='text-align: center;'>3PTA</th>
                            <th style='text-align: center;'>3PT%</th>
                            <th style='text-align: center;'>eFG%</th>
                            <th style='text-align: center;'>SHOTS</th>
                            <th style='text-align: center;'>FTM</th>
                            <th style='text-align: center;'>FTA</th>
                            <th style='text-align: center;'>FT%</th>
                            <th style='text-align: center;'>FTF</th>     <!-- Now a ratio -->
                            <th style='text-align: center;'>ORB</th>
                            <th style='text-align: center;'>DREB</th>
                            <th style='text-align: center;'>TREB</th>
                            <th style='text-align: center;'>PFL</th>
                            <th style='text-align: center;'>AST</th>
                            <th style='text-align: center;'>TO</th>
                            <th style='text-align: center;'>STL</th>
                            <th style='text-align: center;'>BLK</th>
                            <th style='text-align: center;'>CHG</th>
                            <th style='text-align: center;'>DFL</th>
                            <th style='text-align: center;'>ABR</th>
                            <th style='text-align: center;'>SCORE</th>
                        </tr>
                """

        home_cum_pts = 0
        guest_cum_pts = 0

        for period_key in period_order:
            h = home_quarters.get(period_key, {k: 0 for k in stat_keys})
            g = guest_quarters.get(period_key, {k: 0 for k in stat_keys})

            if all(v == 0 for v in h.values()) and all(v == 0 for v in g.values()):
                continue

            h_fga = h['2PTM'] + h['2PTA']
            h_three_a = h['3PTM'] + h['3PTA']
            h_fta = h['FTM'] + h['FTA']
            h_p2 = h['2PTM'] / h_fga * 100 if h_fga > 0 else 0.0
            h_p3 = h['3PTM'] / h_three_a * 100 if h_three_a > 0 else 0.0

            # NEW: eFG% for Home
            h_fgm_total = h['2PTM'] + h['3PTM']
            h_fga_total = h_fga + h_three_a
            h_efg_pct = (h_fgm_total + 0.5 * h['3PTM']) / h_fga_total * 100 if h_fga_total > 0 else 0.0

            h_ft = h['FTM'] / h_fta * 100 if h_fta > 0 else 0.0
            h_pts = (h['2PTM'] * 2) + (h['3PTM'] * 3) + h['FTM']

            # FTF as ratio (recommended)
            h_ftf = (h_fta / h_fga_total) if h_fga_total > 0 else 0.0

            h_treb = h['ORB'] + h['DREB']

            g_fga = g['2PTM'] + g['2PTA']
            g_three_a = g['3PTM'] + g['3PTA']
            g_fta = g['FTM'] + g['FTA']
            g_p2 = g['2PTM'] / g_fga * 100 if g_fga > 0 else 0.0
            g_p3 = g['3PTM'] / g_three_a * 100 if g_three_a > 0 else 0.0
            g_ft = g['FTM'] / g_fta * 100 if g_fta > 0 else 0.0

            # NEW: eFG% for Guest
            g_fgm_total = g['2PTM'] + g['3PTM']
            g_fga_total = g_fga + g_three_a
            g_efg_pct = (g_fgm_total + 0.5 * g['3PTM']) / g_fga_total * 100 if g_fga_total > 0 else 0.0

            g_pts = (g['2PTM'] * 2) + (g['3PTM'] * 3) + g['FTM']

            # FTF as ratio
            g_ftf = (g_fta / g_fga_total) if g_fga_total > 0 else 0.0

            g_treb = g['ORB'] + g['DREB']

            h_tfga = h_fga + h_three_a
            g_tfga = g_fga + g_three_a

            home_cum_pts += h_pts
            guest_cum_pts += g_pts

            # ABR for Home
            h_abr = 0.0
            h_fga_total = h_fga + h_three_a
            if h_fga_total > 0:
                h_abr = (h.get('ABR', 0) / h_fga_total * 100)

            # ABR for Guest
            g_abr = 0.0
            g_fga_total = g_fga + g_three_a
            if g_fga_total > 0:
                g_abr = (g.get('ABR', 0) / g_fga_total * 100)


            display_period = period_labels.get(period_key, period_key)

            quarter_table += f"""
                        <tr>
                            <td rowspan='2'>{display_period}</td>
                            <td>Home</td>
                            <td style='text-align: center;'>{h_pts}</td>
                            <td style='text-align: center;'>{h['2PTM']}</td><td style='text-align: center;'>{h_fga}</td><td style='text-align: center;'>{h_p2:.1f}%</td>
                            <td style='text-align: center;'>{h['3PTM']}</td><td style='text-align: center;'>{h_three_a}</td><td style='text-align: center;'>{h_p3:.1f}%</td>
                            <td style='text-align: center;'>{h_efg_pct:.1f}%</td>
                            <td style='text-align: center;'>{h_tfga}</td>
                            <td style='text-align: center;'>{h['FTM']}</td><td style='text-align: center;'>{h_fta}</td><td style='text-align: center;'>{h_ft:.1f}%</td>
                            <td style='text-align: center;'>{h_ftf:.2f}</td>
                            <td style='text-align: center;'>{h['ORB']}</td><td style='text-align: center;'>{h['DREB']}</td><td style='text-align: center;'>{h_treb}</td>
                            <td style='text-align: center;'>{h['PFL']}</td><td style='text-align: center;'>{h['AST']}</td><td style='text-align: center;'>{h['TO']}</td>
                            <td style='text-align: center;'>{h['STL']}</td><td style='text-align: center;'>{h['BLK']}</td><td style='text-align: center;'>{h['CHG']}</td><td style='text-align: center;'>{h['DFL']}</td>
                            <td style='text-align: center;'>{h_abr:.1f}%</td>
                            <td style='text-align: center;'>{home_cum_pts}</td>
                        </tr>
                        <tr style='background:#DCFFDC;'>
                            <td>Guest</td>
                            <td style='text-align: center;'>{g_pts}</td>
                            <td style='text-align: center;'>{g['2PTM']}</td><td style='text-align: center;'>{g_fga}</td><td style='text-align: center;'>{g_p2:.1f}%</td>
                            <td style='text-align: center;'>{g['3PTM']}</td><td style='text-align: center;'>{g_three_a}</td><td style='text-align: center;'>{g_p3:.1f}%</td>
                            <td style='text-align: center;'>{g_efg_pct:.1f}%</td>
                            <td style='text-align: center;'>{g_tfga}</td>
                            <td style='text-align: center;'>{g['FTM']}</td><td style='text-align: center;'>{g_fta}</td><td style='text-align: center;'>{g_ft:.1f}%</td>
                            <td style='text-align: center;'>{g_ftf:.2f}</td>
                            <td style='text-align: center;'>{g['ORB']}</td><td style='text-align: center;'>{g['DREB']}</td><td style='text-align: center;'>{g_treb}</td>
                            <td style='text-align: center;'>{g['PFL']}</td><td style='text-align: center;'>{g['AST']}</td><td style='text-align: center;'>{g['TO']}</td>
                            <td style='text-align: center;'>{g['STL']}</td><td style='text-align: center;'>{g['BLK']}</td><td style='text-align: center;'>{g['CHG']}</td><td style='text-align: center;'>{g['DFL']}</td>
                            <td style='text-align: center;'>{g_abr:.1f}%</td>
                            <td style='text-align: center;'>{guest_cum_pts}</td>
                        </tr>
                    """

        quarter_table += "</table><br>"
        report += quarter_table

        home_highlights = self.generate_home_highlights_html(game_id)
        guest_highlights = self.generate_guest_highlights_html(game_id)

        # Home Team Section
        report += f"<h3>Home Team Stats: {home_name}</h3>"
        report += f"<table style = 'text-align:left; width:100%;' cellpadding='2'>"
        report += f"<tr style = 'font-weight:bold; background:#75c875;'>"
        report += f"<th style='text-align: center;'>POS</th>"
        report += f"<th style='text-align: center;'>PPP</th>"
        report += f"<th style='text-align: center;'>eFG%</th>"
        report += f"<th style='text-align: center;'>TOR</th>"
        report += f"<th style='text-align: center;'>OREB%</th>"
        report += f"<th style='text-align: center;'>DREB%</th>"
        report += f"<th style='text-align: center;'>TREB%</th>"
        report += f"<th style='text-align: center;'>FTF</th>"
        report += f"<th style='text-align: center;'>ATR</th>"
        report += f"</tr>"
        report += f"<tr style = 'background:#DCFFDC;'>"
        report += f"<td style='text-align: center;'>{home_pos}</td>"
        report += f"<td style='text-align: center;'>{home_ppp: .2f}</td>"
        report += f"<td style='text-align: center;'>{home_total_efg_pct:.1f}%</td>"
        report += f"<td style='text-align: center;'>{home_tov_ratio}%</td>"
        report += f"<td style='text-align: center;'>{home_oreb_pct}%</td>"
        report += f"<td style='text-align: center;'>{home_dreb_pct}%</td>"
        report += f"<td style='text-align: center;'>{home_reb_pct}%</td>"
        report += f"<td style='text-align: center;'>{home_total_ftf:.2f}</td>"
        report += f"<td style='text-align: center;'>{home_atr:.2f}</td>"
        report += f"</tr>"
        report += f"</table>"

        report += home_player_table
        report += self.generate_shot_quality_table_for_game(game_id, home_id, "Home")
        report += f"{home_loc_table}"
        report += f"{home_highlights}"

        # Guest Team Section
        report += f"<br><h3>Guest Team Stats: {guest_name}</h3>"
        report += f"<table style = 'text-align:left; width:100%;' cellpadding='2'>"
        report += f"<tr style = 'font-weight:bold; background:#75c875;'>"
        report += f"<th style='text-align: center;'>POS</th>"
        report += f"<th style='text-align: center;'>PPP</th>"
        report += f"<th style='text-align: center;'>eFG%</th>"
        report += f"<th style='text-align: center;'>TOR</th>"
        report += f"<th style='text-align: center;'>OREB%</th>"
        report += f"<th style='text-align: center;'>DREB%</th>"
        report += f"<th style='text-align: center;'>TREB%</th>"
        report += f"<th style='text-align: center;'>FTF</th>"
        report += f"<th style='text-align: center;'>ATR</th>"
        report += f"</tr>"
        report += f"<tr style = 'background:#DCFFDC;'>"
        report += f"<td style='text-align: center;'>{guest_pos}</td>"
        report += f"<td style='text-align: center;'>{guest_ppp: .2f}</td>"
        report += f"<td style='text-align: center;'>{guest_total_efg_pct:.1f}%</td>"
        report += f"<td style='text-align: center;'>{guest_tov_ratio}%</td>"
        report += f"<td style='text-align: center;'>{guest_oreb_pct}%</td>"
        report += f"<td style='text-align: center;'>{guest_dreb_pct}%</td>"
        report += f"<td style='text-align: center;'>{guest_reb_pct}%</td>"
        report += f"<td style='text-align: center;'>{guest_total_ftf:.2f}</td>"
        report += f"<td style='text-align: center;'>{guest_atr:.2f}</td>"
        report += f"</tr>"
        report += f"</table>"

        report += guest_player_table
        report += self.generate_shot_quality_table_for_game(game_id, guest_id, "Guest")
        report += f"{guest_loc_table}"
        report += f"{guest_highlights}"

        report += f"<p>Powered by CourtTag v{VERSION} (c) 2026 </p>"

        return report

    def generate_home_highlights_html(self, game_id: int) -> str:
        """Home Highlights - Single clickable column"""
        return self._generate_simple_highlights_html(game_id, "home", "Home")

    def generate_guest_highlights_html(self, game_id: int) -> str:
        """Guest Highlights - Single clickable column"""
        return self._generate_simple_highlights_html(game_id, "guest", "Guest")

    def _generate_simple_highlights_html(self, game_id: int, team_side: str, team_label: str) -> str:
        """Internal helper - returns empty string if no YouTube URL"""
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()

        # Get YouTube URL
        c.execute("SELECT youtube_url FROM games WHERE id = ?", (game_id,))
        row = c.fetchone()
        youtube_url = row[0] if row else None

        if not youtube_url:
            conn.close()
            return ""

        # Extract video ID
        if "v=" in youtube_url:
            video_id = youtube_url.split("v=")[-1].split("&")[0]
        else:
            video_id = youtube_url.split("/")[-1]

        # Get highlights + location & quality
        c.execute("""
            SELECT 
                e.time_ms,
                e.quarter,
                p.number,
                p.name,
                e.type,
                e.location,
                e.shot_quality,
                e.player_id
            FROM events e
            JOIN videos v ON e.video_id = v.id
            LEFT JOIN players p ON e.player_id = p.id
            WHERE v.game_id = ?
              AND e.is_highlight = 1
              AND (
                    e.player_id IS NULL 
                    OR EXISTS (
                        SELECT 1 FROM game_rosters gr 
                        WHERE gr.player_id = e.player_id 
                          AND gr.game_id = ? 
                          AND gr.side = ?
                    )
                  )
            ORDER BY e.video_id, e.time_ms
        """, (game_id, game_id, team_side))

        events = c.fetchall()
        conn.close()

        if not events:
            return ""

        row_colour = "#FFFFFF;"

        html = f"<table border='0' cellpadding='0' cellspacing='2'>"
        html += f"<tr style = 'font-weight:bold; background:#75c875;'>"
        html += f"<th>{team_label} Highlight Events (YouTube Links)</th>"

        for ev in events:
            time_ms = ev[0]
            seconds = time_ms // 1000
            adjusted_sec = max(0, seconds - 5)

            time_display = f"{seconds // 60:02d}:{seconds % 60:02d}"
            quarter = ev[1] or ""

            if ev[7] is None:  # Null player
                player = "Team"
            else:
                player = f"#{ev[2]} {ev[3]}" if ev[2] else "Unknown"

            event_type = ev[4] or ""

            # === NEW: Use only first 3 letters of location code ===
            location_code = (ev[5] or "")[:3].strip() if ev[5] else ""
            quality = ev[6] or ""

            # Build extra info
            extra = ""
            if location_code or quality:
                parts = []
                if location_code:
                    parts.append(location_code)
                if quality:
                    parts.append(quality)
                extra = " (" + " - ".join(parts) + ")"

            link = f"https://youtu.be/{video_id}?t={adjusted_sec}"

            html += f"""
            <tr style='background:{row_colour}'><td>
                <a href="{link}" target="_blank" style="color:#0066cc; text-decoration:none; font-weight:bold;"> ▶ 
                    <strong>{time_display}</strong>- {quarter} - 
                    {player} - {event_type}{extra}
                </a>
            </td></tr>
            """
            if row_colour == "#FFFFFF;":
                row_colour = "#DCFFDC;"
            else:
                row_colour = "#FFFFFF;"

        html += "</table>"
        return html

    def generate_shot_quality_table_for_game(self, game_id, team_id, side_label):
        """Generates Shot Quality table for ONE specific team in ONE specific game"""
        # Build display map from global SHOT_QUALITY
        quality_map = {}
        for full_name, code in SHOT_QUALITY:
            display_name = full_name.split('/', 1)[0].strip()
            quality_map[code] = display_name
        quality_map['None'] = 'Not Assigned'

        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()

        #  AND g.is_complete = 1
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
            WHERE g.id = ?                     -- ← Specific Game
              AND (g.home_team_id = ? OR g.guest_team_id = ?)  -- safety
              AND (e.type LIKE '2P%' OR e.type LIKE '3P%')
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
        conn.close()

        total_quality_points = sum(row[5] for row in quality_rows) if quality_rows else 0
        total_fg_attempts = sum((row[1] or 0) + (row[2] or 0) + (row[3] or 0) + (row[4] or 0) for row in quality_rows)

        html = f"""
        <h4>{side_label} Shot Quality Summary</h4>
        <table>
            <tr style='font-weight: bold; background: #75c875;'>
                <th style='text-align: left;'>Shot Quality</th>
                <th style='text-align: center;'>2PTM</th>
                <th style='text-align: center;'>2PTA</th>
                <th style='text-align: center;'>2PT%</th>
                <th style='text-align: center;'>3PTM</th>
                <th style='text-align: center;'>3PTA</th>
                <th style='text-align: center;'>3PT%</th>
                <th style='text-align: center;'>FGM</th>
                <th style='text-align: center;'>FGA</th>
                <th style='text-align: center;'>eFG%</th>
                <th style='text-align: center;'>PTS</th>
                <th style='text-align: center;'>PPS</th>
                <th style='text-align: center;'>TPT%</th>
                <th style='text-align: center;'>TST%</th>
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
            <tr style='background:{row_colour};'>
                <td style='text-align: left; font-weight: bold;'>{display_qual}</td>
                <td style='text-align: center;'>{twopm}</td>
                <td style='text-align: center;'>{twopa}</td>
                <td style='text-align: center;'>{p2:.1f}%</td>
                <td style='text-align: center;'>{threepm}</td>
                <td style='text-align: center;'>{threepa}</td>
                <td style='text-align: center;'>{p3:.1f}%</td>
                <td style='text-align: center;'>{fgm}</td>
                <td style='text-align: center;'>{fga}</td>
                <td style='text-align: center;'>{efg:.1f}%</td>
                <td style='text-align: center;'>{pts}</td>
                <td style='text-align: center;'>{pps:.2f}</td>
                <td style='text-align: center;'>{tpt_pct:.1f}%</td>
                <td style='text-align: center;'>{tst_pct:.1f}%</td>
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
            <tr style='font-weight:bold; background:#75c875;'>
                <td style='text-align: left;'>Total:</td>
                <td style='text-align: center;'>{total_2pm}</td>
                <td style='text-align: center;'>{total_2pa}</td>
                <td style='text-align: center;'>{total_p2:.1f}%</td>
                <td style='text-align: center;'>{total_3pm}</td>
                <td style='text-align: center;'>{total_3pa}</td>
                <td style='text-align: center;'>{total_p3:.1f}%</td>
                <td style='text-align: center;'>{total_fgm}</td>
                <td style='text-align: center;'>{total_fga}</td>
                <td style='text-align: center;'>{total_efg:.1f}%</td>
                <td style='text-align: center;'>{total_pts}</td>
                <td style='text-align: center;'>{total_pps:.2f}</td>
                <td style='text-align: center;'>100%</td>
                <td style='text-align: center;'>100%</td>
            </tr>
        </table>
        """
        return html


    def generate_team_report(self, team_id):
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()

        c.row_factory = lambda cursor, row: tuple(0 if val is None else val for val in row)

        c.execute("SELECT name, season FROM teams WHERE id = ?", (team_id,))
        team_info = c.fetchone()
        if not team_info:
            conn.close()
            return "<h2>Team not found.</h2>"
        team_name, season = team_info

        c.execute("""
                  SELECT g.id,
                         g.date,
                         g.name,
                         CASE WHEN g.home_team_id = ? THEN t_guest.name ELSE t_home.name END AS opponent,
                         CASE WHEN g.home_team_id = ? THEN 'home' ELSE 'guest' END           AS team_side
                  FROM games g
                           LEFT JOIN teams t_home ON g.home_team_id = t_home.id
                           LEFT JOIN teams t_guest ON g.guest_team_id = t_guest.id
                  WHERE (g.home_team_id = ? OR g.guest_team_id = ?)
                    AND g.is_complete = 1
                  ORDER BY g.date ASC
                  """, (team_id, team_id, team_id, team_id))
        games = c.fetchall()
        num_games = len(games)
        if num_games == 0:
            conn.close()
            return f"<h2>Team Report: {team_name}</h2><hr><b>Season:</b> {season}<br><br>No games played yet."

        wins = losses = ties = 0
        game_rows = []

        total_2pm = total_2pa = total_3pm = total_3pa = total_ftm = total_fta = 0

        # Rebound totals for accumulation
        total_orb = total_dreb = total_treb = 0
        total_opp_oreb = total_opp_dreb = total_opp_treb = 0  # ← ADD THIS LINE
        total_pfl = total_ast = total_to = total_stl = total_blk = total_chg = total_dfl = 0
        total_tov_pct_sum = 0
        total_ab_shots = 0

        for game in games:
            g_id, date, g_name, opponent, team_side = game

            # Team points
            c.execute("""
                SELECT SUM(CASE WHEN e.type LIKE '2PM%' THEN 2
                                WHEN e.type LIKE '3PM%' THEN 3
                                WHEN e.type LIKE 'FTM%' THEN 1 ELSE 0 END) as pts
                FROM events e
                JOIN videos v ON e.video_id = v.id
                WHERE v.game_id = ?
                  AND (
                    e.player_id IN (SELECT player_id FROM game_rosters WHERE game_id = ? AND side = ?)
                    OR (e.player_id IS NULL AND e.team_side = ?)
                  )
            """, (g_id, g_id, team_side, team_side))
            team_pts = c.fetchone()[0] or 0

            # Get opponent's points AND rebounds in one query
            opp_side = 'guest' if team_side == 'home' else 'home'
            c.execute("""
                            SELECT 
                                SUM(CASE WHEN e.type LIKE '2PM%' THEN 2
                                         WHEN e.type LIKE '3PM%' THEN 3
                                         WHEN e.type LIKE 'FTM%' THEN 1 ELSE 0 END) as opp_pts,
                                SUM(CASE WHEN e.type LIKE 'ORB%' THEN 1 ELSE 0 END) as opp_oreb,
                                SUM(CASE WHEN e.type LIKE 'DRB%' THEN 1 ELSE 0 END) as opp_dreb
                            FROM events e
                            JOIN videos v ON e.video_id = v.id
                            WHERE v.game_id = ?
                              AND (
                                e.player_id IN (SELECT player_id FROM game_rosters WHERE game_id = ? AND side = ?)
                                OR (e.player_id IS NULL AND e.team_side = ?)
                              )
                        """, (g_id, g_id, opp_side, opp_side))

            opp_data = c.fetchone() or (0, 0, 0)
            opp_pts, opp_oreb, opp_dreb = opp_data
            opp_treb = opp_oreb + opp_dreb

            # === Win / Loss / Tie Counter ===
            if team_pts > opp_pts:
                wins += 1
            elif team_pts < opp_pts:
                losses += 1
            else:
                ties += 1

            # Determine if this team was home or guest
            c.execute("""
                SELECT 
                    CASE WHEN home_team_id = ? THEN 'home' ELSE 'guest' END as team_side
                FROM games 
                WHERE id = ?
            """, (team_id, g_id))
            side_row = c.fetchone()
            team_side = side_row[0] if side_row else 'home'

            # Per-game stats for this team
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
                WHERE v.game_id = ?
                  AND (
                    e.player_id IN (SELECT player_id FROM game_rosters WHERE game_id = ? AND side = ?)
                    OR (e.player_id IS NULL AND e.team_side = ?)
                  )
            """, (g_id, g_id, team_side, team_side))

            stats = c.fetchone() or (0,) * 15
            twopm, twopa_miss, threepm, threepa_miss, ftm, fta_miss, oreb, dreb, pfl, ast, tov, stl, blk, chg, dfl = stats

            fga_2p = twopa_miss + twopm
            fga_3p = threepa_miss + threepm
            fga_total = fga_2p  # kept as 2PT only for the 2PTA column
            fta_total = fta_miss + ftm

            fgm = twopm
            three_a = fga_3p

            poss = round(fga_2p + fga_3p + (fta_total * 0.44) - oreb + tov)
            ppp = round(team_pts / poss, 2) if poss > 0 else 0.00

            p2_pct = (twopm / fga_2p * 100) if fga_2p > 0 else 0.0
            p3_pct = (threepm / fga_3p * 100) if fga_3p > 0 else 0.0
            ft_pct = (ftm / fta_total * 100) if fta_total > 0 else 0.0

            # FTF as ratio
            fga_total_full = fga_2p + fga_3p
            ftf = (fta_total / fga_total_full) if fga_total_full > 0 else 0.0

            # eFG%
            fgm_total = twopm + threepm
            efg_pct = (fgm_total + 0.5 * threepm) / fga_total_full * 100 if fga_total_full > 0 else 0.0

            # Raw rebounds
            treb = oreb + dreb

            # Rebounding Percentages
            oreb_pct = round((oreb / (oreb + opp_dreb) * 100), 1) if (oreb + opp_dreb) > 0 else 0.0
            dreb_pct = round((dreb / (dreb + opp_oreb) * 100), 1) if (dreb + opp_oreb) > 0 else 0.0
            treb_pct = round((treb / (treb + opp_treb) * 100), 1) if (treb + opp_treb) > 0 else 0.0

            # Accumulate TOTALS needed for true season-long rebound %
            total_opp_oreb += opp_oreb
            total_opp_dreb += opp_dreb
            total_opp_treb += opp_treb

            # Turnover Ratio
            tov_pct = round((tov / poss * 100), 1) if poss > 0 else 0.0

            # Per-game ABR using correct side
            abr_game = 0.0
            if fga_total_full > 0:
                c.execute("""
                    SELECT SUM(CASE WHEN e.shot_quality IN ('A','B') THEN 1 ELSE 0 END)
                    FROM events e
                    JOIN videos v ON e.video_id = v.id
                    WHERE v.game_id = ?
                      AND (
                        e.team_side = ?
                        OR e.player_id IN (
                            SELECT player_id FROM game_rosters 
                            WHERE game_id = ? AND side = ?
                        )
                      )
                """, (g_id, team_side, g_id, team_side))
                ab_makes_this_game = c.fetchone()[0] or 0
                abr_game = (ab_makes_this_game / fga_total_full * 100)

            total_ab_shots += ab_makes_this_game

            #print(f"DEBUG Game {g_id}: side={team_side}, fga={fga_total_full}, ab={ab_makes_this_game}")

            # Append to game_rows
            game_rows.append((
                date,
                g_name or "",
                opponent,
                opp_pts,
                team_pts,
                poss,
                ppp,
                fgm,
                fga_total,
                p2_pct,
                threepm,
                three_a,
                p3_pct,
                efg_pct,
                ftm,
                fta_total,
                ft_pct,
                ftf,
                oreb_pct,
                dreb_pct,
                treb_pct,
                pfl,
                ast,
                tov,
                stl,
                blk,
                chg,
                dfl,
                tov_pct,
                abr_game
            ))


            # Accumulation
            total_2pm += twopm
            total_2pa += fga_2p
            total_3pm += threepm
            total_3pa += fga_3p
            total_ftm += ftm
            total_fta += fta_total
            total_orb += oreb  # ← changed from orb
            total_dreb += dreb  # ← changed from dreb
            total_treb += treb  # ← changed from treb
            total_pfl += pfl
            total_ast += ast
            total_to += tov
            total_stl += stl
            total_blk += blk
            total_chg += chg
            total_dfl += dfl
            total_tov_pct_sum += tov_pct

        # Season-wide ABR
        total_fga = total_2pa + total_3pa
        season_abr = 0.0
        if total_fga > 0:
            season_abr = (total_ab_shots / total_fga * 100)

        # Table Header with new TOV% column at the end
        game_table = """
        <table>
            <tr style='text-align: center; font-weight: bold; background: #f2c74a;'>
                <th style='text-align: left;'>Date</th>
                <th style='text-align: left;'>Name</th>
                <th style='text-align: left;'>Opponent</th>
                <th style='text-align: center;'>OPTS</th><th style='text-align: center;'>PTS</th><th style='text-align: center;'>POSS</th><th style='text-align: center;'>PPP</th>
                <th style='text-align: center;'>2PTM</th><th style='text-align: center;'>2PTA</th><th style='text-align: center;'>2PT%</th>
                <th style='text-align: center;'>3PTM</th><th style='text-align: center;'>3PTA</th><th style='text-align: center;'>3PT%</th>
                <th style='text-align: center;'>eFG%</th>
                <th style='text-align: center;'>FTM</th><th style='text-align: center;'>FTA</th><th style='text-align: center;'>FT%</th>
                <th style='text-align: center;'>FTF</th>
                <th style='text-align: center;'>OREB</th><th style='text-align: center;'>DREB</th><th style='text-align: center;'>TREB</th>
                <th style='text-align: center;'>PFL</th><th style='text-align: center;'>AST</th><th style='text-align: center;'>TO</th>
                <th style='text-align: center;'>STL</th><th style='text-align: center;'>BLK</th><th style='text-align: center;'>CHG</th><th style='text-align: center;'>DFL</th>
                <th style='text-align: center;'>TOV%</th>
                <th style='text-align: center;'>ATR</th>
                <th style='text-align: center;'>ABR</th>
            </tr>
        """

        total_opts = total_pts = total_poss = total_fgm = total_fga = total_3pt = total_3pta = 0
        total_ftm = total_fta = 0
        total_pfl = total_ast = total_to = total_stl = total_blk = total_chg = total_dfl = 0
        total_tov_pct_sum = 0

        row_colour = "#FFEDB8;"

        for row in game_rows:
            date, g_name, opp, opts, pts, poss, ppp, fgm, fga, p2, tpt, tpa, p3, efg, ftm, fta, ft, ftf, oreb_pct, dreb_pct, treb_pct, pfl, ast, tov, stl, blk, chg, dfl, tov_pct, abr_game = row
            show_atr = round((ast / tov), 2) if ast > 0 else 0.0
            game_table += f"""
            <tr style='text-align: center; background:{row_colour};'>
                <td style='text-align: left;'>{date}</td>
                <td style='text-align: left;'>{g_name}</td>
                <td style='text-align: left;'>{opp}</td>
                <td style='text-align: center;'>{opts}</td><td style='text-align: center;'>{pts}</td><td style='text-align: center;'>{poss}</td><td style='text-align: center;'>{ppp:.2f}</td>
                <td style='text-align: center;'>{fgm}</td><td style='text-align: center;'>{fga}</td><td style='text-align: center;'>{p2:.1f}%</td>
                <td style='text-align: center;'>{tpt}</td><td style='text-align: center;'>{tpa}</td><td style='text-align: center;'>{p3:.1f}%</td>
                <td style='text-align: center;'>{efg:.1f}%</td>
                <td style='text-align: center;'>{ftm}</td><td style='text-align: center;'>{fta}</td><td style='text-align: center;'>{ft:.1f}%</td>
                <td style='text-align: center;'>{ftf:.2f}</td>
                <td style='text-align: center;'>{oreb_pct:.1f}%</td><td style='text-align: center;'>{dreb_pct:.1f}%</td><td style='text-align: center;'>{treb_pct:.1f}%</td>
                <td style='text-align: center;'>{pfl}</td><td style='text-align: center;'>{ast}</td><td style='text-align: center;'>{tov}</td>
                <td style='text-align: center;'>{stl}</td><td style='text-align: center;'>{blk}</td><td style='text-align: center;'>{chg}</td><td style='text-align: center;'>{dfl}</td>
                <td style='text-align: center;'>{tov_pct}%</td>
                <td style='text-align: center;'>{show_atr:.2f}</td>
                <td style='text-align: center;'>{abr_game:.1f}%</td>
            </tr>
            """

            if row_colour == "#FFFFFF;":
                row_colour = "#FFEDB8;"
            else:
                row_colour = "#FFFFFF;"

            total_opts += opts
            total_pts += pts
            total_poss += poss
            total_fgm += fgm
            total_fga += fga
            total_3pt += tpt
            total_3pta += tpa
            total_ftm += ftm
            total_fta += fta
            total_pfl += pfl
            total_ast += ast
            total_to += tov
            total_stl += stl
            total_blk += blk
            total_chg += chg
            total_dfl += dfl
            total_tov_pct_sum += tov_pct

        total_2p_pct = (total_2pm / total_2pa * 100) if total_2pa > 0 else 0.0
        total_3pt_pct = (total_3pm / total_3pa * 100) if total_3pa > 0 else 0.0
        total_ft_pct = (total_ftm / total_fta * 100) if total_fta > 0 else 0.0
        avg_ppp = round(total_pts / total_poss, 2) if total_poss > 0 else 0.0

        # Overall TOV% for Totals row
        overall_tov_pct = round((total_to / total_poss * 100), 1) if total_poss > 0 else 0.0

        # Totals eFG%
        total_fgm_efg = total_2pm + total_3pm
        total_fga_efg = total_2pa + total_3pa
        total_efg_pct = (total_fgm_efg + 0.5 * total_3pm) / total_fga_efg * 100 if total_fga_efg > 0 else 0.0

        # Totals FTF
        total_fga_all = total_2pa + total_3pa
        total_ftf = (total_fta / total_fga_all) if total_fga_all > 0 else 0.0

        # === TRUE SEASON-LONG REBOUNDING PERCENTAGES ===
        total_oreb_pct = round((total_orb / (total_orb + total_opp_dreb) * 100), 1) if (total_orb + total_opp_dreb) > 0 else 0.0
        total_dreb_pct = round((total_dreb / (total_dreb + total_opp_oreb) * 100), 1) if (total_dreb + total_opp_oreb) > 0 else 0.0
        total_treb_pct = round((total_treb / (total_treb + total_opp_treb) * 100), 1) if (total_treb + total_opp_treb) > 0 else 0.0

        # === FINAL AVERAGES CALCULATION - RIGHT BEFORE TABLE ===
        avg_orb = round(total_orb / num_games, 1) if num_games > 0 else 0.0
        avg_dreb = round(total_dreb / num_games, 1) if num_games > 0 else 0.0
        avg_treb = round(total_treb / num_games, 1) if num_games > 0 else 0.0

        avg_atr = round((total_ast / total_to), 2) if total_ast > 0 else 0.0

        game_table += f"""
                            <tr style='font-weight:bold; background:#f2c74a; text-align: center;'>
                                <td style='text-align: left;'>Games Played:</td>
                                <td style='text-align: center;'>{num_games}</td>
                                <td style='text-align: right;'>Totals:</td>
                                <td style='text-align: center;'>{total_opts}</td>
                                <td style='text-align: center;'>{total_pts}</td>
                                <td style='text-align: center;'>{total_poss}</td>
                                <td style='text-align: center;'>{avg_ppp:.2f}</td>
                                <td style='text-align: center;'>{total_2pm}</td>
                                <td style='text-align: center;'>{total_2pa}</td>
                                <td style='text-align: center;'>{total_2p_pct:.1f}%</td>
                                <td style='text-align: center;'>{total_3pt}</td>
                                <td style='text-align: center;'>{total_3pta}</td>
                                <td style='text-align: center;'>{total_3pt_pct:.1f}%</td>
                                <td style='text-align: center;'>{total_efg_pct:.1f}%</td>
                                <td style='text-align: center;'>{total_ftm}</td>
                                <td style='text-align: center;'>{total_fta}</td>
                                <td style='text-align: center;'>{total_ft_pct:.1f}%</td>
                                <td style='text-align: center;'>{total_ftf:.2f}</td>
                                <td style='text-align: center;'>{total_oreb_pct:.1f}%</td>
                                <td style='text-align: center;'>{total_dreb_pct:.1f}%</td>
                                <td style='text-align: center;'>{total_treb_pct:.1f}%</td>
                                <td style='text-align: center;'>{total_pfl}</td>
                                <td style='text-align: center;'>{total_ast}</td>
                                <td style='text-align: center;'>{total_to}</td>
                                <td style='text-align: center;'>{total_stl}</td>
                                <td style='text-align: center;'>{total_blk}</td>
                                <td style='text-align: center;'>{total_chg}</td>
                                <td style='text-align: center;'>{total_dfl}</td>
                                <td style='text-align: center;'>{overall_tov_pct}%</td>
                                <td style='text-align: center;'>{avg_atr:.2f}</td>
                                <td style='text-align: center;'>{season_abr:.1f}%</td>
                            </tr>
                            <tr style='font-weight:bold; background:#FFEDB8; text-align: center;'>
                                <td style='text-align: left;'>Win-Loss-Tie:</td><td style='text-align: center;'>{wins}-{losses}-{ties}</td>
                                <td style='text-align: right;'>Averages:</td>
                                <td style='text-align: center;'>{round(total_opts / num_games, 1) if num_games > 0 else 0:.1f}</td>
                                <td style='text-align: center;'>{round(total_pts / num_games, 1) if num_games > 0 else 0:.1f}</td>
                                <td style='text-align: center;'>{round(total_poss / num_games, 1) if num_games > 0 else 0:.1f}</td>
                                <td style='text-align: center;'>{avg_ppp:.2f}</td>
                                <td style='text-align: center;'>{round(total_2pm / num_games, 1) if num_games > 0 else 0:.1f}</td>
                                <td style='text-align: center;'>{round(total_2pa / num_games, 1) if num_games > 0 else 0:.1f}</td>
                                <td style='text-align: center;'>-</td>
                                <td style='text-align: center;'>{round(total_3pt / num_games, 1) if num_games > 0 else 0:.1f}</td>
                                <td style='text-align: center;'>{round(total_3pta / num_games, 1) if num_games > 0 else 0:.1f}</td>
                                <td style='text-align: center;'>-</td>
                                <td style='text-align: center;'>-</td>
                                <td style='text-align: center;'>{round(total_ftm / num_games, 1) if num_games > 0 else 0:.1f}</td>
                                <td style='text-align: center;'>{round(total_fta / num_games, 1) if num_games > 0 else 0:.1f}</td>
                                <td style='text-align: center;'>-</td>
                                <td style='text-align: center;'>-</td>
                                <td style='text-align: center;'>{avg_orb:.1f}</td>
                                <td style='text-align: center;'>{avg_dreb:.1f}</td>
                                <td style='text-align: center;'>{avg_treb:.1f}</td>
                                <td style='text-align: center;'>{round(total_pfl / num_games, 1) if num_games > 0 else 0:.1f}</td>
                                <td style='text-align: center;'>{round(total_ast / num_games, 1) if num_games > 0 else 0:.1f}</td>
                                <td style='text-align: center;'>{round(total_to / num_games, 1) if num_games > 0 else 0:.1f}</td>
                                <td style='text-align: center;'>{round(total_stl / num_games, 1) if num_games > 0 else 0:.1f}</td>
                                <td style='text-align: center;'>{round(total_blk / num_games, 1) if num_games > 0 else 0:.1f}</td>
                                <td style='text-align: center;'>{round(total_chg / num_games, 1) if num_games > 0 else 0:.1f}</td>
                                <td style='text-align: center;'>{round(total_dfl / num_games, 1) if num_games > 0 else 0:.1f}</td>
                                <td style='text-align: center;'>-</td>
                                <td style='text-align: center;'>-</td>
                                <td style='text-align: center;'>-</td>
                            </tr>
                            </table>
                            """

        # === Shot Locations (FG only: 2PT + 3PT, including FT-area live FGs) ===
        c.execute("""
                  SELECT e.location,
                         SUM(CASE WHEN e.type LIKE '2PM%' THEN 1 ELSE 0 END)                           as twopm,
                         SUM(CASE WHEN e.type LIKE '2P%' AND e.type NOT LIKE '2PM%' THEN 1 ELSE 0 END) as missed_2pa,
                         SUM(CASE WHEN e.type LIKE '3PM%' THEN 1 ELSE 0 END)                           as threepm,
                         SUM(CASE WHEN e.type LIKE '3P%' AND e.type NOT LIKE '3PM%' THEN 1 ELSE 0 END) as missed_3pa,
                         SUM(CASE WHEN e.shot_quality IN ('A', 'B') THEN 1 ELSE 0 END)                 as ab_shots
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

        # First pass: totals so TPT% / TST% / total ABR% have a denominator
        loc_total_2pm = loc_total_2pa = loc_total_3pm = loc_total_3pa = loc_total_ab = 0
        parsed_locs = []
        for loc, twopm, twopa_miss, threepm, threepa_miss, ab_shots in loc_rows:
            twopm = twopm or 0
            twopa_miss = twopa_miss or 0
            threepm = threepm or 0
            threepa_miss = threepa_miss or 0
            ab_shots = ab_shots or 0
            twopa_total = twopa_miss + twopm
            threepa_total = threepa_miss + threepm
            fga = twopa_total + threepa_total
            pts = (2 * twopm) + (3 * threepm)
            parsed_locs.append((loc, twopm, twopa_total, threepm, threepa_total, fga, pts, ab_shots))
            loc_total_2pm += twopm
            loc_total_2pa += twopa_total
            loc_total_3pm += threepm
            loc_total_3pa += threepa_total
            loc_total_ab += ab_shots

        loc_total_fga = loc_total_2pa + loc_total_3pa
        loc_total_pts = (2 * loc_total_2pm) + (3 * loc_total_3pm)
        loc_total_p2 = (loc_total_2pm / loc_total_2pa * 100) if loc_total_2pa > 0 else 0.0
        loc_total_p3 = (loc_total_3pm / loc_total_3pa * 100) if loc_total_3pa > 0 else 0.0
        loc_total_pps = (loc_total_pts / loc_total_fga) if loc_total_fga > 0 else 0.0
        loc_total_abr = (loc_total_ab / loc_total_fga * 100) if loc_total_fga > 0 else 0.0

        loc_table = """
        <!-- add 2 column table for shot location image -->
        <table border="0" cellpadding="3"><tr><td valign="top">
        <table>
            <tr style='font-weight: bold; background: #f2c74a;'>
                <th style='text-align: left;'>Shot Location (FG Only)</th>
                <th style='text-align: center;'>2PTM</th><th style='text-align: center;'>2PTA</th><th style='text-align: center;'>2PT%</th>
                <th style='text-align: center;'>3PTM</th><th style='text-align: center;'>3PTA</th><th style='text-align: center;'>3PT%</th>
                <th style='text-align: center;'>PTS</th><th style='text-align: center;'>PPS</th>
                <th style='text-align: center;'>TPT%</th><th style='text-align: center;'>TST%</th>
                <th style='text-align: center;'>ABR%</th>
            </tr>
        """

        row_colour = "#FFFFFF;"
        for loc, twopm, twopa_total, threepm, threepa_total, fga, pts, ab_shots in parsed_locs:
            p2 = (twopm / twopa_total * 100) if twopa_total > 0 else 0.0
            p3 = (threepm / threepa_total * 100) if threepa_total > 0 else 0.0
            pps = (pts / fga) if fga > 0 else 0.0
            tpt = (pts / loc_total_pts * 100) if loc_total_pts > 0 else 0.0
            tst = (fga / loc_total_fga * 100) if loc_total_fga > 0 else 0.0
            abr = (ab_shots / fga * 100) if fga > 0 else 0.0

            loc_table += f"""
            <tr style='text-align: center; background:{row_colour};'>
                <td style='text-align: left;'>{loc}</td>
                <td style='text-align: center;'>{twopm}</td><td style='text-align: center;'>{twopa_total}</td><td style='text-align: center;'>{p2:.1f}%</td>
                <td style='text-align: center;'>{threepm}</td><td style='text-align: center;'>{threepa_total}</td><td style='text-align: center;'>{p3:.1f}%</td>
                <td style='text-align: center;'>{pts}</td><td style='text-align: center;'>{pps:.2f}</td>
                <td style='text-align: center;'>{tpt:.1f}%</td><td style='text-align: center;'>{tst:.1f}%</td>
                <td style='text-align: center;'>{abr:.1f}%</td>
            </tr>
            """
            if row_colour == "#FFFFFF;":
                row_colour = "#FFEDB8"
            else:
                row_colour = "#FFFFFF;"

        loc_table += f"""
        <tr style='font-weight:bold; background:#f2c74a; text-align: center;'>
            <td style='text-align: right;'>Total:</td>
            <td style='text-align: center;'>{loc_total_2pm}</td><td style='text-align: center;'>{loc_total_2pa}</td><td style='text-align: center;'>{loc_total_p2:.1f}%</td>
            <td style='text-align: center;'>{loc_total_3pm}</td><td style='text-align: center;'>{loc_total_3pa}</td><td style='text-align: center;'>{loc_total_p3:.1f}%</td>
            <td style='text-align: center;'>{loc_total_pts}</td><td style='text-align: center;'>{loc_total_pps:.2f}</td>
            <td style='text-align: center;'>100%</td><td style='text-align: center;'>100%</td>
            <td style='text-align: center;'>{loc_total_abr:.1f}%</td>
        </tr>
        </table>
        </td><td valign="top">
         <img src=":/Shot_Location_Diagram_v1.svg" width="300" height="240" alt="Shot Location Diagram">
        </td>
        </tr>
        </table>
        """

        # === SHOT QUALITY TABLE (with TST% column) ============================
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
        <h4>Shot Quality Summary</h4>
        <table>
            <tr style='font-weight: bold; background: #f2c74a;'>
                <th style='text-align: left;'>Shot Quality</th>
                <th style='text-align: center;'>2PTM</th>
                <th style='text-align: center;'>2PTA</th>
                <th style='text-align: center;'>2PT%</th>
                <th style='text-align: center;'>3PTM</th>
                <th style='text-align: center;'>3PTA</th>
                <th style='text-align: center;'>3PT%</th>
                <th style='text-align: center;'>FGM</th>
                <th style='text-align: center;'>FGA</th>
                <th style='text-align: center;'>eFG%</th>
                <th style='text-align: center;'>PTS</th>
                <th style='text-align: center;'>PPS</th>
                <th style='text-align: center;'>TPT%</th>
                <th style='text-align: center;'>TST%</th>
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
            <tr style='background:{row_colour};'>
                <td style='text-align: left; font-weight: bold;'>{display_qual}</td>
                <td style='text-align: center;'>{twopm}</td>
                <td style='text-align: center;'>{twopa}</td>
                <td style='text-align: center;'>{p2:.1f}%</td>
                <td style='text-align: center;'>{threepm}</td>
                <td style='text-align: center;'>{threepa}</td>
                <td style='text-align: center;'>{p3:.1f}%</td>
                <td style='text-align: center;'>{fgm}</td>
                <td style='text-align: center;'>{fga}</td>
                <td style='text-align: center;'>{efg:.1f}%</td>
                <td style='text-align: center;'>{pts}</td>
                <td style='text-align: center;'>{pps:.2f}</td>
                <td style='text-align: center;'>{tpt_pct:.1f}%</td>
                <td style='text-align: center;'>{tst_pct:.1f}%</td>
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
            <tr style='font-weight:bold; background:#f2c74a; text-align: center;'>
                <td style='text-align: right;'>Total:</td>
                <td style='text-align: center;'>{total_2pm}</td>
                <td style='text-align: center;'>{total_2pa}</td>
                <td style='text-align: center;'>{total_p2:.1f}%</td>
                <td style='text-align: center;'>{total_3pm}</td>
                <td style='text-align: center;'>{total_3pa}</td>
                <td style='text-align: center;'>{total_p3:.1f}%</td>
                <td style='text-align: center;'>{total_fgm}</td>
                <td style='text-align: center;'>{total_fga}</td>
                <td style='text-align: center;'>{total_efg:.1f}%</td>
                <td style='text-align: center;'>{total_pts}</td>
                <td style='text-align: center;'>{total_pps:.2f}</td>
                <td style='text-align: center;'>100%</td>
                <td style='text-align: center;'>100%</td>
            </tr>
        </table>
        """

        # === Player Stats (unchanged) ===
        player_table = """
        <table style='text-align: left; width:100%;'>
            <tr style='font-weight:bold; background:#f2c74a;'>
                <th style='text-align: left;'>P#</th>
                <th style='text-align: left;'>Player Name</th>
                <th style='text-align: center;'>GP</th>
                <th style='text-align: center;'>2PTM</th>
                <th style='text-align: center;'>2PTA</th>
                <th style='text-align: center;'>2PT%</th>
                <th style='text-align: center;'>3PTM</th>
                <th style='text-align: center;'>3PTA</th>
                <th style='text-align: center;'>3PT%</th>
                <th style='text-align: center;'>eFG%</th>
                <th style='text-align: center;'>FTM</th>
                <th style='text-align: center;'>FTA</th>
                <th style='text-align: center;'>FT%</th>
                <th style='text-align: center;'>FTF</th>
                <th style='text-align: center;'>PTS</th>
                <th style='text-align: center;'>PPG</th>
                <th style='text-align: center;'>OREB</th>
                <th style='text-align: center;'>DREB</th>
                <th style='text-align: center;'>PFL</th>
                <th style='text-align: center;'>AST</th>
                <th style='text-align: center;'>TO</th>
                <th style='text-align: center;'>STL</th>
                <th style='text-align: center;'>BLK</th>
                <th style='text-align: center;'>CHG</th>
                <th style='text-align: center;'>DFL</th>
                <th style='text-align: center;'>ATR</th>
                <th style='text-align: center;'>ABR</th>
            </tr>"""

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

            c.execute("""
                      SELECT SUM(CASE WHEN e.type LIKE '2PM%' THEN 1 ELSE 0 END) as twopm,
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
            fga_total = fga_2p + fga_3p  # Player table keeps full FGA (standard)
            fta_total = fta_miss + ftm

            fgm = twopm + threepm
            three_a = fga_3p
            total_pts = (twopm * 2) + (threepm * 3) + ftm
            p2_pct = (twopm / fga_2p * 100) if fga_2p > 0 else 0.0
            p3_pct = (threepm / fga_3p * 100) if fga_3p > 0 else 0.0
            ft_pct = (ftm / fta_total * 100) if fta_total > 0 else 0.0

            # FTF as ratio (FTA / full FGA) - consistent with other reports
            fga_total_for_ftf = fga_2p + fga_3p
            ftf = (fta_total / fga_total_for_ftf) if fga_total_for_ftf > 0 else 0.0

            avg_ppg = total_pts / gp if gp > 0 else 0.0

            # === NEW: Effective FG% for Player Table ===
            # Using CourtTag's derived full FGA (2PA + 3PA) — same method as game table
            fgm_total = twopm + threepm
            fga_total_efg = fga_2p + fga_3p
            efg_pct = (fgm_total + 0.5 * threepm) / fga_total_efg * 100 if fga_total_efg > 0 else 0.0

            # ATR = Assist / Turnover Ratio
            atr = (ast / tov) if tov > 0 else 0.0


            def fmt(val):
                if gp == 0: return "0 (0.0)"
                avg = val / gp
                return f"{val} ({avg:.1f})" if val > 0 else "0 (0.0)"

            # Player ABR (season total)
            player_abr = 0.0
            if fga_total > 0:   # fga_total = fga_2p + fga_3p from above
                c.execute("""
                    SELECT SUM(CASE WHEN shot_quality IN ('A','B') THEN 1 ELSE 0 END)
                    FROM events e
                    JOIN videos v ON e.video_id = v.id
                    JOIN games g ON v.game_id = g.id
                    WHERE e.player_id = ?
                      AND g.is_complete = 1
                """, (p_id,))
                ab_shots = c.fetchone()[0] or 0
                player_abr = (ab_shots / fga_total * 100)

            player_table += f"""
            <tr style='background:{row_colour};'>
                <td style='text-align:left;'>{padded_num}</td>
                <td style='text-align:left;'>{p_name}</td>
                <td style='text-align:center;'>{gp}</td>
                <td style='text-align:center;'>{twopm}</td>
                <td style='text-align:center;'>{fga_2p}</td>
                <td style='text-align:center;'>{p2_pct:.1f}%</td>
                <td style='text-align:center;'>{threepm}</td>
                <td style='text-align:center;'>{three_a}</td>
                <td style='text-align:center;'>{p3_pct:.1f}%</td>
                <td style='text-align:center;'>{efg_pct:.1f}%</td>
                <td style='text-align:center;'>{ftm}</td>
                <td style='text-align:center;'>{fta_total}</td>
                <td style='text-align:center;'>{ft_pct:.1f}%</td>
                <td style='text-align:center;'>{ftf:.2f}</td>
                <td style='text-align:center;'>{total_pts}</td>
                <td style='text-align:center;'>{avg_ppg:.1f}</td>
                <td style='text-align:center;'>{fmt(oreb)}</td>
                <td style='text-align:center;'>{fmt(dreb)}</td>
                <td style='text-align:center;'>{fmt(pfl)}</td>
                <td style='text-align:center;'>{fmt(ast)}</td>
                <td style='text-align:center;'>{fmt(tov)}</td>
                <td style='text-align:center;'>{fmt(stl)}</td>
                <td style='text-align:center;'>{fmt(blk)}</td>
                <td style='text-align:center;'>{fmt(chg)}</td>
                <td style='text-align:center;'>{fmt(dfl)}</td>
                <td style='text-align:center;'>{atr:.2f}</td>
                <td style='text-align:center;'>{player_abr:.1f}%</td>
            </tr>
            """
            if row_colour == "#FFFFFF;":
                row_colour = "#FFEDB8;"
            else:
                row_colour = "#FFFFFF;"

        player_table += "</table>"


        # Final Report Assembly
        if season:
            report = f"<h2>Team Report: {season} - {team_name}</h2>"
        else:
            report = f"<h2>Team Report:{team_name}</h2>"

        report += "<h3>Games Played Summary:</h3>"
        report += game_table

        report += shot_quality_table

        report += loc_table

        report += "<h3>Player Stats - Total (Per Game Avg)</h3>"
        report += player_table
        report += f"<p>Powered by CourtTag v{VERSION} (c) 2026 </p>"

        conn.close()
        return report

    def export_all_records_to_csv(self):
        """Export all events with joined context to a single CSV file (includes shot_quality)."""
        timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        default_filename = f"CourtTag_all_events_{timestamp}.csv"

        base_folder = Path.home() / "Documents" / "CourtTag" / "CSV"

        try:
            base_folder.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            print(f"Warning: Could not create folder {base_folder}: {e}")
            base_folder = Path.home() / "Documents" / "CourtTag"
            base_folder.mkdir(parents=True, exist_ok=True)

        suggested_path = str(base_folder / default_filename)

        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Export All Records to CSV",
            suggested_path,
            "CSV Files (*.csv);;All Files (*.*)"
        )

        if not file_path:
            return

        query = """
        SELECT 
            e.id                        AS event_id,
            e.video_id,
            v.game_id,
            g.name                      AS game_name,
            g.date                      AS game_date,
            g.location                  AS game_location,
            g.format,
            g.is_complete,
            e.quarter,
            e.time_ms,
            e.type                      AS event_type,
            e.location                  AS event_location,
            e.shot_quality,             -- ← NEW: Shot Quality column
            e.player_id,
            p.name                      AS player_name,
            p.number                    AS player_number,
            p.team_id                   AS player_team_id,
            e.team_side,
            CASE 
                WHEN p.team_id = g.home_team_id 
                     OR e.team_side IN ('home', 'Home', 'H', '1', 'HOME') THEN 'home'
                WHEN p.team_id = g.guest_team_id 
                     OR e.team_side IN ('guest', 'Guest', 'G', 'A', 'AWAY', '0') THEN 'away'
                ELSE 'unknown'
            END AS normalized_side,
            gr.side                     AS roster_side,
            CASE WHEN gr.id IS NOT NULL THEN 1 ELSE 0 END AS was_rostered,
            CASE 
                WHEN e.type LIKE '2PM%' THEN 2
                WHEN e.type LIKE '3PM%' THEN 3
                WHEN e.type LIKE 'FTM%' THEN 1
                ELSE 0
            END AS points_value,
            CASE WHEN e.type LIKE '%M%' AND e.type NOT LIKE '%A%' THEN 1 ELSE 0 END AS is_make,
            g.home_team_id,
            th.name                     AS home_team_name,
            th.season                   AS home_team_season,
            g.guest_team_id,
            tg.name                     AS guest_team_name,
            tg.season                   AS guest_team_season,
            v.filename,
            v.length_seconds,
            v.record_date
        FROM events e
        LEFT JOIN videos      v   ON v.id  = e.video_id
        LEFT JOIN games       g   ON g.id  = v.game_id
        LEFT JOIN players     p   ON p.id  = e.player_id
        LEFT JOIN teams       th  ON th.id = g.home_team_id
        LEFT JOIN teams       tg  ON tg.id = g.guest_team_id
        LEFT JOIN game_rosters gr ON gr.game_id = g.id 
                                  AND gr.player_id = e.player_id
        ORDER BY g.date DESC, v.id, e.time_ms ASC
        """

        conn = None
        try:
            conn = sqlite3.connect(DB_NAME)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(query)
            rows = cursor.fetchall()

            if not rows:
                QMessageBox.information(self, "Export", "No records found to export.")
                return

            with open(file_path, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                # Header row from column names
                writer.writerow([desc[0] for desc in cursor.description])
                # Data rows
                writer.writerows(rows)

            QMessageBox.information(
                self,
                "Export Complete",
                f"Exported {len(rows):,} event records\n→ {file_path}"
            )

        except sqlite3.Error as e:
            QMessageBox.critical(self, "Database Error", f"Failed to read data:\n{str(e)}")
        except IOError as e:
            QMessageBox.critical(self, "File Error", f"Failed to write CSV:\n{str(e)}")
        except Exception as e:
            QMessageBox.critical(self, "Unexpected Error", str(e))
        finally:
            if conn:
                conn.close()

    def show_season_summary_report(self):
        # Step 1: Select season
        seasons = self.get_available_seasons()
        if not seasons:
            QMessageBox.warning(self, "No Data", "No seasons found in database.")
            return

        # Create the dialog instance first (instead of calling getItem directly)
        dialog = QInputDialog(self)
        dialog.setWindowTitle("Select Season")
        dialog.setLabelText("Choose season for report:")
        dialog.setComboBoxItems(seasons)
        dialog.setComboBoxEditable(False)

        # Baseline minimum (for short names)
        dialog.setMinimumWidth(600)

        # Let content expand it further if needed
        dialog.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        combo = dialog.findChild(QComboBox)
        if combo:
            combo.setSizeAdjustPolicy(QComboBox.AdjustToContents)  # auto-size to longest item
            combo.setMinimumContentsLength(40)  # force at least ~40 chars wide

        ok = dialog.exec_() == QDialog.Accepted
        season_name = dialog.textValue() if ok else None

        # Step 2: Fetch data
        shot_data = self.get_shot_events_for_season(season_name)
        if not shot_data:
            QMessageBox.information(self, "No Data", f"No shot events for {season_name}.")
            return

        # Step 3: Process data
        report = self.compute_shot_report(shot_data)

        # Step 4: Build dialog
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Season Shot Summary Report: {season_name}")
        dialog.setMinimumSize(980, 980)  # Wider to give room for doubled first column

        layout = QVBoxLayout(dialog)

        # Header
        header = QLabel(f"Season: {season_name} - All Teams Shot Location Statistics")
        header.setFont(QFont("Arial", 14, QFont.Bold))
        header.setAlignment(Qt.AlignCenter)
        layout.addWidget(header)

        # Games played label
        main_label = QLabel(f"Total Games Played: {report['games_played']}")
        main_label.setFont(QFont("Arial", 10))
        main_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(main_label)

        # Main table
        main_table = QTableWidget()
        main_table.setRowCount(len(report['main_rows']) + 3)
        main_table.setColumnCount(7)
        main_table.setHorizontalHeaderLabels([
            "Shot Location",
            "Made",
            "Attempted",
            "SHOT%",
            "Points",
            "Total Points",
            "All Attempts"          # ← new column
        ])

        main_table.setEditTriggers(QTableWidget.NoEditTriggers)


        # IMPORTANT: Set resize modes per column
        header = main_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Fixed)  # First column: fixed width (respected)
        for col in range(1, 7):
            header.setSectionResizeMode(col, QHeaderView.Stretch)  # Others stretch to fill remaining space

        # Set explicit widths (first ~2x wider; others start equal but will stretch)
        main_table.setColumnWidth(0, 320)  # Shot Location - noticeably wider
        for col in range(1, 7):
            main_table.setColumnWidth(col, 140)  # Starting point for numeric columns

        # Center-align numeric column headers
        for col in range(1, 7):
            main_table.horizontalHeaderItem(col).setTextAlignment(Qt.AlignCenter)

        total_points = report['totals']['points']
        total_fg_attempts = report['totals']['fg_att']   # ← use field-goal attempts only

        row_idx = 0
        alternate = False

        # Populate main data rows
        for loc, made, att, pct, pts in report['main_rows']:
            pct_total = (pts / total_points * 100) if total_points > 0 else 0.0

            # Conditional for % of All Attempts column (exclude FT from attempt distribution)
            if loc == "1PT - Freethrows":  # exact match — safe
                pct_of_attempts_str = "—"
            else:
                pct_of_attempts = (att / total_fg_attempts * 100) if total_fg_attempts > 0 else 0.0
                pct_of_attempts_str = f"{pct_of_attempts:.1f}%"

            values = [loc, made, att, f"{pct:.1f}%", pts, f"{pct_total:.0f}%", pct_of_attempts_str]

            for col, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if col == 0:
                    item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
                else:
                    item.setTextAlignment(Qt.AlignCenter | Qt.AlignVCenter)
                main_table.setItem(row_idx, col, item)

            # Alternating light blue background for data rows
            if alternate:
                for col in range(7):
                    item = main_table.item(row_idx, col)
                    if item is not None:
                        item.setBackground(QColor(220, 240, 255))  # light blue
            alternate = not alternate
            row_idx += 1

        # Totals row - light grey + bold
        total_row = row_idx
        main_table.setItem(total_row, 0, QTableWidgetItem("Totals"))
        main_table.setItem(total_row, 1, QTableWidgetItem(str(report['totals']['made'])))
        main_table.setItem(total_row, 2, QTableWidgetItem(str(report['totals']['att'])))
        main_table.setItem(total_row, 3, QTableWidgetItem("-"))
        main_table.setItem(total_row, 4, QTableWidgetItem(str(total_points)))
        main_table.setItem(total_row, 5, QTableWidgetItem("100%"))
        main_table.setItem(total_row, 6, QTableWidgetItem("100%"))  # ← ADD THIS LINE HERE (new % of All Attempts)

        for col in range(7):
            item = main_table.item(total_row, col)
            if item is not None:
                item.setBackground(QColor(240, 240, 240))  # light grey
                item.setTextAlignment(Qt.AlignCenter if col > 0 else Qt.AlignLeft | Qt.AlignVCenter)
                item.setFont(QFont("Arial", 10, QFont.Bold))

        row_idx += 1

        # 2PT and 3PT breakout rows - light grey
        for label, stats in [("2PT Shot Stats", report['2pt']), ("3PT Shot Stats", report['3pt'])]:
            main_table.setItem(row_idx, 0, QTableWidgetItem(label))
            main_table.setItem(row_idx, 1, QTableWidgetItem(str(stats['made'])))
            main_table.setItem(row_idx, 2, QTableWidgetItem(str(stats['att'])))
            main_table.setItem(row_idx, 3, QTableWidgetItem(f"{stats['pct']:.1f}%"))
            main_table.setItem(row_idx, 4, QTableWidgetItem(str(stats['points'])))

            # Percentage of total points (includes FT points) – column 5
            pct_of_total = (stats['points'] / total_points * 100) if total_points > 0 else 0.0
            pct_str = f"{pct_of_total:.1f}%"
            item_total_pts = QTableWidgetItem(pct_str)
            item_total_pts.setTextAlignment(Qt.AlignCenter | Qt.AlignVCenter)
            main_table.setItem(row_idx, 5, item_total_pts)

            # FIXED: Percentage of FIELD GOAL attempts only (excludes FT) – column 6
            pct_of_fg_attempts = (stats['att'] / total_fg_attempts * 100) if total_fg_attempts > 0 else 0.0
            pct_fg_att_str = f"{pct_of_fg_attempts:.1f}%"
            item_fg_att = QTableWidgetItem(pct_fg_att_str)
            item_fg_att.setTextAlignment(Qt.AlignCenter | Qt.AlignVCenter)
            main_table.setItem(row_idx, 6, item_fg_att)

            # Style all 7 columns (0-6)
            for col in range(7):
                item = main_table.item(row_idx, col)
                if item is not None:
                    item.setBackground(QColor(240, 240, 240))  # light grey
                    item.setTextAlignment(
                        Qt.AlignCenter | Qt.AlignVCenter if col > 0 else Qt.AlignLeft | Qt.AlignVCenter
                    )

            row_idx += 1

        # Make main table taller
        main_table.setMinimumHeight(690)

        layout.addWidget(main_table)

        main_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        main_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        main_table.setTextElideMode(Qt.ElideNone)  # prevent truncation

        # Per Team Averages section (unchanged from previous)
        avg_label = QLabel("Per Team Averages")
        avg_label.setFont(QFont("Arial", 12, QFont.Bold))
        avg_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(avg_label)

        avg_table = QTableWidget(1, 5)
        avg_table.setHorizontalHeaderLabels([
            "# Teams Played", "Made Shots/team", "Attempted Shots/team", "SHOT%", "Points/team"
        ])
        avg_table.setEditTriggers(QTableWidget.NoEditTriggers)
        avg_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)

        for col in range(5):
            avg_table.horizontalHeaderItem(col).setTextAlignment(Qt.AlignCenter)

        teams = report['per_team']['teams']
        avg_table.setItem(0, 0, QTableWidgetItem(str(teams)))
        avg_table.setItem(0, 1, QTableWidgetItem(f"{report['per_team']['made']:.1f}"))
        avg_table.setItem(0, 2, QTableWidgetItem(f"{report['per_team']['att']:.1f}"))
        avg_table.setItem(0, 3, QTableWidgetItem(f"{report['per_team']['pct']:.1f}%"))
        avg_table.setItem(0, 4, QTableWidgetItem(f"{report['per_team']['points']:.1f}"))

        for col in range(5):
            item = avg_table.item(0, col)
            if item is not None:
                item.setTextAlignment(Qt.AlignCenter | Qt.AlignVCenter)

        avg_table.setMinimumHeight(90)

        layout.addWidget(avg_table)

        # Copy button for upper portion
        copy_btn = QPushButton("Copy Upper Table to Clipboard")
        copy_btn.setFixedWidth(220)
        copy_btn.clicked.connect(
            lambda: self.copy_upper_report_to_clipboard(main_table, season_name, report['games_played']))
        #layout.addWidget(copy_btn)

        # Close button
        close_btn = QPushButton("Close")
        close_btn.setFixedWidth(120)
        close_btn.clicked.connect(dialog.accept)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        btn_layout.addWidget(copy_btn)
        btn_layout.addWidget(close_btn)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)

        dialog.exec_()

    def show_season_shot_quality_report(self):
        """Season Shot Quality Summary Report"""
        seasons = self.get_available_seasons()
        if not seasons:
            QMessageBox.warning(self, "No Data", "No seasons found in database.")
            return

        dialog = QInputDialog(self)
        dialog.setWindowTitle("Select Season")
        dialog.setLabelText("Choose season for Shot Quality Report:")
        dialog.setComboBoxItems(seasons)
        dialog.setComboBoxEditable(False)
        dialog.setMinimumWidth(600)

        if dialog.exec_() != QDialog.Accepted:
            return

        season_name = dialog.textValue()
        if not season_name:
            return

        report_data = self.compute_season_shot_quality_report(season_name)

        if not report_data or report_data['total_fg_attempts'] == 0:
            QMessageBox.information(self, "No Data", f"No shot events found for season '{season_name}'.")
            return

        # Build dialog - slightly taller
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Season Shot Quality Summary - {season_name}")
        dialog.setMinimumSize(780, 380)

        layout = QVBoxLayout(dialog)

        header = QLabel(f"Season Shot Quality Summary: {season_name}")
        header.setFont(QFont("Arial", 14, QFont.Bold))
        header.setAlignment(Qt.AlignCenter)
        layout.addWidget(header)

        games_label = QLabel(f"Total Games Played: {report_data['games_played']}")
        games_label.setFont(QFont("Arial", 10))
        games_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(games_label)

        # Main Table
        table = QTableWidget()
        table.setRowCount(len(report_data['rows']) + 1)
        table.setColumnCount(14)
        table.setHorizontalHeaderLabels([
            "Shot Quality", "2PTM", "2PTA", "2PT%", "3PTM", "3PTA", "3PT%",
            "FGM", "FGA", "eFG%", "PTS", "PPS", "TPT%", "TST%"
        ])

        table.setEditTriggers(QTableWidget.NoEditTriggers)

        header = table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        for col in range(1, 14):
            header.setSectionResizeMode(col, QHeaderView.ResizeToContents)

        row_idx = 0
        for qual, data in report_data['rows']:
            table.setItem(row_idx, 0, QTableWidgetItem(qual))
            table.setItem(row_idx, 1, QTableWidgetItem(str(data['twopm'])))
            table.setItem(row_idx, 2, QTableWidgetItem(str(data['twopa'])))
            table.setItem(row_idx, 3, QTableWidgetItem(f"{data['p2']:.1f}%"))
            table.setItem(row_idx, 4, QTableWidgetItem(str(data['threepm'])))
            table.setItem(row_idx, 5, QTableWidgetItem(str(data['threepa'])))
            table.setItem(row_idx, 6, QTableWidgetItem(f"{data['p3']:.1f}%"))
            table.setItem(row_idx, 7, QTableWidgetItem(str(data['fgm'])))
            table.setItem(row_idx, 8, QTableWidgetItem(str(data['fga'])))
            table.setItem(row_idx, 9, QTableWidgetItem(f"{data['efg']:.1f}%"))
            table.setItem(row_idx, 10, QTableWidgetItem(str(data['pts'])))
            table.setItem(row_idx, 11, QTableWidgetItem(f"{data['pps']:.2f}"))
            table.setItem(row_idx, 12, QTableWidgetItem(f"{data['tpt_pct']:.1f}%"))
            table.setItem(row_idx, 13, QTableWidgetItem(f"{data['tst_pct']:.1f}%"))

            for col in range(1, 14):
                item = table.item(row_idx, col)
                if item:
                    item.setTextAlignment(Qt.AlignCenter)

            row_idx += 1

        # Totals row
        total = report_data['totals']
        table.setItem(row_idx, 0, QTableWidgetItem("Total:"))
        table.setItem(row_idx, 1, QTableWidgetItem(str(total['twopm'])))
        table.setItem(row_idx, 2, QTableWidgetItem(str(total['twopa'])))
        table.setItem(row_idx, 3, QTableWidgetItem(f"{total['p2']:.1f}%"))
        table.setItem(row_idx, 4, QTableWidgetItem(str(total['threepm'])))
        table.setItem(row_idx, 5, QTableWidgetItem(str(total['threepa'])))
        table.setItem(row_idx, 6, QTableWidgetItem(f"{total['p3']:.1f}%"))
        table.setItem(row_idx, 7, QTableWidgetItem(str(total['fgm'])))
        table.setItem(row_idx, 8, QTableWidgetItem(str(total['fga'])))
        table.setItem(row_idx, 9, QTableWidgetItem(f"{total['efg']:.1f}%"))
        table.setItem(row_idx, 10, QTableWidgetItem(str(total['pts'])))
        table.setItem(row_idx, 11, QTableWidgetItem(f"{total['pps']:.2f}"))
        table.setItem(row_idx, 12, QTableWidgetItem("100%"))
        table.setItem(row_idx, 13, QTableWidgetItem("100%"))

        for col in range(14):
            item = table.item(row_idx, col)
            if item:
                item.setFont(QFont("Arial", 10, QFont.Bold))
                item.setTextAlignment(Qt.AlignCenter if col > 0 else Qt.AlignLeft | Qt.AlignVCenter)

        table.setMinimumHeight(240)
        layout.addWidget(table)

        # Copy button + Close
        btn_layout = QHBoxLayout()
        copy_btn = QPushButton("Copy Table to Clipboard")
        copy_btn.clicked.connect(
            lambda: self.copy_season_quality_table(table, season_name, report_data['games_played']))
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(dialog.accept)

        btn_layout.addStretch()
        btn_layout.addWidget(copy_btn)
        btn_layout.addWidget(close_btn)
        btn_layout.addStretch()

        layout.addLayout(btn_layout)

        dialog.exec_()

    def get_available_seasons(self):
        """Return sorted list of unique seasons from teams table"""
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT DISTINCT season FROM teams ORDER BY season DESC")
        seasons = [row[0] for row in c.fetchall() if row[0] and row[0].strip()]
        conn.close()
        return seasons

    def get_shot_events_for_season(self, season_name):
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()

        query = """
        SELECT 
            e.type,
            e.location,
            CASE 
                WHEN e.type LIKE '%Made%' OR e.type LIKE '%M %' THEN 1 ELSE 0
            END AS is_make,
            CASE 
                WHEN e.type LIKE 'FT%' OR e.type LIKE '%Free Throw%' THEN 1 ELSE 0
            END AS is_ft,
            CASE 
                WHEN e.type LIKE '3P%' THEN 1 ELSE 0
            END AS is_3pt,
            CASE 
                WHEN e.type LIKE '2P%' THEN 2
                WHEN e.type LIKE '3P%' THEN 3
                WHEN e.type LIKE 'FT%' OR e.type LIKE '%Free Throw%' THEN 1
                ELSE 0
            END AS points_if_make,
            g.id AS game_id
        FROM events e
        JOIN videos v ON e.video_id = v.id
        JOIN games g ON v.game_id = g.id
        JOIN teams th ON g.home_team_id = th.id
        JOIN teams tg ON g.guest_team_id = tg.id
        WHERE (th.season = ? OR tg.season = ?)
          AND (
              e.type LIKE '%2P%' OR 
              e.type LIKE '%3P%' OR 
              e.type LIKE '%FT%' OR 
              e.type LIKE '%Free Throw%' OR 
              e.type LIKE '%Shot %'   -- catches both Attempt and Made
          )
        ORDER BY g.date, v.id, e.time_ms
        """

        c.execute(query, (season_name, season_name))
        rows = c.fetchall()
        conn.close()

        # Debug: print how many rows and sample
        if rows:
            print("Sample event:", rows[0])

        return rows

    def compute_shot_report(self, events):
        from collections import defaultdict

        location_stats = defaultdict(lambda: {'made': 0, 'att': 0, 'points': 0})

        total_made = total_att = total_points = 0
        fg_made = fg_att = fg_points = 0  # only 2P + 3P
        two_pt = {'made': 0, 'att': 0, 'points': 0}
        three_pt = {'made': 0, 'att': 0, 'points': 0}

        game_ids = set()

        ft_count = 0

        for typ, loc, is_make, is_ft, is_3pt, pts_if_make, game_id in events:
            attempt = 1
            made = is_make
            points = pts_if_make if is_make else 0

            # ──────────────────────────────────────────────
            # STRICT prefix-based classification
            # ──────────────────────────────────────────────
            if typ.startswith(('FTA', 'FTM')):
                key = "1PT - Freethrows"
                ft_count += attempt
            elif typ.startswith(('2PA', '2PM')):
                key = f"2PT @ {loc or 'Unknown'}"
            elif typ.startswith(('3PA', '3PM')):  # assuming 3PM, not 3TM
                key = f"3PT @ {loc or 'Unknown'}"
            else:
                # Unknown type — log it once for visibility
                if fg_att == 0:  # only print first time
                    print(f"UNKNOWN TYPE ENCOUNTERED: '{typ}' | Loc: '{loc}'")
                key = f"Unknown @ {loc or 'Unknown'}"

            location_stats[key]['made'] += made
            location_stats[key]['att'] += attempt
            location_stats[key]['points'] += points

            total_made += made
            total_att += attempt
            total_points += points

            # Add to FG totals ONLY for 2P and 3P events
            if typ.startswith(('2PA', '2PM', '3PA', '3PM')):
                fg_made += made
                fg_att += attempt
                fg_points += points

                # Debug: show first few and milestones
                #if fg_att <= 20 or fg_att % 500 == 0:
                #    print(f"Added FG #{fg_att}: Type '{typ}' | Loc '{loc}' | Made {made}")

            # Sub-totals — same strict condition
            if typ.startswith(('3PA', '3PM')):
                three_pt['made'] += made
                three_pt['att'] += attempt
                three_pt['points'] += points
            elif typ.startswith(('2PA', '2PM')):
                two_pt['made'] += made
                two_pt['att'] += attempt
                two_pt['points'] += points

            if game_id is not None:
                game_ids.add(game_id)

        games_played = len(game_ids) if game_ids else 0

        # Debug summary
        #print(f"DEBUG: Total events = {len(events)}")
        #print(f"DEBUG: fg_att final = {fg_att}  ← should now be 1903")
        #print(f"DEBUG: ft_count = {ft_count}")
        #print(f"DEBUG: 2pt att = {two_pt['att']}")
        #print(f"DEBUG: 3pt att = {three_pt['att']}")
        #print(f"DEBUG: fg_att + ft_count = {fg_att + ft_count}")

        # Build main_rows (unchanged)
        main_rows = []

        ft_key = "1PT - Freethrows"
        if ft_key in location_stats:
            s = location_stats.pop(ft_key)
            pct = (s['made'] / s['att'] * 100) if s['att'] > 0 else 0.0
            main_rows.append((ft_key, s['made'], s['att'], pct, s['points']))

        for key in sorted(location_stats.keys()):
            s = location_stats[key]
            pct = (s['made'] / s['att'] * 100) if s['att'] > 0 else 0.0
            main_rows.append((key, s['made'], s['att'], pct, s['points']))

            # ──────────────────────────────────────────────
            # FINAL POLISH: Derive correct 2PT/3PT from main_rows
            # (guarantees breakout matches main table totals)
            # ──────────────────────────────────────────────
            two_pt_att = 0
            two_pt_points = 0
            three_pt_att = 0
            three_pt_points = 0

            for loc, made, att, pct, pts in main_rows:
                if loc.startswith("2PT"):
                    two_pt_att += att
                    two_pt_points += pts
                elif loc.startswith("3PT"):
                    three_pt_att += att
                    three_pt_points += pts

            # Update the dicts so breakout uses real numbers
            two_pt['att'] = two_pt_att
            two_pt['points'] = two_pt_points
            two_pt['pct'] = (two_pt['made'] / two_pt['att'] * 100) if two_pt['att'] > 0 else 0.0

            three_pt['att'] = three_pt_att
            three_pt['points'] = three_pt_points
            three_pt['pct'] = (three_pt['made'] / three_pt['att'] * 100) if three_pt['att'] > 0 else 0.0

            # Optional: derive fg_att too (for consistency)
            fg_att = two_pt['att'] + three_pt['att']

            #print(f"DEBUG: Derived 2PT att from main_rows = {two_pt['att']} (should be ~1743)")
            #print(f"DEBUG: Derived 3PT att from main_rows = {three_pt['att']} (should be 160)")
            #print(f"DEBUG: Derived fg_att = {fg_att} (should be 1903)")

            # Now continue with your existing pct calculations (they'll now be correct)
            two_pt_pct = two_pt['pct']  # already updated
            three_pt_pct = three_pt['pct']


        per_team_teams = games_played * 2
        per_team = {
            'teams': per_team_teams,
            'made': total_made / per_team_teams if per_team_teams > 0 else 0,
            'att': total_att / per_team_teams if per_team_teams > 0 else 0,
            'pct': (total_made / total_att * 100) if total_att > 0 else 0,
            'points': total_points / per_team_teams if per_team_teams > 0 else 0,
        }

        return {
            'main_rows': main_rows,
            'totals': {
                'made': total_made,
                'att': total_att,
                'points': total_points,
                'fg_made': fg_made,
                'fg_att': fg_att,
                'fg_points': fg_points
            },
            '2pt': {**two_pt, 'pct': two_pt_pct},
            '3pt': {**three_pt, 'pct': three_pt_pct},
            'games_played': games_played,
            'per_team': per_team
        }

    def compute_season_shot_quality_report(self, season_name):
        """Compute shot quality stats across all teams for a season - FIXED"""
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()

        # First: Get total unique games in the season
        c.execute("""
            SELECT COUNT(DISTINCT g.id)
            FROM games g
            JOIN teams th ON g.home_team_id = th.id
            JOIN teams tg ON g.guest_team_id = tg.id
            WHERE (th.season = ? OR tg.season = ?)
              AND g.is_complete = 1
        """, (season_name, season_name))
        total_games = c.fetchone()[0] or 0

        # Second: Get shot quality breakdown
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
            JOIN teams th ON g.home_team_id = th.id
            JOIN teams tg ON g.guest_team_id = tg.id
            WHERE (th.season = ? OR tg.season = ?)
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
        """, (season_name, season_name))

        rows = c.fetchall()
        conn.close()

        if not rows:
            return None

        quality_map = {}
        for full_name, code in SHOT_QUALITY:
            quality_map[code] = full_name.split('/', 1)[0].strip()
        quality_map['None'] = 'Not Assigned'

        report_rows = []
        total_twopm = total_twopa = total_threepm = total_threepa = total_fgm = total_fga = total_pts = 0

        for qual, twopm, twopa_miss, threepm, threepa_miss, pts in rows:
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

            report_rows.append((quality_map.get(qual, qual), {
                'twopm': twopm, 'twopa': twopa, 'p2': p2,
                'threepm': threepm, 'threepa': threepa, 'p3': p3,
                'fgm': fgm, 'fga': fga, 'efg': efg,
                'pts': pts, 'pps': pps
            }))

            total_twopm += twopm
            total_twopa += twopa
            total_threepm += threepm
            total_threepa += threepa
            total_fgm += fgm
            total_fga += fga
            total_pts += pts

        # Final totals percentages
        total_p2 = round((total_twopm / total_twopa * 100), 1) if total_twopa > 0 else 0.0
        total_p3 = round((total_threepm / total_threepa * 100), 1) if total_threepa > 0 else 0.0
        total_efg = round(((total_fgm + 0.5 * total_threepm) / total_fga * 100), 1) if total_fga > 0 else 0.0
        total_pps = round(total_pts / total_fga, 2) if total_fga > 0 else 0.00

        # TPT% and TST% use grand totals
        tpt_pct_list = []
        tst_pct_list = []
        for qual, data in report_rows:
            tpt = round((data['pts'] / total_pts * 100), 1) if total_pts > 0 else 0.0
            tst = round((data['fga'] / total_fga * 100), 1) if total_fga > 0 else 0.0
            tpt_pct_list.append(tpt)
            tst_pct_list.append(tst)

        # Rebuild rows with correct TPT% and TST%
        final_rows = []
        for i, (qual, data) in enumerate(report_rows):
            data['tpt_pct'] = tpt_pct_list[i]
            data['tst_pct'] = tst_pct_list[i]
            final_rows.append((qual, data))

        return {
            'rows': final_rows,
            'totals': {
                'twopm': total_twopm, 'twopa': total_twopa, 'p2': total_p2,
                'threepm': total_threepm, 'threepa': total_threepa, 'p3': total_p3,
                'fgm': total_fgm, 'fga': total_fga, 'efg': total_efg,
                'pts': total_pts, 'pps': total_pps
            },
            'games_played': total_games,
            'total_fg_attempts': total_fga
        }

    def copy_upper_report_to_clipboard(self, table, season_name, games_played):
        """Copy the main table + header as tab-separated text for pasting into Excel/etc."""
        lines = []

        # Header lines
        lines.append(f"Season: {season_name} - All Teams Shot Statistics")
        lines.append(f"Total Games Played: {games_played}")
        lines.append("")  # blank line

        # Table header
        header = "\t".join([table.horizontalHeaderItem(col).text() for col in range(table.columnCount())])
        lines.append(header)

        # All rows (including totals and 2PT/3PT)
        for row in range(table.rowCount()):
            row_data = []
            for col in range(table.columnCount()):
                item = table.item(row, col)
                text = item.text() if item else ""
                row_data.append(text)
            lines.append("\t".join(row_data))

        # Join with newlines
        text_to_copy = "\n".join(lines)

        # Copy to clipboard
        QApplication.clipboard().setText(text_to_copy)

        QMessageBox.information(self, "Copied", "Upper table copied to clipboard!\nPaste into Excel, Sheets, etc.")

    def copy_season_quality_table(self, table, season_name, games_played):
        """Copy Season Shot Quality table to clipboard"""
        lines = []
        lines.append(f"Season Shot Quality Summary: {season_name}")
        lines.append(f"Total Games Played: {games_played}")
        lines.append("")

        # Header
        header = "\t".join([table.horizontalHeaderItem(col).text() for col in range(table.columnCount())])
        lines.append(header)

        # All rows including total
        for row in range(table.rowCount()):
            row_data = []
            for col in range(table.columnCount()):
                item = table.item(row, col)
                text = item.text() if item else ""
                row_data.append(text)
            lines.append("\t".join(row_data))

        text_to_copy = "\n".join(lines)
        QApplication.clipboard().setText(text_to_copy)

        QMessageBox.information(self, "Copied", "Season Shot Quality table copied to clipboard!\nPaste into Excel or Sheets.")

    def share_selected_team_report(self):
        """Called when user clicks the bottom 'Share Team Report' button"""
        selected_rows = self.teams_table.selectionModel().selectedRows()
        if not selected_rows:
            QMessageBox.warning(self, "No Team Selected",
                                "Please select a team in the table first.")
            return

        row = selected_rows[0].row()

        team_id_item = self.teams_table.item(row, 1)
        if not team_id_item or not team_id_item.data(Qt.UserRole):
            QMessageBox.warning(self, "Error", "Could not read team data.")
            return

        team_id = int(team_id_item.data(Qt.UserRole))
        team_name = team_id_item.text()

        self.share_team_report(team_id, team_name)

    def share_team_report(self, team_id: int, team_name: str = None):
        """Upload DB to GitHub + generate share link using scrambled team ID"""
        if not team_id:
            QMessageBox.warning(self, "Error", "No team selected.")
            return

        if not team_name:
            conn = sqlite3.connect(DB_NAME)
            c = conn.cursor()
            c.execute("SELECT name FROM teams WHERE id = ?", (team_id,))
            row = c.fetchone()
            conn.close()
            team_name = row[0] if row else f"Team_{team_id}"

        coach_code = self.get_coach_share_code()

        # Scramble the team ID
        scrambled_team = scramble_id(team_id, "T")

        full_code = f"{coach_code}-{scrambled_team}"

        try:
            GITHUB_TOKEN = "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"
            GITHUB_OWNER = "CourtTag"
            GITHUB_REPO = "courttag-dbs"

            db_filename = f"{coach_code}.db"

            with open(str(DB_NAME), 'rb') as f:
                content = f.read()

            url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{db_filename}"
            headers = {
                "Authorization": f"token {GITHUB_TOKEN}",
                "Accept": "application/vnd.github.v3+json"
            }

            resp = requests.get(url, headers=headers)
            sha = resp.json().get("sha") if resp.status_code == 200 else None

            data = {
                "message": f"Update DB for {coach_code} - {team_name}",
                "content": base64.b64encode(content).decode('utf-8'),
                "branch": "main"
            }
            if sha:
                data["sha"] = sha

            upload_resp = requests.put(url, headers=headers, json=data)

            if upload_resp.status_code not in (200, 201):
                raise Exception(f"GitHub upload failed: {upload_resp.text}")

            link = f"https://courttag.streamlit.app/?code={full_code}"

            QMessageBox.information(
                self,
                "✅ Team Report Shared Successfully!",
                f"Team: {team_name}\n\n"
                f"Parent Link:\n{link}\n\n"
                f"Link copied to clipboard."
            )

            QApplication.clipboard().setText(link)
            webbrowser.open(link)

        except Exception as e:
            QMessageBox.critical(self, "Share Failed", f"GitHub upload error:\n{str(e)}")

    def get_coach_share_code(self) -> str:
        """Stable unique code per coach installation"""
        key = "coach_share_code"
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT value FROM defaults WHERE key = ?", (key,))
        row = c.fetchone()
        if row and row[0]:
            conn.close()
            return row[0]

        import random, string
        code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=10))

        c.execute("INSERT OR REPLACE INTO defaults (key, value) VALUES (?, ?)", (key, code))
        conn.commit()
        conn.close()
        return code


class HelpDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("CourtTag – User Guide")
        self.resize(880, 720)           # a bit wider now
        # self.setMinimumSize(780, 620)

        self.base_dir = Path(__file__).parent

        # ── Define topics and their corresponding HTML files ─────────────
        self.topics = [
            #("User Guide",               "User_Guide.html"),
            ("Frequently Asked Questions", "help/CourtTag_FAQ.html"),
            ("First Time Starting",       "help/CourtTag_Starting.html"),
            ("Event Descriptions",        "help/CourtTag_Event_Descriptions.html"),
            ("Shot Locations",            "help/CourtTag_Shot_Locations.html"),
            ("Shot Quality",              "help/CourtTag_Shot_Quality.html"),
            ("Creating Reports",          "help/CourtTag_Creating_Reports.html"),
            ("Sharing Reports",            "help/CourtTag_Sharing_Reports.html"),
            ("Backing up / Restoring Data", "help/CourtTag_Backup_Restore.html"),
            ("Creating Video Clips",      "help/CourtTag_Creating_Video_Clips.html"),
        ]

        self.main_layout = QHBoxLayout(self)
        self.main_layout.setContentsMargins(12, 12, 12, 12)
        self.main_layout.setSpacing(10)

        # ── LEFT: Topic list ─────────────────────────────────────────────
        left_widget = QVBoxLayout()
        left_widget.setSpacing(8)

        title_label = QLabel("Topics")
        title_label.setStyleSheet("font-weight: bold; font-size: 14px;")
        left_widget.addWidget(title_label)

        self.topic_list = QListWidget()
        self.topic_list.setFixedWidth(240)
        self.topic_list.setAlternatingRowColors(True)
        self.topic_list.setSizePolicy(
            QSizePolicy.Fixed,  # width stays fixed
            QSizePolicy.Expanding  # height grows with available space
        )
        self.topic_list.setMinimumHeight(400)
        self.topic_list.setStyleSheet("""
            QListWidget {
                background-color: #f8f9fa;
                border: 1px solid #d0d0d0;
                border-radius: 4px;
            }
            QListWidget::item:selected {
                background-color: #007acc;
                color: white;
            }
        """)

        # Populate list
        for title, _ in self.topics:
            self.topic_list.addItem(title)

        # Auto-select and load first item
        if self.topics:
            self.topic_list.setCurrentRow(0)

        self.topic_list.currentRowChanged.connect(self.on_topic_changed)
        left_widget.addWidget(self.topic_list)

        #left_widget.addStretch()
        self.main_layout.addLayout(left_widget)

        # ── RIGHT: Content area ──────────────────────────────────────────
        right_layout = QVBoxLayout()

        #self.text_edit = QTextEdit()
        self.text_edit = QTextBrowser()
        self.text_edit.setOpenExternalLinks(True)  # ← Critical
        self.text_edit.setReadOnly(True)
        self.text_edit.setStyleSheet("""
            QTextBrowser {
                background-color: white;
                border: 1px solid #c0c0c0;
                font-family: 'Segoe UI';
                font-size: 14px;
                border-radius: 4px;
                padding: 8px;
            }
        """)

        right_layout.addWidget(self.text_edit)

        # Bottom buttons
        btn_box = QDialogButtonBox(QDialogButtonBox.Close)
        btn_box.rejected.connect(self.reject)
        right_layout.addWidget(btn_box)

        self.main_layout.addLayout(right_layout)

        self.main_layout.setStretch(0, 1)  # left column
        self.main_layout.setStretch(1, 4)  # right column (wider

        # ── Load initial content ─────────────────────────────────────────
        self.load_initial_content()

    def load_initial_content(self):
        """Load first topic on startup"""
        if self.topics:
            self.on_topic_changed(0)

    # In on_topic_changed (and load_initial_content calls the same logic):
    def on_topic_changed(self, row: int):
        if row < 0 or row >= len(self.topics):
            self.text_edit.setHtml("<p style='color:gray;'>No topic selected</p>")
            return

        title, filename = self.topics[row]

        # Resource path — matches prefix + filename from .qrc
        res_path = f":/help/{filename}"

        file = QFile(res_path)
        if not file.open(QIODevice.ReadOnly | QIODevice.Text):
            msg = f"<h3>Resource not found</h3><p>{res_path}</p>"
            self.text_edit.setHtml(msg)
            return

        try:
            html = file.readAll().data().decode('utf-8')
            self.text_edit.setHtml(html)
        except Exception as e:
            msg = f"<h3>Error reading resource</h3><p>{str(e)}</p><p>{res_path}</p>"
            self.text_edit.setHtml(msg)
        finally:
            file.close()

class DefaultsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("CourtTag – Set Defaults")
        self.setFixedSize(480, 220)

        layout = QFormLayout(self)
        layout.setSpacing(12)

        # Default Season
        self.season_combo = QComboBox()
        self.season_combo.addItem("None – Show All Seasons")

        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT DISTINCT season FROM teams "
                  "WHERE season IS NOT NULL AND season != '' "
                  "ORDER BY season DESC")
        for (season,) in c.fetchall():
            self.season_combo.addItem(season)
        conn.close()

        layout.addRow("Default Season:", self.season_combo)

        # A-B Ratio Minimum Shots
        self.abr_spin = QSpinBox()
        self.abr_spin.setRange(0, 100)
        self.abr_spin.setValue(10)
        layout.addRow("A-B Shot Minimum Filter:", self.abr_spin)

        # Check for Updates
        self.check_updates_cb = QCheckBox("Check for CourtTag Updates on Load")
        layout.addRow("Auto Check:", self.check_updates_cb)

        # Buttons
        btn_box = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        btn_box.accepted.connect(self.save)
        btn_box.rejected.connect(self.reject)
        layout.addRow(btn_box)

        self.load_current_defaults()

    def load_current_defaults(self):
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()

        # Season
        c.execute("SELECT value FROM defaults WHERE key = 'default_season'")
        row = c.fetchone()
        if row and row[0]:
            idx = self.season_combo.findText(row[0])
            if idx >= 0:
                self.season_combo.setCurrentIndex(idx)

        # A-B Ratio minimum
        c.execute("SELECT value FROM defaults WHERE key = 'abr_minimum'")
        row = c.fetchone()
        if row and row[0]:
            self.abr_spin.setValue(int(row[0]))

        # Check-for-updates
        c.execute("SELECT value FROM defaults WHERE key = 'check_for_updates_on_load'")
        row = c.fetchone()
        checked = (row and row[0] == 'yes') if row else True
        self.check_updates_cb.setChecked(checked)

        conn.close()

    def save(self):
        season_text = self.season_combo.currentText()
        season_value = None if season_text == "None – Show All Seasons" else season_text

        abr_min = self.abr_spin.value()

        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()

        c.execute("INSERT OR REPLACE INTO defaults (key, value) VALUES ('default_season', ?)", (season_value,))
        c.execute("INSERT OR REPLACE INTO defaults (key, value) VALUES ('abr_minimum', ?)", (str(abr_min),))

        check_value = 'yes' if self.check_updates_cb.isChecked() else 'no'
        c.execute("INSERT OR REPLACE INTO defaults (key, value) VALUES ('check_for_updates_on_load', ?)", (check_value,))

        conn.commit()
        conn.close()

        if self.parent():
            self.parent().apply_default_season_to_all_tabs()

        self.accept()

if __name__ == "__main__":

    init_db()

    app = QApplication(sys.argv)

    # Suppress libpng iCCP warnings
    warnings.filterwarnings("ignore", category=UserWarning, module="PIL")

    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('ian.gruber.courttag.1.0')
    except:
        pass

    app.setWindowIcon(QIcon(":/CourtTag_Icon.ico"))
    app.setStyle("Fusion")

    splash = None
    try:
        pixmap = QPixmap(":/CourtTag_Load_Screen_v1.png")
        if not pixmap.isNull():
            splash = QSplashScreen(pixmap, Qt.WindowStaysOnTopHint)
            splash.show()
            splash.showMessage(
                f"Loading CourtTag v1.0{BUILD}...",
                Qt.AlignBottom | Qt.AlignCenter,
                Qt.white
            )
            app.processEvents()
            time.sleep(1.8)
    except Exception as e:
        print(f"Splash screen could not be loaded: {e}")

    window = CoachesStatTracker()
    window.show()

    if splash is not None:
        splash.finish(window)

    # ─────────────────────────────────────────────────────────────
    # NEW: Silent version check AFTER splash is gone and window is visible
    # ─────────────────────────────────────────────────────────────
    def run_version_check():
        try:
            conn = sqlite3.connect(DB_NAME)
            c = conn.cursor()
            c.execute("SELECT value FROM defaults WHERE key = 'check_for_updates_on_load'")
            row = c.fetchone()
            conn.close()

            if row and row[0] == 'yes':
                window.check_for_update(silent=True)   # silent = True → no "up to date" message
        except Exception as e:
            print(f"Version check failed (non-critical): {e}")

    # Run the check ~300ms after splash finishes (gives UI time to settle)
    QTimer.singleShot(300, run_version_check)

    sys.exit(app.exec_())
