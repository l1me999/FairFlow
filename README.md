erDiagram
    users {
        int id PK
        string status
    }
    user_settings {
        int id PK
        int user_id FK
        int max_daily_limit
        float capacity
        json dynamic_params
    }
    orders {
        int id PK
        int parent_id FK
        int executor_id FK
        int sum
        string order_type
        float weight
        string status
        json dynamic_params
    }
    metric_snapshots {
        int id PK
        datetime created_at
        int total_active_users
        float average_user_load
    }
    users ||--o| user_settings : "имеет настройки"
    users ||--o{ orders : "обрабатывает"
    orders ||--o| orders : "родительская заявка"
sequenceDiagram
    participant AIS as Внешняя АИС
    participant API as FastAPI (Balancer)
    participant DB as SQLite / ORM
    
    AIS->>API: POST /api/v1/sync/users (Кэш исполнителей)
    API->>DB: Upsert (сохранение/обновление профилей)
    API-->>AIS: 200 OK
    
    AIS->>API: POST /api/v1/orders/distribute (Новая заявка)
    alt parent_id существует и исполнитель активен
        API->>API: Маршрутизация на предыдущего исполнителя
    else
        API->>API: Оценка динамических правил (RuleEngine)
        API->>API: Поиск наименее загруженного (с учетом веса и capacity)
    end
    API->>DB: Сохранение Order (status: processed)
    API-->>AIS: Возврат ID назначенного исполнителя
    
    Note over AIS: Работа специалиста (2-10 сек)
    AIS->>API: POST /api/v1/orders/{id}/release
    API->>API: Высвобождение слота в In-Memory
    API->>DB: Обновление Order (status: accept/reject)

Использование asyncio.Lock и хранения горячих данных в оперативной памяти (active_slots) позволяет выдавать скорость распределения >10 000 RPS, исключая Race Conditions при записи. База данных SQLite используется исключительно для персистентности и создания исторических метрик.