# Presupuestos por Proyecto — App web (FastAPI + PostgreSQL + Vercel)

Aplicación web para proyectar y controlar **ingresos y egresos de múltiples proyectos**,
con **login multiusuario**, base de datos **PostgreSQL** y despliegue en **Vercel**.

Cada usuario ve solo sus propios proyectos. Los datos se guardan en la base de datos
(no en el navegador).

## Qué incluye

- **Backend** (`api/index.py`): API REST con FastAPI, autenticación JWT, SQLAlchemy.
- **Frontend** (`public/index.html`): dashboard con login, tablas editables y 4 gráficos
  (ingresos vs egresos, real vs proyectado, flujo de caja acumulado, margen %).
- **Vista consolidada** que suma todos tus proyectos.

## Estructura

```
presupuestos-app/
├── api/
│   └── index.py         # Backend FastAPI (toda la lógica y modelos)
├── public/
│   └── index.html       # Frontend (dashboard + login)
├── requirements.txt     # Dependencias Python
├── vercel.json          # Configuración de despliegue en Vercel
├── .env.example         # Variables de entorno de ejemplo
└── README.md
```

## Endpoints principales

| Método | Ruta | Descripción |
|--------|------|-------------|
| POST | `/api/auth/register` | Crear cuenta |
| POST | `/api/auth/login` | Iniciar sesión (devuelve token JWT) |
| GET  | `/api/auth/me` | Datos del usuario actual |
| GET  | `/api/projects` | Listar proyectos del usuario |
| POST | `/api/projects` | Crear proyecto (crea las 12 filas mensuales) |
| PUT  | `/api/projects/{id}` | Renombrar proyecto |
| DELETE | `/api/projects/{id}` | Eliminar proyecto |
| PUT  | `/api/projects/{id}/entries` | Guardar datos mensuales |
| GET  | `/api/health` | Verificar que la API está viva |

---

## Despliegue en Vercel (paso a paso)

### 1. Crea la base de datos PostgreSQL
Vercel no aloja la base de datos; usa una gestionada (capa gratis disponible):

- En el panel de Vercel: **Storage → Create Database → Postgres** (usa Neon por debajo), **o**
- Crea una gratis en https://neon.tech y copia la *connection string*.

Copia la cadena de conexión. Debe verse así:
```
postgresql://usuario:password@host/db?sslmode=require
```

### 2. Sube el proyecto a un repositorio Git
```bash
cd presupuestos-app
git init
git add .
git commit -m "App de presupuestos"
```
Súbelo a GitHub/GitLab (Vercel despliega desde ahí). También puedes usar la CLI: `npm i -g vercel` y luego `vercel`.

### 3. Importa el proyecto en Vercel
En https://vercel.com → **Add New → Project** → selecciona el repositorio.
Vercel detecta `vercel.json` automáticamente.

### 4. Configura las variables de entorno
En **Project Settings → Environment Variables** agrega:

| Variable | Valor |
|----------|-------|
| `DATABASE_URL` | La cadena de conexión de PostgreSQL del paso 1 |
| `SECRET_KEY` | Una clave larga y aleatoria (ver abajo cómo generarla) |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `720` (opcional) |

Generar la `SECRET_KEY`:
```bash
python -c "import secrets; print(secrets.token_hex(32))"
```
> Si usaste **Vercel Postgres**, la variable `POSTGRES_URL` se crea sola y el código también la reconoce; aun así conviene definir `SECRET_KEY`.

### 5. Despliega
Haz clic en **Deploy**. Al terminar tendrás una URL tipo `https://tu-proyecto.vercel.app`.
Las tablas de la base de datos se crean automáticamente en el primer arranque.

### 6. Usa la app
1. Abre la URL → pantalla de login.
2. Clic en **Regístrate**, crea tu cuenta.
3. Inicia sesión y empieza a cargar tus proyectos.

---

## Probar en tu computador (opcional)

Sin PostgreSQL, la app usa un archivo SQLite local para pruebas:

```bash
cd presupuestos-app
pip install -r requirements.txt
export SECRET_KEY="clave-de-prueba"          # en Windows: set SECRET_KEY=...
export DATABASE_URL="sqlite:///./local.db"    # opcional; si no, usa este por defecto
uvicorn api.index:app --reload
```
Abre http://localhost:8000

Para probar contra tu PostgreSQL real en local, exporta `DATABASE_URL` con la cadena de Neon/Vercel.

---

## Notas técnicas y siguientes pasos

- Los datos financieros están en formato numérico; el frontend los muestra en **COP**
  (cámbialo en `public/index.html`, función `fmt`).
- El modelo guarda por proyecto y mes: ingreso proyectado/real y egreso proyectado/real.
- **Vercel es serverless**: cada petición abre una conexión a la BD. Para mucho tráfico,
  usa la cadena *pooled* de Neon (la que incluye `-pooler` en el host).
- Mejoras naturales: roles (admin/editor/lectura), exportar a Excel/PDF desde el servidor,
  categorías de egreso, y varios años además de meses.
