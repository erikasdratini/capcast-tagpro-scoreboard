CAPCAST — LOCAL TAGPRO OBS SCOREBOARD
=====================================
Version 1.1 (community release) — local-first, no external services, no Python packages needed.

QUICK START (WINDOWS)
1. Extract the entire ZIP. Do not run it from inside the ZIP.
2. Install Python 3 if needed: https://www.python.org/downloads/
   On Windows, enable "Add Python to PATH" when installing.
3. Double-click start-windows.bat. A browser dashboard will open.
4. In OBS, Sources > + > Browser. URL:
       http://127.0.0.1:8765/overlay.html
   Set Width to about 1100 (five games) or 1320 (seven games); Height 280.
   For best compatibility, use 1450 x 280 and resize the Source via OBS Transform.
   Position the overlay wherever you like, and scale it in OBS if needed.
5. Keep the black console window running during your broadcast.
6. The browser dashboard is: http://127.0.0.1:8765/

MAC / LINUX
Open Terminal in the extracted folder and run: python3 server.py
Or use the supplied start-mac.command / start-linux.sh.

WORKFLOW
- Pick the top and bottom teams.
- Select 5 games (regular season) or 7 games (playoffs).
- Choose a map preset and Apply, or type map names into each game.
- Type the two final scores after each game. They appear on stream only
  when BOTH scores are filled in; before that the game shows —.
- A green score wins that game. TOTAL counts game wins; AGG sums the
  entered main-game caps for completed games.
- For a tied score, tick OT GAME and choose the overtime winner.
  OT only breaks the tie for TOTAL; it does not add caps to AGG.
- Swap teams safely flips all game scores and overtime winners.
- Clear Scores removes scores and OT results without changing maps/teams.
- Change team names, logos and colors in Team Library.
- Scale the overlay in the control panel or with OBS's transform tool.

TEAMS
The starter database has 30 teams, including all 22 named teams from the
screenshot (8 major / 8 minor / 6 novice) and 8 more team records drawn
from Season 39 official MLTP listings. Historical name/code spellings and
all original hex colors/logos need to be verified against the original
spreadsheet. Screenshot-sampled colors and S39 colors are PLACEHOLDERS.
Use Team Library to edit them or upload a local PNG/JPEG/WEBP logo.

Import team data as JSON or CSV:
  JSON: {"teams":[{"code":"EX","name":"Example Team",
    "primary":"#11ABCD","secondary":"#FFFFFF","era":"S39 · Majors"}]}
  CSV header: code,name,primary,secondary,era
Export the current team library with EXPORT.

MAPS
Two initial presets are included: a Sardonica/Milano legacy rotation
and an empty template. Add, edit, delete and save your own.

BACKUP & PRIVACY
Data is saved in data/state.json, within the extracted project folder.
The app binds to 127.0.0.1 only: NOT visible to the internet or LAN.
Requests with unexpected Host headers or POST Origin headers are rejected. State writes
require JSON. There is no password: other local software may still change data.
It does not make any remote requests. Remote logo URLs are not used.
Use Download Backup regularly; Restore Backup replaces the whole state.
Only import backups, team libraries, or images you trust. The community ZIP has no demo scores.

LIMITATIONS / WHAT'S NEXT
- This version does not retrieve scores automatically from TagPro.
- It does not synchronize across two separate computers.
- It does not ingest XLSX directly. If you send the original spreadsheet,
  the full legacy team database and exact colors can be migrated.
- Changing an OT winner when the regulation score isn't tied has no effect
  on automatic totals; caps entered in the two score fields are added to AGG.
- 5/7 format is based on the intended broadcast workflow, not a guarantee
  of league rules in all seasons/divisions.
- This community version has not received an independent security review; test it in OBS.
Do not expose or port-forward TCP port 8765; keep Python updated.
When updating, back up your data/state.json and do not overwrite it.

STOPPING THE SERVER
Close the console window or press Ctrl+C. Restart using the same batch.
Data remains saved between sessions.
