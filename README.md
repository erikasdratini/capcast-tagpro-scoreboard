# CAPCAST

### A modern, customizable TagPro scoreboard overlay for OBS Studio.

**CAPCAST** is a free, locally hosted scoreboard system designed for competitive TagPro broadcasts. It combines a transparent, broadcast-ready OBS overlay with a dedicated control dashboard, allowing commentators and streamers to manage matches without touching their OBS configuration.

No subscriptions. No accounts. No cloud services. Just a scoreboard that works.

**Version:** 1.1 — Community Beta  
**Platforms:** Windows, macOS, Linux  
**Requirements:** Python 3 and OBS Studio  
**Cost:** Free

---

## Features

### Broadcast-ready scoreboard
- Modern, dark-themed scoreboard with team-color accents.
- Transparent background for seamless OBS integration.
- Five-game regular-season and seven-game playoff layouts.
- Automatically expanding scoreboard for longer series.
- Customizable team names, abbreviations, colors, and logos.
- Map names displayed for each game.
- Highlighted winning scores.
- Automatic series-win and aggregate-cap calculations.
- Flexible positioning and scaling within OBS.

### Dedicated control dashboard
- Manage everything from a separate browser window.
- Select teams from a searchable database.
- Enter final scores after each game.
- Automatically calculate game wins and aggregate caps.
- Track overtime results and designate overtime winners.
- Swap teams without losing their results.
- Clear scores between matches.
- Preview the broadcast scoreboard before and during a stream.
- Update the OBS overlay automatically when changes are made.

### Customizable team library
- 30 starter team records, including historical competitive TagPro teams.
- Add, edit, and remove teams.
- Customize team abbreviations and colors.
- Upload PNG, JPG, or WebP team logos.
- Search teams by name, abbreviation, or division.
- Import and export team databases using JSON or CSV.

**Note:** Historical team colors are approximate, and logos are placeholders until verified source data is incorporated. All teams can be customized.

### Map rotation management
- Create and save custom map rotation presets.
- Apply a complete rotation to a match.
- Override individual map selections at any time.
- Support up to seven map slots per preset.
- Reuse saved rotations across broadcasts.

### Local data and backups
- Automatically save match and team configuration data.
- Restore previously saved configurations.
- Export backups for safekeeping.
- Retain settings between sessions.
- Operate without external servers or accounts.

---

## Download

**[Download CAPCAST from GitHub Releases](../../releases)**

Download the latest ZIP from the Releases section and extract it to a folder on your computer.

You do not need to install Git or clone the repository.

---

## Installation

### Windows

**Step 1 — Install Python**

CAPCAST requires Python 3.

If you don't already have it installed, download it from:

https://www.python.org/downloads/

During installation, enable **Add Python to PATH** if that option is available.

No additional Python packages are required.

**Step 2 — Download CAPCAST**

1. Open the Releases section of this repository.
2. Download the latest CAPCAST ZIP.
3. Right-click the ZIP and select **Extract All**.
4. Open the extracted `CAPCAST_TagPro_Scoreboard` folder.

**Step 3 — Launch CAPCAST**

Double-click:

`start-windows.bat`

A terminal window will open, starting the local scoreboard server.

Your default browser should automatically open the CAPCAST control dashboard.

If it doesn't, navigate to:

`http://127.0.0.1:8765/`

**Keep the terminal window open while broadcasting.** Closing it stops the local scoreboard server.

### macOS

1. Install Python 3 if necessary.
2. Download and extract CAPCAST.
3. Open Terminal in the extracted folder.
4. Run:

```bash
python3 server.py
```

You can also use the included `start-mac.command` launcher if your system permits it.

### Linux

1. Ensure Python 3 is installed.
2. Download and extract CAPCAST.
3. Open a terminal in the extracted folder.
4. Run:

```bash
python3 server.py
```

An optional `start-linux.sh` launcher is also included.

---

## Connecting CAPCAST to OBS

Once CAPCAST is running:

1. Open OBS Studio.
2. Select the scene where you want your scoreboard.
3. Under **Sources**, click the **+** button.
4. Select **Browser**.
5. Create a new source named `CAPCAST Scoreboard`.
6. Leave **Local file** unchecked.
7. Enter the following URL:

```text
http://127.0.0.1:8765/overlay.html
```

8. Set the Browser Source dimensions:

| Setting | Recommended value |
|---|---|
| Width | 1450 |
| Height | 280 |
| Local file | Unchecked |
| Custom CSS | Default is fine |

9. Click **OK**.
10. Position and resize the scoreboard using OBS's transform handles.

The overlay background is transparent, allowing the scoreboard to appear directly over gameplay.

**Important:** CAPCAST and OBS should run on the same computer. The scoreboard server uses your computer's local loopback connection.

---

## Using CAPCAST

### 1. Configure a match

Open the control dashboard:

`http://127.0.0.1:8765/`

Choose your teams, enter the event information, and select a match format:

- **5 Games:** Regular-season layout.
- **7 Games:** Playoff layout.

These formats are configurable presentation options and may not reflect the rules of every TagPro league or season.

### 2. Choose the maps

Select a saved map rotation preset or enter the map names manually.

Presets can be created and edited from the **Map Presets** tab.

### 3. Enter scores

After each game, enter both teams' final scores into the dashboard.

The game result appears on the OBS overlay when both scores are entered.

CAPCAST automatically updates:

**TOTAL:** Number of games won by each team.

**AGG:** Aggregate caps scored across completed games.

Winning scores are visually highlighted.

For tied regulation scores, you can enable the overtime option and specify the overtime winner. Overtime determines the game winner but does not automatically add additional caps to the aggregate total.

### 4. Start the next match

Select the next two teams, update your maps, and use **Clear Scores** to reset the previous results.

The OBS Browser Source does not need to be recreated.

---

## Team Database

CAPCAST includes 30 starter team records spanning multiple eras of competitive TagPro.

The team library supports:

- Custom team names.
- Team abbreviations.
- Primary and secondary colors.
- Custom logos.
- League or season information.
- JSON and CSV imports and exports.

Additional teams can be added at any time.

The starter database is not intended to be a definitive historical archive. Team names, colors, and logos may require verification.

---

## How CAPCAST Works

CAPCAST consists of two primary interfaces:

**Control Dashboard**

`http://127.0.0.1:8765/`

This is where you manage teams, maps, scores, settings, and backups.

**OBS Overlay**

`http://127.0.0.1:8765/overlay.html`

This displays only the broadcast scoreboard, without the control interface.

Both are served by a lightweight Python server running locally on your computer.

Changes made in the control dashboard are automatically reflected in the OBS overlay, without manually refreshing the Browser Source.

No third-party Python packages are required.

---

## Privacy and Security

CAPCAST is designed to operate locally.

- The server binds to `127.0.0.1`, rather than accepting connections from your local network or the public internet.
- Match data is stored on your own computer.
- No user account or authentication service is required.
- No external analytics or cloud synchronization is used.
- No third-party Python packages are needed.
- The server includes input validation, request-size limits, restricted file serving, and request-origin checks.

**Security considerations:**

CAPCAST is an experimental community release and has not received an independent security audit.

The local dashboard does not use authentication, so other software running on the same computer may potentially access its data.

Do not expose or port-forward port `8765` to the internet. Only import backups and files from sources you trust, and keep your Python installation updated.

The source code is available in this repository for community inspection.

---

## Backups and Updates

CAPCAST saves its local application state in:

`data/state.json`

This includes team configurations, map presets, and match data.

Before installing a newer version:

1. Download a backup from the control dashboard.
2. Keep a copy of your current `data/state.json`.
3. Download and extract the new CAPCAST release.
4. Restore your previous data if necessary.

**Do not overwrite your existing `state.json` without backing it up first.**

---

## Current Limitations

CAPCAST is an early community release.

Currently:

- Scores are entered manually after each game.
- Live in-game scoring is not supported.
- Automatic TagPro match-data integration is not included.
- The control dashboard and OBS overlay must run on the same computer.
- Direct Excel (`.xlsx`) importing is not supported.
- Historical team colors and logos are not yet fully verified.
- The application has not undergone an independent security audit.

These limitations may be addressed in future versions.

---

## Feedback, Bugs, and Feature Requests

CAPCAST is an ongoing community project.

If you encounter a bug, have an idea for a feature, or want to suggest an improvement, please use the **Issues** tab on this repository.

Feedback from TagPro players, commentators, and tournament organizers is welcome.

Potential future improvements include expanded historical team data, additional overlay themes, more customization options, and improved broadcast automation.

---

## Contributing

Contributions and suggestions are welcome.

If you're interested in improving CAPCAST, feel free to review the source code, open an issue, or submit a pull request.

Please avoid including personal match backups, private account information, or copyrighted team graphics without permission.

Before redistributing modified versions, check the repository's applicable license.

---

## Acknowledgments

Built for the competitive **TagPro** community and the commentators who help keep its matches entertaining.

CAPCAST is an independent community project and is not officially affiliated with TagPro, Major League TagPro, or OBS Studio.

---

**CAPCAST — Built for TagPro. Made for the broadcast.**
