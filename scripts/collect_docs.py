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
REPO_DOCS_DIRNAME = "docs"
MKDOCS_CONFIG = Path("mkdocs.yml")
ASSETS_DATA_DIR = DOCS_DIR / "assets" / "data"
CACHE_FILE = ASSETS_DATA_DIR / "collect-cache.json"
GITHUB_API = "https://api.github.com"

TOKEN = os.environ.get("DOCS_READ_TOKEN", "")

HEADERS = {
    "Accept": "application/vnd.github+json",
}
if TOKEN:
    HEADERS["Authorization"] = f"Bearer {TOKEN}"

TEXT_EXTENSIONS = {".md"}
IMAGE_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp",
}
DOCS_ONLY_EXTENSIONS = {".json", ".yaml", ".yml"}
ALL_EXTENSIONS = TEXT_EXTENSIONS | IMAGE_EXTENSIONS | DOCS_ONLY_EXTENSIONS
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


def load_cache():
    if not CACHE_FILE.exists():
        return {"version": 1, "repos": {}}

    try:
        data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "repos": {}}

    if not isinstance(data, dict):
        return {"version": 1, "repos": {}}

    repos = data.get("repos")
    if not isinstance(repos, dict):
        repos = {}

    return {
        "version": 1,
        "repos": repos,
    }


def save_cache(cache):
    ASSETS_DATA_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def repo_cache_key(branch: str, repo: str) -> str:
    return f"{branch}/{repo}"


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


def list_repo_tree(owner: str, repo: str, branch: str):
    encoded_branch = quote(branch, safe="")
    url = f"{GITHUB_API}/repos/{owner}/{repo}/git/trees/{encoded_branch}?recursive=1"
    data = gh_get(url)
    return data.get("tree", [])


def remove_empty_parent_dirs(path: Path, stop_at: Path):
    current = path.parent
    stop_at = stop_at.resolve()

    while current.exists():
        try:
            current_resolved = current.resolve()
        except OSError:
            break

        if current_resolved == stop_at:
            break

        try:
            current.rmdir()
        except OSError:
            break

        current = current.parent


def prune_repo_target(repo_target_root: Path, previous_files: set[str], current_files: set[str]):
    stale_files = previous_files - current_files
    for rel_path in sorted(stale_files):
        target = repo_target_root / Path(rel_path)
        if target.exists():
            target.unlink()
            print(f"[REMOVED] {target}")
            remove_empty_parent_dirs(target, repo_target_root)


def prune_stale_repo_cache(cache, active_repo_keys: set[str]):
    repos_cache = cache.get("repos", {})
    stale_repo_keys = set(repos_cache) - active_repo_keys

    for cache_key in sorted(stale_repo_keys):
        branch, repo = cache_key.split("/", 1)
        repo_target_root = TARGET_DIR / branch / repo
        if repo_target_root.exists():
            shutil.rmtree(repo_target_root)
            print(f"[REMOVED] {repo_target_root}")
            remove_empty_parent_dirs(repo_target_root, TARGET_DIR)
        repos_cache.pop(cache_key, None)


def _display_name(path: Path) -> str:
    parts = path.stem.replace("_", " ").replace("-", " ").split()
    if not parts:
        return path.stem
    return " ".join(part if any(c.isupper() for c in part) else part.capitalize() for part in parts)


def _repo_docs_dir(repo_root: Path) -> Path:
    return repo_root / REPO_DOCS_DIRNAME


def _is_under_docs(path: Path) -> bool:
    return bool(path.parts) and path.parts[0] == REPO_DOCS_DIRNAME


def _should_collect_repo_file(path: Path) -> bool:
    suffix = path.suffix.lower()
    if suffix in TEXT_EXTENSIONS | IMAGE_EXTENSIONS:
        return True
    if suffix in DOCS_ONLY_EXTENSIONS and _is_under_docs(path):
        return True
    return False


def _nav_lines_for_dir(directory: Path, depth: int):
    lines = []
    if not directory.exists():
        return lines

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
            repo_dir = _repo_docs_dir(TARGET_DIR / branch / repo_name)
            if not repo_dir.exists():
                continue
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
                repo_dir = _repo_docs_dir(TARGET_DIR / branch / repo_name)
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
        "      slugify: !!python/name:markdown.extensions.toc.slugify_unicode",
        "  - tables",
        "  - fenced_code",
        "",
    ])

    MKDOCS_CONFIG.write_text("\n".join(lines), encoding="utf-8")
    print(f"[MKDOCS NAV UPDATED] {MKDOCS_CONFIG}")


def collect_repo_files(owner: str, repo: str, repo_target_root: Path, branch: str, previous_files):
    tree = list_repo_tree(owner, repo, branch)
    docs_md_found = False
    collected_count = 0
    downloaded_count = 0
    skipped_count = 0
    current_files = {}

    for item in tree:
        if item.get("type") != "blob":
            continue

        item_path = Path(item.get("path", ""))
        if not item_path or not _should_collect_repo_file(item_path):
            continue

        item_sha = item.get("sha", "")
        rel_path = item_path.as_posix()
        target = repo_target_root / item_path
        current_files[rel_path] = item_sha

        if _is_under_docs(item_path) and item_path.suffix.lower() in TEXT_EXTENSIONS:
            docs_md_found = True

        if target.exists() and previous_files.get(rel_path) == item_sha:
            skipped_count += 1
            collected_count += 1
            continue

        target.parent.mkdir(parents=True, exist_ok=True)
        download_url = (
            f"https://raw.githubusercontent.com/{owner}/{repo}/{quote(branch, safe='')}/"
            f"{quote(rel_path, safe='/')}"
        )

        if item_path.suffix.lower() in TEXT_EXTENSIONS:
            raw = raw_download(download_url).decode("utf-8", errors="replace")
            raw = normalize_markdown_paths(raw)
            target.write_text(raw, encoding="utf-8")
            print(f"[MD] {repo}/{item_path} -> {target}")
        else:
            save_binary(download_url, target)
            print(f"[ASSET] {repo}/{item_path} -> {target}")

        downloaded_count += 1
        collected_count += 1

    prune_repo_target(repo_target_root, set(previous_files), set(current_files))
    return {
        "docs_found": docs_md_found,
        "collected_count": collected_count,
        "downloaded_count": downloaded_count,
        "skipped_count": skipped_count,
        "files": current_files,
    }


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
        repo_dir = _repo_docs_dir(branch_root / repo_name)
        if not repo_dir.exists():
            lines.append("")
            continue

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
    TARGET_DIR.mkdir(parents=True, exist_ok=True)
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    cache = load_cache()
    repos_cache = cache["repos"]

    repos = list_org_repos(ORG)
    collected_by_branch = {branch: [] for branch in BRANCHES}
    active_repo_keys = set()

    for branch in BRANCHES:
        for repo in repos:
            repo_name = repo["name"]

            if repo.get("archived") or repo.get("disabled"):
                print(f"[SKIP] {repo_name} archived or disabled")
                continue

            print(f"[CHECK] {repo_name}@{branch}")
            repo_target = TARGET_DIR / branch / repo_name
            cache_key = repo_cache_key(branch, repo_name)
            active_repo_keys.add(cache_key)
            previous_files = repos_cache.get(cache_key, {}).get("files", {})

            try:
                result = collect_repo_files(ORG, repo_name, repo_target, branch, previous_files)
                print(
                    f"[COLLECTED] {repo_name}@{branch}: "
                    f"{result['collected_count']} files "
                    f"(downloaded={result['downloaded_count']}, skipped={result['skipped_count']})"
                )
                repos_cache[cache_key] = {"files": result["files"]}
                if result["docs_found"]:
                    collected_by_branch[branch].append(repo_name)
            except Exception as e:
                print(f"[ERROR] {repo_name}@{branch}: {e}")
                continue

    prune_stale_repo_cache(cache, active_repo_keys)
    save_cache(cache)
    build_collected_index(collected_by_branch)
    remove_category_indexes()
    build_doc_index(collected_by_branch)
    update_mkdocs_nav(collected_by_branch)

    print("")
    print("[DONE]")
    print(f"Collected repos: {collected_by_branch}")


if __name__ == "__main__":
    main()
