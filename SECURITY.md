# Security policy

## Supported versions

| Version | Supported |
|---|---|
| 1.1.1 | Yes |
| 1.1 | No. Please upgrade (see CHANGELOG.md for the issues fixed). |

## Reporting a vulnerability

Please report security problems **privately** through this repository's **Security** tab, using **Report a vulnerability**. The repository owner must turn on private vulnerability reporting in *Settings → Code security*. Do not open a public issue for security problems.

Please include the CAPCAST version, your operating system, your browser or OBS version, and steps to reproduce. The maintainer aims to reply within 7 days.

## Security model

CAPCAST is a single-user tool that runs on the streamer's own computer.

**Protected against:**

- Other devices on the network or the internet. The server listens on `127.0.0.1` only.
- Websites open in the user's browser. Several checks apply:
  - an exact `Host` check, which blocks DNS rebinding;
  - an `Origin` and JSON-only rule for every write;
  - a `Sec-Fetch-Site` gate, which allows cross-site requests only for plain links to the pages;
  - `Cross-Origin-Resource-Policy` and `Cross-Origin-Opener-Policy` headers, plus frame blocking;
  - a strict Content-Security-Policy with no inline script or style.
- Malformed or hostile data in team libraries, backups, logos or `data/state.json`:
  - a strict schema, with field paths in every error;
  - image header checks for type, dimensions and size;
  - logos re-encoded in the browser before they are stored;
  - escaping of every value shown on the page;
  - quarantine of bad state files.
- Lost or clobbered data:
  - version checks (revision and save time), so two dashboards cannot overwrite each other, and a dashboard cannot overwrite different data that CAPCAST was restarted with;
  - atomic, fsync'd writes;
  - automatic backups;
  - lossless migration from v1.1 data, with a byte-identical copy of the original.

**Not protected against:**

- Other software running on the same computer under the same account. It can read and write `data/` directly.
- A user who exposes or port-forwards the server.
- Vulnerabilities in the browser or OBS itself. OBS bundles its own Chromium, so keep OBS updated.

## Review status

v1.1.1 received an AI-assisted code review, a separate adversarial re-test and automated tests:

- `tests/test_server.py`, a standard-library suite that runs on Python 3.9–3.14;
- `tests/test_parity.py`, which checks the browser and server validators agree, using Node.js;
- `tests/test_dist.py`, which builds the release ZIP and checks its contents and launchers;
- `tests/e2e/test_e2e.py`, browser tests with Playwright and Chromium.

This review does not constitute a formal security audit, penetration test, or certification.

**Platform verification:** A limited manual rehearsal was successfully completed on Windows with OBS Studio 33.0.1. Browser Source rendering, synchronization, overtime scoring, automatic totals, five/seven-game layouts, and team logos were verified. Automated testing was also completed on Linux. macOS compatibility and extended live-broadcast reliability remain unverified.

## Known limitations

- **Sync status delay.** The dashboard checks the status once a second, so a change made outside it (another window, a restart, an edited file) can take up to about 2 seconds to show.
- **OBS detection.** The overlay counts as an OBS source when the page has `window.obsstudio`. If OBS also provides that to custom browser docks, an overlay opened as a dock counts as an OBS source. The overlay inside the dashboard's preview never counts.
- **Cross-site popups.** Another website can open the dashboard in a new window. `Cross-Origin-Opener-Policy` cuts it off from the opener, so it cannot read or change anything, but it counts as a second dashboard until it is closed.
- **Local slowdown.** Any program on the computer can hold CAPCAST's 48 connection slots with idle connections, delaying other requests for up to 15 seconds.
- **Heavy files.** A crafted team library or backup within the size limits can still keep the dashboard busy for a few seconds while it is checked.
- **Python 3.9.** A malformed HTTP/0.9 request holds one connection for 15 seconds before it is closed. Newer Python versions close it at once.
- **Not yet checked:** how OBS's and Chrome's image decoders treat unusual image files beyond the header checks, browser Local Network Access prompts, and durability after a power cut.
