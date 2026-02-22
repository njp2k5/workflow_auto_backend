"""
SQLAlchemy models for the meeting processor.
Matches the PostgreSQL schema with members, transcription, meetings, and tasks tables.
"""
from datetime import datetime, date
from typing import Optional, List

from sqlalchemy import (
    Column, 
    Integer, 
    String, 
    Text, 
    Date,
    DateTime, 
    ForeignKey,
    Index,
)
from sqlalchemy.orm import declarative_base, relationship
from sqlalchemy.sql import func

Base = declarative_base()


class Member(Base):
    """
    Model for team members.
    """
    __tablename__ = "members"
    
    member_id = Column(Integer, primary_key=True, autoincrement=True)
    member_name = Column(String(120), nullable=False)
    designation = Column(String(100), nullable=False)
    password = Column(String(120), nullable=False)
    
    # Relationship to tasks
    tasks = relationship("Task", back_populates="member")
    
    def __repr__(self) -> str:
        return f"<Member(member_id={self.member_id}, member_name={self.member_name})>"
    
    def to_dict(self) -> dict:
        return {
            "member_id": self.member_id,
            "member_name": self.member_name,
            "designation": self.designation,
        }


class Transcription(Base):
    """
    Model for meeting transcriptions.
    """
    __tablename__ = "transcription"
    
    transcription_id = Column(Integer, primary_key=True, autoincrement=True)
    transcription_summary = Column(Text, nullable=False)
    
    # Relationship to meetings
    meetings = relationship("Meeting", back_populates="transcription")
    
    def __repr__(self) -> str:
        return f"<Transcription(transcription_id={self.transcription_id})>"
    
    def to_dict(self) -> dict:
        return {
            "transcription_id": self.transcription_id,
            "transcription_summary": self.transcription_summary,
        }


class Meeting(Base):
    """
    Model for meetings.
    """
    __tablename__ = "meetings"
    
    meeting_id = Column(Integer, primary_key=True, autoincrement=True)
    meeting_date = Column(Date, nullable=False)
    transcription_id = Column(
        Integer, 
        ForeignKey("transcription.transcription_id"), 
        nullable=False
    )
    # Confluence integration
    confluence_page_id = Column(
        String(50),
        nullable=True,
        comment="Confluence page ID for meeting notes"
    )
    confluence_url = Column(
        String(500),
        nullable=True,
        comment="URL to Confluence meeting page"
    )
    
    # Relationship to transcription
    transcription = relationship("Transcription", back_populates="meetings")
    
    __table_args__ = (
        Index('ix_meetings_date', 'meeting_date'),
        Index('ix_meetings_transcription', 'transcription_id'),
    )
    
    def __repr__(self) -> str:
        return f"<Meeting(meeting_id={self.meeting_id}, meeting_date={self.meeting_date})>"
    
    def to_dict(self) -> dict:
        meeting_date_val = getattr(self, 'meeting_date', None)
        return {
            "meeting_id": self.meeting_id,
            "meeting_date": meeting_date_val.isoformat() if meeting_date_val else None,
            "transcription_id": self.transcription_id,
            "transcription_summary": self.transcription.transcription_summary if self.transcription else None,
            "confluence_page_id": self.confluence_page_id,
            "confluence_url": self.confluence_url,
        }


class Task(Base):
    """
    Model for tasks assigned to members.
    """
    __tablename__ = "tasks"
    
    task_id = Column(Integer, primary_key=True, autoincrement=True)
    member_id = Column(
        Integer, 
        ForeignKey("members.member_id"), 
        nullable=False
    )
    description = Column(Text, nullable=False)
    deadline = Column(Date, nullable=False)
    
    # Relationship to member
    member = relationship("Member", back_populates="tasks")
    
    __table_args__ = (
        Index('ix_tasks_member', 'member_id'),
        Index('ix_tasks_deadline', 'deadline'),
    )
    
    def __repr__(self) -> str:
        return f"<Task(task_id={self.task_id}, member_id={self.member_id})>"
    
    def to_dict(self) -> dict:
        deadline_val = getattr(self, 'deadline', None)
        return {
            "task_id": self.task_id,
            "member_id": self.member_id,
            "member_name": self.member.member_name if self.member else None,
            "description": self.description,
            "deadline": deadline_val.isoformat() if deadline_val else None,
        }


class ProcessingLog(Base):
    """
    Model for tracking processing attempts and errors.
    """
    __tablename__ = "processing_logs"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    meeting_id = Column(
        Integer,
        ForeignKey("meetings.meeting_id"),
        nullable=True,
        index=True,
        comment="Meeting ID if associated"
    )
    step = Column(
        String(100),
        nullable=False,
        comment="Processing step name"
    )
    status = Column(
        String(50),
        nullable=False,
        comment="Status: started, completed, failed"
    )
    message = Column(
        Text,
        nullable=True,
        comment="Status message or error details"
    )
    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False
    )
    
    __table_args__ = (
        Index('ix_processing_logs_meeting_step', 'meeting_id', 'step'),
    )
    
    def __repr__(self) -> str:
        return f"<ProcessingLog(meeting_id={self.meeting_id}, step={self.step}, status={self.status})>"
