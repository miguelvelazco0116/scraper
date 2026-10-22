"""Secure Azure SQL configuration and read-only connectivity verification."""
from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

VALID_AUTH = {"SQL Password", "Microsoft Entra Interactive"}
HOST = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9.-]*\.database\.windows\.net$")
ILLEGAL = re.compile(r"[;{}\r\n\x00]")

def validate_config(config):
    server = str(config.get("server") or "").strip()
    database = str(config.get("database") or "").strip()
    username = str(config.get("username") or "").strip()
    auth = str(config.get("authentication") or "SQL Password")
    port = int(config.get("port") or 1433)
    if not HOST.fullmatch(server):
        raise ValueError("Servidor inválido: debe terminar en .database.windows.net")
    if not 1 <= port <= 65535:
        raise ValueError("Puerto inválido")
    if not database or len(database) > 128 or ILLEGAL.search(database):
        raise ValueError("Base de datos inválida")
    if not username or len(username) > 256 or ILLEGAL.search(username):
        raise ValueError("Usuario inválido")
    if auth not in VALID_AUTH:
        raise ValueError("Método de autenticación inválido")

def public_settings(config):
    public = {k: config.get(k) for k in ("server", "database", "username", "port", "authentication")}
    public["server"] = str(public["server"] or "").strip()
    public["database"] = str(public["database"] or "").strip()
    public["username"] = str(public["username"] or "").strip()
    public["port"] = int(public["port"] or 1433)
    public["authentication"] = public["authentication"] or "SQL Password"
    public["encrypt"] = True
    public["trust_server_certificate"] = False
    validate_config(public)
    return public

def settings_path(root: Path):
    return root / "config" / "local" / "azure_sql.json"

def load_settings(root: Path):
    path = settings_path(root)
    try:
        return public_settings(json.loads(path.read_text(encoding="utf-8"))) if path.is_file() else {}
    except (ValueError, TypeError, OSError):
        return {}

def save_settings(root: Path, config):
    data = public_settings(config)
    path = settings_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, prefix=".azure_sql_", suffix=".tmp", delete=False) as file:
            tmp = file.name
            json.dump(data, file, indent=2, ensure_ascii=False)
            file.write("\n")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, path)
    finally:
        if tmp and os.path.exists(tmp):
            os.unlink(tmp)
    return path

def _escape_password(password: str):
    if not password or "\x00" in password:
        raise ValueError("Falta contraseña")
    return "{" + password.replace("}", "}}") + "}"

def connection_string(config, password=""):
    cfg = public_settings(config)
    parts = [
        "Driver={ODBC Driver 18 for SQL Server}",
        f"Server=tcp:{cfg['server']},{cfg['port']}",
        f"Database={cfg['database']}",
        "Encrypt=yes", "TrustServerCertificate=no",
        "Connection Timeout=8", "Application Name=ScraperDashboardValidation",
    ]
    if cfg["authentication"] == "SQL Password":
        parts += ["Authentication=SqlPassword", f"UID={cfg['username']}", f"PWD={_escape_password(password)}"]
    else:
        parts += ["Authentication=ActiveDirectoryInteractive", f"UID={cfg['username']}"]
    return ";".join(parts) + ";"

def test_connection(config, password=""):
    """Only a SELECT statement; no migration or schema changes."""
    try:
        import pyodbc
    except ImportError as e:
        raise RuntimeError("Instala pyodbc") from e
    if "ODBC Driver 18 for SQL Server" not in pyodbc.drivers():
        raise RuntimeError("Microsoft ODBC Driver 18 for SQL Server no instalado")
    cfg = public_settings(config)
    if cfg["authentication"] == "Microsoft Entra Interactive" and os.name != "nt":
        raise RuntimeError("La autenticación Entra interactiva requiere Windows")
    if cfg["authentication"] == "SQL Password" and not password:
        raise ValueError("Proporciona contraseña para esta prueba")
    connection = pyodbc.connect(connection_string(cfg, password), timeout=8, autocommit=True)
    try:
        cursor = connection.cursor()
        try:
            row = cursor.execute("SELECT DB_NAME(), SUSER_SNAME()").fetchone()
            return {"database": str(row[0]), "identity": str(row[1]), "status": "CONECTADO"}
        finally:
            cursor.close()
    finally:
        connection.close()
