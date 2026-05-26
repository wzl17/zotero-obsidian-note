---
name: zotero-obsidian-note
description: Use this skill when the user wants to find a paper in their local Zotero library and create an Obsidian-compatible Markdown literature note for it. It checks Zotero readiness, searches by title, DOI, citation key, or fuzzy query, gathers paper metadata, preserves Zotero context such as collections and tags, and writes a YAML-frontmatter note into an Obsidian vault folder.
---

# Zotero Obsidian Note

Use this skill to turn a Zotero item into an Obsidian Markdown literature note.

Base the workflow on the existing Zotero helper instead of reimplementing Zotero access:

```bash
python3 <plugin-root>/skills/zotero/scripts/zotero.py <command>
```

Resolve `<plugin-root>` by locating the installed Zotero plugin, or pass it explicitly to the local helper in this skill.

## Workflow

1. Start with readiness:

```bash
python3 scripts/create_note.py status
```

2. Default the vault folder to:

```text
/Users/wzl17/Library/CloudStorage/OneDrive-Personal/Personal/Files/Obsidian/Notes/Research/Papers
```

Ask for a different destination only when the user wants another location.
3. Search Zotero with the paper title, DOI, citation key, or fuzzy query:

```bash
python3 scripts/create_note.py search --query "attention is all you need"
```

4. If there are multiple plausible matches, ask the user to choose unless one result is clearly dominant.
5. Create the note only after you know the target item and vault path:

```bash
python3 scripts/create_note.py create --query "attention is all you need"
```

Use `--item-key` when the item is already known. Override the destination with `--vault "/other/path"` when needed. Use `--if-exists suffix` only when the user explicitly wants a second file. Use `--if-exists overwrite` only with explicit confirmation.

## Behavior

- The helper preserves exact Zotero collections and Zotero tags in frontmatter.
- It also generates conservative Obsidian tags such as `paper`, `zotero`, `literature-note`, normalized item type, year, venue, and a small set of domain/topic tags when title or abstract provide strong evidence.
- The note body includes `# Title`, `## Abstract`, and `## Notes`.
- The filename prefers the BibTeX citekey and otherwise falls back to first author, year, and a shortened title slug.
- On conflicts, do not overwrite by default. Ask the user whether to overwrite, update missing fields manually, or create a suffixed filename.

## Output expectations

- Report the absolute path of the created note.
- If Zotero is not ready, name the exact blocker from the readiness check.
- If matching is ambiguous, show the best few candidates with title, authors, year, Zotero key, and citekey when available.
