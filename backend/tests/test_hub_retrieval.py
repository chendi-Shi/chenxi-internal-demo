"""Retrieval regressions using fictional figures, not internal research excerpts."""

import json

import pytest

from research_agent import hub_semantic as semantic
from research_agent.hub import Hub
from research_agent.hub_models import (
    DocumentInput,
    IngestRequest,
    PolicyUpdate,
    RetrievalPolicy,
    SearchRequest,
    SourceDefinition,
)


@pytest.fixture
def corpus(workspace):
    directory = workspace / "retrieval"
    directory.mkdir()
    (directory / "query-aliases.local.json").write_text(
        json.dumps({"entities": [
            ["样例存储", "示例存储", "SampleStorage"],
            ["演示云", "样例云", "ExampleCloud"],
        ]}, ensure_ascii=False),
        encoding="utf-8",
    )
    hub = Hub(directory)
    for source, company, body in (
        (
            "a",
            "SampleStorage",
            "Synthetic fixture. Capital expenditures were JPY11.1 billion. Gross margin was 42%.",
        ),
        (
            "b",
            "ExampleCloud",
            "Synthetic fixture. Revenue grew 123% to $456 million. Data center capacity expanded. Semiconductor chip shipments increased.",
        ),
    ):
        hub.upsert_source(SourceDefinition(id=source, name=source))
        hub.ingest(
            IngestRequest(
                source_id=source,
                documents=[
                    DocumentInput(
                        external_id="one",
                        title=company,
                        body=body,
                        pages=[(1, 0, len(body))],
                        metadata={"company": company},
                        published_at="2026-08-01T00:00:00Z",
                    )
                ],
            )
        )
    return hub


@pytest.mark.parametrize(
    ("query", "company"),
    [
        ("样例存储的资本开支是多少？", "SampleStorage"),
        ("What is SampleStorage capital expenditure?", "SampleStorage"),
        ("请问毛利率是多少？", "SampleStorage"),
        ("演示云的收入增长情况如何？", "ExampleCloud"),
        ("What is ExampleCloud revenue growth?", "ExampleCloud"),
        ("数据中心", "ExampleCloud"),
        ("芯片", "ExampleCloud"),
        ("产能", "ExampleCloud"),
    ],
)
def test_bilingual_questions_return_original_evidence(corpus, query, company):
    result = corpus.search(SearchRequest(query=query))
    assert result["results"][0]["title"] == company
    item = result["results"][0]
    original = corpus.fetch(item["id"])["text"]
    assert item["snippet"] == original[item["citation"]["start"] : item["citation"]["end"]]
    assert item["citation"]["page"] == 1


def test_expansion_does_not_drop_unknown_constraints(corpus):
    assert corpus.search(SearchRequest(query="UnknownCompany 资本开支"))["total"] == 0
    assert corpus.search(SearchRequest(query="SampleStorage 毛利率", source_ids=["b"]))["total"] == 0
    assert corpus.search(SearchRequest(query='" OR * --'))["total"] == 0
    assert corpus.search(SearchRequest(query="请问是多少？"))["total"] == 0


def enable_vectors(hub):
    config = {
        "model": semantic.MODEL,
        "model_digest": "test-model",
        "dimensions": 2,
        "minimum_similarity": 0.5,
    }
    with hub.connection() as conn:
        for row in conn.execute("SELECT id FROM hub_chunks"):
            conn.execute(
                "INSERT INTO hub_vectors VALUES(?,?,?,?)",
                (row[0], "test-model", "fixture", semantic.pack((1.0, 0.0))),
            )
        conn.execute("INSERT INTO hub_semantic VALUES(1,?)", (json.dumps(config),))


def test_semantic_filters_policy_and_deletion(corpus, monkeypatch):
    enable_vectors(corpus)
    monkeypatch.setattr(semantic, "query_vector", lambda *_: (1.0, 0.0))
    query = SearchRequest(query="infrastructure expansion")
    assert corpus.search(query)["total"] == 2
    corpus.update_policy(
        PolicyUpdate(
            expected_version=1,
            policy=RetrievalPolicy(source_weights={"b": 8, "a": 0.1}, recency_boost=0),
        )
    )
    result = corpus.search(query)
    assert result["retrieval"]["mode"] == "hybrid"
    assert result["results"][0]["source_id"] == "b"
    assert result["policy_version"] == 2
    assert (
        corpus.search(SearchRequest(query=query.query, metadata={"company": "SampleStorage"}))["total"]
        == 1
    )
    assert (
        corpus.search(SearchRequest(query=query.query, until="2026-01-01T00:00:00Z"))["total"] == 0
    )
    assert corpus.search(SearchRequest(query="SampleStorage infrastructure"))["total"] == 1
    assert corpus.search(SearchRequest(query="UnknownCompany capex"))["total"] == 0
    doc_id = result["results"][0]["id"]
    corpus.upsert_source(SourceDefinition(id="b", name="b", enabled=False))
    assert corpus.search(query)["total"] == 1
    corpus.delete_document(doc_id)
    assert semantic.index_status(corpus)["indexed_chunks"] == 1


def test_update_invalidates_vector_and_outage_keeps_keyword_service(corpus, monkeypatch):
    enable_vectors(corpus)
    corpus.ingest(
        IngestRequest(
            source_id="a",
            documents=[
                DocumentInput(external_id="one", title="SampleStorage", body="Gross margin improved.")
            ],
        )
    )
    assert semantic.index_status(corpus)["pending_chunks"] == 1

    def unavailable(*_):
        raise semantic.SemanticUnavailable("local_embedding_model_unavailable")

    monkeypatch.setattr(semantic, "query_vector", unavailable)
    result = corpus.search(SearchRequest(query="毛利率"))
    assert result["total"] == 1
    assert result["retrieval"] == {
        "mode": "bilingual_fallback",
        "warning": "local_embedding_model_unavailable",
    }


def test_index_is_resumable_and_model_changes_are_detected(corpus, monkeypatch):
    monkeypatch.setattr(semantic, "model_digest", lambda: "first")
    calls = []

    def vectors(texts, **_):
        calls.extend(texts)
        return [(1.0, 0.0)] * len(texts)

    monkeypatch.setattr(semantic, "embed", vectors)
    first = semantic.build_index(corpus, batch_size=1)
    assert first["processed"] == 2 and first["pending_chunks"] == 0
    assert semantic.build_index(corpus)["processed"] == 0
    assert len(calls) == 2
    with corpus.connection() as conn:
        config = semantic.configuration(conn)
    monkeypatch.setattr(semantic, "model_digest", lambda: "second")
    with pytest.raises(semantic.SemanticUnavailable, match="model_changed"):
        semantic.query_vector(config, "question")


def test_invalid_embedding_is_rejected():
    for vector in ([0, 0], [float("nan"), 1], [float("inf")], [], ["secret"]):
        with pytest.raises(semantic.SemanticUnavailable):
            semantic.normalize(vector)


def test_filename_dates_are_explicit_and_unambiguous():
    from research_agent.hub_adapters import filename_date

    assert filename_date("Call_2026-08-12_English.pdf") == "2026-08-12T00:00:00+00:00"
    assert filename_date("Report_20_Aug_2026.pdf") == "2026-08-20T00:00:00+00:00"
    assert filename_date("Report_2026-99-40.pdf") == ""
    assert filename_date("Report_14_Aug_2026_15_Aug_2026.pdf") == ""
    assert filename_date("Report.pdf") == ""


def test_metadata_update_keeps_valid_embeddings(corpus):
    enable_vectors(corpus)
    with corpus.connection() as conn:
        row = conn.execute("SELECT * FROM hub_documents WHERE source_id='a'").fetchone()
    corpus.ingest(
        IngestRequest(
            source_id="a",
            documents=[
                DocumentInput(
                    external_id=row["external_id"],
                    title=row["title"],
                    body=row["body"],
                    pages=json.loads(row["pages_json"]),
                    published_at="2026-09-01T00:00:00Z",
                    metadata={"updated": "yes"},
                )
            ],
        )
    )
    assert semantic.index_status(corpus)["pending_chunks"] == 0
    with corpus.connection() as conn:
        assert conn.execute("SELECT count(*) FROM hub_vectors").fetchone()[0] == 2


def test_e5_windowing_preserves_long_text_tail():
    np = pytest.importorskip("numpy")
    from types import SimpleNamespace

    from research_agent.hub_e5 import Runtime

    original = list(range(100, 1700))

    class Tokenizer:
        def encode(self, text, **_):
            return SimpleNamespace(ids=[3] if text == "passage: " else original)

    class Session:
        def __init__(self):
            self.seen = set()

        def run(self, _, inputs):
            assert inputs["input_ids"].shape[1] <= 512
            assert (inputs["attention_mask"][:, 0] == 1).all()
            self.seen.update(inputs["input_ids"].flatten().tolist())
            return [np.ones((*inputs["input_ids"].shape, 384), dtype=np.float32)]

    model = Runtime.__new__(Runtime)
    model.tokenizer = Tokenizer()
    model.session = Session()
    model.inputs = {"input_ids", "attention_mask"}
    model.bos, model.eos, model.pad = 0, 2, 1
    vector = model.encode(["long text"])[0]
    assert set(original) <= model.session.seen
    assert sum(value * value for value in vector) == pytest.approx(1.0)


def test_e5_missing_model_falls_back(corpus):
    with corpus.connection() as conn:
        conn.execute(
            "INSERT INTO hub_semantic VALUES(1,?)",
            (
                json.dumps(
                    {
                        "engine": "e5",
                        "model_digest": "missing",
                        "model_directory": "missing",
                    }
                ),
            ),
        )
    result = corpus.search(SearchRequest(query="毛利率"))
    assert result["total"] == 1
    assert result["retrieval"]["mode"] == "bilingual_fallback"
