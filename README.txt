CAPCAST - LOCAL TAGPRO OBS SCOREBOARD
=====================================
Version 1.1.1 (community beta) - runs on your computer, no accounts, no cloud,
no Python packages to install.

QUICK START (WINDOWS)
1. Extract the entire ZIP to a NEW folder. Do not run it from inside the ZIP.
2. Install Python 3.11 or newer (3.12+ preferred): https://www.python.org/downloads/
   On Windows, tick "Add Python to PATH" when installing.
   (Python 3.9 and 3.10 still start CAPCAST, with a warning: they no longer
   receive security updates.)
3. Double-click start-windows.bat. A browser dashboard will open.
4. In OBS: Sources > + > Browser. Leave "Local file" unticked. URL:
       http://127.0.0.1:8765/overlay.html
   Width/Height: use the SOURCE WIDTH and SOURCE HEIGHT shown in the
   dashboard's OBS CONNECTION panel (about 1100 x 260 for 5 games and
   1320 x 260 for 7 games at 100%; larger if you raise OVERLAY SIZE).
   Set "Page permissions" to "No access to OBS". CAPCAST needs none.
5. Keep the console window open during your broadcast.
6. The dashboard is: http://127.0.0.1:8765/   (use 127.0.0.1, not "localhost")

MAC / LINUX
Open Terminal in the extracted folder and run:  python3 server.py
Or use start-mac.command / start-linux.sh. (macOS may ask you to confirm
opening a downloaded file the first time: right-click > Open.)
Options: --port 8766 (another port), --no-browser (don't open a tab).

WORKFLOW
- Pick the top and bottom teams.
- Select 5 games (regular season) or 7 games (playoffs). A custom OVERLAY
  LABEL is kept when you switch formats.
- Choose a map preset and Apply, or type map names into each game.
- Type the two final scores after each game. They appear on stream only
  when BOTH scores are filled in; before that the game shows -.
  Score boxes accept whole numbers 0-999. Anything else turns red and is NOT
  published until you fix it; the mouse wheel never changes a score.
- A green score wins that game. TOTAL counts game wins; AGG sums the
  caps of completed games, including the OT cap below.
- For a tied score, tick OT GAME and choose the overtime winner. Until you
  do, the tied game is shown as pending and is not counted in TOTAL, AGG or
  "games completed". The OT winner gets one extra cap on stream and in AGG
  (2-2 won in OT shows 3-2); the score boxes keep the 2-2 you entered.
- Swap teams flips all game scores and overtime winners.
- Clear Scores removes scores and OT results (a backup is saved first).
- Change team names, logos and colors in Team Library. Logos are resized
  to 256 px on your computer before they are saved.

SYNC STATUS (TOP RIGHT OF THE DASHBOARD)
- SAVED - REV n        : your changes are saved (n counts saved versions).
- OBS IN SYNC - REV n  : the OBS browser source is showing exactly that
                         version. Shown only when nothing is waiting to save.
- OBS NOT CONNECTED    : no OBS browser source is showing the overlay.
- Red messages (SAVE BLOCKED, CONFLICT, SERVER OFFLINE, OBS SHOWING OLD
  SCORES, NOT PUBLISHED) also show a banner with what to do. Until they
  clear, what is on stream may differ from the dashboard.
- The status is checked once a second. A change made somewhere else (another
  window, a restart, an edited file) can take up to about 2 seconds to show.
- Use one dashboard at a time. If two are open (a browser tab and an OBS
  dock, say), CAPCAST keeps them in sync, refuses to let one overwrite the
  other, and shows a banner.

TEAMS
The starter list has 30 teams (22 legacy MLTP teams and 8 Season 39 teams).
Names, codes and colors are approximate placeholders; edit them freely.

Import team data as JSON or CSV:
  JSON: {"teams":[{"code":"EX","name":"Example Team",
    "primary":"#11ABCD","secondary":"#FFFFFF","era":"S39 · Majors"}]}
  CSV header: code,name,primary,secondary,era
An import is all-or-nothing: if the result would break a limit (700 teams,
3.5 MB of data in total), nothing is changed and you are told why.
Export the current team library with EXPORT.

MAPS
Two presets are included: a Sardonica/Milano legacy rotation and an empty
template. Add, edit, delete and save your own.

BACKUP & DATA
Your data is saved in data/state.json inside the CAPCAST folder. CAPCAST
also keeps automatic copies in data/backups/ (before Restore, Clear Scores,
team/preset deletion and imports, plus one every 10 minutes while you
work; the newest 30 are kept). Use DOWNLOAD BACKUP regularly; RESTORE
BACKUP shows what it will change and asks before replacing anything.
v1.1 backups restore normally.

UPGRADING FROM v1.1
Recommended: in v1.1 click DOWNLOAD BACKUP, extract v1.1.1 to a new folder,
start it, then click RESTORE BACKUP and pick that file.
Or: copy your old data/state.json into the new folder's data/ folder before
the first start. CAPCAST upgrades it, keeps an exact copy of the original in
data/backups/, and lists anything it adjusted under SERVER NOTICES.
The v1.1.1 ZIP no longer contains data/state.json, so extracting it over an
old folder cannot overwrite your data.

PRIVACY & SECURITY (details: SHARING_AND_SECURITY.txt)
- The server listens only on 127.0.0.1 (this computer). Other devices on
  your network or the internet cannot connect unless you forward the port.
  Do not expose or port-forward TCP port 8765.
- Web pages open in your browser CAN send requests to 127.0.0.1. CAPCAST
  refuses requests from other websites (Host, Origin and Sec-Fetch-Site
  checks) and tells the browser not to let other sites load its files.
- There is no password. Any program running on this computer under your
  account can read or change your scoreboard data.
- CAPCAST makes no internet requests and loads no remote images or fonts.
- Only import team libraries, backups and logos from people you trust.
  CAPCAST checks them, but a crafted file can still slow the dashboard down.

LIMITATIONS
- Scores are entered by hand; no automatic TagPro integration.
- No sync between two computers; OBS and CAPCAST must run on one computer.
- No direct XLSX import.
- Formats are presentation choices, not a guarantee of league rules.
- OBS 30 and older use an old browser engine; parts of the overlay styling
  may look different there. OBS 31 or newer is recommended.
- v1.1.1 received an AI-assisted code review and automated tests on Linux
  (November 2026). That is not a formal security audit, penetration test or
  certification. Rehearse in OBS before going live.

STOPPING THE SERVER
Close the console window or press Ctrl+C. Start again with the same launcher.
Your data stays in data/.

LICENSE: MIT (see LICENSE). Team names and logos may belong to their owners.
