# Agent Execution Guidelines

## Terminal & Execution Restrictions
- **Do NOT run long-running server processes**: Never execute commands like `streamlit run app.py`, `python -m http.server`, or any background servers.
- **Do NOT launch browsers or attempt GUI testing**: Web UI testing and manual validation will be performed directly by the user.
- **Allowed commands**: You may only inspect files, check syntax, run non-interactive unit tests (e.g., `pytest`), or check imports if explicitly requested.
- **Focus**: Limit your role to code generation, refactoring, and static code verification. Do not start the application yourself.