# ZIP inbox operations guide

Upload a ZIP to `_inbox/` (from a phone if you like) and GitHub Actions validates
it, extracts it into the repository, and commits the result. No paid services,
tokens or servers are involved.

## Files

| Path | Purpose |
|---|---|
| `.github/workflows/process-zip.yml` | Workflow: trigger, permissions, commit and push |
| `.github/scripts/zip_inbox.py` | Validation and extraction logic (Python 3.12, standard library only) |
| `.github/scripts/tests/test_zip_inbox.py` | Unit tests (38 tests) |
| `.github/workflows/test-zip-inbox.yml` | Runs the tests when the processor changes |
| `_inbox/README.md`, `_failed/README.md` | Short instructions next to the folders |
| `docs/ZIP_INBOX_OPERATIONS.md` | This guide |

The processor and its tests live under `.github/` on purpose: archives are not
allowed to write there, so an uploaded ZIP can never change the rules that judge it.

## Processing sequence

1. A push touching `_inbox/*.zip` on `main` (or a manual run) starts the workflow.
2. The job checks out the repo and runs the processor for every `.zip` in `_inbox/`.
3. For each ZIP: name check, then size/entry checks, then every entry's path,
   type and policy, with nothing written yet.
4. Entries are streamed into a temporary staging folder outside the repo, with
   real byte counts enforced and CRCs verified. Staged files are scanned for
   secret-like content.
5. Only then are files copied into the repo, using a temp file and atomic
   replace per file. Any unexpected error rolls back every change from that ZIP.
6. A good ZIP is deleted from `_inbox/`. A bad one is moved to `_failed/` with a
   report. Nothing is extracted from a bad ZIP.
7. The job commits (`github-actions[bot]`) and pushes. If a newer commit
   appeared meanwhile it rebases and retries up to 3 times; it never force-pushes.
8. If any ZIP was rejected the run ends **red** after committing the report, so
   the result is obvious in the Actions list.

The run summary lists what was added, updated or rejected.

## Security rules enforced

- Rejected: `..`, `.` or empty path segments, absolute paths, Windows drive
  paths, backslashes, control characters, characters other than letters,
  digits, `.`, `_`, `-` (so no spaces), paths over 240 characters.
- Rejected: symbolic links, devices, pipes, sockets, encrypted entries,
  unsupported compression, corrupt or unreadable archives, CRC failures.
- Rejected: duplicate paths (also case/Unicode-insensitive duplicates) and
  file-versus-folder conflicts inside the archive.
- Allowlist only: top-level folders `assets config docs handovers prompts scripts
  src templates tests`; root files `README.md PROJECT_REQUIREMENTS.md DECISIONS.md
  CHANGELOG.md REVIEW.md SECURITY.md .gitignore`. `_inbox/` and `_failed/` are
  not writable by a ZIP.
- Never accepted at any depth: `.git*` (except `.gitignore`, `.gitkeep`),
  `.github`, `node_modules`, virtualenvs (`venv`, `.venv`, `env`), caches,
  `dist`, `build`, `.next`, `coverage`, and credential folders (`.ssh`, `.aws`,
  `.gnupg`, `.kube`, `.docker`).
- Secret-like names rejected: `.env*` (except `.env.example/.sample/.template`),
  `*.pem *.key *.p12 *.pfx *.jks *.keystore *.ppk *.kdbx`, `id_rsa`-style keys,
  names containing `secret` or `credential`, `.npmrc .pypirc .netrc .htpasswd`,
  service-account JSON, Terraform state/vars.
- Secret-like content rejected: private key blocks and common token formats
  (AWS, GitHub, Anthropic, OpenAI-style, Google API, Slack). Logs and reports show
  only the path and the kind, never the matched text. This is a safety net, not a
  guarantee; never put real secrets in a ZIP.
- Limits: ZIP 50 MB, 1,000 entries, 100 MB unzipped (declared sizes and actual bytes).
- Nothing from a ZIP is ever executed. File modes are ignored; files are written
  as 0644 (so executable bits are not preserved).
- The workflow has `contents: write` only, uses the built-in `GITHUB_TOKEN`
  (no personal access token), serialises runs with a `concurrency` group,
  times out after 15 minutes, and pins actions to commit SHAs.

## Trigger behaviour

Pushes made with `GITHUB_TOKEN` do not start other workflows, so the bot commit
does not re-trigger this one and nothing relies on recursion. Your own upload
commit does trigger it. Note the filter is `_inbox/*.zip`: a file named `.ZIP` in
capitals will not start a run; use the manual run instead.

## Changing the rules deliberately

Edit the constants at the top of `.github/scripts/zip_inbox.py` (`ALLOWED_DIRS`,
`ALLOWED_ROOT_FILES`, limits, secret patterns), update the tests, and commit
directly to the repo (not through a ZIP). The test workflow will run. Update
this guide and `_inbox/README.md` to match.

## Branch protection

If `main` requires pull requests or blocks bot pushes, the final push fails with
an explanatory error annotation. Do not disable protection. Options: allow the
`github-actions` bot to bypass, or change the workflow to push to a
`zip-inbox/<run-id>` branch and open a PR (this needs Settings > Actions >
General > "Allow GitHub Actions to create and approve pull requests"). This is
not implemented because the repository settings were not inspectable.

## Limitations

- Created without access to the repository; the allowlist is a starting point to
  adapt to the real layout.
- Processing is per ZIP, but the commit covers the whole run. If the job crashes
  unexpectedly nothing is committed and ZIPs stay in `_inbox/`; re-run it.
- Deletions are not supported; a ZIP can only add or replace files.
- Secret scanning is pattern-based and can produce false positives (for example
  documentation that quotes a key header). Reword or redact such text.
- Android: GitHub's mobile layout may hide the upload button. Use the browser's
  "Desktop site" option. The GitHub app cannot upload binary files.

## Phone workflow

1. Prepare the ZIP with repository-relative paths and a name like `update-docs.zip`.
2. In Chrome open `github.com/JennyJoshua/github-ai-workforce`.
3. If there is no Add file menu, open Chrome's menu and tick **Desktop site**.
4. Open `_inbox/` (create it with Add file > Create new file, typing
   `_inbox/README.md`, if it does not exist yet).
5. Choose Add file > Upload files, pick the ZIP, commit to `main`.
6. Open the **Actions** tab, then the newest **Process ZIP inbox** run.
7. Read the summary: added/updated files, or the rejection reasons.
8. Check the changed files in the repo, or the report in `_failed/`.
9. Fix a rejected ZIP and upload it again.

## Recovery and troubleshooting

| Symptom | Likely cause and fix |
|---|---|
| No run started | ZIP not in `_inbox/` directly, uploaded to another branch, or named `.ZIP`. Use Actions > Run workflow. |
| Run red, report in `_failed/` | Read the `.txt` report, fix the listed paths, resubmit. |
| "root-level file is not on the allowlist" | Move the file into `docs/` etc., or add it to the allowlist deliberately. |
| "top-level folder ... not on the allowlist" | ZIP has an outer folder. Zip the *contents*, not the folder. |
| "unsupported characters" | Rename files: letters, digits, `.`, `_`, `-` only. |
| "looks like a secret" | Rename or remove the file; never ship real secrets. |
| Push rejected / protected branch | See Branch protection. |
| Rebase conflict | Someone changed the same file at the same time. Re-run the workflow. |
| Wrong content was extracted | Revert the bot commit (`git revert`) or upload a corrected ZIP. |
| Workflow edits needed | Edit `.github/` files directly in the repo; ZIPs cannot change them. |

## Tests

`python -m unittest discover -s .github/scripts/tests -v` covers valid
extraction, overwrite, preserving other files, traversal, absolute paths,
duplicates, symlinks, special files, encryption flag, size/entry/uncompressed
limits, secrets (names and content, without leaking), forbidden folders,
allowlist, corrupt archives, no partial extraction, rollback and unique failure
reports. Live GitHub Actions behaviour (pushing, branch protection, mobile upload)
cannot be tested locally and must be verified with a first real upload.
