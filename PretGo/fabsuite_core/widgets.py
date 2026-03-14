"""
fabsuite_core.widgets — Builders de réponses widgets conformes à la spec v1.0.0.

Chaque fonction retourne un dict prêt à être jsonify() par Flask.
Le type de données est forcé par la fonction utilisée, ce qui empêche
les erreurs type/données incohérentes.
"""

from datetime import datetime


def counter(value, label, unit=""):
    """Widget type 'counter' — un chiffre avec label.

    Retourne : {"value": N, "label": "...", "unit": "..."}
    """
    return {
        "value": int(value) if value is not None else 0,
        "label": str(label),
        "unit": str(unit),
    }


def status_list(items):
    """Widget type 'status' — indicateurs d'état.

    items : liste de dicts {"label": str, "status": "ok"|"warning"|"error"}
    Retourne : {"items": [...]}
    """
    _valid_statuses = {"ok", "warning", "error"}
    clean = []
    for item in items:
        s = str(item.get("status", "ok"))
        if s not in _valid_statuses:
            s = "ok"
        clean.append({
            "label": str(item.get("label", "")),
            "status": s,
        })
    return {"items": clean}


def item_list(items):
    """Widget type 'list' — liste d'éléments.

    items : liste de dicts {"label": str, "value": str, "status": str (optionnel)}
    Retourne : {"items": [...]}
    """
    clean = []
    for item in items:
        entry = {
            "label": str(item.get("label", "")),
            "value": str(item.get("value", "")),
        }
        if "status" in item:
            entry["status"] = str(item["status"])
        clean.append(entry)
    return {"items": clean}


def chart(chart_type, labels, values):
    """Widget type 'chart' — graphique simple.

    chart_type : "bar", "line" ou "pie"
    labels : liste de strings
    values : liste de nombres
    Retourne : {"type": "bar|line|pie", "labels": [...], "values": [...]}
    """
    _valid_types = {"bar", "line", "pie"}
    if chart_type not in _valid_types:
        chart_type = "bar"
    return {
        "type": chart_type,
        "labels": [str(l) for l in labels],
        "values": [float(v) if v is not None else 0 for v in values],
    }


def text(content):
    """Widget type 'text' — texte libre.

    Retourne : {"content": "..."}
    """
    return {"content": str(content)}


def table(headers, rows):
    """Widget type 'table' — tableau de données.

    headers : liste de strings (en-têtes de colonnes)
    rows : liste de listes (lignes de données)
    Retourne : {"headers": [...], "rows": [[...], ...]}
    """
    return {
        "headers": [str(h) for h in headers],
        "rows": [[str(cell) for cell in row] for row in rows],
    }


def notification(id, type, title, message, link="", created_at=None):
    """Construit une notification conforme à la spec v1.0.0.

    Retourne : {"id": "...", "type": "info|warning|error", "title": "...",
                "message": "...", "created_at": "ISO8601", "link": "..."}
    """
    _valid_types = {"info", "warning", "error"}
    if type not in _valid_types:
        type = "info"
    return {
        "id": str(id),
        "type": type,
        "title": str(title),
        "message": str(message),
        "created_at": created_at or datetime.now().isoformat(),
        "link": str(link),
    }
