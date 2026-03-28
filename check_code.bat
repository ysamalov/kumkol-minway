@echo off
echo === Проверка кода в контейнере ===

echo --- optimizer.py: фильтр совместимости ---
docker compose exec server python -c "import linecache; [print(i, linecache.getline('/app/app/optimizer/optimizer.py', i).rstrip()) for i in range(37,45)]"

echo.
echo --- manager.py: exact matching ---
docker compose exec server python -c "f=open('/app/app/fleet/manager.py'); lines=f.readlines(); [print(i+1, l.rstrip()) for i,l in enumerate(lines) if 'p.upper()' in l or 'not is_compat' in l]"

echo.
echo --- STRICT_COMPATIBILITY ---
docker compose exec server python -c "from app.core.config import settings; print('strict_compatibility =', settings.strict_compatibility)"
