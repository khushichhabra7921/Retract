# Retract

Open-source Python SDK that records what agents derive from what. When a source must go, `retract(source)` finds every derived record across agents, tiers and stores, blocks them from reads, repairs them (delete / re-derive / quarantine) and issues a receipt with residue checks.

**Status:** pre-alpha MVP under construction (Nov–Dec 2026). See [docs/PLAN.md](docs/PLAN.md). Guarantees, API and results will be documented here as they are tested.

## Setup

Requires Python 3.11+ and git.

1. Install uv:

   ```bash
   pip install --user uv
   ```

2. Put uv on PATH. `pip --user` installs it to the user scripts directory, which is often not on PATH.

   - Windows (PowerShell), for Python 3.12:

     ```powershell
     $scripts = "$env:APPDATA\Python\Python312\Scripts"
     [Environment]::SetEnvironmentVariable("Path", [Environment]::GetEnvironmentVariable("Path", "User") + ";$scripts", "User")
     ```

     Open a new terminal afterwards. For another Python version, use the `Scripts` folder under `python -m site --user-base`.

   - Linux / macOS: add `$(python3 -m site --user-base)/bin` to `PATH`.

   Check with `uv --version`.

3. Install make. On Windows, run `choco install make`; on Linux and macOS it is usually preinstalled.

4. Install dependencies and run the tests:

   ```bash
   uv sync
   make test
   ```

Optional extras: `llm`, `embed`, `mem0`, `testbed`, `dashboard` (for example `uv sync --extra mem0`).

## Secrets

Retract reads keys only from the process environment. Nothing parses a `.env` file. See [.env.example](.env.example) for the variable names. Keys are never logged or written to receipts.

## License

[Apache-2.0](LICENSE)
