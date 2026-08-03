# AGENTS.md

## Cursor Cloud specific instructions

This repository is a **GitHub profile README** repo (`20AAP02/20AAP02`). Its only tracked file is `README.md`, which GitHub renders on the owner's profile page.

- There is **no application, service, package manager, build step, lint config, or test suite**. There are no dependencies to install and nothing to compile or serve as a "product".
- The only meaningful validation is confirming `README.md` renders correctly as GitHub markdown. To preview it locally the way GitHub renders it, use `grip` (a dev-only preview tool, not a repo dependency), e.g. `grip README.md 0.0.0.0:6419` and open the served page in a browser. `grip` fetches from the GitHub API, so the first render needs outbound network access.
- The embedded `github-readme-stats` / `github-readme-streak-stats` images (vercel.app / herokuapp.com) are third-party services and may not load in this environment; that is expected and unrelated to the repo. The `shields.io` skill badges do load.
- Edits here are almost always simple Markdown changes to `README.md`.
