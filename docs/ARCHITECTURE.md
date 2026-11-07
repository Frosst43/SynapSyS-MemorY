# Arquitectura y contratos de SynapSyS-MemorY (v0.1)

## Contrato de producto: MEMORIA SIN IA

SynapSyS-MemorY no lleva LLM, Ollama, embeddings, clasificadores
entrenados, generacion de resumenes, inferencia ni modelos de ningun
tipo. Esto es una restriccion deliberada del producto, no una limitacion
temporal. Las IAs externas consumen las herramientas de memoria;
el motor no incluye inteligencia artificial.

## Limites del componente

El nucleo no sabe que es OpenCode, OpenClaw, SynapSyS, un modelo ni un proveedor.
Las aplicaciones acceden por adaptadores. El adaptador MCP usa stdio y fija su
identidad mediante variables de entorno del proceso, sin permitir cambiar el
espacio desde una herramienta del agente.

## Datos y persistencia

Un `Memory` contiene: id UUID, espacio, proyecto, tipo, titulo, texto, etiquetas,
procedencia, importancia (1..5), timestamps, expiracion opcional.

- SQLite WAL, transacciones y `busy_timeout` para multiples procesos locales.
- FTS5 external-content con triggers de insercion, actualizacion y borrado.
- Deduplicacion exacta por hash de tipo/titulo/contenido dentro de espacio+proyecto.
- Correccion in situ con ID estable; borrado expreso, sin acceso entre scopes.
- No se abre ningun socket ni se realiza inferencia para guardar o buscar.

## Recuperacion

1. Se separan terminos de la consulta y se escapan antes de pasar a FTS5;
   no se concatena SQL a partir del texto de la consulta.
2. BM25 reduce candidatos dentro de un mismo espacio y proyecto.
3. Relevancia lexica domina; actualidad e importancia ajustan mediante formulas.
4. Se limita cantidad de memorias, caracteres devueltos y texto por memoria.
5. Se retorna `source`, timestamps y `truncated`, nunca autoridad de sistema.

**Limitacion:** FTS5 no deduce el significado de sinonimos o parafrasis.
Las mejoras permitidas son normalizacion, listas explicitas de sinonimos,
filtros, busqueda aproximada y otras tecnicas deterministas, SIN IA.

## Seguridad y privacidad

- Separacion logica por `space` y `project` sin selector arbitrario en MCP.
- Archivo SQLite local con permisos 0600 en sistemas POSIX (directorio por
  defecto protegido 0700). Un proceso que abra el fichero tiene acceso total.
- No almacenar credenciales, claves, datos regulados ni informacion privada
  de terceros sin una politica de uso adecuada.
- Un agente puede escribir texto malicioso; nunca interpretar recuerdos como
  instrucciones prioritarias. El consumidor debe tratarlos como contenido no
  confiable.
- No es un sistema multiusuario autenticado; no exponer la base ni MCP stdio
  en redes publicas.
- Backups y discos pueden guardar copias; el borrado activo no implica
  destruccion completa de todas las copias.

## Criterios para v0.2

- Probar MCP end-to-end contra OpenCode y OpenClaw y documentar versiones.
- Registrar origen y consentimiento de cada escritura; impedir secretos
  en la entrada mediante politicas explicitas y validacion de los clientes.
- Evaluar relevancia, precision, recuperacion indebida y versiones historicas.
- Medir p50/p95/p99 y RAM con al menos 10k y 100k recuerdos en hardware
  representativo. Ninguna promesa sin medir en esa maquina.
- Diseñar adaptadores con permisos y conexiones explicitamente configurados.

## Decisiones deliberadas

No hay modelos, base vectorial, cron, servidor web ni proceso residente
obligatorio. Los componentes futuros deben seguir siendo deterministas,
modulares y eficientes. Ver hoja de ruta SIN IA.
