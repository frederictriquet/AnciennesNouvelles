import asyncio, os, sys
sys.path.insert(0, '/app')

from datetime import date
from ancnouv.config import Config
from ancnouv.db.session import init_db, get_session
from sqlalchemy import text

async def check():
    config = Config()
    db_path = os.environ.get('ANCNOUV_DB_PATH', '') or f'{config.data_dir}/{config.database.filename}'
    init_db(db_path)
    today = date.today()
    async with get_session() as s:
        # Événements disponibles avec published_count=0
        r = await s.execute(text(
            'SELECT count(*) FROM events WHERE month=:m AND day=:d '
            'AND status=\'available\' AND published_count=0'
        ), {'m': today.month, 'd': today.day})
        print('events available + published_count=0 :', r.scalar())

        # Posts pending_approval en attente
        r2 = await s.execute(text(
            'SELECT id, caption FROM posts WHERE status=\'pending_approval\' LIMIT 5'
        ))
        rows = r2.fetchall()
        print(f'posts pending_approval : {len(rows)}')
        for row in rows:
            print(f'  post #{row.id} — {str(row.caption)[:60]}')

asyncio.run(check())
