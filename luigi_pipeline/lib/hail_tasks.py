"""
Tasks for Hail.
"""
from collections import Counter
import json
import logging
import math
import os

import hail as hl
import luigi
from luigi.contrib import gcs
from luigi.parameter import ParameterVisibility

from hail_scripts.elasticsearch.hail_elasticsearch_client import HailElasticsearchClient
from lib.global_config import GlobalConfig
import lib.hail_vep_runners as vep_runners

logger = logging.getLogger(__name__)

GRCh37_STANDARD_CONTIGS = {
    "1",
    "10",
    "11",
    "12",
    "13",
    "14",
    "15",
    "16",
    "17",
    "18",
    "19",
    "2",
    "20",
    "21",
    "22",
    "3",
    "4",
    "5",
    "6",
    "7",
    "8",
    "9",
    "X",
    "Y",
    "MT",
}
GRCh38_STANDARD_CONTIGS = {
    "chr1",
    "chr10",
    "chr11",
    "chr12",
    "chr13",
    "chr14",
    "chr15",
    "chr16",
    "chr17",
    "chr18",
    "chr19",
    "chr2",
    "chr20",
    "chr21",
    "chr22",
    "chr3",
    "chr4",
    "chr5",
    "chr6",
    "chr7",
    "chr8",
    "chr9",
    "chrX",
    "chrY",
    "chrM",
}
OPTIONAL_CHROMOSOMES = ["MT", "chrM", "Y", "chrY"]
VARIANT_THRESHOLD = 100
CONST_GRCh37 = "37"
CONST_GRCh38 = "38"


class MatrixTableSampleSetError(Exception):
    def __init__(self, message, missing_samples):
        super().__init__(message)
        self.missing_samples = missing_samples


class VCFValidationError(Exception):
    pass


def GCSorLocalTarget(filename):
    target = gcs.GCSTarget if filename.startswith("gs://") else luigi.LocalTarget
    return target(filename)


class VcfFile(luigi.Task):
    filename = luigi.Parameter()

    def output(self):
        return GCSorLocalTarget(self.filename)


class HailMatrixTableTask(luigi.Task):
    """
    Task that reads in list of VCFs and writes as a Matrix Table. To be overwritten
    to provide specific operations.
    Does not run if dest path exists (complete) or the source path does not (fail).
    """

    source_paths = luigi.Parameter(
        description="Path or list of paths of VCFs to be loaded."
    )
    wes_filter_source_paths = luigi.OptionalParameter(
        default=[],
        description="Path or list of delivered VCFs with filter annotations.",
    )
    dest_path = luigi.Parameter(description="Path to write the matrix table.")
    genome_version = luigi.Parameter(description="Reference Genome Version (37 or 38)")
    vep_runner = luigi.ChoiceParameter(
        choices=["VEP", "DUMMY"],
        default="VEP",
        description="Choice of which vep runner" "to annotate vep.",
    )
    ignore_missing_samples = luigi.BoolParameter(
        default=False, description="Allow missing samples in the callset."
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        try:
            self.source_paths = list(
                json.loads(
                    self.source_paths,
                    object_pairs_hook=luigi.parameter.FrozenOrderedDict,
                )
            )
        except json.JSONDecodeError:
            self.source_paths = [self.source_paths]

    def requires(self):
        # We only exclude globs in source path here so luigi does not check if the file exists
        return [VcfFile(filename=s) for s in self.source_paths if "*" not in s]

    def output(self):
        return GCSorLocalTarget(self.dest_path)

    def complete(self):
        # Complete is called by Luigi to check if the task is done and will skip if it is.
        # By default it checks to see that the output exists, but we want to check for the
        # _SUCCESS file to make sure it was not terminated halfway.
        return GCSorLocalTarget(os.path.join(self.dest_path, "_SUCCESS")).exists()

    def run(self):
        # Overwrite to do custom transformations.
        mt = self.import_vcf()
        mt.write(self.output().path)

    def import_vcf(self):
        # Import the VCFs from inputs. Set min partitions so that local pipeline execution takes advantage of all CPUs.
        recode = {}
        if self.genome_version == "38":
            recode = {f"{i}": f"chr{i}" for i in (list(range(1, 23)) + ["X", "Y"])}
        elif self.genome_version == "37":
            recode = {f"chr{i}": f"{i}" for i in (list(range(1, 23)) + ["X", "Y"])}
        mt = hl.import_vcf(
            self.source_paths,
            reference_genome="GRCh" + self.genome_version,
            skip_invalid_loci=True,
            contig_recoding=recode,
            force_bgz=True,
            min_partitions=500,
        )
        if self.wes_filter_source_paths:
            logger.info("Importing and annotating WES filters...")
            filters_ht = hl.import_vcf(
                self.wes_filter_source_paths,
                reference_genome="GRCh" + self.genome_version,
                skip_invalid_loci=True,
                contig_recoding=recode,
                force_bgz=True,
                min_partitions=500,
            ).rows()
            mt = mt.annotate_rows(filters=filters_ht[mt.row_key].filters)
        return mt

    @staticmethod
    def contig_check(mt, standard_contigs, threshold):
        check_result_dict = {}

        # check chromosomes that are not in the VCF
        row_dict = mt.aggregate_rows(hl.agg.counter(mt.locus.contig))
        contigs_set = set(row_dict.keys())

        all_missing_contigs = standard_contigs - contigs_set
        missing_contigs_without_optional = [
            contig
            for contig in all_missing_contigs
            if contig not in OPTIONAL_CHROMOSOMES
        ]

        if missing_contigs_without_optional:
            check_result_dict["Missing contig(s)"] = missing_contigs_without_optional
            logger.warning(
                "Missing the following chromosomes(s):{}".format(
                    ", ".join(missing_contigs_without_optional)
                )
            )

        for k, v in row_dict.items():
            if k not in standard_contigs:
                check_result_dict.setdefault("Unexpected chromosome(s)", []).append(k)
                logger.warning("Chromosome %s is unexpected.", k)
            elif (k not in OPTIONAL_CHROMOSOMES) and (v < threshold):
                check_result_dict.setdefault(
                    f"Chromosome(s) whose variants count under threshold {threshold}",
                    [],
                ).append(k)
                logger.warning(
                    "Chromosome %s has %d rows, which is lower than threshold %d.",
                    k,
                    v,
                    threshold,
                )

        return check_result_dict

    @staticmethod
    def sample_type_stats(mt, genome_version, threshold=0.3):
        """
        Calculate stats for sample type by checking against a list of common coding and non-coding variants.
        If the match for each respective type is over the threshold, we return a match.

        :param mt: Matrix Table to check
        :param genome_version: reference genome version
        :param threshold: if the matched percentage is over this threshold, we classify as match
        :return: a dict of coding/non-coding to dict with 'matched_count', 'total_count' and 'match' boolean.
        """
        stats = {}
        types_to_ht_path = {
            "noncoding": GlobalConfig().param_kwargs[
                "validation_%s_noncoding_ht" % genome_version
            ],
            "coding": GlobalConfig().param_kwargs[
                "validation_%s_coding_ht" % genome_version
            ],
        }
        for sample_type, ht_path in types_to_ht_path.items():
            ht = hl.read_table(ht_path)
            stats[sample_type] = ht_stats = {
                "matched_count": mt.semi_join_rows(ht).count_rows(),
                "total_count": ht.count(),
            }
            ht_stats["match"] = (
                ht_stats["matched_count"] / ht_stats["total_count"]
            ) >= threshold
        return stats

    @staticmethod
    def validate_mt(mt, genome_version, sample_type):
        """
        Validate the mt by checking against a list of common coding and non-coding variants given its
        genome version. This validates genome_version, variants, and the reported sample type.

        :param mt: mt to validate
        :param genome_version: reference genome version
        :param sample_type: WGS or WES
        :return: True or Exception
        """
        if genome_version == CONST_GRCh37:
            contig_check_result = HailMatrixTableTask.contig_check(
                mt, GRCh37_STANDARD_CONTIGS, VARIANT_THRESHOLD
            )
        elif genome_version == CONST_GRCh38:
            contig_check_result = HailMatrixTableTask.contig_check(
                mt, GRCh38_STANDARD_CONTIGS, VARIANT_THRESHOLD
            )
        else:
            raise VCFValidationError(
                "Genome version validation error: dataset specified as GRCh{genome_version}.".format(
                    genome_version=genome_version
                )
            )

        if bool(contig_check_result):
            err_msg = ""
            for k, v in contig_check_result.items():
                err_msg += "{k}: {v}. ".format(k=k, v=", ".join(v))
            raise VCFValidationError(err_msg)

        sample_type_stats = HailMatrixTableTask.sample_type_stats(mt, genome_version)

        for name, stat in sample_type_stats.items():
            logger.info(
                "Table contains %i out of %i common %s variants."
                % (stat["matched_count"], stat["total_count"], name)
            )

        has_coding = sample_type_stats["coding"]["match"]
        has_noncoding = sample_type_stats["noncoding"]["match"]

        if not has_coding and not has_noncoding:
            # No common variants detected.
            raise VCFValidationError(
                "Genome version validation error: dataset specified as GRCh{genome_version} but doesn't contain "
                "the expected number of common GRCh{genome_version} variants".format(
                    genome_version=genome_version
                )
            )
        elif has_noncoding and not has_coding:
            # Non coding only.
            raise VCFValidationError(
                "Sample type validation error: Dataset contains noncoding variants but is missing common coding "
                "variants for GRCh{}. Please verify that the dataset contains coding variants.".format(
                    genome_version
                )
            )
        elif has_coding and not has_noncoding:
            # Only coding should be WES.
            if sample_type != "WES":
                raise VCFValidationError(
                    "Sample type validation error: dataset sample-type is specified as WGS but appears to be "
                    "WES because it contains many common coding variants"
                )
        elif has_noncoding and has_coding:
            # Both should be WGS.
            if sample_type != "WGS":
                raise VCFValidationError(
                    "Sample type validation error: dataset sample-type is specified as WES but appears to be "
                    "WGS because it contains many common non-coding variants"
                )
        return True

    def run_vep(mt, genome_version, runner="VEP", vep_config_json_path=None):
        runners = {
            "VEP": vep_runners.HailVEPRunner,
            "DUMMY": vep_runners.HailVEPDummyRunner,
        }

        return runners[runner]().run(
            mt, genome_version, vep_config_json_path=vep_config_json_path
        )

    @staticmethod
    def subset_samples_and_variants(mt, subset_path, ignore_missing_samples=False):
        """
        Subset the MatrixTable to the provided list of samples and to variants present in those samples
        :param mt: MatrixTable from VCF
        :param subset_path: Path to a file with a single column 's'
        :param ignore_missing_samples: ignore missing samples if true unless all samples are missing
        :return: MatrixTable subsetted to list of samples
        """
        subset_ht = hl.import_table(subset_path, key="s")
        subset_count = subset_ht.count()
        anti_join_ht = subset_ht.anti_join(mt.cols())
        anti_join_ht_count = anti_join_ht.count()

        if anti_join_ht_count != 0:
            missing_samples = anti_join_ht.s.collect()
            message = (
                f"Only {subset_count - anti_join_ht_count} out of {subset_count} "
                f"subsetting-table IDs matched IDs in the variant callset.\n"
                f"IDs that aren't in the callset: {missing_samples}\n"
                f"All callset sample IDs:{mt.s.collect()}"
            )
            if (subset_count > anti_join_ht_count) and ignore_missing_samples:
                logger.warning(message)
            else:
                raise MatrixTableSampleSetError(message, missing_samples)

        mt = mt.semi_join_cols(subset_ht)
        mt = mt.filter_rows(hl.agg.any(mt.GT.is_non_ref()))

        logger.info(
            f"Finished subsetting samples. Kept {subset_count} "
            f"out of {mt.count()} samples in vds"
        )
        return mt

    @staticmethod
    def remap_sample_ids(mt, remap_path):
        """
        Remap the MatrixTable's sample ID, 's', field to the sample ID used within seqr, 'seqr_id'
        If the sample 's' does not have a 'seqr_id' in the remap file, 's' becomes 'seqr_id'
        :param mt: MatrixTable from VCF
        :param remap_path: Path to a file with two columns 's' and 'seqr_id'
        :return: MatrixTable remapped and keyed to use seqr_id
        """
        remap_ht = hl.import_table(remap_path, key="s")
        s_dups = [k for k, v in Counter(remap_ht.s.collect()).items() if v > 1]
        seqr_dups = [k for k, v in Counter(remap_ht.seqr_id.collect()).items() if v > 1]

        if len(s_dups) > 0 or len(seqr_dups) > 0:
            raise ValueError(
                f"Duplicate s or seqr_id entries in remap file were found. Duplicate s:{s_dups}. Duplicate seqr_id:{seqr_dups}."
            )

        missing_samples = remap_ht.anti_join(mt.cols()).collect()
        remap_count = remap_ht.count()

        if len(missing_samples) != 0:
            raise MatrixTableSampleSetError(
                f"Only {remap_ht.semi_join(mt.cols()).count()} out of {remap_count} "
                "remap IDs matched IDs in the variant callset.\n"
                f"IDs that aren't in the callset: {missing_samples}\n"
                f"All callset sample IDs:{mt.s.collect()}",
                missing_samples,
            )

        mt = mt.annotate_cols(**remap_ht[mt.s])
        remap_expr = hl.cond(hl.is_missing(mt.seqr_id), mt.s, mt.seqr_id)
        mt = mt.annotate_cols(seqr_id=remap_expr, vcf_id=mt.s)
        mt = mt.key_cols_by(s=mt.seqr_id)
        logger.info(f"Remapped {remap_count} sample ids...")
        return mt

    @staticmethod
    def add_37_coordinates(mt, liftover_ref_path):
        """Annotates the GRCh38 MT with 37 coordinates using hail's built-in liftover
        :param mt: MatrixTable from VCF
        :param liftover_ref_path: Path to GRCh38 to GRCh37 coordinates file
        :return: MatrixTable annotated with GRCh37 coordinates
        """
        rg37 = hl.get_reference("GRCh37")
        rg38 = hl.get_reference("GRCh38")
        rg38.add_liftover(liftover_ref_path, rg37)
        mt = mt.annotate_rows(rg37_locus=hl.liftover(mt.locus, "GRCh37"))
        return mt

    def generate_callstats(self, mt):
        """
        Generate call statistics for all variants in the dataset.

        :param mt: MatrixTable to generate call statistics on.
        :return: Matrixtable with gt_stats annotation.
        """
        return mt.annotate_rows(gt_stats=hl.agg.call_stats(mt.GT, mt.alleles))


class HailElasticSearchTask(luigi.Task):
    """
    Loads a MT to ES (TODO).
    """

    source_path = luigi.OptionalParameter(default=None)
    use_temp_loading_nodes = luigi.BoolParameter(
        default=True, description="Whether to use temporary loading nodes."
    )
    es_host = luigi.Parameter(description="ElasticSearch host.", default="localhost")
    es_port = luigi.IntParameter(description="ElasticSearch port.", default=9200)
    es_index = luigi.Parameter(description="ElasticSearch index.", default="data")
    es_username = luigi.Parameter(
        description="ElasticSearch username.", default="pipeline"
    )
    es_password = luigi.Parameter(
        description="ElasticSearch password.",
        visibility=ParameterVisibility.PRIVATE,
        default=None,
    )
    es_index_min_num_shards = luigi.IntParameter(
        default=1,
        description="Number of shards for the index will be the greater of "
        "this value and a calculated value based on the matrix.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.es_index != self.es_index.lower():
            raise Exception(
                f"Invalid es_index name [{self.es_index}], must be lowercase"
            )

        self._es = HailElasticsearchClient(
            host=self.es_host,
            port=self.es_port,
            es_username=self.es_username,
            es_password=self.es_password,
        )

    def requires(self):
        return [VcfFile(filename=self.source_path)]

    def run(self):
        mt = self.import_mt()
        # TODO: Load into ES

    def import_mt(self):
        return hl.read_matrix_table(self.input()[0].path)

    def export_table_to_elasticsearch(self, table, num_shards, disabled_fields=None):
        func_to_run_after_index_exists = (
            None
            if not self.use_temp_loading_nodes
            else lambda: self._es.route_index_to_temp_es_cluster(self.es_index)
        )
        self._es.export_table_to_elasticsearch(
            table,
            index_name=self.es_index,
            disable_index_for_fields=disabled_fields,
            func_to_run_after_index_exists=func_to_run_after_index_exists,
            elasticsearch_mapping_id="docId",
            num_shards=num_shards,
            write_null_values=True,
        )

    def cleanup(self, es_shards):
        self._es.route_index_off_temp_es_cluster(self.es_index)
        # Current disk configuration requires the previous index to be deleted prior to large indices, ~1TB, transferring off loading nodes
        if es_shards < 25:
            self._es.wait_for_shard_transfer(self.es_index)

    def _mt_num_shards(self, mt):
        # The greater of the user specified min shards and calculated based on the variants and samples
        denominator = 1.4 * 10**9
        calculated_num_shards = math.ceil(
            (mt.count_rows() * mt.count_cols()) / denominator
        )
        return max(self.es_index_min_num_shards, calculated_num_shards)
