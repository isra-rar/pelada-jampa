from datetime import date
from decimal import Decimal

from pydantic import BaseModel


class GroupMetricsOut(BaseModel):
    """Indicadores de sucesso do grupo no período (PRD §21). Taxas de 0 a 1; nulas quando não há base."""

    group_id: int
    since: date
    until: date
    # organização
    matches: int
    cancelled_matches: int
    invited: int
    responded: int
    response_rate: float | None
    occupancy_rate: float | None
    confirmations: int
    cancellations_after_confirmation: int
    late_cancellations: int
    no_shows: int
    # avaliação
    evaluation_matches: int
    evaluations_expected: int
    evaluations_submitted: int
    evaluation_completion_rate: float | None
    avg_evaluation_hours: float | None
    # financeiro
    charges: int
    charges_paid: int
    payment_rate: float | None
    open_debts: int
    open_debts_amount: Decimal
    # sorteio
    matches_with_published_draw: int
    avg_average_difference: float | None
    avg_strength_difference: float | None
    draws_per_match: float | None
    manual_adjust_rate: float | None
    # retenção
    active_players: int
    previous_active_players: int
    retained_players: int
    retention_rate: float | None


class PlatformMetricsOut(BaseModel):
    since: date
    until: date
    users_total: int
    users_active: int
    users_new: int
    users_deleted: int
    groups_total: int
    groups_active: int
    groups_previous_active: int
    groups_retained: int
    group_retention_rate: float | None
    matches: int
    players_active: int
