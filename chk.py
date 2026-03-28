import sys
sys.path.insert(0, '/app')

with open('/app/app/fleet/manager.py') as f:
    code = f.read()

if 'p.upper() == vehicle_type.upper()' in code:
    print("manager.py: EXACT MATCH OK")
else:
    print("manager.py: OLD CODE!")

with open('/app/app/optimizer/optimizer.py') as f:
    code2 = f.read()

if 'not is_compat' in code2 and 'continue' in code2:
    # Check they are on adjacent lines
    lines = code2.split('\n')
    for i, line in enumerate(lines):
        if 'not is_compat' in line:
            print(f"optimizer line {i+1}: {line.strip()}")
            if i+1 < len(lines):
                print(f"optimizer line {i+2}: {lines[i+1].strip()}")

import asyncio
from app.core.config import settings
from app.db.repository import Repository
import asyncpg

async def check():
    pool = await asyncpg.create_pool(dsn=settings.dsn, min_size=1, max_size=2)
    repo = Repository(pool)
    compat = await repo.get_compatibility()
    registry = await repo.get_vehicle_registry()
    
    # Samosval skills
    vt = registry.get(90008002, 'NOT_FOUND')
    print(f"\nvehicle_type 90008002: {vt}")
    
    skills = [t for t, ps in compat.items() 
              if t != 'default' and any(p.strip() == vt for p in ps)]
    print(f"Samosval skills ({len(skills)}): {skills}")
    
    # What does is_compatible return for Samosval + SK5-3?
    # Simulate Vehicle object
    kern_task = None
    for t in compat:
        if 'kerна' in t or t.startswith('SK5') or ('5' in t and 'ерна' in t):
            kern_task = t
            print(f"Found kern task: {kern_task}")
    
    has_kern = any('ерна' in s for s in skills)
    print(f"Samosval has kern task in skills: {has_kern}")
    
    await pool.close()

asyncio.run(check())
