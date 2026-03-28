#!/bin/bash
echo "=== Проверка кода в контейнере ==="

echo "--- optimizer.py строки с фильтром совместимости ---"
docker compose exec server grep -n "is_compat\|not is_compat" /app/app/optimizer/optimizer.py | head -8

echo ""
echo "--- manager.py строка с exact matching ---"
docker compose exec server grep -n "p.upper() ==" /app/app/fleet/manager.py

echo ""
echo "--- STRICT_COMPATIBILITY ---"
docker compose exec server env | grep -i strict
