# _inbox

Drop ZIP files here to have GitHub Actions extract them into this repository.

## How to use

1. Build a ZIP whose contents use **repository-relative paths**, for example:
   `docs/WORKFLOW.md`, `handovers/CLAUDE_HANDOVER.md`, `prompts/reviewer.md`.
2. Name it with **lowercase letters, numbers and hyphens only**, ending in `.zip`
   (for example `update-workforce-docs.zip`).
3. Upload it to this `_inbox/` folder on GitHub and commit to `main`.
4. Processing starts automatically. Open the **Actions** tab and look at the
   **Process ZIP inbox** run. Green = extracted. Red = rejected.

## Rules of thumb

- Do **not** wrap everything in an extra outer folder (no `my-project/docs/...`)
  unless that folder is meant to exist in the repository.
- Existing files with the same path are **overwritten**.
- Files that are not in the ZIP are **left unchanged**. Nothing is ever deleted.
- The whole ZIP is rejected if any single entry breaks a rule; nothing is
  partially extracted. Rejected ZIPs and a reason report go to `_failed/`.
- Allowed top-level folders: `assets`, `config`, `docs`, `handovers`, `prompts`,
  `scripts`, `src`, `templates`, `tests`. Allowed root files: `README.md`,
  `PROJECT_REQUIREMENTS.md`, `DECISIONS.md`, `CHANGELOG.md`, `REVIEW.md`,
  `SECURITY.md`, `.gitignore`.
- Never include `.github/`, `.git/`, `node_modules/`, `.env` files, keys or
  anything that looks like a secret. See `docs/ZIP_INBOX_OPERATIONS.md`.
- Limits: ZIP up to 50 MB, up to 1,000 entries, up to 100 MB unzipped.

Details and troubleshooting: `docs/ZIP_INBOX_OPERATIONS.md`.
