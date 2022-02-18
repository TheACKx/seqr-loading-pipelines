import logging
import sys
import pkg_resources
import pprint

import luigi
import hail as hl

from lib.hail_tasks import HailMatrixTableTask, HailElasticSearchTask, GCSorLocalTarget, MatrixTableSampleSetError
from lib.model.mito_mt_schema import SeqrMitoSchema, SeqrMitoVariantSchema, SeqrMitoGenotypesSchema, SeqrMitoVariantsAndGenotypesSchema
import seqr_loading

logger = logging.getLogger(__name__)

# --reference-ht-path gs://seqr-reference-data/GRCh38/mitochondrial/all_mito_reference_data/combined_reference_data_chrM.ht
class SeqrVCFToVariantMTTask(seqr_loading.SeqrVCFToMTTask):
    """
    Loads all annotations for the variants of a VCF into a Hail Table (parent class of MT is a misnomer).
    """

    def read_mt_write_mt(self, schema_cls=SeqrMitoVariantsAndGenotypesSchema):
        logger.info("Args:")
        pprint.pprint(self.__dict__)

        mt = hl.read_matrix_table(self.source_paths[0])
        if not self.dont_validate:
            self.validate_mt(mt, self.genome_version, self.sample_type)
        if self.remap_path:
            mt = self.remap_sample_ids(mt, self.remap_path)
        if self.subset_path:
            mt = self.subset_samples_and_variants(mt, self.subset_path)

        ref_data = hl.read_table(self.reference_ht_path)

        mt = schema_cls(mt, ref_data=ref_data).annotate_all(
            overwrite=True).select_annotated_mt()

        mt = mt.annotate_globals(sourceFilePath=','.join(self.source_paths),
                                 genomeVersion=self.genome_version,
                                 sampleType=self.sample_type,
                                 hail_version=pkg_resources.get_distribution('hail').version)

        mt.describe()
        mt.write(self.output().path, stage_locally=True, overwrite=True)

    def run(self):
        # We only want to use the Variant Schema.
        self.read_mt_write_mt(schema_cls=SeqrMitoVariantSchema)


class SeqrVCFToGenotypesMTTask(HailMatrixTableTask):
    remap_path = luigi.OptionalParameter(default=None,
                                         description="Path to a tsv file with two columns: s and seqr_id.")
    subset_path = luigi.OptionalParameter(default=None,
                                          description="Path to a tsv file with one column of sample IDs: s.")

    def requires(self):
        return [SeqrVCFToVariantMTTask()]

    def run(self):
        mt = hl.read_matrix_table(self.input()[0].path)

        if self.remap_path:
            mt = self.remap_sample_ids(mt, self.remap_path)
        if self.subset_path:
            mt = self.subset_samples_and_variants(mt, self.subset_path)

        mt = SeqrMitoGenotypesSchema(mt).annotate_all(overwrite=True).select_annotated_mt()

        mt.describe()
        mt.write(self.output().path, stage_locally=True, overwrite=True)


class SeqrMTToESOptimizedTask(HailElasticSearchTask):

    def __init__(self, *args, **kwargs):
        # TODO: instead of hardcoded index, generate from project_guid, etc.
        super().__init__(*args, **kwargs)

    def requires(self):
        return [SeqrVCFToVariantMTTask(), SeqrVCFToGenotypesMTTask()]

    def run(self):
        variants_mt = hl.read_matrix_table(self.input()[0].path)
        genotypes_mt = hl.read_matrix_table(self.input()[1].path)
        row_ht = genotypes_mt.rows().join(variants_mt.rows())

        row_ht = SeqrMitoVariantsAndGenotypesSchema.elasticsearch_row(row_ht)
        es_shards = self._mt_num_shards(genotypes_mt)
        self.export_table_to_elasticsearch(row_ht, es_shards)

        self.cleanup(es_shards)


if __name__ == '__main__':
    # If run does not succeed, exit with 1 status code.
    luigi.run() or sys.exit(1)
