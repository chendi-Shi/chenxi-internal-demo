"""Bounded bilingual query expansion; document text never supplies query instructions."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .store import search_tokens

# Synonyms describe concepts, never answers. Unknown terms remain literal constraints.
CONCEPTS = (
    ("核心自由现金流", "core free cash flow"),
    (
        "资本开支",
        "资本支出",
        "capex",
        "capital expenditure",
        "capital expenditures",
        "capital spending",
    ),
    ("毛利率", "gross margin", "gross margins"),
    ("毛利润", "毛利", "gross profit"),
    ("收入", "营收", "营业收入", "revenue", "revenues", "sales"),
    ("增长", "growth", "grew", "increase", "increased", "increases", "growing"),
    ("数据中心", "data center", "data centers", "datacenter", "datacenters", "data centre"),
    ("产能", "capacity", "production capacity"),
    ("自由现金流", "free cash flow", "fcf"),
    ("经营现金流", "operating cash flow", "cash from operations"),
    ("营业利润", "经营利润", "operating profit", "operating income"),
    ("净利润", "net income", "net profit", "net earnings"),
    ("每股收益", "earnings per share", "eps"),
    ("折旧", "depreciation"),
    ("库存", "存货", "inventory", "inventories"),
    ("订单", "orders", "order", "bookings"),
    ("指引", "guidance", "outlook"),
    ("同比", "year over year", "yoy"),
    ("环比", "quarter over quarter", "qoq"),
)
STOP_WORDS = frozenset(
    "what is are was were the a an of for in on at to how much many did does do "
    "please tell me about show explain its their has have had s".split()
)
CN_FILLERS = re.compile(
    r"请问|请介绍|请说明|告诉我|帮我查一下|帮我查|是多少|多少|情况如何|情况|怎么样|如何|有哪些|什么|的|了|吗|呢"
)


def literal(text: str) -> str:
    tokens = list(dict.fromkeys(search_tokens(text).split()))
    return " AND ".join('"' + token + '"' for token in tokens)


def alternatives(aliases: tuple[str, ...]) -> str:
    return "(" + " OR ".join("(" + literal(alias) + ")" for alias in aliases) + ")"


@dataclass(frozen=True)
class QueryPlan:
    expression: str
    entities: tuple[tuple[str, ...], ...]
    dates: tuple[str, ...] = ()

    def accepts_title(self, title: str) -> bool:
        tokens = set(search_tokens(title).split())
        return all(
            any(set(search_tokens(alias).split()) <= tokens for alias in group)
            for group in self.entities
        ) and all(date in title for date in self.dates)


def load_entity_aliases(path: Path) -> tuple[tuple[str, ...], ...]:
    """Load optional workspace-specific company aliases without shipping them in source."""
    if not path.exists():
        return ()
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(payload, dict) or set(payload) != {"entities"}:
            raise ValueError
        groups = payload["entities"]
        if not isinstance(groups, list) or len(groups) > 100:
            raise ValueError
        result, seen = [], set()
        for group in groups:
            if (
                not isinstance(group, list)
                or not 2 <= len(group) <= 12
                or any(not isinstance(alias, str) or not 2 <= len(alias.strip()) <= 80 for alias in group)
            ):
                raise ValueError
            normalized = tuple(dict.fromkeys(alias.strip() for alias in group))
            key = tuple(sorted(alias.casefold() for alias in normalized))
            if len(normalized) < 2 or key in seen:
                raise ValueError
            seen.add(key)
            result.append(normalized)
        return tuple(result)
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("invalid_query_alias_config") from exc


def plan_query(query: str, entity_aliases: tuple[tuple[str, ...], ...] = ()) -> QueryPlan:
    remaining = query.casefold()
    remaining = re.sub(
        r"(20\d{2})年(\d{1,2})月(\d{1,2})日",
        lambda m: f"{m[1]}-{int(m[2]):02d}-{int(m[3]):02d}",
        remaining,
    )
    dates = tuple(re.findall(r"20\d{2}-\d{2}-\d{2}", remaining))
    groups = []
    entities = []
    # Longest aliases first avoids interpreting 毛利率 as 毛利 or 营业收入 as 收入.
    candidates = sorted(
        [
            (alias, group, entity)
            for entity, collection in ((True, entity_aliases), (False, CONCEPTS))
            for group in collection
            for alias in group
        ],
        key=lambda item: -len(item[0]),
    )
    seen = set()
    for alias, group, entity in candidates:
        pattern = re.escape(alias)
        if alias.isascii():
            pattern = r"(?<![a-z0-9])" + pattern + r"(?![a-z0-9])"
        if not re.search(pattern, remaining):
            continue
        remaining = re.sub(pattern, " ", remaining)
        if group not in seen:
            groups.append(alternatives(group))
            seen.add(group)
            if entity:
                entities.append(group)
    # Strip conversational padding only; unknown Chinese runs keep their AND semantics.
    remaining = CN_FILLERS.sub(" ", remaining)
    terms = [
        term
        for term in re.findall(r"[a-z0-9]+|[\u3400-\u9fff]+", remaining)
        if term not in STOP_WORDS
    ]
    # Preserve explicitly named, unfamiliar companies as semantic constraints as well.
    # Do not turn a failed "UnknownCompany capex" lookup into another company's result.
    capitalized = {word.casefold() for word in re.findall(r"\b[A-Z][A-Za-z0-9]+\b", query)}
    for term in terms:
        if term in capitalized and term.isascii():
            entities.append((term,))
    groups.extend("(" + literal(term) + ")" for term in dict.fromkeys(terms))
    return QueryPlan(" AND ".join(groups), tuple(entities), dates)
