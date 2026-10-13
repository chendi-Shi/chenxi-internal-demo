# Synthetic PostgreSQL retrieval fixture

Everything in `suite.json` is invented. Company names, dates, numbers, claims and URLs are synthetic; they are not financial advice or real-library evidence. This fixture exercises multilingual paraphrases, shared terminology, competing passages, source/date/metadata filters, disabled sources, and deliberately empty filters. It is a reproducible integration diagnostic, not a measured estimate of quality on the real PDF library.

`build_snapshot.py` creates the minimal read-only SQLite interchange snapshot consumed by `research_agent.pg_lab import-snapshot` and writes the same locked queries and qrels. Supply a complete, locally downloaded E5 model directory to embed **all** fixture passages. It never invents vectors or silently downloads a missing model.

```powershell
# From backend; use a dedicated local output directory and a separate PostgreSQL database.
python samples/postgres_synthetic/build_snapshot.py --output-dir "<local-fixture-output>" --model-directory "<local-E5-model>"
# CHENXI_DATABASE_URL must point to the synthetic experiment database, not a real library.
python -m research_agent.pg_lab import-snapshot "<local-fixture-output>/hub.sqlite3"
python -m research_agent.pg_lab evaluate "<local-fixture-output>/queries.json" --stage lexical --output "<local-fixture-output>/B0.json" --warmup 1 --repeats 3
```

Run each stage with the same snapshot, limits and filtering. Keep the automatic rewrite provider fixed before inspecting results. Handwritten test queries are not a substitute for learned query rewriting. No-answer cases in this fixture use impossible filters: they test retrieval filtering and empty result handling, not the model's ability to abstain when unanswerable text is present. Dense top-k normally returns candidates even for unrelated questions; empty qrels alone must not be called semantic no-answer detection.

The evaluation metric is document-level; multiple identical bodies are not added to inflate recall. A positive qrel is directly supported by the fabricated passage. Graded qrels identify partial context independently of the search result. Unjudged documents count as zero in this controlled corpus. Model loading is outside the steady-state query timer; first-query cost and warm repetitions must be reported separately.
