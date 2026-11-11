"""Cut QC bookkeeping for clip_plan_validated.json."""


def update_cut_qc(plan):
    """Populate clip_plan_validated.json['qc'] as the single cut QC source."""
    qc = dict(plan.get("qc", {}))
    warnings = list(qc.get("warnings", []))
    blocking = list(qc.get("blocking", []))
    total = plan["total_duration"]
    target = plan["target_duration"]
    if target is None:
        target_status = "missing"
        target_qc = {
            "status": target_status,
            "target_duration": None,
            "total_duration": round(total, 3),
        }
    else:
        ratio = total / target  # parse_duration_seconds guarantees target > 0
        if ratio < 0.85:
            target_status = "under"
        elif ratio > 1.15:
            target_status = "over"
        else:
            target_status = "ok"
        severity = "warning" if target_status in {"under", "over"} else None
        target_qc = {
            "status": target_status,
            "target_duration": round(target, 3),
            "total_duration": round(total, 3),
            "ratio": round(ratio, 3),
            "warning_thresholds": {"under": 0.85, "over": 1.15},
        }
        if severity:
            warnings.append({
                "code": "target_duration_drift",
                "status": target_status,
                "severity": "warning",
                "target_duration": round(target, 3),
                "total_duration": round(total, 3),
                "ratio": round(ratio, 3),
            })
    qc["target_duration_status"] = target_status
    qc["target_duration"] = target_qc
    qc.setdefault("boundary_status", {})
    qc["clip_count"] = len(plan["clips"])
    qc["total_duration"] = round(total, 3)
    if warnings:
        qc["warnings"] = warnings
    if blocking:
        qc["blocking"] = blocking
    else:
        qc.pop("blocking", None)
    plan["qc"] = qc
    return plan
