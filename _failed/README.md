# _failed

ZIP files that were rejected by the inbox processor end up here, together with
a plain-text report that explains why.

## What you will find

For every rejected upload there are two files with matching names:

- `20261011T120000Z-update-docs.zip` - the rejected archive, unchanged
- `20261011T120000Z-update-docs.txt` - the failure report (original name, time,
  size, SHA-256 and a list of reasons)

The timestamp prefix keeps repeated failures from overwriting each other.
Reports never contain file contents or secret values, only paths and the kind
of problem found.

## How to fix and resubmit

1. Open the `.txt` report and read the reasons.
2. Fix the problems on your device (rename files, remove forbidden or
   secret-like files, correct paths, shrink the archive).
3. Build a new ZIP with a lowercase hyphenated name and upload it to `_inbox/`.
4. Check the Actions tab. When the fixed upload succeeds you may delete the old
   `.zip` and `.txt` from `_failed/` (nothing is deleted automatically).

Nothing from a rejected ZIP was extracted, so there is nothing to undo.
