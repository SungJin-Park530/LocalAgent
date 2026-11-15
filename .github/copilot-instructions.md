# Agent Execution Guidelines

## Terminal & Execution Restrictions
- **Do NOT run long-running server processes**: Never execute commands like `streamlit run app.py`, `python -m http.server`, or any background servers.
- **Do NOT launch browsers or attempt GUI testing**: Web UI testing and manual validation will be performed directly by the user.
- **Allowed commands**: You may only inspect files, check syntax, run non-interactive unit tests (e.g., `pytest`), or check imports if explicitly requested.
- **Focus**: Limit your role to code generation, refactoring, and static code verification. Do not start the application yourself.

## Test Organization
- Keep only automated unit and regression tests in the repository-root `tests/` directory. When creating a test, add it under `tests/`; do not create test files in the repository root.
- The `practice/` directory stores concept-validation and manually run scripts; exclude it from automated test runs.
- Keep `tests/` runnable immediately without external services such as Ollama. Isolate all file and database I/O with `tmp_path` or `:memory:` so tests do not pollute the project root.
- Do not rerun the entire test suite in separate processes at every step. Run tests related to the changed module, or run one combined `pytest -q` at the end of the task.

## Prompt Example Templates
- Maintain a matching `.md.example` template for each system or functional prompt, including prompts under `prompts/tools/`, `prompts/workflow/`, and `prompts/context_summary.md`.
- Exception: under `prompts/persona/`, do not create a separate example for each personalized character persona such as `01_persona_*.md`. Keep only `00_persona_general.md.example` and the representative sample `01_persona_name.md.example`.