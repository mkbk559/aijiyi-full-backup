"""Select a clean same-speaker reference from prepared voice clips.

Uses the CAM++ speaker embedding bundled with Seed-VC to find the dominant
speaker cluster, then joins the clips closest to that cluster centroid. The
result is suitable for zero-shot Seed-VC/GPT-SoVITS style profiles.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def select_closest(
    rows: list[dict],
    *,
    centroid: list[float],
    count: int = 3,
    min_seconds: float = 4.5,
    max_seconds: float = 9.0,
) -> list[dict]:
    ranked: list[tuple[float, dict]] = []
    try:
        import numpy as np
    except ImportError:
        np = None  # type: ignore[assignment]
    if np is not None:
        center = np.asarray(centroid, dtype=np.float32)
    else:
        center = [float(value) for value in centroid]
    for row in rows:
        seconds = float(row.get("seconds") or 0.0)
        if not (min_seconds <= seconds <= max_seconds):
            continue
        if np is not None:
            vector = np.asarray(row["embedding"], dtype=np.float32)
            distance = 1.0 - float(np.dot(vector, center))
        else:
            vector = [float(value) for value in row["embedding"]]
            distance = 1.0 - sum(
                left * right for left, right in zip(vector, center, strict=False))
        ranked.append((distance, row))
    ranked.sort(key=lambda item: item[0])
    return [row for _distance, row in ranked[:max(1, int(count))]]


def _dominant_cluster(rows: list[dict]) -> tuple[int, list[float], dict]:
    import numpy as np
    from sklearn.cluster import AgglomerativeClustering

    matrix = np.asarray([row["embedding"] for row in rows], dtype=np.float32)
    max_clusters = min(6, max(2, len(rows) // 4))
    best_labels = np.zeros(len(rows), dtype=int)
    best_score = -1.0
    best_k = 1
    for clusters in range(2, max_clusters + 1):
        labels = AgglomerativeClustering(
            n_clusters=clusters, metric="cosine", linkage="average"
        ).fit_predict(matrix)
        score = 0.0
        for label in {int(value) for value in labels}:
            group = matrix[labels == label]
            center = group.mean(axis=0)
            center /= max(1e-8, float(np.linalg.norm(center)))
            score += float(np.mean(group @ center)) * len(group)
        score /= len(rows)
        if score > best_score:
            best_score, best_labels, best_k = score, labels, clusters

    counts = {
        label: int(sum(1 for value in best_labels if int(value) == label))
        for label in {int(value) for value in best_labels}
    }
    dominant = max(counts, key=lambda label: counts[label])
    group = matrix[best_labels == dominant]
    centroid = group.mean(axis=0)
    centroid /= max(1e-8, float(np.linalg.norm(centroid)))
    return dominant, centroid.tolist(), {
        "clusters": best_k,
        "cluster_counts": {str(key): value for key, value in sorted(counts.items())},
        "dominant": dominant,
        "silhouette_like_score": round(best_score, 5),
    }


def _embed_campplus(
    clips: list[Path],
    *,
    repo: Path,
    device: str,
) -> list[dict]:
    import soundfile as sf
    import torch
    import torchaudio

    sys.path.insert(0, str(repo))
    from modules.campplus.DTDNN import CAMPPlus

    torch_device = torch.device(device)
    model = CAMPPlus(feat_dim=80, embedding_size=192)
    state = torch.load(
        repo / "campplus_cn_common.bin",
        map_location="cpu",
        weights_only=True,
    )
    model.load_state_dict(state)
    model.eval()
    model.to(torch_device)
    rows: list[dict] = []
    with torch.inference_mode():
        for index, path in enumerate(clips, 1):
            audio, sample_rate = sf.read(path, dtype="float32", always_2d=True)
            mono = torch.from_numpy(audio.mean(axis=1))
            if sample_rate != 16000:
                mono = torchaudio.functional.resample(mono, sample_rate, 16000)
            if mono.numel() < 16000:
                continue
            feat = torchaudio.compliance.kaldi.fbank(
                mono.unsqueeze(0),
                num_mel_bins=80,
                dither=0,
                sample_frequency=16000,
            )
            feat = feat - feat.mean(dim=0, keepdim=True)
            vector = model(feat.unsqueeze(0).to(torch_device))
            vector = torch.nn.functional.normalize(
                vector.float().cpu(), dim=-1).squeeze(0)
            rows.append({
                "file": str(path),
                "seconds": round(mono.numel() / 16000.0, 3),
                "embedding": vector.tolist(),
            })
            if index % 20 == 0:
                print(f"speaker embeddings: {index}/{len(clips)}", flush=True)
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--clips", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument(
        "--repo", type=Path,
        default=Path(__file__).resolve().parents[1] / "voice-engine" / "seed-vc",
    )
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    parser.add_argument("--count", type=int, default=3)
    parser.add_argument("--no-asr", action="store_true")
    args = parser.parse_args()

    clips = sorted(args.clips.glob("*.wav"))
    if len(clips) < 3:
        raise SystemExit("need at least three wav clips")
    rows = _embed_campplus(clips, repo=args.repo.resolve(), device=args.device)
    if len(rows) < 3:
        raise SystemExit("not enough valid clips for speaker selection")
    cluster_id, centroid, cluster_stats = _dominant_cluster(rows)
    selected = select_closest(
        rows, centroid=centroid, count=max(1, min(5, args.count)))
    if not selected:
        raise SystemExit("no candidate in the preferred duration range")

    import numpy as np
    import soundfile as sf

    samples: list[np.ndarray] = []
    sample_rate = 0
    for row in selected:
        audio, rate = sf.read(row["file"], dtype="float32", always_2d=False)
        if sample_rate and rate != sample_rate:
            raise SystemExit("candidate clips have mixed sample rates")
        sample_rate = int(rate)
        if audio.ndim > 1:
            audio = audio.mean(axis=1)
        samples.append(audio.astype(np.float32, copy=False))
        samples.append(np.zeros(int(sample_rate * 0.2), dtype=np.float32))
    merged = np.concatenate(samples[:-1]) if samples else np.zeros(1, np.float32)
    peak = float(np.max(np.abs(merged))) if merged.size else 0.0
    if peak > 0:
        merged *= min(0.95 / peak, 8.0)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    sf.write(args.out, merged, sample_rate, subtype="PCM_16")

    transcript = ""
    confidence = 0.0
    if not args.no_asr:
        from aiji.real_engines import transcribe_file

        transcript, confidence = transcribe_file(args.out)
        (args.out.parent / "reference.txt").write_text(
            transcript + "\n", encoding="utf-8")

    manifest_path = args.manifest or args.out.parent / "reference_selection.json"
    payload = {
        "reference": str(args.out.resolve()),
        "duration_sec": round(len(merged) / sample_rate, 3),
        "sample_rate": sample_rate,
        "transcript": transcript,
        "confidence": confidence,
        "speaker_cluster": cluster_id,
        "cluster": cluster_stats,
        "selected": [
            {
                "file": row["file"],
                "seconds": row["seconds"],
            }
            for row in selected
        ],
    }
    manifest_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
