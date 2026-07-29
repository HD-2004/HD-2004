#!/usr/bin/env python3
"""Update the repository section in the profile README."""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


START_MARKER = "<!-- AUTO-REPOSITORIES:START -->"
END_MARKER = "<!-- AUTO-REPOSITORIES:END -->"
API_VERSION = "2026-03-10"
VALID_MATCH_FIELDS = {"name", "description", "topics"}
VALID_SORT_FIELDS = {"name", "created_at", "updated_at", "pushed_at"}


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"File not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {path}: {exc}") from exc


def validate_config(config: Any) -> dict[str, Any]:
    if not isinstance(config, dict):
        raise ValueError("Configuration must be a JSON object.")

    username = config.get("github_username")
    if not isinstance(username, str) or not username.strip():
        raise ValueError("'github_username' must be a non-empty string.")

    match_fields = config.get("match_fields", ["name", "description", "topics"])
    if (
        not isinstance(match_fields, list)
        or not match_fields
        or any(field not in VALID_MATCH_FIELDS for field in match_fields)
    ):
        allowed = ", ".join(sorted(VALID_MATCH_FIELDS))
        raise ValueError(f"'match_fields' must only contain: {allowed}.")

    sort_by = config.get("sort_by", "name")
    if sort_by not in VALID_SORT_FIELDS:
        allowed = ", ".join(sorted(VALID_SORT_FIELDS))
        raise ValueError(f"'sort_by' must be one of: {allowed}.")

    categories = config.get("categories")
    if not isinstance(categories, list) or not categories:
        raise ValueError("'categories' must be a non-empty list.")

    seen_ids: set[str] = set()
    default_count = 0
    for category in categories:
        if not isinstance(category, dict):
            raise ValueError("Each category must be a JSON object.")

        category_id = category.get("id")
        title = category.get("title")
        keywords = category.get("keywords", [])

        if not isinstance(category_id, str) or not category_id.strip():
            raise ValueError("Each category needs a non-empty string 'id'.")
        if category_id in seen_ids:
            raise ValueError(f"Duplicate category id: {category_id}")
        seen_ids.add(category_id)

        if not isinstance(title, str) or not title.strip():
            raise ValueError(f"Category '{category_id}' needs a non-empty 'title'.")
        if not isinstance(keywords, list) or any(
            not isinstance(keyword, str) or not keyword.strip()
            for keyword in keywords
        ):
            raise ValueError(
                f"Category '{category_id}' has invalid 'keywords'; "
                "use a list of non-empty strings."
            )

        if category.get("default", False):
            default_count += 1

    if default_count != 1:
        raise ValueError("Exactly one category must have 'default': true.")

    exclude = config.get("exclude", {})
    if not isinstance(exclude, dict):
        raise ValueError("'exclude' must be a JSON object.")
    names = exclude.get("names", [])
    if not isinstance(names, list) or any(not isinstance(name, str) for name in names):
        raise ValueError("'exclude.names' must be a list of repository names.")
    for option in ("forks", "archived", "disabled"):
        if option in exclude and not isinstance(exclude[option], bool):
            raise ValueError(f"'exclude.{option}' must be true or false.")

    return config


def fetch_repositories(username: str, token: str | None = None) -> list[dict[str, Any]]:
    repositories: list[dict[str, Any]] = []
    page = 1

    while True:
        query = urllib.parse.urlencode(
            {
                "type": "owner",
                "sort": "full_name",
                "direction": "asc",
                "per_page": 100,
                "page": page,
            }
        )
        encoded_username = urllib.parse.quote(username, safe="")
        url = f"https://api.github.com/users/{encoded_username}/repos?{query}"
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": f"{username}-profile-repository-updater",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"

        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                page_items = json.load(response)
        except urllib.error.HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"GitHub API returned HTTP {exc.code}: {details}"
            ) from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Could not reach the GitHub API: {exc.reason}") from exc

        if not isinstance(page_items, list):
            raise RuntimeError("GitHub API returned an unexpected response.")

        repositories.extend(page_items)
        if len(page_items) < 100:
            break
        page += 1

    return repositories


def normalize(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        value = " ".join(str(item) for item in value)

    normalized = unicodedata.normalize("NFKD", str(value).casefold())
    without_accents = "".join(
        character
        for character in normalized
        if not unicodedata.combining(character)
    )
    return re.sub(r"[-_.\s]+", " ", without_accents).strip()


def searchable_text(repository: dict[str, Any], fields: list[str]) -> str:
    return " ".join(normalize(repository.get(field)) for field in fields)


def should_exclude(repository: dict[str, Any], exclude: dict[str, Any]) -> bool:
    excluded_names = {
        normalize(name)
        for name in exclude.get("names", [])
    }
    if normalize(repository.get("name")) in excluded_names:
        return True
    if exclude.get("forks", False) and repository.get("fork", False):
        return True
    if exclude.get("archived", False) and repository.get("archived", False):
        return True
    if exclude.get("disabled", False) and repository.get("disabled", False):
        return True
    return False


def classify_repositories(
    repositories: list[dict[str, Any]], config: dict[str, Any]
) -> dict[str, list[dict[str, Any]]]:
    categories = config["categories"]
    match_fields = config.get(
        "match_fields", ["name", "description", "topics"]
    )
    exclude = config.get("exclude", {})
    default_category = next(
        category for category in categories if category.get("default", False)
    )
    classified = {category["id"]: [] for category in categories}

    for repository in repositories:
        if should_exclude(repository, exclude):
            continue

        haystack = searchable_text(repository, match_fields)
        selected_category = default_category
        for category in categories:
            keywords = category.get("keywords", [])
            if any(normalize(keyword) in haystack for keyword in keywords):
                selected_category = category
                break

        classified[selected_category["id"]].append(repository)

    sort_by = config.get("sort_by", "name")
    reverse = sort_by != "name"
    for category_repositories in classified.values():
        category_repositories.sort(
            key=lambda repository: normalize(repository.get(sort_by)),
            reverse=reverse,
        )

    return classified


def format_repository(repository: dict[str, Any]) -> str:
    name = html.escape(str(repository.get("name", "Unnamed repository")), quote=False)
    url = str(repository.get("html_url", "")).strip()
    description = " ".join(str(repository.get("description") or "").split())
    escaped_description = html.escape(description, quote=False)

    item = f"- **[{name}]({url})**"
    if escaped_description:
        item += f" — {escaped_description}"
    return item


def render_section(
    classified: dict[str, list[dict[str, Any]]], config: dict[str, Any]
) -> str:
    lines = [
        START_MARKER,
        "<!-- Generated by scripts/update_repositories.py. "
        "Edit .github/repository-categories.json to change classification. -->",
        "",
    ]

    for index, category in enumerate(config["categories"]):
        if index:
            lines.append("")
        lines.append(f"### {category['title']}")
        lines.append("")

        repositories = classified[category["id"]]
        if repositories:
            lines.extend(format_repository(repository) for repository in repositories)
        else:
            lines.append("_No matching public repositories yet._")

    lines.extend(["", END_MARKER])
    return "\n".join(lines)


def update_readme(readme_path: Path, generated_section: str) -> bool:
    try:
        content = readme_path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ValueError(f"README not found: {readme_path}") from exc

    if content.count(START_MARKER) != 1 or content.count(END_MARKER) != 1:
        raise ValueError(
            f"{readme_path} must contain exactly one {START_MARKER} and "
            f"one {END_MARKER}."
        )

    before, marker, remainder = content.partition(START_MARKER)
    _, end_marker, after = remainder.partition(END_MARKER)
    if not marker or not end_marker:
        raise ValueError("Repository markers are missing or out of order.")

    updated = before + generated_section + after
    if updated == content:
        return False

    with readme_path.open("w", encoding="utf-8", newline="\n") as readme:
        readme.write(updated)
    return True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch, classify, and render public GitHub repositories."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(".github/repository-categories.json"),
        help="Path to the repository classification configuration.",
    )
    parser.add_argument(
        "--readme",
        type=Path,
        default=Path("README.md"),
        help="README file to update.",
    )
    parser.add_argument(
        "--repositories-json",
        type=Path,
        help="Use a local GitHub API response instead of making a network request.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Do not write; return a non-zero status if README would change.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        config = validate_config(load_json(args.config))
        if args.repositories_json:
            repositories = load_json(args.repositories_json)
            if not isinstance(repositories, list):
                raise ValueError("'--repositories-json' must contain a JSON list.")
        else:
            repositories = fetch_repositories(
                config["github_username"], os.environ.get("GITHUB_TOKEN")
            )

        classified = classify_repositories(repositories, config)
        generated_section = render_section(classified, config)

        if args.check:
            current = args.readme.read_text(encoding="utf-8")
            before, marker, remainder = current.partition(START_MARKER)
            _, end_marker, after = remainder.partition(END_MARKER)
            if not marker or not end_marker:
                raise ValueError("Repository markers are missing or out of order.")
            would_change = before + generated_section + after != current
            print("README is up to date." if not would_change else "README needs updating.")
            return 1 if would_change else 0

        changed = update_readme(args.readme, generated_section)
        counts = ", ".join(
            f"{category['id']}={len(classified[category['id']])}"
            for category in config["categories"]
        )
        print(f"Processed {len(repositories)} repositories ({counts}).")
        print("README updated." if changed else "README already up to date.")
        return 0
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
