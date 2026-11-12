# Contributing to CAPCAST

Thanks for your interest in improving CAPCAST!

CAPCAST is a free, open-source scoreboard and OBS overlay built for the TagPro broadcasting community. Contributions, bug reports, and thoughtful suggestions are welcome.

## Reporting Bugs

Before opening an issue, check whether someone has already reported the problem.

Use the repository's **Bug report** issue template and include:
- CAPCAST version
- Operating system and Python version
- OBS Studio and browser versions, if relevant
- Steps to reproduce the problem
- Expected and actual behavior
- Relevant error messages or screenshots

Please do not include credentials, personal information, or private backup files.

## Suggesting Features

Open a GitHub issue describing:
- What problem the feature would solve
- How broadcasters would use it
- Why it would benefit competitive TagPro broadcasts

For larger changes, please discuss your proposal in an issue before beginning implementation.

## Getting Started

1. Fork the repository and clone your fork.
2. Install Python 3.11 or newer and Node.js 22.
3. Open a terminal in the repository directory.
4. Start CAPCAST using `python server.py` (or `python3 server.py`).
5. Open `http://127.0.0.1:8765/` in your browser.

CAPCAST's application server uses Python's standard library and does not require additional runtime packages.

## Running Tests

Run these commands from the repository root:

**Server regression tests:**

    python -B -m unittest discover -s tests -p 'test_server.py' -v

**Dashboard/server validation and overtime scoring:**

    python -B -m unittest tests.test_parity tests.test_scoring -v

**Release packaging tests:**

    python -B -m unittest tests.test_dist -v

**Optional browser integration tests:**

Install Playwright and Chromium:

    python -m pip install playwright
    python -m playwright install chromium

Then run:

    python -B -m unittest tests.e2e.test_e2e -v

GitHub Actions also runs automated tests on pushes and pull requests. A pull request should pass the applicable checks before merging.

## Pull Requests

1. Create a descriptive branch in your fork.
2. Make a focused change.
3. Add or update tests when changing application behavior.
4. Run the relevant test suites.
5. Open a pull request against `main`.
6. Explain what changed, why it changed, and how you tested it.

Please avoid unrelated formatting or refactoring changes in the same pull request.

## Security and Privacy

CAPCAST is designed to run locally and protect user data.

Contributions must preserve its localhost-only design, request validation, safe file handling, and data integrity protections.

**Do not disclose suspected security vulnerabilities in public GitHub issues or pull requests.**

Use the repository's private vulnerability reporting feature, as described in [SECURITY.md](SECURITY.md).

Never commit real user data, private backups, credentials, or generated local state files.

## Licensing

By submitting a contribution, you agree that it may be distributed under CAPCAST's [MIT License](LICENSE).

---

**CAPCAST — Built for TagPro. Made for the broadcast.**
