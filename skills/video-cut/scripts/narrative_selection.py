"""Validate explicitly required source spans in a normalized constant-speed cut plan.

This checks source-span selection and ordering only.  It does not claim that the
rendered mix is audible or that the declared content is semantically correct.
"""

import math
import os
from typing import TypeGuard


_EPSILON = 1e-9


def _invalid(message):
    return {
        "selection_status": "BLOCK",
        "nodes": [],
        "findings": [
            {
                "code": "REQUIRED_EVIDENCE_INVALID",
                "message": message,
            }
        ],
        "semantic_status": "NOT_CHECKED",
    }


def _realpath(value, *, require_absolute=True):
    if not isinstance(value, str) or (require_absolute and not os.path.isabs(value)):
        raise ValueError("required evidence source must be an absolute path")
    return os.path.realpath(value)


def _finite_nonnegative(value: object) -> TypeGuard[int | float]:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 0
    )


def _validate_contract(contract):
    if not isinstance(contract, dict):
        raise ValueError("required_evidence must be an object")
    nodes = contract.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        raise ValueError("required_evidence.nodes must be a non-empty array")
    if not isinstance(contract.get("before"), list):
        raise ValueError("required_evidence.before must be an array")

    normalized = []
    ids = set()
    for index, node in enumerate(nodes, 1):
        if not isinstance(node, dict):
            raise ValueError(f"required evidence node #{index} must be an object")
        node_id = node.get("id")
        if not isinstance(node_id, str) or not node_id.strip() or node_id in ids:
            raise ValueError("required evidence node ids must be unique non-empty strings")
        ids.add(node_id)
        track = node.get("track")
        if track not in {"audio", "video"}:
            raise ValueError(f"required evidence node {node_id} has invalid track")
        content = node.get("content")
        if not isinstance(content, str) or not content.strip():
            raise ValueError(f"required evidence node {node_id} has empty content")
        start, end = node.get("start"), node.get("end")
        if not (_finite_nonnegative(start) and _finite_nonnegative(end) and end > start):
            raise ValueError(f"required evidence node {node_id} has invalid start/end")
        source_id = node.get("source_id")
        if "source_id" in node and (
            not isinstance(source_id, str) or not source_id.strip()
        ):
            raise ValueError(f"required evidence node {node_id} has invalid source_id")

        item = {
            "id": node_id,
            "track": track,
            "content": content,
            "source": _realpath(node.get("source")),
            "start": start,
            "end": end,
        }
        if "source_id" in node:
            item["source_id"] = source_id
        normalized.append(item)

    edges = []
    for edge in contract["before"]:
        if (
            not isinstance(edge, list)
            or len(edge) != 2
            or edge[0] not in ids
            or edge[1] not in ids
            or edge[0] == edge[1]
        ):
            raise ValueError("required_evidence.before contains an invalid node pair")
        edges.append((edge[0], edge[1]))
    return normalized, edges


def _same(value, expected):
    return abs(value - expected) <= _EPSILON


def _matching_segments(node, validated_plan, input_video):
    segments = []
    for clip in validated_plan["clips"]:
        source = _realpath(
            os.fspath(clip.get("source_path", input_video)), require_absolute=False
        )
        if source != node["source"]:
            continue
        if "source_id" in node and clip.get("source_id") != node["source_id"]:
            continue

        clip_start = clip["source_start"]
        clip_end = clip["source_end"]
        start = max(node["start"], clip_start)
        end = min(node["end"], clip_end)
        if end <= start:
            continue
        output_start = clip["output_start"] + start - clip_start
        segments.append(
            {
                "source_id": clip.get("source_id"),
                "source_start": start,
                "source_end": end,
                "output_start": output_start,
                "output_end": output_start + end - start,
            }
        )
    return sorted(segments, key=lambda item: item["output_start"])


def _occurrences(node, validated_plan, input_video):
    segments = _matching_segments(node, validated_plan, input_video)
    occurrences = []
    for index, segment in enumerate(segments):
        if not _same(segment["source_start"], node["start"]):
            continue
        source_cursor = segment["source_end"]
        output_cursor = segment["output_end"]
        for following in segments[index + 1 :]:
            if source_cursor >= node["end"] - _EPSILON:
                break
            if (
                following["source_id"] == segment["source_id"]
                and _same(following["source_start"], source_cursor)
                and _same(following["output_start"], output_cursor)
            ):
                source_cursor = following["source_end"]
                output_cursor = following["output_end"]
        if _same(source_cursor, node["end"]):
            occurrences.append(
                {"start": segment["output_start"], "end": output_cursor}
            )
    return occurrences


def check_required_evidence(
    contract, validated_plan, *, input_video, source_audio: dict[str, bool]
) -> dict:
    """Return bounded source-selection QC for a declared required-evidence contract."""
    try:
        nodes, edges = _validate_contract(contract)
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        return _invalid(str(exc))

    findings = []
    occurrences_by_id = {}
    fragment_starts_by_id = {}
    report_nodes = []
    for node in nodes:
        audio_available = source_audio.get(node["source"]) is True
        if node["track"] == "audio" and not audio_available:
            occurrences = []
            fragment_starts = []
            findings.append(
                {
                    "code": "REQUIRED_EVIDENCE_AUDIO_UNAVAILABLE",
                    "message": (
                        f"audio node {node['id']} requires a source with an audio stream"
                    ),
                    "node_id": node["id"],
                }
            )
        else:
            fragment_starts = [
                segment["output_start"]
                for segment in _matching_segments(node, validated_plan, input_video)
            ]
            occurrences = _occurrences(node, validated_plan, input_video)
            if not occurrences:
                findings.append(
                    {
                        "code": "REQUIRED_EVIDENCE_MISSING",
                        "message": (
                            f"node {node['id']} is not retained as one continuous source "
                            "and output span"
                        ),
                        "node_id": node["id"],
                    }
                )
        occurrences_by_id[node["id"]] = occurrences
        fragment_starts_by_id[node["id"]] = fragment_starts
        report_nodes.append({**node, "occurrences": occurrences})

    for premise_id, result_id in edges:
        premise_occurrences = occurrences_by_id[premise_id]
        result_fragment_starts = fragment_starts_by_id[result_id]
        unpreceded_starts = [
            result_start
            for result_start in result_fragment_starts
            if not any(
                premise["end"] <= result_start + _EPSILON
                for premise in premise_occurrences
            )
        ]
        ordered = bool(result_fragment_starts) and not unpreceded_starts
        if not ordered:
            detail = (
                f"; earliest unpreceded output start={min(unpreceded_starts):.9g}s"
                if unpreceded_starts
                else "; no matching result fragment was retained"
            )
            findings.append(
                {
                    "code": "REQUIRED_EVIDENCE_ORDER",
                    "message": (
                        f"every retained fragment of {result_id} must follow a complete "
                        f"occurrence of {premise_id}{detail}"
                    ),
                    "node_id": result_id,
                }
            )

    return {
        "selection_status": "BLOCK" if findings else "PASS",
        "nodes": report_nodes,
        "findings": findings,
        "semantic_status": "NOT_CHECKED",
    }
