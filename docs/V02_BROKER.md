# Controlador local de sesiones v0.2.0.dev2 (experimental)

No incluye IA. Es un broker local con sockets Unix y dos planos separados:
- **Datos**: clientes con token de sesión, limitado a un proyecto y operaciones.
- **Administración**: emitir, cambiar y revocar sesiones mediante clave admin.

## Aislamiento verificable en esta etapa

- El cliente no elige proyecto ni permisos; el broker los vincula al token.
- `grant` exige sesión nueva; `switch` emite un token nuevo y anula el anterior
  bajo la misma exclusión mutua de las operaciones de datos.
- No se conceden dos proyectos a una misma sesión.
- El broker es dueño del acceso a SQLite; los agentes usan `BrokerClient`.
- Caducidad y revocación: cualquier petición posterior a revocación se deniega.
- Se conservan memorias de otros proyectos sin dar acceso a ellas.
- Los permisos de lectura/escritura/borrado se verifican en **cada llamada**.
- No hay acceso a red TCP; solo Unix Domain Sockets locales.
- El adaptador MCP no abre SQLite directo por defecto.

## Limitación crítica

**Esto aún NO es un sandbox del agente ni autenticación fuerte entre procesos
bajo el mismo usuario de Linux.** Un agente con terminal sin restricciones,
acceso a la base de datos o a la clave administrativa podría eludir las ACL.
La protección real requiere ejecutar el broker bajo una identidad OS separada,
proteger el socket de administración y el fichero SQLite, entregar cada token
solo a su proceso autorizado y restringir sus permisos de archivos y procesos.

No instalar un agente sin sandbox con acceso global al home de usuario y
considerar que este broker protege los proyectos. Sin una configuración de OS
verificada, esto es aislamiento lógico entre clientes cooperativos.

## Prueba demostrativa sin acceso a proyectos reales

```bash
PYTHONPATH=src python3 scripts/demo_broker_switch.py
PYTHONPATH=src python3 -m unittest discover -s tests -v
# Con el extra MCP instalado: python3 scripts/verify_mcp_stdio.py
```

## Uso experimental (sesiones de terminal de confianza)

En terminal ADMIN, generar una clave fuerte y mantenerla secreta:

```bash
export SYMEM_ADMIN_KEY="$(python3 -c 'import secrets;print(secrets.token_urlsafe(48))')"
synapsys-memory-broker serve
```

En **otra** terminal ADMIN, con la misma clave configurada (no enviarla por
chat ni copiarla a un agente):

```bash
synapsys-memory-broker grant --session qwen-demo --client qwen --space privado \
  --project sypminer-terminal-pro --rights read,write
synapsys-memory-broker switch --session qwen-demo --client qwen --space privado \
  --project synapsys --rights read,write
synapsys-memory-broker revoke --session qwen-demo
```

El comando grant/switch muestra el nuevo `token` **solo al administrador**.
Se debe entregar al proceso MCP autorizado mediante un mecanismo de secretos
apropiado y configurar `SYMEM_BROKER_SOCKET` + `SYMEM_SESSION_TOKEN`. No
exponer ese token, ni la clave admin, en el prompt de la IA ni en repos Git.

El proyecto se configura por sesión, nunca lo decide la herramienta MCP.

**Pendiente**: integración con clientes reales, aislamiento OS, credenciales
protegidas por un lanzador confiable, gestión de permisos de archivos, prueba
MCP end-to-end. No se fusionará este avance en `main` hasta cumplir esos gates.
