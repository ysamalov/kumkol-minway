"""
AI Explainer (§7.5)
===================
Генерирует человекочитаемые объяснения решений маршрутизатора
через OpenRouter API (совместим с OpenAI Chat Completions API).

Используется как бонусный слой поверх детерминированного scorer.py:
  1. scorer.py всегда возвращает быстрый шаблонный reason (fallback)
  2. explainer.py обогащает его живым текстом от LLM (если API доступен)

Если OpenRouter недоступен или ключ не задан — молча возвращает
шаблонный reason без ошибки (graceful degradation).
"""
from __future__ import annotations

import logging
from typing import Optional

import httpx

from app.core.config import settings

log = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# Модель по умолчанию — быстрая и дешёвая для текстовых пояснений
DEFAULT_MODEL = "mistralai/mistral-7b-instruct"


class AIExplainer:
    """
    Async LLM client для генерации текстовых объяснений.
    Используйте как singleton через app.state.explainer.
    """

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_MODEL,
        timeout: float = 10.0,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None

    async def start(self) -> None:
        self._client = httpx.AsyncClient(
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "HTTP-Referer": "https://uto.local",   # required by OpenRouter
                "X-Title": "IS UTO",
                "Content-Type": "application/json",
            },
            timeout=self.timeout,
        )

    async def stop(self) -> None:
        if self._client:
            await self._client.aclose()

    # ── Recommendation reason ─────────────────────────────────────────────────

    async def explain_recommendation(
        self,
        vehicle_name: str,
        distance_km: float,
        eta_minutes: float,
        wait_minutes: float,
        is_compatible: bool,
        priority: str,
        task_type: str,
        score: float,
        fallback_reason: str,
    ) -> str:
        """
        Генерирует краткое (~1-2 предложения) объяснение выбора техники
        на русском языке для диспетчера.
        """
        if not self._is_available():
            return fallback_reason

        prompt = f"""Ты — помощник диспетчера на нефтяном месторождении.
Объясни выбор техники в 1-2 предложениях на русском языке, без технических деталей.

Данные о технике:
- Название: {vehicle_name}
- Расстояние до объекта: {distance_km:.1f} км
- Расчётное время прибытия (ETA): {eta_minutes:.0f} мин
- Время ожидания (техника занята): {wait_minutes:.0f} мин
- Совместима с типом работ "{task_type}": {"да" if is_compatible else "нет"}
- Приоритет заявки: {priority}
- Итоговый скор: {score:.2f} из 1.0

Напиши только объяснение, без вступлений и без форматирования."""

        return await self._chat(prompt, fallback=fallback_reason)

    # ── Multitask grouping reason ─────────────────────────────────────────────

    async def explain_grouping(
        self,
        strategy: str,
        groups: list[list[str]],
        savings_percent: float,
        total_distance_km: float,
        baseline_distance_km: float,
        fallback_reason: str,
    ) -> str:
        """
        Генерирует объяснение стратегии группировки заявок.
        """
        if not self._is_available():
            return fallback_reason

        groups_desc = "; ".join(
            f"группа {i+1}: {', '.join(g)}" for i, g in enumerate(groups)
        )

        prompt = f"""Ты — помощник диспетчера на нефтяном месторождении.
Объясни решение о группировке заявок в 2-3 предложениях на русском языке.

Данные:
- Стратегия: {strategy}
- Группировка: {groups_desc}
- Суммарный пробег при объединении: {total_distance_km:.1f} км
- Суммарный пробег без объединения (baseline): {baseline_distance_km:.1f} км
- Экономия: {savings_percent:.1f}%

Напиши только объяснение, без вступлений и без форматирования."""

        return await self._chat(prompt, fallback=fallback_reason)

    # ── Internal ──────────────────────────────────────────────────────────────

    async def _chat(self, prompt: str, fallback: str) -> str:
        try:
            resp = await self._client.post(
                OPENROUTER_URL,
                json={
                    "model": self.model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 150,
                    "temperature": 0.4,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            content = data.get("choices", [{}])[0].get("message", {}).get("content") or ""
            text = content.strip()
            return text if text else fallback
        except Exception as exc:
            log.warning("OpenRouter unavailable, using fallback reason: %s", exc)
            return fallback

    def _is_available(self) -> bool:
        return bool(self._client and self.api_key)


# ── Factory ───────────────────────────────────────────────────────────────────

def create_explainer() -> Optional[AIExplainer]:
    """
    Создаёт AIExplainer если OPENROUTER_API_KEY задан в настройках.
    Возвращает None если ключ не задан — система работает без AI.
    """
    if not settings.openrouter_api_key:
        log.info("OPENROUTER_API_KEY not set — AI explanations disabled")
        return None
    return AIExplainer(
        api_key=settings.openrouter_api_key,
        model=settings.openrouter_model,
    )