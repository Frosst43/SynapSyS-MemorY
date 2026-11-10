# Fragmentos de conectores: no son instalaciones validadas

Los ejemplos antiguos de dev1 usaban SYMEM_DB/SPACE/PROJECT/RIGHTS. El modo
normal actual exige un broker y una concesion del administrador; esos campos
no autorizan un proyecto ni habilitan acceso directo a SQLite.

Los fragmentos ahora muestran SYMEM_BROKER_SOCKET y SYMEM_SESSION_TOKEN_FILE.
Solo contienen rutas de ejemplo; no poner tokens, claves admin o datos reales
bajo Git. Las variantes de esquema de cada cliente deben contrastarse con
su version instalada. No se certifica aqui OpenCode, Codex ni OpenClaw reales.

Dentro del sandbox las rutas del puente son /run/synapsys-memory/data.sock y
/run/synapsys-memory/session.token. El runtime MCP tambien debe estar montado
explicitamente en lectura. La ruta de un venv del HOME del host no aparece
magicamente dentro del sandbox. No montar todo HOME para hacerla funcionar.

Estos fragmentos no conceden permisos de archivos, no levantan el broker,
no entregan credenciales y no activan un supervisor de cambio de proyecto.
Ver ../docs/V1_AUDIT.md antes de conectar datos o proyectos reales.
