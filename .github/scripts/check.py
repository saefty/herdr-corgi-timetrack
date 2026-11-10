#!/usr/bin/env python3
"""Repo check for a herdr-corgi mod repo, run from the repo root: python3 .github/scripts/check.py

Runs the unit tests (node --test for test_*.mjs, python3 for test_*.py, track.py --selftest), a syntax
check of every mod .js, a generic leak scan of every tracked file, and fails if files written by
`herdr-app check` (gpui-kit.d.ts, jsconfig.json) are tracked. Stdlib only; kept identical in every repo.
"""
import os
import re
import subprocess
import sys

# Generic leak patterns only; the personal word list never lives in a public repo.
PATTERNS = [
    re.compile(r"/Users/[A-Za-z]"),
    re.compile(r"/home/[a-z]"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9-]{20,}")  # not inside words like "task-…",
]
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
SSH_REMOTE = re.compile(r"git@[A-Za-z0-9.-]+:")  # git@host:owner/repo is a remote, not an e-mail
GENERATED = {"gpui-kit.d.ts", "jsconfig.json"}


def leaks(text):
    """Every leak-like match in text, as strings."""
    hits = [m.group(0) for p in PATTERNS for m in p.finditer(text)]
    for m in EMAIL.finditer(text):
        mail = m.group(0)
        if SSH_REMOTE.match(text, m.start()) or mail.endswith("@users.noreply.github.com") or mail.startswith("noreply@"):
            continue
        hits.append(mail)
    return hits


def generated(files):
    return [f for f in files if os.path.basename(f) in GENERATED]


def tracked():
    out = subprocess.run(["git", "ls-files", "-z"], capture_output=True, text=True, check=True).stdout
    return [f for f in out.split("\0") if f]


def run(cmd, cwd="."):
    print("$", " ".join(cmd), f"(in {cwd})" if cwd != "." else "", flush=True)
    return subprocess.run(cmd, cwd=cwd).returncode == 0


def main():
    files = tracked()
    failed = []
    for f in generated(files):
        failed.append(f"generated file is tracked: {f}")
    for f in files:
        if not os.path.isfile(f):
            continue
        try:
            with open(f, encoding="utf-8") as fh:
                text = fh.read()
        except (UnicodeDecodeError, OSError):
            continue  # binary
        for n, line in enumerate(text.splitlines(), 1):
            for hit in leaks(line):
                failed.append(f"leak: {f}:{n}: {hit}")
    mods = [f for f in files if f.startswith("mods/")]
    for f in mods:
        name = os.path.basename(f)
        d, base = os.path.dirname(f) or ".", name
        if name.endswith(".js") and not run(["node", "--check", f]):
            failed.append(f"syntax: {f}")
        elif name.startswith("test_") and name.endswith(".mjs") and not run(["node", "--test", base], d):
            failed.append(f"test: {f}")
        elif name.startswith("test_") and name.endswith(".py") and not run([sys.executable, base], d):
            failed.append(f"test: {f}")
        elif name == "track.py" and not run([sys.executable, base, "--selftest"], d):
            failed.append(f"selftest: {f}")
    for line in failed:
        print("FAIL", line)
    print("check:", "FAILED" if failed else "ok")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
