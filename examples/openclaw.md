# OpenClaw: integracion pendiente de prueba en una instalacion real

OpenClaw dispone de un gestor MCP para definiciones salientes y soporte de
herramientas MCP mediante sus mecanismos de integracion. La configuracion
concreta depende de la version y no debe inventarse ni instalarse a ciegas.

Preparacion comun:

```bash
cd /RUTA/AL/REPO
.venv/bin/python -m pip install -e '.[mcp]'
SYMEM_SPACE=shared SYMEM_PROJECT=demo .venv/bin/synapsys-memory-mcp
```

Ese ultimo comando queda esperando el handshake por stdio: no es una terminal
interactiva. En una version de OpenClaw que admita servidores MCP salientes,
registre el servidor con la ruta absoluta (sin ejecutar aun capturas automaticas):

```bash
openclaw mcp add synapsys-memory \
  --command /RUTA/AL/REPO/.venv/bin/synapsys-memory-mcp \
  --env SYMEM_SPACE=shared \
  --env SYMEM_PROJECT=demo
openclaw mcp doctor synapsys-memory --probe
```

El ultimo comando comprueba una conexion real. Antes de habilitar herramientas
para todos los agentes, revise los permisos de escritura y borrado.

Ver la documentacion oficial actual: https://docs.openclaw.ai/mcp

**Estado**: servidor MCP implementado en el repo, compatibilidad real con
OpenClaw aun no probada. No se ha tocado ninguna configuracion del usuario.
