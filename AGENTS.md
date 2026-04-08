Dev guide:

- Avoid `getattr` at any cost because it makes the codebase extremely hard to maintain.
- Do not `from __future__ import annotations` because it is deprecated.
