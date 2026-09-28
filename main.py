<<<<<<< HEAD
from typing import List, Dict, Any
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
import uvicorn

from balancer import BalancerService, Order, User, UserSettings, ParameterDefinition
=======
from contextlib import asynccontextmanager
from typing import List
from fastapi import FastAPI, HTTPException, Query, Depends
from sqlalchemy.ext.asyncio import AsyncSession
import uvicorn

from database import engine, Base, get_db
from balancer import BalancerService, Order, User
>>>>>>> 6a7d5fb2ba35a7658a479535089c6f77576c36d2
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
    return {"status": "ok", "service": "Executor Balancer", "ui_url": "/app"}

# --- API параметров и пользователей ---

@app.post("/api/v1/parameters", summary="Создать параметр и применить его ко всем исполнителям")
async def create_parameter(param: ParameterDefinition):
    await balancer.register_parameter(param)
    return {"status": "ok", "message": f"Параметр '{param.name}' успешно добавлен ко всем исполнителям"}

@app.get("/api/v1/parameters", summary="Получить список зарегистрированных параметров")
async def get_parameters():
    return list(balancer.parameters.values())

@app.get("/api/v1/users", summary="Список всех исполнителей и их параметров")
async def get_users():
    return list(balancer.users.values())

@app.post("/api/v1/users/{user_id}/param", summary="Изменить параметр у конкретного исполнителя")
async def update_user_param(user_id: int, param_name: str = Query(...), value: str = Query(...)):
    await balancer.set_user_param(user_id, param_name, value)
    return {"status": "ok", "user_id": user_id, "param": param_name, "value": value}

<<<<<<< HEAD
@app.post("/api/v1/sync/users", summary="Синхронизация кэша пользователей из АИС")
async def sync_users(users: List[User]):
    await balancer.update_users_cache(users)
    # Гарантируем, что уже существующие в каталоге параметры проставятся новым пользователям
    for p in balancer.parameters.values():
        if p.entity in ("user", "both"):
            for u in balancer.users.values():
                u.settings.dynamic_params.setdefault(p.name, p.default_value)
    return {"status": "ok", "synced_count": len(users)}

# --- API правил и распределения ---

@app.get("/api/v1/rules", summary="Список активных правил")
async def get_rules():
    return active_rules

=======
>>>>>>> 6a7d5fb2ba35a7658a479535089c6f77576c36d2
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

<<<<<<< HEAD
# --- Встроенный UI (форма сайта) ---

@app.get("/app", response_class=HTMLResponse)
async def serve_ui():
    return """
    <!DOCTYPE html>
    <html lang="ru">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Executor Balancer UI</title>
        <script src="https://cdn.tailwindcss.com"></script>
    </head>
    <body class="bg-slate-50 text-slate-900 p-6 md:p-10 font-sans">
        <div class="max-w-6xl mx-auto space-y-8">
            <header class="flex justify-between items-center border-b border-slate-200 pb-5">
                <div>
                    <h1 class="text-3xl font-extrabold text-indigo-700">Executor Balancer</h1>
                    <p class="text-sm text-slate-500 mt-1">Панель управления параметрами, исполнителями и конструктором правил</p>
                </div>
                <div class="space-x-3">
                    <a href="/docs" target="_blank" class="px-4 py-2 bg-slate-200 hover:bg-slate-300 text-slate-800 rounded-lg text-sm font-semibold transition">Swagger API</a>
                    <a href="/api/v1/metrics" target="_blank" class="px-4 py-2 bg-indigo-50 hover:bg-indigo-100 text-indigo-700 rounded-lg text-sm font-semibold transition">Метрики (JSON)</a>
                </div>
            </header>

            <div class="grid grid-cols-1 md:grid-cols-2 gap-8">
                <!-- ФОРМА 1: Добавить параметр ВСЕМ исполнителям -->
                <div class="bg-white p-6 rounded-2xl shadow-sm border border-slate-200">
                    <h2 class="text-lg font-bold mb-4 text-slate-800 flex items-center gap-2">
                        <span>➕</span> Добавить параметр всем исполнителям
                    </h2>
                    <form id="paramForm" class="space-y-4">
                        <div>
                            <label class="block text-xs font-semibold text-slate-600 uppercase">Имя поля (латиница)</label>
                            <input type="text" id="pName" placeholder="например: category" required 
                                   class="w-full border border-slate-300 p-2.5 rounded-lg mt-1 focus:ring-2 focus:ring-indigo-500 outline-none text-sm">
                        </div>
                        <div>
                            <label class="block text-xs font-semibold text-slate-600 uppercase">Отображаемое название</label>
                            <input type="text" id="pDisplay" placeholder="например: Категория клиента" required 
                                   class="w-full border border-slate-300 p-2.5 rounded-lg mt-1 focus:ring-2 focus:ring-indigo-500 outline-none text-sm">
                        </div>
                        <div>
                            <label class="block text-xs font-semibold text-slate-600 uppercase">Значение по умолчанию для ВСЕХ</label>
                            <input type="text" id="pDefault" placeholder="например: STANDARD" required 
                                   class="w-full border border-slate-300 p-2.5 rounded-lg mt-1 focus:ring-2 focus:ring-indigo-500 outline-none text-sm">
                        </div>
                        <button type="submit" class="w-full bg-indigo-600 hover:bg-indigo-700 text-white font-semibold py-2.5 rounded-lg transition text-sm">
                            Применить параметр ко всем
                        </button>
                    </form>
                </div>

                <!-- ФОРМА 2: Конструктор условий алгоритма -->
                <div class="bg-white p-6 rounded-2xl shadow-sm border border-slate-200">
                    <h2 class="text-lg font-bold mb-4 text-slate-800 flex items-center gap-2">
                        <span>⚙️</span> Конструктор условий распределения
                    </h2>
                    <form id="ruleForm" class="space-y-4">
                        <div>
                            <label class="block text-xs font-semibold text-slate-600 uppercase">ID и название правила</label>
                            <div class="grid grid-cols-2 gap-2 mt-1">
                                <input type="text" id="rId" placeholder="rule_category" required 
                                       class="border border-slate-300 p-2.5 rounded-lg text-sm focus:ring-2 focus:ring-emerald-500 outline-none">
                                <input type="text" id="rName" placeholder="Проверка категории" required 
                                       class="border border-slate-300 p-2.5 rounded-lg text-sm focus:ring-2 focus:ring-emerald-500 outline-none">
                            </div>
                        </div>
                        <div>
                            <label class="block text-xs font-semibold text-slate-600 uppercase">Условие соответствия</label>
                            <div class="grid grid-cols-3 gap-2 mt-1">
                                <input type="text" id="rField" placeholder="order.category" required 
                                       class="border border-slate-300 p-2 rounded-lg text-sm focus:ring-2 focus:ring-emerald-500 outline-none">
                                <select id="rOp" class="border border-slate-300 p-2 rounded-lg text-sm bg-white focus:ring-2 focus:ring-emerald-500 outline-none">
                                    <option value="==">== (Равно)</option>
                                    <option value="!=">!= (Не равно)</option>
                                    <option value=">=">&gt;=</option>
                                    <option value="<=">&lt;=</option>
                                </select>
                                <input type="text" id="rTarget" placeholder="user.category" required 
                                       class="border border-slate-300 p-2 rounded-lg text-sm focus:ring-2 focus:ring-emerald-500 outline-none">
                            </div>
                        </div>
                        <button type="submit" class="w-full bg-emerald-600 hover:bg-emerald-700 text-white font-semibold py-2.5 rounded-lg transition text-sm">
                            Сохранить правило в движок
                        </button>
                    </form>
                </div>
            </div>

            <!-- СПИСОК ИСПОЛНИТЕЛЕЙ С ПАРАМЕТРАМИ И НАГРУЗКОЙ -->
            <div class="bg-white p-6 rounded-2xl shadow-sm border border-slate-200">
                <div class="flex justify-between items-center mb-4">
                    <div>
                        <h2 class="text-lg font-bold text-slate-800">👥 Исполнители и их текущие параметры</h2>
                        <p class="text-xs text-slate-400">Данные обновляются автоматически каждые 3 секунды</p>
                    </div>
                    <button onclick="loadUsers()" class="px-3 py-1.5 bg-slate-100 hover:bg-slate-200 text-slate-700 text-xs font-semibold rounded-lg transition">
                        🔄 Обновить вручную
                    </button>
                </div>
                <div class="overflow-x-auto">
                    <table class="w-full text-left border-collapse">
                        <thead>
                            <tr class="bg-slate-100/70 border-b border-slate-200 text-xs font-semibold text-slate-600">
                                <th class="p-3">Специалист</th>
                                <th class="p-3">Статус</th>
                                <th class="p-3">Capacity</th>
                                <th class="p-3">Параметры (dynamic_params)</th>
                                <th class="p-3 text-right">Действие</th>
                            </tr>
                        </thead>
                        <tbody id="usersTable" class="divide-y divide-slate-100 text-sm">
                            <tr><td colspan="5" class="p-6 text-center text-slate-400">Загрузка данных...</td></tr>
                        </tbody>
                    </table>
                </div>
            </div>
        </div>

        <script>
            async function loadUsers() {
                try {
                    const res = await fetch('/api/v1/users');
                    const users = await res.json();
                    const tbody = document.getElementById('usersTable');
                    if (!users.length) {
                        tbody.innerHTML = '<tr><td colspan="5" class="p-6 text-center text-slate-400">Исполнители не загружены. Запустите simulator.py или выполните sync_users.</td></tr>';
                        return;
                    }
                    tbody.innerHTML = users.map(u => `
                        <tr class="hover:bg-slate-50/60 transition">
                            <td class="p-3 font-semibold text-slate-800">User #${u.id}</td>
                            <td class="p-3">
                                <span class="px-2.5 py-1 rounded-full text-xs font-medium ${u.status === 'active' ? 'bg-emerald-100 text-emerald-800' : 'bg-rose-100 text-rose-800'}">
                                    ${u.status}
                                </span>
                            </td>
                            <td class="p-3 font-medium text-slate-700">${u.settings.capacity || 1.0}</td>
                            <td class="p-3 font-mono text-xs text-slate-600">
                                <pre class="bg-slate-50 p-2 rounded border border-slate-200">${JSON.stringify(u.settings.dynamic_params || {}, null, 2)}</pre>
                            </td>
                            <td class="p-3 text-right">
                                <button onclick="editParam(${u.id})" class="text-indigo-600 hover:text-indigo-800 font-semibold text-xs transition">
                                    Изменить значение ✏️
                                </button>
                            </td>
                        </tr>
                    `).join('');
                } catch (e) {
                    console.error("Ошибка загрузки:", e);
                }
            }

            async function editParam(userId) {
                const name = prompt("Введите имя параметра (например, category):");
                if (!name) return;
                const val = prompt(`Новое значение параметра '${name}' для User #${userId}:`);
                if (val === null) return;
                await fetch(`/api/v1/users/${userId}/param?param_name=${encodeURIComponent(name)}&value=${encodeURIComponent(val)}`, { method: 'POST' });
                await loadUsers();
            }

            document.getElementById('paramForm').onsubmit = async (e) => {
                e.preventDefault();
                const payload = {
                    name: document.getElementById('pName').value.trim(),
                    display_name: document.getElementById('pDisplay').value.trim(),
                    data_type: "string",
                    default_value: document.getElementById('pDefault').value.trim(),
                    entity: "both"
                };
                const res = await fetch('/api/v1/parameters', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(payload)
                });
                if (res.ok) {
                    alert(`Параметр "${payload.name}" успешно применён ко всем исполнителям!`);
                    document.getElementById('paramForm').reset();
                    await loadUsers();
                } else {
                    alert("Ошибка при сохранении параметра");
                }
            };

            document.getElementById('ruleForm').onsubmit = async (e) => {
                e.preventDefault();
                const rule = {
                    id: document.getElementById('rId').value.trim(),
                    name: document.getElementById('rName').value.trim(),
                    conditions: [{
                        field: document.getElementById('rField').value.trim(),
                        operator: document.getElementById('rOp').value,
                        target_field: document.getElementById('rTarget').value.trim(),
                        constant: null
                    }]
                };
                const res = await fetch('/api/v1/rules', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(rule)
                });
                if (res.ok) {
                    alert(`Правило "${rule.name}" успешно зарегистрировано!`);
                    document.getElementById('ruleForm').reset();
                } else {
                    alert("Ошибка при добавлении правила");
                }
            };

            loadUsers();
            setInterval(loadUsers, 3000);
        </script>
    </body>
    </html>
    """
=======
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
>>>>>>> 6a7d5fb2ba35a7658a479535089c6f77576c36d2

if __name__ == "__main__":
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)