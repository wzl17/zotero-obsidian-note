#!/usr/bin/env python3
"""Create Obsidian-compatible Markdown notes from Zotero items."""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import subprocess
import sys
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any


DEFAULT_STABLE_TAGS = ["paper", "literature-note", "zotero"]
DEFAULT_VAULT_DIR = (
    "/Users/wzl17/Library/CloudStorage/OneDrive-Personal/Personal/Files/"
    "Obsidian/Notes/Research/Papers"
)
TOPIC_TAG_RULES = {
    "machine-learning": [
        "machine learning",
        "deep learning",
        "neural network",
        "representation learning",
    ],
    "nlp": [
        "natural language processing",
        "language model",
        "large language model",
        "transformer",
        "text generation",
        "tokenization",
        "sequence-to-sequence",
    ],
    "computer-vision": [
        "computer vision",
        "image classification",
        "object detection",
        "segmentation",
        "vision transformer",
    ],
    "hci": [
        "human-computer interaction",
        "human computer interaction",
        "user study",
        "usability",
        "interaction design",
    ],
    "biology": [
        "biology",
        "genomics",
        "protein",
        "cell",
        "molecular",
        "bioinformatics",
    ],
    "robotics": [
        "robot",
        "robotics",
        "motion planning",
        "manipulation",
        "autonomous system",
    ],
    "reinforcement-learning": [
        "reinforcement learning",
        "policy gradient",
        "markov decision process",
        "q-learning",
    ],
}


@dataclass
class Match:
    score: float
    row: dict[str, Any]


def dump_json(value: Any) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False))


def exit_with(message: str, *, payload: dict[str, Any] | None = None, code: int = 1) -> None:
    if payload is not None:
        payload = dict(payload)
        payload.setdefault("error", message)
        dump_json(payload)
    raise SystemExit(code)


def skill_root() -> Path:
    return Path(__file__).resolve().parent.parent


def discover_zotero_helper(explicit: str | None = None) -> Path:
    candidates: list[Path] = []
    if explicit:
        candidate = Path(explicit).expanduser().resolve()
        if candidate.is_dir():
            candidate = candidate / "skills/zotero/scripts/zotero.py"
        candidates.append(candidate)

    codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    candidates.extend(
        sorted(
            codex_home.glob("plugins/cache/openai-curated/zotero/*/skills/zotero/scripts/zotero.py")
        )
    )

    for candidate in candidates:
        if candidate.exists():
            return candidate

    raise SystemExit(
        "Could not locate the Zotero helper. Pass --plugin-root or set CODEX_HOME so "
        "the skill can find <plugin-root>/skills/zotero/scripts/zotero.py."
    )


def run_helper(helper: Path, *args: str) -> Any:
    command = ["python3", str(helper), *args]
    try:
        completed = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.strip() or exc.stdout.strip() or str(exc)
        raise SystemExit(f"Zotero helper failed for {' '.join(args)}: {detail}") from exc

    output = completed.stdout.strip()
    if not output:
        return None
    try:
        return json.loads(output)
    except json.JSONDecodeError:
        return output


def request_json(base_url: str, path: str) -> Any:
    url = base_url.rstrip("/") + path
    request = urllib.request.Request(url, headers={"Zotero-API-Version": "3"})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"Zotero API request failed: {url} status={exc.code} detail={body[:200]}")
    except OSError as exc:
        raise SystemExit(f"Could not reach Zotero local API at {url}: {exc}") from exc


def normalize_space(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def slugify(text: str, *, allow_slash: bool = False) -> str:
    normalized = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    normalized = normalized.lower()
    pattern = r"[^a-z0-9/-]+" if allow_slash else r"[^a-z0-9-]+"
    normalized = re.sub(r"[\s_]+", "-", normalized)
    normalized = re.sub(pattern, "-", normalized)
    normalized = re.sub(r"-{2,}", "-", normalized).strip("-/")
    return normalized or "untitled"


def first_author_last_name(authors: list[str]) -> str:
    if not authors:
        return "unknown"
    first = authors[0].strip()
    if "," in first:
        last = first.split(",", 1)[0]
    else:
        last = first.split()[-1]
    return slugify(last)


def score_match(query: str, row: dict[str, Any]) -> float:
    query_norm = normalize_space(query).lower()
    title = normalize_space(row.get("title")).lower()
    citekey = normalize_space(row.get("bibtexKey")).lower()
    creators = " ".join(row.get("creators") or []).lower()

    score = 0.0
    if title:
        score = max(score, difflib.SequenceMatcher(None, query_norm, title).ratio())
        if query_norm == title:
            score = max(score, 1.0)
        elif query_norm and query_norm in title:
            score = max(score, 0.97)
    if citekey and query_norm == citekey:
        score = max(score, 0.995)
    if creators and query_norm in creators:
        score = max(score, 0.75)
    return score


def search_matches(helper: Path, query: str) -> list[Match]:
    rows = run_helper(helper, "search", query, "--with-bibtex-keys", "--json") or []
    matches = [Match(score=score_match(query, row), row=row) for row in rows]
    matches.sort(key=lambda match: (-match.score, match.row.get("year") or "", match.row.get("title") or ""))
    return matches


def dominant_match(matches: list[Match]) -> Match | None:
    if not matches:
        return None
    top = matches[0]
    second = matches[1] if len(matches) > 1 else None
    if top.score >= 0.985:
        return top
    if top.score >= 0.92 and (second is None or top.score - second.score >= 0.08):
        return top
    return None


def extract_bibtex_key(helper: Path, item_key: str) -> str | None:
    exported = run_helper(helper, "export-bibtex", "--item-key", item_key)
    if not isinstance(exported, str):
        return None
    match = re.search(r"@\w+\s*\{\s*([^,\s]+)", exported)
    return match.group(1) if match else None


def load_item_detail(base_url: str, item_key: str) -> dict[str, Any]:
    item = request_json(base_url, f"/api/users/0/items/{urllib.parse.quote(item_key)}")
    data = item.get("data", item)
    collections = []
    for collection_key in data.get("collections", []) or []:
        collection = request_json(
            base_url, f"/api/users/0/collections/{urllib.parse.quote(collection_key)}"
        )
        collection_data = collection.get("data", collection)
        if collection_data.get("name"):
            collections.append(collection_data["name"])
    data["collectionNames"] = collections
    return data


def creators_from_item(data: dict[str, Any]) -> list[str]:
    authors: list[str] = []
    for creator in data.get("creators", []) or []:
        name = creator.get("name")
        if not name:
            first = creator.get("firstName", "").strip()
            last = creator.get("lastName", "").strip()
            name = " ".join(part for part in [first, last] if part)
        if name:
            authors.append(name)
    return authors


def year_from_item(data: dict[str, Any]) -> int | None:
    raw = data.get("date") or ""
    match = re.search(r"(\d{4})", raw)
    return int(match.group(1)) if match else None


def publication_from_item(data: dict[str, Any]) -> str | None:
    for field in ("publicationTitle", "proceedingsTitle", "bookTitle", "websiteTitle", "forumTitle"):
        value = normalize_space(data.get(field))
        if value:
            return value
    return None


def zotero_tags_from_item(data: dict[str, Any]) -> list[str]:
    tags = []
    for tag in data.get("tags", []) or []:
        value = normalize_space(tag.get("tag"))
        if value:
            tags.append(value)
    return tags


def attachment_urls(helper: Path, item_key: str) -> list[str]:
    children = run_helper(helper, "children", item_key, "--json") or []
    urls: list[str] = []
    for child in children:
        if child.get("itemType") != "attachment" or not child.get("key"):
            continue
        try:
            value = run_helper(helper, "file-url", child["key"])
        except SystemExit:
            continue
        if isinstance(value, str) and value:
            urls.append(value.strip())
    return urls


def topic_tags(title: str, abstract: str) -> list[str]:
    haystack = f"{title} {abstract}".lower()
    found: list[str] = []
    for tag, keywords in TOPIC_TAG_RULES.items():
        hits = sum(1 for keyword in keywords if keyword in haystack)
        if hits >= 1 and (tag in {"nlp", "machine-learning", "biology"} or hits >= 2):
            found.append(tag)
    return found


def normalized_tags(data: dict[str, Any]) -> list[str]:
    tags: list[str] = list(DEFAULT_STABLE_TAGS)
    item_type = normalize_space(data.get("itemType"))
    publication = publication_from_item(data)
    year = year_from_item(data)
    title = normalize_space(data.get("title"))
    abstract = normalize_space(data.get("abstractNote"))

    if item_type:
        tags.append(slugify(item_type))
    if year:
        tags.append(f"year-{year}")
    if publication:
        venue_slug = slugify(publication)
        if venue_slug and len(venue_slug) <= 40:
            tags.append(f"venue-{venue_slug}")
    tags.extend(topic_tags(title, abstract))
    tags.extend(slugify(tag, allow_slash=True) for tag in zotero_tags_from_item(data))

    seen: set[str] = set()
    ordered: list[str] = []
    for tag in tags:
        tag = tag.strip("-/")
        if not tag or tag in seen:
            continue
        seen.add(tag)
        ordered.append(tag)
    return ordered


def yaml_quote(text: str) -> str:
    return json.dumps(text, ensure_ascii=False)


def yaml_scalar(value: Any) -> str:
    if value is None:
        return '""'
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    return yaml_quote(str(value))


def yaml_list(lines: list[str], key: str, values: list[Any]) -> None:
    if not values:
        return
    lines.append(f"{key}:")
    for value in values:
        lines.append(f"  - {yaml_scalar(value)}")


def build_frontmatter(data: dict[str, Any], *, created_on: str) -> str:
    lines = ["---"]
    authors = data.get("authors") or []
    lines.append(f"title: {yaml_scalar(normalize_space(data.get('title')) or 'Untitled')}")
    yaml_list(lines, "authors", authors)

    ordered_fields: list[tuple[str, Any]] = [
        ("year", data.get("year")),
        ("item_type", normalize_space(data.get("itemType")) or None),
        ("publication", data.get("publication")),
        ("doi", data.get("doi")),
    ]

    for key, value in ordered_fields:
        if value is not None and value != "":
            lines.append(f"{key}: {yaml_scalar(value)}")

    primary_url = normalize_space(data.get("url")) or None
    if primary_url:
        lines.append(f"url: {yaml_scalar(primary_url)}")

    for key in ("zotero_key", "citekey"):
        value = data.get(key)
        if value:
            lines.append(f"{key}: {yaml_scalar(value)}")

    attachment_urls_value = data.get("attachment_urls") or []
    yaml_list(lines, "attachment_urls", attachment_urls_value)

    tags = data.get("tags") or []
    yaml_list(lines, "tags", tags)

    collections = data.get("collections") or []
    yaml_list(lines, "collections", collections)

    zotero_tags = data.get("zotero_tags") or []
    yaml_list(lines, "zotero_tags", zotero_tags)

    lines.append(f"created: {yaml_scalar(created_on)}")
    lines.append("---")
    return "\n".join(lines)


def build_note_content(data: dict[str, Any], *, created_on: str) -> str:
    title = normalize_space(data.get("title")) or "Untitled"
    abstract = normalize_space(data.get("abstract")) or "Abstract unavailable."
    frontmatter = build_frontmatter(data, created_on=created_on)
    return (
        f"{frontmatter}\n\n"
        f"# {title}\n\n"
        f"## Abstract\n\n"
        f"{abstract}\n\n"
        f"## Notes\n"
    )


def filename_for_note(data: dict[str, Any]) -> str:
    citekey = normalize_space(data.get("citekey"))
    if citekey:
        return f"{slugify(citekey)}.md"

    author = first_author_last_name(data.get("authors") or [])
    year = data.get("year") or "unknown"
    title_slug = slugify(normalize_space(data.get("title")) or "untitled")
    short_title = "-".join(title_slug.split("-")[:8])
    return f"{author}-{year}-{short_title}.md"


def choose_output_path(vault: Path, filename: str, mode: str) -> tuple[Path, str]:
    vault = vault.expanduser().resolve()
    vault.mkdir(parents=True, exist_ok=True)
    path = vault / filename
    if not path.exists():
        return path, "create"
    if mode == "overwrite":
        return path, "overwrite"
    if mode == "suffix":
        stem = path.stem
        suffix = path.suffix
        for index in range(2, 1000):
            candidate = vault / f"{stem}-{index}{suffix}"
            if not candidate.exists():
                return candidate, "suffix"
        raise SystemExit(f"Could not find a free suffixed filename for {path}")
    raise SystemExit(
        f"Note already exists: {path}. Re-run with --if-exists overwrite or --if-exists suffix "
        "after confirming with the user."
    )


def note_payload(helper: Path, base_url: str, *, item_key: str, fallback_citekey: str | None) -> dict[str, Any]:
    data = load_item_detail(base_url, item_key)
    authors = creators_from_item(data)
    zotero_tags = zotero_tags_from_item(data)
    attachments = attachment_urls(helper, item_key)
    citekey = fallback_citekey or extract_bibtex_key(helper, item_key)

    payload = {
        "title": normalize_space(data.get("title")),
        "authors": authors,
        "year": year_from_item(data),
        "itemType": data.get("itemType"),
        "publication": publication_from_item(data),
        "doi": normalize_space(data.get("DOI")) or None,
        "url": normalize_space(data.get("url")) or None,
        "zotero_key": item_key,
        "citekey": citekey,
        "collections": data.get("collectionNames") or [],
        "zotero_tags": zotero_tags,
        "abstract": normalize_space(data.get("abstractNote")) or "",
        "attachment_urls": attachments,
    }
    payload["tags"] = normalized_tags(
        {
            **data,
            "title": payload["title"],
            "abstractNote": payload["abstract"],
            "collectionNames": payload["collections"],
        }
    )
    return payload


def cmd_status(args: argparse.Namespace) -> None:
    helper = discover_zotero_helper(args.plugin_root)
    payload = run_helper(helper, "status", "--json")
    dump_json({"helper": str(helper), "status": payload})


def cmd_search(args: argparse.Namespace) -> None:
    helper = discover_zotero_helper(args.plugin_root)
    matches = search_matches(helper, args.query)
    dominant = dominant_match(matches)
    payload = {
        "helper": str(helper),
        "query": args.query,
        "match_count": len(matches),
        "dominant_match": dominant.row.get("key") if dominant else None,
        "matches": [
            {
                "score": round(match.score, 3),
                "item_key": match.row.get("key"),
                "citekey": match.row.get("bibtexKey"),
                "title": match.row.get("title"),
                "authors": match.row.get("creators") or [],
                "year": match.row.get("year"),
                "item_type": match.row.get("itemType"),
            }
            for match in matches[: args.limit]
        ],
    }
    dump_json(payload)


def resolve_target_item(helper: Path, query: str | None, item_key: str | None) -> tuple[str, str | None]:
    if item_key:
        return item_key, None
    if not query:
        raise SystemExit("Provide --query or --item-key")
    matches = search_matches(helper, query)
    top = dominant_match(matches)
    if top is None:
        exit_with(
            "Multiple plausible matches found; ask the user to choose an item before creating the note.",
            payload={
                "query": query,
                "matches": [
                    {
                        "score": round(match.score, 3),
                        "item_key": match.row.get("key"),
                        "citekey": match.row.get("bibtexKey"),
                        "title": match.row.get("title"),
                        "authors": match.row.get("creators") or [],
                        "year": match.row.get("year"),
                    }
                    for match in matches[:5]
                ],
            },
        )
    return top.row["key"], top.row.get("bibtexKey")


def cmd_create(args: argparse.Namespace) -> None:
    helper = discover_zotero_helper(args.plugin_root)
    status = run_helper(helper, "status", "--json") or {}
    if not status.get("local_api_enabled_pref"):
        raise SystemExit("Zotero local API is disabled. Enable it before creating a note.")
    if not status.get("api_running"):
        raise SystemExit(
            "Zotero local API is not reachable. Start Zotero or fix the local API before creating a note."
        )

    item_key, fallback_citekey = resolve_target_item(helper, args.query, args.item_key)
    payload = note_payload(
        helper,
        status.get("base_url") or "http://127.0.0.1:23119",
        item_key=item_key,
        fallback_citekey=fallback_citekey,
    )
    note_text = build_note_content(payload, created_on=date.today().isoformat())
    filename = filename_for_note(payload)
    output_path, action = choose_output_path(Path(args.vault), filename, args.if_exists)
    output_path.write_text(note_text, encoding="utf-8")

    dump_json(
        {
            "action": action,
            "path": str(output_path),
            "item_key": item_key,
            "citekey": payload.get("citekey"),
            "title": payload.get("title"),
            "tags": payload.get("tags"),
        }
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Search Zotero and create an Obsidian-compatible Markdown note."
    )
    parser.add_argument(
        "--plugin-root",
        help="Optional Zotero plugin root that contains skills/zotero/scripts/zotero.py",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    status = subparsers.add_parser("status", help="Check Zotero readiness through the Zotero helper")
    status.set_defaults(func=cmd_status)

    search = subparsers.add_parser("search", help="Search Zotero and rank likely matches")
    search.add_argument("--query", required=True)
    search.add_argument("--limit", type=int, default=5)
    search.set_defaults(func=cmd_search)

    create = subparsers.add_parser("create", help="Create an Obsidian Markdown note for a Zotero item")
    source = create.add_mutually_exclusive_group(required=True)
    source.add_argument("--query")
    source.add_argument("--item-key")
    create.add_argument(
        "--vault",
        default=DEFAULT_VAULT_DIR,
        help="Absolute or user-resolved Obsidian folder",
    )
    create.add_argument(
        "--if-exists",
        choices=["error", "suffix", "overwrite"],
        default="error",
        help="Conflict behavior for an existing note path",
    )
    create.set_defaults(func=cmd_create)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
