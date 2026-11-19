from sqlalchemy import create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from .config import database_connect_args, settings

database_url = make_url(settings.database_url)
is_sqlite = database_url.drivername.startswith("sqlite")
connect_args = database_connect_args(
    settings.database_url,
    settings.database_ssl_mode,
    settings.database_disable_prepared_statements,
)

engine_options = {"connect_args": connect_args, "pool_pre_ping": True}
if not is_sqlite:
    engine_options.update(pool_size=settings.database_pool_size, max_overflow=settings.database_max_overflow)
engine = create_engine(settings.database_url, **engine_options)
if is_sqlite:
    @event.listens_for(engine, "connect")
    def enable_sqlite_foreign_keys(connection, _record):
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
