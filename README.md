# SynapSyS-MemorY

**Memoria local, persistente, modular e independiente para aplicaciones con IA.**

Estado: **v0.1.0 / base funcional**. Repositorio privado. Motor sin dependencia de modelos, nubes ni GPU. MCP stdio es opcional. No se afirma que ya este instalado o integrado en OpenCode/OpenClaw.

## Principios

- **Una base, multiples aplicaciones**: varias IAs pueden utilizar intencionalmente el mismo espacio.
- **Aislamiento por defecto**: espacio (`space`) y proyecto (`project`) fijos por conector. No hay busqueda entre espacios.
- **Contexto util, no historiales infinitos**: recuperacion indexada con limites de texto.
- **Trazabilidad y control**: procedencia, fecha, etiquetas, prioridad, correccion y borrado.
- **Local primero**: SQLite con FTS5/BM25 y WAL; cero llamadas externas.
- **Seguridad**: los recuerdos recuperados son datos, no instrucciones confiables.

## Instalacion en un entorno virtual

Requiere Python **3.11+** con soporte SQLite FTS5.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

Instalar tambien el adaptador MCP (opcional):

```bash
python -m pip install -e '.[mcp]'
```

La dependencia MCP esta fijada a la serie oficial 1.x por compatibilidad con clientes que utilizan el handshake MCP clasico. La adopcion de v2 se tratara como una migracion independiente, con pruebas de interoperabilidad.

## CLI

```bash
synapsys-memory --space personal --project demo remember "La configuracion del proyecto usa Python" --kind decision --title "Lenguaje"
synapsys-memory --space personal --project demo recall "configuracion Python"
synapsys-memory --space personal --project demo recent
synapsys-memory --space personal --project demo stats
synapsys-memory --space personal --project demo backup ~/memoria-respaldo.sqlite3
```

Por defecto almacena en `~/.local/share/synapsys-memory/memory.sqlite3`. El contenido no se publica en Git: nunca versionar la base de datos ni almacenar credenciales en los recuerdos.

## MCP stdio para agentes

Servidor con herramientas `memory_remember`, `memory_recall`, `memory_recent`, `memory_get`, `memory_update`, `memory_forget`, `memory_stats`.

```bash
SYMEM_SPACE=personal SYMEM_PROJECT=demo synapsys-memory-mcp
```

El comando MCP escribe el protocolo en stdout: no insertar mensajes de depuracion por stdout.

### OpenCode

Ver [examples/opencode-v1.json](examples/opencode-v1.json) y [examples/opencode-v2.json](examples/opencode-v2.json). **Usar el ejemplo correspondiente a la version instalada.** El comando debe apuntar a la ruta absoluta de `.venv/bin/synapsys-memory-mcp` en el equipo del usuario; las rutas de los ejemplos son marcadores por sustituir, no configuraciones ejecutadas.

### OpenClaw

Ver [examples/openclaw.md](examples/openclaw.md). La capacidad de consumir un servidor MCP depende de la version y la configuracion real de OpenClaw. No modificar su instalacion sin probar antes la integracion.

## Pruebas y rendimiento

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
PYTHONPATH=src python3 bench/benchmark.py --entries 2000 --queries 100
```

Las mediciones reflejan un equipo concreto y no demuestran metas de produccion. Antes de anunciar rendimiento, medir latencias p50/p95/p99, memoria RAM, espacio, concurrencia y crecimiento del indice con conjuntos de datos reales y comparables.

## Limites conocidos

- Busca por palabras (sin embeddings semanticos ni sinónimos). La mejora semantica sera un modulo optativo, nunca requisito de GPU.
- Los agentes deben invocar herramientas MCP; la captura automatica desde sus conversaciones **no esta implementada**.
- Los espacios se aislan por filtros dentro del mismo archivo SQLite; el modelo de confianza asume que los procesos tienen acceso al archivo local. Para aislamiento fuerte entre usuarios, se requeriran bases y permisos OS separados o un servicio autenticado.
- `memory_forget` borra el registro de la base activa; los respaldos independientes pueden conservarlo. No se promete borrado forense.
- No hay API HTTP, control de acceso remoto, sincronizacion distribuida ni conectores de producto ya instalados.

## Proximo trabajo

1. Pruebas de protocolo MCP reales con clientes compatibles, latencia de extremo a extremo, manejo de fallos y versiones.
2. Captura selectiva de recuerdos, deduplicacion semantica, resolucion de contradicciones y consolidacion corto/medio/largo plazo con aprobacion.
3. Recuperacion hibrida opcional (FTS + embeddings), ranking evaluado con benchmarks y trazas.
4. Adaptadores por producto, permisos explicitos y aislamiento fuerte donde se requiera.

Arquitectura y criterios en [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
