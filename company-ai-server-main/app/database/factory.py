"""Scripts ke liye: DATABASE_URL set ho to accounts PostgreSQL mein, warna SQLite file (--db ya DATABASE_PATH)."""
from app.core.config import settings


class OpenedAccounts:
    def __init__(self, repo, db=None):
        self.repo = repo
        self.db = db

    async def close(self):
        await self.repo.close()
        if self.db is not None:
            await self.db.close()


async def open_accounts(db_path: str | None = None, dsn: str | None = None) -> OpenedAccounts:
    dsn = settings.DATABASE_URL if dsn is None else dsn
    if dsn and not db_path:
        from app.database.pg_accounts import PgAccountsRepository
        from app.database.postgres import PostgresDatabase
        db = PostgresDatabase(dsn)
        await db.connect()
        repo = PgAccountsRepository(db, session_ttl_hours=settings.SESSION_TTL_HOURS)
        await repo.init()
        return OpenedAccounts(repo, db)
    from app.database.accounts import AccountsRepository
    repo = AccountsRepository(db_path or settings.DATABASE_PATH, session_ttl_hours=settings.SESSION_TTL_HOURS)
    await repo.init()
    return OpenedAccounts(repo)
