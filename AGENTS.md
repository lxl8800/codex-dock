# Repository Guidelines

## Project Structure & Module Organization
`codex.py` is the thin root entry point. Core runtime code lives in `scripts/`: use `main.py` for CLI/install flow, `service.py` for account storage and switching logic, `web.py` for the local dashboard, and `mcp_stdio_proxy.py` for MCP stdio compatibility. Keep persistent project data in `config/accounts.json`. Root-level `.sh`, `.bat`, and `.ps1` files are launcher or installer wrappers. Primary docs live in `README.md` and `新手使用指南.md`.

## Build, Test, and Development Commands
This project has no build step and currently uses only the Python standard library.

- `python codex.py` — start the Web dashboard locally.
- `python codex.py --cli` — open the terminal menu instead of the dashboard.
- `python -m scripts --cli` — alternate CLI entry point for module-based runs.
- `./start-codex-dock.sh` or `start-codex-dock.bat` — platform launchers.
- `./install-codex-dock-command.sh` or `install-codex-dock-command.ps1` — install the global `codex-dock` shortcut.

## Coding Style & Naming Conventions
Target Python 3.10+ and keep the codebase stdlib-only unless a dependency is clearly justified. Use 4-space indentation, type hints on new or changed public functions, and `pathlib.Path` for filesystem work. Follow existing naming: modules and functions in `snake_case`, classes in `PascalCase`, constants in `UPPER_SNAKE_CASE`. Keep platform-specific shell behavior inside the launcher/install scripts rather than scattering OS checks across modules.

## Testing Guidelines
No automated `tests/` directory is committed right now. For every change, run a manual smoke test for both entry modes: `python codex.py` and `python codex.py --cli`. When adding automated coverage, place `unittest`-based files under `tests/` using `test_<module>.py` naming and avoid tests that touch a real `~/.codex` profile.

## Commit & Pull Request Guidelines
Recent history favors short, imperative English commit subjects such as `Add ...`, `Bump ...`, `Delete ...`, and `Document ...`. Keep the first line concise and scoped to one change. Pull requests should include: a short summary, affected OSes, manual verification steps, and dashboard screenshots when UI output changes.

## Security & Configuration Tips
Do not commit real account data, tokens, or local auth archives. Treat `config/accounts.json`, `~/.codex/auth.json`, and any `CODEX_HOME` overrides as sensitive. Mask emails or tokens in logs and screenshots, and prefer sample data when documenting behavior.
