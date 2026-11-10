# Auditoria de preparacion v1 — 2026-11-10

Base remota: `1350b71d0064b19e2f0020500b08770a3600cdfd`, rama
`experiment/v02-permissions-mcp-migration`. Candidata: `1.0.0rc1`.
Estado: **NO ESTABLE / NO FUSIONAR**. Sin IA interna ni cambios de consenso
u otros proyectos. Datos y pruebas exclusivamente sinteticos/temporales.

## Hallazgos corregidos

| Area | Hallazgo | Correccion |
|---|---|---|
| MCP + broker | `memory_remember` solo-escritura esperaba `memory` despues de que el broker redujera la respuesta. Fallaba con KeyError tras guardar. | Acepta el acuse reducido y nunca devuelve contenido al cliente sin lectura. |
| Concurrencia | `expected_revision: null` eludia el requisito del broker en update/delete. | Entero positivo obligatorio antes de invocar el almacen. |
| Migracion | ALTER TABLE podia persistir tras fallar el siguiente paso; la migracion no era atomica. | BEGIN IMMEDIATE, DDL individual y commit/rollback conjunto, sin executescript que cierre la transaccion. |
| Primer arranque | Inicializaciones paralelas podian encontrar SQLITE_BUSY al activar WAL. | Reintento acotado solo de ese PRAGMA idempotente, sin repetir escrituras de aplicacion. |
| Durabilidad | NORMAL no sincronizaba cada commit del WAL. | FULL por defecto; se acepta su coste de escritura. |
| Backup | Fichero inicialmente demasiado abierto, enlace simbolico roto seguido, conexion no cerrada explicitamente. | Reserva exclusiva 0600, no sobrescritura, rechazo de enlaces, cierre e integridad. |
| SQLite directo | Hardlink o archivo con modo amplio podia abrirse y cambiarse de permisos. | Validacion de inode, propietario, modo y auxiliares WAL/SHM/journal, sin chmod silencioso. |
| Caducidad | Un registro caducado era invisible incluso para borrado explicito por ID. | Se puede eliminar y auditar sin devolver su contenido. |
| Sandbox | Raices de proyectos solapadas o identicas mezclaban archivos; politica dentro de otro workspace era aceptada. | Registro de rutas disjuntas y politica fuera de todos los workspaces. |
| Sandbox | Hardlinks a archivos externos, FIFO/sockets y subdirectorios de sistema podian entrar en workspace. | Preflight recursivo acotado; se rechazan hardlinks, tipos especiales y rutas reservadas. |
| Configuracion | JSON con claves repetidas permitia sobrescrituras ambiguas de politica. | Parser estricto tambien para el protocolo; rechaza duplicados y valores no finitos. |
| Sesiones | Retroceder reloj podia alargar un permiso; crecimiento sin limite de sesiones/cache/hilos. | Caducidad monotona adicional, indice de tokens, limites y cache acotada. |
| Protocolo | Fallos SQLite podian cortar el socket con traceback; argumentos desconocidos se ignoraban. | Respuestas acotadas sin detalles internos y esquema de argumentos por operacion. |
| Cierre | stop() de un broker no iniciado podia borrar un socket ajeno. | Solo elimina sockets creados por esa instancia y con el mismo inode. |
| Ejemplos | Configuraciones de clientes seguian indicando SQLite directo desactivado. | Usan socket y archivo de token; esquemas de productos y runtime siguen pendientes de prueba real. |
| Version | pyproject declaraba dev5 mientras __version__ seguia en dev2. | Ambos declaran 1.0.0rc1. |

## Evidencia y alcance real

Se reconstruyeron los archivos de trabajo a partir de los adjuntos previos,
comprobando los blobs de las piezas auditadas contra el repositorio vivo.
No se obtuvo un clon de red en el entorno: DNS de github.com no disponible.
Las lecturas y la publicacion remota se realizan mediante el conector GitHub.
No se afirma una auditoria de todo el historial Git ni de secretos historicos.

Antes de corregir, 23 pruebas dirigidas dieron 21 fallos, 1 error y 1 PASS.
Son criterios de regresion y endurecimiento: no equivalen a 22 vulnerabilidades
independientes. Tras las correcciones y una correccion adicional de la carrera
WAL, la suite ampliada termino con **139/139 PASS**, sin omisiones, en 4,435 s.
Un primer lanzamiento de la suite se interrumpio por el limite del entorno;
se diagnostico el caso y se registro la ejecucion completa posterior. No se
ocultan los intentos intermedios en las evidencias de entrega.

La prueba adicional con dos UIDs reales de Linux paso: el token del UID
cliente no servia desde el UID del broker, el cliente autorizado pudo usar
memoria, y SQLite, socket admin y un archivo ajeno protegido permanecieron
inaccesibles. No demuestra sandbox completo de un agente real.

Benchmark sintetico: 10.000 recuerdos, 200 consultas, mediana 11,384 ms,
p95 13,055 ms, maximo 17,721 ms, 2.234,0 inserciones/s. Entorno de auditoria,
Python 3.13.5, sin modelos. No es una comparativa controlada con Kubuntu ni
una medicion end-to-end; tampoco mide calidad semantica.

El propietario ya aporto PASS de MCP, Bubblewrap y puente Bubblewrap+broker
para dev5 en Kubuntu. Son evidencia historica de ESA revision; no se transfieren
a rc1 ni equivalen a ejecutar OpenCode/Qwen/OpenClaw reales.

## Bloqueos que impiden declarar estable la v1

1. **Cambio de proyecto y ciclo de vida del ejecutor.** Revocar una concesion
   detiene futuras operaciones de memoria, pero no termina automaticamente
   un proceso que ya puede escribir en su workspace. Hace falta un controlador
   confiable que cierre y recoja el sandbox anterior, invalide su sesion y solo
   entonces lance el nuevo. No basta cambiar cwd ni el proyecto en un prompt.
2. **Cliente real y acceso al modelo.** No se ha acreditado el cliente de IA
   completo dentro del sandbox con el runtime y acceso al proveedor aprobados.
   El sandbox actual bloquea la red: abrirla globalmente no es una solucion
   aceptable. Debe mantenerse el aislamiento de archivos y credenciales.
3. **Despliegue en el equipo de destino.** Ejecutar el validador de rc1 y revisar
   UIDs, propietario de SQLite, secretos, montajes y permisos reales.

Las comprobaciones de hardlinks/paths no eliminan carreras contra un proceso
externo privilegiado o con el mismo UID que modifique el arbol concurrentemente.
No se promete proteger un agente con terminal libre en el host.

## Una validacion del candidato en el equipo de destino

```bash
.venv/bin/python -m pip install -e '.[mcp]'
.venv/bin/python scripts/validate_release.py
```

El script no instala programas, no usa sudo, no modifica proyectos reales y
no publica recuerdos. Genera un resumen que distingue PASS/FAIL/BLOCKED y
mantiene `stable_release_ready: false` mientras los gates manuales sigan abiertos.

## Referencias primarias

- SQLite, transacciones: https://sqlite.org/lang_transaction.html
- SQLite, WAL y durabilidad: https://sqlite.org/wal.html
- SQLite, pragmas: https://sqlite.org/pragma.html
- MCP Python SDK 1.x: https://py.sdk.modelcontextprotocol.io/v1/
- Bubblewrap, modelo de seguridad: https://github.com/containers/bubblewrap/blob/main/README.md
