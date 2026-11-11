"""Git-backed persistence for MARCUS_V2_HOME in ephemeral cloud sessions.

Cloud containers are temporary, so the state folder (ledger, mandates, evidence,
audit, watchlist) is kept in a PRIVATE git repository and synced:
  pull() at the start of a run  -> clone or fast-forward only,
  push() after a state change   -> commit and push; never force.
If the remote moved on concurrently (non fast-forward), the push is refused and
reported: ledgers are never auto-merged. After every pull the ledger hash chain
is verified.

Configure with MARCUS_V2_STATE_REPO (git URL or local path) and optional
MARCUS_V2_STATE_BRANCH (default "state"). The repo must be private: it holds the
portfolio. Caches are not synced.
"""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .ledger import LedgerReader

IGNORE = "cache/\nllm_cache/\n*.tmp\n"


class StateSyncError(Exception):
    pass


@dataclass
class SyncResult:
    action: str
    detail: str
    commit: Optional[str] = None


def _git(home: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    p = subprocess.run(["git", "-C", str(home), *args], capture_output=True, text=True, timeout=120)
    if check and p.returncode != 0:
        raise StateSyncError(f"git {' '.join(args)} failed: {p.stderr.strip()[-400:]}")
    return p


def configured() -> Optional[tuple[str, str]]:
    repo = os.environ.get("MARCUS_V2_STATE_REPO")
    return (repo, os.environ.get("MARCUS_V2_STATE_BRANCH", "state")) if repo else None


def _remote_has_branch(repo: str, branch: str) -> bool:
    """True if the remote has `branch`, False if the remote is reachable but has no such branch (or is empty).

    Any other failure (no credentials, 403, network, unknown repo) raises: an unreachable state repo
    must never be mistaken for an empty one, or a run would start from blank state and report on it.
    """
    p = subprocess.run(["git", "ls-remote", "--exit-code", "--heads", repo, branch],
                       capture_output=True, text=True, timeout=120)
    if p.returncode == 0:
        return True
    if p.returncode == 2:                                    # reachable, no matching ref
        return False
    raise StateSyncError(f"cannot reach state repo (auth, network or access problem); refusing to start from "
                         f"blank state: {p.stderr.strip()[-300:]}")


def pull(home: Path, repo: str, branch: str = "state") -> SyncResult:
    home = Path(home)
    if not (home / ".git").exists():
        if home.exists() and any(home.iterdir()):
            raise StateSyncError(f"{home} has local state but is not a synced checkout; refusing to overwrite it")
        home.parent.mkdir(parents=True, exist_ok=True)
        if _remote_has_branch(repo, branch):
            p = subprocess.run(["git", "clone", "--quiet", "--branch", branch, repo, str(home)],
                               capture_output=True, text=True, timeout=300)
            if p.returncode != 0:
                raise StateSyncError(f"clone of state repo failed: {p.stderr.strip()[-300:]}")
            action = "cloned"
        else:
            # remote reachable but empty / branch missing: start a fresh state checkout on that branch
            home.mkdir(parents=True, exist_ok=True)
            _git(home, "init", "--quiet", "-b", branch)
            _git(home, "remote", "add", "origin", repo)
            (home / ".gitignore").write_text(IGNORE)
            action = "initialised"
    else:
        remote_url = _git(home, "remote", "get-url", "origin").stdout.strip()
        if _remote_has_branch(remote_url, branch):           # raises if the remote can't be reached
            _git(home, "fetch", "--quiet", "origin", branch)
            p = _git(home, "merge", "--ff-only", "--quiet", f"origin/{branch}", check=False)
            if p.returncode != 0:
                raise StateSyncError("local state diverged from the remote; resolve manually (ledgers are never auto-merged)")
        action = "fast-forwarded"
    _verify(home)
    return SyncResult(action, f"state at {home}", _head(home))


def push(home: Path, message: str, branch: str = "state") -> SyncResult:
    home = Path(home)
    if not (home / ".git").exists():
        raise StateSyncError("state folder is not a synced checkout; run pull first")
    _verify(home)
    _git(home, "add", "-A")
    if _git(home, "status", "--porcelain").stdout.strip() == "":
        return SyncResult("unchanged", "nothing to save", _head(home))
    _git(home, "-c", "user.name=Marcus V2", "-c", "user.email=marcus-v2@users.noreply.github.com",
         "commit", "--quiet", "-m", message)
    p = _git(home, "push", "--quiet", "origin", f"HEAD:{branch}", check=False)
    if p.returncode != 0:
        raise StateSyncError(f"push refused (remote moved on or no access): {p.stderr.strip()[-300:]}; nothing was overwritten")
    return SyncResult("pushed", message, _head(home))


def _head(home: Path) -> Optional[str]:
    p = _git(home, "rev-parse", "--short", "HEAD", check=False)
    return p.stdout.strip() or None


def _verify(home: Path) -> None:
    for led in (home / "ledger").glob("*.jsonl") if (home / "ledger").exists() else []:
        problems = LedgerReader(led).verify()
        if problems:
            raise StateSyncError(f"ledger {led.name} failed integrity check: {problems[:3]}")
