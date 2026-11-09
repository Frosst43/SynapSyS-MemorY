# Validación MCP stdio en Kubuntu — 2026-11-09

**Origen de la evidencia**: salida de Konsole compartida por el propietario.
**Versión de aplicación**: `synapsys-memory 0.2.0.dev4`.
**Versión del SDK MCP**: `mcp 1.30.0`, instalada con el extra `.[mcp]`.
**Rama**: `experiment/v02-permissions-mcp-migration`.

## Comandos ejecutados

```bash
cd ~/Documentos/SynapSyS-MemorY
.venv/bin/python -m pip install -e '.[mcp]'
.venv/bin/python scripts/verify_mcp_stdio.py
```

## Evidencia observada

El log del propietario muestra instalación `Successfully installed ... mcp-1.30.0
... synapsys-memory-0.2.0.dev4` y luego:
```text
Processing request of type ListToolsRequest
Processing request of type CallToolRequest
Processing request of type ListToolsRequest
Processing request of type CallToolRequest
Processing request of type ListToolsRequest
Processing request of type CallToolRequest
PASS: MCP stdio + broker; lectura compartida, derechos y aislamiento
```

Se acredita **PASS MCP stdio real con tres procesos de prueba**: escritura
en proyecto compartido, lectura por cliente autorizado, y ausencia de la
memoria en un proyecto distinto. Los clientes de prueba usan identificadores
OpenCode, Codex y OpenClaw, **no son ejecuciones reales de esos productos**.

La prueba anterior de `scripts/pilot_project_memory.py` también concluyó
con `result: PASS` y sus 6 comprobaciones con datos temporales.

## Lo que NO valida este resultado

- Integración con instalaciones reales de OpenCode, Codex, OpenClaw o Qwen.
- Aislamiento de archivos Linux con Bubblewrap.
- Despliegue del broker bajo identidades OS separadas y credenciales reales.
- Confianza en una IA que disponga de shell no restringida.
- Seguridad de datos confidenciales o preparación de versión estable.

Próximo gate: `PYTHONPATH=src .venv/bin/python scripts/verify_sandbox_bwrap.py`
en Kubuntu, con dos proyectos temporales. Un PASS indica el aislamiento
de esa prueba, no la seguridad completa del despliegue.

Sin tokens, contraseñas ni rutas sensibles reproducidos en este informe.
