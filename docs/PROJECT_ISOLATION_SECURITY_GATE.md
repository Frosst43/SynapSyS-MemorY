# Regla obligatoria: aislamiento de proyectos y limites de archivos

Estado: requisito de seguridad aprobado por el propietario; **no todo implementado**.
Alcance: OpenCode, Codex, OpenClaw, SynapSyS, Terminal PRO y cualquier cliente futuro.
Motor: SIN IA.

## Regla de oro

UNA plataforma de memoria sirve a VARIOS clientes y proyectos, pero cada
**sesion/conector** posee un contexto autorizado de proyecto y operaciones.
No puede ver ni modificar memorias de otros proyectos simplemente porque
el mismo usuario o cliente tenga acceso a ellos en otra sesion.

Memoria compartida NO equivale a permisos sobre archivos del ordenador.

Ejemplo: una sesion OpenCode para Terminal PRO puede leer y escribir
solamente recuerdos de Terminal PRO. No debe leer, modificar ni borrar
recuerdos de SynapSyS ni de otro proyecto. Tampoco debe modificar los
archivos de SynapSyS, pero esa ultima garantia requiere aislamiento
real del entorno de ejecucion, no solo ACL del servidor de memoria.

## Dos fronteras de seguridad DISTINTAS

### 1. Memoria: responsabilidad de SynapSyS-MemorY

- Identidad de cliente/sesion autenticada y vinculada a proyecto(s).
- Permisos separados READ, CREATE, UPDATE, DELETE y ADMIN por proyecto.
- Configuracion mas segura: sin acceso por defecto (fail closed).
- Negar acceso entre proyectos, tambien con memoria compartida por varias IAs.
- Un cliente no puede enviar `project=otro` ni alterar variables de entorno
  para elevar derechos. El servidor confiable hace el mapeo y verifica ACL.
- La coincidencia de un texto, nombre, etiqueta o busqueda nunca elimina
  un filtro de proyecto/permisos.
- Auditoria de denegaciones y operaciones sin copiar secretos en logs.
- Las exportaciones, backups, listados y estadisticas requieren ACL.
- Si el motor usa una base fisica comun, solo el broker de confianza puede
  abrirla; los conectores externos no reciben acceso directo al fichero.
- Memorias que varias IAs utilizan en el mismo proyecto mantienen el
  mismo estado; memorias de otros proyectos no son visibles.
- Operaciones sobre varios proyectos requieren grant explicito de
  alcance y una sesion con contexto apropiado.

### 2. Archivos del proyecto: responsabilidad del sandbox del cliente

SynapSyS-MemorY no ejecuta instrucciones sobre archivos ni controla
automaticamente los permisos de los agentes conectados. Para impedir
modificar SynapSyS desde una sesion Terminal PRO, el cliente ejecutor
debe limitarse mediante contenedor, sandbox, permisos de SO o
otro mecanismo efectivo a los directorios permitidos.

- Espacio de trabajo canónico y lista positiva de rutas escribibles.
- Rechazar traversal, symlinks que escapen del workspace y accesos
  indirectos via herramientas/shell/editores.
- Restringir montajes, subprocesses, rutas absolutas, Git worktrees,
  herramientas con privilegios y red si pueden eludir el aislamiento.
- Separar credenciales Git y tokens; no montar repos ajenos con escritura.
- No asumir que un prompt, una advertencia o `cwd` constituyen sandbox.
- Comprobar con una prueba de escritura DENEGADA sobre un repositorio ajeno
  desde el mismo ejecutor, con un fixture no destructivo.
- Si la aplicacion no admite aislamiento verificable, indicar
  expresamente que solo hay una politica de buenas practicas y NO una
  garantia de bloqueo a nivel de sistema de archivos.

## Matriz de autorizacion de ejemplo (configuracion, NO resultado actual)

| Sesion | Proyecto memoria | Memoria | Directorio escribible |
| --- | --- | --- | --- |
| OpenCode/Terminal PRO | terminal-pro | read,create,update | Terminal_PRO_QWEN |
| Codex/Terminal PRO | terminal-pro | read | ninguno |
| OpenClaw/SynapSyS | synapsys | read,create,update | segun sandbox de SynapSyS |
| OpenCode/SYPcoin | sypcoin | read,create,update | segun sandbox de SYPcoin |

No conceder DELETE por defecto. Cada sesion de un mismo cliente
puede tener grants distintos. No equivale a afirmar que estos
sandbox ya existen o estan activados.

## Bloqueo de salida v0.2 -> estable

NO fusionar ni declarar el aislamiento seguro hasta:
1. Prueba de sesiones con cliente identico y proyectos distintos.
2. Una escritura Terminal PRO no aparece en consulta SynapSyS.
3. Lectura, escritura, modificacion, borrado, exportacion y metadatos
   entre proyectos no autorizados son rechazados.
4. Alterar proyecto/rights/identidad del conector no eleva derechos.
5. Acceso directo de un cliente no confiable al archivo de memoria
   queda impedido mediante proceso broker/credenciales/aislamiento de SO.
6. Los clientes siguen compartiendo correctamente la memoria de su
   proyecto con autorizacion explicita.
7. Las restricciones de archivos se prueban por separado en el
   entorno de OpenCode, Codex u OpenClaw correspondiente.
8. No introducir IA en SynapSyS-MemorY.

## Estado de v0.2.0.dev1

**NO CUMPLE TODAVIA ESTE GATE COMPLETO.** La rama experimental limita
herramientas MCP por `SYMEM_RIGHTS` y fija espacio/proyecto por proceso,
pero variables de entorno manipulables y acceso directo a SQLite no
proporcionan aislamiento fuerte. El motor tampoco puede impedir
escrituras en repositorios externos realizadas por las herramientas
de un agente. Hace falta el broker y un sandbox real por aplicacion.

Esta es una exigencia de diseño, no un resultado de prueba.
