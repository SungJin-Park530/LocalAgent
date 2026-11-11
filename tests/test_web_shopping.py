import json

import pytest

from config.intent_routes import INTENT_ANCHORS
from engine import preflight_node, router_node
from prompt_loader import assemble_system_prompt
from room_manager import TOOL_GROUP_SCHEMAS, get_available_tool_keys
from tools import ALL_SCHEMAS, TOOL_REGISTRY
from tools import web_shopping


def test_web_tool_group_is_registered_for_rooms_and_engine():
    assert "web" in get_available_tool_keys()
    assert len(TOOL_GROUP_SCHEMAS["web"]) == 3
    assert all(
        schema["function"]["name"] in TOOL_REGISTRY
        for schema in TOOL_GROUP_SCHEMAS["web"]
    )
    assert all(schema in ALL_SCHEMAS for schema in TOOL_GROUP_SCHEMAS["web"])
    assert len(INTENT_ANCHORS["web"]) >= 8

    prompt = assemble_system_prompt(None, ["web"])
    assert "웹 및 쇼핑 검색 도구 명세" in prompt
    assert "검색어와 제외어 분리" in prompt


def test_web_route_marks_missing_group_and_preflight_names_it(monkeypatch):
    from langchain_core.messages import HumanMessage

    monkeypatch.setattr("engine.classify_intent", lambda query: ("web", 0.9))
    route_result = router_node({
        "messages": [HumanMessage(content="IPS가 아닌 모니터")],
        "selected_tools": [],
    })
    message = preflight_node({"missing_tool": route_result["missing_tool"]})

    assert route_result["missing_tool"] == "web"
    assert "[웹·쇼핑 검색]" in message["messages"][0].content


def test_exclude_filter_drops_positive_option_and_keeps_negated_context():
    items = [
        {"title": "블루투스 포함 무선 마우스", "summary": "Bluetooth 지원"},
        {"title": "블루투스 미지원 마우스", "summary": "블루투스 없음"},
        {"title": "무선 마우스", "summary": "블루투스 제외 모델"},
        {"title": "27인치 모니터", "summary": "IPS 패널 아닌 제품"},
    ]

    kept, filtered_count = web_shopping._filter_items(
        items, exclude_keywords=["블루투스", "IPS"], max_results=10
    )

    assert filtered_count == 1
    assert len(kept) == 3
    assert all("context_note" in item for item in kept)


def test_include_keywords_rank_matching_results_first():
    items = [
        {"title": "일반 모니터", "summary": "24인치"},
        {"title": "27인치 모니터", "summary": "IPS 패널"},
    ]

    kept, _ = web_shopping._filter_items(
        items, include_keywords=["27인치"], max_results=2
    )

    assert kept[0]["title"] == "27인치 모니터"
    assert kept[0]["include_score"] == 1


def test_page_extraction_removes_noise_and_keeps_tables(monkeypatch):
    monkeypatch.setattr(web_shopping, "WEB_PAGE_MAX_CHARS", 80)
    result = web_shopping._extract_page_details(
        """<html><head><title>제품 사양</title><style>hidden</style></head>
        <body><nav>메뉴</nav><main><p>27인치 제품 설명</p>
        <table><tr><th>패널</th><td>VA</td></tr></table></main>
        <footer>푸터</footer><script>noise</script></body></html>"""
    )

    assert result["title"] == "제품 사양"
    assert "메뉴" not in result["content"]
    assert "hidden" not in result["content"]
    assert "패널 | VA" in result["content"]
    assert len(result["content"]) <= 80


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/admin",
        "http://localhost/",
        "file:///etc/passwd",
        "http://user:pass@example.com/",
    ],
)
def test_private_or_non_http_page_urls_are_rejected(url):
    with pytest.raises(ValueError):
        web_shopping._validate_public_http_url(url)


def test_search_output_contains_filter_metadata(monkeypatch):
    monkeypatch.setattr(
        web_shopping,
        "_collect_shopping_raw",
        lambda query: (
            [
                {"title": "모니터 A", "spec_summary": "27인치 IPS"},
                {"title": "모니터 B", "spec_summary": "27인치 VA"},
            ],
            "danawa",
        ),
    )

    output = json.loads(
        web_shopping.search_shopping_products.invoke(
            {"query": "27인치 모니터", "exclude_keywords": ["IPS"]}
        )
    )

    assert output["total_fetched"] == 2
    assert output["filtered_out_count"] == 1
    assert output["remaining_items"] == 1
    assert output["source_used"] == "danawa"


def test_danawa_parser_extracts_product_fields_and_headers(monkeypatch):
    class FakeResponse:
        text = """
        <ul>
                    <li class="border-b">
                        <p class="mb-2"><a href="https://prod.danawa.com/info/?pcode=1"> 모니터   모델 A </a></p>
                        <div class="leading-18">모니터 / 27인치 / VA 패널 / USB-C</div>
                        <a class="flex text-px12">189,000원</a>
          </li>
                    <li class="border-b">
                        <p class="mb-2"><a href="https://ads.example/ad">광고 상품</a></p>
          </li>
                    <li class="border-b"><p class="mb-2"><a href="https://prod.danawa.com/info/?pcode=2">모니터 B</a></p></li>
        </ul>
        """

        def raise_for_status(self):
            pass

    request = {}

    def fake_get(url, **kwargs):
        request["url"] = url
        request.update(kwargs)
        return FakeResponse()

    monkeypatch.setattr(web_shopping.httpx, "get", fake_get)
    items = web_shopping._fetch_danawa_items("27인치 모니터", 1)

    assert len(items) == 1
    assert items[0] == {
        "title": "모니터 모델 A",
        "spec_summary": "모니터 / 27인치 / VA 패널 / USB-C",
        "summary": "모니터 / 27인치 / VA 패널 / USB-C",
        "price": "189,000원",
        "url": "https://prod.danawa.com/info/?pcode=1",
        "source": "danawa",
    }
    assert request["url"] == "https://search.danawa.com/dsearch.php"
    assert request["params"]["k1"] == "27인치 모니터"
    assert request["params"]["limit"] == 1
    assert request["headers"]["Accept-Language"].startswith("ko-KR")
    assert request["headers"]["Referer"] == "https://www.danawa.com/"


@pytest.mark.parametrize("danawa_result", [[], RuntimeError("network error")])
def test_shopping_collection_falls_back_to_duckduckgo(monkeypatch, danawa_result):
    def fetch_danawa(query, max_items):
        if isinstance(danawa_result, Exception):
            raise danawa_result
        return danawa_result

    fallback_items = [{"title": "DDG product", "summary": "spec"}]
    monkeypatch.setattr(web_shopping, "_fetch_danawa_items", fetch_danawa)
    monkeypatch.setattr(
        web_shopping,
        "_fetch_ddg_shopping_items",
        lambda query, max_items: fallback_items,
    )

    items, source = web_shopping._collect_shopping_raw("monitor")

    assert items == fallback_items
    assert source == "duckduckgo"


def test_shopping_collection_prefers_danawa(monkeypatch):
    danawa_items = [{"title": "Danawa product", "spec_summary": "VA"}]
    monkeypatch.setattr(
        web_shopping, "_fetch_danawa_items", lambda query, max_items: danawa_items
    )
    monkeypatch.setattr(
        web_shopping,
        "_fetch_ddg_shopping_items",
        lambda query, max_items: pytest.fail("DDG should not be called"),
    )

    items, source = web_shopping._collect_shopping_raw("monitor")

    assert items == danawa_items
    assert source == "danawa"


def test_filter_checks_danawa_spec_summary():
    items, filtered_count = web_shopping._filter_items(
        [{"title": "27인치 모니터", "spec_summary": "IPS 패널"}],
        exclude_keywords=["IPS"],
    )

    assert items == []
    assert filtered_count == 1


def test_naver_api_settings_and_collector_are_removed():
    from config import settings

    assert not hasattr(settings, "NAVER_CLIENT_ID")
    assert not hasattr(settings, "NAVER_CLIENT_SECRET")
    assert not hasattr(web_shopping, "_collect_naver_shopping")