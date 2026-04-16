import os
import time
import shutil
import json
import re
import requests
from pathlib import Path
from urllib.parse import quote

ORG = "OmniOneID"
BRANCHES = ("develop", "main")
DEFAULT_BRANCH = BRANCHES[0]
TARGET_DIR = Path("docs/collected")
DOCS_DIR = Path("docs")
MKDOCS_CONFIG = Path("mkdocs.yml")
ASSETS_DATA_DIR = DOCS_DIR / "assets" / "data"
GITHUB_API = "https://api.github.com"

TOKEN = os.environ.get("DOCS_READ_TOKEN", "")

HEADERS = {
    "Accept": "application/vnd.github+json",
}
if TOKEN:
    HEADERS["Authorization"] = f"Bearer {TOKEN}"

TEXT_EXTENSIONS = {".md"}
ASSET_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp",
    ".json", ".yaml", ".yml"
}
ALL_EXTENSIONS = TEXT_EXTENSIONS | ASSET_EXTENSIONS
MAX_RETRIES = 3
MARKDOWN_LOCAL_IMAGES_PREFIX_RE = re.compile(
    r"(?P<prefix>\]\(\s*<?|^[ \t]*\[[^\]]+\]:\s*<?)\./(?P<path>images/)",
    re.IGNORECASE | re.MULTILINE,
)
HTML_LOCAL_IMAGES_ATTR_RE = re.compile(
    r"(?P<prefix>\b(?:src|href)\s*=\s*[\"']?)(?:\./)?(?P<path>images/)",
    re.IGNORECASE,
)


class GitHubFetchError(RuntimeError):
    pass


def _rate_limit_wait_seconds(headers) -> int | None:
    remaining = headers.get("X-RateLimit-Remaining")
    reset = headers.get("X-RateLimit-Reset")
    if remaining == "0" and reset:
        return max(int(reset) - int(time.time()) + 3, 3)
    return None


def gh_get(url: str):
    for attempt in range(1, MAX_RETRIES + 1):
        resp = requests.get(url, headers=HEADERS, timeout=30)

        if resp.status_code == 200:
            return resp.json()

        if resp.status_code == 404:
            resp.raise_for_status()

        if resp.status_code == 403:
            wait_seconds = _rate_limit_wait_seconds(resp.headers)
            if wait_seconds is not None:
                print(f"[RATE LIMIT] waiting {wait_seconds}s...")
                time.sleep(wait_seconds)
                continue

            raise GitHubFetchError(
                f"GitHub API returned 403 for {url}. "
                "Check DOCS_READ_TOKEN permissions or org/repo visibility."
            )

        if resp.status_code in {429, 500, 502, 503, 504} and attempt < MAX_RETRIES:
            wait_seconds = attempt * 5
            print(f"[RETRY] {resp.status_code} from {url}, waiting {wait_seconds}s...")
            time.sleep(wait_seconds)
            continue

        resp.raise_for_status()

    raise GitHubFetchError(f"Exceeded retry limit while requesting {url}")


def raw_download(url: str) -> bytes:
    for attempt in range(1, MAX_RETRIES + 1):
        r = requests.get(url, headers=HEADERS, timeout=30)
        if r.status_code == 200:
            return r.content

        if r.status_code == 403:
            wait_seconds = _rate_limit_wait_seconds(r.headers)
            if wait_seconds is not None:
                print(f"[RATE LIMIT-DOWNLOAD] waiting {wait_seconds}s...")
                time.sleep(wait_seconds)
                continue

            raise GitHubFetchError(
                f"GitHub download returned 403 for {url}. "
                "Check DOCS_READ_TOKEN permissions or raw content access."
            )

        if r.status_code in {429, 500, 502, 503, 504} and attempt < MAX_RETRIES:
            wait_seconds = attempt * 5
            print(f"[RETRY-DOWNLOAD] {r.status_code} from {url}, waiting {wait_seconds}s...")
            time.sleep(wait_seconds)
            continue

        r.raise_for_status()

    raise GitHubFetchError(f"Exceeded retry limit while downloading {url}")


def save_binary(download_url: str, target_path: Path):
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_bytes(raw_download(download_url))


def normalize_markdown_paths(content: str) -> str:
    content = MARKDOWN_LOCAL_IMAGES_PREFIX_RE.sub(r"\g<prefix>\g<path>", content)
    return HTML_LOCAL_IMAGES_ATTR_RE.sub(r"\g<prefix>../\g<path>", content)


def list_org_repos(org: str):
    repos = []
    page = 1
    while True:
        url = f"{GITHUB_API}/orgs/{org}/repos?per_page=100&page={page}"
        data = gh_get(url)
        if not data:
            break
        repos.extend(data)
        page += 1
    return repos


def list_dir(owner: str, repo: str, path: str, branch: str):
    encoded_path = quote(path, safe="/")
    encoded_branch = quote(branch, safe="")
    url = f"{GITHUB_API}/repos/{owner}/{repo}/contents/{encoded_path}?ref={encoded_branch}"
    try:
        return gh_get(url)
    except requests.HTTPError as e:
        if e.response.status_code == 404:
            return None
        raise


def _display_name(path: Path) -> str:
    parts = path.stem.replace("_", " ").replace("-", " ").split()
    if not parts:
        return path.stem
    return " ".join(part if any(c.isupper() for c in part) else part.capitalize() for part in parts)


def _nav_lines_for_dir(directory: Path, depth: int):
    lines = []

    for child in sorted(directory.iterdir(), key=lambda item: (item.is_file(), item.name.lower())):
        if child.name.startswith("."):
            continue

        label = _display_name(child)
        indent = "  " * depth

        if child.is_dir():
            child_lines = _nav_lines_for_dir(child, depth + 1)
            if child_lines:
                lines.append(f"{indent}- {label}:")
                lines.extend(child_lines)
        elif child.suffix.lower() == ".md":
            rel = child.relative_to(DOCS_DIR).as_posix()
            lines.append(f"{indent}- {label}: {rel}")

    return lines


def remove_category_indexes():
    categories_dir = DOCS_DIR / "categories"
    categories_index = DOCS_DIR / "categories.md"

    if categories_dir.exists():
        shutil.rmtree(categories_dir)
        print(f"[REMOVED] {categories_dir}")

    if categories_index.exists():
        categories_index.unlink()
        print(f"[REMOVED] {categories_index}")


def build_doc_index(collected_by_branch):
    ASSETS_DATA_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "branches": list(BRANCHES),
        "defaultBranch": DEFAULT_BRANCH,
        "items": [],
    }

    for branch in BRANCHES:
        for repo_name in sorted(collected_by_branch.get(branch, [])):
            repo_dir = TARGET_DIR / branch / repo_name
            for md_file in sorted(repo_dir.rglob("*.md")):
                rel = md_file.relative_to(DOCS_DIR).as_posix()
                title_parts = md_file.relative_to(repo_dir).parts
                title = _display_name(Path("/".join(title_parts)))
                payload["items"].append({
                    "branch": branch,
                    "repo": repo_name,
                    "title": title,
                    "path": rel,
                })

    output = ASSETS_DATA_DIR / "doc-index.json"
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[DOC INDEX CREATED] {output}")


def update_mkdocs_nav(collected_by_branch):
    lines = [
        "site_name: Docs Portal",
        "site_description: Consolidated Markdown docs from OmniOneID repositories",
        "",
        "theme:",
        "  name: material",
        "  logo: images/logo.png",
        "  palette:",
        "    primary: orange",
        "    accent: deep orange",
        "  features:",
        "    - search.highlight",
        "    - search.suggest",
        "",
        "nav:",
        "  - Home: index.md",
        "  - Collected Docs: collected-index.md",
    ]

    if any(collected_by_branch.values()):
        lines.append("  - By Branch:")
        for branch in BRANCHES:
            collected_repos = collected_by_branch.get(branch, [])
            if not collected_repos:
                continue

            lines.append(f"      - {branch}:")
            lines.append(f"          - Overview: collected/{branch}/index.md")
            for repo_name in sorted(collected_repos):
                repo_dir = TARGET_DIR / branch / repo_name
                repo_nav = _nav_lines_for_dir(repo_dir, 7)
                if repo_nav:
                    lines.append(f"          - {repo_name}:")
                    lines.extend(repo_nav)

    lines.extend([
        "",
        "plugins:",
        "  - search",
        "",
        "extra_javascript:",
        "  - assets/javascripts/category-filter.js",
        "",
        "extra_css:",
        "  - assets/stylesheets/category-filter.css",
        "",
        "markdown_extensions:",
        "  - toc:",
        "      permalink: true",
        "  - tables",
        "  - fenced_code",
        "",
    ])

    MKDOCS_CONFIG.write_text("\n".join(lines), encoding="utf-8")
    print(f"[MKDOCS NAV UPDATED] {MKDOCS_CONFIG}")


def collect_docs_recursive(owner: str, repo: str, path: str, repo_target_root: Path, branch: str):
    items = list_dir(owner, repo, path, branch)
    if not items:
        return False

    found = False

    for item in items:
        item_type = item.get("type")
        item_path = item.get("path")
        item_name = item.get("name")
        suffix = Path(item_name).suffix.lower()

        if item_type == "dir":
            if collect_docs_recursive(owner, repo, item_path, repo_target_root, branch):
                found = True

        elif item_type == "file" and suffix in ALL_EXTENSIONS:
            rel_under_docs = Path(item_path).relative_to("docs")
            target = repo_target_root / rel_under_docs
            target.parent.mkdir(parents=True, exist_ok=True)

            if suffix in TEXT_EXTENSIONS:
                raw = raw_download(item["download_url"]).decode("utf-8", errors="replace")
                raw = normalize_markdown_paths(raw)
                target.write_text(raw, encoding="utf-8")
                print(f"[MD] {repo}/{item_path} -> {target}")
            else:
                save_binary(item["download_url"], target)
                print(f"[ASSET] {repo}/{item_path} -> {target}")

            found = True

    return found


def build_legacy_collected_index(collected_repos):
    lines = [
        "# Collected Documents",
        "",
        f"총 {len(collected_repos)}개 저장소에서 `docs/` 하위 문서를 수집했습니다.",
        "",
    ]

    for repo_name in sorted(collected_repos):
        lines.append(f"## {repo_name}")
        repo_dir = TARGET_DIR / repo_name

        for md_file in sorted(repo_dir.rglob("*.md")):
            rel = md_file.relative_to(DOCS_DIR)
            title = md_file.stem
            lines.append(f"- [{title}]({rel.as_posix()})")
        lines.append("")

    output_file = DOCS_DIR / "collected-index.md"
    output_file.write_text("\n".join(lines), encoding="utf-8")
    print(f"[INDEX CREATED] {output_file}")


def build_branch_index(branch: str, collected_repos):
    branch_root = TARGET_DIR / branch
    lines = [
        f"# {branch} Documents",
        "",
        f"Collected `docs/` content from {len(collected_repos)} repositories on `{branch}`.",
        "",
    ]

    for repo_name in sorted(collected_repos):
        lines.append(f"## {repo_name}")
        repo_dir = branch_root / repo_name

        for md_file in sorted(repo_dir.rglob("*.md")):
            rel = md_file.relative_to(branch_root)
            title = md_file.stem
            lines.append(f"- [{title}]({rel.as_posix()})")
        lines.append("")

    output_file = branch_root / "index.md"
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text("\n".join(lines), encoding="utf-8")
    print(f"[INDEX CREATED] {output_file}")


def build_collected_index(collected_by_branch):
    total = sum(len(repos) for repos in collected_by_branch.values())
    lines = [
        "# Collected Documents",
        "",
        f"Collected `docs/` content from {total} repository/branch combinations.",
        "",
    ]

    for branch in BRANCHES:
        collected_repos = collected_by_branch.get(branch, [])
        lines.append(f"## {branch}")
        lines.append("")
        if collected_repos:
            lines.append(f"- [Overview](collected/{branch}/index.md)")
            for repo_name in sorted(collected_repos):
                lines.append(f"- {repo_name}")
        else:
            lines.append("- No documents collected.")
        lines.append("")

        build_branch_index(branch, collected_repos)

    output_file = DOCS_DIR / "collected-index.md"
    output_file.write_text("\n".join(lines), encoding="utf-8")
    print(f"[INDEX CREATED] {output_file}")


def main():
    if TARGET_DIR.exists():
        shutil.rmtree(TARGET_DIR)
    TARGET_DIR.mkdir(parents=True, exist_ok=True)
    DOCS_DIR.mkdir(parents=True, exist_ok=True)

    repos = list_org_repos(ORG)
    collected_by_branch = {branch: [] for branch in BRANCHES}

    for branch in BRANCHES:
        for repo in repos:
            repo_name = repo["name"]

            if repo.get("archived") or repo.get("disabled"):
                print(f"[SKIP] {repo_name} archived or disabled")
                continue

            print(f"[CHECK] {repo_name}@{branch}/docs")
            repo_target = TARGET_DIR / branch / repo_name

            try:
                found = collect_docs_recursive(ORG, repo_name, "docs", repo_target, branch)
                if found:
                    collected_by_branch[branch].append(repo_name)
            except Exception as e:
                print(f"[ERROR] {repo_name}@{branch}: {e}")
                continue

    build_collected_index(collected_by_branch)
    remove_category_indexes()
    build_doc_index(collected_by_branch)
    update_mkdocs_nav(collected_by_branch)

    print("")
    print("[DONE]")
    print(f"Collected repos: {collected_by_branch}")


if __name__ == "__main__":
    main()
