# Arquitectura multicliente y multiproyecto

Fecha: 2026-11-07
Estado: requisito funcional confirmado; desarrollo parcial en v0.1.

## Proposito

SynapSyS-MemorY es un servicio independiente y **sin IA interna** que
puede conectarse simultaneamente a OpenCode, OpenClaw, Codex, SynapSyS y
a cualquier otra aplicacion que pueda invocar un conector compatible.

Un mismo proyecto debe poder conservar una memoria continua aunque
las sesiones cambien de cliente, proveedor o maquina. La memoria no es
propiedad del agente que la escribio; pertenece al espacio/proyecto
configurado por el propietario.

## Ejemplo de uso

1. OpenCode escribe una decision en el proyecto SYPcoin.
2. Codex retoma el proyecto y recupera esa decision.
3. OpenClaw consulta el mismo estado si tiene acceso de lectura.
4. Las memorias de SynapSyS u otros proyectos no se mezclan.

Nada de esto requiere que SynapSyS-MemorY ejecute modelos.

## Separacion de conceptos

- Cliente: OpenCode, Codex, OpenClaw u otro programa.
- Proyecto: SYPcoin, SynapSyS, etc.
- Espacio: privado o compartido (autorizado explicitamente).
- Recuerdo: dato con origen, autor/cliente, fecha, estado y version.
- Permisos: cliente puede leer, crear, actualizar, eliminar o administrar,
  siempre de forma limitada a los proyectos autorizados.

Relaciones muchos-a-muchos:
- Un cliente puede operar en varios proyectos, segun permisos.
- Un proyecto puede estar accesible para varios clientes.
- Una cuenta/espacio privado puede quedar fuera de todo compartido.

## Conectividad

- MCP stdio: primera interfaz local, disponible en v0.1 como adaptador
  opcional (interoperabilidad real pendiente de validar).
- Un broker MCP/HTTP local autenticado permitiria a varias aplicaciones
  compartir un proceso, y ampliar a varias maquinas con TLS y ACL.
  No se expone en red por defecto; no implementado en v0.1.
- SDK Python/CLI para herramientas y proyectos sin MCP.
- Otros protocolos se incorporan mediante adaptadores desacoplados.

## Restricciones esenciales

- No IA, ni embeddings, ni Ollama, ni inferencia dentro de la memoria.
- Los conectores **no** pueden elegir libremente otro espacio/proyecto.
- En v0.1 existe aislamiento logico por espacio/proyecto, pero NO
  autenticacion fuerte: los procesos con acceso directo a SQLite pueden
  saltar esos filtros. No afirmar seguridad multiusuario hasta implementar
  broker o separacion de bases y permisos del sistema operativo.
- Lectura, escritura y borrado deben poder concederse por separado.
- Ningun cliente puede sobrescribir sin rastro decisiones ajenas: registro
  versionado y reglas de concurrencia pendientes.
- Deduplicar eventos importados de distintas fuentes mediante claves
  idempotentes; evitar guardar varias veces el mismo evento.
- No confundir *tener acceso a herramientas MCP* con captura automatica
  de todas las conversaciones. Esto ultimo requiere adaptadores y permisos.

## Compatibilidad documentada (no integra pruebas realizadas)

- OpenCode: MCP local/remoto, con version/configuracion dependiente
  de la generacion instalada.
- Codex: MCP stdio y HTTP, configuracion global o por proyecto.
- OpenClaw: conexion a MCP externo mediante ajustes mcp.servers
  y politicas de herramientas.

Fuentes oficiales:
- https://opencode.ai/docs/mcp-servers
- https://learn.chatgpt.com/docs/extend/mcp?surface=cli
- https://docs.openclaw.ai/tools/mcp

## Criterio de aceptacion principal

Preparar un entorno de pruebas con dos o mas clientes reales,
un proyecto compartido y otro privado. Un cliente registra un recuerdo,
otro lo recupera, y un tercero sin acceso recibe denegacion. Probar
versiones, concurrencia, reinicios y cierre del servicio. Probar tambien
que el motor arranca y funciona sin software de IA instalado.

La funcion multicliente completa, autenticacion, sincronizacion entre
maquinas y captura automatica NO estan implementadas todavia.
