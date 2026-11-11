"""Web and shopping search tools with separate collection and filtering stages."""

import ipaddress
import json
import re
import socket
from urllib.parse import parse_qs, unquote, urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup
from langchain_core.tools import tool

from config.settings import (
    WEB_PAGE_MAX_BYTES,
    WEB_PAGE_MAX_CHARS,
    WEB_REQUEST_TIMEOUT,
    WEB_SEARCH_MAX_RESULTS,
    WEB_SEARCH_OVERFETCH,
)

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
_NEGATION_TERMS = (
    "미지원",
    "없음",
    "제외",
    "불가",
    "미포함",
    "아님",
    "아닌",
    "아니고",
    "아닙니다",
)


def _plain_text(value: str) -> str:
    text = BeautifulSoup(str(value or ""), "html.parser").get_text(" ", strip=True)
    return " ".join(text.split())


def _keyword_occurrences(text: str, keyword: str) -> tuple[bool, bool]:
    """Return whether the keyword has positive and negated occurrences."""
    escaped = re.escape(keyword.strip())
    if not escaped:
        return False, False

    positive = False
    negated = False
    for match in re.finditer(escaped, text, flags=re.IGNORECASE):
        before = text[max(0, match.start() - 18) : match.start()]
        after = text[match.end() : match.end() + 18]
        preceding_negation = re.search(
            rf"(?:{'|'.join(map(re.escape, _NEGATION_TERMS))})\s*$",
            before,
            flags=re.IGNORECASE,
        )
        following_negation = re.match(
            rf"[\s\w가-힣]{{0,18}}?(?:{'|'.join(map(re.escape, _NEGATION_TERMS))})",
            after,
            flags=re.IGNORECASE,
        )
        if preceding_negation or following_negation:
            negated = True
        else:
            positive = True
    return positive, negated


def _filter_items(
    items: list[dict],
    exclude_keywords: list[str] | None = None,
    include_keywords: list[str] | None = None,
    max_results: int = WEB_SEARCH_MAX_RESULTS,
) -> tuple[list[dict], int]:
    excluded = [word.strip() for word in exclude_keywords or [] if word.strip()]
    included = [word.strip() for word in include_keywords or [] if word.strip()]
    kept = []
    filtered_out_count = 0

    for source_item in items:
        item = dict(source_item)
        searchable_text = " ".join(
            str(item.get(field, ""))
            for field in ("title", "summary", "spec_summary")
        )
        needs_context = False
        definitely_excluded = False
        for keyword in excluded:
            positive, negated = _keyword_occurrences(searchable_text, keyword)
            if positive:
                definitely_excluded = True
                break
            if negated:
                needs_context = True

        if definitely_excluded:
            filtered_out_count += 1
            continue

        item["include_score"] = sum(
            1
            for keyword in included
            if keyword.casefold() in searchable_text.casefold()
        )
        if needs_context:
            item["context_note"] = "[문맥확인필요] 제외 옵션이 실제로 없는 의미인지 확인하세요."
        kept.append(item)

    kept.sort(key=lambda result: result["include_score"], reverse=True)
    return kept[: max(0, max_results)], filtered_out_count


def _extract_ddg_url(href: str) -> str:
    absolute = urljoin("https://duckduckgo.com", href)
    parsed = urlsplit(absolute)
    redirected = parse_qs(parsed.query).get("uddg")
    return unquote(redirected[0]) if redirected else absolute


def _collect_duckduckgo(query: str, limit: int) -> list[dict]:
    response = httpx.get(
        "https://html.duckduckgo.com/html/",
        params={"q": query},
        headers={"User-Agent": _USER_AGENT},
        timeout=WEB_REQUEST_TIMEOUT,
        follow_redirects=True,
    )
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    results = []
    for result in soup.select(".result")[:limit]:
        link = result.select_one(".result__title a")
        if not link:
            continue
        snippet = result.select_one(".result__snippet")
        results.append(
            {
                "title": _plain_text(link.get_text(" ", strip=True)),
                "url": _extract_ddg_url(str(link.get("href", ""))),
                "summary": _plain_text(snippet.get_text(" ", strip=True)) if snippet else "",
            }
        )
    return results


def _fetch_danawa_items(query: str, max_items: int) -> list[dict]:
    if max_items <= 0:
        return []
    response = httpx.get(
        "https://search.danawa.com/dsearch.php",
        params={
            "k1": query,
            "module": "goods",
            "act": "dispMain",
            "limit": min(max(1, max_items), WEB_SEARCH_OVERFETCH),
        },
        headers={
            "User-Agent": _USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
            "Referer": "https://www.danawa.com/",
        },
        timeout=WEB_REQUEST_TIMEOUT,
        follow_redirects=True,
    )
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    results = []
    title_links = soup.select('p.mb-2 a[href*="prod.danawa.com/info/?pcode="]')
    if not title_links:
        title_links = soup.select('a[href*="prod.danawa.com/info/?pcode="]')

    seen_urls = set()
    for title_link in title_links:
        product = title_link.find_parent("li")
        if product is None:
            continue
        title = _plain_text(title_link.get_text(" ", strip=True))
        url = str(title_link.get("href", "")).strip()
        if (
            not title
            or not url
            or title.casefold() in {"광고", "상품명 없음"}
            or url in seen_urls
        ):
            continue
        seen_urls.add(url)
        if url.startswith("//"):
            url = f"https:{url}"
        elif url.startswith("/"):
            url = urljoin("https://www.danawa.com", url)

        spec_nodes = [
            node
            for node in product.select("div.leading-18")
            if "/" in node.get_text(" ", strip=True)
        ]
        spec_summary = max(
            (_plain_text(node.get_text(" ", strip=True)) for node in spec_nodes),
            key=len,
            default="",
        )
        price_text = next(
            (
                _plain_text(anchor.get_text(" ", strip=True))
                for anchor in product.select("a")
                if re.search(r"[\d,]+\s*원", anchor.get_text(" ", strip=True))
            ),
            "",
        )
        results.append(
            {
                "title": title,
                "spec_summary": spec_summary,
                "summary": spec_summary,
                "price": price_text or None,
                "url": url,
                "source": "danawa",
            }
        )
        if len(results) >= max_items:
            break
    return results


def _fetch_ddg_shopping_items(query: str, max_items: int) -> list[dict]:
    results = _collect_duckduckgo(query, min(max(1, max_items), WEB_SEARCH_OVERFETCH))
    for item in results:
        text = f"{item.get('title', '')} {item.get('summary', '')}"
        price = re.search(r"(?:₩\s*|KRW\s*)([\d,]+)|([\d,]+)\s*원", text, re.IGNORECASE)
        if price:
            item["price"] = next(group for group in price.groups() if group)
        item["source"] = "duckduckgo"
    return results


def _collect_shopping_raw(query: str) -> tuple[list[dict], str]:
    limit = min(max(1, WEB_SEARCH_OVERFETCH), 100)
    try:
        items = _fetch_danawa_items(query, limit)
    except Exception as error:
        print(f"[WebSearch] Danawa collection failed; using DuckDuckGo: {error}", flush=True)
        items = []
    if items:
        return items, "danawa"
    return _fetch_ddg_shopping_items(query, limit), "duckduckgo"


def _collect_web_raw(query: str, limit: int) -> list[dict]:
    return _collect_duckduckgo(query, min(max(1, limit), WEB_SEARCH_OVERFETCH))


def _validate_public_http_url(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("http 또는 https 웹 주소만 조회할 수 있습니다.")
    if parsed.username or parsed.password:
        raise ValueError("사용자 정보가 포함된 URL은 조회할 수 없습니다.")

    host = parsed.hostname.rstrip(".")
    if host.casefold() == "localhost" or host.casefold().endswith(".localhost"):
        raise ValueError("로컬 네트워크 주소는 조회할 수 없습니다.")
    try:
        addresses = [ipaddress.ip_address(host)]
    except ValueError:
        try:
            records = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80))
        except OSError as error:
            raise ValueError("웹 주소의 호스트를 확인할 수 없습니다.") from error
        addresses = [ipaddress.ip_address(record[4][0]) for record in records]
    if not addresses or any(not address.is_global for address in addresses):
        raise ValueError("공개 인터넷 주소만 조회할 수 있습니다.")
    return url


def _fetch_public_page(url: str) -> tuple[str, str]:
    current_url = url
    headers = {"User-Agent": _USER_AGENT, "Accept": "text/html,application/xhtml+xml"}
    with httpx.Client(
        timeout=WEB_REQUEST_TIMEOUT,
        follow_redirects=False,
        trust_env=False,
        headers=headers,
    ) as client:
        for _ in range(6):
            _validate_public_http_url(current_url)
            with client.stream("GET", current_url) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if not location:
                        raise ValueError("리다이렉트 위치가 비어 있습니다.")
                    current_url = urljoin(current_url, location)
                    continue
                response.raise_for_status()
                chunks = []
                total_bytes = 0
                for chunk in response.iter_bytes():
                    total_bytes += len(chunk)
                    if total_bytes > WEB_PAGE_MAX_BYTES:
                        raise ValueError("페이지 응답이 허용 크기를 초과했습니다.")
                    chunks.append(chunk)
                content_type = response.headers.get("content-type", "")
                if content_type and not any(
                    kind in content_type.casefold()
                    for kind in ("text/html", "application/xhtml+xml", "text/plain")
                ):
                    raise ValueError("HTML 또는 텍스트 페이지가 아닙니다.")
                return b"".join(chunks).decode("utf-8", errors="replace"), str(response.url)
    raise ValueError("리다이렉트가 너무 많습니다.")


def _extract_page_details(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    title = _plain_text(soup.title.get_text(" ", strip=True)) if soup.title else ""
    for node in soup.select("script, style, nav, footer, header, aside, noscript, svg"):
        node.decompose()

    tables = []
    for table in soup.select("table"):
        rows = []
        for row in table.select("tr"):
            cells = [
                _plain_text(cell.get_text(" ", strip=True))
                for cell in row.select("th, td")
            ]
            if cells:
                rows.append(" | ".join(cells))
        if rows:
            tables.append("[표]\n" + "\n".join(rows))

    main = soup.select_one("main, article") or soup.body or soup
    body_text = _plain_text(main.get_text(" ", strip=True))
    table_text = "\n\n".join(tables)
    content = "\n\n".join(part for part in (body_text, table_text) if part)
    return {"title": title, "content": content[:WEB_PAGE_MAX_CHARS]}


@tool
def search_shopping_products(
    query: str,
    include_keywords: list[str] | None = None,
    exclude_keywords: list[str] | None = None,
    max_results: int = 15,
) -> str:
    """웹 쇼핑 상품을 검색하고 포함어 우선 정렬 및 제외 옵션 필터링을 수행합니다."""
    if not query.strip():
        return json.dumps({"error": "검색어를 입력해 주세요."}, ensure_ascii=False)
    try:
        fetched, source_used = _collect_shopping_raw(query)
    except Exception as error:
        return json.dumps({"error": f"상품 검색에 실패했습니다: {error}"}, ensure_ascii=False)
    items, filtered_out = _filter_items(
        fetched,
        exclude_keywords=exclude_keywords,
        include_keywords=include_keywords,
        max_results=max_results,
    )
    return json.dumps(
        {
            "total_fetched": len(fetched),
            "filtered_out_count": filtered_out,
            "remaining_items": len(items),
            "source_used": source_used,
            "items": items,
        },
        ensure_ascii=False,
    )


@tool
def search_web_info(
    query: str,
    exclude_keywords: list[str] | None = None,
    max_results: int = 10,
) -> str:
    """일반 웹 문서, 리뷰, 기술 정보를 검색하고 지정한 제외어를 필터링합니다."""
    if not query.strip():
        return json.dumps({"error": "검색어를 입력해 주세요."}, ensure_ascii=False)
    try:
        fetched = _collect_web_raw(query, min(max_results, WEB_SEARCH_OVERFETCH))
    except Exception as error:
        return json.dumps({"error": f"웹 검색에 실패했습니다: {error}"}, ensure_ascii=False)
    items, filtered_out = _filter_items(
        fetched,
        exclude_keywords=exclude_keywords,
        max_results=max_results,
    )
    return json.dumps(
        {
            "total_fetched": len(fetched),
            "filtered_out_count": filtered_out,
            "remaining_items": len(items),
            "items": items,
        },
        ensure_ascii=False,
    )


@tool
def fetch_page_details(url: str) -> str:
    """공개 웹페이지의 본문과 표 내용을 제한된 길이로 추출합니다."""
    try:
        html, final_url = _fetch_public_page(url)
        details = _extract_page_details(html)
        details["url"] = final_url
        return json.dumps(details, ensure_ascii=False)
    except Exception as error:
        return json.dumps({"error": f"페이지를 가져오지 못했습니다: {error}"}, ensure_ascii=False)


WEB_TOOLS = [search_shopping_products, search_web_info, fetch_page_details]

WEB_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "search_shopping_products",
            "description": search_shopping_products.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "품목과 핵심 스펙만 포함한 검색어"},
                    "include_keywords": {"type": "array", "items": {"type": "string"}},
                    "exclude_keywords": {"type": "array", "items": {"type": "string"}},
                    "max_results": {"type": "integer", "minimum": 1, "maximum": 40},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_web_info",
            "description": search_web_info.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "exclude_keywords": {"type": "array", "items": {"type": "string"}},
                    "max_results": {"type": "integer", "minimum": 1, "maximum": 40},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_page_details",
            "description": fetch_page_details.description,
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string", "format": "uri"}},
                "required": ["url"],
            },
        },
    },
]