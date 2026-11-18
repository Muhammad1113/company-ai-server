"""
Regulatory service: (1) versioned documents (EU/UK/AU/... manual upload + archive), (2) openFDA sync/approval,
(3) eCFR (21 CFR) sync/approval aur (4) law-text sources: UK legislation.gov.uk ("uk") + EUR-Lex ("eu"), wohi approval flow.

Versioned document: ek doc_key (jaise "eu-mdr-2017-745") ka sirf EK version RAG index mein (active). Purane versions
archive folder mein (asli file + nikala hua text) aur DB mein 'archived'. Restore karne par woh dobara active.
"""
import asyncio
import logging
import os
import re
import shutil
import time
from pathlib import Path, PurePath

from app.core import tenancy
from app.rag.extractors import extract_text
from app.services import ecfr, lawsync, openfda

logger = logging.getLogger("company_ai.regulatory")

KEY_RE = re.compile(r"^[A-Za-z0-9][\w.\-]{0,99}$")
VERSION_RE = re.compile(r"^[A-Za-z0-9][\w.\-]{0,39}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class RegulatoryError(ValueError):
    pass


def _safe_filename(name: str) -> str:
    base = PurePath(name.replace("\\", "/")).name
    base = re.sub(r"[^\w.\-]", "_", base)[:120]
    return base or "document"


class RegulatoryService:

    def __init__(self, repo, rag, archive_dir: str, client: "openfda.OpenFDAClient", *, max_codes: int = 30,
                 max_pages: int = 500, max_chars: int = 500000, ecfr_client: "ecfr.ECFRClient | None" = None,
                 max_parts: int = 12, law_sources: "list[lawsync.LawSource] | None" = None):
        self.repo = repo
        self.law_sources = {ls.name: ls for ls in (law_sources or [])}
        self._law_locks: dict[str, asyncio.Lock] = {}
        self.ecfr_client = ecfr_client
        self.max_parts = max_parts
        self._ecfr_lock = asyncio.Lock()
        self.rag = rag
        self.archive = Path(archive_dir)
        self.client = client
        self.max_codes = max_codes
        self.max_pages = max_pages
        self.max_chars = max_chars
        self._sync_lock = asyncio.Lock()

    # ------------------------------------------------------------------ versioned documents
    def _dir(self, collection: str, doc_key: str, version: str) -> Path:
        d = (self.archive / collection / doc_key / version).resolve()
        if self.archive.resolve() not in d.parents:          # path escape ka koi rasta nahi
            raise RegulatoryError("invalid path")
        return d

    @staticmethod
    def _header(doc_key, version, effective_date, source_url) -> str:
        parts = [f"Regulatory document {doc_key}, version {version}"]
        if effective_date:
            parts.append(f"effective {effective_date}")
        if source_url:
            parts.append(f"source {source_url}")
        return "[" + ", ".join(parts) + "]"

    async def _index(self, collection, doc_key, version, effective_date, source_url, text, meta) -> int:
        body = self._header(doc_key, version, effective_date, source_url) + "\n\n" + text
        result = await self.rag.ingest(doc_key, body, collection, {**meta, "version": version})
        return result["chunks"]

    async def upload_version(self, *, collection, doc_key, version, filename, data: bytes, effective_date=None,
                             source_url=None, note=None, by=None, make_active=True) -> dict:
        if not tenancy.is_shared(collection):
            raise RegulatoryError("versioned documents live only in shared regulatory collections (reg__...)")
        if not KEY_RE.match(doc_key or ""):
            raise RegulatoryError("document key: letters, digits, . _ - (max 100)")
        if not VERSION_RE.match(version or ""):
            raise RegulatoryError("version label: letters, digits, . _ - (max 40)")
        if effective_date and not DATE_RE.match(effective_date):
            raise RegulatoryError("effective date must look like 2026-05-01")
        if source_url and not re.match(r"^https?://", source_url):
            raise RegulatoryError("source link must start with http:// or https://")
        if await self.repo.get_version(collection, doc_key, version):
            raise RegulatoryError("this version already exists for this document; use a new version label")

        fname = _safe_filename(filename)
        text, meta = await asyncio.to_thread(extract_text, fname, data, max_pages=self.max_pages, max_chars=self.max_chars)

        folder = self._dir(collection, doc_key, version)
        await asyncio.to_thread(folder.mkdir, parents=True, exist_ok=True)
        try:
            await asyncio.to_thread((folder / fname).write_bytes, data)
            await asyncio.to_thread((folder / "extracted.txt").write_text, text, "utf-8")

            previous = await self.repo.get_active(collection, doc_key) if make_active else None
            chunks = 0
            if make_active:
                chunks = await self._index(collection, doc_key, version, effective_date, source_url, text, meta)
            vid = await self.repo.add_version(
                collection=collection, doc_key=doc_key, version=version, effective_date=effective_date,
                source_url=source_url, note=(note or None), status="active" if make_active else "archived",
                filename=fname, archive_dir=str(folder.relative_to(self.archive.resolve())), chars=len(text),
                chunks=chunks, uploaded_by=by)
        except Exception:
            await asyncio.to_thread(shutil.rmtree, folder, True)      # adhura archive nahi chhodna
            raise
        if previous:
            await self.repo.set_status(previous["id"], "archived")
        return await self.repo.get_by_id(vid)

    async def restore(self, version_id: int, by: str | None = None) -> dict:
        target = await self.repo.get_by_id(version_id)
        if not target:
            raise LookupError("version not found")
        if target["status"] == "active":
            raise RegulatoryError("this version is already active")
        folder = self._dir(target["collection"], target["doc_key"], target["version"])
        text_file = folder / "extracted.txt"
        if not text_file.is_file():
            raise RegulatoryError("archive text for this version is missing")
        text = await asyncio.to_thread(text_file.read_text, "utf-8")
        previous = await self.repo.get_active(target["collection"], target["doc_key"])
        chunks = await self._index(target["collection"], target["doc_key"], target["version"],
                                   target["effective_date"], target["source_url"], text, {"type": "restored"})
        if previous:
            await self.repo.set_status(previous["id"], "archived")
        await self.repo.set_status(version_id, "active")
        logger.info("regulatory restore: %s/%s -> %s by %s", target["collection"], target["doc_key"], target["version"], by)
        updated = await self.repo.get_by_id(version_id)
        updated["chunks"] = chunks
        return updated

    async def version_text(self, version_id: int) -> dict:
        """Admin "View": kisi bhi version (archived/retired samet) ka nikala hua text."""
        v = await self.repo.get_by_id(version_id)
        if not v:
            raise LookupError("version not found")
        f = self._dir(v["collection"], v["doc_key"], v["version"]) / "extracted.txt"
        if not f.is_file():
            raise RegulatoryError("archive text for this version is missing")
        return {"version": v, "text": await asyncio.to_thread(f.read_text, "utf-8")}

    async def retire(self, version_id: int) -> dict:
        """Active version ko index se hata do (archive mein rehta hai). Naya version aane tak is doc ka jawab nahi."""
        target = await self.repo.get_by_id(version_id)
        if not target:
            raise LookupError("version not found")
        if target["status"] != "active":
            raise RegulatoryError("only the active version can be retired")
        await self.rag.delete(target["doc_key"], target["collection"])
        await self.repo.set_status(version_id, "retired")
        return await self.repo.get_by_id(version_id)

    # ------------------------------------------------------------------ openFDA
    async def openfda_status(self) -> dict:
        return {
            "codes": await self.repo.list_codes(),
            "pending_count": await self.repo.count_pending(),
            "last_run": await self.repo.get_state("last_run"),
            "api_key_set": bool(self.client.api_key),
            "collection": openfda.FDA_COLLECTION,
        }

    async def add_code(self, code: str, label: str | None):
        code = (code or "").strip().upper()
        if not openfda.CODE_RE.match(code):
            raise RegulatoryError("FDA product code is 3 letters/digits, for example GAD")
        codes = await self.repo.list_codes()
        if len(codes) >= self.max_codes and code not in {c["code"] for c in codes}:
            raise RegulatoryError(f"at most {self.max_codes} product codes can be tracked")
        await self.repo.add_code(code, (label or "").strip()[:120] or None)

    async def run_sync(self) -> dict:
        """FDA se latest laao, farq nikaalo, pending banao. RAG ko haath nahi lagata."""
        if self._sync_lock.locked():
            raise RegulatoryError("a check is already running")
        async with self._sync_lock:
            codes = [c["code"] for c in await self.repo.list_codes()]
            if not codes:
                raise RegulatoryError("add at least one FDA product code first")
            changes, errors = await openfda.build_changes(self.client, self.repo, codes)
            pending_id = None
            if changes:
                pending_id = await self.repo.create_pending(openfda.summarize(changes), changes)
            result = {"checked_codes": len(codes), "changes": len(changes), "errors": errors,
                      "pending_id": pending_id, "at": time.time()}
            await self.repo.set_state("last_run", result)
            return result

    async def approve(self, pending_id: int, by: str) -> dict:
        pending = await self.repo.get_pending(pending_id)
        if not pending:
            raise LookupError("update not found")
        if pending["status"] != "pending":
            raise RegulatoryError(f"this update is already {pending['status']}")
        changes = pending["payload"]
        if pending.get("source") == "ecfr":
            return await self._approve_ecfr(pending_id, pending, by)
        if pending.get("source") in self.law_sources:
            return await self._approve_law(pending_id, pending, by)
        await self.repo.upsert_snapshot(changes)
        snapshot = await self.repo.snapshot()
        touched = {(c["kind"], c["code"]) for c in changes}
        ingested = []
        for kind, code in sorted(touched):
            records = [r for r in snapshot.values() if r["kind"] == kind and r["code"] == code]
            text = openfda.render(kind, code, records)
            await self.rag.ingest(openfda.doc_id_for(kind, code), text, openfda.FDA_COLLECTION,
                                  {"type": "openfda", "source": "api.fda.gov"})
            ingested.append(openfda.doc_id_for(kind, code))
        if not await self.repo.decide(pending_id, "approved", by):
            raise RegulatoryError("this update was decided by someone else")
        return {"approved": len(changes), "documents": ingested}

    async def reject(self, pending_id: int, by: str) -> dict:
        pending = await self.repo.get_pending(pending_id)
        if not pending:
            raise LookupError("update not found")
        if pending["status"] != "pending":
            raise RegulatoryError(f"this update is already {pending['status']}")
        await self.repo.add_ignored([(c["key"], c["hash"]) for c in pending["payload"]])
        if not await self.repo.decide(pending_id, "rejected", by):
            raise RegulatoryError("this update was decided by someone else")
        return {"rejected": len(pending["payload"])}

    # ------------------------------------------------------------------ eCFR (21 CFR)
    def _need_ecfr(self):
        if self.ecfr_client is None:
            raise RegulatoryError("eCFR sync is not configured on this server")

    async def ecfr_status(self) -> dict:
        self._need_ecfr()
        if not await self.repo.get_state("ecfr_seeded"):          # pehli baar: 820/801/807/878 khud add
            for part, label in ecfr.DEFAULT_PARTS:
                await self.repo.add_part(part, label)
            await self.repo.set_state("ecfr_seeded", True)
        return {
            "parts": await self.repo.list_parts(),
            "title": self.ecfr_client.title,
            "pending_count": await self.repo.count_pending(),
            "last_run": await self.repo.get_state("ecfr_last_run"),
            "collection": ecfr.COLLECTION,
        }

    async def add_part(self, part: str, label: str | None):
        self._need_ecfr()
        part = (part or "").strip()
        if not ecfr.PART_RE.match(part):
            raise RegulatoryError("a CFR part is a number, for example 820")
        parts = await self.repo.list_parts()
        if len(parts) >= self.max_parts and part not in {p["part"] for p in parts}:
            raise RegulatoryError(f"at most {self.max_parts} CFR parts can be tracked")
        await self.repo.add_part(part, (label or "").strip()[:120] or None)

    async def run_ecfr_sync(self) -> dict:
        """eCFR se latest laao, section-wise farq nikaalo, pending banao. RAG ko haath nahi lagata."""
        self._need_ecfr()
        if self._ecfr_lock.locked():
            raise RegulatoryError("an eCFR check is already running")
        async with self._ecfr_lock:
            parts = [p["part"] for p in await self.repo.list_parts()]
            if not parts:
                raise RegulatoryError("add at least one CFR part first, for example 820")
            changes, errors, as_of = await ecfr.build_changes(self.ecfr_client, self.repo, parts)
            pending_id = None
            if changes:
                pending_id = await self.repo.create_pending(ecfr.summarize(changes, as_of), changes, source="ecfr")
            result = {"checked_parts": len(parts), "changes": len(changes), "errors": errors,
                      "pending_id": pending_id, "as_of": as_of, "at": time.time()}
            await self.repo.set_state("ecfr_last_run", result)
            return result

    async def _approve_ecfr(self, pending_id: int, pending: dict, by: str) -> dict:
        changes = pending["payload"]
        as_of = (pending.get("summary") or {}).get("as_of")
        await self.repo.upsert_snapshot([c for c in changes if c["change"] != "removed"])
        removed = [c["key"] for c in changes if c["change"] == "removed"]
        if removed:
            await self.repo.delete_snapshot(removed)
        snapshot = await self.repo.snapshot()
        labels = {p["part"]: p["label"] for p in await self.repo.list_parts()}
        ingested = []
        for part in sorted({c["code"] for c in changes}):
            records = [r for r in snapshot.values() if r["kind"] == ecfr.KIND and r["code"] == part]
            doc_id = ecfr.doc_id_for(part)
            if records:
                await self.rag.ingest(doc_id, ecfr.render(part, labels.get(part), records, as_of), ecfr.COLLECTION,
                                      {"type": "ecfr", "source": "ecfr.gov"})
            else:
                await self.rag.delete(doc_id, ecfr.COLLECTION)
            ingested.append(doc_id)
        if not await self.repo.decide(pending_id, "approved", by):
            raise RegulatoryError("this update was decided by someone else")
        return {"approved": len(changes), "documents": ingested}

    # ------------------------------------------------------------------ law-text sources (UK legislation.gov.uk, EUR-Lex)
    def _law(self, source: str) -> "lawsync.LawSource":
        ls = self.law_sources.get(source)
        if ls is None:
            raise RegulatoryError(f"unknown or unconfigured law source '{source}'")
        return ls

    async def law_status(self, source: str) -> dict:
        ls = self._law(source)
        if not await self.repo.get_state(f"{source}_seeded"):          # pehli baar: default act(s) khud add
            for code, label in ls.module.DEFAULT_ITEMS:
                await self.repo.add_tracked(source, code, label)
            await self.repo.set_state(f"{source}_seeded", True)
        return {
            "source": source,
            "items": await self.repo.list_tracked(source),
            "pending_count": await self.repo.count_pending(),
            "last_run": await self.repo.get_state(f"{source}_last_run"),
            "collection": ls.module.COLLECTION,
            "max_items": ls.max_items,
        }

    async def add_law_item(self, source: str, code: str, label: str | None) -> str:
        ls = self._law(source)
        try:
            code = ls.module.normalize_code(code)
        except lawsync.LawSourceError as exc:
            raise RegulatoryError(str(exc))
        items = await self.repo.list_tracked(source)
        if len(items) >= ls.max_items and code not in {i["code"] for i in items}:
            raise RegulatoryError(f"at most {ls.max_items} items can be tracked for this source")
        await self.repo.add_tracked(source, code, (label or "").strip()[:120] or None)
        return code

    async def remove_law_item(self, source: str, code: str) -> bool:
        ls = self._law(source)
        try:
            code = ls.module.normalize_code(code)
        except lawsync.LawSourceError:
            return False
        return await self.repo.remove_tracked(source, code)

    async def run_law_sync(self, source: str) -> dict:
        """Source se latest laao, section-wise farq nikaalo, pending banao. RAG ko haath nahi lagata."""
        ls = self._law(source)
        lock = self._law_locks.setdefault(source, asyncio.Lock())
        if lock.locked():
            raise RegulatoryError("a check is already running for this source")
        async with lock:
            items = await self.repo.list_tracked(source)
            if not items:
                raise RegulatoryError("add at least one item to track first")
            names = {i["code"]: (i["label"] or ls.module.item_name(i["code"])) for i in items}
            changes, errors, metas = await lawsync.build_changes(
                ls.client.fetch_act, ls.module.KIND, self.repo, [i["code"] for i in items], delay=ls.client.delay)
            pending_id = None
            if changes:
                pending_id = await self.repo.create_pending(lawsync.summarize(changes, metas, names), changes, source=source)
            result = {"checked_items": len(items), "changes": len(changes), "errors": errors,
                      "pending_id": pending_id, "versions": {c: m.get("as_of") for c, m in metas.items()}, "at": time.time()}
            await self.repo.set_state(f"{source}_last_run", result)
            return result

    async def _approve_law(self, pending_id: int, pending: dict, by: str) -> dict:
        source = pending["source"]
        ls = self._law(source)
        changes = pending["payload"]
        metas = (pending.get("summary") or {}).get("meta") or {}
        await self.repo.upsert_snapshot([c for c in changes if c["change"] != "removed"])
        removed = [c["key"] for c in changes if c["change"] == "removed"]
        if removed:
            await self.repo.delete_snapshot(removed)
        snapshot = await self.repo.snapshot()
        labels = {i["code"]: i["label"] for i in await self.repo.list_tracked(source)}
        ingested = []
        for code in sorted({c["code"] for c in changes}):
            records = [r for r in snapshot.values() if r["kind"] == ls.module.KIND and r["code"] == code]
            doc_id = ls.module.doc_id_for(code)
            if records:
                await self.rag.ingest(doc_id, ls.module.render(code, labels.get(code), records, metas.get(code)),
                                      ls.module.COLLECTION, {"type": source, "source": ls.module.SOURCE_HOST})
            else:
                await self.rag.delete(doc_id, ls.module.COLLECTION)
            ingested.append(doc_id)
        if not await self.repo.decide(pending_id, "approved", by):
            raise RegulatoryError("this update was decided by someone else")
        return {"approved": len(changes), "documents": ingested}

    async def auto_loop(self, hours: float):
        """Optional background check. Sirf pending banata hai; approval hamesha admin ka."""
        while True:
            await asyncio.sleep(hours * 3600)
            try:
                if await self.repo.list_codes():
                    await self.run_sync()
            except Exception:
                logger.exception("openFDA auto check failed")
            try:
                if self.ecfr_client is not None and await self.repo.list_parts():
                    await self.run_ecfr_sync()
            except Exception:
                logger.exception("eCFR auto check failed")
            for name in self.law_sources:
                try:
                    if await self.repo.list_tracked(name):
                        await self.run_law_sync(name)
                except Exception:
                    logger.exception("%s auto check failed", name)
