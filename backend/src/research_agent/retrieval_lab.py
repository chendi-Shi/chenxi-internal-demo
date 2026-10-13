"""Controlled retrieval experiments. No changes to the live HTTP/MCP contract."""
from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class Candidate:
    chunk_id: int
    document_id: str
    title: str
    text: str
    score: float
    page: int | None = None


def fuse(lists: list[list[Candidate]], *, method: str, k: int = 60) -> list[Candidate]:
    """RRF uses rank only; normalized fusion is a separate ablation."""
    if method not in {"rrf", "normalized"} or k < 1:
        raise ValueError("invalid_fusion")
    scores, items = {}, {}
    for rows in lists:
        seen = set()
        maximum = max((max(0, r.score) for r in rows), default=0) or 1
        rank = 0
        for row in rows:
            if row.chunk_id in seen:
                continue
            seen.add(row.chunk_id)
            rank += 1
            items.setdefault(row.chunk_id, row)
            contribution = 1 / (k + rank) if method == "rrf" else max(0, row.score) / maximum
            scores[row.chunk_id] = scores.get(row.chunk_id, 0) + contribution
    return [Candidate(**(vars(items[i]) | {"score": scores[i]}))
            for i in sorted(scores, key=lambda i: (-scores[i], i))]


STAGES = ("lexical", "dense", "hybrid", "rrf", "rerank", "rewrite")


def run_pipeline(store, query: str, *, stage: str, filters=None, limit=10,
                 candidate_limit=100, rewrite: Callable | None = None,
                 rerank: Callable | None = None):
    if stage not in STAGES or not query.strip() or not 1 <= limit <= candidate_limit <= 1000:
        raise ValueError("invalid_experiment")
    started = time.perf_counter()
    warnings = []
    queries = [query]
    if stage == "rewrite":
        if rewrite is None:
            raise ValueError("rewrite_stage_requires_provider")
        try:
            variants = rewrite(query)
            if not isinstance(variants, list) or any(not isinstance(v, str) for v in variants):
                raise ValueError("invalid_rewrites")
            queries.extend(v.strip() for v in variants if v.strip() and len(v) <= 500)
            queries = list(dict.fromkeys(queries))[:3]
        except Exception:
            warnings.append("rewrite_failed_using_original_query")
    lists = []
    for q in queries:
        if stage != "dense":
            lists.append(store.lexical(q, filters or {}, candidate_limit))
        if stage != "lexical":
            try:
                lists.append(store.dense(q, filters or {}, candidate_limit))
            except Exception:
                if stage == "dense":
                    raise
                warnings.append("dense_failed_using_lexical")
    if stage in {"lexical", "dense"}:
        rows = lists[0]
    else:
        rows = fuse(lists, method="normalized" if stage == "hybrid" else "rrf")
    if stage in {"rerank", "rewrite"}:
        if rerank is None:
            raise ValueError("rerank_stage_requires_provider")
        try:
            # Repeated passages from a long PDF must not consume the entire rerank budget.
            pool, rerank_documents = [], set()
            for row in rows:
                if row.document_id not in rerank_documents:
                    rerank_documents.add(row.document_id)
                    pool.append(row)
                if len(pool) == 50:
                    break
            values = list(rerank(query, pool))
            if len(values) != len(pool) or any(not math.isfinite(float(v)) for v in values):
                raise ValueError("invalid_rerank_scores")
            rows = sorted([Candidate(**(vars(r) | {"score": float(v)}))
                           for r, v in zip(pool, values, strict=True)],
                          key=lambda r: (-r.score, r.chunk_id))
        except Exception:
            warnings.append("rerank_failed_using_rrf")
    # One result per document, after passage reranking.
    documents, seen = [], set()
    for row in rows:
        if row.document_id not in seen:
            seen.add(row.document_id)
            documents.append(row)
    return {"results": documents[:limit], "stage": stage, "queries": queries,
            "matched_chunks": len(rows), "total_candidates": len(documents),
            "warnings": list(dict.fromkeys(warnings)),
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 2)}


def metrics(ids: list[str], relevance: dict[str, int], k: int = 10):
    if k < 1 or not relevance or any(v < 0 for v in relevance.values()):
        raise ValueError("positive_qrels_required")
    positives = {i for i, v in relevance.items() if v > 0}
    if not positives:
        raise ValueError("positive_qrels_required")
    top = list(dict.fromkeys(ids))[:k]
    hits = [rank for rank, i in enumerate(top, 1) if i in positives]
    dcg = sum((2 ** relevance.get(i, 0) - 1) / math.log2(rank + 1)
              for rank, i in enumerate(top, 1))
    ideal = sum((2 ** v - 1) / math.log2(rank + 1)
                for rank, v in enumerate(sorted(relevance.values(), reverse=True)[:k], 1))
    return {"recall_at_k": len(hits) / len(positives),
            "mrr_at_k": 1 / hits[0] if hits else 0, "ndcg_at_k": dcg / ideal}
