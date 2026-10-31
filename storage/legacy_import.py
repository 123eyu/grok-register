"""Import account records from the legacy TXT and pending JSONL formats."""

from dataclasses import dataclass
import json
from pathlib import Path

from filelock import FileLock

from storage.database import Database


@dataclass(frozen=True)
class ImportSummary:
    total: int
    imported: int
    duplicates: int
    malformed: int
    source_size: int = 0
    source_mtime_ns: int = 0


def _attach_source(database, account_ids, source_type, path, file_format):
    database.add_artifacts(account_ids, source_type, str(path), file_format)


def _summary(path, records, malformed, inserted):
    stat = path.stat()
    imported = sum(created for _account_id, created in inserted)
    return ImportSummary(
        total=len(records) + malformed,
        imported=imported,
        duplicates=len(records) - imported,
        malformed=malformed,
        source_size=stat.st_size,
        source_mtime_ns=stat.st_mtime_ns,
    )


def import_legacy_accounts(database, path, email_provider=""):
    """Import legacy email/password/SSO records without modifying the file."""
    if not isinstance(database, Database):
        raise TypeError("database must be a Database instance")

    source = Path(path).expanduser().resolve()
    with FileLock(str(source) + ".lock", timeout=30):
        records = []
        malformed = 0
        with open(source, "r", encoding="utf-8-sig") as handle:
            for raw_line in handle:
                line = raw_line.rstrip("\r\n")
                if not line.strip():
                    continue
                parts = line.split("----", 2)
                if len(parts) != 3 or not parts[0].strip() or not parts[2].strip():
                    malformed += 1
                    continue
                records.append({
                    "email": parts[0].strip(),
                    "password": parts[1],
                    "sso": parts[2].strip(),
                    "email_provider": email_provider,
                    "registration_status": "registered",
                    "source": "legacy_txt",
                })

        inserted = database.insert_accounts(records)
        account_ids = [account_id for account_id, _created in inserted]
        _attach_source(database, account_ids, "legacy_txt", source, "txt")
        return _summary(source, records, malformed, inserted)


def import_legacy_pending(database, path, email_provider=""):
    """Import pending records for visibility while leaving recovery files intact."""
    if not isinstance(database, Database):
        raise TypeError("database must be a Database instance")

    source = Path(path).expanduser().resolve()
    with FileLock(str(source) + ".lock", timeout=30):
        records = []
        malformed = 0
        with open(source, "r", encoding="utf-8-sig") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                    if not isinstance(record, dict):
                        raise ValueError("pending record must be an object")
                    email = str(record.get("email") or "").strip()
                    sso = str(record.get("sso") or "").strip()
                    if not email or not sso:
                        raise ValueError("pending record is missing email or SSO")
                except (json.JSONDecodeError, ValueError):
                    malformed += 1
                    continue
                records.append({
                    "email": email,
                    "password": str(record.get("password") or ""),
                    "sso": sso,
                    "email_provider": email_provider,
                    "registration_status": "pending_recovery",
                    "source": "legacy_pending",
                    "profile": record.get("profile") if isinstance(record.get("profile"), dict) else {},
                    "created_at": record.get("queued_at"),
                })

        inserted = database.insert_accounts(records)
        account_ids = [account_id for account_id, _created in inserted]
        _attach_source(database, account_ids, "legacy_pending", source, "jsonl")
        return _summary(source, records, malformed, inserted)


def import_legacy_directory(database, directory, email_provider=""):
    """Import changed legacy exports and pending queues from one output directory."""
    root = Path(directory).expanduser().resolve()
    imported = []
    sources = sorted(root.glob("accounts*.txt"))
    sources.extend(sorted(root.glob("accounts*.txt.pending.jsonl")))

    for source in sources:
        source = source.resolve()
        stat = source.stat()
        fingerprint = (stat.st_size, stat.st_mtime_ns)
        if database.legacy_import_fingerprint(str(source)) == fingerprint:
            continue
        if source.name.endswith(".pending.jsonl"):
            summary = import_legacy_pending(database, source, email_provider)
        else:
            summary = import_legacy_accounts(database, source, email_provider)
        database.record_legacy_import(
            str(source), summary.source_size, summary.source_mtime_ns, summary
        )
        imported.append((str(source), summary))

    return imported
