"""
API de Presupuestos por Proyecto
FastAPI + PostgreSQL (SQLAlchemy) + autenticacion JWT multiusuario.
Pensado para desplegar como funcion serverless en Vercel.

Todo el backend vive en este archivo para evitar problemas de rutas de import
en el runtime serverless. El frontend estatico se sirve desde /public.
"""

import os
import base64
import hashlib
import hmac
import secrets as _secrets
import datetime as dt
from typing import List, Optional
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr, Field
from jose import jwt, JWTError

from sqlalchemy import (
    create_engine, Column, Integer, String, Float, ForeignKey,
    UniqueConstraint, DateTime, func
)
from sqlalchemy.orm import sessionmaker, relationship, declarative_base, Session

# --------------------------------------------------------------------------
# Configuracion
# --------------------------------------------------------------------------
def _db_url() -> str:
    url = (os.getenv("DATABASE_URL")
           or os.getenv("POSTGRES_URL")
           or os.getenv("POSTGRES_PRISMA_URL")
           or "sqlite:///./local.db")  # fallback para pruebas locales sin Postgres
    # SQLAlchemy requiere el esquema postgresql:// (Vercel/Neon a veces dan postgres://)
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    # Normaliza los parametros de la cadena de Postgres:
    #  - elimina channel_binding (psycopg2 puede fallar con require)
    #  - garantiza sslmode=require (Neon lo exige)
    if url.startswith("postgresql"):
        parts = urlsplit(url)
        q = [(k, v) for k, v in parse_qsl(parts.query) if k.lower() != "channel_binding"]
        if not any(k.lower() == "sslmode" for k, _ in q):
            q.append(("sslmode", "require"))
        url = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(q), parts.fragment))
    return url

DATABASE_URL = _db_url()
SECRET_KEY = os.getenv("SECRET_KEY", "dev-insecure-change-me")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "720"))

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, pool_pre_ping=True, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

# --------------------------------------------------------------------------
# Modelos ORM
# --------------------------------------------------------------------------
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    name = Column(String, nullable=False, default="")
    hashed_password = Column(String, nullable=False)
    created_at = Column(DateTime, server_default=func.now())
    projects = relationship("Project", back_populates="owner", cascade="all, delete-orphan")


class Project(Base):
    __tablename__ = "projects"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    owner_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    created_at = Column(DateTime, server_default=func.now())
    owner = relationship("User", back_populates="projects")
    entries = relationship("MonthlyEntry", back_populates="project", cascade="all, delete-orphan")


class MonthlyEntry(Base):
    """Un registro por proyecto y mes (1-12)."""
    __tablename__ = "monthly_entries"
    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    month = Column(Integer, nullable=False)               # 1..12
    income_proj = Column(Float, nullable=False, default=0)   # ingreso proyectado
    income_real = Column(Float, nullable=False, default=0)   # ingreso real
    expense_proj = Column(Float, nullable=False, default=0)  # egreso proyectado
    expense_real = Column(Float, nullable=False, default=0)  # egreso real
    project = relationship("Project", back_populates="entries")
    __table_args__ = (UniqueConstraint("project_id", "month", name="uix_project_month"),)


def init_db():
    """Crea las tablas si no existen. Se puede reintentar sin romper la app."""
    Base.metadata.create_all(bind=engine)

try:
    init_db()
except Exception as _e:  # no impedir el arranque si la BD aun no responde
    print("Aviso: no se pudieron crear las tablas al iniciar:", _e)

# --------------------------------------------------------------------------
# Esquemas Pydantic
# --------------------------------------------------------------------------
class UserCreate(BaseModel):
    email: EmailStr
    name: str = ""
    password: str = Field(min_length=6)

class UserOut(BaseModel):
    id: int
    email: EmailStr
    name: str
    class Config: from_attributes = True

class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"

class EntryIn(BaseModel):
    month: int = Field(ge=1, le=12)
    income_proj: float = 0
    income_real: float = 0
    expense_proj: float = 0
    expense_real: float = 0

class EntryOut(EntryIn):
    class Config: from_attributes = True

class ProjectIn(BaseModel):
    name: str

class ProjectOut(BaseModel):
    id: int
    name: str
    entries: List[EntryOut] = []
    class Config: from_attributes = True

# --------------------------------------------------------------------------
# Utilidades de auth
# --------------------------------------------------------------------------
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

_PBKDF2_ITERS = 200_000

def hash_password(p: str) -> str:
    """Hash de contrasena con PBKDF2-HMAC-SHA256 (libreria estandar, sin dependencias nativas)."""
    salt = _secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", p.encode("utf-8"), salt, _PBKDF2_ITERS)
    return "pbkdf2_sha256${}${}${}".format(
        _PBKDF2_ITERS,
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(dk).decode("ascii"),
    )

def verify_password(p: str, stored: str) -> bool:
    try:
        algo, iters, salt_b64, dk_b64 = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(dk_b64)
        dk = hashlib.pbkdf2_hmac("sha256", p.encode("utf-8"), salt, int(iters))
        return hmac.compare_digest(dk, expected)
    except Exception:
        return False

def create_access_token(sub: str) -> str:
    expire = dt.datetime.utcnow() + dt.timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    return jwt.encode({"sub": sub, "exp": expire}, SECRET_KEY, algorithm=ALGORITHM)

def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> User:
    cred_exc = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                             detail="Credenciales invalidas",
                             headers={"WWW-Authenticate": "Bearer"})
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email = payload.get("sub")
        if not email:
            raise cred_exc
    except JWTError:
        raise cred_exc
    user = db.query(User).filter(User.email == email).first()
    if not user:
        raise cred_exc
    return user

def ensure_12_entries(db: Session, project: Project):
    """Garantiza que un proyecto tenga las 12 filas mensuales."""
    existing = {e.month for e in project.entries}
    for m in range(1, 13):
        if m not in existing:
            db.add(MonthlyEntry(project_id=project.id, month=m))
    db.commit()

# --------------------------------------------------------------------------
# App
# --------------------------------------------------------------------------
app = FastAPI(title="Presupuestos por Proyecto", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/api/health")
def health(db: Session = Depends(get_db)):
    result = {"status": "ok", "db": DATABASE_URL.split("://")[0]}
    try:
        init_db()  # asegura que las tablas existan
        from sqlalchemy import text
        db.execute(text("SELECT 1"))
        result["db_connection"] = "ok"
    except Exception as e:
        result["status"] = "error"
        result["db_connection"] = "fail"
        result["detail"] = str(e)[:300]
    return result

# ---- Auth ----
@app.post("/api/auth/register", response_model=UserOut)
def register(data: UserCreate, db: Session = Depends(get_db)):
    if db.query(User).filter(User.email == data.email).first():
        raise HTTPException(status_code=400, detail="El correo ya esta registrado")
    user = User(email=data.email, name=data.name, hashed_password=hash_password(data.password))
    db.add(user); db.commit(); db.refresh(user)
    return user

@app.post("/api/auth/login", response_model=Token)
def login(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    # OAuth2PasswordRequestForm usa 'username'; aqui equivale al email
    user = db.query(User).filter(User.email == form.username).first()
    if not user or not verify_password(form.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Correo o contrasena incorrectos")
    return Token(access_token=create_access_token(user.email))

@app.get("/api/auth/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return user

# ---- Proyectos ----
@app.get("/api/projects", response_model=List[ProjectOut])
def list_projects(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    projs = db.query(Project).filter(Project.owner_id == user.id).order_by(Project.id).all()
    for p in projs:
        if len(p.entries) < 12:
            ensure_12_entries(db, p); db.refresh(p)
        p.entries.sort(key=lambda e: e.month)
    return projs

@app.post("/api/projects", response_model=ProjectOut)
def create_project(data: ProjectIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    proj = Project(name=data.name, owner_id=user.id)
    db.add(proj); db.commit(); db.refresh(proj)
    ensure_12_entries(db, proj); db.refresh(proj)
    proj.entries.sort(key=lambda e: e.month)
    return proj

def _owned_project(project_id: int, user: User, db: Session) -> Project:
    proj = db.query(Project).filter(Project.id == project_id, Project.owner_id == user.id).first()
    if not proj:
        raise HTTPException(status_code=404, detail="Proyecto no encontrado")
    return proj

@app.put("/api/projects/{project_id}", response_model=ProjectOut)
def rename_project(project_id: int, data: ProjectIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    proj = _owned_project(project_id, user, db)
    proj.name = data.name; db.commit(); db.refresh(proj)
    proj.entries.sort(key=lambda e: e.month)
    return proj

@app.delete("/api/projects/{project_id}")
def delete_project(project_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    proj = _owned_project(project_id, user, db)
    db.delete(proj); db.commit()
    return {"deleted": project_id}

@app.put("/api/projects/{project_id}/entries", response_model=ProjectOut)
def update_entries(project_id: int, entries: List[EntryIn],
                   user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Actualiza (upsert) los datos mensuales de un proyecto."""
    proj = _owned_project(project_id, user, db)
    by_month = {e.month: e for e in proj.entries}
    for item in entries:
        row = by_month.get(item.month)
        if row is None:
            row = MonthlyEntry(project_id=proj.id, month=item.month)
            db.add(row)
        row.income_proj = item.income_proj
        row.income_real = item.income_real
        row.expense_proj = item.expense_proj
        row.expense_real = item.expense_real
    db.commit(); db.refresh(proj)
    proj.entries.sort(key=lambda e: e.month)
    return proj
