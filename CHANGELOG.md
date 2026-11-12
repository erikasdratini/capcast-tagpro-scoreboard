# Changelog

## 1.1.1 (2026-11-11): security and sync hardening

A patch release that fixes the issues found in the v1.1 security review. The look, the features and the five- and seven-game layouts are unchanged except where noted. Existing v1.1 data and backups load without loss.

### Fixed: what is on stream now always matches the dashboard, or the dashboard says so

- **One rejected save no longer freezes OBS (F-01).**
  - Every edit is checked against the same limits the server uses *before* it is applied. Imports are all-or-nothing.
  - If the server still refuses a change, saving stops and a red banner explains why, with **Revert**, **Download unsaved changes** and **Retry**. There is no silent retry loop.
  - Unicode is handled the way the server counts it. Uppercasing happens before truncation, so `ß` → `SS` can no longer overflow, and emoji are never split.
- **Live sync status replaces the fixed "SYNCED" label (F-01, requirement 6).**
  - The header shows `SAVED · REV n` and `OBS IN SYNC · REV n`.
  - The overlay reports which revision it is showing. "In sync" appears only when OBS shows the latest saved version and the dashboard has nothing unpublished.
  - Otherwise the header shows `OBS NOT CONNECTED`, `OBS UPDATING`, `OBS SHOWING OLD SCORES` or `OBS ERROR`. Red states also show a banner, and the tab title gains a ⚠.
  - The status is checked once a second. Your own edits show `WAITING FOR SAVE` at once; a change made somewhere else (another window, a restart, an edited file) can take up to about 2 seconds to show.
  - The overlay inside the dashboard's preview never counts as an OBS source, even if the page has `window.obsstudio`.
  - The map name is published while you type, like scores.
- **Two dashboards can no longer overwrite each other (F-02).**
  - Saves carry the version they were based on (revision and save time), and the server refuses stale ones.
  - A clean second window follows changes automatically.
  - A window with unsaved edits shows **CHANGED IN ANOTHER WINDOW**, with **Load latest**, **Download my version** and **Overwrite with mine**.
  - The team editor saves only the fields you changed, so it keeps another window's edits to the same team. If both windows changed the same field, it refuses and shows the other value.
  - A banner appears when more than one dashboard is open.
- **Restarts are detected.** Each CAPCAST start has its own id. If CAPCAST restarts with the same saved data, nothing changes. If it restarts with different data (another folder, a restored or edited file), a clean dashboard reloads it and a dashboard with unsaved edits shows **CAPCAST WAS RESTARTED**; nothing is overwritten automatically.
- **Score boxes (F-04)** are now text fields that accept 0–999 only.
  - The mouse wheel and arrow keys no longer change scores.
  - Invalid input such as `3.5` or `-2` turns red, shows **NOT PUBLISHED**, and is never silently replaced by an older value.
  - Full-width digits (`７`) are accepted.
- **Overtime (F-05).** A tied game counts only once **OT GAME** is ticked and a winner is chosen. Until then it shows as pending on stream and in the dashboard. An overtime winner without OT ticked is refused.

### Fixed: security

- **Untrusted files (F-03).**
  - Team imports are size-capped (CSV 1 MB, JSON 3.5 MB) and run in linear time. CSV files may have at most 64 columns and 1,400 rows; unknown columns are ignored.
  - The dashboard and the server measure the 3.5 MB data limit identically, so the server never accepts data that the dashboard would then refuse to publish.
  - Fields of the wrong type (for example a number where a team id belongs) are refused with a message instead of crashing startup or the server.
  - Logos are checked from their file header: type must match contents, at most 2048×2048 and 600 KB. A 16000×16000 "decompression bomb" is refused.
  - Imported logos are then re-encoded to a PNG of at most 256 px, which also strips EXIF/GPS metadata.
- **Unsafe rendering (F-06).**
  - Every value is escaped, scores included.
  - `data/state.json` is validated at startup and whenever it changes on disk. Invalid files are copied to `data/quarantine/` and never rendered.
  - The CSP is now `default-src 'none'` with `base-uri 'none'`, `form-action 'none'` and no `unsafe-inline`. Colours are set through the CSSOM.
- **Other websites (F-07).**
  - New `Cross-Origin-Resource-Policy: same-origin` and `Cross-Origin-Opener-Policy: same-origin` headers.
  - A `Sec-Fetch-Site` gate, so other sites can no longer load `app.js` or `styles.css` to detect CAPCAST.
  - The unneeded CORS header was removed, and error pages no longer use Python's HTML template.
- **Safer writes (F-08).**
  - Writes use a temp file, fsync and atomic replace, with retries on Windows file locks.
  - Temporary files are always removed, and leftovers in `data/`, `data/backups/` and `data/quarantine/` are cleaned at startup.
  - If `data/state.json` is a symbolic link, the file it points to is updated and the link is kept.
  - Automatic backups go to `data/backups/`: before Restore, Clear Scores, deletions and imports, and every 10 minutes. The newest 30 automatic backups are kept. Rotation deletes only files CAPCAST named itself and never follows symbolic links; migration copies and your own files are never deleted.
  - If a safety backup cannot be written (for example, the disk is full), the change is still saved and a notice says so. Previously this showed up as a save error followed by a false conflict.
  - A `state.json` replaced while CAPCAST runs is copied byte-for-byte to `data/backups/external-original-*.json` before it is upgraded or checked.
- **Server notices** have unique ids, so dismissing one never hides later ones. Control characters in field names are shown as escapes rather than sent to the console. Long lists of adjustments go to a report file in `data/backups/`.
- **Validation (F-09).**
  - The schema is strict: unknown fields are refused, `mode` must be 5 or 7, and preset ids must be unique and well formed.
  - Errors name the exact field. NaN, huge numbers and deeply nested JSON are refused cleanly.
- **Robustness (F-11).**
  - Idle connections time out after 15 s, and at most 48 are served at once.
  - Windows uses an exclusive port bind.
  - A clear message replaces the traceback when the port is busy. `--no-browser` is a new option.

### Fixed: other

- **Team logos** now sit on a subtle glow in the team's colour instead of a square box, on stream and in the dashboard's team selectors. Size and position are unchanged; teams without a logo keep the boxed initials.
- **Logo upload works again (F-10).** The browser's `createImageBitmap` is used, so the strict CSP needs no `blob:`.
- **Overlay size (F-13).** The dashboard shows both SOURCE WIDTH and SOURCE HEIGHT for the current format and scale. The old "250 PX" height cut off the bottom line.
- **Labels and screen readers.** The OT winner menu reads "game 1", not "game 0". Switching between 5 and 7 games keeps a custom overlay label.
- **Launchers (F-12).**
  - `start-linux.sh` and `start-mac.command` are executable and pass options through.
  - `start-windows.bat` uses CRLF line endings and explains a missing Python.
  - All launchers run Python in isolated mode (`-I`) and say that 3.11 or newer is recommended. The Linux and macOS launchers skip a `python` that is Python 2.
- **The "use 127.0.0.1" page** shown for a wrong address no longer uses styles that its own CSP blocks.

### Data and upgrade notes

- **New data format (schema 2).** It adds `schemaVersion`, `rev` and `savedAt`.
- **Upgrading v1.1 data.** v1.1 data and backups are upgraded automatically:
  - The original file is copied byte-for-byte to `data/backups/pre-1.1.1-migration-*.json`.
  - Any adjustment is listed under **SERVER NOTICES**: ignored unknown fields, an OT box ticked to match a stored OT winner, renamed duplicate preset ids, control characters replaced, or logos that fail the new image checks moved to `data/quarantine/` instead of being deleted.
  - Data that v1.1 itself would have refused is rejected as a whole, never partly imported.
- **The ZIP no longer contains `data/state.json`.** It is created from `starter-state.json` on first run, so extracting a new version over an old folder cannot overwrite your data.
- **Python.** Python 3.11 or newer is recommended. 3.9 and 3.10 still run, with a warning, because they no longer receive security updates.
- **Behaviour changes.**
  - A tie without an OT winner is no longer counted (see F-05).
  - The overtime winner of a tied game now gets one extra cap on stream and in the aggregate (a 2–2 game won in OT shows 3–2). The entered regulation scores are stored unchanged, so changing or clearing the winner never double-counts. Unequal scores never get an extra cap.
  - Choosing the same team for both rows is still allowed, but now shows a warning.

### Known limitations

- **Platform testing:** Automated regression and browser tests passed on Linux. A limited manual rehearsal also passed on Windows using OBS Studio 33.0.1, including Browser Source rendering, synchronization, overtime scoring, match totals, five/seven-game layouts, and logos. macOS compatibility and extended live-broadcast reliability remain unverified (see [SECURITY.md](SECURITY.md)).
- The overlay decides whether it is "OBS" from `window.obsstudio`. If OBS also provides that to custom browser docks, an overlay opened as a dock counts as an OBS source.
- Another website can open the dashboard in a new window. It cannot read or change anything, but the window counts as a second dashboard until closed.
- Any program on your computer can open many idle connections and slow CAPCAST down for about 15 seconds. This is the cost of a small local server with no accounts.
- A crafted team file within the size limits can still make the dashboard work hard for a few seconds while it is checked.

### Tests

New automated tests: `tests/test_server.py` (52), `tests/test_parity.py` (5), `tests/test_scoring.py` (4), `tests/test_dist.py` (6) and `tests/e2e/test_e2e.py` (24). They cover regression cases R-01 to R-18 from the v1.1 review, the findings of an independent review of this release, and normal scoreboard use.

## 1.1.0 (2026-11-11)

Initial community release.
