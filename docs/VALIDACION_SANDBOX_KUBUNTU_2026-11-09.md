# Validacion Bubblewrap en Kubuntu — 2026-11-09

Evidencia: salida de Konsole proporcionada por el propietario.

```bash
cd ~/Documentos/SynapSyS-MemorY
command -v bwrap >/dev/null || sudo apt install bubblewrap
PYTHONPATH=src .venv/bin/python scripts/verify_sandbox_bwrap.py
```

Salida observada:
```text
SANDBOX_CHILD_PASS
PASS: proyecto A escribible, proyecto B invisible, fichero B intacto
```

Se ha verificado fisicamente el lanzamiento bajo Bubblewrap, una
escritura en un workspace temporal permitido, la invisibilidad del
fichero del segundo proyecto y su integridad posterior. El ensayo
no incluye OpenCode/Qwen reales ni conexion a la memoria DENTRO del
sandbox ni despliegue multi-UID en produccion.

Siguiente gate: `scripts/verify_sandbox_broker_bridge.py`.
