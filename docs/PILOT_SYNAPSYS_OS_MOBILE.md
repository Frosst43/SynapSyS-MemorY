# Piloto de SynapSyS-MemorY con SynapSyS-oS Mobile

Estado: **piloto experimental y aislado**, sin IA interna.

El objetivo es comprobar que dos conectores pueden compartir la **memoria
solo de un proyecto** y que otros proyectos permanecen inaccesibles. Este
piloto no requiere que exista un repositorio de Mobile y **no abre ni edita
archivos de SynapSyS, SYPcoin, Terminal PRO ni Mobile**.

## Etapa 1: prueba de broker y aislamiento (sin MCP ni IA)

En Kubuntu con Python >=3.11 y Git, desde un directorio **nuevo**:

```bash
mkdir -p "$HOME/Documentos"
git clone --branch experiment/v02-permissions-mcp-migration \
  https://github.com/Frosst43/SynapSyS-MemorY.git \
  "$HOME/Documentos/SynapSyS-MemorY-piloto"
cd "$HOME/Documentos/SynapSyS-MemorY-piloto"
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python scripts/pilot_project_memory.py \
  --project synapsys-os-mobile --foreign-project synapsys
```

La URL requiere acceso autorizado al repositorio privado. Si ya existe
ese directorio, **no forzar clonación ni borrar nada**: usar otra ruta nueva.
La prueba emplea una base SQLite y sockets en un directorio temporal;
se eliminan al terminar. No guarda tokens ni credenciales del usuario.

Resultado esperado: JSON con `result: PASS`, seis comprobaciones,
`writes_real_projects: false`, `real_clients_tested: false` y
`mcp_tested: false`. **Solo afirmar PASS después de ejecutar y comprobar.**

Para las dos pruebas nuevas:

```bash
PYTHONPATH=src:. .venv/bin/python -m unittest discover \
  -s tests -p 'test_pilot_project_memory.py' -v
```

## Etapa 2: protocolo MCP real, aún pendiente en el equipo

Después de superar etapa 1, instalar el **protocolo** MCP (no IA):

```bash
.venv/bin/python -m pip install -e '.[mcp]'
PYTHONPATH=src .venv/bin/python scripts/verify_mcp_stdio.py
```

Esta prueba conecta tres procesos MCP temporales con el broker.
Los nombres de clientes se simulan, pero se realiza un handshake MCP real.
**No modifica** OpenCode, Codex ni OpenClaw instalados.

## Etapa 3: prueba del sandbox de archivos, en entorno temporal

Si `bwrap` no está instalado, se puede instalar Bubblewrap por el gestor
normal del sistema (con permiso del administrador) y ejecutar:

```bash
PYTHONPATH=src .venv/bin/python scripts/verify_sandbox_bwrap.py
```

El script prueba dos carpetas **temporales**, no el repositorio real.
Un retorno diferente de cero significa **NO VALIDADO** o **FAIL**;
no se debe afirmar aislamiento completo.

## Etapa 4: integrar Mobile cuando los gates sean verdes

Para el proyecto real se usará un identificador fijo, por ejemplo
`synapsys-os-mobile`, sin heredar accesos sobre `synapsys`, `sypcoin`
o `synapsys-memory`. El administrador creará permisos de lectura y,
cuando lo apruebe, de escritura **solo** para Mobile.

Requisitos antes de integrar con Qwen/OpenCode instalados:

- Lanzador confiable que entregue el token de sesión al proceso correcto
  y lo retire al cambiar de proyecto; nunca escribir tokens en prompts.
- Broker protegido y base fuera de los directorios del agente.
- Sandbox con acceso solo al workspace Mobile y sin rutas hacia repos ajenos.
- El sandbox dev4 actual **no monta todavía el socket del broker** y
  **deshabilita red**, por lo que Qwen a través de Ollama no puede usarse
  tal como está. Hace falta integrar ambos accesos con política restringida
  y pruebas de extremo a extremo. No desactivar las protecciones a ciegas.
- Comprobar que el agente no modifica archivos de ningún proyecto ajeno
  antes de activar trabajo real. El piloto de memoria no prueba esto.

La memoria de producto y los requisitos aprobados de Mobile se pueden
registrar después a través de un conector real autorizado, sin dar a la
memoria capacidades de IA. **No** importar conversaciones de terceros
ni historiales enteros sin una decisión explícita del usuario.

## Gate de seguridad

Etapas 1-3 verifican componentes, no validan automáticamente una
instalación real segura. Mantener `main` y los repos reales intactos;
no promocionar esta versión a estable hasta completar MCP + permisos del
sistema operativo + sandbox y ensayo contra aplicaciones reales.