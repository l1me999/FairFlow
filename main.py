from typing import List, Dict, Any
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
import uvicorn
import random

from balancer import BalancerService, Order, User, UserSettings, ParameterDefinition
from rule_engine import DynamicRule, Condition

app = FastAPI(title="Executor Balancer Core API", version="1.0.0")
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

# --- API параметров ---

@app.post("/api/v1/parameters", summary="Зарегистрировать параметр (для заявок или пользователей)")
async def create_parameter(param: ParameterDefinition):
    await balancer.register_parameter(param)
    return {"status": "ok", "message": f"Параметр '{param.name}' зарегистрирован для '{param.entity}'"}

@app.get("/api/v1/parameters/orders", summary="Список параметров заявок")
async def get_order_parameters():
    return list(balancer.order_parameters.values())

@app.get("/api/v1/parameters/users", summary="Список параметров пользователей")
async def get_user_parameters():
    return list(balancer.user_parameters.values())

# --- API пользователей ---

@app.get("/api/v1/users", summary="Список всех исполнителей")
async def get_users():
    return list(balancer.users.values())

@app.post("/api/v1/users/{user_id}/param", summary="Изменить параметр у исполнителя")
async def update_user_param(user_id: int, param_name: str = Query(...), value: str = Query(...)):
    await balancer.set_user_param(user_id, param_name, value)
    return {"status": "ok", "user_id": user_id, "param": param_name, "value": value}

@app.post("/api/v1/sync/users", summary="Синхронизация кэша пользователей")
async def sync_users(users: List[User]):
    await balancer.update_users_cache(users)
    return {"status": "ok", "synced_count": len(users)}

# --- API правил и распределения ---

@app.get("/api/v1/rules", summary="Список активных правил")
async def get_rules():
    return active_rules

@app.post("/api/v1/rules", summary="Добавление правила из конструктора")
async def add_rule(rule: DynamicRule):
    active_rules.append(rule)
    return {"status": "ok", "rule_id": rule.id}

@app.post("/api/v1/orders/distribute", summary="Распределение входящей заявки")
async def distribute_order(order: Order):
    executor_id = await balancer.select_executor(order, active_rules)
    if executor_id is None:
        raise HTTPException(status_code=409, detail="Подходящий исполнитель не найден или лимиты исчерпаны")
    
    return {
        "order_id": order.id,
        "assigned_user_id": executor_id,
        "applied_order_params": order.dynamic_params,
        "status": "assigned"
    }

@app.post("/api/v1/orders/{order_id}/release", summary="Уведомление об освобождении слота")
async def release_order_slot(
    order_id: int,
    user_id: int = Query(...),
    weight: float = Query(1.0)
):
    await balancer.release_slot(user_id, weight)
    return {"status": "ok", "order_id": order_id, "user_id": user_id}

@app.get("/api/v1/metrics", summary="Метрики распределения")
async def get_metrics():
    return {
        "active_slots": balancer.active_slots,
        "daily_counts": balancer.daily_counts
    }

# --- Полноценный UI с Конструктором параметров заявок ---

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
    <body class="bg-slate-50 text-slate-900 p-6 md:p-8 font-sans">
        <div class="max-w-7xl mx-auto space-y-6">
            <header class="flex justify-between items-center border-b border-slate-200 pb-4">
                <div>
                    <h1 class="text-3xl font-extrabold text-indigo-700">Executor Balancer</h1>
                    <p class="text-sm text-slate-500 mt-1">Конструктор параметров заявок и исполнителей, алгоритмы распределения</p>
                </div>
                <div class="space-x-2">
                    <a href="/docs" target="_blank" class="px-4 py-2 bg-slate-200 hover:bg-slate-300 text-slate-800 rounded-lg text-sm font-semibold transition">Swagger API</a>
                    <a href="/api/v1/metrics" target="_blank" class="px-4 py-2 bg-indigo-50 hover:bg-indigo-100 text-indigo-700 rounded-lg text-sm font-semibold transition">Метрики</a>
                </div>
            </header>

            <div class="grid grid-cols-1 md:grid-cols-3 gap-6">
                <!-- ФОРМА 1: Конструктор параметров ДЛЯ ЗАЯВОК -->
                <div class="bg-white p-5 rounded-2xl shadow-sm border border-indigo-100">
                    <h2 class="text-md font-bold mb-3 text-indigo-700 flex items-center gap-2">
                        <span>📝</span> Конструктор параметров заявок
                    </h2>
                    <form id="orderParamForm" class="space-y-3">
                        <div>
                            <label class="block text-xs font-semibold text-slate-600 uppercase">Имя поля (латиница)</label>
                            <input type="text" id="opName" placeholder="priority" required 
                                   class="w-full border border-slate-300 p-2 rounded-lg mt-1 text-sm focus:ring-2 focus:ring-indigo-500 outline-none">
                        </div>
                        <div>
                            <label class="block text-xs font-semibold text-slate-600 uppercase">Отображаемое название</label>
                            <input type="text" id="opDisplay" placeholder="Приоритет заявки" required 
                                   class="w-full border border-slate-300 p-2 rounded-lg mt-1 text-sm focus:ring-2 focus:ring-indigo-500 outline-none">
                        </div>
                        <div>
                            <label class="block text-xs font-semibold text-slate-600 uppercase">Значение по умолчанию</label>
                            <input type="text" id="opDefault" placeholder="NORMAL" required 
                                   class="w-full border border-slate-300 p-2 rounded-lg mt-1 text-sm focus:ring-2 focus:ring-indigo-500 outline-none">
                        </div>
                        <button type="submit" class="w-full bg-indigo-600 hover:bg-indigo-700 text-white font-semibold py-2 rounded-lg transition text-sm">
                            Добавить параметр в заявки
                        </button>
                    </form>
                    <div class="mt-4">
                        <span class="text-xs font-semibold text-slate-500 uppercase">Активные параметры заявок:</span>
                        <div id="orderParamsList" class="flex flex-wrap gap-1 mt-2"></div>
                    </div>
                </div>

                <!-- ФОРМА 2: Добавить параметр исполнителям -->
                <div class="bg-white p-5 rounded-2xl shadow-sm border border-slate-200">
                    <h2 class="text-md font-bold mb-3 text-slate-800 flex items-center gap-2">
                        <span>👥</span> Параметр для исполнителей
                    </h2>
                    <form id="userParamForm" class="space-y-3">
                        <div>
                            <label class="block text-xs font-semibold text-slate-600 uppercase">Имя поля (латиница)</label>
                            <input type="text" id="upName" placeholder="priority" required 
                                   class="w-full border border-slate-300 p-2 rounded-lg mt-1 text-sm focus:ring-2 focus:ring-indigo-500 outline-none">
                        </div>
                        <div>
                            <label class="block text-xs font-semibold text-slate-600 uppercase">Отображаемое название</label>
                            <input type="text" id="upDisplay" placeholder="Допустимый приоритет" required 
                                   class="w-full border border-slate-300 p-2 rounded-lg mt-1 text-sm focus:ring-2 focus:ring-indigo-500 outline-none">
                        </div>
                        <div>
                            <label class="block text-xs font-semibold text-slate-600 uppercase">Значение всем по умолчанию</label>
                            <input type="text" id="upDefault" placeholder="NORMAL" required 
                                   class="w-full border border-slate-300 p-2 rounded-lg mt-1 text-sm focus:ring-2 focus:ring-indigo-500 outline-none">
                        </div>
                        <button type="submit" class="w-full bg-slate-800 hover:bg-slate-900 text-white font-semibold py-2 rounded-lg transition text-sm">
                            Применить исполнителям
                        </button>
                    </form>
                </div>

                <!-- ФОРМА 3: Конструктор условий -->
                <div class="bg-white p-5 rounded-2xl shadow-sm border border-emerald-100">
                    <h2 class="text-md font-bold mb-3 text-emerald-800 flex items-center gap-2">
                        <span>⚙️</span> Конструктор условий
                    </h2>
                    <form id="ruleForm" class="space-y-3">
                        <div>
                            <input type="text" id="rId" placeholder="ID правила (например rule_priority)" required 
                                   class="w-full border border-slate-300 p-2 rounded-lg text-sm focus:ring-2 focus:ring-emerald-500 outline-none mb-2">
                            <input type="text" id="rName" placeholder="Название правила" required 
                                   class="w-full border border-slate-300 p-2 rounded-lg text-sm focus:ring-2 focus:ring-emerald-500 outline-none">
                        </div>
                        <div class="grid grid-cols-3 gap-1">
                            <input type="text" id="rField" placeholder="order.priority" required 
                                   class="border border-slate-300 p-1.5 rounded-lg text-xs focus:ring-2 focus:ring-emerald-500 outline-none">
                            <select id="rOp" class="border border-slate-300 p-1.5 rounded-lg text-xs bg-white focus:ring-2 focus:ring-emerald-500 outline-none">
                                <option value="==">==</option>
                                <option value="!=">!=</option>
                                <option value=">=">&gt;=</option>
                                <option value="<=">&lt;=</option>
                            </select>
                            <input type="text" id="rTarget" placeholder="user.priority" required 
                                   class="border border-slate-300 p-1.5 rounded-lg text-xs focus:ring-2 focus:ring-emerald-500 outline-none">
                        </div>
                        <button type="submit" class="w-full bg-emerald-600 hover:bg-emerald-700 text-white font-semibold py-2 rounded-lg transition text-sm">
                            Сохранить правило
                        </button>
                    </form>
                </div>
            </div>

            <!-- ИНТЕРАКТИВНОЕ ТЕСТИРОВАНИЕ: Отправить тестовую заявку -->
            <div class="bg-indigo-50/60 p-5 rounded-2xl border border-indigo-100 flex flex-col md:flex-row justify-between items-center gap-4">
                <div>
                    <h3 class="font-bold text-indigo-900 text-sm">🧪 Интерактивный тест распределения заявки</h3>
                    <p class="text-xs text-indigo-700 mt-0.5">Создает тестовую заявку с учетом всех новых параметров и показывает назначенного исполнителя</p>
                </div>
                <div class="flex items-center gap-3">
                    <button onclick="sendTestOrder()" class="px-4 py-2 bg-indigo-600 hover:bg-indigo-700 text-white rounded-lg text-sm font-semibold transition shadow-sm">
                        🚀 Отправить тестовую заявку
                    </button>
                    <span id="testOrderResult" class="text-xs font-mono font-semibold text-slate-700"></span>
                </div>
            </div>

            <!-- СПИСОК ИСПОЛНИТЕЛЕЙ -->
            <div class="bg-white p-6 rounded-2xl shadow-sm border border-slate-200">
                <div class="flex justify-between items-center mb-4">
                    <div>
                        <h2 class="text-lg font-bold text-slate-800">👥 Исполнители и параметры</h2>
                        <p class="text-xs text-slate-400">Данные синхронизируются в реальном времени</p>
                    </div>
                    <button onclick="loadUsers()" class="px-3 py-1.5 bg-slate-100 hover:bg-slate-200 text-slate-700 text-xs font-semibold rounded-lg transition">
                        🔄 Обновить
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
            async function loadOrderParams() {
                const res = await fetch('/api/v1/parameters/orders');
                const params = await res.json();
                const container = document.getElementById('orderParamsList');
                if (!params.length) {
                    container.innerHTML = '<span class="text-xs text-slate-400">Нет параметров</span>';
                    return;
                }
                container.innerHTML = params.map(p => `
                    <span class="px-2 py-1 bg-indigo-100 text-indigo-800 rounded text-xs font-mono">
                        ${p.name}: <b>${p.default_value}</b>
                    </span>
                `).join('');
            }

            async function loadUsers() {
                try {
                    const res = await fetch('/api/v1/users');
                    const users = await res.json();
                    const tbody = document.getElementById('usersTable');
                    if (!users.length) {
                        tbody.innerHTML = '<tr><td colspan="5" class="p-6 text-center text-slate-400">Исполнители не загружены. Запустите simulator.py</td></tr>';
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
                                    Изменить ✏️
                                </button>
                            </td>
                        </tr>
                    `).join('');
                } catch (e) {
                    console.error("Ошибка:", e);
                }
            }

            async function editParam(userId) {
                const name = prompt("Имя параметра для изменения (например, priority):");
                if (!name) return;
                const val = prompt(`Новое значение для User #${userId}:`);
                if (val === null) return;
                await fetch(`/api/v1/users/${userId}/param?param_name=${encodeURIComponent(name)}&value=${encodeURIComponent(val)}`, { method: 'POST' });
                await loadUsers();
            }

            // Добавление параметра для заявки
            document.getElementById('orderParamForm').onsubmit = async (e) => {
                e.preventDefault();
                const payload = {
                    name: document.getElementById('opName').value.trim(),
                    display_name: document.getElementById('opDisplay').value.trim(),
                    data_type: "string",
                    default_value: document.getElementById('opDefault').value.trim(),
                    entity: "order"
                };
                const res = await fetch('/api/v1/parameters', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(payload)
                });
                if (res.ok) {
                    alert(`Параметр заявки "${payload.name}" добавлен! Теперь он автоматически присваивается всем входящим заявкам.`);
                    document.getElementById('orderParamForm').reset();
                    await loadOrderParams();
                }
            };

            // Добавление параметра для исполнителей
            document.getElementById('userParamForm').onsubmit = async (e) => {
                e.preventDefault();
                const payload = {
                    name: document.getElementById('upName').value.trim(),
                    display_name: document.getElementById('upDisplay').value.trim(),
                    data_type: "string",
                    default_value: document.getElementById('upDefault').value.trim(),
                    entity: "user"
                };
                const res = await fetch('/api/v1/parameters', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(payload)
                });
                if (res.ok) {
                    alert(`Параметр "${payload.name}" применён ко всем исполнителям!`);
                    document.getElementById('userParamForm').reset();
                    await loadUsers();
                }
            };

            // Добавление правила
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
                    alert(`Правило "${rule.name}" успешно сохранено!`);
                    document.getElementById('ruleForm').reset();
                }
            };

            // Отправка тестовой заявки
            async function sendTestOrder() {
                const resBox = document.getElementById('testOrderResult');
                resBox.innerText = "Распределение...";
                const testOrder = {
                    id: Math.floor(Math.random() * 90000) + 10000,
                    sum: 25000,
                    order_type: "ORDER_1",
                    weight: 1.0,
                    dynamic_params: {}
                };
                const res = await fetch('/api/v1/orders/distribute', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(testOrder)
                });
                if (res.ok) {
                    const data = await res.json();
                    resBox.innerHTML = `<span class="text-emerald-700">✅ Заявка #${data.order_id} назначена на <b>User #${data.assigned_user_id}</b> (Параметры: ${JSON.stringify(data.applied_order_params)})</span>`;
                } else {
                    const err = await res.json();
                    resBox.innerHTML = `<span class="text-rose-600">❌ Ошибка: ${err.detail}</span>`;
                }
                loadUsers();
            }

            loadUsers();
            loadOrderParams();
            setInterval(loadUsers, 3000);
        </script>
    </body>
    </html>
    """

if __name__ == "__main__":
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)