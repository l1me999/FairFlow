import asyncio
from typing import Dict, List, Optional, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from pydantic import BaseModel, Field

from rule_engine import DynamicRule, RuleEngine
from models import UserModel, UserSettingsModel, OrderModel

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

    async def update_users_cache(self, users: List[User], db: AsyncSession):
        async with self._lock:
            for u in users:
                self.users[u.id] = u
                self.active_slots.setdefault(u.id, 0.0)
                self.daily_counts.setdefault(u.id, 0)
                
                result = await db.execute(
                    select(UserModel).options(selectinload(UserModel.settings)).where(UserModel.id == u.id)
                )
                db_user = result.scalar_one_or_none()
                
                if not db_user:
                    db_user = UserModel(id=u.id, status=u.status)
                    db_settings = UserSettingsModel(
                        user_id=u.id,
                        max_daily_limit=u.settings.max_daily_limit,
                        capacity=u.settings.capacity,
                        dynamic_params=u.settings.dynamic_params
                    )
                    db_user.settings = db_settings
                    db.add(db_user)
                else:
                    db_user.status = u.status
                    if db_user.settings:
                        db_user.settings.max_daily_limit = u.settings.max_daily_limit
                        db_user.settings.capacity = u.settings.capacity
                        db_user.settings.dynamic_params = u.settings.dynamic_params
                        
            await db.commit()

    async def select_executor(self, order: Order, rules: List[DynamicRule], db: AsyncSession) -> Optional[int]:
        assigned_user_id = None
        
        async with self._lock:
            if order.parent_id and order.parent_id in self.order_history:
                prev_user_id = self.order_history[order.parent_id]
                prev_user = self.users.get(prev_user_id)
                if prev_user and prev_user.status == "active":
                    if self._matches_all_rules(order, prev_user, rules):
                        assigned_user_id = prev_user.id

            if not assigned_user_id:
                candidates = []
                for u in self.users.values():
                    if u.status != "active": continue
                    limit = u.settings.max_daily_limit
                    if limit is not None and self.daily_counts[u.id] >= limit: continue
                    if not self._matches_all_rules(order, u, rules): continue
                    candidates.append(u)

                if candidates:
                    best_candidate = min(
                        candidates,
                        key=lambda u: (
                            (self.active_slots[u.id] + order.weight) / max(u.settings.capacity, 0.1),
                            self.daily_counts[u.id]
                        )
                    )
                    assigned_user_id = best_candidate.id

            if assigned_user_id:
                self.active_slots[assigned_user_id] += order.weight
                self.daily_counts[assigned_user_id] += 1
                self.order_history[order.id] = assigned_user_id

        if assigned_user_id:
            new_order = OrderModel(
                id=order.id,
                parent_id=order.parent_id,
                executor_id=assigned_user_id,
                sum=order.sum,
                order_type=order.order_type,
                weight=order.weight,
                status="processed",
                dynamic_params=order.dynamic_params
            )
            await db.merge(new_order)
            await db.commit()
            
        return assigned_user_id

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

    async def release_slot(self, user_id: int, order_weight: float, order_id: int, final_status: str, db: AsyncSession):
        async with self._lock:
            if user_id in self.active_slots:
                self.active_slots[user_id] = max(0.0, self.active_slots[user_id] - order_weight)
                
        result = await db.execute(select(OrderModel).where(OrderModel.id == order_id))
        db_order = result.scalar_one_or_none()
        if db_order:
            db_order.status = final_status
            await db.commit()