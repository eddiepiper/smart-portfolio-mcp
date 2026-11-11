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


def pull(home: Path, repo: str, branch: str = "state") -> SyncResult:
    home = Path(home)
    if not (home / ".git").exists():
        if home.exists() and any(home.iterdir()):
            raise StateSyncError(f"{home} has local state but is not a synced checkout; refusing to overwrite it")
        home.parent.mkdir(parents=True, exist_ok=True)
        p = subprocess.run(["git", "clone", "--quiet", "--branch", branch, repo, str(home)], capture_output=True, text=True, timeout=300)
        if p.returncode != 0:
            # empty remote or branch missing: start a fresh state checkout on that branch
            home.mkdir(parents=True, exist_ok=True)
            _git(home, "init", "--quiet", "-b", branch)
            _git(home, "remote", "add", "origin", repo)
            (home / ".gitignore").write_text(IGNORE)
            action = "initialised"
        else:
            action = "cloned"
    else:
        _git(home, "fetch", "--quiet", "origin", branch, check=False)
        has_remote = _git(home, "rev-parse", "--verify", "--quiet", f"origin/{branch}", check=False).returncode == 0
        if has_remote:
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
