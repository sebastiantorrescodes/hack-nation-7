"""Deterministic rules: missing or malformed evidence is unknown, never permission to save."""

import math
from .models import Condition, Skill


class UnknownRecord(ValueError):
    def __init__(self, fields: list[str]):
        self.fields = sorted(set(fields))
        super().__init__('Cannot verify these fields: ' + ', '.join(self.fields))


def _as_list(value) -> list[str]:
    return [str(x).strip().upper() for x in value] if isinstance(value, list) else [str(value).strip().upper()]


def _num(value) -> float | None:
    try:
        result = float(value)
        return result if not isinstance(value, bool) and math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def evaluate(cond: Condition, record: dict) -> bool | None:
    actual = record.get(cond.field)
    if actual is None or isinstance(actual, dict): return None
    vals = [v.strip().upper() for v in cond.values]
    values = _as_list(actual)
    if cond.op in {'is_true','is_false'}:
        if not isinstance(actual, bool): return None
        return actual if cond.op=='is_true' else not actual
    if cond.op in {'gt','lt'}:
        a, b = _num(actual), _num(vals[0] if len(vals)==1 else None)
        if a is None or b is None: return None
        return a>b if cond.op=='gt' else a<b
    if cond.op == 'empty': return not values or values==['']
    if cond.op == 'not_empty': return bool(values) and values!=['']
    if not vals: return None
    if cond.op == 'eq': return values==[vals[0]]
    if cond.op == 'neq': return values!=[vals[0]]
    if cond.op == 'in': return any(v in vals for v in values)
    if cond.op == 'not_in': return not any(v in vals for v in values)
    if cond.op == 'contains': return all(v in values for v in vals)
    if cond.op == 'not_contains': return not any(v in values for v in vals)
    return None


def check(cond: Condition, record: dict) -> bool:
    return evaluate(cond, record) is True


def matches(conds: list[Condition], record: dict) -> bool:
    if not conds: raise UnknownRecord(['skill_trigger'])
    values = [evaluate(c, record) for c in conds]
    if False in values: return False
    if None in values: raise UnknownRecord([c.field for c, v in zip(conds, values) if v is None])
    return True


def triggered_skills(skills: list[Skill], record: dict) -> list[Skill]:
    result, unknown = [], []
    for skill in skills:
        try:
            if matches(skill.trigger, record): result.append(skill)
        except UnknownRecord as exc:
            unknown.extend(exc.fields)
    if unknown: raise UnknownRecord(unknown)
    return result


def guardrail_violations(skills: list[Skill], original: dict, edited: dict) -> list[tuple[Skill,list[Condition]]]:
    result, unknown = [], []
    for skill in triggered_skills(skills, original):
        if not skill.guardrail.must: raise UnknownRecord(['skill_guardrail'])
        values = [evaluate(c, edited) for c in skill.guardrail.must]
        unknown.extend(c.field for c,v in zip(skill.guardrail.must, values) if v is None)
        failed = [c for c,v in zip(skill.guardrail.must,values) if v is not True]
        if failed: result.append((skill,failed))
    if unknown: raise UnknownRecord(unknown)
    return result


def mastery_status(attempts, skill_id: str) -> str:
    rows = [a for a in attempts if a.skill_id==skill_id]
    prediction = next((a for a in rows if a.kind=='prediction'),None)
    save = next((a for a in rows if a.kind=='save_check'),None)
    if prediction and save and prediction.correct and save.correct and rows.index(prediction) < rows.index(save): return 'mastered'
    if rows: return 'practicing'
    return 'not_yet'
