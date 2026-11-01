"""Summarize the stopped six-hour field audit from recorded samples."""
import json
from collections import Counter
from datetime import datetime
from pathlib import Path

root = Path(__file__).resolve().parents[1] / "data"
samples = [json.loads(line) for line in (root / "field_audit_6h.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
if not samples:
    raise SystemExit("No six-hour audit samples")
start, end = samples[0]["ts"], samples[-1]["ts"]
events = [json.loads(line) for line in (root / "field_monitor.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
events = [event for event in events if start <= event.get("ts", 0) <= end + 60]
alerts = Counter(alert for event in events for alert in event.get("alerts", []))
errors = [line for event in events for line in event.get("log_errors", [])]
first, last = samples[0], samples[-1]
summary = {
    "status": "stopped_early_by_user",
    "requested_duration_seconds": 21600,
    "started_at": datetime.fromtimestamp(start).astimezone().isoformat(),
    "last_sample_at": datetime.fromtimestamp(end).astimezone().isoformat(),
    "recorded_duration_seconds": round(end - start),
    "samples": len(samples),
    "health_failures": sum(not bool((s.get("health") or {}).get("ok")) for s in samples),
    "max_pending": max((int((s.get("health") or {}).get("pending") or 0) for s in samples), default=0),
    "qq_assistant_first": (first.get("aiji") or {}).get("qq_assistant"),
    "qq_assistant_last": (last.get("aiji") or {}).get("qq_assistant"),
    "alert_sample_counts": dict(alerts),
    "logged_error_count": len(errors),
    "latest_logged_errors": errors[-5:],
    "last_sample": last,
}
(root / "field_audit_6h_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({k: summary[k] for k in ("status", "started_at", "last_sample_at", "recorded_duration_seconds", "samples", "health_failures", "max_pending", "qq_assistant_first", "qq_assistant_last", "alert_sample_counts", "logged_error_count")}, ensure_ascii=False))
