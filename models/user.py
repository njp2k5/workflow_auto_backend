from sqlalchemy import Column, Integer, String
from db.base import Base

class User(Base):
    """User/Member model - maps to members table."""
    __tablename__ = "members"

    member_id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    member_name = Column(String(120), nullable=False, index=True)
    designation = Column(String(100), nullable=False)
    password = Column(String(120), nullable=False)
