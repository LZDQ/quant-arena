Dev guide:

- Avoid `getattr` at any cost because it makes the codebase extremely hard to maintain.
- Do not `from __future__ import annotations` because it is deprecated.
- It is okay to use busy waiting because it is easy to debug and this is a personal project without a need to scale up.
- Sometimes I manually edit the code and you should not revert them.
- NEVER write or run any tests.
- NEVER use type `Any`.
- To install or remove a python package, do not directly edit `pyproject.toml` or `uv.lock`. Instead, ask the user to run `uv add` or `uv remove`.
- When updating code that affects deployment setup, also modify `README.md`.
- Do not write migration logic unless the user asks you to do so.
