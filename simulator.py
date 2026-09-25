import asyncio
import random
from typing import List
import httpx

BASE_URL = "http://127.0.0.1:8000"

async def setup_mock_users(client: httpx.AsyncClient) -> bool:
    print("-> Загрузка тестовых исполнителей...")
    users = [
        {
            "id": 1,
            "status": "active",
            "settings": {
                "user_id": 1,
                "max_daily_limit": 100,
                "capacity": 2.0,
                "dynamic_params": {
                    "min_accept_sum": 0,
                    "max_accept_sum": 500000,
                    "order_type": "ORDER_1",
                },
            },
        },
        {
            "id": 2,
            "status": "active",
            "settings": {
                "user_id": 2,
                "max_daily_limit": 50,
                "capacity": 1.0,
                "dynamic_params": {
                    "min_accept_sum": 1000,
                    "max_accept_sum": 200000,
                    "order_type": "ORDER_1",
                },
            },
        },
        {
            "id": 3,
            "status": "active",
            "settings": {
                "user_id": 3,
                "max_daily_limit": 10,
                "capacity": 0.5,
                "dynamic_params": {
                    "min_accept_sum": 5000,
                    "max_accept_sum": 50000,
                    "order_type": "ORDER_1",
                },
            },
        },
    ]
    try:
        resp = await client.post(f"{BASE_URL}/api/v1/sync/users", json=users)
        print(f"-> Код ответа сервера: {resp.status_code}")
        if resp.status_code != 200:
            print("-> Текст ошибки от сервера:", resp.text)
            return False
        print("-> Ответ синхронизации пользователей:", resp.json())
        return True
    except Exception as e:
        print(f"-> Ошибка подключения к серверу: {e}")
        return False

async def send_order(client: httpx.AsyncClient, order_id: int):
    order = {
        "id": order_id,
        "parent_id": None,
        "sum": random.randint(5000, 45000),
        "order_type": "ORDER_1",
        "weight": random.choice([1.0, 1.5, 2.0]),
        "status": "processed",
        "dynamic_params": {},
    }
    
    try:
        resp = await client.post(f"{BASE_URL}/api/v1/orders/distribute", json=order)
        if resp.status_code == 200:
            assigned = resp.json()
            user_id = assigned["assigned_user_id"]
            print(f"[Order #{order_id}] Назначен на User #{user_id} (Вес: {order['weight']})")
            
            # Симуляция работы специалиста: освобождение слота через 2-4 секунды
            await asyncio.sleep(random.uniform(2.0, 4.0))
            await client.post(
                f"{BASE_URL}/api/v1/orders/{order_id}/release",
                params={"user_id": user_id, "weight": order["weight"]},
            )
            print(f"[Order #{order_id}] Завершен, слот User #{user_id} освобожден!")
        else:
            print(f"[Order #{order_id}] Ошибка распределения ({resp.status_code}): {resp.text}")
    except Exception as e:
        print(f"[Order #{order_id}] Сбой запроса: {e}")

async def main():
    async with httpx.AsyncClient(trust_env=False, timeout=10.0) as client:
        ok = await setup_mock_users(client)
        if not ok:
            print("Прерывание: не удалось синхронизировать исполнителей. Проверь консоль сервера uvicorn.")
            return

        print("\n-> Запуск потока заявок (нажмите Ctrl+C для остановки)...")
        order_counter = 100
        while True:
            tasks = [send_order(client, order_counter + i) for i in range(5)]
            order_counter += 5
            await asyncio.gather(*tasks)
            await asyncio.sleep(1.5)

if __name__ == "__main__":
    asyncio.run(main())

#python simulator.py