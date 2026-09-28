import io
import csv
from datetime import datetime
from typing import List, Dict, Any
from fastapi import FastAPI, HTTPException, Query, BackgroundTasks, Response
from fastapi.responses import HTMLResponse
import uvicorn
import httpx

from balancer import BalancerService, Order, User, UserSettings, ParameterDefinition
from rule_engine import DynamicRule, Condition

app = FastAPI(title="Executor Balancer Core API", version="1.0.0")
balancer = BalancerService()

AIS_BASE_URL = "http://127.0.0.1:8001"

active_rules: List[DynamicRule] = []

# Хранилище аналитики распределений для дашборда
distribution_events: List[Dict[str, Any]] = []

async def notify_ais_assignment(order_id: int, assigned_user_id: int):
    async with httpx.AsyncClient(trust_env=False, timeout=2.0) as client:
        try:
            await client.post(
                f"{AIS_BASE_URL}/api/v1/ais/orders/assign",
                json={"order_id": order_id, "assigned_user_id": assigned_user_id}
            )
        except Exception:
            pass

@app.get("/")
async def root():
    return {"status": "ok", "service": "Executor Balancer", "ui_url": "/app"}

# --- СИНХРОНИЗАЦИЯ ПОЛЬЗОВАТЕЛЕЙ ---

@app.post("/api/v1/sync/users", summary="Прямая синхронизация списка исполнителей")
async def sync_users(users: List[User]):
    await balancer.update_users_cache(users)
    return {"status": "ok", "synced_count": len(users)}

@app.post("/api/v1/sync/from-ais", summary="Полная синхронизация из внешней АИС")
async def sync_from_ais():
    async with httpx.AsyncClient(trust_env=False, timeout=5.0) as client:
        try:
            resp = await client.get(f"{AIS_BASE_URL}/api/v1/ais/users")
            if resp.status_code != 200:
                raise HTTPException(status_code=502, detail="АИС вернула ошибку")
            raw_users = resp.json()
            users = [User(**u) for u in raw_users]
            await balancer.update_users_cache(users)
            return {"status": "ok", "synced_count": len(users)}
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Ошибка связи с АИС: {e}")

@app.post("/api/v1/sync/user-delta", summary="Прием дельты пользователя")
async def sync_user_delta(user_data: Dict[str, Any]):
    user = User(**user_data)
    await balancer.update_single_user(user)
    return {"status": "ok", "updated_user_id": user.id}

# --- ПАРАМЕТРЫ И ПРАВИЛА ---

@app.post("/api/v1/parameters")
async def create_parameter(param: ParameterDefinition):
    await balancer.register_parameter(param)
    return {"status": "ok", "message": f"Параметр '{param.name}' зарегистрирован"}

@app.get("/api/v1/parameters/orders")
async def get_order_parameters():
    return list(balancer.order_parameters.values())

@app.get("/api/v1/parameters/users")
async def get_user_parameters():
    return list(balancer.user_parameters.values())

@app.get("/api/v1/users")
async def get_users():
    return list(balancer.users.values())

@app.post("/api/v1/users/{user_id}/param")
async def update_user_param(user_id: int, param_name: str = Query(...), value: str = Query(...)):
    await balancer.set_user_param(user_id, param_name, value)
    return {"status": "ok"}

@app.get("/api/v1/rules")
async def get_rules():
    return active_rules

@app.post("/api/v1/rules")
async def add_rule(rule: DynamicRule):
    active_rules.append(rule)
    return {"status": "ok", "rule_id": rule.id}

@app.delete("/api/v1/rules/{rule_id}")
async def delete_rule(rule_id: str):
    global active_rules
    active_rules = [r for r in active_rules if r.id != rule_id]
    return {"status": "ok", "deleted": rule_id}

# --- УПРАВЛЕНИЕ ЗАЯВКАМИ И СЛОТАМИ ---

@app.post("/api/v1/orders/distribute")
async def distribute_order(order: Order, background_tasks: BackgroundTasks):
    executor_id = await balancer.select_executor(order, active_rules)
    if executor_id is None:
        raise HTTPException(status_code=409, detail="Подходящий исполнитель не найден или лимиты исчерпаны")
    
    distribution_events.append({
        "timestamp": datetime.now().strftime("%H:%M:%S"),
        "order_id": order.id,
        "assigned_user_id": executor_id,
        "weight": order.weight,
        "sum": order.sum,
        "order_type": order.order_type
    })
    if len(distribution_events) > 500:
        distribution_events.pop(0)

    background_tasks.add_task(notify_ais_assignment, order.id, executor_id)
    return {
        "order_id": order.id,
        "assigned_user_id": executor_id,
        "applied_order_params": order.dynamic_params,
        "status": "assigned"
    }

@app.post("/api/v1/orders/{order_id}/release")
async def release_order_slot(order_id: int, user_id: int = Query(...), weight: float = Query(1.0)):
    await balancer.release_slot(user_id, weight)
    return {"status": "ok", "order_id": order_id, "user_id": user_id}

@app.post("/api/v1/slots/reset")
async def reset_slots():
    await balancer.reset_all_slots()
    distribution_events.clear()
    return {"status": "ok", "message": "Слоты, суточные лимиты и аналитика сброшены"}

# --- МЕТРИКИ И ЭКСПОРТ (EXCEL / API) ---

@app.get("/api/v1/metrics", summary="Сводные метрики распределения")
async def get_metrics():
    total_assigned = sum(balancer.daily_counts.values())
    active_users = [u for u in balancer.users.values() if u.status == "active"]
    
    eligible_users = [
        u for u in active_users 
        if u.settings.max_daily_limit is None or balancer.daily_counts.get(u.id, 0) < u.settings.max_daily_limit
    ]
    
    fairness_deviation = 0.0
    total_capacity = sum(u.settings.capacity for u in eligible_users)
    eligible_orders = sum(balancer.daily_counts.get(u.id, 0) for u in eligible_users)

    if len(eligible_users) > 1 and total_capacity > 0 and eligible_orders >= len(eligible_users) * 2:
        deviations = []
        for u in eligible_users:
            expected = eligible_orders * (u.settings.capacity / total_capacity)
            fact = balancer.daily_counts.get(u.id, 0)
            if expected > 0:
                deviations.append(abs(fact - expected) / expected)
        fairness_deviation = round(max(deviations) * 100, 2) if deviations else 0.0

    return {
        "active_slots": balancer.active_slots,
        "daily_counts": balancer.daily_counts,
        "total_assigned": total_assigned,
        "fairness_deviation_pct": min(fairness_deviation, 100.0),
        "recent_events": distribution_events[-20:]
    }

@app.get("/api/v1/metrics/export/excel", summary="Выгрузка метрик в Excel (.csv с UTF-8 BOM)")
async def export_metrics_excel():
    output = io.StringIO()
    output.write('\ufeff')
    writer = csv.writer(output, delimiter=';')
    
    writer.writerow(["ID исполнителя", "Статус", "Емкость (Capacity)", "Текущие слоты", "Заявок за день", "Лимит за день"])
    for u in balancer.users.values():
        writer.writerow([
            f"User #{u.id}",
            u.status,
            u.settings.capacity,
            balancer.active_slots.get(u.id, 0.0),
            balancer.daily_counts.get(u.id, 0),
            u.settings.max_daily_limit if u.settings.max_daily_limit is not None else "Не ограничен"
        ])
    
    writer.writerow([])
    writer.writerow(["История последних распределений"])
    writer.writerow(["Время", "ID заявки", "Исполнитель", "Вес заявки", "Сумма", "Тип заявки"])
    for ev in reversed(distribution_events):
        writer.writerow([
            ev["timestamp"],
            ev["order_id"],
            f"User #{ev['assigned_user_id']}",
            ev["weight"],
            ev["sum"],
            ev["order_type"]
        ])

    return Response(
        content=output.getvalue(),
        media_type="application/vnd.ms-excel",
        headers={"Content-Disposition": f"attachment; filename=metrics_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"}
    )

# --- UI ВЕБ-ИНТЕРФЕЙС ---

@app.get("/app", response_class=HTMLResponse)
async def serve_ui():
    return """
    <!DOCTYPE html>
    <html lang="ru">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Executor Balancer Analytics</title>
        <script src="https://cdn.tailwindcss.com"></script>
        <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    </head>
    <body class="bg-slate-50 text-slate-900 p-6 md:p-8 font-sans">
        <div class="max-w-7xl mx-auto space-y-6">
            <!-- Шапка -->
            <header class="flex flex-col md:flex-row justify-between items-start md:items-center border-b border-slate-200 pb-4 gap-4">
                <div>
                    <h1 class="text-3xl font-extrabold text-indigo-700">Executor Balancer</h1>
                    <p class="text-sm text-slate-500 mt-1">Интерактивный дашборд аналитики, балансировка и конструктор параметров</p>
                </div>
                <div class="flex flex-wrap items-center gap-3">
                    <a href="/api/v1/metrics/export/excel" class="px-4 py-2 bg-emerald-600 hover:bg-emerald-700 text-white rounded-lg text-sm font-semibold transition shadow-sm flex items-center gap-1.5">
                        <span>📊</span> Экспорт в Excel (.csv)
                    </a>
                    <button onclick="resetAllSlots()" class="px-4 py-2 bg-amber-500 hover:bg-amber-600 text-white rounded-lg text-sm font-semibold transition shadow-sm flex items-center gap-1.5">
                        <span>⚡</span> Сбросить слоты и лимиты
                    </button>
                    <button onclick="syncFromAIS()" class="px-4 py-2 bg-indigo-600 hover:bg-indigo-700 text-white rounded-lg text-sm font-semibold transition shadow-sm flex items-center gap-1.5">
                        <span>🔄</span> Синхронизация с АИС
                    </button>
                    <a href="/docs" target="_blank" class="px-3 py-2 bg-slate-200 hover:bg-slate-300 text-slate-800 rounded-lg text-sm font-semibold transition">API</a>
                </div>
            </header>

            <!-- KPI ВИДЖЕТЫ -->
            <div class="grid grid-cols-1 md:grid-cols-4 gap-4">
                <div class="bg-white p-5 rounded-2xl shadow-sm border border-slate-200">
                    <span class="text-xs font-bold text-slate-400 uppercase">Всего распределено</span>
                    <div id="kpiTotal" class="text-3xl font-extrabold text-slate-800 mt-1">0</div>
                    <span class="text-xs text-slate-500">за текущую сессию</span>
                </div>
                <div class="bg-white p-5 rounded-2xl shadow-sm border border-slate-200">
                    <span class="text-xs font-bold text-slate-400 uppercase">Активных специалистов</span>
                    <div id="kpiActiveUsers" class="text-3xl font-extrabold text-indigo-600 mt-1">0</div>
                    <span class="text-xs text-slate-500">готовы к обработке</span>
                </div>
                <div class="bg-white p-5 rounded-2xl shadow-sm border border-slate-200">
                    <span class="text-xs font-bold text-slate-400 uppercase">Погрешность балансировки</span>
                    <div id="kpiFairness" class="text-3xl font-extrabold text-emerald-600 mt-1">0.0%</div>
                    <span class="text-xs text-emerald-700 font-medium">целевой диапазон ±1-2%</span>
                </div>
                <div class="bg-white p-5 rounded-2xl shadow-sm border border-slate-200">
                    <span class="text-xs font-bold text-slate-400 uppercase">Средний вес заявки</span>
                    <div id="kpiAvgWeight" class="text-3xl font-extrabold text-amber-500 mt-1">1.0</div>
                    <span class="text-xs text-slate-500">сложность потока</span>
                </div>
            </div>

            <!-- ИНТЕРАКТИВНЫЕ ГРАФИКИ -->
            <div class="grid grid-cols-1 md:grid-cols-2 gap-6">
                <div class="bg-white p-5 rounded-2xl shadow-sm border border-slate-200">
                    <h3 class="font-bold text-slate-800 text-sm mb-4 flex items-center gap-2">
                        <span>📈</span> Распределение заявок по исполнителям (Факт vs Емкость)
                    </h3>
                    <div class="h-64">
                        <canvas id="loadChart"></canvas>
                    </div>
                </div>

                <div class="bg-white p-5 rounded-2xl shadow-sm border border-slate-200">
                    <h3 class="font-bold text-slate-800 text-sm mb-4 flex items-center gap-2">
                        <span>⏱️</span> Текущая загрузка специалистов (Активные слоты)
                    </h3>
                    <div class="h-64">
                        <canvas id="slotsChart"></canvas>
                    </div>
                </div>
            </div>

            <!-- ТЕСТОВАЯ ЗАЯВКА -->
            <div class="bg-indigo-50/70 p-4 rounded-2xl border border-indigo-100 flex flex-col md:flex-row justify-between items-center gap-3">
                <div>
                    <h3 class="font-bold text-indigo-950 text-sm">🚀 Тестовое распределение заявки</h3>
                    <p class="text-xs text-indigo-700">Передайте динамические параметры заявки в формате JSON (например: {"priority": "NORMAL"})</p>
                </div>
                <div class="flex items-center gap-2 w-full md:w-auto">
                    <input type="text" id="testOrderParams" placeholder='{"priority": "NORMAL"}' value='{"priority": "NORMAL"}' 
                           class="border border-indigo-200 p-2 rounded-lg text-xs font-mono outline-none w-52 bg-white">
                    <button onclick="sendTestOrder()" class="px-4 py-2 bg-indigo-600 hover:bg-indigo-700 text-white rounded-lg text-sm font-semibold transition shrink-0">
                        Отправить заявку
                    </button>
                </div>
            </div>
            <div id="testOrderResult" class="text-xs font-mono font-semibold px-2"></div>

            <!-- КОНСТРУКТОР ПАРАМЕТРОВ И ПРАВИЛ -->
            <div class="grid grid-cols-1 md:grid-cols-3 gap-6">
                <!-- Параметр заявки -->
                <div class="bg-white p-5 rounded-2xl shadow-sm border border-indigo-100">
                    <h2 class="text-md font-bold mb-3 text-indigo-700">📝 Параметр заявки</h2>
                    <form id="orderParamForm" class="space-y-3">
                        <input type="text" id="opName" placeholder="Имя поля (priority)" required class="w-full border p-2 rounded-lg text-sm outline-none">
                        <input type="text" id="opDisplay" placeholder="Название (Приоритет)" required class="w-full border p-2 rounded-lg text-sm outline-none">
                        <input type="text" id="opDefault" placeholder="Значение по умолчанию (NORMAL)" required class="w-full border p-2 rounded-lg text-sm outline-none">
                        <button type="submit" class="w-full bg-indigo-600 hover:bg-indigo-700 text-white font-semibold py-2 rounded-lg text-sm">Добавить в заявки</button>
                    </form>
                    <div id="orderParamsList" class="flex flex-wrap gap-1 mt-3"></div>
                </div>

                <!-- Параметр исполнителя -->
                <div class="bg-white p-5 rounded-2xl shadow-sm border border-slate-200">
                    <h2 class="text-md font-bold mb-3 text-slate-800">👥 Параметр исполнителя</h2>
                    <form id="userParamForm" class="space-y-3">
                        <input type="text" id="upName" placeholder="Имя поля (priority)" required class="w-full border p-2 rounded-lg text-sm outline-none">
                        <input type="text" id="upDisplay" placeholder="Название (Допустимый приоритет)" required class="w-full border p-2 rounded-lg text-sm outline-none">
                        <input type="text" id="upDefault" placeholder="Значение по умолчанию (NORMAL)" required class="w-full border p-2 rounded-lg text-sm outline-none">
                        <button type="submit" class="w-full bg-slate-800 hover:bg-slate-900 text-white font-semibold py-2 rounded-lg text-sm">Применить исполнителям</button>
                    </form>
                </div>

                <!-- Конструктор условий -->
                <div class="bg-white p-5 rounded-2xl shadow-sm border border-emerald-100">
                    <h2 class="text-md font-bold mb-3 text-emerald-800">⚙️ Конструктор условий</h2>
                    <form id="ruleForm" class="space-y-3">
                        <input type="text" id="rId" placeholder="ID правила (rule_priority)" required class="w-full border p-2 rounded-lg text-sm outline-none">
                        <input type="text" id="rName" placeholder="Название правила" required class="w-full border p-2 rounded-lg text-sm outline-none">
                        <div class="grid grid-cols-3 gap-1">
                            <input type="text" id="rField" placeholder="order.priority" required class="border p-1.5 rounded-lg text-xs outline-none">
                            <select id="rOp" class="border p-1.5 rounded-lg text-xs outline-none bg-white">
                                <option value="==">==</option>
                                <option value="!=">!=</option>
                                <option value=">=">&gt;=</option>
                                <option value="<=">&lt;=</option>
                            </select>
                            <input type="text" id="rTarget" placeholder="user.priority" required class="border p-1.5 rounded-lg text-xs outline-none">
                        </div>
                        <button type="submit" class="w-full bg-emerald-600 hover:bg-emerald-700 text-white font-semibold py-2 rounded-lg text-sm">Сохранить правило</button>
                    </form>
                </div>
            </div>

            <!-- СПИСОК АКТИВНЫХ ПРАВИЛ -->
            <div class="bg-white p-5 rounded-2xl shadow-sm border border-slate-200">
                <h2 class="text-md font-bold mb-3 text-slate-800">📋 Активные правила распределения</h2>
                <div id="rulesList" class="space-y-2 text-xs">
                    <span class="text-slate-400">Загрузка правил...</span>
                </div>
            </div>

            <!-- ТАБЛИЦА ИСПОЛНИТЕЛЕЙ -->
            <div class="bg-white p-6 rounded-2xl shadow-sm border border-slate-200">
                <div class="flex justify-between items-center mb-4">
                    <h2 class="text-lg font-bold text-slate-800">👥 Статус и квоты исполнителей</h2>
                    <span id="syncNotice" class="text-xs font-medium text-emerald-600"></span>
                </div>
                <div class="overflow-x-auto">
                    <table class="w-full text-left border-collapse text-sm">
                        <thead>
                            <tr class="bg-slate-100 border-b text-xs font-semibold text-slate-600">
                                <th class="p-3">ID</th>
                                <th class="p-3">Статус</th>
                                <th class="p-3">Емкость (Capacity)</th>
                                <th class="p-3">Нагрузка (слоты)</th>
                                <th class="p-3">Лимит за день (факт / макс)</th>
                                <th class="p-3">Параметры</th>
                                <th class="p-3 text-right">Действие</th>
                            </tr>
                        </thead>
                        <tbody id="usersTable" class="divide-y divide-slate-100">
                            <tr><td colspan="7" class="p-6 text-center text-slate-400">Нет исполнителей</td></tr>
                        </tbody>
                    </table>
                </div>
            </div>
        </div>

        <script>
            let loadChartInstance = null;
            let slotsChartInstance = null;
            const CHART_COLORS = ['#6366f1', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6'];

            function initCharts() {
                const ctxLoad = document.getElementById('loadChart').getContext('2d');
                loadChartInstance = new Chart(ctxLoad, {
                    type: 'bar',
                    data: {
                        labels: [],
                        datasets: [{
                            label: 'Назначено заявок (факт)',
                            data: [],
                            backgroundColor: CHART_COLORS,
                            borderRadius: 6
                        }]
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        plugins: { legend: { display: false } },
                        scales: { y: { beginAtZero: true } }
                    }
                });

                const ctxSlots = document.getElementById('slotsChart').getContext('2d');
                slotsChartInstance = new Chart(ctxSlots, {
                    type: 'doughnut',
                    data: {
                        labels: [],
                        datasets: [{
                            data: [],
                            backgroundColor: CHART_COLORS
                        }]
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        plugins: { legend: { position: 'bottom' } }
                    }
                });
            }

            async function loadRules() {
                try {
                    const res = await fetch('/api/v1/rules');
                    const rules = await res.json();
                    const container = document.getElementById('rulesList');
                    if (!rules.length) {
                        container.innerHTML = '<span class="text-slate-400">Нет добавленных правил (балансировка идет по текущей нагрузке и емкости)</span>';
                        return;
                    }
                    container.innerHTML = rules.map(r => `
                        <div class="flex justify-between items-center bg-slate-50 p-2.5 rounded-lg border border-slate-200">
                            <div>
                                <span class="font-bold text-slate-700">${r.name} (${r.id})</span>: 
                                <span class="font-mono text-indigo-700">${r.conditions.map(c => `${c.field} ${c.operator}${c.target_field || c.constant}`).join(' AND ')}</span>
                            </div>
                            <button onclick="deleteRule('${r.id}')" class="text-rose-600 hover:text-rose-800 font-semibold transition">Удалить ✕</button>
                        </div>
                    `).join('');
                } catch(e) {
                    console.error("Ошибка загрузки правил:", e);
                }
            }

            async function deleteRule(ruleId) {
                if (!confirm(`Удалить правило ${ruleId}?`)) return;
                await fetch(`/api/v1/rules/${ruleId}`, { method: 'DELETE' });
                await loadRules();
            }

            async function updateDashboard() {
                try {
                    const resUsers = await fetch('/api/v1/users');
                    const users = await resUsers.json();
                    const resMetrics = await fetch('/api/v1/metrics');
                    const metrics = await resMetrics.json();

                    document.getElementById('kpiTotal').innerText = metrics.total_assigned || 0;
                    const activeCount = users.filter(u => u.status === 'active').length;
                    document.getElementById('kpiActiveUsers').innerText = activeCount;
                    document.getElementById('kpiFairness').innerText = (metrics.fairness_deviation_pct || 0) + '%';

                    if (metrics.recent_events && metrics.recent_events.length > 0) {
                        const sumWeights = metrics.recent_events.reduce((acc, ev) => acc + (ev.weight || 1.0), 0);
                        document.getElementById('kpiAvgWeight').innerText = (sumWeights / metrics.recent_events.length).toFixed(1);
                    }

                    const labels = users.map(u => `User #${u.id}`);
                    const dailyData = users.map(u => metrics.daily_counts[u.id] || 0);
                    const slotsData = users.map(u => metrics.active_slots[u.id] || 0);

                    if (loadChartInstance) {
                        loadChartInstance.data.labels = labels;
                        loadChartInstance.data.datasets[0].data = dailyData;
                        loadChartInstance.data.datasets[0].backgroundColor = labels.map((_, i) => CHART_COLORS[i % CHART_COLORS.length]);
                        loadChartInstance.update();
                    }

                    if (slotsChartInstance) {
                        slotsChartInstance.data.labels = labels;
                        slotsChartInstance.data.datasets[0].data = slotsData.length && slotsData.some(v => v > 0) ? slotsData : [1];
                        slotsChartInstance.data.datasets[0].backgroundColor = labels.map((_, i) => CHART_COLORS[i % CHART_COLORS.length]);
                        slotsChartInstance.update();
                    }

                    const tbody = document.getElementById('usersTable');
                    if (users.length) {
                        tbody.innerHTML = users.map(u => {
                            const slots = (metrics.active_slots && metrics.active_slots[u.id]) || 0;
                            const daily = (metrics.daily_counts && metrics.daily_counts[u.id]) || 0;
                            const maxLimit = (u.settings.max_daily_limit !== null && u.settings.max_daily_limit !== undefined) 
                                ? u.settings.max_daily_limit : '∞';

                            const isLimitReached = (maxLimit !== '∞' && daily >= maxLimit);

                            let statusBadge;
                            if (u.status !== 'active') {
                                statusBadge = `<span class="px-2 py-0.5 rounded-full text-xs font-medium bg-rose-100 text-rose-800">inactive</span>`;
                            } else if (isLimitReached) {
                                statusBadge = `<span class="px-2 py-0.5 rounded-full text-xs font-semibold bg-amber-100 text-amber-800">лимит исчерпан</span>`;
                            } else {
                                statusBadge = `<span class="px-2 py-0.5 rounded-full text-xs font-medium bg-emerald-100 text-emerald-800">active</span>`;
                            }

                            const slotBadge = slots > 0 
                                ? `<span class="px-2 py-0.5 rounded font-bold bg-amber-100 text-amber-900">${slots}</span>` 
                                : `<span class="text-slate-400">0</span>`;

                            const limitBadge = isLimitReached
                                ? `<span class="px-2 py-0.5 rounded font-bold bg-rose-100 text-rose-800">${daily} / ${maxLimit}</span>`
                                : `<span class="font-medium text-slate-700">${daily} / ${maxLimit}</span>`;

                            return `
                            <tr>
                                <td class="p-3 font-semibold">User #${u.id}</td>
                                <td class="p-3">${statusBadge}</td>
                                <td class="p-3 font-semibold">${u.settings.capacity || 1.0}</td>
                                <td class="p-3">${slotBadge}</td>
                                <td class="p-3 text-xs">${limitBadge}</td>
                                <td class="p-3 font-mono text-xs"><pre class="bg-slate-50 p-1.5 rounded">${JSON.stringify(u.settings.dynamic_params || {}, null, 2)}</pre></td>
                                <td class="p-3 text-right">
                                    <button onclick="editParam(${u.id})" class="text-indigo-600 hover:underline text-xs font-semibold">Изменить</button>
                                </td>
                            </tr>
                            `;
                        }).join('');
                    }
                } catch (e) {
                    console.error("Dashboard error:", e);
                }
            }

            async function resetAllSlots() {
                if (!confirm("Сбросить текущую нагрузку, лимиты и историю всех специалистов?")) return;
                await fetch('/api/v1/slots/reset', { method: 'POST' });
                await updateDashboard();
            }

            async function syncFromAIS() {
                const notice = document.getElementById('syncNotice');
                notice.innerText = "Синхронизация...";
                try {
                    const res = await fetch('/api/v1/sync/from-ais', { method: 'POST' });
                    const data = await res.json();
                    if (res.ok) {
                        notice.innerText = `✅ Загружено ${data.synced_count} исполнителей из АИС!`;
                        await updateDashboard();
                    } else {
                        notice.innerText = `❌ Ошибка: ${data.detail}`;
                    }
                } catch(e) {
                    notice.innerText = "❌ АИС недоступна на порту 8001";
                }
            }

            async function editParam(userId) {
                const name = prompt("Имя параметра:");
                if (!name) return;
                const val = prompt(`Новое значение для User #${userId}:`);
                if (val === null) return;
                await fetch(`/api/v1/users/${userId}/param?param_name=${encodeURIComponent(name)}&value=${encodeURIComponent(val)}`, { method: 'POST' });
                updateDashboard();
            }

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
                await fetch('/api/v1/rules', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(rule)
                });
                document.getElementById('ruleForm').reset();
                await loadRules();
                alert("Правило успешно добавлено!");
            };

            document.getElementById('orderParamForm').onsubmit = async (e) => {
                e.preventDefault();
                const payload = {
                    name: document.getElementById('opName').value.trim(),
                    display_name: document.getElementById('opDisplay').value.trim(),
                    data_type: "string",
                    default_value: document.getElementById('opDefault').value.trim(),
                    entity: "order"
                };
                await fetch('/api/v1/parameters', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(payload)
                });
                document.getElementById('orderParamForm').reset();
                alert("Параметр добавлен в схему заявок!");
            };

            document.getElementById('userParamForm').onsubmit = async (e) => {
                e.preventDefault();
                const payload = {
                    name: document.getElementById('upName').value.trim(),
                    display_name: document.getElementById('upDisplay').value.trim(),
                    data_type: "string",
                    default_value: document.getElementById('upDefault').value.trim(),
                    entity: "user"
                };
                await fetch('/api/v1/parameters', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(payload)
                });
                document.getElementById('userParamForm').reset();
                await updateDashboard();
                alert("Параметр применен ко всем исполнителям!");
            };

            async function sendTestOrder() {
                const resBox = document.getElementById('testOrderResult');
                resBox.innerText = "Отправка...";
                
                let dynamicParams = {};
                try {
                    const raw = document.getElementById('testOrderParams').value.trim();
                    if (raw) dynamicParams = JSON.parse(raw);
                } catch(e) {
                    alert("Ошибка в формате JSON параметров заявки!");
                    return;
                }

                const testOrder = {
                    id: Math.floor(Math.random() * 90000) + 10000,
                    sum: 25000,
                    order_type: "ORDER_1",
                    weight: 1.0,
                    dynamic_params: dynamicParams
                };

                const res = await fetch('/api/v1/orders/distribute', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(testOrder)
                });

                if (res.ok) {
                    const data = await res.json();
                    resBox.innerHTML = `<span class="text-emerald-700">✅ Назначен <b>User #${data.assigned_user_id}</b></span>`;
                } else {
                    const err = await res.json();
                    resBox.innerHTML = `<span class="text-rose-600">❌ ${err.detail}</span>`;
                }
                await updateDashboard();
            }

            initCharts();
            updateDashboard();
            loadRules();
            setInterval(updateDashboard, 2500);
        </script>
    </body>
    </html>
    """

if __name__ == "__main__":
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)