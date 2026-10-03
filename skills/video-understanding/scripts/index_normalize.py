"""Deterministic clean-up of the model-written understanding index (consolidate.py Pass B).

Two defects seen in real indexes are repaired here, after the model reply is parsed:

- `plot_points[*].time` written as "00:95" (seconds >= 60, i.e. raw scene seconds poured into
  the ss field). The time is re-read as minutes * 60 + seconds and rewritten as canonical
  "MM:SS" ("H:MM:SS" past an hour). A time that cannot be parsed, or that lies past the end
  of the analysed scenes, is dropped instead of kept as a wrong timestamp.
- The same character split into two `characters` entries where one's name is the other's name
  or alias (a shared alias alone is not enough). Entries are merged (first occurrence wins the name, the others become aliases) and relationships are
  re-pointed at the surviving name.

Pure functions only: no I/O, no model calls.
"""

import json
import re

_TIME_RE = re.compile(r"^\s*(?:(\d+):)?(\d+):(\d+(?:\.\d+)?)\s*$")
_APPROX_RE = re.compile(r"^(?:大约|约)\s*")
# A range starts with a digit, so a leading "-" (a negative time) is not a range separator.
_RANGE_RE = re.compile(r"^(\d[\d:.]*?)\s*(?:秒|s|S)?\s*(?:-|~|～|—|–|至|到)\s*\d")
_UNIT_RE = re.compile(r"\s*(?:秒|s|S)$")
_DURATION_SLACK_SECONDS = 1.0
_CONFIDENCE_RANK = {"high": 3, "medium": 2, "low": 1}
_LIST_FIELDS = ("aliases", "visual_descriptions", "asr_mentions", "evidence_ids")


def _plot_time_text(value):
    """Strip the wrappers models put around a time: 约/大约, a trailing 秒/s, a range tail."""
    text = str(value or "").strip()
    text = _APPROX_RE.sub("", text)
    match = _RANGE_RE.match(text)
    if match:
        text = match.group(1)
    return _UNIT_RE.sub("", text).strip()


def parse_plot_time(value):
    """Seconds for "MM:SS" / "H:MM:SS" / a bare number, or None when unreadable.

    Fields are not range-checked: "00:95" is 95 s, "1:75" is 135 s (the model wrote scene
    seconds into the seconds field). "约01:20", "12.5秒" and the start of a range
    ("01:20-01:45") are read too. Negative or non-finite values are unreadable."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        seconds = float(value)
    else:
        text = _plot_time_text(value)
        match = _TIME_RE.match(text)
        if match:
            hours, minutes, secs = match.groups()
            seconds = int(hours or 0) * 3600 + int(minutes) * 60 + float(secs)
        else:
            try:
                seconds = float(text)
            except ValueError:
                return None
    if seconds != seconds or seconds in (float("inf"), float("-inf")) or seconds < 0:
        return None
    return seconds


def format_plot_time(seconds):
    whole = int(seconds)
    hours, rest = divmod(whole, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def normalize_plot_times(plot_points, duration=None):
    """Rewrite each object plot point's `time` as canonical text; drop unusable times.

    Returns (plot_points, dropped_count). Bare-string plot points pass through unchanged."""
    out = []
    dropped = 0
    for point in plot_points or []:
        if not isinstance(point, dict) or "time" not in point:
            out.append(point)
            continue
        point = dict(point)
        seconds = parse_plot_time(point["time"])
        if seconds is None or (
            duration is not None and seconds > duration + _DURATION_SLACK_SECONDS
        ):
            point.pop("time")
            dropped += 1
        else:
            point["time"] = format_plot_time(seconds)
        out.append(point)
    return out, dropped


def _term_key(value):
    return " ".join(str(value or "").split()).casefold()


def _aliases(character):
    """`aliases` as a list; a bare string is one alias, not a sequence of characters."""
    value = character.get("aliases")
    if value is None or value == "":
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def _alias_keys(character):
    return {key for key in (_term_key(alias) for alias in _aliases(character)) if key}


def _stable_union(*lists):
    seen = set()
    out = []
    for values in lists:
        for value in values or []:
            key = json.dumps(value, ensure_ascii=False, sort_keys=True)
            if key not in seen:
                seen.add(key)
                out.append(value)
    return out


def _merge_into(target, other):
    other_name = str(other.get("name", "")).strip()
    aliases = _aliases(other)
    if other_name and _term_key(other_name) != _term_key(target.get("name")):
        aliases = [other_name] + aliases
    for field in _LIST_FIELDS:
        if field == "aliases":
            target[field] = _stable_union(_aliases(target), aliases)
        else:
            target[field] = _stable_union(target.get(field), other.get(field))
    target["aliases"] = [
        alias for alias in target["aliases"]
        if _term_key(alias) != _term_key(target.get("name"))
    ]
    for field in ("description", "research_role"):
        if not str(target.get(field) or "").strip() and str(other.get(field) or "").strip():
            target[field] = other[field]
    ranks = [
        _CONFIDENCE_RANK.get(str(item.get("confidence")), 0) for item in (target, other)
    ]
    if ranks[1] > ranks[0]:
        target["confidence"] = other["confidence"]


def _links(name, aliases, group):
    """True when an entry names a member of `group`, or a member's name is the entry's alias.

    Two aliases alone never link: a generic alias the model hands two people (男子, 老板)
    must not fold them into one character."""
    names, group_aliases = group
    return bool(name and (name in names or name in group_aliases)) or bool(aliases & names)


def merge_characters(characters):
    """Merge character entries that are the same person by name (case/space-insensitive).

    Two entries merge when one's name equals the other's name or one of its aliases; a shared
    alias alone does not merge. Deterministic: groups are formed transitively in input order,
    each group keeps the first entry's name and position. Returns (characters, renames) where
    renames maps every merged name key to the surviving name. Non-dict entries pass through."""
    merged = []
    groups = []  # per merged slot: (member name keys, member alias keys), None if not a group
    renames = {}
    for item in characters or []:
        if not isinstance(item, dict):
            merged.append(item)
            groups.append(None)
            continue
        name = _term_key(item.get("name"))
        aliases = _alias_keys(item)
        owners = [i for i, group in enumerate(groups) if group and _links(name, aliases, group)]
        if not owners:
            merged.append(dict(item))
            groups.append(({name} if name else set(), set(aliases)))
            continue
        index = owners[0]
        entry = merged[index]
        names, group_aliases = groups[index]
        # A later entry can bridge two earlier groups: fold the later group in too.
        for other_index in owners[1:]:
            other = merged[other_index]
            _merge_into(entry, other)
            for key, survivor in list(renames.items()):
                if survivor == other.get("name"):
                    renames[key] = entry.get("name")
            if _term_key(other.get("name")):
                renames[_term_key(other.get("name"))] = entry.get("name")
            names |= groups[other_index][0]
            group_aliases |= groups[other_index][1]
            merged[other_index] = None
            groups[other_index] = None
        _merge_into(entry, item)
        if name:
            renames[name] = entry.get("name")
            names.add(name)
        group_aliases |= aliases
    return [entry for entry in merged if entry is not None], renames


def repoint_relationships(relationships, renames):
    """Point relationships at surviving character names; drop self-loops and duplicates."""
    out = []
    by_key = {}
    for rel in relationships or []:
        if not isinstance(rel, dict):
            out.append(rel)
            continue
        rel = dict(rel)
        renamed = False
        for side in ("a", "b"):
            key = _term_key(rel.get(side))
            if key in renames:
                rel[side] = renames[key]
                renamed = True
        if renamed and _term_key(rel.get("a")) == _term_key(rel.get("b")):
            continue  # "X — 同一人 — X" left over from the split entries
        key = (_term_key(rel.get("a")), _term_key(rel.get("b")), _term_key(rel.get("relation")))
        if key in by_key:
            existing = by_key[key]
            existing["evidence_ids"] = _stable_union(
                existing.get("evidence_ids"), rel.get("evidence_ids")
            )
            continue
        by_key[key] = rel
        out.append(rel)
    return out


def scenes_end(scenes):
    """Latest scene end (source seconds), or None when no scene carries a usable end."""
    ends = []
    for scene in scenes or []:
        try:
            ends.append(float(scene["end"]))
        except (KeyError, TypeError, ValueError):
            continue
    return max(ends) if ends else None


def coerce_character_aliases(characters):
    """Rewrite a non-list `aliases` on each character as a list (a string is one alias)."""
    return [
        dict(item, aliases=_aliases(item))
        if isinstance(item, dict) and "aliases" in item and not isinstance(item["aliases"], list)
        else item
        for item in characters or []
    ]


def normalize_index(index, duration=None):
    """Apply both repairs to a parsed index; returns (index, report)."""
    index = dict(index)
    before = len(index.get("characters") or [])
    characters, renames = merge_characters(coerce_character_aliases(index.get("characters")))
    renames = {
        key: name for key, name in renames.items() if key != _term_key(name)
    }
    index["characters"] = characters
    index["relationships"] = repoint_relationships(index.get("relationships"), renames)
    index["plot_points"], dropped = normalize_plot_times(index.get("plot_points"), duration)
    return index, {
        "merged_characters": before - len(characters),
        "dropped_plot_times": dropped,
    }
