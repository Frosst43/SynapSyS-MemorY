# Sandbox universal por proyecto — v0.2.0.dev4 (experimental)

**Regla:** se protege **cualquier proyecto**, sin nombres codificados como
SynapSyS, Terminal PRO, SYPcoin o un producto específico. Cada sesión recibe
un workspace concreto y no accede a repositorios ajenos.

La primera implementación usa **Bubblewrap (bwrap)**, una herramienta
Linux que crea un espacio de montajes separado y namespaces. El motor de
memoria sigue siendo 100 % sin IA y sin dependencia de Bubblewrap para
almacenar o consultar recuerdos. El lanzador es un componente opcional.

## Funcionamiento

- Una política JSON de confianza asocia project_id a **una ruta canónica**.
- Se deniegan proyectos no registrados, políticas con permisos amplios,
  symlinks de raíz, rutas ambiguas y workspaces del sistema o todo HOME.
- Se rechazan montajes anidados bajo el workspace y políticas que el
  propio proyecto pudiera editar.
- El lanzador ejecuta siempre el programa *dentro* de bwrap, con un único
  workspace /workspace (lectura/escritura o solo lectura).
- Monta herramientas del SO desde /usr, /bin, /lib, /lib64, /sbin
  en **solo lectura** si están disponibles. No monta todo HOME ni /etc.
- Usa /tmp, /run efímeros y namespaces separados de PID, IPC, UTS,
  usuario y red. No ofrece red ni GPU por defecto.
- Limpia todas las variables del entorno y reconstruye las mínimas necesarias.
- Si Bubblewrap no existe o el kernel rechaza namespaces, **falla cerrado**:
  no se ejecuta el programa sin aislamiento.

## Ejemplo de política (archivo ADMIN; no guardarlo en el repositorio)

Crear la política en una carpeta de configuración privada y darle modo 0600.
Es **solo un ejemplo**: rutas genéricas que deben sustituirse por directorios
reales. El agente no elige ni modifica este archivo.

```json
{
  "schema_version": 1,
  "projects": {
    "mi-proyecto": {
      "workspace": "/home/usuario/Proyectos/mi-proyecto",
      "read_only": false
    },
    "otro-proyecto": {
      "workspace": "/home/usuario/Proyectos/otro-proyecto",
      "read_only": true
    }
  }
}
```

```bash
sudo apt install bubblewrap
chmod 600 /RUTA/PRIVADA/politica.json
synapsys-memory-sandbox --policy /RUTA/PRIVADA/politica.json   --project mi-proyecto -- python3 programa.py
```

El comando es un **lanzador externo de confianza**: debe ejecutarlo el
administrador/controlador *antes* de que arranque la IA. **No sirve**
para quitar permisos a una IA que ya se esté ejecutando sin restricciones;
esa IA debe terminarse y reiniciarse bajo el sandbox.

## Limitaciones y bloqueos

- No se ha validado todavía un proceso de agente real con Bubblewrap en
  el equipo del usuario. Las pruebas de argumentos, políticas y fallos son
  unitarias y no demuestran que el kernel esté imponiendo el sandbox.
- No se ha conectado aún el broker a un socket accesible *dentro* del
  sandbox ni distribuido credenciales de memoria. La protección de
  memoria depende de permisos del broker y de su identidad OS separada.
- Si el workspace autorizado contiene secretos o submontajes sensibles,
  podrían ser visibles. Hay que auditar cada workspace antes de autorizarlo.
- Un cliente con mismo UID que el controlador y acceso previo al sistema,
  o capaz de editar la política, puede eludir el modelo. Para despliegues
  fuertes se requieren identidades OS, rutas de configuración y credenciales
  separadas del agente. No considerar esto seguro para clientes hostiles.
- El aislamiento estricto por defecto bloquea Ollama remoto/local por red,
  plugins instalados dentro de HOME y operaciones que requieran otras rutas.
  Estas capacidades requieren concesiones explícitas independientes y pruebas.
- Bubblewrap no bloquea por sí solo todos los canales de datos (descriptores
  heredados, procesos previos, dispositivos pasados, secrets dentro del
  workspace). No conceder permisos globales de shell al agente fuera de él.

## Comprobar sin tocar proyectos reales

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_sandbox.py' -v
PYTHONPATH=src python3 scripts/verify_sandbox_bwrap.py
```

El segundo comando exige Bubblewrap y namespaces de usuario disponibles.
Si no están, devuelve NO VALIDADO con código distinto de cero.

## Próxima validación

1. Instalar bubblewrap en entorno de pruebas controlado.
2. Crear dos proyectos de prueba en carpetas temporales y registrar solo A.
3. Ejecutar dentro de Bwrap un programa que escriba en A y otro que intente
   escribir en B; **A debe permitir y B debe denegar**.
4. Verificar cierre del sandbox, sin procesos ni montajes residuales.
5. Probar por separado el conector de memoria con dos UIDs y el agente real.

**No fusionar a main ni ejecutar en proyectos reales hasta superar estas pruebas.**
