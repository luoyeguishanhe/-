"""Token accounting and explicit, user-priced API cost estimates."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from .translation import TokenUsage


@dataclass(frozen=True)
class Pricing:
    input_per_million: Decimal
    output_per_million: Decimal
    currency: str = "CNY"

    def __post_init__(self):
        for name in ("input_per_million", "output_per_million"):
            try:
                value = Decimal(str(getattr(self, name)))
            except InvalidOperation:
                raise ValueError("单价必须是有效的非负数") from None
            if not value.is_finite() or value < 0:
                raise ValueError("单价必须是有效的非负数")
            object.__setattr__(self, name, value)
        if self.currency not in ("CNY", "USD"):
            raise ValueError("请选择人民币 CNY 或美元 USD")

    def cost(self, usage: TokenUsage) -> Decimal:
        return (usage.prompt_tokens * self.input_per_million
                + usage.completion_tokens * self.output_per_million) / Decimal(1_000_000)


def money_text(value: Decimal) -> str:
    if 0 < value < Decimal("0.000001"):
        return "<0.000001"
    return f"{value:.6f}"


@dataclass
class UsageRecord:
    endpoint: str
    model: str
    pricing: Pricing | None
    usage: TokenUsage = field(default_factory=TokenUsage)
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def cost_summary(self) -> str:
        usage = self.usage
        if not usage.reported_calls and not usage.unreported_calls:
            return "本次费用估算：尚未请求"
        if self.pricing is None:
            return "本次费用估算：未设置单价"
        if not usage.reported_calls:
            return "本次费用估算：未知（服务未返回用量）"
        note = f"（仅已知部分；{usage.unreported_calls} 次调用用量未知）" if usage.unreported_calls else ""
        return f"本次费用估算：{self.pricing.currency} {money_text(self.pricing.cost(usage))}" + note

    def to_dict(self):
        usage = self.usage
        known = self.pricing is not None and usage.reported_calls > 0
        return {
            "started_at": self.started_at, "endpoint": self.endpoint, "model": self.model,
            "reported_input_tokens": usage.prompt_tokens,
            "reported_output_tokens": usage.completion_tokens,
            "reported_total_tokens": usage.prompt_tokens + usage.completion_tokens,
            "reported_calls": usage.reported_calls, "unreported_calls": usage.unreported_calls,
            "currency": self.pricing.currency if self.pricing else None,
            "input_price_per_million": str(self.pricing.input_per_million) if self.pricing else None,
            "output_price_per_million": str(self.pricing.output_per_million) if self.pricing else None,
            "estimated_known_cost": str(self.pricing.cost(usage)) if known else None,
            "estimate_complete": known and usage.unreported_calls == 0,
        }


def usage_report(records: list[UsageRecord]) -> dict:
    totals = TokenUsage()
    costs = {}
    unpriced_calls = 0
    for record in records:
        usage = record.usage
        totals.prompt_tokens += usage.prompt_tokens
        totals.completion_tokens += usage.completion_tokens
        totals.reported_calls += usage.reported_calls
        totals.unreported_calls += usage.unreported_calls
        if record.pricing is None:
            unpriced_calls += usage.reported_calls + usage.unreported_calls
        elif usage.reported_calls:
            currency = record.pricing.currency
            costs[currency] = costs.get(currency, Decimal(0)) + record.pricing.cost(usage)
    return {
        "scope": "current_tool_window", "pricing_unit": "per_1000000_tokens",
        "reported_input_tokens": totals.prompt_tokens,
        "reported_output_tokens": totals.completion_tokens,
        "reported_total_tokens": totals.prompt_tokens + totals.completion_tokens,
        "reported_calls": totals.reported_calls, "unreported_calls": totals.unreported_calls,
        "unpriced_calls": unpriced_calls,
        "estimated_known_cost_by_currency": {key: str(value) for key, value in costs.items()},
        "estimate_complete": totals.unreported_calls == 0 and unpriced_calls == 0,
        "tasks": [record.to_dict() for record in records],
    }


def window_summary(records: list[UsageRecord]) -> str:
    report = usage_report(records)
    tokens = (f"输入 {report['reported_input_tokens']} / 输出 {report['reported_output_tokens']} / "
              f"合计 {report['reported_total_tokens']} tokens")
    costs = "；".join(f"{currency} {money_text(Decimal(amount))}" for currency, amount
                       in report["estimated_known_cost_by_currency"].items()) or "暂无可计价用量"
    notes = []
    if report["unreported_calls"]:
        notes.append(f"{report['unreported_calls']} 次用量未知")
    if report["unpriced_calls"]:
        notes.append(f"{report['unpriced_calls']} 次未设单价")
    if notes:
        costs = "已知部分：" + costs + "（" + "；".join(notes) + "）"
    return f"本窗口已知累计：{tokens}\n累计费用估算：{costs}"
