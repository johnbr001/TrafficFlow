"""Small SQLite audit ledger for versioned city data and SUMO experiments."""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATABASE_PATH = PROJECT_ROOT / "data" / "cities" / "city_catalog.sqlite"


def _connect(path=None):
    database = Path(path) if path else DATABASE_PATH
    database.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(database))
    connection.row_factory = sqlite3.Row
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS city_data_versions (
            id INTEGER PRIMARY KEY, city TEXT NOT NULL, region_code TEXT NOT NULL,
            retrieved_at_utc TEXT NOT NULL, version_hash TEXT NOT NULL, manifest_json TEXT NOT NULL,
            UNIQUE(city, version_hash)
        );
        CREATE TABLE IF NOT EXISTS experiments (
            run_id TEXT PRIMARY KEY, city TEXT NOT NULL, experiment_type TEXT NOT NULL,
            created_at_utc TEXT NOT NULL, scenario_json TEXT NOT NULL, summary_path TEXT NOT NULL,
            summary_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS rl_models (
            model_path TEXT PRIMARY KEY, city TEXT NOT NULL, trained_at_utc TEXT NOT NULL,
            metadata_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS signal_recommendations (
            id INTEGER PRIMARY KEY, run_id TEXT NOT NULL, signal_id TEXT NOT NULL,
            metrics_json TEXT NOT NULL,
            FOREIGN KEY(run_id) REFERENCES experiments(run_id)
        );
    """)
    return connection


def record_city_data_version(manifest, database_path=None):
    hashes = manifest.get("sha256", {})
    version = json.dumps(hashes, sort_keys=True, separators=(",", ":"))
    import hashlib
    version_hash = hashlib.sha256(version.encode("utf-8")).hexdigest()
    with _connect(database_path) as connection:
        connection.execute("INSERT OR IGNORE INTO city_data_versions "
                           "(city, region_code, retrieved_at_utc, version_hash, manifest_json) "
                           "VALUES (?, ?, ?, ?, ?)",
                           (manifest.get("city", ""), manifest.get("region_code", ""),
                            manifest.get("api_retrieved_at_utc") or datetime.now(timezone.utc).isoformat(),
                            version_hash, json.dumps(manifest, ensure_ascii=False)))
    return version_hash


def record_experiment(run_id, city, experiment_type, scenario, summary, summary_path,
                      signal_rows=None, database_path=None):
    created = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    with _connect(database_path) as connection:
        connection.execute("INSERT OR REPLACE INTO experiments "
                           "(run_id, city, experiment_type, created_at_utc, scenario_json, summary_path, summary_json) "
                           "VALUES (?, ?, ?, ?, ?, ?, ?)",
                           (str(run_id), str(city), str(experiment_type), created,
                            json.dumps(scenario or {}, ensure_ascii=False), str(summary_path),
                            json.dumps(summary or {}, ensure_ascii=False)))
        connection.execute("DELETE FROM signal_recommendations WHERE run_id = ?", (str(run_id),))
        for signal_id, metrics in signal_rows or []:
            connection.execute("INSERT INTO signal_recommendations (run_id, signal_id, metrics_json) VALUES (?, ?, ?)",
                               (str(run_id), str(signal_id), json.dumps(metrics, ensure_ascii=False)))


def record_rl_model(model_path, city, metadata, database_path=None):
    with _connect(database_path) as connection:
        connection.execute("INSERT OR REPLACE INTO rl_models (model_path, city, trained_at_utc, metadata_json) "
                           "VALUES (?, ?, ?, ?)",
                           (str(Path(model_path).resolve()), str(city),
                            datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
                            json.dumps(metadata, ensure_ascii=False)))
