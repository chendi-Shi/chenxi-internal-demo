"""Repeatable local acceptance against a running HTTP + MCP server.

Labels are user-owned JSON, not bundled internal documents. No model response is simulated.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from research_agent.hub_runtime import load_credentials


async def verify(args):
    credentials = load_credentials(args.credentials)
    labels = json.loads(args.cases.read_text(encoding="utf-8"))
    results = []
    documents = {}
    async with httpx.AsyncClient(
        base_url=args.url,
        timeout=60,
        trust_env=False,
        headers={"Authorization": f"Bearer {credentials.read_token}"},
    ) as web:
        status = await web.get("/api/retrieval")
        status.raise_for_status()
        async with streamable_http_client(args.url + "/mcp", http_client=web) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tool_names = {tool.name for tool in (await session.list_tools()).tools}
                assert {"search", "fetch", "search_documents"} <= tool_names
                for label in labels:
                    request = {"query": label["query"], "source_ids": [args.source], "limit": 5}
                    start = time.monotonic()
                    response = await web.post("/api/search", json=request)
                    response.raise_for_status()
                    http_result = response.json()
                    mcp_result = await session.call_tool("search_documents", {"request": request})
                    assert not mcp_result.isError
                    result = mcp_result.structuredContent
                    same = [r["id"] for r in http_result["results"]] == [
                        r["id"] for r in result["results"]
                    ] and http_result["policy_version"] == result["policy_version"]
                    citations_ok = True
                    expected_rank = None
                    snippet_has_answer = False
                    fetched_page_has_answer = False
                    for rank, hit in enumerate(result["results"], 1):
                        if hit["id"] not in documents:
                            fetched = await session.call_tool("fetch", {"id": hit["id"]})
                            assert not fetched.isError
                            documents[hit["id"]] = fetched.structuredContent
                        doc = documents[hit["id"]]
                        cite = hit["citation"]
                        citations_ok &= hit["snippet"] == doc["text"][cite["start"] : cite["end"]]
                        citations_ok &= any(
                            page == cite["page"] and a <= cite["start"] < cite["end"] <= b
                            for page, a, b in doc["metadata"]["pages"]
                        )
                        if hit["title"].startswith(label["title_prefix"]):
                            expected_rank = rank
                            snippet_has_answer = cite["page"] == label["page"] and all(
                                needle in hit["snippet"] for needle in label["contains"]
                            )
                            fetched_page_has_answer = any(
                                page == label["page"]
                                and all(needle in doc["text"][a:b] for needle in label["contains"])
                                for page, a, b in doc["metadata"]["pages"]
                            )
                    row = {
                        "id": label["id"],
                        "query": label["query"],
                        "document_rank": expected_rank,
                        "citations_valid": bool(citations_ok),
                        "http_mcp_agree": same,
                        "snippet_has_answer": snippet_has_answer,
                        "fetched_page_has_answer": fetched_page_has_answer,
                        "retrieval": result["retrieval"],
                        "elapsed_seconds": round(time.monotonic() - start, 3),
                    }
                    results.append(row)
                    print(
                        label["id"],
                        "doc_rank=",
                        expected_rank,
                        "snippet=",
                        snippet_has_answer,
                        flush=True,
                    )
    report = {
        "scope": "Local retrieval + real HTTP/MCP SDK calls, not a ChatGPT answer evaluation",
        "index": status.json(),
        "queries": len(results),
        "top1_documents": sum(r["document_rank"] == 1 for r in results),
        "top5_documents": sum(r["document_rank"] is not None for r in results),
        "valid_citations": sum(r["citations_valid"] for r in results),
        "http_mcp_agree": sum(r["http_mcp_agree"] for r in results),
        "snippets_with_answer": sum(r["snippet_has_answer"] for r in results),
        "fetched_pages_with_answer": sum(r["fetched_page_has_answer"] for r in results),
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {k: v for k, v in report.items() if k != "results"}, ensure_ascii=False, indent=2
        )
    )
    return (
        0
        if all(
            r["document_rank"] is not None
            and r["citations_valid"]
            and r["http_mcp_agree"]
            and r["fetched_page_has_answer"]
            for r in results
        )
        else 1
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8765")
    parser.add_argument("--credentials", required=True, type=Path)
    parser.add_argument("--cases", required=True, type=Path)
    parser.add_argument("--source", default="internal_pdfs")
    parser.add_argument("--output", required=True, type=Path)
    return asyncio.run(verify(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
