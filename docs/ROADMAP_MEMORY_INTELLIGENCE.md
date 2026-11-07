# Investigacion y hoja de ruta de memoria inteligente

Fecha: 2026-11-07
Estado: plan tecnico; NO es funcionalidad ya implementada.
Base: v0.1.0, SQLite/FTS5 y MCP stdio opcional.

## Objetivo

Construir un motor de memoria independiente de cualquier LLM o aplicacion, ligero,
local primero, persistente, con procedencia, continuidad interaplicaciones,
permisos explicitos y respuesta limitada a evidencias pertinentes.

La ventaja competitiva no debe ser duplicar la memoria propia de OpenClaw
(ya tiene busqueda hibrida y captura), sino ofrecer una memoria coherente
entre aplicaciones sin mezclar informacion privada ni perder decisiones.

## Hallazgos

- OpenCode: servidores MCP locales/remotos. Su V2 usa mcp.servers;
  versiones anteriores definen servidores directamente bajo mcp.
  Los plugins tienen eventos de mensajes y sesiones.
- OpenClaw: soporta MCP externo, plugins/hook de sesion y una memoria
  propia SQLite/FTS5/vectorial. No sustituirla sin una prueba comparativa.
- Mem0: extraccion selectiva y consolidacion de hechos en lugar de
  reenviar la totalidad del historial al agente.
- Graphiti: aristas y entidades con vigencia temporal y evidencias.
- LongMemEval: evaluar extraccion, razonamiento entre sesiones,
  temporalidad, actualizaciones y abstencion.
- sqlite-vec: motor KNN local opcional, todavia pre-v1.
- Ollama: endpoint /api/embed, compatible con entradas por lotes.
- Modelos candidatos: qwen3-embedding:0.6b (~639 MB paquete Ollama)
  y EmbeddingGemma (~308 millones de parametros).
- RRF: fusion de rankings BM25/vector sin equiparar escalas.

Referencias:
- https://opencode.ai/v2/docs/mcp-servers
- https://docs.opencode.ai/docs/plugins/
- https://docs.openclaw.ai/tools/mcp
- https://docs.openclaw.ai/plugins/hooks
- https://docs.openclaw.ai/concepts/memory
- https://arxiv.org/abs/2504.19413
- https://www.getzep.com/platform/graphiti/
- https://proceedings.iclr.cc/paper_files/paper/2025/hash/d813d324dbf0598bbdc9c8e79740ed01-Abstract-Conference.html
- https://github.com/asg017/sqlite-vec
- https://github.com/ollama/ollama/blob/main/docs/capabilities/embeddings.mdx
- https://ollama.com/library/qwen3-embedding
- https://developers.googleblog.com/introducing-embeddinggemma/
- https://docs.opensearch.org/latest/vector-search/ai-search/hybrid-search/rrf/

## Arquitectura propuesta

Conector -> captura consentida -> extraccion selectiva -> validacion ->
almacen versionado -> indice lexical y vectorial -> ranking hibrido ->
filtro de permisos -> devolucion limitada con evidencias.

Capturar: eventos idempotentes por aplicacion/sesion/evento, con fuente y
marca temporal. Almacenar hechos, decisiones, preferencias y episodios de
forma deliberada, nunca volcar automaticamente un historial entero.
Rechazar claves, tokens y datos sin permiso; tratar cualquier texto como
contenido NO confiable, no como orden del sistema.

Guardar: SQLite WAL y FTS5 siguen siendo obligatorios. Tabla de
revisiones append-only para la trazabilidad; estados activo, propuesta,
sustituido, disputado, retirado. Relaciones temporales simples, sin base
de grafos externa como requisito. Version del modelo embeddings y hash
de contenido para reconstruir indices. Cola de indexacion acotada para
mantener el camino interactivo rapido.

Buscar: filtro de espacio/proyecto y permisos ANTES de devolver resultados;
BM25 para identificadores/errores y similitud vectorial para parafrasis;
fusion por RRF; diversidad, actualidad, vigencia y evidencia; presupuesto
de caracteres/tokens. Si el modelo de embeddings no esta disponible, FTS5
continua funcionando.

Corto plazo: contexto temporal con TTL.
Medio plazo: resumenes de sesiones, tareas y hitos activos.
Largo plazo: decisiones aprobadas, hechos duraderos y preferencias.
Promocion solo por relevancia/confianza, no por cantidad de lecturas.

Ejemplo de contradiccion:
- Fecha A: objetivo de bloque de 4 minutos (aprobado).
- Fecha B: propuesta de 5 minutos (no aprobada).
- Fecha C: ratificacion de 4 minutos.
El agente debe recibir 4 minutos como estado vigente, con referencia a C.
La propuesta B y el hecho historico A conservan su situacion temporal.

## Fases

### v0.2: blindar el nucleo, integracion MCP real
- Migraciones SQLite versionadas y copia de seguridad verificada.
- Pruebas focalizadas de FTS, concurrencia, error, borrado y escapes.
- Handshake de MCP stdio real con el SDK, y pruebas con versiones
  instaladas de OpenCode y OpenClaw.
- Separacion de permisos de escritura/borrado y lectura por conexion.
Gate: ninguna regresion; MCP realmente conectado, no solo tests unitarios.

### v0.3: busqueda semantica hibrida
- Interfaz de embeddings con proveedor local opcional.
- Comparar Qwen3 0.6B vs EmbeddingGemma sobre corpus espanol/ingles.
- Cache por version de modelo, indexacion por lotes, fallback FTS.
- sqlite-vec como plugin no obligatorio, fusion BM25+vector con RRF.
- Evaluar errores exactos (ids/rutas/hashes) y parafrasis.
Gate: mejora medible en Recall@5/nDCG@10 sin perder exact-match.

### v0.4: memoria temporal e inteligente
- Extractor de hechos con reglas y modelo local opcional.
- Propuestas ADD/UPDATE/SUPERSEDE/IGNORE y reconciliacion por fuente.
- Dedupe semantico como sugerencia, nunca fusion silenciosa.
- Revision historica, relaciones simples y resumentes por sesion.
- Confirmacion de decisiones delicadas o contradictorias.
Gate: pruebas de cambios, contradicciones, vigencia y abstencion.

### v0.5: conectores automáticos
- OpenCode: plugin optativo en evento de sesion idle/cierre.
- OpenClaw: plugin/hook con permiso de lectura de conversacion.
- Interfaz MCP y SDK Python para otros programas.
- API local autenticada solo si una aplicacion no admite MCP.
- Prueba de continuidad con dos clientes y aislamiento por defecto.
Gate: lectura/escritura controlada entre dos clientes reales, sin fugas.

## Rendimiento y calidad

Cargas: 1k, 10k y 100k memorias sinteticas; dataset de preguntas
con evidencias, sinonimos, proyectos cruzados, cambios y desconocidos.
Medir Recall@5, nDCG@10, exactitud final, abstencion correcta, fugas
de espacio, p50/p95/p99 en frio/caliente, RAM, CPU, reindexacion y
consumo de tokens; desglosar latencia embedding/FTS/MCP.

Metas a validar en la maquina de destino, NO promesas:
- Sin degradacion frente a v0.1 en ruta FTS5.
- FTS caliente: p95 objetivo inferior a 50 ms a 10k memorias.
- Hibrido caliente: p95 objetivo inferior a 500 ms a 10k memorias;
  si no alcanza, conservar una via FTS rapida.
- Cero accesos cruzados sin autorizacion en pruebas de seguridad.

## Restricciones

Sin LLM permanente ni GPU obligatoria. Sin sincronizacion remota por
defecto. Sin reemplazo de la memoria de terceros antes de comprobar
compatibilidad. El conector del agente no decide por si mismo sus
propios permisos. El usuario puede revisar, corregir, exportar y
eliminar recuerdos; backups obedecen retencion independiente.
