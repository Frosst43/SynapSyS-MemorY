# v0.2.0.dev3: vinculacion a UID Unix y permisos de socket

**Estado: experimental, sin IA, no garantiza sandbox de archivos.**

## Reglas que ahora aplica el broker

- Cada concesion (`grant` / `switch`) vincula el token a un **UID de proceso** (`peer_uid`). Por defecto usa el UID efectivo del broker para pruebas locales. Para desplegar con cuentas Linux separadas, el administrador indica `--peer-uid UID_DEL_AGENTE`.
- En cada peticion de datos el servidor lee las credenciales reales del socket Unix desde el kernel (`SO_PEERCRED` en Linux; `getpeereid` donde exista). Rechaza token correcto si el UID de su portador no coincide. El UID **nunca** se acepta desde el JSON del agente.
- La administracion, ademas de requerir una clave larga, solo acepta procesos del UID propietario del broker. Nunca se debe entregar la clave a un agente.
- Se deniegan directorios de sockets/base accesibles por otros, directorios modificables por el grupo, SQLite que no pertenezca al broker, base con permisos distintos de privados (0600), symlinks directos y hardlinks de SQLite.
- SQLite nuevo se reserva con modo `0600` antes de abrirse, incluso con umask permisiva.
- El socket de administracion permanece `0600`. Opcionalmente, `--client-gid` permite dar `0660` **solo** al socket de datos para un grupo Linux expresamente preparado. Su directorio debe permitir el paso del grupo sin escritura grupal ni acceso para otros, p. ej. `0710`. El programa no cambia permisos de directorios arbitrarios.

## Despliegue previsto (aun NO validado con dos usuarios del sistema)

1. Crear cuentas del sistema separadas para el broker y los ejecutores de IA y un grupo especifico de clientes de memoria.
2. Ejecutar el broker como propietario de SQLite. No dar a agentes acceso directo a la base, backups, secreto administrativo o socket administrativo.
3. Conceder al grupo de agentes solo permiso de paso al directorio del socket de datos y acceso a este. Revisar TODOS los directorios superiores.
4. Al autorizar un proyecto, fijar UID real del agente con `--peer-uid`. No sustituir consentimiento expreso por un mensaje del modelo.
5. Arrancar cada agente en un **sandbox de archivos real** que solo permita las rutas autorizadas de ese proyecto. Al cambiar, cerrar sandbox antiguo, revocar token y crear otro.
6. Probar con dos UIDs reales sin privilegios, denegacion de base de datos, denegacion del socket admin, lectura autorizada, cambio y escritura ajena prohibida.

## CLI de referencia (esquema, no ejecutar sobre datos reales)

```bash
# Directorio del socket preparado por administrador con permisos seguros:
SYMEM_ADMIN_KEY=... synapsys-memory-broker \
  --socket /run/synapsys-memory/data.sock \
  --admin-socket /run/synapsys-memory/admin.sock \
  serve --db /var/lib/synapsys-memory/memory.sqlite3 \
  --client-gid GID_CLIENTES

SYMEM_ADMIN_KEY=... synapsys-memory-broker \
  --admin-socket /run/synapsys-memory/admin.sock \
  grant --session sesion-unica --client opencode --space privado \
  --project proyecto-arbitrario --rights read,write --peer-uid UID_EJECUTOR
```

**Importante:** clientes con el mismo UID siguen pudiendo acceder a los recursos que ese UID tenga permitidos por Linux; vincular tokens a UID no resuelve esto. La seguridad entre proyectos requiere separar usuarios/permisos y un sandbox real. Este avance tampoco valida por si solo el protocolo MCP con el SDK oficial.
