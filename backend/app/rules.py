"""Deterministic evaluation of skill triggers and guardrails against a claim."""

from .models import Condition, Skill


def _as_list(v) -> list[str]:
    if v is None:
        return []
    if isinstance(v, list):
        return [str(x).strip().upper() for x in v]
    return [str(v).strip().upper()]


def _num(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def check(cond: Condition, claim: dict) -> bool:
    actual = claim.get(cond.field)
    vals = [v.strip().upper() for v in cond.values]
    actual_list = _as_list(actual)

    match cond.op:
        case "eq":
            return bool(vals) and actual_list == [vals[0]]
        case "neq":
            return not vals or actual_list != [vals[0]]
        case "in":
            return any(a in vals for a in actual_list)
        case "not_in":
            return not any(a in vals for a in actual_list)
        case "contains":
            return all(v in actual_list for v in vals)
        case "not_contains":
            return not any(v in actual_list for v in vals)
        case "gt" | "lt":
            a, b = _num(actual), _num(vals[0] if vals else None)
            if a is None or b is None:
                return False
            return a > b if cond.op == "gt" else a < b
        case "is_true":
            return actual is True
        case "is_false":
            return actual is False or actual is None
        case "empty":
            return not actual_list or actual_list == [""]
        case "not_empty":
            return bool(actual_list) and actual_list != [""]
    return False


def matches(conds: list[Condition], claim: dict) -> bool:
    return all(check(c, claim) for c in conds)


def triggered_skills(skills: list[Skill], claim: dict) -> list[Skill]:
    return [s for s in skills if matches(s.trigger, claim)]


def guardrail_violations(skills: list[Skill], original: dict, edited: dict) -> list[tuple[Skill, list[Condition]]]:
    """Triggers are evaluated on the claim as it arrived (before the learner touched it);
    guardrails are evaluated on the claim the learner is about to save."""
    out = []
    for s in triggered_skills(skills, original):
        failed = [c for c in s.guardrail.must if not check(c, edited)]
        if failed:
            out.append((s, failed))
    return out
