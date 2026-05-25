from __future__ import annotations

from typing import Any


def compute_retrieval(retrieved_chunks: list[dict[str, Any]],
                      reference_chunk_ids: list[str] | None) -> dict[str, float]:
    """Compute retrieval quality metrics.

    Logic mirrored from evaluation_service.py lines 488-531.
    """
    if not reference_chunk_ids:
        return {}

    retrieved_ids = [c.get("chunk_id", "") for c in retrieved_chunks]
    retrieved_set = set(retrieved_ids)
    relevant_set = set(reference_chunk_ids)

    # Precision: proportion of retrieved that are relevant
    precision = len(retrieved_set & relevant_set) / len(retrieved_set) if retrieved_set else 0.0

    # Recall: proportion of relevant that were retrieved
    recall = len(retrieved_set & relevant_set) / len(relevant_set) if relevant_set else 0.0

    # F1 score
    f1 = 2 * (precision * recall) / (precision + recall) if (precision + recall) > 0 else 0.0

    # Hit rate: whether any relevant chunk was retrieved
    hit_rate = 1.0 if (retrieved_set & relevant_set) else 0.0

    # MRR (Mean Reciprocal Rank): position of first relevant chunk
    mrr = 0.0
    for i, chunk_id in enumerate(retrieved_ids, 1):
        if chunk_id in relevant_set:
            mrr = 1.0 / i
            break

    return {
        "precision": precision,
        "recall": recall,
        "f1_score": f1,
        "hit_rate": hit_rate,
        "mrr": mrr,
    }
