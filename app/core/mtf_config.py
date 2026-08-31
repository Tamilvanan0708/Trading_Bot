"""
Multi-Timeframe (MTF) configuration for research.

Defines which timeframe supplies each analytical ROLE in the confluence
engine:

  htf        -> macro bias (voting weight 2, SMC premium/discount zone)
  structure  -> market structure trend (voting weight 1)
  setup      -> SMC (FVG/OB/sweeps) + Fibonacci golden-zone setup
  trigger    -> entry trigger candle confirmation
  base       -> candle walk granularity for signal generation (usually = trigger)

The PRODUCTION configuration is always ``PROD_4H_1H_30M_15M`` and is the
default everywhere (``mtf=None``).  Research configs are used ONLY by the
research harness; the production strategy is never modified.
"""

from dataclasses import dataclass

from app.core.constants import TimeFrame


@dataclass(frozen=True)
class MTFConfig:
    name: str
    htf: TimeFrame
    structure: TimeFrame
    setup: TimeFrame
    trigger: TimeFrame
    base: TimeFrame

    @property
    def label(self) -> str:
        return self.name


# Production baseline (unchanged from the current live strategy).
PROD_4H_1H_30M_15M = MTFConfig(
    name="PROD_4H_1H_30M_15M",
    htf=TimeFrame.H4,
    structure=TimeFrame.H1,
    setup=TimeFrame.M30,
    trigger=TimeFrame.M15,
    base=TimeFrame.M15,
)

# Research configurations.
MTF_4H_1H_15M_5M = MTFConfig(
    name="4H_1H_15M_5M",          # entry refinement: 5M trigger on 15M setup
    htf=TimeFrame.H4,
    structure=TimeFrame.H1,
    setup=TimeFrame.M15,
    trigger=TimeFrame.M5,
    base=TimeFrame.M5,
)

MTF_4H_1H_30M_5M = MTFConfig(
    name="4H_1H_30M_5M",          # faster structure: 5M trigger on 30M setup
    htf=TimeFrame.H4,
    structure=TimeFrame.H1,
    setup=TimeFrame.M30,
    trigger=TimeFrame.M5,
    base=TimeFrame.M5,
)

MTF_1H_30M_15M_5M = MTFConfig(
    name="1H_30M_15M_5M",         # alternative: 1H bias, 30M structure, 15M setup, 5M trigger
    htf=TimeFrame.H1,
    structure=TimeFrame.M30,
    setup=TimeFrame.M15,
    trigger=TimeFrame.M5,
    base=TimeFrame.M5,
)

ALL_MTF_CONFIGS = (
    PROD_4H_1H_30M_15M,
    MTF_4H_1H_15M_5M,
    MTF_4H_1H_30M_5M,
    MTF_1H_30M_15M_5M,
)


def resolve_mtf(mtf: MTFConfig | None) -> MTFConfig:
    """Resolves None to the production config (safe default)."""
    return mtf if mtf is not None else PROD_4H_1H_30M_15M
