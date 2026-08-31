"""
Institutional Risk Management Engine for XAU/USD.
"""


from app.config.settings import Settings, get_settings
from app.core.constants import SignalDirection
from app.risk.models import PositionSizeResult, RiskCalculationRequest


class RiskManager:
    """
    Risk Manager enforcing position sizing, maximum loss constraints,
    and contract specifications for XAU/USD (Gold).
    """

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    def calculate_position_size(self, req: RiskCalculationRequest) -> PositionSizeResult:
        """
        Calculates exact lot size and potential dollar outcomes for XAU/USD.
        Formula:
          Risk Amount ($) = Account Balance * (Risk % / 100)
          SL Distance ($) = |Entry - SL|
          Dollar Value Per Lot for Distance = SL Distance * Contract Size (100 oz)
          Lot Size = Risk Amount / (SL Distance * Contract Size)
        """
        # Validate risk limits
        if req.risk_percent > self.settings.MAX_RISK_PERCENT:
            return PositionSizeResult(
                direction=req.direction,
                lot_size=0.0,
                risk_amount_usd=0.0,
                stop_loss_distance=0.0,
                potential_loss_usd=0.0,
                potential_profit_tp1_usd=0.0,
                potential_profit_tp2_usd=0.0,
                potential_profit_tp3_usd=0.0,
                risk_reward_tp1=0.0,
                risk_reward_tp2=0.0,
                risk_reward_tp3=0.0,
                is_valid=False,
                rejection_reason=f"Risk percent ({req.risk_percent}%) exceeds max allowable limit ({self.settings.MAX_RISK_PERCENT}%).",
            )

        # Validate Direction vs SL
        if req.direction == SignalDirection.LONG:
            if req.stop_loss >= req.entry_price:
                return PositionSizeResult(
                    direction=req.direction,
                    lot_size=0.0,
                    risk_amount_usd=0.0,
                    stop_loss_distance=0.0,
                    potential_loss_usd=0.0,
                    potential_profit_tp1_usd=0.0,
                    potential_profit_tp2_usd=0.0,
                    potential_profit_tp3_usd=0.0,
                    risk_reward_tp1=0.0,
                    risk_reward_tp2=0.0,
                    risk_reward_tp3=0.0,
                    is_valid=False,
                    rejection_reason=f"Invalid Long SL: Stop loss ({req.stop_loss}) must be below Entry ({req.entry_price}).",
                )
            sl_distance = req.entry_price - req.stop_loss
            tp1_dist = max(0.0, req.take_profit_1 - req.entry_price)
            tp2_dist = max(0.0, req.take_profit_2 - req.entry_price)
            tp3_dist = max(0.0, req.take_profit_3 - req.entry_price)

        elif req.direction == SignalDirection.SHORT:
            if req.stop_loss <= req.entry_price:
                return PositionSizeResult(
                    direction=req.direction,
                    lot_size=0.0,
                    risk_amount_usd=0.0,
                    stop_loss_distance=0.0,
                    potential_loss_usd=0.0,
                    potential_profit_tp1_usd=0.0,
                    potential_profit_tp2_usd=0.0,
                    potential_profit_tp3_usd=0.0,
                    risk_reward_tp1=0.0,
                    risk_reward_tp2=0.0,
                    risk_reward_tp3=0.0,
                    is_valid=False,
                    rejection_reason=f"Invalid Short SL: Stop loss ({req.stop_loss}) must be above Entry ({req.entry_price}).",
                )
            sl_distance = req.stop_loss - req.entry_price
            tp1_dist = max(0.0, req.entry_price - req.take_profit_1)
            tp2_dist = max(0.0, req.entry_price - req.take_profit_2)
            tp3_dist = max(0.0, req.entry_price - req.take_profit_3)
        else:
            return PositionSizeResult(
                direction=req.direction,
                lot_size=0.0,
                risk_amount_usd=0.0,
                stop_loss_distance=0.0,
                potential_loss_usd=0.0,
                potential_profit_tp1_usd=0.0,
                potential_profit_tp2_usd=0.0,
                potential_profit_tp3_usd=0.0,
                risk_reward_tp1=0.0,
                risk_reward_tp2=0.0,
                risk_reward_tp3=0.0,
                is_valid=False,
                rejection_reason="No trade direction provided.",
            )

        if sl_distance <= 0.01:
            return PositionSizeResult(
                direction=req.direction,
                lot_size=0.0,
                risk_amount_usd=0.0,
                stop_loss_distance=0.0,
                potential_loss_usd=0.0,
                potential_profit_tp1_usd=0.0,
                potential_profit_tp2_usd=0.0,
                potential_profit_tp3_usd=0.0,
                risk_reward_tp1=0.0,
                risk_reward_tp2=0.0,
                risk_reward_tp3=0.0,
                is_valid=False,
                rejection_reason="Stop loss distance is too narrow.",
            )

        # Risk amount in USD
        risk_amount_usd = req.account_balance * (req.risk_percent / 100.0)

        # Raw lot calculation (1 standard lot = 100 oz)
        # 1 lot moving $1.00 in Gold = $100 profit/loss
        dollar_per_point_per_lot = req.contract_size
        loss_per_lot = sl_distance * dollar_per_point_per_lot

        raw_lots = risk_amount_usd / loss_per_lot

        # Respect broker contract constraints.  If the desired risk cannot be
        # achieved with the broker's minimum lot, REJECT — never scale risk up.
        if raw_lots < self.settings.MIN_LOT:
            return PositionSizeResult(
                direction=req.direction,
                lot_size=0.0,
                risk_amount_usd=0.0,
                stop_loss_distance=0.0,
                potential_loss_usd=0.0,
                potential_profit_tp1_usd=0.0,
                potential_profit_tp2_usd=0.0,
                potential_profit_tp3_usd=0.0,
                risk_reward_tp1=0.0,
                risk_reward_tp2=0.0,
                risk_reward_tp3=0.0,
                is_valid=False,
                rejection_reason=(
                    f"Desired risk (${risk_amount_usd:.2f}) cannot be achieved: "
                    f"computed {raw_lots:.4f} lots is below broker minimum {self.settings.MIN_LOT}."
                ),
            )

        # Floor to the broker's lot step, then clamp to max lot.
        lot_step = self.settings.LOT_STEP
        lot_size = round(int(raw_lots / lot_step + 1e-10) * lot_step, 2)
        lot_size = min(self.settings.MAX_LOT, lot_size)

        actual_loss_usd = round(lot_size * sl_distance * dollar_per_point_per_lot, 2)
        profit_tp1_usd = round(lot_size * tp1_dist * dollar_per_point_per_lot, 2)
        profit_tp2_usd = round(lot_size * tp2_dist * dollar_per_point_per_lot, 2)
        profit_tp3_usd = round(lot_size * tp3_dist * dollar_per_point_per_lot, 2)

        rr_tp1 = round(tp1_dist / sl_distance, 2) if sl_distance > 0 else 0.0
        rr_tp2 = round(tp2_dist / sl_distance, 2) if sl_distance > 0 else 0.0
        rr_tp3 = round(tp3_dist / sl_distance, 2) if sl_distance > 0 else 0.0

        return PositionSizeResult(
            symbol="XAUUSD",
            direction=req.direction,
            lot_size=lot_size,
            risk_amount_usd=round(risk_amount_usd, 2),
            stop_loss_distance=round(sl_distance, 2),
            potential_loss_usd=actual_loss_usd,
            potential_profit_tp1_usd=profit_tp1_usd,
            potential_profit_tp2_usd=profit_tp2_usd,
            potential_profit_tp3_usd=profit_tp3_usd,
            risk_reward_tp1=rr_tp1,
            risk_reward_tp2=rr_tp2,
            risk_reward_tp3=rr_tp3,
            is_valid=True,
            rejection_reason="",
        )
