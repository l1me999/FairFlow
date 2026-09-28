from typing import Any, Dict, List, Optional
from pydantic import BaseModel

class Condition(BaseModel):
    field: str
    operator: str
    target_field: Optional[str] = None
    constant: Optional[Any] = None

class DynamicRule(BaseModel):
    id: str
    name: str
    conditions: List[Condition]

class RuleEngine:
    @staticmethod
    def _extract_val(data: Dict[str, Any], path: str) -> Any:
        keys = path.split(".")
        current = data
        for k in keys:
            if isinstance(current, dict) and k in current:
                current = current[k]
            else:
                return None
        return current

    @classmethod
    def evaluate(cls, rule: DynamicRule, order: Dict[str, Any], user: Dict[str, Any]) -> bool:
        context = {"order": order, "user": user}
        
        for cond in rule.conditions:
            left_val = cls._extract_val(context, cond.field)
            right_val = (
                cls._extract_val(context, cond.target_field)
                if cond.target_field
                else cond.constant
            )

            # Если поле не задано ни у заявки, ни у исполнителя — не блокируем заявку
            if left_val is None or right_val is None:
                continue

            op = cond.operator
            try:
                if op == "==" and not (str(left_val) == str(right_val)):
                    return False
                elif op == "!=" and not (str(left_val) != str(right_val)):
                    return False
                elif op == ">" and not (float(left_val) > float(right_val)):
                    return False
                elif op == "<" and not (float(left_val) < float(right_val)):
                    return False
                elif op == ">=" and not (float(left_val) >= float(right_val)):
                    return False
                elif op == "<=" and not (float(left_val) <= float(right_val)):
                    return False
                elif op == "in" and not (left_val in right_val):
                    return False
            except (ValueError, TypeError):
                return False
        return True