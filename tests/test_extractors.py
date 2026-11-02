import json

import httpx
import pytest

from sarkari_core.extractor import DocumentText, ExtractionError, ExtractionResult, PageText, QuotaExhausted
from sarkari_crawler.extractors import PROVIDERS, ChatExtractor, FallbackExtractor, get_extractor
from sarkari_crawler.extractors.chat import parse_json_object

DOC = DocumentText(header_pages=[PageText(1, "header text")], posts_pages=[PageText(2, "posts text")])


def chat_response(
    content: str, status: int = 200, headers: dict | None = None, tokens: int = 0, finish_reason: str = "stop"
) -> httpx.Response:
    if status == 200:
        body = {"choices": [{"message": {"content": content}, "finish_reason": finish_reason}], "usage": {"total_tokens": tokens}}
    else:
        body = {"error": content or "nope"}
    return httpx.Response(status, json=body, headers=headers)


GROQ_DAILY_LIMIT = (
    "Rate limit reached for model `openai/gpt-oss-120b` in organization `org_x` service tier `on_demand` on "
    "tokens per day (TPD): Limit 200000, Used 199000, Requested 6000. Please try again in 7m12s."
)


def make_extractor(
    responses: list[httpx.Response], requests: list | None = None, sleeps: list | None = None, provider: str = "groq"
):
    queue = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        if requests is not None:
            requests.append(request)
        return queue.pop(0)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    return ChatExtractor(
        PROVIDERS[provider], "test-model", "secret", client=client, sleep=(sleeps.append if sleeps is not None else lambda s: None)
    )


@pytest.fixture
def header_and_posts(sample_payload):
    posts = sample_payload.pop("posts")
    return json.dumps(sample_payload), json.dumps({"posts": posts, "low_confidence_fields": ["posts[0].max_age"]})


def test_two_step_extraction(header_and_posts):
    requests = []
    extractor = make_extractor(
        [chat_response(header_and_posts[0], tokens=3000), chat_response(header_and_posts[1], tokens=4500)], requests
    )

    result = extractor.extract(DOC)

    assert result.ok
    assert len(result.draft.posts) == 2
    assert result.draft.low_confidence_fields == ["posts[0].max_age"]
    assert (result.extractor, result.model, result.prompt_version) == ("groq", "test-model", "v2")
    assert result.tokens_used == 7500
    first, second = (json.loads(r.content) for r in requests)
    assert first["model"] == "test-model" and first["temperature"] == 0
    assert first["response_format"] == {"type": "json_object"}
    assert first["reasoning_effort"] == "low"  # GPT-OSS: reasoning tokens count against the caps
    assert (first["max_tokens"], second["max_tokens"]) == (2500, 3500)
    assert "header text" in first["messages"][1]["content"]
    assert "posts text" in second["messages"][1]["content"]
    assert requests[0].headers["authorization"] == "Bearer secret"
    assert str(requests[0].url) == "https://api.groq.com/openai/v1/chat/completions"


def test_corrigendum_skips_posts_step(sample_payload):
    sample_payload.pop("posts")
    sample_payload["document_kind"] = "CORRIGENDUM"
    requests = []
    result = make_extractor([chat_response(json.dumps(sample_payload))], requests).extract(DOC)
    assert result.ok and result.draft.posts == []
    assert len(requests) == 1


def test_bad_posts_output_keeps_header(header_and_posts):
    result = make_extractor([chat_response(header_and_posts[0]), chat_response("not json at all")]).extract(DOC)
    assert not result.ok
    assert result.draft is not None and result.draft.posts == []
    assert result.errors[0].startswith("posts:")
    assert result.raw_outputs[1] == "not json at all"


def test_invalid_header_gives_no_draft():
    result = make_extractor([chat_response(json.dumps({"title": "missing organisation"}))]).extract(DOC)
    assert result.draft is None
    assert "organisation_name" in result.errors[0]


def test_empty_document_makes_no_calls():
    requests = []
    result = make_extractor([], requests).extract(DocumentText(header_pages=[], posts_pages=[]))
    assert result.draft is None and requests == []
    assert "no text found" in result.errors[0]


def test_retries_rate_limits_using_retry_after(header_and_posts):
    sleeps = []
    responses = [
        chat_response("", 429, {"retry-after": "7"}),
        chat_response("", 503),
        chat_response(header_and_posts[0]),
        chat_response(header_and_posts[1]),
    ]
    result = make_extractor(responses, sleeps=sleeps).extract(DOC)
    assert result.ok
    assert sleeps == [7.0, 4.0]


def test_gives_up_after_max_attempts():
    with pytest.raises(ExtractionError, match="after 6 attempts") as info:
        make_extractor([chat_response("", 429)] * 6).extract(DOC)
    assert not isinstance(info.value, QuotaExhausted)


@pytest.mark.parametrize(
    "response",
    [
        chat_response(GROQ_DAILY_LIMIT, 429, {"retry-after": "432"}),
        chat_response("Rate limit exceeded: free-models-per-day. Add 10 credits to unlock 1000 free model requests per day", 429),
        chat_response("slow down", 429, {"retry-after": "3600"}),
    ],
)
def test_daily_limit_stops_without_retrying(response):
    sleeps, requests = [], []
    with pytest.raises(QuotaExhausted):
        make_extractor([response], requests, sleeps).extract(DOC)
    assert len(requests) == 1 and sleeps == []


def test_per_minute_limit_waits_and_retries(header_and_posts):
    tpm = "Rate limit reached for model on tokens per minute (TPM): Limit 8000, Used 7000, Requested 3000."
    sleeps = []
    responses = [chat_response(tpm, 429, {"retry-after": "9"}), chat_response(header_and_posts[0]), chat_response(header_and_posts[1])]
    assert make_extractor(responses, sleeps=sleeps).extract(DOC).ok
    assert sleeps == [9.0]


def test_truncated_reply_is_explained(header_and_posts):
    responses = [chat_response(header_and_posts[0]), chat_response('{"posts": [{"name": "A', finish_reason="length")]
    result = make_extractor(responses).extract(DOC)
    assert result.errors[0].startswith("posts:") and "token limit" in result.errors[0]


def test_openrouter_asks_for_low_reasoning(header_and_posts):
    requests = []
    responses = [chat_response(header_and_posts[0]), chat_response(header_and_posts[1])]
    make_extractor(responses, requests, provider="openrouter").extract(DOC)
    body = json.loads(requests[0].content)
    assert body["reasoning"] == {"effort": "low"} and "reasoning_effort" not in body
    assert str(requests[0].url) == "https://openrouter.ai/api/v1/chat/completions"


def test_client_errors_are_not_retried():
    with pytest.raises(ExtractionError, match="HTTP 401"):
        make_extractor([chat_response("", 401)]).extract(DOC)


@pytest.mark.parametrize(
    "reply",
    ['{"a": 1}', '```json\n{"a": 1}\n```', 'Here you go: {"a": 1} hope that helps'],
)
def test_parse_json_object_tolerates_wrapping(reply):
    assert parse_json_object(reply) == {"a": 1}


def test_parse_json_object_rejects_arrays():
    with pytest.raises(ValueError):
        parse_json_object("[1, 2]")


class StubExtractor:
    def __init__(self, name, outcome):
        self.name, self.model, self.outcome = name, name, outcome
        self.calls = 0

    def extract(self, document):
        self.calls += 1
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def test_fallback_moves_on_only_for_provider_failures():
    good = ExtractionResult("openrouter", "m2", "v1")
    primary = StubExtractor("groq:m1", ExtractionError("quota"))
    secondary = StubExtractor("openrouter:m2", good)
    chain = FallbackExtractor([primary, secondary])
    assert chain.extract(DOC) is good
    assert chain.name == "groq:m1,openrouter:m2"

    bad_output = ExtractionResult("groq", "m1", "v1", errors=["header: invalid"])
    tertiary = StubExtractor("openrouter:m3", good)
    assert FallbackExtractor([StubExtractor("groq:m1", bad_output), tertiary]).extract(DOC) is bad_output
    assert tertiary.calls == 0


def test_fallback_skips_an_exhausted_provider_for_the_rest_of_the_run():
    good = ExtractionResult("openrouter", "m2", "v2")
    primary = StubExtractor("groq:m1", QuotaExhausted("daily"))
    chain = FallbackExtractor([primary, StubExtractor("openrouter:m2", good)])
    assert chain.extract(DOC) is good
    assert chain.extract(DOC) is good
    assert primary.calls == 1


def test_fallback_reports_quota_when_every_provider_is_out():
    chain = FallbackExtractor([StubExtractor("a", QuotaExhausted("x")), StubExtractor("b", QuotaExhausted("y"))])
    with pytest.raises(QuotaExhausted):
        chain.extract(DOC)
    mixed = FallbackExtractor([StubExtractor("a", QuotaExhausted("x")), StubExtractor("b", ExtractionError("down"))])
    with pytest.raises(ExtractionError) as info:
        mixed.extract(DOC)
    assert not isinstance(info.value, QuotaExhausted)  # b may work later today


def test_fallback_raises_when_all_fail():
    chain = FallbackExtractor([StubExtractor("a", ExtractionError("x")), StubExtractor("b", ExtractionError("y"))])
    with pytest.raises(ExtractionError, match="x | y"):
        chain.extract(DOC)


def test_get_extractor_parses_specs():
    env = {"GROQ_API_KEY": "g", "OPENROUTER_API_KEY": "o"}
    single = get_extractor("groq:some-model", env)
    assert isinstance(single, ChatExtractor) and single.model == "some-model"

    chain = get_extractor(" groq:openai/gpt-oss-120b , openrouter:vendor/model:free ", env)
    assert chain.extractors[0].model == "openai/gpt-oss-120b"
    assert isinstance(chain, FallbackExtractor)
    assert chain.extractors[1].model == "vendor/model:free"

    assert get_extractor(None, {**env, "SARKARI_EXTRACTORS": "groq:from-env"}).model == "from-env"


@pytest.mark.parametrize(
    "spec, env, message",
    [
        ("", {}, "no extractor configured"),
        ("groq", {"GROQ_API_KEY": "g"}, "bad extractor spec"),
        ("gemini:x", {}, "unknown provider"),
        ("openrouter:x", {"GROQ_API_KEY": "g"}, "OPENROUTER_API_KEY is not set"),
    ],
)
def test_get_extractor_errors(spec, env, message):
    with pytest.raises(ValueError, match=message):
        get_extractor(spec, env)


def test_missing_title_falls_back_to_the_link_label(header_and_posts):
    header = json.loads(header_and_posts[0])
    header["title"] = None
    document = DocumentText(
        header_pages=[PageText(1, "h")], posts_pages=[PageText(2, "p")],
        title_hint="Research Assistant under Labour Welfare Department",
    )
    result = make_extractor([chat_response(json.dumps(header)), chat_response(header_and_posts[1])]).extract(document)
    assert result.ok and result.draft.title == "Research Assistant under Labour Welfare Department"
    assert "title" in result.draft.low_confidence_fields


def test_missing_title_without_a_hint_still_fails(header_and_posts):
    header = json.loads(header_and_posts[0])
    header["title"] = None
    result = make_extractor([chat_response(json.dumps(header))]).extract(DOC)
    assert result.draft is None and result.errors[0].startswith("header: title")


def test_missing_organisation_comes_from_the_source(header_and_posts):
    header = json.loads(header_and_posts[0])
    header["organisation_name"] = None
    header["document_kind"] = None
    document = DocumentText(header_pages=[PageText(1, "h")], posts_pages=[PageText(2, "p")], organisation_hint="ESIC")
    result = make_extractor([chat_response(json.dumps(header)), chat_response(header_and_posts[1])]).extract(document)
    assert result.ok and result.draft.organisation_name == "ESIC"
    assert result.draft.document_kind.value == "NOTIFICATION"
