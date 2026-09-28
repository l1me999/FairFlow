import asyncio
from typing import Dict, List, Optional, Any
from pydantic import BaseModel, Field

from rule_engine import DynamicRule, RuleEngine

class ParameterDefinition(BaseModel):
    name: str              # например, "category" или "vip"
    display_name: str      # "Категория обращений"
    data_type: str         # "string", "number", "boolean", "list"
    default_value: Any     # Значение по умолчанию для всех: "STANDARD", 0, false и т.д.
    entity: str = "both"   # "user", "order", "both"

class Order(BaseModel):
    id: int
    parent_id: Optional[int] = None
    sum: int
    order_type: str
    weight: float = Field(default=1.0, ge=0.1)  # Сложность/важность заявки
    status: str = "processed"
    dynamic_params: Dict[str, Any] = {}


class UserSettings(BaseModel):
    user_id: int
    max_daily_limit: Optional[int] = None
    capacity: float = 1.0  # Квалификация / пропускная способность
    dynamic_params: Dict[str, Any] = {}


class User(BaseModel):
    id: int
    status: str  # "active" / "inactive"
    settings: UserSettings


class BalancerService:
    def __init__(self):
        self._lock = asyncio.Lock()
        self.users: Dict[int, User] = {}
        self.active_slots: Dict[int, float] = {}
        self.daily_counts: Dict[int, int] = {}
        self.order_history: Dict[int, int] = {}
        # Реестр доступных параметров
        self.parameters: Dict[str, ParameterDefinition] = {}

    async def register_parameter(self, param: ParameterDefinition):
        """Регистрирует новый параметр и автоматически проставляет default_value всем существующим исполнителям."""
        async with self._lock:
            self.parameters[param.name] = param
            # Автоматически проставляем параметр всем исполнителям в кэше
            if param.entity in ("user", "both"):
                for u in self.users.values():
                    if param.name not in u.settings.dynamic_params:
                        u.settings.dynamic_params[param.name] = param.default_value

    async def set_user_param(self, user_id: int, param_name: str, value: Any):
        """Точечное изменение значения параметра конкретного исполнителя через интерфейс."""
        async with self._lock:
            if user_id in self.users:
                self.users[user_id].settings.dynamic_params[param_name] = value

    async def batch_set_param(self, param_name: str, value: Any):
        """Массовое присвоение нового значения параметра абсолютно всем исполнителям."""
        async with self._lock:
            for u in self.users.values():
                u.settings.dynamic_params[param_name] = value

    async def update_users_cache(self, users: List[User]):
        async with self._lock:
            for u in users:
                self.users[u.id] = u
                self.active_slots.setdefault(u.id, 0.0)
                self.daily_counts.setdefault(u.id, 0)

    async def select_executor(
        self, order: Order, rules: List[DynamicRule]
    ) -> Optional[int]:
        async with self._lock:
            # 1. Проверка parent_id (связанная родительская заявка)
            if order.parent_id and order.parent_id in self.order_history:
                prev_user_id = self.order_history[order.parent_id]
                prev_user = self.users.get(prev_user_id)
                
                # Исполнитель должен быть активен и подходить под правила (суточный лимит игнорируется)
                if prev_user and prev_user.status == "active":
                    if self._matches_all_rules(order, prev_user, rules):
                        self._assign(prev_user.id, order)
                        return prev_user.id

            # 2. Фильтрация доступных кандидатов
            candidates = []
            for u in self.users.values():
                if u.status != "active":
                    continue
                # Проверка суточного лимита
                daily_limit = u.settings.max_daily_limit
                if daily_limit is not None and self.daily_counts[u.id] >= daily_limit:
                    continue
                # Проверка динамических правил
                if not self._matches_all_rules(order, u, rules):
                    continue
                
                candidates.append(u)

            if not candidates:
                return None

            # 3. Скоринг и выбор наименее загруженного исполнителя
            best_candidate = min(
                candidates,
                key=lambda u: (
                    (self.active_slots[u.id] + order.weight) / max(u.settings.capacity, 0.1),
                    self.daily_counts[u.id]
                )
            )

            # 4. Атомарная фиксация слота
            self._assign(best_candidate.id, order)
            return best_candidate.id

    def _matches_all_rules(self, order: Order, user: User, rules: List[DynamicRule]) -> bool:
        # Для Pydantic v2 используется model_dump(), для v1 — dict()
        dump_order = order.model_dump() if hasattr(order, "model_dump") else order.dict()
        dump_user = user.model_dump() if hasattr(user, "model_dump") else user.dict()
        dump_settings = user.settings.model_dump() if hasattr(user.settings, "model_dump") else user.settings.dict()

        order_dict = {**dump_order, **order.dynamic_params}
        user_dict = {**dump_user, **dump_settings, **user.settings.dynamic_params}

        for rule in rules:
            if not RuleEngine.evaluate(rule, order_dict, user_dict):
                return False
        return True

    def _assign(self, user_id: int, order: Order):
        self.active_slots[user_id] += order.weight
        self.daily_counts[user_id] += 1
        self.order_history[order.id] = user_id

    async def release_slot(self, user_id: int, order_weight: float):
        """Освобождение слота при завершении обработки заявки."""
        async with self._lock:
            if user_id in self.active_slots:
                self.active_slots[user_id] = max(0.0, self.active_slots[user_id] - order_weight)