"""Capture a review target without changing Git state; make numbered input batches."""

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

BATCH_CHARS = 18000  # A batching heuristic, not a model token limit.


def git(repo, *args, check=True):
    run = subprocess.run(
        ["git", "--literal-pathspecs", "-C", str(repo), *args],
        capture_output=True, env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
    )
    if check and run.returncode:
        raise ValueError(run.stderr.decode(errors="replace").strip())
    return run


def repo_root(path):
    return Path(git(path, "rev-parse", "--show-toplevel").stdout.decode().strip()).resolve()


def revision(repo, ref):
    return git(repo, "rev-parse", "--verify", "--end-of-options", ref + "^{commit}").stdout.decode().strip()


def sensitive(path):
    p = Path(path)
    return (p.name.startswith(".env") or p.name in {"id_rsa", "id_ed25519", "credentials.json"}
            or p.suffix.lower() in {".pem", ".p12", ".pfx", ".key"}
            or any(x.lower() in {".ssh", "secrets"} for x in p.parts))


def numbered_patch(text):
    """Keep both original and new line numbers even if a large hunk is split."""
    old = new = None
    rows = []
    for line in text.splitlines():
        match = re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", line)
        if match:
            old, new = map(int, match.groups())
            rows.append(line)
        elif old is not None and line.startswith(("+", "-", " ")):
            tag = line[0]
            rows.append(f"old:{old if tag != '+' else '-'} new:{new if tag != '-' else '-'} {line}")
            old += tag != "+"
            new += tag != "-"
        else:
            rows.append(line)
    return rows


def make_units(label, rows, limit=BATCH_CHARS):
    header = "FILE " + json.dumps(label, ensure_ascii=False) + "\n"
    chunks, current = [], []
    size = len(header)
    for row in rows:
        if current and size + len(row) + 1 > limit:
            chunks.append(current)
            current, size = [], len(header)
        current.append(row)
        size += len(row) + 1
    if current:
        chunks.append(current)
    return [{"label": f"{label} ({i + 1}/{len(chunks)})", "text": header + "\n".join(chunk),
             "paths": [label]}
            for i, chunk in enumerate(chunks)]


def pack_units(units, limit=BATCH_CHARS):
    """Pack nearby paths and unambiguous implementation/test pairs, without parsing code.

    Existing file chunks retain their headers and order. A single row longer than
    the budget remains intact, so that unit can still exceed the character limit.
    """
    files = {}
    for unit in units:
        files.setdefault(unit["paths"][0], []).append(unit)
    implementations, tests = {}, {}
    for path in files:
        file = Path(path)
        stem = file.stem.casefold()
        name = re.sub(r"^(?:test|spec)[_-]|[._-](?:test|spec)$", "", stem)
        is_test = name != stem or any(part.casefold() in {"test", "tests", "__tests__"}
                                     for part in file.parts[:-1])
        if is_test:
            tests[path] = name
        else:
            implementations.setdefault(name, []).append(path)
    anchors = {path: path for path in files}
    for path, name in tests.items():
        matches = implementations.get(name, [])
        if len(matches) == 1:
            anchors[path] = matches[0]
    groups = {}
    for path in sorted(files):
        groups.setdefault(anchors[path], []).append(path)

    batches, current, size = [], [], 0

    def flush():
        nonlocal current, size
        if current:
            batches.append({
                "label": " + ".join(unit["label"] for unit in current),
                "text": "\n\n".join(unit["text"] for unit in current),
                "paths": list(dict.fromkeys(path for unit in current for path in unit["paths"])),
            })
            current, size = [], 0

    for anchor in sorted(groups):
        paths = sorted(groups[anchor], key=lambda path: (path != anchor, path))
        group = [unit for path in paths for unit in files[path]]
        group_size = sum(len(unit["text"]) for unit in group) + 2 * (len(group) - 1)
        # Keep a matching implementation and its tests together when they fit.
        if current and group_size <= limit and size + 2 + group_size > limit:
            flush()
        for unit in group:
            if current and size + 2 + len(unit["text"]) > limit:
                flush()
            size += (2 if current else 0) + len(unit["text"])
            current.append(unit)
    flush()
    return batches


def capture_review(path, base=None, commit=None, paths=None):
    repo = repo_root(path)
    head_run = git(repo, "rev-parse", "--verify", "HEAD", check=False)
    head = head_run.stdout.decode().strip() if head_run.returncode == 0 else None
    pathspec = list(paths or [])
    if any(Path(p).is_absolute() or ".." in Path(p).parts for p in pathspec):
        raise ValueError("--path must be a repository-relative path without '..'")
    diff_options = ["--no-ext-diff", "--no-textconv", "--no-color", "--no-renames", "--unified=12"]
    diff_prefix = ["diff", *diff_options]
    if base:
        if not head:
            raise ValueError("Branch review needs a committed HEAD")
        base_sha = revision(repo, base)
        start = git(repo, "merge-base", base_sha, head).stdout.decode().strip()
        comparison = [start, head]
        scope = f"branch {base} merge-base {start} → HEAD {head}; working tree excluded"
        names = git(repo, "diff", "--name-only", "--no-renames", "-z", *comparison, "--", *pathspec).stdout
    elif commit:
        target = revision(repo, commit)
        parents = git(repo, "rev-list", "--parents", "-n", "1", target).stdout.decode().split()
        if len(parents) > 1:
            comparison = [parents[1], target]
            names = git(repo, "diff", "--name-only", "--no-renames", "-z", *comparison, "--", *pathspec).stdout
        else:
            comparison = [target]
            diff_prefix = ["show", "--format=", *diff_options]
            names = git(repo, "diff-tree", "--root", "--no-commit-id", "--name-only", "--no-renames", "-r", "-z", target, "--", *pathspec).stdout
        scope = f"commit {target} versus first parent (empty tree for root commit); working tree excluded"
    else:
        comparison = [head] if head else []
        names = (git(repo, "diff", "--name-only", "--no-renames", "-z", head, "--", *pathspec).stdout if head
                 else git(repo, "ls-files", "-z", "--", *pathspec).stdout)
        scope = f"working tree versus {head or 'empty tree'}; staged, unstaged and non-ignored untracked files"
    if pathspec:
        scope += "; selected paths: " + json.dumps(pathspec, ensure_ascii=False)
    files = [x.decode() for x in names.split(b"\0") if x]
    untracked = []
    if not base and not commit:
        untracked = [x.decode() for x in git(repo, "ls-files", "--others", "--exclude-standard", "-z", "--", *pathspec).stdout.split(b"\0") if x]
    files = sorted(set(files + untracked))
    units, omitted, captured = [], [], []
    for name in files:
        label = json.dumps(name, ensure_ascii=False)
        if sensitive(name):
            omitted.append(f"{label}: credential-like path excluded")
            continue
        file = repo / name
        if name in untracked or (not head and not base and not commit):
            if file.is_symlink() or not file.is_file():
                omitted.append(f"{label}: untracked symlink or non-regular file")
                continue
            data = file.read_bytes()
            try:
                text = data.decode("utf-8")
                if "\0" in text:
                    raise UnicodeError()
            except UnicodeError:
                omitted.append(f"{label}: binary/non-UTF-8 file")
                continue
            rows = [f"old:- new:{i} +{line}" for i, line in enumerate(text.splitlines(), 1)]
            if not rows:
                rows = ["New empty file."]
        else:
            data = git(repo, *diff_prefix, *comparison, "--", name).stdout
            try:
                text = data.decode("utf-8")
            except UnicodeError:
                omitted.append(f"{label}: non-UTF-8 patch")
                continue
            if "Binary files " in text or re.search(r"(?:mode |index .* )160000", text):
                omitted.append(f"{label}: binary or submodule change")
                continue
            rows = numbered_patch(text)
        captured.append({"path": name, "sha256": hashlib.sha256(data).hexdigest()})
        units.extend(make_units(name, rows))
    protected = [str(repo)]
    for flag in ("--absolute-git-dir", "--git-common-dir"):
        p = Path(git(repo, "rev-parse", flag).stdout.decode().strip())
        protected.append(str((repo / p).resolve() if not p.is_absolute() else p.resolve()))
    result = {"repo": str(repo), "kind": "review", "scope": scope, "units": pack_units(units),
              "omitted": omitted, "files": files, "captured": captured, "head": head,
              "protected_paths": sorted(set(protected))}
    result["snapshot_sha256"] = hashlib.sha256(json.dumps(captured, sort_keys=True).encode()).hexdigest()
    return result


def capture_audit(path, documents, kind):
    repo = Path(path).resolve(strict=True)
    units, captured, protected = [], [], [str(repo)]
    git_dir = git(repo, "rev-parse", "--absolute-git-dir", check=False)
    if git_dir.returncode == 0:
        for result in (git_dir, git(repo, "rev-parse", "--git-common-dir")):
            p = Path(result.stdout.decode().strip())
            protected.append(str((repo / p).resolve() if not p.is_absolute() else p.resolve()))
    for document in documents:
        file = Path(document).resolve(strict=True)
        if sensitive(file):
            raise ValueError(f"Credential-like document path excluded: {file}")
        data = file.read_bytes()
        text = data.decode("utf-8")
        if not text.strip() or "\0" in text:
            raise ValueError(f"Empty or binary document: {file}")
        units.extend(make_units(str(file), [f"line:{i} {line}" for i, line in enumerate(text.splitlines(), 1)]))
        captured.append({"path": str(file), "sha256": hashlib.sha256(data).hexdigest()})
        protected.append(str(file))
    return {"repo": str(repo), "kind": kind, "scope": f"{kind} audit: " + ", ".join(x["path"] for x in captured),
            "units": pack_units(units), "omitted": [], "captured": captured, "protected_paths": protected,
            "snapshot_sha256": hashlib.sha256(json.dumps(captured, sort_keys=True).encode()).hexdigest()}
