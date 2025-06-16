from sqlalchemy import create_engine, Column, Integer, BigInteger, String, Boolean, ForeignKey
from sqlalchemy.orm import declarative_base, relationship, sessionmaker
import os

DATABASE_URL = os.getenv("DATABASE_URL")

engine = create_engine(DATABASE_URL, connect_args={"sslmode": "require"})
Session = sessionmaker(bind=engine)
Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    id = Column(BigInteger, primary_key=True)  # ID do Telegram
    username = Column(String)

    channels = relationship("Channel", back_populates="owner")
    groups = relationship("Group", back_populates="owner")

class Channel(Base):
    __tablename__ = "channels"
    id = Column(BigInteger, primary_key=True)  # ID do canal no Telegram
    owner_id = Column(BigInteger, ForeignKey("users.id"))
    username = Column(String)
    title = Column(String)

    owner = relationship("User", back_populates="channels")
    group_links = relationship("GroupChannel", back_populates="channel")

class Group(Base):
    __tablename__ = "groups"
    id = Column(Integer, primary_key=True)
    name = Column(String)
    owner_id = Column(BigInteger, ForeignKey("users.id"))

    owner = relationship("User", back_populates="groups")
    channels = relationship("GroupChannel", back_populates="group", cascade="all, delete-orphan")

class GroupChannel(Base):
    __tablename__ = "group_channels"
    id = Column(Integer, primary_key=True)
    group_id = Column(Integer, ForeignKey("groups.id"))
    channel_id = Column(BigInteger, ForeignKey("channels.id"))
    accepted = Column(Boolean, default=False)

    group = relationship("Group", back_populates="channels")
    channel = relationship("Channel", back_populates="group_links")

Base.metadata.create_all(engine)
