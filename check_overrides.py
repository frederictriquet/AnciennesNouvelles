import asyncio, os
from ancnouv.config import Config
from ancnouv.db.session import init_db, get_session
from ancnouv.db.config_store import get_all_overrides
from ancnouv.config_loader import apply_dot_overrides

async def check():
    config = Config()
    db_path = os.environ.get('ANCNOUV_DB_PATH', '') or f'{config.data_dir}/{config.database.filename}'
    init_db(db_path)
    async with get_session() as s:
        overrides = await get_all_overrides(s)
    print('overrides bruts :', overrides)
    base = config.model_dump()
    print('content dans base :', base.get('content', {}).get('wikipedia_event_types'))
    merged = apply_dot_overrides(base, overrides)
    print('content apres merge :', merged.get('content', {}).get('wikipedia_event_types'))
    effective = type(config).model_validate(merged)
    print('effective.content.wikipedia_event_types :', effective.content.wikipedia_event_types)

asyncio.run(check())
