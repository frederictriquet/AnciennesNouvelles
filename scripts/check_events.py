import asyncio, os, sys
sys.path.insert(0, '/app')

from datetime import date, datetime, timezone, timedelta
from ancnouv.config import Config
from ancnouv.db.session import init_db, get_session
from ancnouv.db.utils import get_scheduler_state
from sqlalchemy import text

async def check():
    config = Config()
    db_path = os.environ.get('ANCNOUV_DB_PATH', '') or f'{config.data_dir}/{config.database.filename}'
    init_db(db_path)
    today = date.today()
    async with get_session() as s:
        pending = (await s.execute(text(
            'SELECT count(*) FROM posts WHERE status=\'pending_approval\''
        ))).scalar()
        print(f'posts pending_approval : {pending}')

        escalation = await get_scheduler_state(s, 'escalation_level')
        print(f'escalation_level : {escalation or 0}')

        # events disponibles selon chaque politique dedup
        n_never = (await s.execute(text(
            'SELECT count(*) FROM events WHERE month=:m AND day=:d '
            'AND status=\'available\' AND published_count=0'
        ), {'m': today.month, 'd': today.day})).scalar()
        print(f'events available published_count=0 : {n_never}')

        cutoff = datetime.now(timezone.utc) - timedelta(days=365)
        n_window = (await s.execute(text(
            'SELECT count(*) FROM events WHERE month=:m AND day=:d '
            'AND status=\'available\' '
            'AND (last_used_at IS NULL OR last_used_at < :cutoff)'
        ), {'m': today.month, 'd': today.day, 'cutoff': cutoff})).scalar()
        print(f'events available last_used_at<365j : {n_window}')

asyncio.run(check())
