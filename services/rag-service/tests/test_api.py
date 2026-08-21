"""End-to-end API tests. These exercise the real (small, CPU-only)
sentence-transformers model rather than mocking it — it's small enough
(~90MB, all-MiniLM-L6-v2) to keep the suite fast."""
from app import config
from app.embeddings import _get_model


def test_embedding_model_is_warmed_up_by_startup_not_by_the_first_request(client):
    """The `client` fixture already ran the real FastAPI lifespan (see
    conftest.py's `with TestClient(app) as test_client`) before this test
    body even runs -- if startup warm-up is working, the model is already
    cached here, with zero queries sent yet. This is the regression test
    for the real incident it fixes: a live /chat request timing out
    because rag-service's first-ever query had to load the model inline."""
    assert _get_model.cache_info().currsize == 1


def test_auth_disabled_by_default_in_dev(client):
    assert config.REQUIRE_AUTH is False
    assert client.get("/health").status_code == 200


def test_auth_enabled_rejects_missing_or_wrong_key(client):
    config.REQUIRE_AUTH = True
    config.API_KEY = "secret-123"

    resp = client.post("/query", json={"query": "hi"})
    assert resp.status_code == 401
    resp = client.post("/query", json={"query": "hi"}, headers={"X-API-Key": "wrong"})
    assert resp.status_code == 401


def test_auth_enabled_accepts_correct_key(client):
    config.REQUIRE_AUTH = True
    config.API_KEY = "secret-123"

    resp = client.post("/query", json={"query": "hi"}, headers={"X-API-Key": "secret-123"})
    assert resp.status_code == 200


def test_auth_enabled_still_exempts_health_and_metrics(client):
    config.REQUIRE_AUTH = True
    config.API_KEY = "secret-123"

    assert client.get("/health").status_code == 200
    assert client.get("/metrics").status_code == 200


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "chunks_in_store" in body


def test_metrics_endpoint_exposes_prometheus_text_format(client):
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "text/plain" in resp.headers["content-type"]
    assert "rag_query_total" in resp.text or "rag_ingest_total" in resp.text


def test_ingest_requires_text_or_url(client):
    resp = client.post("/ingest", json={"source": "test-fixture-no-content"})
    assert resp.status_code == 422


def test_ingest_rejects_both_text_and_url(client):
    resp = client.post(
        "/ingest",
        json={"text": "hello", "url": "https://example.com", "source": "test"},
    )
    assert resp.status_code == 422


def test_ingest_and_naive_semantic_query(client):
    ingest_resp = client.post(
        "/ingest",
        json={
            "doc_id": "test_doc_widgets",
            "text": (
                "CloudWidget Pro is our flagship product for managing inventory "
                "widgets across multiple warehouses. It supports barcode scanning, "
                "low-stock alerts, and automatic reorder points."
            ),
            "source": "test-fixture",
        },
    )
    assert ingest_resp.status_code == 200
    body = ingest_resp.json()
    assert body["doc_id"] == "test_doc_widgets"
    assert body["chunks_ingested"] >= 1

    query_resp = client.post(
        "/query",
        json={"query": "How do I manage warehouse inventory?", "k": 3, "mode": "naive"},
    )
    assert query_resp.status_code == 200
    qbody = query_resp.json()
    assert qbody["retrieval_path"] == "semantic"
    assert qbody["matched_keyword"] is None
    assert any(r["doc_id"] == "test_doc_widgets" for r in qbody["results"])
    # scores should be in a sane similarity range
    assert all(-1.0 <= r["score"] <= 1.0 for r in qbody["results"])


def test_hybrid_mode_diverges_from_naive_on_a_keyword_trigger(client):
    # Seed the real fictional-product KB so the triggered doc_id has content.
    from app.seeding import seed_if_missing

    seed_if_missing()

    query_text = "I have a question about billing and my last invoice"

    hybrid_resp = client.post("/query", json={"query": query_text, "mode": "hybrid"})
    assert hybrid_resp.status_code == 200
    hbody = hybrid_resp.json()
    assert hbody["retrieval_path"] == "keyword_trigger"
    assert hbody["routed_doc_id"] == "doc_billing"
    assert hbody["matched_keyword"] is not None
    assert len(hbody["results"]) > 0
    assert all(r["doc_id"] == "doc_billing" for r in hbody["results"])

    naive_resp = client.post("/query", json={"query": query_text, "mode": "naive"})
    assert naive_resp.status_code == 200
    nbody = naive_resp.json()
    # naive mode never checks keyword_triggers.yaml, so the path always
    # reads "semantic" even for a query that would trigger in hybrid mode.
    assert nbody["retrieval_path"] == "semantic"
    assert nbody["routed_doc_id"] is None


def test_query_k_is_respected(client):
    from app.seeding import seed_if_missing

    seed_if_missing()

    resp = client.post("/query", json={"query": "team roles and permissions", "k": 2, "mode": "naive"})
    assert resp.status_code == 200
    assert len(resp.json()["results"]) <= 2
