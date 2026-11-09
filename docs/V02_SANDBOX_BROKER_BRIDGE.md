# Sandbox + broker — puente experimental v0.2.0.dev5

El lanzador opcional de Bubblewrap admite un bloque `memory` en la
politica privada por proyecto. Monta **solo** el socket Unix de DATOS
del broker y un fichero de token de sesion temporal de SOLO LECTURA,
en `/run/synapsys-memory/`. No monta SQLite, el socket de administracion,
el directorio que contiene el socket ni el HOME del usuario.

No es necesario ningun modelo de IA; todas las reglas son deterministas.

Ejemplo (sustituir las rutas; nunca poner tokens reales en Git):
```json
{
  "schema_version": 1,
  "projects": {
    "mi-proyecto": {
      "workspace": "/home/usuario/Proyectos/mi-proyecto",
      "read_only": false,
      "memory": {
        "data_socket": "/run/privado/symem/data.sock",
        "token_file": "/ruta-privada/credenciales/mi-proyecto.token",
        "space": "privado"
      }
    }
  }
}
```

El sistema valida que las rutas sean canonicas, que el socket sea Unix,
que el token sea un archivo privado 0600, que ninguna de esas rutas quede
en un workspace, y que el permiso real del broker coincida con el
proyecto Y espacio indicados. Si falta o falla algo, no ejecuta el
programa. El agente no elige estos valores.

En MCP, `SYMEM_SESSION_TOKEN_FILE` permite leer el token desde fichero.
Se rechaza la presencia simultanea de `SYMEM_SESSION_TOKEN` inline.
El token no figura en la linea de comandos de Bubblewrap.

### Ensayo fisico pendiente en el equipo del propietario

```bash
cd ~/Documentos/SynapSyS-MemorY
git pull --ff-only
PYTHONPATH=src .venv/bin/python scripts/verify_sandbox_broker_bridge.py
```

El script crea un broker, SQLite, dos proyectos y un token **en un
directorio temporal**. Un proceso en Bubblewrap escribe en su workspace
y guarda un recuerdo usando el socket. Otro proyecto no ve el recuerdo;
se comprueba que el fichero ajeno queda intacto y el token se revoca.

La prueba fisica conjunta Bubblewrap+broker no se ha ejecutado todavia
en Kubuntu. El aislamiento de archivos y el protocolo MCP, por separado,
ya dieron PASS en el equipo del propietario.

**Limites esenciales**: no constituye seguridad para un agente
con shell libre en el host y el mismo UID; el sandbox y el broker
necesitan lanzadores privilegiados de confianza, separacion de UIDs,
validacion de OpenCode real, y mecanismos seguros de distribucion de
credenciales. La red IP sigue bloqueada: Qwen/Ollama en el host no es
accesible desde este sandbox de forma predeterminada.
No conectar a proyectos reales hasta completar estas validaciones.
