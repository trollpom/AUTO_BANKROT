"""Deterministic scenario arithmetic. Missing assumptions never become zero."""
from decimal import Decimal, ROUND_FLOOR, ROUND_HALF_UP

from .schemas import Costs

FIXED = ("transport_rub", "inspection_rub", "fees_fixed_rub", "storage_rub",
         "other_rub", "tax_reserve_rub", "risk_reserve_rub")
CORE = FIXED + ("buyer_fee_pct", "selling_fee_pct", "resale_discount_pct",
                "capital_rate_pct_year", "holding_months")


def money(value):
    return float(value.quantize(Decimal(".01"), rounding=ROUND_HALF_UP))


def calculate(market, costs: Costs, bid_price):
    missing = [name for name in CORE if getattr(costs, name) is None]
    if bid_price is None:
        missing.append("bid_price_rub")
    if market.get("status") != "estimated":
        missing.append("market_comparables")
    scenarios = []
    for name, price_key, repair_key in (("conservative", "low_rub", "repair_high_rub"),
                                        ("base", "median_rub", "repair_base_rub"),
                                        ("optimistic", "high_rub", "repair_low_rub")):
        scenario_missing = missing + ([repair_key] if getattr(costs, repair_key) is None else [])
        row = {"name": name, "status": "incomplete", "missing": scenario_missing}
        if not scenario_missing:
            asking = Decimal(str(market[price_key]))
            repair = getattr(costs, repair_key)
            fixed = sum((getattr(costs, x) for x in FIXED), Decimal(0)) + repair
            purchase_factor = 1 + costs.buyer_fee_pct / 100
            holding_factor = costs.capital_rate_pct_year * costs.holding_months / 1200
            resale = asking * (1 - costs.resale_discount_pct / 100)
            net_resale = resale * (1 - costs.selling_fee_pct / 100)
            before_capital = Decimal(str(bid_price)) * purchase_factor + fixed
            capital_cost = before_capital * holding_factor
            total = before_capital + capital_cost
            profit = net_resale - total
            def ceiling(target):
                raw = ((net_resale - target) / (1 + holding_factor) - fixed) / purchase_factor
                return {"bid_rub": float(max(Decimal(0), raw).quantize(Decimal(".01"), rounding=ROUND_FLOOR)),
                        "feasible": raw > 0}
            row.update(status="calculated", asking_price_rub=money(asking), resale_price_rub=money(resale),
                       net_resale_rub=money(net_resale), repair_rub=money(repair), fixed_costs_rub=money(fixed),
                       capital_cost_rub=money(capital_cost), total_cost_rub=money(total), profit_rub=money(profit),
                       roi_pct=money(profit / total * 100) if total else None,
                       break_even_bid=ceiling(Decimal(0)),
                       max_bid_for_target=ceiling(costs.target_profit_rub) if costs.target_profit_rub is not None else None)
        scenarios.append(row)
    conservative = scenarios[0]
    if conservative["status"] != "calculated":
        decision = "insufficient_data"
    elif conservative["profit_rub"] <= 0:
        decision = "conservative_loss_or_break_even"
    elif costs.target_profit_rub is None:
        decision = "positive_under_assumptions"
    elif Decimal(str(conservative["profit_rub"])) >= costs.target_profit_rub:
        decision = "target_met_under_assumptions"
    else:
        decision = "below_target_under_assumptions"
    return {"decision": decision, "bid_price_rub": float(bid_price) if bid_price is not None else None,
            "assumptions": costs.model_dump(mode="json"), "scenarios": scenarios,
            "winning_probability": {"value": None, "status": "unknown",
                                    "reason": "Нет проверенной истории ставок, участников и итогов сопоставимых торгов"},
            "notes": ["Сценарии используют цены объявлений и ваши допущения; это не прогноз цены сделки.",
                      "Стоимость капитала: простые проценты на покупку и все заданные расходы за срок владения.",
                      "Налоги, ремонт и комиссии задаются пользователем. Возвратный задаток не считается расходом автоматически.",
                      "Расчёт не подтверждает допуск к торгам, юридическую чистоту или отсутствие скрытых неисправностей."]}
