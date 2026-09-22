# Seguridad del backend

Resumen de las medidas de seguridad implementadas y la checklist de despliegue.

## Autenticación y tokens (JWT)

- **Login por email** con `djangorestframework-simplejwt`.
- **Access token de vida corta** (15 min) → ventana mínima si se filtra.
- **Refresh token** de 7 días con **rotación** (`ROTATE_REFRESH_TOKENS`).
- Claims **mínimos** (solo `user_id`): el JWT no está cifrado, no se guarda PII.
- Header `Authorization: Bearer <token>` (stateless, sin cookies → sin CSRF en la API).
- `UPDATE_LAST_LOGIN=True` para auditoría básica de accesos.

### Revocación server-side (logout real) ✔

- Cada JWT lleva la **versión de token** con la que se emitió (claim `tv`).
- `POST /api/auth/logout/` incrementa `user.token_version` → **todos** los tokens
  anteriores (access + refresh, incluso los que se hubieran filtrado) dejan de
  validar de inmediato. `users/authentication.RevocationAwareJWTAuthentication`
  compara el claim en cada request; el refresh también se valida contra la versión.
- Ventaja sobre comparar por `iat`: es exacto, sin la ambigüedad de segundos que
  impediría volver a iniciar sesión justo después del logout.
- Se eligió este mecanismo porque la app `token_blacklist` de SimpleJWT no se
  puede instalar aquí: una de sus migraciones supera el **límite de 260 caracteres
  de Windows** en esta ruta de OneDrive (verificado: 265). Este enfoque no
  necesita esa app y cubre el caso de uso (cierre de sesión / revocación).

### Cierre por inactividad ✔

- `SESSION_IDLE_TIMEOUT_MINUTES` (en `.env`, en minutos; `0` lo desactiva) define
  cuánto puede estar quieto un usuario antes de que su sesión muera.
- Lo aplica el **servidor**: cada petición autenticada actualiza `user.last_activity`
  y, si el hueco supera el plazo, el token deja de validar (`users/session.py`).
  Se comprueba tanto en la autenticación como en el **refresh**: si solo se mirara
  en la primera, bastaría con renovar el token para resucitar una sesión muerta.
- El valor viaja en `/api/me/` (`session_timeout_minutes`) para que el frontend
  lleve al usuario al login a tiempo. Eso es comodidad de interfaz, **no** la
  barrera: un token usado desde `curl` se rechaza igual.

### Sesión única: una cuenta, un navegador ✔

- Una cuenta solo puede tener sesión abierta en **un navegador**, y un navegador
  solo puede tener abierta **una cuenta**. El segundo intento recibe un `409`
  (`sesion_en_otro_dispositivo` / `dispositivo_ocupado`).
- Cada cliente se identifica con la cabecera `X-Device-Id`; la cuenta apunta a
  ese navegador (`user.device_id`) mientras la sesión viva. Un access o refresh
  **usado desde otro navegador se rechaza**, así que un token robado no sirve
  por sí solo.
- Se libera al cerrar sesión, al caducar por inactividad, o cuando un
  administrador fuerza el cierre con `POST /api/usuarios/<id>/cerrar-sesion/`
  (válvula de escape para cuentas atascadas en un equipo inaccesible).
- **Alcance real:** el identificador lo guarda el navegador, así que se puede
  borrar para presentarse como uno nuevo. Eso no da dos sesiones simultáneas —la
  cuenta solo admite un dispositivo y el resto se rechaza—, pero significa que
  "un navegador, una cuenta" es una restricción de uso, no una barrera
  criptográfica. Atar la sesión al hardware exigiría certificados de cliente.

## Contraseñas

- **Hashing Argon2id** como algoritmo principal (recomendación OWASP), con
  PBKDF2/bcrypt de respaldo para verificar y re-hashear.
- Validadores reforzados: longitud mínima **10**, similitud con datos del
  usuario, contraseñas comunes y puramente numéricas.
- Nunca se serializa la contraseña (`write_only`).

## Autorización

- **Secure-by-default:** `IsAuthenticated` global; solo la obtención/refresh de
  token se abren con `AllowAny`.
- **Sin auto-registro público:** el endpoint público de alta se **eliminó** (era
  un vector: cualquiera podía crear una cuenta con acceso de lectura a
  dispositivos y a los PINs de desbloqueo). Las cuentas las crea un Administrador
  en `POST /api/usuarios/` (`IsAdminRole`).
- **Roles** (Administrador/Operador/Consulta): escritura reservada a Operador+ y la
  gestión de usuarios solo a Administrador (`users/permissions.py`).
- **Anti mass-assignment:** el alta solo acepta `email`, `password`,
  `first_name`, `last_name`, `role`. `is_staff`/`is_superuser`/`groups` no son
  asignables desde la API (sin escalado de privilegios).

## Rate limiting (anti fuerza bruta / abuso)

| Scope      | Límite   | Aplica a                    |
|------------|----------|-----------------------------|
| `login`    | 5/min    | obtención y refresh de token |
| `register` | 10/hora  | alta de cuentas             |
| `anon`     | 60/hora  | peticiones anónimas         |
| `user`     | 1000/hora| peticiones autenticadas     |

## Cabeceras y transporte

- `X-Frame-Options: DENY`, `SECURE_CONTENT_TYPE_NOSNIFF`, `Referrer-Policy: same-origin`.
- Cookies `HttpOnly` + `SameSite=Lax`.
- En producción (`DEBUG=False`): `SECURE_SSL_REDIRECT`, cookies `Secure`,
  **HSTS** 1 año con subdominios y preload, `SECURE_PROXY_SSL_HEADER`.
- **CORS** con allowlist explícita (nunca comodín en producción).
- El navegador de la API (BrowsableAPIRenderer) solo se sirve en desarrollo.

## Secretos

- `SECRET_KEY` y `JWT_SIGNING_KEY` vienen de variables de entorno.
- **Credenciales de integración WhaleTV** (llaves de la Device Lock API y usuario/
  contraseña del Portal) ya **no están hardcodeadas** en `settings.py`: se leen del
  entorno (`.env`, gitignoreado). Con `DEBUG=False` un guard **impide arrancar** si
  falta alguna. Las claves esperadas están documentadas en [README.md](README.md).
- Guard que **impide arrancar con `DEBUG=False`** si sigue la clave de desarrollo.
- `.env` está en `.gitignore` (no se versiona).

> ⚠️ **Rotar credenciales expuestas:** las llaves de la Lock API y la contraseña
> del Portal estuvieron en texto plano en el código. Si ese código se compartió o
> versionó alguna vez (p. ej. el repo hermano `whaletv`), conviene **rotarlas** en
> Zeasn/WhaleTV, ya que moverlas a `.env` no borra copias previas.

## Checklist de despliegue a producción

1. `DEBUG=False` y `SECRET_KEY` fuerte (ver comando en [README.md](README.md)).
2. `ALLOWED_HOSTS` y `CSRF_TRUSTED_ORIGINS` con tus dominios reales.
3. `CORS_ALLOWED_ORIGINS` con el dominio del frontend; `CORS_ALLOW_ALL_ORIGINS=False`.
4. Servir **solo HTTPS** (el proxy debe enviar `X-Forwarded-Proto: https`).
5. Migrar a **PostgreSQL** (SQLite es solo para desarrollo).
6. Definir los **secretos WhaleTV** (`WHALETV_LOCK_API_*`, `WHALETV_PORTAL_*`) en
   el entorno; sin ellos el arranque falla con `DEBUG=False`.
7. Verificar con: `python manage.py check --deploy`.

> Estado actual: `check --deploy` pasa **sin issues** con configuración de
> producción simulada.
