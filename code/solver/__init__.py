"""code/solver — Financial Decision & Optimization Solver.

Exports:
  - SafeAmountCalculator: computes amount_safe_to_pay (S)
  - FullPaymentFinder: forward 90-day scan for earliest lump-sum date (D_full)
  - InstallmentEvaluator: simulates and filters seller installment options
  - PartialPaymentBuilder: constructs 2-payment partial plans
  - SpendingChangeOptimizer: greedy search for up to 3 spending adjustments
  - PlanRanker: 6-tier lexicographical comparator
  - DecisionEngine: end-to-end evaluation orchestrator
"""

from .safe_amount_calculator import SafeAmountCalculator
from .full_payment_finder import FullPaymentFinder
from .installment_evaluator import InstallmentEvaluator
from .partial_payment_builder import PartialPaymentBuilder
from .spending_change_optimizer import SpendingChangeOptimizer
from .plan_ranker import PlanRanker
from .decision_engine import DecisionEngine

__all__ = [
    "SafeAmountCalculator",
    "FullPaymentFinder",
    "InstallmentEvaluator",
    "PartialPaymentBuilder",
    "SpendingChangeOptimizer",
    "PlanRanker",
    "DecisionEngine",
]
