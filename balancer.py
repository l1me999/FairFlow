import asyncio
from typing import Dict, List, Optional, Any
from pydantic import BaseModel, Field

from rule_engine import DynamicRule, RuleEngine


class ParameterDefinition(BaseModel):
    name: str              # Например: "priority", "region", "category"
    display_name: str      # "Приоритет обращения"
    data_type: str         # "string", "number", "boolean"
    default_value: Any     # "NORMAL", 1, False
    entity: str = "order"  # "user", "order", "both"


class Order(BaseModel):
    id: int
    parent_id: Optional[int] = None
    sum: int
    order_type: str
    weight: float = Field(default=1.0, ge=0.1)
    status: str = "processed"
    dynamic_params: Dict[str, Any] = {}


class UserSettings(BaseModel):
    user_id: int
    max_daily_limit: Optional[int] = None
    capacity: float = 1.0
    dynamic_params: Dict[str, Any] = {}


class User(BaseModel):
    id: int
    status: str
    settings: UserSettings


class BalancerService:
    def __init__(self):
        self._lock = asyncio.Lock()
        self.users: Dict[int, User] = {}
        self.active_slots: Dict[int, float] = {}
        self.daily_counts: Dict[int, int] = {}
        self.order_history: Dict[int, int] = {}
        # Реестры параметров
        self.user_parameters: Dict[str, ParameterDefinition] = {}
        self.order_parameters: Dict[str, ParameterDefinition] = {}

    async def register_parameter(self, param: ParameterDefinition):
        """Регистрирует параметр для исполнителей, заявок или обоих сразу."""
        async with self._lock:
            if param.entity in ("user", "both"):
                self.user_parameters[param.name] = param
                for u in self.users.values():
                    if param.name not in u.settings.dynamic_params:
                        u.settings.dynamic_params[param.name] = param.default_value

            if param.entity in ("order", "both"):
                self.order_parameters[param.name] = param

    def enrich_order(self, order: Order) -> Order:
        """Автоматически подставляет значения по умолчанию для всех зарегистрированных параметров заявки."""
        for name, param in self.order_parameters.items():
            if name not in order.dynamic_params:
                order.dynamic_params[name] = param.default_value
        return order

    async def update_users_cache(self, users: List[User]):
        async with self._lock:
            for u in users:
                self.users[u.id] = u
                self.active_slots.setdefault(u.id, 0.0)
                self.daily_counts.setdefault(u.id, 0)
                # Проставляем зарегистрированные параметры пользователей
                for p in self.user_parameters.values():
                    u.settings.dynamic_params.setdefault(p.name, p.default_value)

    async def set_user_param(self, user_id: int, param_name: str, value: Any):
        async with self._lock:
            if user_id in self.users:
                self.users[user_id].settings.dynamic_params[param_name] = value

    async def select_executor(
        self, order: Order, rules: List[DynamicRule]
    ) -> Optional[int]:
        # Автоматическое обогащение параметров заявки
        order = self.enrich_order(order)

        async with self._lock:
            # 1. Проверка parent_id (связанная родительская заявка)
            if order.parent_id and order.parent_id in self.order_history:
                prev_user_id = self.order_history[order.parent_id]
                prev_user = self.users.get(prev_user_id)
                if prev_user and prev_user.status == "active":
                    if self._matches_all_rules(order, prev_user, rules):
                        self._assign(prev_user.id, order)
                        return prev_user.id

            # 2. Фильтрация доступных кандидатов
            candidates = []
            for u in self.users.values():
                if u.status != "active":
                    continue
                daily_limit = u.settings.max_daily_limit
                if daily_limit is not None and self.daily_counts[u.id] >= daily_limit:
                    continue
                if not self._matches_all_rules(order, u, rules):
                    continue
                candidates.append(u)

            if not candidates:
                return None

            # 3. Скоринг и взвешенная балансировка нагрузки
            best_candidate = min(
                candidates,
                key=lambda u: (
                    (self.active_slots[u.id] + order.weight) / max(u.settings.capacity, 0.1),
                    self.daily_counts[u.id]
                )
            )

            # 4. Фиксация слота
            self._assign(best_candidate.id, order)
            return best_candidate.id

    def _matches_all_rules(self, order: Order, user: User, rules: List[DynamicRule]) -> bool:
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
        async with self._lock:
            if user_id in self.active_slots:
                self.active_slots[user_id] = max(0.0, self.active_slots[user_id] - order_weight)