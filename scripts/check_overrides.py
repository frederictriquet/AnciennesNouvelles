import asyncio, os, sys
sys.path.insert(0, '/app')

from ancnouv.config import Config
from ancnouv.db.session import init_db, get_session
from ancnouv.db.config_store import get_all_overrides
from ancnouv.config_loader import apply_dot_overrides_config

async def check():
    config = Config()
    db_path = os.environ.get('ANCNOUV_DB_PATH', '') or f'{config.data_dir}/{config.database.filename}'
    init_db(db_path)
    async with get_session() as s:
        overrides = await get_all_overrides(s)
    print('overrides en DB :', overrides)
    effective = apply_dot_overrides_config(config, overrides)
    print('wikipedia_event_types effectif :', effective.content.wikipedia_event_types)
    print('rss.enabled effectif :', effective.content.rss.enabled)

asyncio.run(check())
