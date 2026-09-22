# core — API con Django REST Framework

Proyecto base de **Django REST Framework**. El proyecto principal se llama `core`
(la configuración raíz vive en la carpeta `core/`) y la lógica de la API está en
la app `api/`.

## Stack

- Django 6.0
- Django REST Framework
- SimpleJWT (autenticación con tokens)
- django-cors-headers
- python-dotenv (variables de entorno)
- SQLite (por defecto)

## Estructura

```
backend/
├── core/               # Proyecto principal (settings, urls, wsgi/asgi)
│   ├── settings.py
│   └── urls.py
├── api/                # App con la lógica de la API
│   ├── models.py       # Modelo Item
│   ├── serializers.py
│   ├── views.py        # ItemViewSet + health
│   └── urls.py
├── env/                # Entorno virtual
├── manage.py
├── requirements.txt
├── .env                # Variables de entorno y secretos (NO se versiona)
└── README.md
```

> **Nota (Windows):** la ruta del proyecto es muy larga y Windows limita las rutas
> a 260 caracteres. Si en otro equipo `pip install` falla al instalar
> `djangorestframework-simplejwt`, habilita el soporte de rutas largas
> (`LongPathsEnabled=1` en el registro, requiere admin) o instala ese paquete
> montando un disco corto con `subst`.

## Puesta en marcha

```powershell
# 1. Activar el entorno virtual (ya creado en ./env)
.\env\Scripts\Activate.ps1

# 2. (Opcional) instalar dependencias en otro equipo
pip install -r requirements.txt

# 3. Crear el archivo .env (ver "Variables de entorno" más abajo).
#    Está gitignoreado, así que en un equipo nuevo hay que crearlo a mano.

# 4. Aplicar migraciones
python manage.py migrate

# 5. Crear superusuario (para el admin y obtener tokens)
python manage.py createsuperuser

# 6. Levantar el servidor
python manage.py runserver
```

## Variables de entorno

El backend lee su configuración de `backend/.env`, que **está en `.gitignore`**
porque contiene secretos. En un equipo nuevo hay que crearlo con estas claves:

```ini
# --- Núcleo ---
SECRET_KEY=            # genérala con el comando de abajo
DEBUG=True
ALLOWED_HOSTS=localhost,127.0.0.1

# --- JWT ---
JWT_SIGNING_KEY=       # opcional; si se omite usa SECRET_KEY

# --- Sesión ---
# Minutos de inactividad tras los cuales se cierra la sesión: 1 = 1 minuto,
# 30 = 30 minutos… 0 (o ausente) desactiva el cierre automático.
# El corte lo aplica el servidor (users/session.py) y el valor viaja en
# /api/me/, así que se cambia aquí y el frontend obedece sin recompilarse.
SESSION_IDLE_TIMEOUT_MINUTES=1

# --- CORS (allowlist explícita; NO uses el comodín en producción) ---
CORS_ALLOW_ALL_ORIGINS=False
CORS_ALLOWED_ORIGINS=http://localhost:5173,http://127.0.0.1:5173

# --- Solo producción (DEBUG=False) ---
CSRF_TRUSTED_ORIGINS=

# --- WhaleTV Device Lock API (Zeasn) — SECRETOS ---
WHALETV_LOCK_API_ACCESS_KEY=
WHALETV_LOCK_API_SECRET_KEY=

# --- WhaleTV Portal (automatización Selenium) — SECRETOS ---
WHALETV_PORTAL_EMAIL=
WHALETV_PORTAL_PASSWORD=
```

Genera un `SECRET_KEY` seguro con:

```powershell
python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

Con `DEBUG=False` el arranque **falla** si faltan los secretos de WhaleTV o si
sigue la clave de desarrollo. Ver [SECURITY.md](SECURITY.md).

## Endpoints

| Método            | Ruta                     | Descripción                     |
|-------------------|--------------------------|---------------------------------|
| GET               | `/api/health/`           | Chequeo de estado               |
| GET/POST          | `/api/items/`            | Listar / crear items            |
| GET/PUT/PATCH/DEL | `/api/items/{id}/`       | Detalle / editar / borrar item  |
| POST              | `/api/token/`            | Obtener access + refresh token  |
| POST              | `/api/token/refresh/`    | Renovar access token            |
| —                 | `/admin/`                | Panel de administración         |

### Sesión del portal

| Método | Ruta                                   | Descripción                          |
|--------|----------------------------------------|--------------------------------------|
| POST   | `/api/auth/token/`                     | Iniciar sesión (email + contraseña)  |
| POST   | `/api/auth/logout/`                    | Cerrar la propia sesión              |
| POST   | `/api/usuarios/{id}/cerrar-sesion/`    | Cerrar la sesión de otro (admin)     |

Toda petición autenticada debe llevar, además del `Authorization: Bearer`, la
cabecera **`X-Device-Id`** con el identificador del navegador. Es lo que sostiene
la sesión única (una cuenta = un navegador): el login devuelve el `device_id`
asignado y el cliente lo reenvía en cada llamada. Un token usado desde otro
navegador se rechaza. Ver `users/session.py` y `SECURITY.md`.

### Autenticación

```bash
# Obtener token
curl -X POST http://127.0.0.1:8000/api/token/ \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "tu_password"}'

# Usar el token
curl http://127.0.0.1:8000/api/items/ \
  -H "Authorization: Bearer <access_token>"
```

La lectura es pública (`IsAuthenticatedOrReadOnly`); crear/editar/borrar requiere token.
