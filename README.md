# SynapSyS-MemorY

**Memoria local independiente y sin IA interna, para distintos clientes y proyectos.**

## Estado: 1.0.0-rc1, NO estable

Esta candidata corrige fallos reproducidos sobre `1350b71` (dev5). La rama
experimental ha validado el cierre coordinado del sandbox al cambiar de
proyecto y las consultas MCP de OpenCode con Ollama y Strata en proyectos de
prueba. La validación del despliegue para uso diario sigue pendiente.
[Pilotos y criterios de cierre](docs/PERSISTENCE_PILOT.md#remaining-v1-acceptance)

No incluye LLM, embeddings, Ollama, inferencia ni GPU. La memoria no decide
que es verdad: guarda y consulta datos suministrados por clientes autorizados.

## Incluido

- SQLite local con FTS5/BM25, transacciones, revisiones optimistas y TTL.
- Broker Unix con tokens por sesion/proyecto y UID real del proceso cliente.
- Adaptador MCP stdio opcional, CLI y API Python. Sin servidor HTTP publico.
- Concesiones revocables y limites de sesiones, conexiones y handlers.
- Migraciones atomicas, backups privados y consistentes, auditoria sin cuerpos.
- Lanzador Bubblewrap experimental con red desactivada y un unico workspace.
- Rechazo de directorios solapados, enlaces duros fuera del árbol auditado,
  archivos especiales y políticas ambiguas.

## Datos privados frente al codigo publicado

GitHub aloja codigo, pruebas sinteticas y documentacion; no los recuerdos.
El broker guarda por defecto en `~/.local/state/synapsys-memory/memory.sqlite3`.
La CLI directa conserva su ruta heredada en
`~/.local/share/synapsys-memory/memory.sqlite3`; no son la misma base salvo
configuracion explicita. La integracion de agentes debe utilizar el broker,
no abrir SQLite directamente.

Manten las bases, tokens y backups fuera del checkout. `.gitignore` es una
precaucion, no una frontera de seguridad. Un cliente externo puede enviar al
proveedor de su modelo el texto que recupere: el almacenamiento local por si
solo no garantiza que todo el procesamiento del cliente sea local.

## Instalacion y validacion local

Python 3.11+ y SQLite con FTS5. Broker/sandbox: Linux recomendado y probado.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[mcp]'
.venv/bin/python scripts/validate_release.py
```

El validador ejecuta una vez la suite y las pruebas MCP y Bubblewrap+broker,
con bases temporales. No instala dependencias, no ejecuta sudo, no arranca
modelos ni toca repositorios reales. Escribe evidencias en una carpeta
privada temporal fuera del repositorio. Un requisito ausente se informa como
BLOCKED, nunca como PASS. Aunque los componentes pasen, muestra los gates
manuales pendientes y no certifica una version estable.

La rama experimental incluye una unidad de usuario para systemd y pruebas
de recuperacion y lectura MCP. [Pilotos, despliegue y pendientes de v1](docs/PERSISTENCE_PILOT.md)

## Seguridad y limites de uso

Usa un controlador de confianza, credenciales privadas y separacion OS del
broker y agentes. No concedas al agente acceso directo a SQLite, al socket
admin, al secreto administrativo ni al archivo de politica.

Un proyecto por sesion. El proyecto se obtiene de la concesion del broker,
no de parametros elegidos por el agente. Los nombres de proyectos no tienen
privilegios especiales. La colaboracion padre/hijo sigue aplazada.

Las comprobaciones de rutas y enlaces son un preflight, no una defensa
contra un proceso no aislado del mismo UID que cambie el host concurrentemente.
Bubblewrap mantiene la red aislada. El controlador permite autorizar el chat
de un modelo local mediante un socket privado de la sesión.
[Acceso restringido al runtime local](docs/PERSISTENCE_PILOT.md#restricted-local-runtime-access)

`backup` es una operacion de administrador que incluye TODA la base, no una
exportacion por proyecto. No se expone a herramientas MCP ni clientes del
broker. El borrado activo no elimina backups externos ni garantiza borrado
forense. Las migraciones no proporcionan downgrade automatico.

## Resultado de la auditoria local

139/139 pruebas unitarias y de sockets PASS; prueba con dos UIDs Linux reales
PASS. Las pruebas MCP y Bubblewrap de dev5 pasaron en el equipo del propietario;
la revision rc1 debe volver a validar esas integraciones. En el entorno de
esta auditoria no estan disponibles el SDK MCP ni Bubblewrap.

La medicion sintetica local con 10.000 recuerdos y 200 consultas dio p50
11,384 ms y p95 13,055 ms, sin contar MCP ni un cliente de IA. No es una promesa
para otro equipo o corpus. Ver [auditoria y gates](docs/V1_AUDIT.md).

Los [fragmentos de conectores](examples/README.md) no equivalen a integraciones
validadas ni conceden permisos por si mismos.

Los documentos `V02_*` son historicos; este README y `V1_AUDIT.md` describen
el alcance actual. El repositorio publico no cambia la licencia propietaria
ya declarada en `pyproject.toml`; no se ha elegido una licencia nueva.
