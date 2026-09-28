import streamlit as st
import requests
import pandas as pd
import time

BASE_URL = "http://127.0.0.1:8000"

# Настройка страницы
st.set_page_config(page_title="FairFlow Dashboard", layout="wide")
st.title("FairFlow 📊 | Мониторинг балансировщика")

# Боковая панель управления
with st.sidebar:
    st.header("Управление")
    
    # Тумблер для обновления в реальном времени
    auto_refresh = st.checkbox("Автообновление (2 сек)", value=False)
    
    st.markdown("---")
    st.write("Сбор аналитики:")
    
    # Кнопка генерации среза (Snapshot)
    if st.button("📸 Сделать снимок метрик"):
        try:
            res = requests.post(f"{BASE_URL}/api/v1/metrics/snapshot")
            if res.status_code == 200:
                st.success("Снимок сохранен в БД!")
            else:
                st.error("Ошибка при сохранении снимка")
        except:
            st.error("Сервер недоступен")
            
    st.markdown("---")
    
    # Кнопка скачивания Excel
    try:
        excel_res = requests.get(f"{BASE_URL}/api/v1/metrics/excel")
        if excel_res.status_code == 200:
            st.download_button(
                label="📥 Скачать Excel-отчет",
                data=excel_res.content,
                file_name="fairflow_metrics.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
    except:
        st.warning("API отчетов недоступно")

# Вкладки интерфейса
tab_monitor, tab_constructor = st.tabs(["📊 Мониторинг", "⚙️ Конструктор параметров"])

with tab_monitor:
    try:
        metrics_res = requests.get(f"{BASE_URL}/api/v1/metrics")
        metrics_data = metrics_res.json()
        
        active_slots = metrics_data.get("active_slots", {})
        daily_counts = metrics_data.get("daily_counts", {})
        
        if not active_slots:
            st.info("Нет потока заявок. Запустите скрипт эмуляции АИС (simulator.py).")
        else:
            col1, col2 = st.columns(2)
            with col1:
                st.subheader("Текущая нагрузка (Взвешенная)")
                df_slots = pd.DataFrame(list(active_slots.items()), columns=["ID Исполнителя", "Суммарный вес заявок"]).set_index("ID Исполнителя")
                st.bar_chart(df_slots, color="#ff4b4b")
                
            with col2:
                st.subheader("Выполнено за сессию (Суточный лимит)")
                df_counts = pd.DataFrame(list(daily_counts.items()), columns=["ID Исполнителя", "Количество заявок"]).set_index("ID Исполнителя")
                st.bar_chart(df_counts, color="#0068c9")
    except Exception as e:
        st.error(f"Не удалось подключиться к ядру балансировщика: {e}")

with tab_constructor:
    st.subheader("Создание нового правила маршрутизации")
    st.markdown("Здесь вы можете добавить новые динамические параметры без изменения исходного кода (требование ТЗ).")
    
    with st.form("new_rule_form"):
        rule_id = st.text_input("ID Правила (напр. 'city_match')")
        rule_name = st.text_input("Название (напр. 'Совпадение по городу')")
        
        col1, col2, col3 = st.columns(3)
        with col1:
            field = st.text_input("Поле заявки (напр. 'order.city')")
        with col2:
            operator = st.selectbox("Оператор", ["==", "!=", ">", "<", ">=", "<=", "in"])
        with col3:
            target = st.text_input("Поле исполнителя (напр. 'user.city')")
            
        submitted = st.form_submit_button("Добавить правило в ядро")
        
        if submitted:
            import json
            # Попытка распарсить введенное значение как массив, если используется оператор in
            parsed_target = target
            if operator == "in":
                try:
                    parsed_target = json.loads(target)
                except:
                    # Если не JSON, оставляем как строку (возможно, поиск подстроки)
                    pass

            new_rule = {
                "id": rule_id,
                "name": rule_name,
                "conditions": [
                    {
                        "field": field,
                        "operator": operator,
                        # Если это константа (например массив), кладем в constant
                        "target_field": None if operator == "in" else target,
                        "constant": parsed_target if operator == "in" else None
                    }
                ]
            }

if auto_refresh:
    time.sleep(2)
    st.rerun()