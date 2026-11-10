# OpenClaw: integracion real pendiente

Ver [contrato de los ejemplos](README.md). No sustituir automaticamente la
memoria nativa del cliente ni importar conversaciones sin autorizacion.

El adaptador de SynapSyS-MemorY se ejecuta mediante stdio:

- Comando: ruta absoluta al runtime autorizado `synapsys-memory-mcp`.
- `SYMEM_BROKER_SOCKET`: socket Unix de datos, nunca el socket admin.
- `SYMEM_SESSION_TOKEN_FILE`: archivo privado de una concesion de proyecto.

El proyecto, derechos y UID se deciden en el broker. Las variables de entorno
`SYMEM_PROJECT` o `SYMEM_RIGHTS` no los amplian. Configurar el transporte segun
la documentacion de la version de OpenClaw realmente instalada; esta revision
no ha probado la aplicacion real ni habilita captura automatica.

No introducir la clave administrativa en la configuracion del agente. El
launcher/sandbox y acceso al modelo son gates pendientes para v1 estable.
