# SynapSyS-MemorY v0.2.0.dev1 — Seguridad y pruebas

**Estado:** código experimental, no preparado para datos confidenciales.
No se incluye ni se ejecuta IA.

## Cambios implementados

1. `ConnectorPolicy` valida cliente y derechos `read`, `write`, `delete`.
   Por defecto `read,write`; el borrado no se expone si no se concede.
2. Las herramientas MCP se registran únicamente si el proceso dispone
   del derecho correspondiente. La memoria y el proyecto se fijan al
   arrancar el proceso, no como parámetros controlados por el agente.
3. MCP asigna fuente `mcp:<client>`. No se acepta que el agente MCP
   suplante libremente este campo mediante `memory_remember`.
4. La migración conserva las memorias v0.1 y añade un entero `revision`
   que comienza en 1; posteriores cambios incrementan la versión.
5. MCP exige `expected_revision` en `memory_update` y `memory_forget`.
   Si otro cliente modificó antes el recuerdo, falla sin sobrescribirlo.
6. `memory_events` almacena actor, acción, versión, espacio/proyecto y
   tiempo. **No** guarda cuerpos ni permite recuperar el texto borrado.
7. CLI permite `audit`, `check` y `--actor`; el motor permite comprobar
   integridad básica de SQLite con `PRAGMA quick_check`.

## Límites de seguridad (críticos)

- En v0.2 los derechos son **capacidades configuradas para el adaptador**,
  no autenticación del usuario. Un cliente que controla su lanzador MCP
  podría modificar el entorno, y cualquier proceso con acceso de lectura
  a SQLite puede saltar el adaptador. No presentarlo como aislamiento
  hostil. Para protección real: permisos OS separados y un broker local
  propietario de la base con identificación y ACL por cliente.
- Un proceso con `delete` puede borrar contenido; el historial conserva
  eventos sin el cuerpo. Los respaldos pueden mantener el texto.
- Los recuerdos de agentes son datos no confiables, nunca órdenes.
- Evitar guardar contraseñas, claves o tokens y no abrir puertos públicos.
- El campo `actor` indica el cliente configurado, pero no es identidad
  criptográficamente autenticada.
- La prueba `quick_check` no certifica el índice FTS ni la integridad
  de todos los respaldos.

## Comprobaciones reproducibles

    PYTHONPATH=src python3 -m unittest discover -s tests -v
    PYTHONPATH=src python3 bench/benchmark.py --entries 10000 --queries 200
    python3 scripts/verify_mcp_stdio.py

La tercera requiere `pip install -e '.[mcp]'` y no ha podido
realizarse en el entorno de creación, donde no se puede obtener
la dependencia. No inventar un PASS MCP.

La prueba MCP usa temporal y tres procesos stdio: OpenCode (nombre
simulado de conector, escritura), Codex (solo lectura), OpenClaw
(otro proyecto). **No** son los clientes instalados reales. Después
se realizará un ensayo real de configuración con cada uno.

## Compatibilidad de datos

- Actualización desde v0.1 (user_version=0) -> v0.2 (user_version=2).
- El código v0.1 no debe utilizarse para escribir después de la
  migración: desconoce revisiones y eventos, y no se garantiza que
  mantenga el estado de auditoría esperado.
- Antes de actualizar una base real, realizar y verificar un respaldo.
- No hay migración de descenso automática.
