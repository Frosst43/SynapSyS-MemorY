# Hoja de ruta de SynapSyS-MemorY: memoria SIN IA

Fecha: 2026-11-07
Decision del propietario: **no implementar inteligencia artificial dentro de SynapSyS-MemorY**.
Estado: plan de desarrollo; las funciones futuras no estan implementadas.

## Principio obligatorio

SynapSyS-MemorY es un **servicio de memoria**, no un agente ni un modelo.
Las IAs de terceros se conectan a este servicio; nunca forman parte del motor.

Excluido del motor y de sus modulos:
- LLM, modelos locales o remotos, Ollama, clasificadores entrenados y agentes.
- Embeddings, modelos vectoriales, rerankers neuronales e inferencia.
- Extraccion automatica de hechos mediante IA, resúmenes generativos
  o reconciliacion semantica automatica.
- GPU obligatoria, nube obligatoria o entrenamientos.

La aplicacion conectada puede decidir por su cuenta que recuerdos
escribir, actualizar, leer o borrar; SynapSyS-MemorY **no toma esas
decisiones usando IA**. Registra operaciones y metadatos explicitamente
entregados por el cliente, sujetos a permisos.

## Arquitectura

OpenCode / OpenClaw / SynapSyS / otras aplicaciones
  -> conectores MCP, SDK o API local futura
  -> validacion + autenticacion/autorizacion por conector
  -> servicio de memoria (determinista)
  -> SQLite WAL + FTS5/BM25 + indices + historial versionado.

Los conectores no pueden cambiar unilateralmente el espacio al que
tienen acceso. El acceso a SQLite directamente evade los permisos
logicos, por lo que los clientes no confiables requeriran un broker
autenticado o archivos/bases separados con controles del sistema operativo.

## Funciones del motor

1. Guardar, recuperar, corregir, borrar y exportar recuerdos.
2. Separar informacion por propietario, aplicacion, proyecto y espacio.
3. Adjuntar fuente, fecha, tags, categoria, prioridad, vigencia y TTL.
4. Dedupe exacta determinista con hashes e idempotencia de eventos.
5. Busqueda por texto completo FTS5/BM25 y filtros de metadatos.
6. Soporte optativo de sinonimos escritos por el usuario, normalizacion
   linguistica, prefijos y coincidencia aproximada **algoritmica**
   cuando mejore las medidas; sin embeddings ni modelos.
7. Historial de revisiones: version actual, anterior, sustitucion o
   controversia **declaradas**; no adivinar contradicciones en lenguaje.
8. Retencion configurable, respaldo consistente, importacion y auditoria.
9. Limitacion de resultados, presupuesto de caracteres, paginacion y
   salidas estructuradas para evitar inflar el contexto de la IA cliente.
10. Aislamiento fuerte, permisos por herramienta, pruebas de seguridad.

## Memoria de corto, medio y largo plazo

- Corta: contenido con caducidad; el cliente fija el TTL.
- Media: hitos/estado de sesion almacenados con categoria apropiada.
- Larga: decisiones y hechos duraderos aprobados por el cliente.
- Sin promocion automatica por inferencia, resúmenes de IA o evaluacion
  subjetiva del contenido. Las politicas de vencimiento son reglas claras.

Ejemplo temporal:
- Un cliente guarda un parametro aprobado: 4 minutos.
- Otro cliente propone 5 minutos: registra estado "propuesta".
- Se confirma 4 minutos: se registra "vigente", conservando el historial.
El motor devuelve el estado registrado, no decide cual es correcto.

## Fases

### v0.2: robustez y conexiones reales
- Migraciones versionadas, respaldos/restauracion y validacion de integridad.
- Handshake MCP end-to-end, y pruebas con OpenCode y OpenClaw instalados.
- Endurecer permisos de lectura/escritura/borrado; limites de tamaño.
- Test unitarios de concurrencia, FTS, aislamiento y fallos.
Criterio: las herramientas conectan y devuelven datos reales sin violar permisos.

### v0.3: recuperacion determinista mejorada
- BM25 ajustado con datos de calidad medidos.
- Filtros por fecha, fuente, etiquetas, categoria, proyecto y vigencia.
- Busqueda exacta por identificadores, rutas, hashes y frases.
- Sinonimos configurados, prefijos y coincidencia aproximada si los
  benchmarks muestran que ayudan; todo sin IA.
- Consulta estructurada con paginacion y presupuesto de resultados.
Criterio: mejor precision y recall sobre FTS5 base sin latencias excesivas.

### v0.4: historial, relaciones y continuidad
- Esquema de revisiones, vigencia y estado de decision explícitos.
- Relaciones entre memorias definidas por el cliente.
- Politicas de corto/medio/largo plazo basadas en reglas y TTL.
- Importacion/exportacion controlada con trazabilidad.
Criterio: restaurar decisiones actuales e historicas sin mezclar estados.

### v0.5: conectores y administracion
- Plugin opcional OpenCode y adaptador OpenClaw.
- Clientes realizan llamadas de almacenamiento/lectura segun su
  configuracion y permisos. El servidor nunca usa IA para capturar.
- SDK Python, API local autenticada solo si hace falta.
- Politicas para compartir selectivamente memorias entre dos clientes.
Criterio: continuidad real entre dos aplicaciones y cero acceso no autorizado.

## Calidad y rendimiento

Pruebas con 1k, 10k y 100k recuerdos, incluyendo coincidencia literal,
errores tipograficos, sinonimos declarados, espacios cruzados, datos
caducados y versiones contradictorias explicitamente registradas.

Medir recall, precision, p50/p95/p99, RAM, CPU, tamaño de base,
duracion de indices y errores de autorizacion.

Objetivo no garantizado: p95 inferior a 50 ms en consultas FTS calientes
con 10k recuerdos en la maquina de destino; benchmark reproducible
antes de declarar resultados.

## Referencias utiles (solo interoperabilidad e indexacion)

- https://opencode.ai/v2/docs/mcp-servers
- https://docs.opencode.ai/docs/plugins/
- https://docs.openclaw.ai/tools/mcp
- https://docs.openclaw.ai/plugins/hooks
- https://docs.openclaw.ai/concepts/memory
- https://www.sqlite.org/fts5.html
- https://www.sqlite.org/wal.html

**Regla de producto:** las decisiones de introducir IA, embeddings o
modelos en SynapSyS-MemorY no pertenecen a la hoja de ruta vigente.
