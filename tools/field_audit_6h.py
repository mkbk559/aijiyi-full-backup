"""Run the existing field audit for six hours with separate output files."""

from pathlib import Path

from field_audit_30m import worker
import field_audit_30m as audit


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1] / "data"
    audit.DURATION_S = 6 * 60 * 60
    audit.OUT = root / "field_audit_6h.jsonl"
    audit.SUMMARY = root / "field_audit_6h_summary.json"
    worker()
