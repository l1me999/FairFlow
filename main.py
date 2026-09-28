from contextlib import asynccontextmanager
from typing import List
from fastapi import FastAPI, HTTPException, Query, Depends
from sqlalchemy.ext.asyncio import AsyncSession
import uvicorn

from database import engine, Base, get_db
from balancer import BalancerService, Order, User
from rule_engine import DynamicRule, Condition

import io
import pandas as pd
from fastapi.responses import StreamingResponse
from sqlalchemy import select, func
from models import UserModel, OrderModel, MetricSnapshotModel

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

@app.post("/api/v1/rules", summary="Добавление правила из конструктора")
async def add_rule(rule: DynamicRule):
    active_rules.append(rule)
    return {"status": "ok", "rule_id": rule.id}

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

@app.post("/api/v1/orders/{order_id}/release")
async def release_order_slot(
    order_id: int,
    user_id: int = Query(...),
    weight: float = Query(1.0),
    final_status: str = Query("accept"),
    db: AsyncSession = Depends(get_db)
):
    await balancer.release_slot(user_id, weight, order_id, final_status, db)
    return {"status": "ok", "order_id": order_id}

@app.get("/api/v1/metrics", summary="Метрики распределения")
async def get_metrics():
    return {
        "active_slots": balancer.active_slots,
        "daily_counts": balancer.daily_counts
    }

@app.put("/api/v1/users/{user_id}", summary="Точечное обновление настроек исполнителя из АИС")
async def update_single_user(user_id: int, user: User, db: AsyncSession = Depends(get_db)):
    if user_id != user.id:
        raise HTTPException(status_code=400, detail="ID в пути и теле запроса не совпадают")
    
    await balancer.update_users_cache([user], db)
    return {"status": "ok", "updated_user_id": user.id}

@app.post("/api/v1/metrics/snapshot", summary="Сгенерировать и сохранить срез агрегированных метрик")
async def create_metric_snapshot(db: AsyncSession = Depends(get_db)):
    users_result = await db.execute(select(func.count(UserModel.id)).where(UserModel.status == "active"))
    active_users = users_result.scalar() or 0

    processed_result = await db.execute(select(func.count(OrderModel.id)).where(OrderModel.status == "processed"))
    accepted_result = await db.execute(select(func.count(OrderModel.id)).where(OrderModel.status == "accept"))
    
    processed_orders = processed_result.scalar() or 0
    accepted_orders = accepted_result.scalar() or 0

    total_load = sum(balancer.active_slots.values())
    avg_load = total_load / active_users if active_users > 0 else 0.0

    snapshot = MetricSnapshotModel(
        total_active_users=active_users,
        total_orders_processed=processed_orders,
        total_orders_accepted=accepted_orders,
        average_user_load=avg_load
    )
    db.add(snapshot)
    await db.commit()
    
    return {"status": "ok", "snapshot_id": snapshot.id}

@app.get("/api/v1/metrics/excel", summary="Выгрузка метрик в Excel")
async def export_metrics_excel(db: AsyncSession = Depends(get_db)):
    users_data = []
    for user_id, user in balancer.users.items():
        users_data.append({
            "ID Исполнителя": user.id,
            "Статус": user.status,
            "Пропускная способность": user.settings.capacity,
            "Текущая нагрузка (Вес)": balancer.active_slots.get(user_id, 0.0),
            "Выполнено за сегодня": balancer.daily_counts.get(user_id, 0)
        })
    
    df = pd.DataFrame(users_data)
    
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Нагрузка исполнителей")
        
        result = await db.execute(select(MetricSnapshotModel).order_by(MetricSnapshotModel.id.desc()).limit(100))
        snapshots = result.scalars().all()
        if snapshots:
            snap_df = pd.DataFrame([{
                "Дата/Время": s.created_at.strftime("%Y-%m-%d %H:%M:%S") if s.created_at else "",
                "Активных юзеров": s.total_active_users,
                "В процессе": s.total_orders_processed,
                "Завершено": s.total_orders_accepted,
                "Средняя нагрузка": round(s.average_user_load, 2)
            } for s in snapshots])
            snap_df.to_excel(writer, index=False, sheet_name="Агрегированные метрики")

    output.seek(0)
    
    headers = {
        'Content-Disposition': 'attachment; filename="fairflow_metrics.xlsx"'
    }
    return StreamingResponse(output, headers=headers, media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

if __name__ == "__main__":
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)