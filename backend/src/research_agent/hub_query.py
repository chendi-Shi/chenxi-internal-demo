"""Bounded bilingual query planning for short keywords and natural-language questions."""

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
    ("芯片", "半导体", "chip", "chips", "semiconductor", "semiconductors"),
    ("产能", "capacity", "production capacity"),
    ("供应", "供给", "supply", "supplies", "supplied", "supply chain"),
    ("需求", "demand", "demands"),
    ("客户", "customer", "customers", "client", "clients"),
    ("GPU", "图形处理器", "gpu", "gpus", "accelerator", "accelerators"),
    ("扩张", "扩建", "扩大", "expansion", "expand", "expanded", "expanding"),
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
    "please tell me about show explain its their has have had s can could would will "
    "should based discuss describe summarize provide give use regarding currently or and not "
    "relevant information overview which who when whose where why find any".split()
)
ANCHOR_CONCEPTS = (
    ("HBM", "high bandwidth memory", "高带宽内存"),
    ("DRAM", "dynamic random access memory", "动态随机存取存储器"),
    ("NAND", "NAND flash", "闪存"),
)
TOPIC_ACRONYMS = frozenset(
    "ai hbm hbm3e hbm4 dram nand ssd hdd cpu asic llm npu tpu ddr5 gddr pcie cxl euv".split()
)
CN_FILLERS = re.compile(
    r"请问|请介绍|请说明|请帮我|告诉我|帮我查一下|帮我查|查一下|查找|查询|分析一下|分析|总结|解释|说明|介绍|描述|结合|根据|基于|关于|相关|具体|目前|当前|最新|情况如何|情况|怎么样|如何|怎样|有哪些|什么|多少|是否|能否|能不能|可以|以及|并且|或者|与|和|的|了|吗|呢|是"
)
CN_STOP_WORDS = frozenset(
    "请问 请你 帮我 告诉 我们 他们 目前 当前 最新 具体 相关 分析 总结 解释 说明 介绍 描述 "
    "结合 根据 基于 关于 情况 如何 怎样 什么 多少 是否 能否 可以 以及 并且 或者 这个 那个 "
    "这些 那些 其中 对于 以及 请帮".split()
)
MAX_LITERAL_TERMS = 48


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
    anchors: tuple[tuple[str, ...], ...] = ()

    def accepts_title(self, title: str) -> bool:
        tokens = set(search_tokens(title).split())
        return all(
            any(set(search_tokens(alias).split()) <= tokens for alias in group)
            for group in self.entities
        ) and all(date in title for date in self.dates)

    def accepts_passage(self, title: str, text: str) -> bool:
        tokens = set(search_tokens(title + " " + text).split())
        return all(
            any(set(search_tokens(alias).split()) <= tokens for alias in group)
            for group in self.anchors
        )


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
    entity_groups = []
    concept_groups = []
    anchor_groups = []
    entities = []
    anchors = []
    # Longest aliases first avoids interpreting 毛利率 as 毛利 or 营业收入 as 收入.
    candidates = sorted(
        [
            (alias, group, entity)
            for entity, collection in (
                (True, entity_aliases), (False, ANCHOR_CONCEPTS), (False, CONCEPTS)
            )
            for group in collection
            for alias in group
        ],
        key=lambda item: -len(item[0]),
    )
    seen = set()
    for alias, group, entity in candidates:
        pattern = re.escape(alias.casefold())
        if alias.isascii():
            pattern = r"(?<![a-z0-9])" + pattern + r"(?![a-z0-9])"
        if not re.search(pattern, remaining):
            continue
        remaining = re.sub(pattern, " ", remaining)
        if group not in seen:
            if group in ANCHOR_CONCEPTS:
                anchor_groups.append(alternatives(group))
                anchors.append(group)
            else:
                (entity_groups if entity else concept_groups).append(alternatives(group))
            seen.add(group)
            if entity:
                entities.append(group)

    # Dates and named entities are hard constraints. Other content words are useful
    # retrieval clues, but requiring every word in a paragraph to occur in one chunk
    # makes normal questions fail on wording differences between question and source.
    for date in dates:
        remaining = remaining.replace(date, " ")
    ignored_capitalized = STOP_WORDS | {
        "i", "we", "you", "they", "he", "she", "it", "this", "that", "these", "those",
        "first", "second", "please", "based", "discuss", "describe", "summarize",
        "provide", "explain", "analyze", "analysis", "what", "how", "why", "where",
    }
    known_aliases = {
        alias.casefold()
        for group in (*entity_aliases, *(group for _, group, _ in candidates))
        for alias in group
    }
    for word in dict.fromkeys(re.findall(r"\b[A-Z][A-Za-z0-9]+\b", query)):
        normalized = word.casefold()
        if normalized in ignored_capitalized or normalized in known_aliases or normalized in TOPIC_ACRONYMS:
            continue
        if any(normalized in {alias.casefold() for alias in group} for group in entities):
            continue
        entity_groups.append("(" + literal(word) + ")")
        entities.append((word,))
        remaining = re.sub(r"(?<![a-z0-9])" + re.escape(normalized) + r"(?![a-z0-9])", " ", remaining)

    remaining = CN_FILLERS.sub(" ", remaining)
    terms = []
    for term in re.findall(r"[a-z0-9]+", remaining):
        if term not in STOP_WORDS and len(term) > 1:
            terms.append(term)
    for run in re.findall(r"[\u3400-\u9fff]+", remaining):
        if len(run) == 1:
            terms.append(run)
        else:
            terms.extend(
                run[index : index + 2]
                for index in range(len(run) - 1)
                if run[index : index + 2] not in CN_STOP_WORDS
            )
    term_groups = [
        "(" + literal(term) + ")" for term in list(dict.fromkeys(terms))[:MAX_LITERAL_TERMS]
    ]
    groups = list(dict.fromkeys(entity_groups + concept_groups + term_groups))
    operator = " OR " if len(groups) > 4 else " AND "
    parts = [*anchor_groups]
    if groups:
        parts.append("(" + operator.join(groups) + ")")
    return QueryPlan(" AND ".join(parts), tuple(entities), dates, tuple(anchors))
