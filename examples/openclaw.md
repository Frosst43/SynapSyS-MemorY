# OpenClaw — Conectar SynapSyS-MemorY

No sustituir automáticamente la memoria nativa de OpenClaw; el objetivo
es compartir proyectos entre varios clientes, con permisos explícitos.

La documentación actual de OpenClaw admite servidores externos en
`mcp.servers` con transporte `stdio` y configuración mediante UI/CLI.
Para una instalación real, revisar la versión instalada antes de registrar.

## Preparación

```bash
cd /RUTA/AL/REPO
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[mcp]'
```

En la configuración MCP de OpenClaw, añadir servidor de tipo `stdio`:

- Comando: `/RUTA/AL/REPO/.venv/bin/synapsys-memory-mcp`
- Entorno: `SYMEM_DB=/RUTA/PRIVADA/memory.sqlite3`
- Entorno: `SYMEM_SPACE=shared`
- Entorno: `SYMEM_PROJECT=demo`
- Entorno: `SYMEM_CLIENT=openclaw`
- Entorno: `SYMEM_RIGHTS=read`

La base de datos y proyecto deben ser los mismos que los de OpenCode y Codex
para compartir datos; los derechos pueden ser distintos. Configurar
separadamente cada cliente. No utilizar rutas relativas en producción.

Una vez guardado, revisar con `openclaw mcp doctor <nombre> --probe`.
No se ha instalado ni validado OpenClaw real en este entorno.

Referencia oficial: https://docs.openclaw.ai/tools/mcp
