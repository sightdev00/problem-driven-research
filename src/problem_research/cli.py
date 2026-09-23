#!/usr/bin/env python3
"""Small, resumable first research cycle; Python standard library only."""

from __future__ import annotations

import datetime as dt
import hashlib
import html
from html.parser import HTMLParser
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import socket
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
RESEARCH = ROOT / "research"
SOURCE_REGISTRY = ROOT / "structure" / "sources.json"
MAX_SOURCE_BYTES = 350_000
PAPERS_PER_QUERY = 10
MAX_DISCOVERY_QUERIES = 3
MAX_WEB_EXPANSIONS = 5
MAX_ANALYSIS_SOURCES = 24
MAX_ANALYSIS_SOURCE_CHARS = 1_400
ANALYSIS_CHUNK_SIZE = 5
MODEL_TIMEOUT_SECONDS = 180

DOMAIN_PROTOCOLS = {
    "visual-intelligence": {
        "version": "draft-1",
        "checks": "成像、预处理、数据、标注、模型、后处理和系统决策",
    },
    "physics": {
        "version": "draft-1",
        "checks": "理论定义与适用范围、基本假设与对称性、数学推导或计算方法、近似与极限、可观测量或可检验后果、已有证据与反例、未解问题",
    },
}


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def save_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def load_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"研究记录 JSON 无效：{path}（{exc}）") from exc
    if not isinstance(value, dict):
        raise ValueError(f"研究记录 JSON 顶层应为对象：{path}")
    return value


def source_registry() -> dict:
    try:
        data = json.loads(SOURCE_REGISTRY.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"全局来源注册表无效：{SOURCE_REGISTRY}（{exc}）") from exc
    sources = data.get("sources") if isinstance(data, dict) else None
    if not isinstance(sources, list) or not all(isinstance(item, dict) and item.get("id") for item in sources):
        raise RuntimeError(f"全局来源注册表缺少 sources 列表：{SOURCE_REGISTRY}")
    return data


def enabled_source_ids(domain: str, registry: dict) -> set[str]:
    return {str(item["id"]) for item in registry["sources"]
            if domain in item.get("domains", []) and item.get("enabled", True)}


def registered_web_urls(registry: dict, source_ids: set[str]) -> list[tuple[str, str]]:
    output = []
    for item in registry["sources"]:
        if item["id"] not in source_ids:
            continue
        for url in item.get("urls", []):
            if isinstance(url, str) and url:
                output.append((str(item["id"]), url))
    return output


def append_jsonl(path: Path, row: dict) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def read_prompt(label: str) -> str:
    """Decode terminal input before Python's locale-bound text wrapper does."""
    sys.stdout.write(label)
    sys.stdout.flush()
    raw = sys.stdin.buffer.readline()
    if not raw:
        raise EOFError
    try:
        return raw.decode("utf-8").strip()
    except UnicodeDecodeError:
        try:
            return raw.decode("gb18030").strip()
        except UnicodeDecodeError as exc:
            raise ValueError("无法识别终端输入编码；请将终端设置为 UTF-8。") from exc


def ask(label: str, *, required: bool = True) -> str:
    while True:
        value = read_prompt(label)
        if value or not required:
            return value
        print("此项不能为空。")


def approved(label: str) -> bool:
    return read_prompt(label + " [y/N] ").lower() == "y"


def http_json(url: str, payload: dict | None = None, headers: dict | None = None, timeout: int = 55) -> dict:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=data, headers=headers or {}, method="POST" if data else "GET")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        status = getattr(response, "status", response.getcode())
        raw = response.read(MAX_SOURCE_BYTES * 2).decode("utf-8", errors="replace")
    try:
        result = json.loads(raw)
    except json.JSONDecodeError as exc:
        preview = raw.strip().replace("\n", " ")[:240]
        detail = "响应为空" if not preview else "响应开头：" + repr(preview)
        raise ValueError(f"接口未返回合法 JSON（HTTP {status}；{detail}）。请检查 QWEN_BASE_URL 是否指向 OpenAI 兼容 API，而非模型服务首页。") from exc
    if not isinstance(result, dict):
        raise ValueError(f"接口返回的 JSON 顶层应为对象，实际为 {type(result).__name__}。")
    return result


def http_text(url: str, headers: dict | None = None) -> str:
    request = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(request, timeout=55) as response:
        return response.read(MAX_SOURCE_BYTES * 2).decode("utf-8", errors="replace")


def completion_text(response: dict) -> str:
    """Extract the final assistant text from the OpenAI chat-completions shape."""
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise ValueError("接口响应缺少 choices[0]；请确认服务实现了 /v1/chat/completions。")
    message = choices[0].get("message")
    if not isinstance(message, dict):
        raise ValueError("接口响应缺少 choices[0].message。")
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    # Some OpenAI-compatible servers return multipart content.
    if isinstance(content, list):
        text = "".join(part.get("text", "") for part in content if isinstance(part, dict))
        return text.strip()
    if content is None:
        finish_reason = choices[0].get("finish_reason")
        has_reasoning = bool(message.get("reasoning_content") or message.get("reasoning"))
        hint = "；服务只返回了推理内容，需关闭模型的 thinking/reasoning 模式" if has_reasoning else ""
        raise ValueError("模型没有返回最终文本内容" + hint + f"（finish_reason={finish_reason!r}）。")
    raise ValueError("模型返回的 content 不是文本。")


def completion_json(content: str) -> dict:
    """Read a JSON object even when a reasoning model prepends visible deliberation."""
    content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content).strip()
    try:
        result = json.loads(content)
    except json.JSONDecodeError:
        # Some Qwen-compatible services put visible reasoning and the final answer
        # in the same content field.  The final complete object is the only part
        # usable by this workflow; prose is deliberately discarded.
        decoder = json.JSONDecoder()
        objects: list[tuple[int, dict]] = []
        for match in re.finditer(r"\{", content):
            try:
                value, end = decoder.raw_decode(content[match.start():])
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                objects.append((match.start() + end, value))
        if objects:
            # An outer object ends after any nested object.  The candidate ending
            # latest is consequently the final complete response object.
            return max(objects, key=lambda item: item[0])[1]
        preview = content.replace("\n", " ")[:240]
        raise ValueError("模型最终文本不含合法 JSON 对象（开头：" + repr(preview) + "）。请让服务保留 JSON 输出。") from None
    if not isinstance(result, dict):
        raise ValueError("模型返回值应是JSON对象。")
    return result


def model(prompt: str) -> dict:
    base = os.environ.get("QWEN_BASE_URL", "").strip().rstrip("/")
    name = os.environ.get("QWEN_MODEL", "").strip()
    if not base or not name:
        raise RuntimeError("请先设置 QWEN_BASE_URL 和 QWEN_MODEL；详见 README.md。")
    if not base.startswith(("http://", "https://")):
        raise RuntimeError("QWEN_BASE_URL 必须是 http(s) 服务地址。")
    endpoint = base + ("/chat/completions" if base.endswith("/v1") else "/v1/chat/completions")
    headers = {"Content-Type": "application/json"}
    if os.environ.get("QWEN_API_KEY"):
        headers["Authorization"] = "Bearer " + os.environ["QWEN_API_KEY"]
    response = http_json(endpoint, {
        "model": name,
        "temperature": 0.2,
        "messages": [
            {"role": "system", "content": "你是视觉智能问题研究助手。只输出一个合法JSON对象，不要Markdown。区分原文事实与推断，不伪造证据。"},
            {"role": "user", "content": prompt},
        ],
    }, headers, timeout=MODEL_TIMEOUT_SECONDS)
    content = completion_text(response)
    if not content:
        raise ValueError("模型返回了空文本；请检查模型是否完成生成，或关闭其 thinking/reasoning 模式后重试。")
    return completion_json(content)


class PlainText(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in {"script", "style", "nav", "footer"}:
            self.skip += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "nav", "footer"} and self.skip:
            self.skip -= 1

    def handle_data(self, data: str) -> None:
        if not self.skip and data.strip():
            self.parts.append(data.strip())


def public_url(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    try:
        ip = ipaddress.ip_address(parsed.hostname)
        return ip.is_global
    except ValueError:
        return parsed.hostname.lower() not in {"localhost", "localhost.localdomain"}


def fetch_page(url: str) -> str:
    if not public_url(url):
        raise ValueError("只接受公开的 HTTP(S) 网址；本地文件和内网地址请走人工输入。")
    request = urllib.request.Request(url, headers={"User-Agent": "ProblemResearchDraft/0.1 (research reading)"})
    with urllib.request.urlopen(request, timeout=20) as response:
        final = response.geturl()
        if not public_url(final):
            raise ValueError("跳转到了非公开地址。")
        content_type = response.headers.get_content_type()
        if content_type not in {"text/html", "text/plain"}:
            raise ValueError("目前仅自动读取 HTML 或纯文本，请手动提供 PDF 的公开摘要。")
        encoding = response.headers.get_content_charset() or "utf-8"
        raw = response.read(MAX_SOURCE_BYTES).decode(encoding, errors="replace")
    if content_type == "text/plain":
        return raw[:12000]
    parser = PlainText()
    parser.feed(raw)
    return html.unescape(" ".join(parser.parts))[:12000]


def openalex_search(query: str, limit: int = PAPERS_PER_QUERY) -> list[dict]:
    endpoint = "https://api.openalex.org/works?" + urllib.parse.urlencode({
        "search": query, "per_page": str(limit), "select": "id,title,doi,publication_year,authorships,abstract_inverted_index,primary_location"
    })
    result = http_json(endpoint, headers={"User-Agent": "ProblemResearchDraft/0.1"})
    output = []
    for work in result.get("results", []):
        inverted = work.get("abstract_inverted_index") or {}
        words = [""] * (max((max(pos) for pos in inverted.values() if pos), default=-1) + 1)
        for word, positions in inverted.items():
            for position in positions:
                if 0 <= position < len(words):
                    words[position] = word
        output.append({
            "id": "P" + str(len(output) + 1), "kind": "paper_abstract", "title": work.get("title") or "Untitled",
            "url": ((work.get("primary_location") or {}).get("landing_page_url")
                    or work.get("doi") or work.get("id") or ""), "year": work.get("publication_year"),
            "organization": sorted({(item.get("display_name") or "") for author in work.get("authorships", []) for item in author.get("institutions", []) if item.get("display_name")})[:8],
            "text": " ".join(words)[:5000],
            "note": "仅使用索引摘要，未阅读原论文全文",
        })
    return output


def crossref_search(query: str, limit: int = PAPERS_PER_QUERY) -> list[dict]:
    """Fallback metadata/abstract search when OpenAlex is temporarily rate limited."""
    endpoint = "https://api.crossref.org/works?" + urllib.parse.urlencode({
        "query.bibliographic": query, "rows": str(limit), "select": "DOI,title,published,author,abstract,URL"
    })
    result = http_json(endpoint, headers={"User-Agent": "ProblemResearchDraft/0.1 (research reading)"})
    output = []
    for work in result.get("message", {}).get("items", []):
        abstract = html.unescape(re.sub(r"<[^>]+>", " ", work.get("abstract") or "")).strip()
        authors = work.get("author") or []
        names = [" ".join(filter(None, [author.get("given"), author.get("family")])) for author in authors]
        published = work.get("published", {}).get("date-parts", [[None]])[0][0]
        output.append({
            "id": "C" + str(len(output) + 1), "kind": "paper_abstract" if abstract else "paper_metadata",
            "title": (work.get("title") or ["Untitled"])[0], "url": "https://doi.org/" + work["DOI"] if work.get("DOI") else work.get("URL", ""),
            "year": published, "organization": names[:8], "text": abstract[:5000],
            "note": "仅使用 Crossref 登记摘要，未阅读原论文全文" if abstract else "仅有书目信息，不能作为论据",
        })
    return output


def arxiv_search(query: str, limit: int = PAPERS_PER_QUERY) -> list[dict]:
    """Retrieve a bounded set of physics preprint abstracts from arXiv's Atom API."""
    endpoint = "https://export.arxiv.org/api/query?" + urllib.parse.urlencode({
        "search_query": "all:" + query, "start": "0", "max_results": str(limit),
        "sortBy": "relevance", "sortOrder": "descending",
    })
    try:
        root = ET.fromstring(http_text(endpoint, {
            "User-Agent": "Mozilla/5.0 (compatible; ProblemResearchDraft/0.1; research reading)",
            "Accept": "application/atom+xml, application/xml;q=0.9, */*;q=0.1",
        }))
    except ET.ParseError as exc:
        raise ValueError("arXiv 接口未返回可解析的 Atom XML。") from exc
    atom = "{http://www.w3.org/2005/Atom}"
    output = []
    for entry in root.findall(atom + "entry"):
        url = (entry.findtext(atom + "id") or "").replace("http://", "https://")
        published = entry.findtext(atom + "published") or ""
        output.append({
            "id": "A" + str(len(output) + 1), "kind": "paper_abstract",
            "title": " ".join((entry.findtext(atom + "title") or "Untitled").split()), "url": url,
            "year": published[:4] or None,
            "organization": [" ".join((author.findtext(atom + "name") or "").split())
                             for author in entry.findall(atom + "author")][:8],
            "text": " ".join((entry.findtext(atom + "summary") or "").split())[:5000],
            "note": "仅使用 arXiv 预印本摘要，未阅读全文；未经同行评审状态需另行核查",
        })
    return output


def retrieve_paper_pages(sources: list[dict], limit: int = MAX_WEB_EXPANSIONS) -> list[dict]:
    """Turn a small, diverse set of discovered paper URLs into readable web sources."""
    expanded = []
    seen = set()
    for source in sources:
        if len(expanded) >= limit or source.get("kind") not in {"paper_abstract", "paper_metadata"}:
            continue
        url = str(source.get("url") or "")
        if not url or url in seen or not public_url(url):
            continue
        seen.add(url)
        try:
            text = fetch_page(url)
            expanded.append({"id": "W" + str(len(expanded) + 1), "kind": "discovered_web",
                             "title": source.get("title") or url, "url": url, "text": text[:8000],
                             "based_on": source["id"], "note": "由论文检索自动扩展的公开落地页；网页文本可能不完整"})
            print("已读取自动扩展来源：" + url)
        except (OSError, ValueError, UnicodeError) as exc:
            expanded.append({"id": "W" + str(len(expanded) + 1), "kind": "discovered_unread",
                             "title": source.get("title") or url, "url": url, "text": "",
                             "based_on": source["id"], "note": "自动扩展来源读取失败：" + str(exc)})
            print("自动扩展来源读取失败，保留待查：" + url)
    return expanded


def discovery_queries(draft: dict, problem: dict) -> list[str]:
    """Build a small, complementary query set while remaining API-rate conscious."""
    supplied = draft.get("search_queries")
    candidates = supplied if isinstance(supplied, list) else []
    primary = str(draft.get("search_query") or problem["title"]).strip()
    candidates += [primary, primary + " review", primary + " recent developments"]
    queries = []
    seen = set()
    for value in candidates:
        query = str(value).strip()[:120]
        key = query.casefold()
        if query and key not in seen:
            queries.append(query)
            seen.add(key)
        if len(queries) == MAX_DISCOVERY_QUERIES:
            break
    return queries


def add_index_results(sources: list[dict], items: list[dict], prefix: str) -> None:
    """Give results stable IDs across multiple queries and remove duplicate records."""
    known = {(str(source.get("url") or "").casefold(), str(source.get("title") or "").casefold()) for source in sources}
    next_id = 1 + sum(1 for source in sources if str(source.get("id", "")).startswith(prefix))
    for item in items:
        key = (str(item.get("url") or "").casefold(), str(item.get("title") or "").casefold())
        if key in known:
            continue
        item = dict(item)
        item["id"] = prefix + str(next_id)
        next_id += 1
        sources.append(item)
        known.add(key)


def unique_readable_sources(sources: list[dict]) -> list[dict]:
    """Keep the first readable representation of a URL for model context."""
    output = []
    seen = set()
    for source in sources:
        if not source.get("text"):
            continue
        key = str(source.get("url") or source.get("id") or "").strip().casefold()
        if key in seen:
            continue
        seen.add(key)
        output.append(source)
    return output


def start() -> Path:
    print("\n新建研究：请记录原始现象，暂勿把原因写成事实。")
    title = ask("题目：")
    observation = ask("原始现象 / 论文线索：")
    goal = ask("实际目标 / 希望支持的决策：")
    domain = ask("领域 [visual-intelligence]：", required=False) or "visual-intelligence"
    if domain not in DOMAIN_PROTOCOLS:
        raise RuntimeError("当前内置领域协议为 visual-intelligence、physics；切换其他领域需要先设计并审查新协议。")
    links = ask("补充必查网址（多个用空格分隔，可留空；全局来源会自动检索）：", required=False).split()
    slug = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    path = RESEARCH / slug
    path.mkdir(parents=True, exist_ok=False)
    save_json(path / "problem.json", {"title": title, "observation": observation, "goal": goal,
                                      "domain": domain, "domain_version": DOMAIN_PROTOCOLS[domain]["version"], "required_urls": links,
                                      "source_registry_version": source_registry().get("version", "unknown"), "created_at": now()})
    save_json(path / "progress.json", {"stage": "framing", "updated_at": now()})
    return path


def choose() -> Path:
    candidates = []
    for path in sorted((p for p in RESEARCH.iterdir() if (p / "progress.json").exists()), reverse=True):
        try:
            load_json(path / "problem.json")["title"]
            load_json(path / "progress.json")["stage"]
        except (KeyError, ValueError) as exc:
            print(f"跳过无效研究记录 [{path.name}]：{exc}", file=sys.stderr)
            continue
        candidates.append(path)
    if not candidates:
        raise RuntimeError("没有可继续的研究，请新建。")
    for index, path in enumerate(candidates[:20], 1):
        print(f"{index}. {load_json(path / 'problem.json')['title']}  ({load_json(path / 'progress.json')['stage']})  [{path.name}]")
    selection = ask("编号：")
    if not selection.isdigit() or not 1 <= int(selection) <= min(20, len(candidates)):
        raise ValueError("编号无效。")
    return candidates[int(selection) - 1]


def stage(path: Path, value: str) -> None:
    save_json(path / "progress.json", {"stage": value, "updated_at": now()})


def archive_round(path: Path) -> Path:
    """Preserve the current evidence before refreshing the same study."""
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d-%H%M%S")
    archive = path / "rounds" / stamp
    archive.mkdir(parents=True, exist_ok=False)
    for relative in ("sources.json", "analysis_chunks.json", "analysis.json", "reports/current.md"):
        source = path / relative
        if source.exists():
            target = archive / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    save_json(archive / "round.json", {"archived_at": now(), "previous_stage": load_json(path / "progress.json")["stage"]})
    return archive


def trash_research(path: Path) -> Path:
    """Move a selected research directory to a recoverable local trash folder."""
    if path.parent.resolve() != RESEARCH.resolve() or not (path / "problem.json").is_file():
        raise ValueError("只能删除 research/ 下的完整研究目录。")
    trash = RESEARCH / ".trash"
    trash.mkdir(exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d-%H%M%S")
    target = trash / f"{path.name}--deleted-{stamp}"
    shutil.move(str(path), str(target))
    return target


def frame(path: Path, problem: dict) -> bool:
    draft_path = path / "draft.json"
    if not draft_path.exists():
        protocol = DOMAIN_PROTOCOLS.get(problem.get("domain"))
        if not protocol:
            raise RuntimeError("研究记录引用了未内置的领域协议，不能自动继续。")
        draft = model("按该领域的“" + protocol["checks"] + "”检查下面的线索。"
                      "返回字段 question（可操作化的研究问题，字符串）、observations（已知事实数组）、"
                      "hypotheses（至少三个竞争假设的数组，每项含 name/prediction）、"
                      "unknowns（待核查数组）、search_query（一个英文短检索词组）、search_queries（2到3个互补英文短检索词组）。保留未知和多因素作用，不把观察直接当因果。\n"
                      + json.dumps(problem, ensure_ascii=False))
        if not isinstance(draft.get("hypotheses"), list) or not draft.get("question"):
            raise ValueError("模型的问题框架不完整，未进入自动搜索。")
        save_json(draft_path, draft)
    else:
        draft = load_json(draft_path)
    print("\n问题框架（模型建议，尚未确认）：\n" + json.dumps(draft, ensure_ascii=False, indent=2))
    if not approved("确认此问题框架，允许公开资料检索？"):
        stage(path, "awaiting_problem_review")
        print("已暂停。可编辑 research/<研究编号>/draft.json 后运行 ./research.sh resume。")
        return False
    append_jsonl(path / "decisions.jsonl", {"at": now(), "type": "human_problem_confirmation", "question": draft["question"]})
    stage(path, "discovery")
    return True


def discover(path: Path, problem: dict) -> list[dict]:
    draft = load_json(path / "draft.json")
    registry = source_registry()
    source_ids = enabled_source_ids(problem["domain"], registry)
    sources = []
    for index, (registry_source, url) in enumerate(registered_web_urls(registry, source_ids), 1):
        try:
            body = fetch_page(url)
            sources.append({"id": "G" + str(index), "kind": "global_curated_web", "title": url,
                            "url": url, "text": body[:8000], "registry_source": registry_source,
                            "note": "全局来源注册表中的公开机构页面；网页文本可能不完整"})
            print("已读取全局来源：" + url)
        except (OSError, ValueError, UnicodeError) as exc:
            sources.append({"id": "G" + str(index), "kind": "global_curated_unread", "title": url,
                            "url": url, "text": "", "registry_source": registry_source,
                            "note": "全局来源读取失败：" + str(exc)})
            print("全局来源读取失败，保留待查：" + url)
    for index, url in enumerate(problem["required_urls"], 1):
        try:
            body = fetch_page(url)
            sources.append({"id": "H" + str(index), "kind": "curated_web", "title": url,
                            "url": url, "text": body[:8000], "note": "人工必查来源；网页文本可能不完整"})
            print("已读取人工来源：" + url)
        except (OSError, ValueError, UnicodeError) as exc:
            sources.append({"id": "H" + str(index), "kind": "curated_unread", "title": url,
                            "url": url, "text": "", "note": "读取失败：" + str(exc)})
            print("人工来源读取失败，保留待查：" + url)
    queries = discovery_queries(draft, problem)
    for index, query in enumerate(queries):
        try:
            # Use one OpenAlex request per round to reduce the chance of another 429;
            # the remaining complementary queries still run through Crossref.
            if index == 0 and "openalex" in source_ids:
                add_index_results(sources, openalex_search(query), "P")
                print("已完成 OpenAlex 检索：" + query)
        except (OSError, ValueError, KeyError) as exc:
            print("OpenAlex 暂不可用，已记录待查：" + str(exc))
        try:
            if "crossref" in source_ids:
                add_index_results(sources, crossref_search(query), "C")
                print("已完成 Crossref 检索：" + query)
        except (OSError, ValueError, KeyError) as exc:
            print("Crossref 接口暂不可用，已记录待查：" + str(exc))
    if "arxiv" in source_ids:
        try:
            add_index_results(sources, arxiv_search(queries[0]), "A")
            print("已完成 arXiv 预印本检索：" + queries[0])
        except (OSError, ValueError, KeyError) as exc:
            print("arXiv 接口暂不可用，已记录待查：" + str(exc))
    sources.extend(retrieve_paper_pages(sources))
    save_json(path / "sources.json", {"retrieved_at": now(), "registry_version": registry.get("version", "unknown"),
                                       "enabled_sources": sorted(source_ids), "items": sources})
    stage(path, "analysis")
    return sources


def analysis(path: Path, problem: dict, sources: list[dict]) -> None:
    draft = load_json(path / "draft.json")
    readable = unique_readable_sources(sources)
    if not readable:
        stage(path, "awaiting_sources")
        print("没有可读资料；请提供可公开读取的网址后继续。")
        return
    if len(readable) < sum(bool(source.get("text")) for source in sources):
        print(f"分析前已按网址去重可读资料：{len(readable)} 条。")
    source_input = [{**{k: s.get(k) for k in ("id", "kind", "title", "url", "year", "note")},
                     "text": str(s.get("text") or "")[:MAX_ANALYSIS_SOURCE_CHARS]}
                    for s in readable[:MAX_ANALYSIS_SOURCES]]
    fingerprint = hashlib.sha256(json.dumps(source_input, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    chunks_path = path / "analysis_chunks.json"
    cached = load_json(chunks_path) if chunks_path.exists() else {}
    chunks = cached.get("chunks", []) if cached.get("fingerprint") == fingerprint else []
    if not isinstance(chunks, list):
        chunks = []
    for start in range(len(chunks) * ANALYSIS_CHUNK_SIZE, len(source_input), ANALYSIS_CHUNK_SIZE):
        batch = source_input[start:start + ANALYSIS_CHUNK_SIZE]
        batch_ids = {source["id"] for source in batch}
        review = model("逐条审查以下来源，只能引用给定的来源ID。摘要不是全文，不能声称完成论文验证；"
                       "不得声称完成本地实验。返回 findings（每项含 source_id/claim/limits/relation）、"
                       "mechanism_leads（待验证机制线索数组）、counterpoints（反例或替代解释数组）、"
                       "human_needed（需人工提供的内容数组）、open_questions（数组）。无证据时用空数组。\n"
                       + json.dumps({"problem": problem, "frame": draft, "sources": batch}, ensure_ascii=False))
        findings = review.get("findings", [])
        if not isinstance(findings, list):
            raise ValueError("模型没有返回可校验的分块发现列表。")
        review["findings"] = [entry for entry in findings if isinstance(entry, dict) and entry.get("source_id") in batch_ids]
        chunks.append({"source_ids": sorted(batch_ids), "review": review})
        save_json(chunks_path, {"fingerprint": fingerprint, "chunks": chunks, "updated_at": now()})
        print(f"已完成资料审查分块：{len(chunks)}/{(len(source_input) + ANALYSIS_CHUNK_SIZE - 1) // ANALYSIS_CHUNK_SIZE}。")
    result = model("基于以下已分块审查的来源结果，形成第一轮综合分析。只能保留给定 source_id 的发现；"
                   "摘要不是全文，不能声称完成论文验证；不得声称完成本地实验。返回字段："
                   "findings（数组，每项含 source_id/claim/limits/relation）、"
                   "mechanism_conclusions（机制性结论或待验证线索数组）、"
                   "engineering_conclusions（当前工程结论或待验证建议数组）、"
                   "counterpoints（反例/替代解释数组）、next_question（最相关的下一子问题字符串）、"
                   "human_needed（需人提供的数据或判断数组）。结果无法支持结论时用空数组。\n"
                   + json.dumps({"problem": problem, "frame": draft, "source_reviews": chunks}, ensure_ascii=False))
    known = {s["id"] for s in source_input}
    findings = result.get("findings", [])
    if not isinstance(findings, list):
        raise ValueError("模型没有返回可校验的发现列表。")
    verified = [entry for entry in findings if isinstance(entry, dict) and entry.get("source_id") in known]
    if len(verified) < len(findings):
        print("已排除引用不存在来源的模型主张。")
    result["findings"] = verified
    save_json(path / "analysis.json", result)
    print("\n阶段性分析：\n" + json.dumps(result, ensure_ascii=False, indent=2))
    if not approved("将这轮阶段性分析写入研究记录？"):
        stage(path, "awaiting_analysis_review")
        print("已暂停；检查 research/<研究编号>/analysis.json 后用 ./research.sh resume 继续。")
        return
    write_report(path, problem, draft, sources, result)
    stage(path, "awaiting_next_round")
    print("\n研究记录：" + str(path / "reports" / "current.md"))
    print("下一步如需本地实验：人提供真实素材和现有工程仓库的能力说明后再设计可执行脚本。")


def write_report(path: Path, problem: dict, draft: dict, sources: list[dict], result: dict) -> None:
    reports = path / "reports"
    reports.mkdir(exist_ok=True)
    lines = ["# 当前研究结论（第一轮，待原文与本地实验验证）", "", f"研究目标：{problem['goal']}",
             f"原始现象：{problem['observation']}", f"当前问题：{draft['question']}", "", "## 资料与发现", ""]
    for item in result.get("findings", []):
        source = next(s for s in sources if s["id"] == item["source_id"])
        lines.append(f"- [{item['source_id']}] {item.get('claim', '')}；限制：{item.get('limits', '')}。来源：{source['url']}")
    for title, field in [("机制性判断", "mechanism_conclusions"), ("工程判断", "engineering_conclusions"),
                         ("反例与替代解释", "counterpoints"), ("需要人工提供", "human_needed")]:
        lines.extend(["", "## " + title, ""])
        for value in result.get(field, []):
            lines.append("- " + str(value))
    lines.extend(["", "## 下一子问题", "", str(result.get("next_question", "待人工决定")), "",
                  "说明：本轮可能只读取论文摘要或部分网页；本地工程结论尚须实验证实。", ""])
    (reports / "current.md").write_text("\n".join(lines), encoding="utf-8")
    with (reports / "history.md").open("a", encoding="utf-8") as handle:
        handle.write(f"\n## {now()} · 首轮研究\n\n确认问题框架；检索 {len(sources)} 条候选来源；"
                     f"接受 {len(result.get('findings', []))} 条可定位来源的发现。当前结论：见 current.md。\n")
    (reports / "revisions.md").touch(exist_ok=True)
    append_jsonl(path / "decisions.jsonl", {"at": now(), "type": "human_analysis_confirmation", "source_count": len(sources)})


def run(path: Path) -> None:
    problem = load_json(path / "problem.json")
    current = load_json(path / "progress.json")["stage"]
    print("研究：" + problem["title"] + "；当前状态：" + current)
    if current in {"framing", "awaiting_problem_review"}:
        if not frame(path, problem):
            return
        current = "discovery"
    if current == "discovery":
        sources = discover(path, problem)
        current = "analysis"
    if current in {"analysis", "awaiting_analysis_review"}:
        sources = load_json(path / "sources.json")["items"]
        if current == "awaiting_analysis_review":
            result = load_json(path / "analysis.json")
            print("待确认分析：\n" + json.dumps(result, ensure_ascii=False, indent=2))
            if approved("写入研究记录？"):
                write_report(path, problem, load_json(path / "draft.json"), sources, result)
                stage(path, "awaiting_next_round")
            return
        analysis(path, problem, sources)
    elif current == "awaiting_sources":
        print("请在 problem.json 的 required_urls 中补充公开网址，再运行 ./research.sh retry-sources。")
    elif current == "awaiting_next_round":
        print("首轮研究已完成。当前结论：" + str(path / "reports" / "current.md"))


def main() -> int:
    RESEARCH.mkdir(exist_ok=True)
    args = sys.argv[1:]
    if args and args[0] in {"-h", "--help"}:
        print("用法: ./research.sh [new|resume|retry-sources|refresh|delete]；默认显示交互菜单。")
        return 0
    action = args[0] if args else ask("启动研究 [new] / 继续研究 [resume]：", required=False) or "new"
    if action == "new":
        path = start()
    elif action in {"resume", "retry-sources", "refresh", "next-round"}:
        path = choose()
        if action == "retry-sources":
            if load_json(path / "progress.json")["stage"] != "awaiting_sources":
                raise RuntimeError("只能对等待来源的研究重新检索。")
            stage(path, "discovery")
        elif action in {"refresh", "next-round"}:
            current = load_json(path / "progress.json")["stage"]
            if current not in {"analysis", "awaiting_analysis_review", "awaiting_next_round", "awaiting_sources"}:
                raise RuntimeError("当前阶段不能补充研究；请先确认问题框架或完成当前来源检索。")
            archive = archive_round(path)
            append_jsonl(path / "decisions.jsonl", {"at": now(), "type": "research_refresh", "archive": str(archive.relative_to(path))})
            stage(path, "discovery")
            print("已归档上一轮来源与分析：" + str(archive))
    elif action == "delete":
        path = choose()
        title = load_json(path / "problem.json")["title"]
        confirmation = read_prompt(f"将研究“{title}”移入 research/.trash（可恢复）。输入 DELETE 确认：")
        if confirmation != "DELETE":
            print("已取消；未删除任何研究。")
            return 0
        target = trash_research(path)
        print("已移入可恢复回收目录：" + str(target))
        return 0
    else:
        raise ValueError("只支持 new、resume、retry-sources。")
    run(path)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (KeyboardInterrupt, EOFError):
        print("\n已中断；已有记录保留，可使用 ./research.sh resume。", file=sys.stderr)
        raise SystemExit(130)
    except (OSError, ValueError, KeyError, RuntimeError, urllib.error.URLError, socket.timeout) as exc:
        print("研究暂停：" + str(exc), file=sys.stderr)
        print("已有记录保留；排除问题后运行 ./research.sh resume。", file=sys.stderr)
        raise SystemExit(1)
