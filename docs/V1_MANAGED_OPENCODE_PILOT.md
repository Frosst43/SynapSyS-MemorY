# Control de ciclo de vida y piloto OpenCode V2 (experimental)

Base: `033a0414ae97e60fdab61a0f99405a284d3737a5` / `1.0.0rc1`.
Estado: ampliacion experimental; no modifica main ni declara v1 estable.
Sin IA interna. Motor, broker, almacenamiento y adaptador MCP existentes
permanecen sin cambios en esta entrega.

## Evidencia del propietario

El propietario confirmo el commit anterior en su copia local y los tres
resultados `PASS: unit`, `PASS: mcp` y `PASS: sandbox_broker`. Tambien facilito:
OpenCode 2.2.5, Ollama 0.40.3 y Bubblewrap 0.11.1. No hay que reinstalar ni
actualizar esas aplicaciones para este piloto. Las rutas absolutas privadas
de su equipo no se publican como configuracion de producto.

## Nuevo controlador, aplicable a cualquier proyecto

`managed_session.py` ofrece un componente de confianza, NO una herramienta MCP.
El propietario/controlador externo aprueba un proyecto; no basta un texto del
modelo. Secuencia: aprobar -> revocar memoria anterior -> detener entorno anterior
-> confirmar cierre -> emitir nueva concesion -> iniciar nuevo entorno.

- Un rechazo no altera la sesion previa.
- Un fallo de revocacion igualmente intenta cerrar el proceso, pero impide
  conceder el proyecto siguiente. Un fallo de cierre tambien bloquea el cambio.
- Cada instancia administra una sesion; no habilita padre/hijos ni grants globales.
- Un identificador estable del kernel (pidfd) identifica al init del namespace
  de procesos, no solo al proceso monitor de bwrap. La salida del programa principal
  NO es suficiente para certificar que sus hijos han terminado.
- El ejecutable permanece tras una barrera `--block-fd` hasta obtener y verificar
  el pidfd. No se libera una aplicacion con identidad de proceso no verificada.
- El cierre envia SIGKILL al init del namespace y espera su finalizacion, y luego
  recoge el proceso monitor. **Es un cierre forzado: no guarda cambios no guardados
  dentro de una aplicacion**. No utilizar aun con sesiones reales de trabajo.
- Este componente aun requiere una interfaz administrativa de consentimiento,
  provision de credenciales y supervision continua de caidas/TTL para produccion.
  No transforma en seguros agentes ya arrancados con shell libre en el host.

## Piloto nuevo (datos temporales exclusivamente)

```bash
PYTHONPATH=src .venv/bin/python scripts/pilot_opencode_managed.py \
  --opencode "$HOME/.opencode/bin/opencode"
```

Primero se inicia un proyecto A temporal con un descendiente separado por
setsid e ignorando SIGTERM que sigue escribiendo un marcador. El cambio a B debe
revocar la memoria de A, terminar su namespace y confirmar que el marcador ya
no cambia. La memoria de A no se devuelve a B. Los nombres son fixtures, no
proyectos privilegiados ni rutas del usuario.

Despues se ejecuta el **OpenCode real** proporcionado por el usuario, con
`mcp list`, dentro de Bubblewrap. El piloto:

- No llama a Ollama, no carga modelos, no pide respuestas a ninguna IA y no usa GPU.
- Copia unicamente el ejecutable nativo ELF a un directorio temporal.
- Monta en lectura el venv de MemorY y su `src`, con sus rutas originales para que
  funcione la instalacion editable. No monta el checkout completo ni su `.git`.
- Crea una configuracion OpenCode V2 nueva con `mcp.servers`, sin copiar la global
  del usuario. HOME/XDG son efimeros. El pequeno archivo de configuracion administrada
  desactiva actualizaciones y compartir sesiones; el kernel mantiene red IP aislada.
- Expone solo el socket de DATOS y el token temporal del proyecto, no SQLite ni admin.
- Exige salida exitosa, estado `synapsys-memory connected` y una nueva consulta de
  perfil al broker iniciada por el proceso MCP. Un codigo cero aislado no da PASS.
- Al terminar, revoca la sesion y elimina los proyectos, DB, configuracion y tokens
  temporales. Deja solo logs/resumenes en un directorio privado de evidencias temporal.

`--lifecycle-only` prueba la transicion sin necesitar OpenCode ni SDK MCP.
No ejecutarlo con sudo. No actualiza configuraciones del usuario, no instala
paquetes, no cambia politicas del kernel ni toca el proyecto Mobile.

## Alcance de las pruebas de esta entrega

23/23 pruebas nuevas PASS localmente: orden de cierre y revocacion, rechazos,
fallos de cierre, no concesion posterior, credenciales fuera del repr, parser
V2, protocolo de identidad y terminacion de un proceso temporal propio mediante
pidfd real. Algunas pruebas del backend de sandbox usan dobles; no acreditan
Bubblewrap real. Se corrigio el campo de actualizacion al contrastarlo con el
esquema V2 y se volvio a ejecutar la bateria nueva, sin repetir la suite anterior.

En el entorno de desarrollo no hay Bubblewrap ni el ejecutable OpenCode del
propietario. El ensayo fisico del controlador y la conexion OpenCode+MCP permanecen
**PENDIENTES** hasta recibir su salida o una ejecucion verificada. Los PASS rc1
anteriores no se atribuyen automaticamente a este componente nuevo.

Incluso un PASS de `mcp list` acredita conexion y herramientas, NO una tarea
completa de Qwen ni una prueba de inferencia. El acceso restringido al modelo y
la operacion diaria persistente siguen pendientes. La version no es aun estable.

## Referencias primarias verificadas

- OpenCode V2 MCP: https://opencode.ai/docs/mcp-servers
- OpenCode configuracion: https://opencode.ai/docs/config
- Esquema: https://opencode.ai/config.json
- Bubblewrap v0.11.1: https://github.com/containers/bubblewrap/blob/v0.11.1/bubblewrap.c
- PID namespaces: https://man7.org/linux/man-pages/man7/pid_namespaces.7.html
- pidfd: https://man7.org/linux/man-pages/man2/pidfd_open.2.html
