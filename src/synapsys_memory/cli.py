"""Interfaz CLI sin dependencias para comprobar el motor de memoria."""

import argparse
import json
from pathlib import Path

from .store import MemoryStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="synapsys-memory", description="Memoria persistente independiente")
    parser.add_argument("--db", type=Path, help="Ruta de la base SQLite")
    parser.add_argument("--space", default="personal", help="Espacio aislado o compartido")
    parser.add_argument("--project", default="", help="Proyecto aislado (opcional)")
    commands = parser.add_subparsers(dest="command", required=True)
    save = commands.add_parser("remember", help="Guardar un recuerdo")
    save.add_argument("body")
    save.add_argument("--title", default="")
    save.add_argument("--kind", default="fact")
    save.add_argument("--tag", action="append", default=[])
    save.add_argument("--source", default="manual")
    save.add_argument("--importance", type=int, default=3)
    save.add_argument("--ttl-days", type=int)
    recall = commands.add_parser("recall", help="Buscar recuerdos")
    recall.add_argument("query")
    recall.add_argument("--limit", type=int, default=5)
    recall.add_argument("--max-chars", type=int, default=4000)
    recent = commands.add_parser("recent", help="Memorias recientes")
    recent.add_argument("--limit", type=int, default=20)
    recent.add_argument("--kind")
    for command in ("get", "forget"):
        action = commands.add_parser(command)
        action.add_argument("id")
    update = commands.add_parser("update", help="Corregir memoria existente")
    update.add_argument("id")
    update.add_argument("body")
    update.add_argument("--title", default="")
    commands.add_parser("stats", help="Estadisticas")
    backup = commands.add_parser("backup", help="Respaldar SQLite")
    backup.add_argument("destination", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        with MemoryStore(args.db, space=args.space, project=args.project) as store:
            if args.command == "remember":
                result = store.remember(args.body, title=args.title, kind=args.kind,
                                        tags=args.tag, source=args.source,
                                        importance=args.importance, ttl_days=args.ttl_days)
            elif args.command == "recall":
                result = store.recall(args.query, limit=args.limit, max_chars=args.max_chars)
            elif args.command == "recent":
                result = store.list_recent(limit=args.limit, kind=args.kind)
            elif args.command == "get":
                result = store.get(args.id)
            elif args.command == "forget":
                result = {"deleted": store.forget(args.id)}
            elif args.command == "update":
                result = store.update(args.id, body=args.body, title=args.title)
            elif args.command == "stats":
                result = store.stats()
            elif args.command == "backup":
                result = {"backup": store.backup(args.destination)}
            else:
                raise ValueError("Comando desconocido")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError) as exc:
        build_parser().exit(2, f"Error: {exc}\n")
    return 2
