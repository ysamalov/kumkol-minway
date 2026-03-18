"""
AI Analytics (§7.5 — расширение)
==================================
Три модуля предиктивного AI поверх детерминированного scorer.py:

1. TrafficPredictor   — коэффициент скорости по времени суток
2. AnomalyDetector    — обнаружение аномальных скоростей из snapshot-дельт
3. ETAPredictor       — скорректированный ETA с учётом трафика

Работают без внешних зависимостей (только stdlib + данные из БД).
Для production замените TrafficFactors на модель из исторических данных.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Optional


# ── Traffic Prediction ────────────────────────────────────────────────────────

# Часовые пояса пиковой нагрузки → коэффициент скорости [0..1]
# 1.0 = нормальная скорость, 0.6 = пробки, скорость снижена на 40%
_TRAFFIC_SCHEDULE: list[tuple[tuple[int, int], float]] = [
    ((7, 9),   0.65),   # утренний пик (07:00–09:00)
    ((12, 13), 0.80),   # обеденный перерыв
    ((17, 19), 0.60),   # вечерний пик
    ((22, 6),  0.90),   # ночь — свободнее, но осторожнее
]


def traffic_factor(hour: int) -> float:
    """
    Возвращает коэффициент скорости для заданного часа суток.
    Пример: hour=8 → 0.65 (утренний пик, едем на 35% медленнее).
    """
    for (start, end), factor in _TRAFFIC_SCHEDULE:
        if start <= end:
            if start <= hour < end:
                return factor
        else:
            # Диапазон через полночь: (22, 6) — с 22 до 06
            if hour >= start or hour < end:
                return factor
    return 1.0  # нет ограничений


def adjusted_eta(
    distance_km: float,
    base_speed_kmh: float,
    planned_hour: int = 8,
    *,
    hour: int | None = None,
) -> float:
    if hour is not None:
        planned_hour = hour
    """
    Считает ETA (минуты) с учётом трафика.

    Parameters
    ----------
    distance_km    : расстояние по графу
    base_speed_kmh : средняя скорость ТС из snapshot
    planned_hour   : час начала поездки (0-23)
    """
    if base_speed_kmh <= 0:
        base_speed_kmh = 40.0
    factor = traffic_factor(planned_hour)
    effective_speed = base_speed_kmh * factor
    return round((distance_km / effective_speed) * 60.0, 1)


# ── Anomaly Detection ─────────────────────────────────────────────────────────

# Пороги аномальной скорости (км/ч)
from app.core.config import settings as _cfg_an
SPEED_FLOOR = _cfg_an.analytics_speed_floor
SPEED_CEIL  = _cfg_an.analytics_speed_ceil


@dataclass
class AnomalyRecord:
    wialon_id: int
    vehicle_name: str
    speed_kmh: float
    anomaly_type: str   # "stalled" | "overspeed" | "teleport"
    description: str


def detect_speed_anomalies(
    speeds: dict[int, float],
    vehicle_names: Optional[dict[int, str]] = None,
) -> list[AnomalyRecord]:
    """
    Обнаруживает аномальные скорости по словарю {wialon_id: speed_kmh}.

    Returns список аномалий. Пустой список = всё в норме.
    """
    anomalies: list[AnomalyRecord] = []
    names = vehicle_names or {}

    for vid, spd in speeds.items():
        name = names.get(vid, f"vehicle_{vid}")

        if spd < SPEED_FLOOR:
            anomalies.append(AnomalyRecord(
                wialon_id=vid,
                vehicle_name=name,
                speed_kmh=spd,
                anomaly_type="stalled",
                description=(
                    f"Техника не движется (скорость {spd:.1f} км/ч). "
                    "Возможна поломка или ошибка трекера."
                ),
            ))
        elif spd > SPEED_CEIL:
            anomalies.append(AnomalyRecord(
                wialon_id=vid,
                vehicle_name=name,
                speed_kmh=spd,
                anomaly_type="overspeed" if spd < 300 else "teleport",
                description=(
                    f"Аномально высокая скорость: {spd:.0f} км/ч. "
                    + ("Превышение допустимой скорости." if spd < 300
                       else "Возможен телепорт / ошибка GPS.")
                ),
            ))

    return anomalies


def detect_position_anomalies(
    snapshots: list[dict],
) -> list[AnomalyRecord]:
    """
    Обнаруживает аномалии по парам snapshot-записей.

    snapshots: список dict с ключами wialon_id, pos_x, pos_y, pos_t, nm
    Возвращает аномалии типа teleport (расстояние > 500 км за один шаг).
    """
    anomalies: list[AnomalyRecord] = []

    # Группируем по vehicle
    by_vehicle: dict[int, list[dict]] = {}
    for s in snapshots:
        vid = s["wialon_id"]
        by_vehicle.setdefault(vid, []).append(s)

    for vid, snaps in by_vehicle.items():
        snaps_sorted = sorted(snaps, key=lambda x: x["pos_t"])
        for i in range(len(snaps_sorted) - 1):
            a, b = snaps_sorted[i], snaps_sorted[i + 1]
            dt_sec = b["pos_t"] - a["pos_t"]
            if dt_sec <= 0:
                continue

            # Приближённое расстояние (Haversine упрощённо)
            dlat = b["pos_y"] - a["pos_y"]
            dlon = (b["pos_x"] - a["pos_x"]) * math.cos(
                math.radians((a["pos_y"] + b["pos_y"]) / 2)
            )
            dist_km = 111.32 * math.sqrt(dlat ** 2 + dlon ** 2)
            speed = dist_km / (dt_sec / 3600.0)

            if dist_km > 500:
                anomalies.append(AnomalyRecord(
                    wialon_id=vid,
                    vehicle_name=a.get("nm", f"vehicle_{vid}"),
                    speed_kmh=round(speed, 1),
                    anomaly_type="teleport",
                    description=(
                        f"Перемещение на {dist_km:.0f} км за {dt_sec}с — "
                        "возможна ошибка GPS или замена трекера."
                    ),
                ))

    return anomalies


# ── ETA Predictor (facade) ────────────────────────────────────────────────────

class ETAPredictor:
    """
    Фасад для предсказания ETA с учётом трафика.

    В текущей реализации использует rule-based traffic_factor.
    Для production: заменить predict() на модель GradientBoosting,
    обученную на исторических snapshot-триплетах.
    """

    def predict(
        self,
        distance_km: float,
        base_speed_kmh: float,
        planned_hour: int,
        vehicle_type: str = "",
    ) -> float:
        """
        Предсказывает ETA в минутах.

        Parameters
        ----------
        distance_km    : расстояние по графу дорог
        base_speed_kmh : базовая скорость ТС
        planned_hour   : час отправления (0-23)
        vehicle_type   : тип техники (зарезервировано для future ML)
        """
        return adjusted_eta(distance_km, base_speed_kmh, planned_hour)

    def batch_predict(
        self,
        requests: list[dict],
    ) -> list[float]:
        """
        Пакетное предсказание ETA.

        requests: список dict с ключами distance_km, base_speed_kmh, planned_hour
        """
        return [
            self.predict(
                r["distance_km"],
                r.get("base_speed_kmh", 40.0),
                r.get("planned_hour", 8),
                r.get("vehicle_type", ""),
            )
            for r in requests
        ]
