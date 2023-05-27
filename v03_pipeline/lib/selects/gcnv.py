from __future__ import annotations

import datetime

import hail as hl
import pytz

from hail_scripts.computed_fields import variant_id as expression_helpers


def pos(mt: hl.MatrixTable, **_):
    return hl.agg.min(mt.sample_start)


def xpos(mt: hl.MatrixTable, **_):
    return expression_helpers.get_expr_for_xpos(
        hl.locus(expression_helpers.replace_chr_prefix(mt.chr), pos(mt)),
    )


def variant_id(mt: hl.MatrixTable, **_):
    return hl.format(
        f'%s_%s_{datetime.datetime.now(tz=pytz.timezone("US/Eastern")).date():%m%d%Y}',
        mt.variant_name,
        mt.svtype,
    )
