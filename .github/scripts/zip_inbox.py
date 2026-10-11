#!/usr/bin/env python3
"""Secure ZIP inbox processor for GitHub Actions.

Validates every ZIP in ``_inbox/`` and extracts accepted archives straight
into repository-relative paths. Rejected archives are moved to ``_failed/``
together with a plain-text failure report.

Design rules (see docs/ZIP_INBOX_OPERATIONS.md):
  * The whole archive is validated before anything is written to the repo.
  * Files are extracted into a temporary staging directory first, re-checked,
    and only then copied into the repository (with rollback on error).
  * Nothing from an archive is ever executed.
  * Secret values are never printed; only the path and the kind of match.

This file lives under .github/, which archives are not allowed to touch, so an
uploaded ZIP cannot change the rules that judge it. Standard library only.
"""
from __future__ import annotations

import argparse
import dataclasses
import filecmp
import hashlib
import os
import re
import shutil
import stat
import sys
import tempfile
import unicodedata
import zipfile
import zlib
from datetime import datetime, timezone
from pathlib import Path

# --------------------------------------------------------------------------
# Limits and allowlists. Change deliberately; see the operations guide.
# --------------------------------------------------------------------------
MAX_ZIP_BYTES = 50 * 1024 * 1024
MAX_ENTRIES = 1000
MAX_TOTAL_UNCOMPRESSED = 100 * 1024 * 1024
MAX_PATH_LEN = 240
MAX_COMPONENT_LEN = 100
MAX_REASONS = 50
MAX_ZIP_NAME_LEN = 80

ALLOWED_DIRS = frozenset(
    {"assets", "config", "docs", "handovers", "prompts", "scripts", "src", "templates", "tests"}
)
ALLOWED_ROOT_FILES = frozenset(
    {
        ".gitignore",
        "CHANGELOG.md",
        "DECISIONS.md",
        "PROJECT_REQUIREMENTS.md",
        "README.md",
        "REVIEW.md",
        "SECURITY.md",
    }
)

# Any path component (case-insensitive) that is never accepted.
FORBIDDEN_COMPONENTS = frozenset(
    {
        ".git",
        ".github",
        "node_modules",
        "venv",
        ".venv",
        "env",
        "virtualenv",
        "site-packages",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".tox",
        ".cache",
        ".next",
        ".nuxt",
        ".turbo",
        "dist",
        "build",
        "coverage",
        ".gradle",
    }
)
# .gitignore / .gitkeep are the only dot-git names allowed.
ALLOWED_GIT_NAMES = frozenset({".gitignore", ".gitkeep"})
# Directories that normally hold credentials.
CREDENTIAL_DIRS = frozenset({".ssh", ".aws", ".gnupg", ".kube", ".docker"})

ENV_TEMPLATE_NAMES = frozenset({".env.example", ".env.sample", ".env.template"})
SECRET_NAME_REGEXES = [
    (re.compile(r"^\.env($|\.)"), "environment file"),
    (re.compile(r"\.(pem|key|p12|pfx|pkcs12|jks|keystore|ppk|kdbx)$"), "key or keystore file"),
    (re.compile(r"^id_(rsa|dsa|ecdsa|ed25519)"), "SSH private key"),
    (re.compile(r"secret"), "file name contains 'secret'"),
    (re.compile(r"credential"), "file name contains 'credential'"),
    (re.compile(r"^\.(npmrc|pypirc|netrc|htpasswd|pgpass)$"), "credential configuration file"),
    (re.compile(r"^service[-_]?account.*\.json$"), "service account key"),
    (re.compile(r"\.(tfstate|tfvars)(\.|$)"), "Terraform state or variables"),
]

CONTENT_PATTERNS = [
    ("private key block", re.compile(rb"-----BEGIN [A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----")),
    ("AWS access key ID", re.compile(rb"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("GitHub token", re.compile(rb"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{22,})")),
    ("Anthropic API key", re.compile(rb"\bsk-ant-[A-Za-z0-9_-]{20,}")),
    ("OpenAI-style API key", re.compile(rb"\bsk-(?:proj-)?[A-Za-z0-9_-]{32,}")),
    ("Google API key", re.compile(rb"\bAIza[0-9A-Za-z_-]{35}")),
    ("Slack token", re.compile(rb"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
]

ALLOWED_COMPRESSION = (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
VALID_ZIP_NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\.zip")
_WIN_DRIVE = re.compile(r"^[A-Za-z]:")
_SAFE_PART = re.compile(r"^[A-Za-z0-9._-]+$")
IGNORED_INBOX_FILES = frozenset({"README.md", ".gitkeep"})


class ZipRejected(Exception):
    """The archive failed validation. ``reasons`` explains why."""

    def __init__(self, reasons):
        self.reasons = list(reasons)[:MAX_REASONS]
        super().__init__("; ".join(self.reasons))


@dataclasses.dataclass(frozen=True)
class Entry:
    info: zipfile.ZipInfo
    path: str  # normalised, repo-relative, forward slashes


@dataclasses.dataclass
class Applied:
    added: list
    updated: list
    unchanged: list


@dataclasses.dataclass
class Outcome:
    name: str
    status: str  # processed | rejected | ignored
    applied: Applied | None = None
    reasons: list | None = None
    failed_zip: str | None = None
    failed_report: str | None = None


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def printable(text, limit: int = 120) -> str:
    """ASCII-safe, single-line rendering of untrusted text for logs/reports."""
    out = []
    for ch in str(text):
        if 32 <= ord(ch) < 127:
            out.append(ch)
        else:
            out.append(ch.encode("unicode_escape").decode("ascii"))
    s = "".join(out)
    return s if len(s) <= limit else s[: limit - 3] + "..."


def _dedupe_key(path: str) -> str:
    return unicodedata.normalize("NFC", path).casefold()


def inspect_name(raw: str):
    """Return (normalised_path | None, is_dir, problems)."""
    if any(ord(c) < 32 or ord(c) == 127 for c in raw):
        return None, False, ["name contains control characters"]
    if "\\" in raw:
        return None, False, ["name contains a backslash"]
    if raw.startswith("/") or _WIN_DRIVE.match(raw):
        return None, False, ["absolute path"]
    is_dir = raw.endswith("/")
    body = raw[:-1] if is_dir else raw
    if body == "":
        return None, False, ["empty path"]
    parts = body.split("/")
    problems = []
    if ".." in parts:
        problems.append("path traversal ('..')")
    if "." in parts:
        problems.append("contains a '.' path segment")
    if "" in parts:
        problems.append("contains an empty path segment")
    if problems:
        return None, is_dir, problems
    if len(body) > MAX_PATH_LEN:
        problems.append(f"path longer than {MAX_PATH_LEN} characters")
    if any(len(p) > MAX_COMPONENT_LEN for p in parts):
        problems.append(f"a path component is longer than {MAX_COMPONENT_LEN} characters")
    if any(not _SAFE_PART.match(p) for p in parts):
        problems.append("unsupported characters (allowed: letters, digits, '.', '_', '-')")
    return "/".join(parts), is_dir, problems


def secret_name_problem(basename: str):
    low = basename.lower()
    if low in ENV_TEMPLATE_NAMES:
        return None
    for rx, label in SECRET_NAME_REGEXES:
        if rx.search(low):
            return label
    return None


def policy_problems(path: str, is_dir: bool):
    """Allowlist / forbidden-name / secret-name rules for a normalised path."""
    problems = []
    parts = path.split("/")
    if len(parts) == 1 and not is_dir:
        if path not in ALLOWED_ROOT_FILES:
            problems.append("root-level file is not on the allowlist")
    elif parts[0] not in ALLOWED_DIRS:
        problems.append(f"top-level folder '{parts[0]}' is not on the allowlist")
    for part in parts:
        low = part.lower()
        if low in FORBIDDEN_COMPONENTS or (low.startswith(".git") and low not in ALLOWED_GIT_NAMES):
            problems.append(f"forbidden folder or file name '{part}'")
            break
        if low in CREDENTIAL_DIRS:
            problems.append(f"credential folder '{part}' is not accepted")
            break
    if not is_dir:
        label = secret_name_problem(parts[-1])
        if label:
            problems.append(f"looks like a secret ({label})")
    return problems


def mode_problems(info: zipfile.ZipInfo, is_dir: bool):
    problems = []
    if info.flag_bits & 0x1:
        problems.append("encrypted entry")
    if info.compress_type not in ALLOWED_COMPRESSION:
        problems.append("unsupported compression method")
    if info.create_system == 3:  # created on Unix: high 16 bits hold st_mode
        mode = info.external_attr >> 16
        ftype = stat.S_IFMT(mode)
        if ftype == stat.S_IFLNK:
            problems.append("symbolic link")
        elif ftype not in (0, stat.S_IFREG, stat.S_IFDIR):
            problems.append("special file (device, pipe or socket)")
        elif ftype == stat.S_IFDIR and not is_dir:
            problems.append("directory mode on a non-directory entry")
    return problems


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------
def validate_archive(zip_path: Path):
    """Check the archive without extracting. Returns a list of file Entries."""
    zip_path = Path(zip_path)
    st = os.lstat(zip_path)
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        raise ZipRejected(["inbox item is not a regular file (symbolic link or special file)"])
    if st.st_size > MAX_ZIP_BYTES:
        raise ZipRejected([f"ZIP is larger than {MAX_ZIP_BYTES // (1024 * 1024)} MB"])
    try:
        zf = zipfile.ZipFile(zip_path)
    except (zipfile.BadZipFile, OSError, EOFError, ValueError, NotImplementedError):
        raise ZipRejected(["not a valid or readable ZIP archive"]) from None
    with zf:
        infos = zf.infolist()
        if len(infos) > MAX_ENTRIES:
            raise ZipRejected([f"archive has more than {MAX_ENTRIES} entries ({len(infos)})"])
        if not infos:
            raise ZipRejected(["archive is empty"])
        reasons = []
        files = {}  # dedupe key -> Entry
        dirs = set()
        total = 0
        for info in infos:
            shown = printable(info.filename)
            path, is_dir, problems = inspect_name(info.filename)
            if path is not None:
                problems += policy_problems(path, is_dir)
            problems += mode_problems(info, is_dir)
            total += info.file_size
            for p in problems:
                reasons.append(f"{shown}: {p}")
            if problems or path is None:
                continue
            key = _dedupe_key(path)
            if is_dir:
                dirs.add(key)
            elif key in files:
                reasons.append(f"{shown}: duplicate path in archive")
            else:
                files[key] = Entry(info, path)
        if total > MAX_TOTAL_UNCOMPRESSED:
            reasons.append(
                f"total uncompressed size exceeds {MAX_TOTAL_UNCOMPRESSED // (1024 * 1024)} MB"
            )
        # file/folder conflicts inside the archive
        for key, entry in files.items():
            if key in dirs:
                reasons.append(f"{printable(entry.path)}: used as both a file and a folder")
                continue
            parts = key.split("/")
            for i in range(1, len(parts)):
                if "/".join(parts[:i]) in files:
                    reasons.append(f"{printable(entry.path)}: parent path is also a file")
                    break
        if not files and not reasons:
            reasons.append("archive contains no files")
        if reasons:
            raise ZipRejected(reasons)
        return list(files.values())


# --------------------------------------------------------------------------
# Staging, content scan, apply
# --------------------------------------------------------------------------
def extract_to_staging(zip_path: Path, entries, staging: Path):
    """Stream entries into ``staging`` enforcing real (not declared) byte limits."""
    staging = Path(staging).resolve()
    total = 0
    try:
        with zipfile.ZipFile(zip_path) as zf:
            for entry in entries:
                dest = staging.joinpath(*entry.path.split("/"))
                if staging not in dest.resolve().parents:
                    raise ZipRejected([f"{printable(entry.path)}: escapes staging area"])
                dest.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(entry.info) as src, open(dest, "xb") as out:
                    while True:
                        chunk = src.read(64 * 1024)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > MAX_TOTAL_UNCOMPRESSED:
                            raise ZipRejected(
                                ["actual uncompressed size exceeds "
                                 f"{MAX_TOTAL_UNCOMPRESSED // (1024 * 1024)} MB"]
                            )
                        out.write(chunk)
    except ZipRejected:
        raise
    except (zipfile.BadZipFile, RuntimeError, EOFError, ValueError, NotImplementedError, zlib.error):
        raise ZipRejected(["corrupted or unreadable archive entry"]) from None


def scan_staged_content(staging: Path, entries):
    reasons = []
    for entry in entries:
        data = Path(staging).joinpath(*entry.path.split("/")).read_bytes()
        for label, rx in CONTENT_PATTERNS:
            if rx.search(data):
                reasons.append(f"{printable(entry.path)}: contains what looks like a {label}")
                break
    if reasons:
        raise ZipRejected(reasons)


def _precheck_targets(repo_root: Path, entries):
    reasons = []
    root = repo_root.resolve()
    for entry in entries:
        parts = entry.path.split("/")
        cur = repo_root
        bad = None
        for part in parts[:-1]:
            cur = cur / part
            if cur.is_symlink():
                bad = "existing parent folder is a symbolic link"
                break
            if cur.exists() and not cur.is_dir():
                bad = "existing parent path is a file"
                break
        target = repo_root.joinpath(*parts)
        if bad is None:
            if target.is_symlink():
                bad = "existing target is a symbolic link"
            elif target.is_dir():
                bad = "existing target is a folder"
            elif root not in target.resolve().parents:
                bad = "resolves outside the repository"
        if bad:
            reasons.append(f"{printable(entry.path)}: {bad}")
    if reasons:
        raise ZipRejected(reasons)


def apply_to_repo(staging: Path, entries, repo_root: Path) -> Applied:
    """Copy staged files into the repository; roll back everything on error."""
    repo_root = Path(repo_root)
    _precheck_targets(repo_root, entries)
    result = Applied([], [], [])
    backups = {}
    created_files = []
    created_dirs = []
    backup_dir = Path(tempfile.mkdtemp(prefix="zip-inbox-backup-"))
    try:
        for i, entry in enumerate(entries):
            src = Path(staging).joinpath(*entry.path.split("/"))
            target = repo_root.joinpath(*entry.path.split("/"))
            if target.exists():
                if filecmp.cmp(src, target, shallow=False):
                    result.unchanged.append(entry.path)
                    continue
                bkp = backup_dir / str(i)
                shutil.copy2(target, bkp)
                backups[target] = bkp
                result.updated.append(entry.path)
            else:
                missing = []
                p = target.parent
                while not p.exists():
                    missing.append(p)
                    p = p.parent
                target.parent.mkdir(parents=True, exist_ok=True)
                created_dirs.extend(reversed(missing))
                created_files.append(target)
                result.added.append(entry.path)
            tmp = target.with_name(f".zipinbox-{os.getpid()}-{i}.tmp")
            shutil.copyfile(src, tmp)
            os.chmod(tmp, 0o644)
            os.replace(tmp, target)
        return result
    except BaseException:
        for target, bkp in backups.items():
            shutil.copyfile(bkp, target)
        for f in created_files:
            if f.exists():
                f.unlink()
        for d in reversed(created_dirs):
            try:
                d.rmdir()
            except OSError:
                pass
        for leftover in repo_root.rglob(".zipinbox-*.tmp"):
            leftover.unlink()
        raise
    finally:
        shutil.rmtree(backup_dir, ignore_errors=True)


def process_zip(zip_path: Path, repo_root: Path) -> Applied:
    """Validate, stage, scan and apply one archive. Raises ZipRejected."""
    entries = validate_archive(zip_path)
    with tempfile.TemporaryDirectory(prefix="zip-inbox-staging-") as tmp:
        staging = Path(tmp)
        extract_to_staging(zip_path, entries, staging)
        scan_staged_content(staging, entries)
        return apply_to_repo(staging, entries, Path(repo_root))


# --------------------------------------------------------------------------
# Inbox handling
# --------------------------------------------------------------------------
def _sha256(path: Path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def quarantine(src: Path, failed_dir: Path, reasons, now: datetime):
    """Move a rejected item to _failed/ with a unique name and a report."""
    failed_dir.mkdir(parents=True, exist_ok=True)
    stem = src.name[:-4] if src.name.lower().endswith(".zip") else src.name
    base = re.sub(r"[^a-z0-9-]+", "-", stem.lower()).strip("-")[:60] or "archive"
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    n = 0
    while True:
        suffix = "" if n == 0 else f"-{n}"
        zip_dest = failed_dir / f"{stamp}-{base}{suffix}.zip"
        report = failed_dir / f"{stamp}-{base}{suffix}.txt"
        if not zip_dest.exists() and not report.exists() and not zip_dest.is_symlink():
            break
        n += 1
    st = os.lstat(src)
    digest = _sha256(src) if stat.S_ISREG(st.st_mode) else "n/a (not a regular file)"
    shutil.move(str(src), str(zip_dest))
    lines = [
        "ZIP REJECTED - nothing from this archive was extracted.",
        "",
        f"Original name : {printable(src.name)}",
        f"Rejected at   : {now.strftime('%Y-%m-%d %H:%M:%S')} UTC",
        f"Size (bytes)  : {st.st_size}",
        f"SHA-256       : {digest}",
        f"Stored as     : {zip_dest.name}",
        "",
        "Reasons:",
    ]
    lines += [f"  - {r}" for r in reasons[:MAX_REASONS]]
    if len(reasons) > MAX_REASONS:
        lines.append(f"  - ... and {len(reasons) - MAX_REASONS} more")
    lines += [
        "",
        "How to fix: correct the problems above, rebuild the ZIP with a lowercase",
        "hyphenated name, and upload it to _inbox/ again. See _failed/README.md.",
        "",
    ]
    report.write_text("\n".join(lines), encoding="utf-8")
    return zip_dest, report


def process_inbox(repo_root, inbox="_inbox", failed="_failed", now=None):
    repo_root = Path(repo_root)
    inbox_dir = repo_root / inbox
    failed_dir = repo_root / failed
    now = now or datetime.now(timezone.utc)
    outcomes = []
    if not inbox_dir.is_dir():
        return outcomes
    for item in sorted(inbox_dir.iterdir(), key=lambda p: p.name):
        name = item.name
        if name in IGNORED_INBOX_FILES:
            continue
        if not name.lower().endswith(".zip"):
            outcomes.append(Outcome(name, "ignored"))
            continue
        reasons = None
        applied = None
        if not VALID_ZIP_NAME.fullmatch(name) or len(name) > MAX_ZIP_NAME_LEN:
            reasons = [
                "ZIP file name must use only lowercase letters, numbers and hyphens "
                f"(max {MAX_ZIP_NAME_LEN} characters), e.g. update-docs.zip"
            ]
        else:
            try:
                applied = process_zip(item, repo_root)
            except ZipRejected as exc:
                reasons = exc.reasons
        if reasons is None:
            item.unlink()
            outcomes.append(Outcome(name, "processed", applied=applied))
        else:
            zdest, rep = quarantine(item, failed_dir, reasons, now)
            outcomes.append(
                Outcome(name, "rejected", reasons=reasons,
                        failed_zip=f"{failed}/{zdest.name}", failed_report=f"{failed}/{rep.name}")
            )
    return outcomes


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def _gh_escape(text: str) -> str:
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def render_summary(outcomes) -> str:
    if not outcomes:
        return "### ZIP inbox\n\nNo ZIP files were waiting in `_inbox/`.\n"
    out = ["### ZIP inbox results", ""]
    for o in outcomes:
        if o.status == "processed":
            a = o.applied
            out.append(
                f"- ✅ **{printable(o.name)}** processed: {len(a.added)} added, "
                f"{len(a.updated)} updated, {len(a.unchanged)} unchanged"
            )
            for label, items in (("added", a.added), ("updated", a.updated)):
                for p in items:
                    out.append(f"  - {label}: `{p}`")
        elif o.status == "rejected":
            out.append(f"- ❌ **{printable(o.name)}** rejected. Report: `{o.failed_report}`")
            for r in o.reasons[:10]:
                out.append(f"  - {r}")
        else:
            out.append(f"- ⚠️ `{printable(o.name)}` ignored (not a .zip file)")
    out.append("")
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo-root", default=".")
    ap.add_argument("--inbox", default="_inbox")
    ap.add_argument("--failed", default="_failed")
    ap.add_argument("--summary", help="write a Markdown summary to this path")
    args = ap.parse_args(argv)

    outcomes = process_inbox(args.repo_root, args.inbox, args.failed)
    processed = sum(o.status == "processed" for o in outcomes)
    rejected = sum(o.status == "rejected" for o in outcomes)

    if not outcomes:
        print("No ZIP files in the inbox. Nothing to do.")
    for o in outcomes:
        if o.status == "processed":
            a = o.applied
            print(f"PROCESSED {printable(o.name)}: {len(a.added)} added, "
                  f"{len(a.updated)} updated, {len(a.unchanged)} unchanged")
            for p in a.added:
                print(f"  + {p}")
            for p in a.updated:
                print(f"  ~ {p}")
        elif o.status == "rejected":
            print(f"::error title=ZIP rejected::{_gh_escape(printable(o.name))} was rejected; "
                  f"see {o.failed_report}")
            for r in o.reasons:
                print(f"  - {r}")
        else:
            print(f"::warning::Ignored non-ZIP file in inbox: {_gh_escape(printable(o.name))}")
    print(f"Done: {processed} processed, {rejected} rejected.")

    if args.summary:
        Path(args.summary).write_text(render_summary(outcomes), encoding="utf-8")
    gh_out = os.environ.get("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a", encoding="utf-8") as f:
            f.write(f"processed={processed}\nrejected={rejected}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
