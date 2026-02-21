from sqlalchemy import Column, Integer, String
from db.base import Base

class User(Base):
    __tablename__ = "members"

    member_id = Column(Integer, primary_key=True, index=True)
    member_name = Column(String(120), unique=True, index=True, nullable=False)
    designation = Column(String(100), nullable=False)
    password = Column(String(120), nullable=False)
