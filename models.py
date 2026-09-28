from sqlalchemy import Column, Integer, String, Float, ForeignKey, JSON, DateTime
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from database import Base

class UserModel(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    status = Column(String, nullable=False, default="active")
    
    settings = relationship("UserSettingsModel", back_populates="user", uselist=False, cascade="all, delete-orphan")
    orders = relationship("OrderModel", back_populates="executor")

class UserSettingsModel(Base):
    __tablename__ = "user_settings"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, unique=True)
    
    max_daily_limit = Column(Integer, nullable=True)
    capacity = Column(Float, default=1.0)
    
    # Динамические параметры, которые можно менять "на лету"
    dynamic_params = Column(JSON, default={})
    
    user = relationship("UserModel", back_populates="settings")

class OrderModel(Base):
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True, index=True)
    parent_id = Column(Integer, ForeignKey("orders.id"), nullable=True)
    executor_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    
    sum = Column(Integer, nullable=False)
    order_type = Column(String, nullable=False)
    weight = Column(Float, default=1.0)
    status = Column(String, nullable=False, default="processed")
    
    dynamic_params = Column(JSON, default={})
    
    executor = relationship("UserModel", back_populates="orders")

class MetricSnapshotModel(Base):
    """Таблица для хранения сводных и агрегированных метрик (бонусное задание)"""
    __tablename__ = "metric_snapshots"

    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    total_active_users = Column(Integer, default=0)
    total_orders_processed = Column(Integer, default=0)
    total_orders_accepted = Column(Integer, default=0)
    
    # Средняя нагрузка (вес) на одного исполнителя в момент среза
    average_user_load = Column(Float, default=0.0)