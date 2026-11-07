# SynapSyS-MemorY

**Motor persistente e independiente de memoria para conectar OpenCode, Codex, OpenClaw y proyectos propios.**

No incluye inteligencia artificial, embeddings, modelos, Ollama, nube ni GPU.
El motor es determinista: guarda y busca datos que sus aplicaciones cliente entregan.

## Estado

- **v0.2.0.dev1 — experimental**: pruebas unitarias locales y de adaptador. No es una versión lista para conectar a datos privados reales.
- **SQLite + FTS5/BM25**: búsqueda local y almacenamiento persistente.
- **MCP stdio**: adaptador opcional; el protocolo y los tres clientes reales siguen pendientes de verificación en la máquina de destino.
- **Política por proceso MCP**: herramientas de lectura, escritura y borrado separadas.
- **Versiones y concurrencia**: correcciones y borrados con `expected_revision` para evitar sobrescrituras silenciosas.
- **Auditoría ligera**: acción, versión, fecha y actor, sin recuperar texto borrado.
- **Migración de v0.1**: conservadora, mediante `PRAGMA user_version=2`.

### Aviso importante de permisos

Estos permisos restringen las herramientas expuestas por cada proceso MCP; **no son autenticación fuerte**. Cualquier proceso con permisos del sistema operativo para leer el archivo SQLite podría ignorar esos filtros. Para conectar usuarios no confiables o equipos distintos se necesitará un servicio intermediario autenticado y una configuración de permisos de archivos apropiada. **No exponer directamente la base ni MCP stdio en Internet**.

## Instalación local

Python 3.11+ con soporte SQLite FTS5:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

Para el protocolo MCP (dependencia de protocolo, no una IA):

```bash
python -m pip install -e '.[mcp]'
```

## Ejemplo básico por CLI

```bash
synapsys-memory --space compartido --project demo remember 'Bloques de cuatro minutos' --kind decision --title 'Consenso'
synapsys-memory --space compartido --project demo recall 'consenso bloques'
synapsys-memory --space compartido --project demo stats
synapsys-memory --space compartido --project demo check
```

Ruta por defecto: `~/.local/share/synapsys-memory/memory.sqlite3`. Se puede modificar con `SYMEM_DB` o `--db`. La base y sus respaldos quedan fuera de Git.

Para modificar desde la CLI se puede utilizar `--expected-revision N` tras `update` o `forget`. Los clientes MCP deben enviar obligatoriamente una revisión esperada para esas operaciones.

## Conectar dos aplicaciones al mismo proyecto

Configuración MCP en cada cliente:

```text
SYMEM_DB=/RUTA/PRIVADA/memory.sqlite3
SYMEM_SPACE=compartido
SYMEM_PROJECT=demo
SYMEM_CLIENT=opencode
SYMEM_RIGHTS=read,write
```

Codex puede utilizar el mismo DB/SPACE/PROJECT con `SYMEM_CLIENT=codex` y `SYMEM_RIGHTS=read` para acceder solo en lectura. El proceso MCP permite de forma predeterminada `read,write`, **sin delete**. Los derechos válidos son `read`, `write` y `delete` (este último exige `write`).

Referencias: [OpenCode](examples/opencode-v1.json), [Codex](examples/codex.toml), [OpenClaw](examples/openclaw.md). Las rutas deben adaptarse a la instalación. La incorporación a los clientes no está ejecutada.

## Pruebas

Sin bibliotecas externas:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
PYTHONPATH=src python3 bench/benchmark.py --entries 10000 --queries 200
```

Prueba **real de protocolo MCP stdio** con tres procesos en una base temporal (requiere el extra MCP):

```bash
python3 scripts/verify_mcp_stdio.py
```

La prueba verifica que un cliente escribe, otro de solo lectura recupera el dato y un tercero de otro proyecto no lo ve. **No prueba todavía OpenCode, Codex ni OpenClaw reales**.

## Lo que sigue pendiente

- Validar handshake y herramientas MCP en un entorno con SDK instalado.
- Integración verificada con versiones reales de OpenCode, Codex y OpenClaw.
- Broker autenticado y permisos sólidos para procesos que no compartan confianza.
- Versionado de contenido completo, restauración y exportaciones; solo tenemos auditoría de acciones.
- Pruebas de concurrencia más exigentes y evaluación de rendimiento en hardware de destino.

[Arquitectura multicliente](docs/MULTI_CLIENT_ARCHITECTURE.md) · [Hoja de ruta sin IA](docs/ROADMAP_MEMORY_INTELLIGENCE.md) · [Alcance de v0.2](docs/V02_SECURITY_AND_TESTS.md)
