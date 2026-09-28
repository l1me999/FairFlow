import asyncio
import random
from typing import Dict, List, Optional, Any
from fastapi import FastAPI, HTTPException, BackgroundTasks
from pydantic import BaseModel
import httpx
import uvicorn

app = FastAPI(title="External AIS Mock Server", version="1.0.0")

BALANCER_URL = "http://127.0.0.1:8000"

# --- Хранилище данных АИС в памяти ---
users_db: Dict[int, Dict[str, Any]] = {
    1: {
        "id": 1,
        "first_name": "Иван",
        "last_name": "Иванов",
        "status": "active",
        "settings": {
            "user_id": 1,
            "min_accept_sum": 0,
            "max_accept_sum": 500000,
            "order_type": "ORDER_1",
            "capacity": 2.0,
            "max_daily_limit": 100,
            "dynamic_params": {"category": "VIP_CLIENTS", "priority": "HIGH"}
        }
    },
    2: {
        "id": 2,
        "first_name": "Петр",
        "last_name": "Петров",
        "status": "active",
        "settings": {
            "user_id": 2,
            "min_accept_sum": 1000,
            "max_accept_sum": 200000,
            "order_type": "ORDER_1",
            "capacity": 1.0,
            "max_daily_limit": 50,
            "dynamic_params": {"category": "STANDARD", "priority": "NORMAL"}
        }
    },
    3: {
        "id": 3,
        "first_name": "Анна",
        "last_name": "Сидорова",
        "status": "active",
        "settings": {
            "user_id": 3,
            "min_accept_sum": 5000,
            "max_accept_sum": 50000,
            "order_type": "ORDER_1",
            "capacity": 0.5,
            "max_daily_limit": 10,
            "dynamic_params": {"category": "STANDARD", "priority": "NORMAL"}
        }
    }
}

orders_db: Dict[int, Dict[str, Any]] = {}

# --- Фоновая задача с задержкой 2-10 сек по условиям кейса ---
async def delayed_order_commit(order_id: int, user_id: int):
    delay = random.uniform(2.0, 5.0)  # симуляция фиксации в финальной БД АИС
    await asyncio.sleep(delay)
    if order_id in orders_db:
        orders_db[order_id]["assigned_user_id"] = user_id
        orders_db[order_id]["status"] = "assigned_in_db"
        print(f"[AIS DB] Заявка #{order_id} окончательно зафиксирована за User #{user_id} спустя {delay:.1f} сек!")

# --- Эндпоинты CRUD пользователей и настроек ---

@app.get("/api/v1/ais/users", summary="Выгрузка всех пользователей для Balancer")
async def get_all_users():
    return list(users_db.values())

@app.get("/api/v1/ais/users/{user_id}")
async def get_user(user_id: int):
    if user_id not in users_db:
        raise HTTPException(status_code=404, detail="User not found")
    return users_db[user_id]

@app.patch("/api/v1/ais/users/{user_id}/settings", summary="Изменение настроек пользователя в АИС")
async def update_user_settings(user_id: int, updates: Dict[str, Any]):
    if user_id not in users_db:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Обновляем локально в АИС
    user = users_db[user_id]
    if "status" in updates:
        user["status"] = updates["status"]
    
    for key, val in updates.items():
        if key in user["settings"]:
            user["settings"][key] = val
        else:
            user["settings"]["dynamic_params"][key] = val

    # Уведомляем Balancer об изменении одного исполнителя (дельта)
    async with httpx.AsyncClient(trust_env=False, timeout=5.0) as client:
        try:
            await client.post(f"{BALANCER_URL}/api/v1/sync/user-delta", json=user)
            print(f"[AIS] Отправлен вебхук дельты по User #{user_id} в Balancer")
        except Exception as e:
            print(f"[AIS] Не удалось отправить дельту в Balancer: {e}")

    return {"status": "ok", "user": user}

# --- Эндпоинты для работы с заявками ---

@app.post("/api/v1/ais/orders/assign", summary="Колбэк от Balancer: назначение исполнителя")
async def assign_order_callback(payload: Dict[str, Any], background_tasks: BackgroundTasks):
    order_id = payload.get("order_id")
    user_id = payload.get("assigned_user_id")
    
    if not order_id or not user_id:
        raise HTTPException(status_code=400, detail="Missing order_id or assigned_user_id")
        
    if order_id not in orders_db:
        orders_db[order_id] = {"id": order_id, "status": "pending_commit"}
        
    # Запускаем фиксацию в БД с задержкой 2-10 секунд
    background_tasks.add_task(delayed_order_commit, order_id, user_id)
    return {"status": "accepted_for_commit", "order_id": order_id}

@app.get("/api/v1/ais/orders")
async def get_orders():
    return list(orders_db.values())

if __name__ == "__main__":
    uvicorn.run("ais_mock:app", host="127.0.0.1", port=8001, reload=True)