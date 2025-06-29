import os
from sqlalchemy import create_engine, Column, Integer, BigInteger, String, Boolean, ForeignKey
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL")

engine = create_engine(
    DATABASE_URL,
    connect_args={"sslmode": "require"},
    pool_pre_ping=True,       # ✅ Verifica conexão antes de usar
    pool_recycle=1800         # ✅ Recicla conexões a cada 30 minutos
)

Session = sessionmaker(bind=engine)
Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    id = Column(BigInteger, primary_key=True)
    username = Column(String)

class Channel(Base):
    __tablename__ = "channels"
    id = Column(BigInteger, primary_key=True)
    owner_id = Column(BigInteger, ForeignKey("users.id"))
    username = Column(String)
    title = Column(String)
    authenticated = Column(Boolean, default=False)
    owner = relationship("User", foreign_keys=[owner_id])

class Group(Base):
    __tablename__ = "groups"
    id = Column(Integer, primary_key=True)
    name = Column(String)
    owner_id = Column(BigInteger, ForeignKey("users.id"))
    owner = relationship("User", foreign_keys=[owner_id])
    channels = relationship("GroupChannel", back_populates="group")

class GroupChannel(Base):
    __tablename__ = "group_channels"
    id = Column(Integer, primary_key=True)
    group_id = Column(Integer, ForeignKey("groups.id"))
    channel_id = Column(BigInteger, ForeignKey("channels.id"))
    inviter_id = Column(BigInteger, ForeignKey("users.id"), nullable=True)
    accepted = Column(Boolean, default=None)
    group = relationship("Group", back_populates="channels")
    channel = relationship("Channel")
    inviter = relationship("User", foreign_keys=[inviter_id])

Base.metadata.create_all(engine)
