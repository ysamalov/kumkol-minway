# Запустить: docker compose exec server python /tmp/debug_live.py
# Сначала скопировать файл в контейнер:
# docker compose cp debug_live.py server:/tmp/debug_live.py

import asyncio
import sys
sys.path.insert(0, '/app')

from app.core.config import settings
from app.db.repository import Repository
import asyncpg

async def main():
    pool = await asyncpg.create_pool(dsn=settings.dsn, min_size=1, max_size=2)
    repo = Repository(pool)
    
    # 1. Load compatibility from DB
    compat = await repo.get_compatibility()
    print(f"Compatibility entries: {len(compat)}")
    
    sk5 = compat.get('СК5-3 Отбор керна', [])
    print(f"СК5-3 Отбор керна patterns: {sk5}")
    print(f"'Самосвал' in patterns: {'Самосвал' in sk5}")
    
    # 2. Load vehicle registry
    registry = await repo.get_vehicle_registry()
    samosval = {k: v for k, v in registry.items() if v == 'Самосвал'}
    print(f"\nСамосвалы в реестре: {samosval}")
    
    # 3. Simulate _skills_for for Самосвал
    vehicle_type = 'Самосвал'
    skills = []
    for task_type, patterns in compat.items():
        if task_type == 'default':
            continue
        for p in patterns:
            if p.upper() == vehicle_type.upper():
                skills.append(task_type)
                break
    
    print(f"\nSkills для Самосвала ({len(skills)}):")
    for s in skills:
        print(f"  {s}")
    
    print(f"\nСК5-3 Отбор керна в skills: {'СК5-3 Отбор керна' in skills}")
    print(f"\nstrict_compatibility: {settings.strict_compatibility}")
    
    await pool.close()

asyncio.run(main())
