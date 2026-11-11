"""跨源对账的口径规则。

为什么需要这一层
----------------
两个数据源报同一只标的的同一根 K 线，数值不同**不等于**有一个是错的。真实
存在三类差异，只有第三类才是数据冲突：

1. **舍入差** —— 价格的小数位精度不同带来的亚分差。不是冲突。
2. **单位口径差** —— 成交量一个存「手」、一个存「股」，差精确的 100 倍或
   10000 倍；基金份额类标的还带**份额折算**。不是冲突。
3. **真正的数据冲突** —— 成交额明显不等，或在两边口径一致的前提下价格不等。
   这才是冲突。

把这些规则写死在对账逻辑里，而不是散落在各脚本的临时判断里，有三个好处：
规则本身可单测；口径知识只有一份；新增数据源时不用重写判据。

判据的核心：成交额是锚，价格是受约束的量
------------------------------------------
A 股成交额 = 均价 × 成交量。所以：

- **两边成交额一致** ⇒ 均价一致 ⇒ 价格也必须一致。此时价格只要超出舍入
  容差，就是真实的错价，必须抓出来。
- **两边成交额因份额折算而不可比** ⇒ 均价口径本身不同 ⇒ 价格差异在相对
  容差内可以放过。

这个区分是必需的，两个真实案例正好卡在两侧：

- 600519.SH 茅台：成交额**完全一致**，收盘价却差 0.53 元 → 真实错价。
- 000170.SH「50AH优选」：成交额差 1.6%（份额折算所致），收盘价差 1.83 元
  → 同一折算的副作用，放过。

只用相对容差会漏掉茅台（0.043%）；只用绝对容差会误杀 LOF（8.32 元）。

实测依据（2026-11-10，通达信官网盘后包 vs 项目同花顺源，6,125 个可比标的）
----------------------------------------------------------------------
- 成交额 6,118 个**精确相等**（单位都是「元」，无歧义）→ 成交额是最干净的
  对照字段，也是判据的锚。
- 个股成交量 5,572 个**100% 精确相等** → 个股成交量单位两边一致，零例外。
- 指数成交量 541 个差精确 100 倍、2 个差精确 10000 倍 → 单位口径差。
- 收盘价 5,620 个精确相等；open/high **零冲突**，low/close 各只有 3 个例外。
- **全部 6,125 个标的中只有 4 个成交额相对差 >1%**：
  - 000170.SH「50AH优选」1.6%、000867.SH「港中小企」8.1%、000869.SH「HK银行」
    13.6% —— 三只都是 **LOF/ETF 基金**，成交量比值 105.4 / 108.7 / 112.4 倍，
    是**基金份额折算**（二级市场成交份额 ≠ 基金净值份额），不是数据错。
  - 399002.SZ「深成指R」成交额差 **50.4%** —— 这是**真实的数据不一致**，
    必须被判为冲突。

ETF/LOF 的处理
--------------
份额折算的倍数不是整数（105.4 / 108.7 / 112.4），无法用固定常数归一；而且
倍数会随行情漂移。因此规则的设计原则是：

- **精确整数倍** → 单位口径差，已归一，不算冲突。
- **落在 [90, 120] 窄带内的非整数倍** → 份额折算口径差，**不算冲突**，
  但**也不算一致** —— 成交额随之不可比，价格改用相对容差。
- **窄带之外** → 正常比值判据。

这个窄带把三只 LOF 放进来、把 399002.SZ「深成指R」的 65.75 倍排除在外 ——
后者是真实的成交额不一致（差 50%），必须被抓出来。

绝不把口径差当成一致（那会掩盖真问题），也绝不当成冲突（那会误杀正常数据）。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

#: 价格绝对容差：A股最小价格单位是 0.01 元，两家厂商的小数位精度不同会
#: 引入亚分差。实测通达信与项目在 6,125 个可比标的中最大舍入差 0.005 元。
PRICE_ABS_TOLERANCE = Decimal("0.011")

#: 价格相对容差：只在两边口径**不可比**（份额折算）时使用。实测三只 LOF
#: 基金的收盘价绝对差高达 8.32 元，但相对差只有 0.02%–0.15%。
PRICE_REL_TOLERANCE = Decimal("0.005")

#: 成交额相对容差。成交额两边都是「元」，无单位歧义，可以直接比相对差。
#: 实测 6,118 个精确相等、7 个落在 0.1% 以内，阈值取 1% 能容忍正常的
#: 汇总尾差而不放过真实的金额错误。
AMOUNT_REL_TOLERANCE = Decimal("0.01")

#: 成交量相对容差（单位归一之后）。归一后仍差超过 1% 视为真实不一致。
VOLUME_REL_TOLERANCE = Decimal("0.01")

#: 判定为「单位口径差」的精确倍数候选。通达信指数成交量是「手」、项目存
#: 「股」，换算倍数随指数而变（实测 100 与 10000 都出现过），所以这里按
#: 候选集逐一尝试，而不是写死一个常数。
VOLUME_UNIT_RATIOS: tuple[Decimal, ...] = (
    Decimal("100"),
    Decimal("10000"),
)

#: 成交量比值落在该窄带内时，视为**份额折算**口径差而非数据冲突。
#:
#: 基金份额类标的（LOF / ETF）的二级市场成交量与基金净值份额并非 1:1 ——
#: 实测 000170.SH「50AH优选」为 105.4 倍、000867.SH「港中小企」为 108.7 倍、
#: 000869.SH「HK银行」为 112.4 倍，都不是整数倍，无法用固定常数归一。
#:
#: 窄带取 [90, 120]：它覆盖了全部实测的份额折算案例，同时把
#: 399002.SZ「深成指R」的 65.75 倍排除在外 —— 那一只是真实的成交额不一致
#: （差 50%），必须被判为冲突。
VOLUME_SHARE_FOLD_BAND: tuple[Decimal, Decimal] = (Decimal("90"), Decimal("120"))


@dataclass(frozen=True)
class FieldComparison:
    """单个字段的跨源比较结果。"""

    #: 归一后的相对差（``None`` 表示无法比较或一方缺失）
    rel_diff: Decimal | None
    #: 原始相对差（未做单位归一）
    raw_rel_diff: Decimal | None
    #: 是否判定为冲突
    is_conflict: bool
    #: 是否判定为单位口径差（已归一，非冲突）
    is_unit_adjusted: bool = False
    #: 说明文字
    note: str | None = None


@dataclass(frozen=True)
class OhlcComparison:
    """四个价格字段的整体比较结果。"""

    #: 逐字段的最大绝对差（``None`` 表示无可比字段）
    max_abs_diff: Decimal | None
    #: 有差异的字段名
    differing_fields: tuple[str, ...]
    #: 是否判定为冲突
    is_conflict: bool
    #: 说明文字
    note: str | None = None


def _rel_diff(left: Decimal | None, right: Decimal | None) -> Decimal | None:
    if left is None or right is None:
        return None
    if right == 0:
        return None
    return abs(left - right) / abs(right)


def compare_close(
    left: Decimal | None,
    right: Decimal | None,
    *,
    comparable: bool = True,
) -> FieldComparison:
    """比较收盘价。

    ``comparable=True``（两边口径一致）时只允许**绝对**舍入容差：此时成交额
    已经证明两边均价一致，价格再差就只能��真实的错价。

    ``comparable=False``（两边因份额折算而口径不同）时改用**相对**容差：
    均价口径都不同，绝对差没有可比性。
    """
    if left is None or right is None:
        return FieldComparison(rel_diff=None, raw_rel_diff=None, is_conflict=False)
    abs_diff = abs(left - right)
    rel = _rel_diff(left, right)

    if comparable:
        if abs_diff <= PRICE_ABS_TOLERANCE:
            return FieldComparison(rel_diff=rel, raw_rel_diff=rel, is_conflict=False)
        rel_text = f"{rel:.6%}" if rel is not None else "n/a"
        return FieldComparison(
            rel_diff=rel,
            raw_rel_diff=rel,
            is_conflict=True,
            note=f"close differs by {abs_diff} ({rel_text})",
        )

    if rel is not None and rel <= PRICE_REL_TOLERANCE:
        return FieldComparison(rel_diff=rel, raw_rel_diff=rel, is_conflict=False)
    rel_text = f"{rel:.6%}" if rel is not None else "n/a"
    return FieldComparison(
        rel_diff=rel,
        raw_rel_diff=rel,
        is_conflict=True,
        note=f"close differs by {abs_diff} ({rel_text})",
    )


def compare_ohlc(
    left_prices: dict[str, Decimal | None],
    right_prices: dict[str, Decimal | None],
    *,
    comparable: bool = True,
) -> OhlcComparison:
    """比较四个价格字段的整体一致性。

    单独看收盘价会漏掉一种真实错误：某天开盘价被写错，但收盘价恰好正确
    （例如复权因子只错了一天）。所以 OHLC 四个字段都要过一遍容差检查 ——
    实测通达信与项目在 open/high 上零冲突，low/close 各只有 3 个 ETF/LOF 例外。
    """
    differing: list[str] = []
    max_diff: Decimal | None = None
    for field in ("open", "high", "low", "close"):
        left = left_prices.get(field)
        right = right_prices.get(field)
        if left is None or right is None:
            continue
        diff = abs(left - right)
        if max_diff is None or diff > max_diff:
            max_diff = diff
        if comparable:
            is_conflict = diff > PRICE_ABS_TOLERANCE
        else:
            rel = _rel_diff(left, right)
            is_conflict = rel is None or rel > PRICE_REL_TOLERANCE
        if is_conflict:
            differing.append(field)

    if not differing:
        return OhlcComparison(
            max_abs_diff=max_diff,
            differing_fields=(),
            is_conflict=False,
        )
    return OhlcComparison(
        max_abs_diff=max_diff,
        differing_fields=tuple(differing),
        is_conflict=True,
        note=(
            f"price fields differ beyond tolerance: {', '.join(differing)}"
        ),
    )


def compare_amount(
    left: Decimal | None,
    right: Decimal | None,
    *,
    comparable: bool = True,
) -> FieldComparison:
    """比较成交额：两边都是「元」，直接比相对差，无单位歧义。

    ``comparable=False`` 时只记录不判冲突 —— 用于成交量已被判为份额折算口径
    差、成交额同样带着折算的情形。
    """
    rel = _rel_diff(left, right)
    if rel is None:
        return FieldComparison(rel_diff=None, raw_rel_diff=None, is_conflict=False)
    if not comparable:
        return FieldComparison(
            rel_diff=rel,
            raw_rel_diff=rel,
            is_conflict=False,
            note=f"amount not comparable (volume unit differs); rel diff {rel:.6%}",
        )
    if rel <= AMOUNT_REL_TOLERANCE:
        return FieldComparison(rel_diff=rel, raw_rel_diff=rel, is_conflict=False)
    return FieldComparison(
        rel_diff=rel,
        raw_rel_diff=rel,
        is_conflict=True,
        note=f"amount differs by {rel:.6%} (tolerance {AMOUNT_REL_TOLERANCE})",
    )


def compare_volume(
    left: Decimal | None,
    right: Decimal | None,
) -> FieldComparison:
    """比较成交量：先尝试单位归一，再比相对差。

    归一只在**能证明**是单位差时发生 —— 两边之比精确等于候选倍数之一。
    归一后仍不等就是冲突；无法归一的差异（例如 ETF/LOF 的换算既不是 100
    也不是 10000）判为不可比，不算冲突也不算一致。
    """
    rel = _rel_diff(left, right)
    if left is None or right is None or rel is None:
        return FieldComparison(rel_diff=None, raw_rel_diff=None, is_conflict=False)

    if left == right:
        return FieldComparison(rel_diff=Decimal("0"), raw_rel_diff=Decimal("0"), is_conflict=False)

    for ratio in VOLUME_UNIT_RATIOS:
        if right != 0 and left == right * ratio:
            return FieldComparison(
                rel_diff=Decimal("0"),
                raw_rel_diff=rel,
                is_conflict=False,
                is_unit_adjusted=True,
                note=f"volume unit differs by x{ratio}",
            )
        if left != 0 and right == left * ratio:
            return FieldComparison(
                rel_diff=Decimal("0"),
                raw_rel_diff=rel,
                is_conflict=False,
                is_unit_adjusted=True,
                note=f"volume unit differs by x{ratio}",
            )

    # 基金份额折算：比值落在窄带内说明两边用的是不同的份额口径，而非数据
    # 错误。这类差异**不能**归一成「一致」——归一会掩盖真实问题——但也不该
    # 当成冲突。所以标成口径差，交由成交额与收盘价继续判据。
    ratio = left / right
    low, high = VOLUME_SHARE_FOLD_BAND
    if low <= ratio <= high:
        return FieldComparison(
            rel_diff=rel,
            raw_rel_diff=rel,
            is_conflict=False,
            is_unit_adjusted=True,
            note=f"volume share-fold unit differs by x{ratio:.4f}",
        )

    if rel <= VOLUME_REL_TOLERANCE:
        return FieldComparison(rel_diff=rel, raw_rel_diff=rel, is_conflict=False)
    return FieldComparison(
        rel_diff=rel,
        raw_rel_diff=rel,
        is_conflict=True,
        note=f"volume differs by {rel:.6%} after unit normalisation",
    )


def compare_daily_bar_fields(
    left_close: Decimal | None,
    right_close: Decimal | None,
    left_volume: Decimal | None,
    right_volume: Decimal | None,
    left_amount: Decimal | None,
    right_amount: Decimal | None,
) -> dict[str, FieldComparison]:
    """一次比较日线的三个核心字段。

    判据之间有依赖关系，顺序不能颠倒：

    1. **成交量**先比 —— 它决定了两边的口径是否可比（单位差 / 份额折算）。
    2. **成交额**次之 —— 它是锚：成交额一致则均价一致，两边口径可比。
    3. **收盘价**最后 —— 口径可比时只允许绝对舍入容差；不可比时（份额折算）
       改用相对容差。

    实测 2026-11-10：6,124 个标的判为一致/口径差，只有 399002.SZ「深成指R」
    因成交额差 50.4% 被判为真实冲突。
    """
    volume_cmp = compare_volume(left_volume, right_volume)
    # 成交量被判定为口径差时，成交额继承了同样的折算，不可直接比。
    amount_comparable = not volume_cmp.is_unit_adjusted
    amount_cmp = compare_amount(
        left_amount, right_amount, comparable=amount_comparable
    )

    # 成交额一致 ⇒ 均价一致 ⇒ 价格必须一致（只允许舍入容差）。
    # 成交额不可比（份额折算）⇒ 价格改用相对容差。
    # 成交额本身冲突 ⇒ 价格无意义可比，按相对容差处理，让成交额的冲突
    # 成为唯一且充分的冲突理由。
    price_comparable = amount_comparable and not amount_cmp.is_conflict
    close_cmp = compare_close(
        left_close, right_close, comparable=price_comparable
    )
    return {
        "close": close_cmp,
        "volume": volume_cmp,
        "amount": amount_cmp,
    }
