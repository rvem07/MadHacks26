"""The one repo the agent may act on (spec section 7).

Rules enforced here, independent of the agent config (defense in depth):
  * The agent works on its own branch per task (`agent/<task-id>`).
  * Writes to a protected branch (main) are refused with ProtectedBranchError.
    The tool wrapper turns that into a `blocked` event.
  * The only way work reaches main is a pull request a human merges.

Two backends share one interface:
  * Repo (default): a simulated repo kept in the local store, so the demo
    needs no GitHub credentials and never touches a real repository.
  * GitHubRestRepo: the real GitHub REST API (AFD_GITHUB_MODE=rest).
    UNVERIFIED in this build: written against the documented API but not
    exercised end-to-end. Real GitHub branch protection on main is the
    backstop the spec asks for; configure it in the repo settings.
"""
from __future__ import annotations

import base64
from typing import Any

from . import audit
from .store import LocalStore


class ProtectedBranchError(PermissionError):
    pass


class Repo:
    """Simulated repo: branches are full file maps; PRs are records."""

    def __init__(self, store: LocalStore, protected: tuple[str, ...] = ("main",)):
        self.store = store
        self.protected = set(protected)

    # -- persistence ---------------------------------------------------------
    def _state(self) -> dict:
        rows = self.store.all("repo_state")
        return rows[0] if rows else {"branches": {}, "prs": []}

    def _save(self, state: dict) -> None:
        self.store.replace_all("repo_state", [state])

    def reset(self, files: dict[str, str]) -> None:
        self._save({"branches": {"main": {"files": dict(files), "base": None, "created_at": audit.now_iso()}},
                    "prs": []})

    # -- reads -----------------------------------------------------------------
    def branches(self) -> list[str]:
        return list(self._state()["branches"].keys())

    def list_files(self, branch: str = "main") -> list[str]:
        return sorted(self._branch(branch)["files"].keys())

    def read(self, path: str, branch: str = "main") -> str:
        files = self._branch(branch)["files"]
        if path not in files:
            raise FileNotFoundError(f"{path} not found on {branch}")
        return files[path]

    def prs(self) -> list[dict]:
        return self._state()["prs"]

    def diff(self, branch: str) -> list[dict]:
        """Files added/changed on `branch` relative to its base."""
        state = self._state()
        b = self._branch(branch, state)
        base = state["branches"].get(b["base"] or "main", {"files": {}})["files"]
        out = []
        for path, content in sorted(b["files"].items()):
            if base.get(path) != content:
                out.append({"path": path, "status": "added" if path not in base else "modified",
                            "lines": content.count("\n") + 1})
        return out

    # -- writes ----------------------------------------------------------------
    def create_branch(self, name: str, from_branch: str = "main") -> dict:
        state = self._state()
        if name in state["branches"]:
            return {"branch": name, "created": False}
        src = self._branch(from_branch, state)
        state["branches"][name] = {"files": dict(src["files"]), "base": from_branch, "created_at": audit.now_iso()}
        self._save(state)
        return {"branch": name, "created": True, "from": from_branch}

    def write(self, path: str, content: str, branch: str) -> dict:
        if branch in self.protected:
            raise ProtectedBranchError(f"refusing to write {path} directly to protected branch {branch}")
        state = self._state()
        b = self._branch(branch, state)
        existed = path in b["files"]
        b["files"][path] = content
        self._save(state)
        return {"path": path, "branch": branch, "status": "modified" if existed else "added",
                "bytes": len(content.encode())}

    def open_pr(self, branch: str, title: str, body: str, author: str) -> dict:
        if branch in self.protected:
            raise ProtectedBranchError("cannot open a PR from a protected branch")
        state = self._state()
        self._branch(branch, state)
        number = len(state["prs"]) + 1
        pr = {"number": number, "branch": branch, "base": "main", "title": title, "body": body,
              "author": author, "state": "open", "created_at": audit.now_iso(),
              "url": f"sim://lumen/ledger/pull/{number}", "files": self.diff(branch)}
        state["prs"].append(pr)
        self._save(state)
        return pr

    def _branch(self, name: str, state: dict | None = None) -> dict:
        state = state or self._state()
        if name not in state["branches"]:
            raise FileNotFoundError(f"branch {name} does not exist")
        return state["branches"][name]


class GitHubRestRepo:  # pragma: no cover - requires a real repo + token (UNVERIFIED)
    """Same interface as Repo, backed by the GitHub REST API."""

    def __init__(self, repo: str, token: str, protected: tuple[str, ...] = ("main",)):
        import httpx

        self.repo, self.protected = repo, set(protected)
        self.http = httpx.Client(base_url="https://api.github.com", timeout=20, headers={
            "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"})

    def _get(self, url: str) -> Any:
        r = self.http.get(url)
        r.raise_for_status()
        return r.json()

    def branches(self) -> list[str]:
        return [b["name"] for b in self._get(f"/repos/{self.repo}/branches")]

    def read(self, path: str, branch: str = "main") -> str:
        data = self._get(f"/repos/{self.repo}/contents/{path}?ref={branch}")
        return base64.b64decode(data["content"]).decode()

    def list_files(self, branch: str = "main") -> list[str]:
        tree = self._get(f"/repos/{self.repo}/git/trees/{branch}?recursive=1")
        return [t["path"] for t in tree["tree"] if t["type"] == "blob"]

    def create_branch(self, name: str, from_branch: str = "main") -> dict:
        sha = self._get(f"/repos/{self.repo}/git/ref/heads/{from_branch}")["object"]["sha"]
        r = self.http.post(f"/repos/{self.repo}/git/refs", json={"ref": f"refs/heads/{name}", "sha": sha})
        return {"branch": name, "created": r.status_code == 201, "from": from_branch}

    def write(self, path: str, content: str, branch: str) -> dict:
        if branch in self.protected:
            raise ProtectedBranchError(f"refusing to write {path} directly to protected branch {branch}")
        sha = None
        r = self.http.get(f"/repos/{self.repo}/contents/{path}?ref={branch}")
        if r.status_code == 200:
            sha = r.json()["sha"]
        body = {"message": f"agent: update {path}", "branch": branch,
                "content": base64.b64encode(content.encode()).decode()}
        if sha:
            body["sha"] = sha
        r = self.http.put(f"/repos/{self.repo}/contents/{path}", json=body)
        r.raise_for_status()
        return {"path": path, "branch": branch, "status": "modified" if sha else "added", "bytes": len(content)}

    def open_pr(self, branch: str, title: str, body: str, author: str) -> dict:
        r = self.http.post(f"/repos/{self.repo}/pulls", json={"title": title, "body": body, "head": branch, "base": "main"})
        r.raise_for_status()
        pr = r.json()
        return {"number": pr["number"], "url": pr["html_url"], "branch": branch, "title": title, "state": "open"}

    def diff(self, branch: str) -> list[dict]:
        data = self._get(f"/repos/{self.repo}/compare/main...{branch}")
        return [{"path": f["filename"], "status": f["status"], "lines": f.get("changes", 0)} for f in data["files"]]

    def prs(self) -> list[dict]:
        return self._get(f"/repos/{self.repo}/pulls?state=all")


def get_repo(store: LocalStore):
    """The configured repo backend."""
    from .config import get_settings

    s = get_settings()
    if s.github_mode == "rest" and s.github_repo and s.github_token:
        return GitHubRestRepo(s.github_repo, s.github_token)
    return Repo(store)
