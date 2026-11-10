#!/usr/bin/env python3
"""Release gate, run from the repo root: python3 .github/scripts/release_check.py vX.Y.Z > notes.md

Checks that the tag equals the version in mods/<mod>/corgi.json and that CHANGELOG.md has a `## X.Y.Z`
section; prints that section (the release notes).
"""
import glob
import json
import os
import re
import sys


def notes(tag, root="."):
    m = re.fullmatch(r"v(\d+\.\d+\.\d+)", tag)
    if not m:
        sys.exit(f"tag {tag!r} is not vX.Y.Z")
    version = m.group(1)
    manifests = glob.glob(os.path.join(root, "mods", "*", "corgi.json"))
    if len(manifests) != 1:
        sys.exit(f"expected one mods/*/corgi.json, found {len(manifests)}")
    with open(manifests[0], encoding="utf-8") as f:
        have = json.load(f).get("version")
    if have != version:
        sys.exit(f"tag {tag} does not match corgi.json version {have}")
    try:
        with open(os.path.join(root, "CHANGELOG.md"), encoding="utf-8") as f:
            log = f.read()
    except FileNotFoundError:
        sys.exit("CHANGELOG.md is missing")
    s = re.search(rf"^## {re.escape(version)}(?:[ \t][^\n]*)?(?:\n|\Z)(.*?)(?=^## |\Z)", log, re.M | re.S)
    if not s:
        sys.exit(f"CHANGELOG.md has no '## {version}' section")
    return s.group(1).strip() + "\n"


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    sys.stdout.write(notes(sys.argv[1]))
