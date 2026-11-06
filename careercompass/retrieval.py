"""Hybrid retrieval module combining ChromaDB dense vector search and BM25 sparse search.

Provides document building, persistent vector storage, in-memory BM25 indexing,
phrase-level alias query expansion, curated role profile matching, confidence scoring
and escalation routing, Reciprocal Rank Fusion (RRF), and cosine-based blend weight computation.
"""

from __future__ import annotations

import logging
import math
import os
import re
from pathlib import Path
from typing import Any

# Disable Chroma telemetry before import to silence telemetry errors
os.environ["ANONYMIZED_TELEMETRY"] = "False"

import chromadb
import duckdb
import numpy as np
import yaml
from rank_bm25 import BM25Okapi
from sentence_transformers import SentenceTransformer

from careercompass import config

logger = logging.getLogger(__name__)

DEFAULT_EMBED_MODEL = "BAAI/bge-small-en-v1.5"
CHROMA_PATH = Path("data/chroma")
ALIASES_PATH = Path("data/aliases.yaml")
ROLE_PROFILES_PATH = Path("data/role_profiles.yaml")
COLLECTION_NAME = "occupations"
BGE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "

_TOKEN_PATTERN = re.compile(r"\w+")

# In-memory caches for BM25 and metadata
_BM25_INDEX: BM25Okapi | None = None
_DOC_METADATAS: list[dict[str, Any]] | None = None
_SOC_TO_INDEX: dict[str, int] | None = None
_EMBED_MODEL_INSTANCE: SentenceTransformer | None = None
_ALIASES_MAP: dict[str, str] | None = None
_ROLE_PROFILES_MAP: dict[str, Any] | None = None


def _tokenize(text: str) -> list[str]:
    """Tokenize text into lowercase words."""
    return _TOKEN_PATTERN.findall(text.lower())


def get_embed_model() -> SentenceTransformer:
    """Retrieve or initialize the SentenceTransformer model singleton."""
    global _EMBED_MODEL_INSTANCE
    if _EMBED_MODEL_INSTANCE is None:
        model_name = os.getenv("EMBED_MODEL", DEFAULT_EMBED_MODEL)
        logger.info("Loading embedding model: %s", model_name)
        _EMBED_MODEL_INSTANCE = SentenceTransformer(model_name)
    return _EMBED_MODEL_INSTANCE


def load_aliases(alias_path: Path | str | None = None) -> dict[str, str]:
    """Load aliases YAML dictionary."""
    global _ALIASES_MAP
    if _ALIASES_MAP is not None and alias_path is None:
        return _ALIASES_MAP

    path = Path(alias_path) if alias_path is not None else ALIASES_PATH
    if not path.exists():
        return {}

    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    aliases = data.get("aliases", {})
    if alias_path is None:
        _ALIASES_MAP = aliases
    return aliases


def load_role_profiles(profiles_path: Path | str | None = None) -> dict[str, Any]:
    """Load curated role profiles YAML dictionary."""
    global _ROLE_PROFILES_MAP
    if _ROLE_PROFILES_MAP is not None and profiles_path is None:
        return _ROLE_PROFILES_MAP

    path = Path(profiles_path) if profiles_path is not None else ROLE_PROFILES_PATH
    if not path.exists():
        return {}

    with path.open("r", encoding="utf-8") as f:
        profiles = yaml.safe_load(f) or {}

    if profiles_path is None:
        _ROLE_PROFILES_MAP = profiles
    return profiles


def match_curated_profile(
    query: str,
    profiles_path: Path | str | None = None,
) -> dict[str, Any] | None:
    """Check if query matches a curated role profile alias (phrase match, case-insensitive)."""
    profiles = load_role_profiles(profiles_path=profiles_path)
    clean_q = query.strip().lower()

    for role_name, data in profiles.items():
        aliases = [a.lower() for a in data.get("aliases", [])]
        aliases.append(role_name.lower())
        for alias in aliases:
            # Match exact phrase or phrase boundary match
            pattern = r"^\s*" + re.escape(alias) + r"\s*$"
            if re.match(pattern, clean_q):
                return {
                    "role_name": role_name,
                    "human_reviewed": data.get("human_reviewed", False),
                    "socs": data.get("socs", []),
                }
    return None


def expand_query(query: str, aliases: dict[str, str] | None = None) -> str:
    """Expand query with matching aliases using phrase-level matching (longest match first)."""
    if aliases is None:
        aliases = load_aliases()

    if not aliases:
        return query

    lower_query = query.lower()
    expansions: list[str] = []

    # Sort aliases by length descending to match longer phrases before substrings
    sorted_aliases = sorted(aliases.items(), key=lambda x: len(x[0]), reverse=True)

    matched_spans: list[tuple[int, int]] = []

    for alias_key, expansion in sorted_aliases:
        pattern = r"\b" + re.escape(alias_key.lower()) + r"\b"
        for m in re.finditer(pattern, lower_query):
            start, end = m.span()
            # Avoid matching overlapping sub-phrases
            if any(s <= start and end <= e for s, e in matched_spans):
                continue
            matched_spans.append((start, end))
            # Only append unique terms not already present
            expansion_tokens = expansion.split()
            unique_tokens = [
                t for t in expansion_tokens
                if t.lower() not in lower_query.split() and t not in expansions
            ]
            if unique_tokens:
                expansions.append(" ".join(unique_tokens))

    if expansions:
        expanded = f"{query} {' '.join(expansions)}".strip()
        return expanded
    return query


def _load_documents_from_db(
    db_path: Path | str | None = None,
) -> list[dict[str, Any]]:
    """Build document representation for every occupation from DuckDB."""
    target_db = Path(db_path) if db_path is not None else config.ONET_DB
    con = duckdb.connect(str(target_db), read_only=True)

    try:
        occ_rows = con.execute(
            "SELECT soc, title, description FROM occupation ORDER BY soc"
        ).fetchall()

        total_occ = len(occ_rows)

        alt_rows = con.execute(
            """
            SELECT soc, title
            FROM (
                SELECT soc, title,
                       ROW_NUMBER() OVER (PARTITION BY soc ORDER BY title) as rn
                FROM alt_titles
            )
            WHERE rn <= 15
            ORDER BY soc, rn
            """
        ).fetchall()
        alt_map: dict[str, list[str]] = {}
        for soc, title in alt_rows:
            alt_map.setdefault(soc, []).append(title)

        task_rows = con.execute(
            """
            SELECT soc, task
            FROM (
                SELECT soc, task,
                       ROW_NUMBER() OVER (PARTITION BY soc ORDER BY task_id) as rn
                FROM tasks
            )
            WHERE rn <= 10
            ORDER BY soc, rn
            """
        ).fetchall()
        task_map: dict[str, list[str]] = {}
        for soc, task in task_rows:
            task_map.setdefault(soc, []).append(task)

        tech_rows = con.execute(
            """
            WITH tech_doc_freq AS (
                SELECT example, COUNT(DISTINCT soc) as df
                FROM technology
                GROUP BY example
            ),
            ranked_tech AS (
                SELECT t.soc, t.example,
                       ROW_NUMBER() OVER (
                           PARTITION BY t.soc
                           ORDER BY (CASE WHEN t.hot THEN 1.5 ELSE 1.0 END) * ln((? + 1.0) / (d.df + 1.0)) DESC,
                                    t.example ASC
                       ) as rn
                FROM technology t
                JOIN tech_doc_freq d ON t.example = d.example
            )
            SELECT soc, example
            FROM ranked_tech
            WHERE rn <= 10
            ORDER BY soc, rn
            """,
            [total_occ],
        ).fetchall()
        tech_map: dict[str, list[str]] = {}
        for soc, example in tech_rows:
            tech_map.setdefault(soc, []).append(example)

        docs: list[dict[str, Any]] = []
        for soc, title, description in occ_rows:
            alts = alt_map.get(soc, [])
            tasks = task_map.get(soc, [])
            techs = tech_map.get(soc, [])

            sections = [f"Occupation: {title}."]
            if description:
                sections.append(f"Description: {description}")
            if alts:
                sections.append(f"Alternate Titles: {', '.join(alts)}.")
            if tasks:
                sections.append(f"Core Tasks: {' '.join(tasks)}")
            if techs:
                sections.append(f"Key Technologies: {', '.join(techs)}.")

            full_text = "\n".join(sections)
            docs.append(
                {
                    "soc": soc,
                    "title": title,
                    "description": description or "",
                    "text": full_text,
                }
            )

        return docs
    finally:
        con.close()


def _ensure_bm25_cached(db_path: Path | str | None = None) -> tuple[BM25Okapi, list[dict[str, Any]]]:
    """Ensure the BM25 index and document cache are populated."""
    global _BM25_INDEX, _DOC_METADATAS, _SOC_TO_INDEX

    if _BM25_INDEX is not None and _DOC_METADATAS is not None:
        return _BM25_INDEX, _DOC_METADATAS

    docs = _load_documents_from_db(db_path=db_path)
    corpus_tokens = [_tokenize(d["text"]) for d in docs]
    bm25 = BM25Okapi(corpus_tokens)

    _BM25_INDEX = bm25
    _DOC_METADATAS = docs
    _SOC_TO_INDEX = {d["soc"]: i for i, d in enumerate(docs)}

    return _BM25_INDEX, _DOC_METADATAS


def build(
    db_path: Path | str | None = None,
    chroma_dir: Path | str | None = None,
) -> int:
    """Build dense vector index in ChromaDB and initialize BM25 cache.

    Idempotent: updates/replaces collection in ChromaDB and refreshes BM25.
    Returns the number of indexed occupations.
    """
    global _BM25_INDEX, _DOC_METADATAS, _SOC_TO_INDEX

    chroma_target = Path(chroma_dir) if chroma_dir is not None else CHROMA_PATH
    chroma_target.mkdir(parents=True, exist_ok=True)

    docs = _load_documents_from_db(db_path=db_path)
    num_docs = len(docs)

    corpus_tokens = [_tokenize(d["text"]) for d in docs]
    _BM25_INDEX = BM25Okapi(corpus_tokens)
    _DOC_METADATAS = docs
    _SOC_TO_INDEX = {d["soc"]: i for i, d in enumerate(docs)}

    client = chromadb.PersistentClient(path=str(chroma_target))
    try:
        client.delete_collection(COLLECTION_NAME)
    except Exception:
        pass

    collection = client.create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )

    model = get_embed_model()
    texts = [d["text"] for d in docs]
    ids = [d["soc"] for d in docs]
    metadatas = [{"title": d["title"], "soc": d["soc"]} for d in docs]

    batch_size = 64
    for i in range(0, num_docs, batch_size):
        batch_texts = texts[i : i + batch_size]
        batch_ids = ids[i : i + batch_size]
        batch_meta = metadatas[i : i + batch_size]

        embeddings = model.encode(
            batch_texts,
            normalize_embeddings=True,
            show_progress_bar=False,
            convert_to_numpy=True,
        ).tolist()

        collection.add(
            ids=batch_ids,
            embeddings=embeddings,
            metadatas=batch_meta,
            documents=batch_texts,
        )

    print(f"Indexed {num_docs} occupations into ChromaDB and BM25 index.")
    return num_docs


def margin(cands: list[dict[str, Any]]) -> float:
    """Compute the difference between top 1 and top 2 scores.

    Returns score[0] - score[1] if >= 2 cands, score[0] if 1 cand, or 0.0 if empty.
    """
    if not cands:
        return 0.0
    if len(cands) == 1:
        return float(cands[0].get("score", 0.0))
    return float(cands[0].get("score", 0.0) - cands[1].get("score", 0.0))


def confidence(
    query: str,
    chroma_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Compute confidence score for a query using dense top-1 cosine and cosine gap.

    Returns:
        {score: float, escalate: bool, top1_cos: float, cos_gap: float, dispersion: int}
    """
    # Exclude curated profiles from escalation
    if match_curated_profile(query):
        return {
            "score": 1.0,
            "escalate": False,
            "top1_cos": 1.0,
            "cos_gap": 1.0,
            "dispersion": 1,
            "curated": True,
        }

    chroma_target = Path(chroma_dir) if chroma_dir is not None else CHROMA_PATH
    client = chromadb.PersistentClient(path=str(chroma_target))
    collection = client.get_collection(COLLECTION_NAME)

    model = get_embed_model()
    dense_query_text = f"{BGE_QUERY_PREFIX}{query}"
    query_embedding = model.encode(
        dense_query_text,
        normalize_embeddings=True,
        show_progress_bar=False,
        convert_to_numpy=True,
    ).tolist()

    res = collection.query(
        query_embeddings=[query_embedding],
        n_results=5,
    )

    if not res or not res["distances"] or not res["distances"][0]:
        return {"score": 0.0, "escalate": True, "top1_cos": 0.0, "cos_gap": 0.0, "dispersion": 0}

    cosines = [1.0 - d for d in res["distances"][0]]
    socs = res["ids"][0]
    top1_cos = cosines[0]
    cos_gap = (cosines[0] - cosines[1]) if len(cosines) > 1 else 0.0
    dispersion = len(set(s[:5] for s in socs))

    # Signal: top1_cos * cos_gap.
    # On the tune split, ambiguous queries have top1*gap < 0.010.
    # Setting threshold at 0.012 achieves 100% recall on ambiguous tune queries (3/3).
    score = float(top1_cos * cos_gap)
    CONFIDENCE_THRESHOLD = 0.012
    escalate = bool(score < CONFIDENCE_THRESHOLD)

    return {
        "score": score,
        "escalate": escalate,
        "top1_cos": top1_cos,
        "cos_gap": cos_gap,
        "dispersion": dispersion,
    }


def blend_weights(
    cands: list[dict[str, Any]],
    temp: float = 0.04,
    margin_cutoff: float = 0.10,
    top: int = 3,
) -> list[float]:
    """Compute softmax weights over dense cosine similarities of top candidates.

    Filters out candidates whose dense cosine similarity is more than `margin_cutoff`
    below the top candidate, then applies temperature-scaled softmax.

    Args:
        cands: Candidates returned by search().
        temp: Softmax temperature parameter (default 0.04).
        margin_cutoff: Maximum allowable cosine distance from top candidate (default 0.10).
        top: Number of top candidates to consider (default 3).

    Returns:
        List of weights summing to 1.0.
    """
    if not cands or top <= 0:
        return []

    # If candidates come from a curated profile, return their pre-assigned alphas
    if cands[0].get("source") == "curated_profile" and "alpha" in cands[0]:
        subset = cands[:top]
        raw_alphas = [float(c.get("alpha", 1.0 / len(subset))) for c in subset]
        total = sum(raw_alphas)
        return [a / total for a in raw_alphas] if total > 0 else [1.0 / len(subset)] * len(subset)

    subset = cands[:top]
    # Cosine similarities: stored in 'dense_score' if available, else derive from score
    cos_scores = [
        float(c.get("dense_score", c.get("score", 0.0)))
        for c in subset
    ]

    top_cos = cos_scores[0]
    kept_scores = [c for c in cos_scores if (top_cos - c) <= margin_cutoff]

    if not kept_scores:
        return [1.0]

    scores_arr = np.array(kept_scores, dtype=np.float64)
    shifted = (scores_arr - top_cos) / temp
    exp_scores = np.exp(shifted)
    sum_exp = float(np.sum(exp_scores))

    if sum_exp == 0.0 or np.isnan(sum_exp):
        probs = [1.0 / len(kept_scores)] * len(kept_scores)
    else:
        probs = [float(e / sum_exp) for e in exp_scores]

    # Drop candidates with alpha < 0.10 and renormalize
    filtered_probs = [p for p in probs if p >= 0.10]
    if not filtered_probs:
        return [1.0]

    total_filtered = sum(filtered_probs)
    renormalized = [p / total_filtered for p in filtered_probs]

    # Ensure sum is strictly 1.0 within 1e-9
    total_norm = sum(renormalized)
    return [p / total_norm for p in renormalized]


def search(
    query: str,
    k: int = 8,
    use_alias: bool = True,
    method: str = "hybrid",
    chroma_dir: Path | str | None = None,
    db_path: Path | str | None = None,
) -> list[dict[str, Any]]:
    """Search occupations using curated profiles or dense/BM25/hybrid search.

    Checks curated role profiles first (source='curated_profile'). If not matched,
    performs dense vector and BM25 sparse search fused via RRF (source='retrieval').

    Args:
        query: User search query string.
        k: Number of candidates to return (default 8).
        use_alias: Whether to apply query alias expansion.
        method: 'hybrid', 'dense', or 'bm25'.
        chroma_dir: Optional path to Chroma directory.
        db_path: Optional path to DuckDB database.

    Returns:
        List of candidate dicts including source, soc, title, score, dense_score, ranks, query.
    """
    raw_query = query

    # 1. Check curated profiles first
    curated = match_curated_profile(query)
    if curated is not None:
        results = []
        for item in curated["socs"][:k]:
            results.append(
                {
                    "soc": item["soc"],
                    "title": item["title"],
                    "score": float(item["alpha"]),
                    "dense_score": 1.0,
                    "alpha": float(item["alpha"]),
                    "dense_rank": 1,
                    "bm25_rank": 1,
                    "source": "curated_profile",
                    "role_name": curated["role_name"],
                    "human_reviewed": curated["human_reviewed"],
                    "raw_query": raw_query,
                    "query": query,
                }
            )
        return results

    # 2. Expand query if requested
    search_query = expand_query(query) if use_alias else query

    # 3. Retrieve BM25 candidates
    bm25, doc_metas = _ensure_bm25_cached(db_path=db_path)
    tokens = _tokenize(search_query)

    bm25_top_30: list[tuple[str, int, float]] = []
    if method in ("bm25", "hybrid"):
        bm25_scores = bm25.get_scores(tokens)
        top_bm25_idx = np.argsort(bm25_scores)[::-1][:30]
        for rank, idx in enumerate(top_bm25_idx, start=1):
            soc = doc_metas[idx]["soc"]
            bm25_top_30.append((soc, rank, float(bm25_scores[idx])))

    # 4. Retrieve Dense candidates
    dense_top_30: list[tuple[str, int, float, float]] = []
    dense_scores_map: dict[str, float] = {}
    if method in ("dense", "hybrid"):
        chroma_target = Path(chroma_dir) if chroma_dir is not None else CHROMA_PATH
        client = chromadb.PersistentClient(path=str(chroma_target))
        collection = client.get_collection(COLLECTION_NAME)

        model = get_embed_model()
        dense_query_text = f"{BGE_QUERY_PREFIX}{search_query}"
        query_embedding = model.encode(
            dense_query_text,
            normalize_embeddings=True,
            show_progress_bar=False,
            convert_to_numpy=True,
        ).tolist()

        res = collection.query(
            query_embeddings=[query_embedding],
            n_results=30,
        )
        if res and res["ids"] and res["ids"][0]:
            for rank, (soc, dist) in enumerate(zip(res["ids"][0], res["distances"][0], strict=False), start=1):
                cosine_sim = 1.0 - dist
                dense_scores_map[soc] = float(cosine_sim)
                dense_top_30.append((soc, rank, float(cosine_sim), float(dist)))

    soc_meta_lookup = {d["soc"]: d for d in doc_metas}

    def _make_res(
        soc: str,
        score: float,
        d_rank: int | None,
        b_rank: int | None,
    ) -> dict[str, Any]:
        title = soc_meta_lookup[soc]["title"] if soc in soc_meta_lookup else ""
        return {
            "soc": soc,
            "title": title,
            "score": score,
            "dense_score": dense_scores_map.get(soc, 0.0),
            "dense_rank": d_rank,
            "bm25_rank": b_rank,
            "source": "retrieval",
            "raw_query": raw_query,
            "query": search_query,
        }

    if method == "dense":
        return [
            _make_res(soc, score, rank, None)
            for soc, rank, score, _ in dense_top_30[:k]
        ]

    if method == "bm25":
        return [
            _make_res(soc, score, None, rank)
            for soc, rank, score in bm25_top_30[:k]
        ]

    # Hybrid Reciprocal Rank Fusion (RRF k=60)
    RRF_K = 60.0
    rrf_scores: dict[str, float] = {}
    dense_ranks: dict[str, int] = {}
    bm25_ranks: dict[str, int] = {}

    for soc, rank, _, _ in dense_top_30:
        dense_ranks[soc] = rank
        rrf_scores[soc] = rrf_scores.get(soc, 0.0) + (1.0 / (RRF_K + rank))

    for soc, rank, _ in bm25_top_30:
        bm25_ranks[soc] = rank
        rrf_scores[soc] = rrf_scores.get(soc, 0.0) + (1.0 / (RRF_K + rank))

    sorted_cands = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)[:k]

    return [
        _make_res(
            soc=soc,
            score=score,
            d_rank=dense_ranks.get(soc),
            b_rank=bm25_ranks.get(soc),
        )
        for soc, score in sorted_cands
    ]
