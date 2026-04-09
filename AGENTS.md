Dev guide:

- Avoid `getattr` at any cost because it makes the codebase extremely hard to maintain.
- Do not `from __future__ import annotations` because it is deprecated.
- It is okay to use busy waiting because it is easy to debug and this is a personal project without a need to scale up.
