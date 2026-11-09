# Futuro: proyectos principales blindados y acceso combinado a subproyectos

Fecha: 2026-11-08
Estado: **decision de producto, APARCADA para una version posterior**.
No forma parte de v0.2 ni esta implementada.

## Clasificacion de proyectos

Cada proyecto tiene un ID inmutable, un tipo y, si corresponde,
un parent_id. La jerarquia nunca se deduce del nombre, de una ruta
de archivos ni del programa de IA que lo utilice.

Principales que el propietario quiere blindar como raices independientes:

- SynapSyS
- SynapSyS-MemorY
- SYPcoin
- SynapSyS-Lab

**Estas raices no se mezclan entre si por defecto ni mediante una
autorizacion implicita por compartir cliente o usuario.** No debe
existir una opcion de "todos los proyectos" que abra estas raices.

Los proyectos secundarios pueden estar vinculados por una relacion
explicita parent_id al principal correspondiente. No se asigna
automaticamente SYPminer/Terminal PRO a SYPcoin ni a ninguna otra raiz
sin confirmar su pertenencia y su alcance.

## Regla actual (v0.2)

Una sesion/conector trabaja en UN proyecto autorizado. Cambiar a otro
exige permiso nuevo y revocar el anterior, tanto en memoria como en el
sandbox de archivos.

La nueva excepcion de acceso combinado es SOLO un requisito futuro
y no modifica la regla actual.

## Excepcion futura: colaboracion entre proyecto padre e hijos

Ejemplo hipotetico:
- Una sesion autorizada trabaja en SynapSyS / componente-interfaz.
- El usuario solicita utilizar tambien SynapSyS / componente-api.
- El controlador confiable solicita consentimiento especifico para
  añadir ese segundo proyecto dentro de la misma raiz.
- Si se concede, se activa un permiso de sesion temporario y limitado
  a esos dos proyectos y a las operaciones concretas autorizadas.
- Los otros proyectos hermanos y todas las otras raices siguen
  completamente denegados.

Reglas que se deben implementar antes de activar esta opcion:

1. **Deny by default**: parent_id NO implica heredar permisos.
2. ID de proyecto y raiz verificados por registro de proyectos.
3. Consentimiento de propietario explicito, fuera del control del agente,
   y revocable en cualquier momento.
4. Permiso combinado expresa `session_id`, `project_ids`, `actions`,
   `expires_at`, actor y version de politica.
5. Rechazar combinaciones cuyos proyectos pertenezcan a distintas
   raices principales protegidas (SynapSyS, SynapSyS-MemorY,
   SYPcoin, SynapSyS-Lab). No hay grant universal.
6. La memoria aplica ACL por cada operacion y por cada resultado; no
   se amplia una busqueda por parecido de nombre o ruta.
7. El sandbox de archivos aplica una lista positiva de directorios
   canonicos independiente de los permisos de memoria; resolver y
   bloquear symlinks/montajes que escapen del alcance.
8. Revocacion atomica: invalidar derechos antiguos, drenar acciones
   pendientes y evitar conservar handles/tokens/caches con capacidad.
9. Log de autorizacion/revocacion y pruebas negativas de acceso a
   otras raices, hermanos no incluidos y recursos fuera de la sesion.
10. No introducir IA en el motor de memoria.

## Casos de prueba futuros

- Acceso solo a hijo A: denegar padre y hermano B.
- Autorizar explicitamente A+B dentro de la misma raiz: permitir
  acciones concedidas y bloquear cualquier otro proyecto.
- Intentar combinar hijo de SYPcoin con hijo de SynapSyS:
  denegacion, aunque ambas sesiones usen Qwen.
- Revocar B y comprobar denegacion inmediata de lecturas, escrituras
  y operaciones en archivos incluso con peticiones concurrentes.
- Una solicitud del agente que diga "activa permisos" no sustituye
  la decision del usuario.
- Sin broker/sandbox verificable, no activar el modo multiproyecto.

## Prioridad

**Pospuesta**. Antes terminar seguridad v0.2, conexion MCP real,
autenticacion/broker y aislamiento de archivos por proyecto. Incorporar
luego la jerarquia y grants temporales como evolucion compatible,
sin romper las memorias ya almacenadas.

La pertenencia concreta de proyectos secundarios a cada raiz se
confirmara cuando se vaya a implementar la funcion.
