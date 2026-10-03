"""Deterministic clean-up of the model-written understanding index (consolidate.py Pass B).

Two defects seen in real indexes are repaired here, after the model reply is parsed:

- `plot_points[*].time` written as "00:95" (seconds >= 60, i.e. raw scene seconds poured into
  the ss field). The time is re-read as minutes * 60 + seconds and rewritten as canonical
  "MM:SS" ("H:MM:SS" past an hour). A time that cannot be parsed, or that lies past the end
  of the analysed scenes, is dropped instead of kept as a wrong timestamp.
- The same character split into two `characters` entries where one's name is the other's name
  or alias (a shared alias alone is not enough, and an entry that would bridge two different
  characters is left on its own). Which entries merge does not depend on their order; the
  merged character sits at the first occurrence and keeps the first name any member has, the
  other names become aliases, and relationships are re-pointed at the surviving name.

Pure functions only: no I/O, no model calls.
"""

import json
import re

_TIME_RE = re.compile(r"^\s*(?:(\d+):)?(\d+):(\d+(?:\.\d+)?)\s*$")
_APPROX_RE = re.compile(r"^(?:大约|约)\s*")
# "01:20左右" / "3分钟前后" / "80秒许": approximation words, dropped wherever they sit.
_APPROX_WORD_RE = re.compile(r"\s*(?:左右|前后|许)")
# "第3分钟" / "第95秒" / "第2小时": an ordinal names the Nth unit, read as its start
# ((N-1) units, "第1分钟" is 00:00). Only a lone whole unit: "第3分20秒" is the time 03:20.
_ORDINAL_UNIT_RE = re.compile(
    r"^第\s*(\d+)\s*(小时|分钟|分|秒)(?=\s*(?:$|[-~～—–至到]))"
)
_ORDINAL_UNIT_SECONDS = {"小时": 3600, "分钟": 60, "分": 60, "秒": 1}
# Any other ordinal prefix on a time ("第01:20", "第3分20秒") reads as that time.
_ORDINAL_RE = re.compile(r"^第\s*(?=\d)")
# A range starts with a digit, so a leading "-" (a negative time) is not a range separator.
_RANGE_RE = re.compile(r"^(\d[\d:.]*?)\s*(?:秒|s|S)?\s*(?:-|~|～|—|–|至|到)\s*\d")
_UNIT_RE = re.compile(r"\s*(?:秒|s|S)$")
# "3分20秒" / "1小时2分3秒" / "3分钟" / "1.5分": rewritten as seconds ("200.0" / "3723.0" /
# "180.0" / "90.0") before parsing.
_CN_TIME_RE = re.compile(
    r"(?:(\d+)\s*(?:小时|时)\s*)?(\d+(?:\.\d+)?)\s*分(?:钟)?\s*(?:(\d+(?:\.\d+)?)\s*秒?)?"
)
_DURATION_SLACK_SECONDS = 1.0
_CONFIDENCE_RANK = {"high": 3, "medium": 2, "low": 1}
_LIST_FIELDS = ("aliases", "visual_descriptions", "asr_mentions", "evidence_ids")


def _ordinal_unit(match):
    count, unit = match.groups()
    return repr(max(int(count) - 1, 0) * _ORDINAL_UNIT_SECONDS[unit])


def _cn_time(match):
    hours, minutes, secs = match.groups()
    return repr(int(hours or 0) * 3600 + float(minutes) * 60 + float(secs or 0))


def _plot_time_text(value):
    """Strip the wrappers models put around a time: 约/大约/左右/前后/许, an ordinal 第, a
    trailing 秒/s, a range tail.

    A full-width colon ("00：95") is read as ":", minute/second words ("3分20秒", "1.5分")
    are rewritten as seconds, an ordinal unit ("第3分钟") as the start of that unit."""
    text = str(value or "").strip().replace("：", ":")
    text = _APPROX_WORD_RE.sub("", _APPROX_RE.sub("", text))
    text = _ORDINAL_UNIT_RE.sub(_ordinal_unit, text)
    text = _ORDINAL_RE.sub("", text)
    text = _CN_TIME_RE.sub(_cn_time, text)
    match = _RANGE_RE.match(text)
    if match:
        text = match.group(1)
    return _UNIT_RE.sub("", text).strip()


def parse_plot_time(value):
    """Seconds for "MM:SS" / "H:MM:SS" / a bare number, or None when unreadable.

    Fields are not range-checked: "00:95" is 95 s, "1:75" is 135 s (the model wrote scene
    seconds into the seconds field). "约01:20", "01:20左右", "12.5秒", "00：95", "3分20秒",
    "1.5分", an ordinal unit ("第3分钟" is the third minute, read as its start 02:00; "第1分钟"
    and "第0分钟" are 00:00; "第95秒" is 94 s; "第2小时" is 1:00:00) and the start of a range
    ("01:20-01:45") are read too.
    Negative or non-finite values are unreadable."""
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


def _character_nodes(characters):
    """Pool dict entries by name key: entries with the same name are one character.

    Returns nodes as {"members": [input positions], "name": key, "aliases": alias keys}; an
    entry without a name is a node of its own."""
    nodes = []
    by_name = {}
    for position, item in enumerate(characters):
        if not isinstance(item, dict):
            continue
        name = _term_key(item.get("name"))
        aliases = _alias_keys(item)
        if name and name in by_name:
            node = nodes[by_name[name]]
            node["members"].append(position)
            node["aliases"] |= aliases
            continue
        if name:
            by_name[name] = len(nodes)
        nodes.append({"members": [position], "name": name, "aliases": set(aliases)})
    return nodes


def _linked(x, y):
    """True when one node's name is the other's alias. Two aliases alone never link: a
    generic alias the model hands two people (男子, 老板) must not fold them into one."""
    return bool(
        (x["name"] and x["name"] in y["aliases"]) or (y["name"] and y["name"] in x["aliases"])
    )


def _character_groups(nodes):
    """Partition nodes into characters, independent of their order.

    A node whose linked neighbours include two nodes not linked to each other is a bridge
    (a 男子 extra named by an alias two leads share, or one entry carrying two people's
    names): it stays on its own and links nothing. The other nodes are unioned along their
    links. Returns groups as sorted member-position lists, ordered by first occurrence."""
    count = len(nodes)
    links = [
        [j for j in range(count) if j != i and _linked(nodes[i], nodes[j])]
        for i in range(count)
    ]
    bridge = [
        any(
            not _linked(nodes[j], nodes[k])
            for a, j in enumerate(neighbours)
            for k in neighbours[a + 1:]
        )
        for neighbours in links
    ]
    parent = list(range(count))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(count):
        if bridge[i]:
            continue
        for j in links[i]:
            if not bridge[j]:
                parent[root(i)] = root(j)
    members = {}
    for i, node in enumerate(nodes):
        members.setdefault(root(i), []).extend(node["members"])
    return sorted((sorted(group) for group in members.values()), key=lambda group: group[0])

def merge_characters(characters):
    """Merge character entries that are the same person by name (case/space-insensitive).

    Entries with the same name are one character. Two characters merge when one's name is
    the other's alias; a shared alias alone does not merge, and an entry that links two
    characters not linked to each other is kept on its own instead of folding them together.
    The partition does not depend on input order: each merged character sits at its first
    entry's position and keeps the name of its first entry that has one, the others' names
    become aliases. Returns (characters, renames) where renames maps every merged name key
    to the surviving name. Non-dict entries pass through."""
    characters = list(characters or [])
    groups = _character_groups(_character_nodes(characters))
    first_of = {group[0]: group for group in groups}
    merged = []
    renames = {}
    for position, item in enumerate(characters):
        if not isinstance(item, dict):
            merged.append(item)
            continue
        group = first_of.get(position)
        if group is None:
            continue  # folded into an earlier entry
        entry = dict(item)
        if not _term_key(entry.get("name")):
            # A nameless first entry keeps its position but takes the first member name.
            named = next(
                (characters[m] for m in group if _term_key(characters[m].get("name"))), None
            )
            if named is not None:
                entry = {"name": named["name"], **{k: v for k, v in entry.items() if k != "name"}}
        for other in group[1:]:
            _merge_into(entry, characters[other])
            name = _term_key(characters[other].get("name"))
            if name:
                renames[name] = entry.get("name")
        merged.append(entry)
    return merged, renames


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
