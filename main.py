from contextlib import asynccontextmanager
from typing import List
from fastapi import FastAPI, HTTPException, Query, Depends
from sqlalchemy.ext.asyncio import AsyncSession
import uvicorn

from database import engine, Base, get_db
from balancer import BalancerService, Order, User
from rule_engine import DynamicRule, Condition

# Инициализация базы данных при старте сервера
@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        print("-> Инициализация таблиц базы данных SQLite...")
        await conn.run_sync(Base.metadata.create_all)
    yield

app = FastAPI(title="Executor Balancer Core API", version="1.0.0", lifespan=lifespan)
balancer = BalancerService()

active_rules: List[DynamicRule] = [
    DynamicRule(
        id="sum_range_rule",
        name="Проверка диапазона сумм заявки",
        conditions=[
            Condition(field="order.sum", operator=">=", target_field="user.min_accept_sum"),
            Condition(field="order.sum", operator="<=", target_field="user.max_accept_sum"),
        ]
    )
]

@app.get("/")
async def root():
    return {"status": "ok", "service": "Executor Balancer"}

@app.post("/api/v1/sync/users", summary="Синхронизация кэша пользователей из АИС")
async def sync_users(users: List[User], db: AsyncSession = Depends(get_db)):
    await balancer.update_users_cache(users, db)
    return {"status": "ok", "synced_count": len(users)}

@app.post("/api/v1/orders/distribute", summary="Распределение входящей заявки")
async def distribute_order(order: Order, db: AsyncSession = Depends(get_db)):
    executor_id = await balancer.select_executor(order, active_rules, db)
    if executor_id is None:
        raise HTTPException(status_code=409, detail="Подходящий исполнитель не найден или лимиты исчерпаны")
    
    return {
        "order_id": order.id,
        "assigned_user_id": executor_id,
        "status": "assigned"
    }

@app.post("/api/v1/orders/{order_id}/release", summary="Уведомление об освобождении слота")
async def release_order_slot(
    order_id: int,
    user_id: int = Query(...),
    weight: float = Query(1.0),
    db: AsyncSession = Depends(get_db)
):
    await balancer.release_slot(user_id, weight, order_id, db)
    return {"status": "ok", "order_id": order_id, "user_id": user_id}

@app.get("/api/v1/metrics", summary="Метрики распределения")
async def get_metrics():
    return {
        "active_slots": balancer.active_slots,
        "daily_counts": balancer.daily_counts
    }

if __name__ == "__main__":
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)