import pytest

from qa_bot.agent import parse_model_usage
from qa_bot.pricing import TokenUsage
from qa_bot.report import export_csv, format_summary, summarize
from qa_bot.store import QuestionRecord, Store


def test_単価から料金を計算する(prices):
    u = TokenUsage(input=1_000_000, output=1_000_000, cache_write=1_000_000, cache_read=1_000_000)
    assert prices.cost("claude-sonnet-5-5", u) == pytest.approx(2 + 10 + 2.5 + 0.2)


def test_日付付きのモデルIDも引ける(prices):
    assert prices.find("claude-haiku-4-5-20251001") is prices.prices["claude-haiku-4-5"]
    # claude-opus-5 より長い claude-opus-5-5 を優先する
    assert prices.find("claude-opus-5-5") is prices.prices["claude-opus-5-5"]
    assert prices.find("unknown-model") is None


def test_SDKのmodelUsageを読み取る():
    usage = parse_model_usage({
        "claude-sonnet-5-5": {
            "inputTokens": 10, "outputTokens": 20, "cacheReadInputTokens": 30, "cacheCreationInputTokens": 40,
            "webSearchRequests": 0, "costUSD": 0.1, "contextWindow": 1, "maxOutputTokens": 1,
        }
    })
    assert usage["claude-sonnet-5-5"] == TokenUsage(input=10, output=20, cache_write=40, cache_read=30)


def record(user: str, cost: float | None, model: str = "claude-sonnet-5-5", **kw) -> QuestionRecord:
    u = TokenUsage(1000, 2000, 3000, 4000)
    base = dict(
        user_id=user, channel=None, thread_key=None, question="質問", answer="回答", answer_chars=2, is_error=False,
        subtype="success", num_turns=3, duration_ms=100, auth_mode="subscription", auth_source="none",
        configured_model=model, session_id="s", resumed=False, permission_denials=0, sdk_cost_usd=cost,
        virtual_cost_usd=cost, model_usage={model: (u, cost)},
    )
    base.update(kw)
    return QuestionRecord(**base)


def test_集計と見込みと評価(prices, tmp_path):
    store = Store(tmp_path / "db.sqlite3")
    q1 = store.add_question(record("A", 0.10))
    q2 = store.add_question(record("A", 0.30))
    store.add_question(record("B", None, model="unknown"))
    store.set_feedback(q1, "A", 1)
    store.set_feedback(q2, "A", -1, "バージョンが違う")

    s = summarize(store, prices)
    assert s.count == 3 and s.unpriced == 1
    assert s.total_usd == pytest.approx(0.40)
    assert s.avg_usd == pytest.approx(0.20)
    assert s.by_model["claude-sonnet-5-5"] == TokenUsage(2000, 4000, 6000, 8000)
    assert s.what_if["claude-haiku-4-5"] < s.what_if["claude-opus-5-5"]
    assert (s.good, s.bad) == (1, 1)

    text = format_summary(s, [100])
    assert "100 件/月: $20.00" in text
    assert "👍 の割合 50%" in text
    assert "単価表にないモデル" in text
    assert "中央値" not in text and "90%点" not in text
    assert "  A: 2 件" in text
    assert "  <A>: 2 件" in format_summary(s, [100], user_label=lambda u: f"<{u}>")

    lines = export_csv(store).strip().splitlines()
    assert lines[0].startswith("id,asked_at_utc") and len(lines) == 4
    assert "バージョンが違う" in lines[2]


def test_記録がなければその旨を表示する(prices, tmp_path):
    assert "記録された質問はありません" in format_summary(summarize(Store(tmp_path / "db.sqlite3"), prices))
