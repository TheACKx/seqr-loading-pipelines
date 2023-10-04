from __future__ import annotations

import hail as hl
import luigi

from v03_pipeline.lib.misc.io import import_callset
from v03_pipeline.lib.misc.validation import validate_contigs, validate_sample_type
from v03_pipeline.lib.model import CachedReferenceDatasetQuery
from v03_pipeline.lib.paths import (
    imported_callset_path,
    valid_cached_reference_dataset_query_path,
)
from v03_pipeline.lib.tasks.base.base_write_task import BaseWriteTask
from v03_pipeline.lib.tasks.files import CallsetTask, GCSorLocalTarget, HailTableTask


class WriteImportedCallsetTask(BaseWriteTask):
    n_partitions = 500
    callset_path = luigi.Parameter()
    filters_path = luigi.OptionalParameter(
        default=None,
        description='Optional path to part two outputs from callset (VCF shards containing filter information)',
    )
    validate = luigi.BoolParameter(
        default=True,
        parsing=luigi.BoolParameter.EXPLICIT_PARSING,
    )

    def output(self) -> luigi.Target:
        return GCSorLocalTarget(
            imported_callset_path(
                self.reference_genome,
                self.dataset_type,
                self.callset_path,
            ),
        )

    def requires(self) -> list[luigi.Task]:
        requirements = []
        if self.filters_path:
            requirements = [
                *requirements,
                CallsetTask(self.filters_path),
            ]
        if self.validate:
            requirements = [
                *requirements,
                HailTableTask(
                    valid_cached_reference_dataset_query_path(
                        self.reference_genome,
                        CachedReferenceDatasetQuery.GNOMAD_CODING_AND_NONCODING_VARIANTS,
                    ),
                ),
            ]
        return [
            *requirements,
            CallsetTask(self.callset_path),
        ]

    def create_table(self) -> hl.MatrixTable:
        mt = import_callset(
            self.callset_path,
            self.reference_genome,
            self.dataset_type,
            self.filters_path,
        )
        if self.validate and self.dataset_type.can_run_validation:
            validate_contigs(mt, self.reference_genome)
            coding_and_noncoding_ht = hl.read_table(
                valid_cached_reference_dataset_query_path(
                    self.reference_genome,
                    CachedReferenceDatasetQuery.GNOMAD_CODING_AND_NONCODING_VARIANTS,
                ),
            )
            validate_sample_type(
                mt,
                coding_and_noncoding_ht,
                self.reference_genome,
                self.sample_type,
            )
        return mt
