# SynapSyS-MemorY

**Motor de memoria independiente, rapido, local y 100% sin IA interna.**

Estado: **v0.1.0 / base funcional**. Repositorio privado. Motor sin
modelos, embeddings, nubes ni GPU. Adaptador MCP stdio opcional.
La integracion end-to-end con OpenCode/OpenClaw sigue pendiente de validar.

**Decision de arquitectura: SynapSyS-MemorY NO implementa IA.**
Es una infraestructura de almacenamiento y consulta para aplicaciones
que SI pueden contener IA. Las aplicaciones eligen que conservar
mediante las herramientas de memoria. El motor aplica reglas
deterministas y no interpreta por su cuenta conversaciones.

## Principios

- **Una memoria, multiples aplicaciones**: se puede compartir un espacio
  entre distintas IAs solo si esta autorizado expresamente.
- **Aislamiento por defecto**: filtros de espacio y proyecto en v0.1;
  permisos fuertes por conector pendientes de desarrollo.
- **Rapidez sin modelos**: SQLite con FTS5/BM25, WAL e indices locales.
- **Control y trazabilidad**: fuente, fecha, tags, prioridad, correccion,
  borrado y respaldo. Historial temporal versionado: hoja de ruta.
- **Contexto limitado**: recuperar solo registros relevantes mediante
  limites de resultados y texto, sin cargar conversaciones completas.
- **Sin inferencia**: ni LLM, ni embeddings, ni Ollama, ni GPU.
- **Recuerdos como datos**: nunca ejecutar instrucciones que aparezcan
  en un recuerdo; no introducir credenciales.

## Instalacion

Python 3.11+ con SQLite compilado con FTS5.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

El adaptador MCP es opcional:

```bash
python -m pip install -e '.[mcp]'
```

La dependencia del protocolo MCP NO es una IA.

## Uso por terminal

```bash
synapsys-memory --space personal --project demo remember "El proyecto usa Python" --kind decision --title "Lenguaje"
synapsys-memory --space personal --project demo recall "proyecto Python"
synapsys-memory --space personal --project demo recent
synapsys-memory --space personal --project demo stats
synapsys-memory --space personal --project demo backup ~/memoria-respaldo.sqlite3
```

Ruta por defecto: `~/.local/share/synapsys-memory/memory.sqlite3`.
No publicar bases, backups, contraseñas ni tokens en Git.

## MCP stdio

Herramientas: `memory_remember`, `memory_recall`, `memory_recent`,
`memory_get`, `memory_update`, `memory_forget`, `memory_stats`.

```bash
SYMEM_SPACE=personal SYMEM_PROJECT=demo synapsys-memory-mcp
```

La IA cliente decide cuándo invocar las herramientas; el servidor
no ejecuta IA y nunca debe escribir logs de depuracion en stdout.

- [OpenCode: ejemplos de configuracion](examples/opencode-v1.json).
- [OpenCode: ejemplo alternativo](examples/opencode-v2.json).
- [OpenClaw: guia inicial](examples/openclaw.md).

Los ejemplos contienen rutas por sustituir y no certifican una
conexion real con las aplicaciones instaladas.

## Pruebas y benchmark

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
PYTHONPATH=src python3 bench/benchmark.py --entries 2000 --queries 100
```

Los objetivos de rendimiento deben comprobarse en el equipo de destino.

## Limitaciones actuales

- FTS5 busca terminos; no comprende sinonimos ni parafrasis por inferencia.
  Las mejoras futuras seran algoritmos tradicionales deterministas.
- No hay extraccion automatica de conversaciones; los clientes deben
  guardar los recuerdos mediante API/MCP.
- Los espacios se separan por filtros SQL, no con autenticacion
  fuerte entre procesos que puedan abrir el archivo directamente.
- `memory_forget` borra la base activa, no backups independientes.
- No hay API HTTP, sincronizacion en red ni conectores ya instalados.

## Proximas fases (sin IA)

1. Protocolo MCP e integraciones reales; permisos y aislamiento.
2. Busqueda FTS5 avanzada, filtros, coincidencia exacta y opcional
   aproximada mediante algoritmos clasicos.
3. Historico de decisiones, vigencia, relaciones explicitas y TTL.
4. Conectores reutilizables y administracion de accesos.

Detalles: [arquitectura](docs/ARCHITECTURE.md) y
[hoja de ruta SIN IA](docs/ROADMAP_MEMORY_INTELLIGENCE.md).
