"""Cut QC bookkeeping for clip_plan_validated.json."""


def update_cut_qc(plan, *, allow_duration_drift=False, duration_drift_allowed_by=None):
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
        ratio = total / target if target > 0 else 0.0
        if ratio < 0.85:
            target_status = "under"
        elif ratio > 1.15:
            target_status = "over"
        else:
            target_status = "ok"
        severity = None
        if ratio < 0.60 or ratio > 1.40:
            severity = "blocking"
        elif target_status in {"under", "over"}:
            severity = "warning"
        target_qc = {
            "status": target_status,
            "target_duration": round(target, 3),
            "total_duration": round(total, 3),
            "ratio": round(ratio, 3),
            "warning_thresholds": {"under": 0.85, "over": 1.15},
            "blocking_thresholds": {"under": 0.60, "over": 1.40},
        }
        if severity:
            warning = {
                "code": "target_duration_drift",
                "status": target_status,
                "severity": "warning" if allow_duration_drift else severity,
                "target_duration": round(target, 3),
                "total_duration": round(total, 3),
                "ratio": round(ratio, 3),
            }
            if allow_duration_drift:
                warning["allowed"] = True
                warning["duration_drift_allowed_by"] = (
                    duration_drift_allowed_by or "--allow-duration-drift"
                )
                target_qc["duration_drift_allowed_by"] = warning[
                    "duration_drift_allowed_by"
                ]
            warnings.append(warning)
            if severity == "blocking" and not allow_duration_drift:
                blocking.append(warning)
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
