"""記録したトークン数から、従量課金だった場合の料金を集計する"""

from __future__ import annotations

import csv
import io
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable

from .pricing import PriceTable, TokenUsage
from .store import Store

JPY_PER_USD = 150


@dataclass
class Summary:
    month: str | None
    count: int = 0
    errors: int = 0
    total_usd: float = 0.0
    costs: list[float] = field(default_factory=list)
    unpriced: int = 0
    by_model: dict[str, TokenUsage] = field(default_factory=dict)
    by_user: dict[str, tuple[int, float]] = field(default_factory=dict)
    what_if: dict[str, float] = field(default_factory=dict)
    auth_modes: dict[str, int] = field(default_factory=dict)
    good: int = 0
    bad: int = 0

    @property
    def avg_usd(self) -> float:
        return statistics.fmean(self.costs) if self.costs else 0.0


def summarize(store: Store, prices: PriceTable, month: str | None = None) -> Summary:
    rows = store.questions(month)
    s = Summary(month=month, count=len(rows))
    users: dict[str, list[float]] = defaultdict(lambda: [0, 0.0])
    modes: dict[str, int] = defaultdict(int)
    for r in rows:
        modes[r["auth_mode"]] += 1
        if r["is_error"]:
            s.errors += 1
        cost = r["virtual_cost_usd"]
        users[r["user_id"]][0] += 1
        if cost is None:
            s.unpriced += 1
            continue
        s.costs.append(cost)
        s.total_usd += cost
        users[r["user_id"]][1] += cost
    s.by_user = {u: (int(v[0]), v[1]) for u, v in users.items()}
    s.auth_modes = dict(modes)

    by_model: dict[str, TokenUsage] = defaultdict(TokenUsage)
    for m in store.model_rows(month):
        by_model[m["model"]] += TokenUsage(
            m["input_tokens"], m["output_tokens"], m["cache_write_tokens"], m["cache_read_tokens"]
        )
    s.by_model = dict(by_model)

    for f in store.feedback_rows(month):
        if f["rating"] > 0:
            s.good += 1
        else:
            s.bad += 1

    # 同じトークン数を別のモデルの単価で数え直した場合の料金(実際にはモデルでトークン数も変わる点に注意)
    total_usage = sum(s.by_model.values(), TokenUsage())
    for name, price in prices.prices.items():
        s.what_if[name] = price.cost(total_usage)
    return s


def _usd(v: float) -> str:
    return f"${v:,.2f}(約{v * JPY_PER_USD:,.0f}円)"


def format_summary(s: Summary, projections: list[int] = (300, 600, 1500),
                   user_label: Callable[[str], str] = str) -> str:
    title = s.month or "全期間"
    lines = [f"■ 仮想料金レポート({title})"]
    if s.count == 0:
        lines.append("記録された質問はありません")
        return "\n".join(lines)

    lines += [
        f"質問数: {s.count} 件(エラー {s.errors} 件)  認証モード: "
        + ", ".join(f"{k}={v}" for k, v in sorted(s.auth_modes.items())),
        f"合計: {_usd(s.total_usd)}",
        f"1件あたり: 平均 ${s.avg_usd:.3f} / 最大 ${max(s.costs) if s.costs else 0:.3f}",
    ]
    if s.good or s.bad:
        lines.append(f"評価: 👍 {s.good} 件 / 👎 {s.bad} 件(👍 の割合 {s.good / (s.good + s.bad):.0%})")
    if s.unpriced:
        lines.append(f"※ 単価表にないモデルを含む {s.unpriced} 件は合計から除いています(config/pricing.toml に追加してください)")

    lines.append("")
    lines.append("モデル別トークン数(入力 / 出力 / キャッシュ書込 / キャッシュ読出)")
    for model, u in sorted(s.by_model.items()):
        lines.append(f"  {model}: {u.input:,} / {u.output:,} / {u.cache_write:,} / {u.cache_read:,}")

    lines.append("")
    lines.append("月の質問数ごとの見込み(1件あたり平均 × 件数)")
    for n in projections:
        lines.append(f"  {n:,} 件/月: {_usd(s.avg_usd * n)}")

    if s.what_if:
        lines.append("")
        lines.append("同じトークン数を各モデルの単価で数え直した場合の合計(参考)")
        for model, cost in sorted(s.what_if.items(), key=lambda kv: kv[1]):
            lines.append(f"  {model}: {_usd(cost)}")

    if len(s.by_user) > 1:
        lines.append("")
        lines.append("利用者別")
        for user, (n, cost) in sorted(s.by_user.items(), key=lambda kv: -kv[1][1]):
            lines.append(f"  {user_label(user)}: {n} 件 / ${cost:.2f}")
    return "\n".join(lines)


def export_csv(store: Store, month: str | None = None) -> str:
    rows = store.questions(month)
    models = defaultdict(list)
    for m in store.model_rows(month):
        models[m["question_id"]].append(m)
    ratings: dict[int, list] = defaultdict(list)
    for f in store.feedback_rows(month):
        ratings[f["question_id"]].append(f)

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([
        "id", "asked_at_utc", "user_id", "auth_mode", "auth_source", "cli_version", "configured_model", "resumed",
        "num_turns", "duration_ms", "is_error", "subtype", "permission_denials", "attachments",
        "input_tokens", "output_tokens", "cache_write_tokens", "cache_read_tokens",
        "virtual_cost_usd", "sdk_cost_usd", "models", "good", "bad", "bad_reasons", "question", "answer",
    ])
    for r in rows:
        ms = models.get(r["id"], [])
        fs = ratings.get(r["id"], [])
        w.writerow([
            r["id"], r["asked_at"], r["user_id"], r["auth_mode"], r["auth_source"], r["cli_version"], r["configured_model"],
            r["resumed"], r["num_turns"], r["duration_ms"], r["is_error"], r["subtype"], r["permission_denials"],
            r["attachments"],
            sum(m["input_tokens"] for m in ms), sum(m["output_tokens"] for m in ms),
            sum(m["cache_write_tokens"] for m in ms), sum(m["cache_read_tokens"] for m in ms),
            r["virtual_cost_usd"], r["sdk_cost_usd"], " ".join(sorted({m["model"] for m in ms})),
            sum(1 for f in fs if f["rating"] > 0), sum(1 for f in fs if f["rating"] < 0),
            " / ".join(f["reason"] for f in fs if f["rating"] < 0 and f["reason"]), r["question"], r["answer"],
        ])
    return buf.getvalue()
