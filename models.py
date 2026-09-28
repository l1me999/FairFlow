from sqlalchemy import Column, Integer, String, Float, ForeignKey, JSON
from sqlalchemy.orm import relationship
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