"""
models.py — SQLAlchemy ORM table definitions.

Defines the three database tables used by the Cost Optimizer Agent:
  - Instance  → `instances` table
  - Cycle     → `cycles` table
  - KillSwitch → `kill_switch` table

This file contains ONLY ORM table definitions — no logic, no DB connection setup.
"""

from sqlalchemy import Boolean, Column, DateTime, Integer, String
from sqlalchemy.orm import declarative_base
from sqlalchemy.sql import func

Base = declarative_base()


class Instance(Base):
    """Snapshot of an EC2 instance, updated each orchestrator cycle."""

    __tablename__ = "instances"

    id                    = Column(String, primary_key=True)       # EC2 instance-id
    name                  = Column(String)
    instance_type         = Column(String)
    status                = Column(String)
    tags                  = Column(String)                         # JSON string
    last_action           = Column(String, nullable=True)
    last_action_reasoning = Column(String, nullable=True)
    pending_resize        = Column(String, nullable=True)          # target type e.g. "t3.small"
    updated_at            = Column(DateTime, default=func.now(), onupdate=func.now())


class Cycle(Base):
    """One action record written per validated action in each orchestrator cycle."""

    __tablename__ = "cycles"

    id            = Column(Integer, primary_key=True, autoincrement=True)
    cycle         = Column(Integer)
    timestamp     = Column(DateTime, default=func.now())
    instance_id   = Column(String)
    instance_name = Column(String)
    action        = Column(String)
    reasoning     = Column(String)
    validated     = Column(Boolean)
    aws_response  = Column(String)                                 # JSON string


class KillSwitch(Base):
    """Each row represents a toggle event; the most recent row is the current state."""

    __tablename__ = "kill_switch"

    id         = Column(Integer, primary_key=True, autoincrement=True)
    active     = Column(Boolean, nullable=False)
    toggled_at = Column(DateTime, default=func.now())
