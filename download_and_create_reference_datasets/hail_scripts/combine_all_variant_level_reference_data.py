import argparse
import hail
import logging
from pprint import pprint

from hail_scripts.utils.add_1kg_phase3 import add_1kg_phase3_to_vds, read_1kg_phase3_vds
from hail_scripts.utils.add_cadd import add_cadd_to_vds, read_cadd_vds
from hail_scripts.utils.add_dbnsfp import add_dbnsfp_to_vds, read_dbnsfp_vds
from hail_scripts.utils.add_eigen import add_eigen_to_vds, read_eigen_vds
from hail_scripts.utils.add_exac import add_exac_to_vds, read_exac_vds
from hail_scripts.utils.add_gnomad import add_gnomad_to_vds, read_gnomad_vds
from hail_scripts.utils.add_gnomad_coverage import add_gnomad_exome_coverage_to_vds, add_gnomad_genome_coverage_to_vds
from hail_scripts.utils.add_mpc import add_mpc_to_vds, read_mpc_vds
from hail_scripts.utils.add_primate_ai import add_primate_ai_to_vds, read_primate_ai_vds
from hail_scripts.utils.add_topmed import add_topmed_to_vds, read_topmed_vds
from hail_scripts.utils.gcloud_utils import delete_gcloud_file
from hail_scripts.utils.vds_utils import write_vds, read_vds

logging.basicConfig(format='%(asctime)s %(levelname)-8s %(message)s')
logger = logging.getLogger()
logger.setLevel(logging.INFO)


p = argparse.ArgumentParser()
p.add_argument("-g", "--genome-version", help="Genome build: 37 or 38", choices=["37", "38"], required=True)

p.add_argument("--output-vds", help="Output vds path",
               default = "gs://seqr-reference-data/GRCh{genome_version}/all_reference_data/combined_reference_data_grch{genome_version}.vds")

p.add_argument('--subset', const="X:31097677-33339441", nargs='?',
               help="All data will first be subsetted to this chrom:start-end range. Intended for testing.")

p.add_argument("--exclude-dbnsfp", action="store_true", help="Don't add annotations from dbnsfp. Intended for testing.")
p.add_argument("--exclude-1kg", action="store_true", help="Don't add 1kg AFs. Intended for testing.")
p.add_argument("--exclude-omim", action="store_true", help="Don't add OMIM mim id column. Intended for testing.")
p.add_argument("--exclude-gene-constraint", action="store_true", help="Don't add gene constraint columns. Intended for testing.")
p.add_argument("--exclude-eigen", action="store_true", help="Don't add Eigen scores. Intended for testing.")
p.add_argument("--exclude-cadd", action="store_true", help="Don't add CADD scores (they take a really long time to load). Intended for testing.")
p.add_argument("--exclude-gnomad", action="store_true", help="Don't add gnomAD exome or genome fields. Intended for testing.")
p.add_argument("--exclude-exac", action="store_true", help="Don't add ExAC fields. Intended for testing.")
p.add_argument("--exclude-topmed", action="store_true", help="Don't add TopMed AFs. Intended for testing.")
p.add_argument("--exclude-mpc", action="store_true", help="Don't add MPC fields. Intended for testing.")
p.add_argument("--exclude-primate-ai", action="store_true", help="Don't add PrimateAI fields. Intended for testing.")
p.add_argument("--exclude-gnomad-coverage", action="store_true", help="Don't add gnomAD exome and genome coverage. Intended for testing.")

p.add_argument("--start-with-step", help="Which step to start with.", type=int, default=0, choices=[0, 1, 2, 3, 4])

p.add_argument("--dont-delete-intermediate-vds-files", action="store_true", help="Keep intermediate VDS files to allow restarting the pipeline from the middle using --start-with-step")

#p.add_argument("-H", "--host", help="Elasticsearch node host or IP. To look this up, run: `kubectl describe nodes | grep Addresses`")
#p.add_argument("-p", "--port", help="Elasticsearch port", default=30001, type=int)  # 9200
#p.add_argument("--export-to-vds", help="Path of vds", default="gs://seqr-reference-data/GRCh%(genome_version)s/all_reference_data/all_reference_data.vds")
#p.add_argument("--export-to-elastic-search", help="Whether to export the data to elasticsearch", action="store_true")
#p.add_argument("--index", help="Elasticsearch index name", default="all-reference-data")
#p.add_argument("--index-type", help="Elasticsearch index type", default="variant")
#p.add_argument("--block-size", help="Elasticsearch block size", default=200, type=int)
#p.add_argument("--num-shards", help="Number of shards", default=1, type=int)

args = p.parse_args()

filter_interval = args.subset if args.subset else None  #"1-MT"

output_vds = args.output_vds.format(genome_version=args.genome_version)

test_output_vds = output_vds + ".test"
step0_output_vds = output_vds.replace(".vds", "") + "_minimal.vds"
step1_output_vds = output_vds.replace(".vds", "") + "_with_coverage1.vds"
step2_output_vds = output_vds.replace(".vds", "") + "_with_coverage2.vds"
step3_output_vds = output_vds.replace(".vds", "") + "_annotations1.vds"


if args.start_with_step == 0:
    logger.info("\n=============================== step 0 - combine all datasets into 1 minimal vds ===============================")
    hc = hail.HailContext(log="/hail.log")

    # check that args.output_vds path is writable
    with hail.utils.hadoop_write(test_output_vds) as f:
        f.write("")
    delete_gcloud_file(test_output_vds)

    # compute a vds that contains the union of all variants from all the reference datasets
    all_vds_objects = []
    if not args.exclude_cadd: all_vds_objects.append(read_cadd_vds(hc, args.genome_version, subset=filter_interval))
    if not args.exclude_dbnsfp: all_vds_objects.append(read_dbnsfp_vds(hc, args.genome_version, subset=filter_interval))
    if not args.exclude_1kg: all_vds_objects.append(read_1kg_phase3_vds(hc, args.genome_version, subset=filter_interval))
    if not args.exclude_exac: all_vds_objects.append(read_exac_vds(hc, args.genome_version, subset=filter_interval))
    if not args.exclude_topmed: all_vds_objects.append(read_topmed_vds(hc, args.genome_version, subset=filter_interval))
    if not args.exclude_mpc: all_vds_objects.append(read_mpc_vds(hc, args.genome_version, subset=filter_interval))
    if not args.exclude_gnomad: all_vds_objects.append(read_gnomad_vds(hc, args.genome_version, "exomes", subset=filter_interval))
    if not args.exclude_gnomad: all_vds_objects.append(read_gnomad_vds(hc, args.genome_version, "genomes", subset=filter_interval))
    if not args.exclude_eigen: all_vds_objects.append(read_eigen_vds(hc, args.genome_version, subset=filter_interval))
    if not args.exclude_primate_ai: all_vds_objects.append(read_primate_ai_vds(hc, args.genome_version, subset=filter_interval))

    all_vds_objects_with_minimal_schema = []
    for vds_object in all_vds_objects:
        all_vds_objects_with_minimal_schema.append(
            vds_object.annotate_variants_expr('va = {}'))  # drop all variant-level fields except chrom-pos-ref-alt

    vds = hail.VariantDataset.union(*all_vds_objects_with_minimal_schema)
    vds = vds.deduplicate()

    write_vds(vds, step0_output_vds)

    hc.stop()


if args.start_with_step <= 1:
    logger.info("=============================== step 1 - read in minimal vds and add in gnomAD exomes coverage ===============================")

    hc = hail.HailContext(log="/hail.log")
    vds = read_vds(hc, step0_output_vds)

    pprint(vds.variant_schema)

    # start with the cadd vds since it contains all possible SNPs and common indels
    if not args.exclude_gnomad_coverage:
        vds = add_gnomad_exome_coverage_to_vds(hc, vds, args.genome_version, root="va.gnomad_exome_coverage")

    write_vds(vds, step1_output_vds)

    hc.stop()

    #if not args.dont_delete_intermediate_vds_files:
    #    delete_gcloud_file(step0_output_vds, is_directory=True)

if args.start_with_step <= 2:
    logger.info("=============================== step 2 - read in minimal vds and add in gnomAD genomes coverage ===============================")

    hc = hail.HailContext(log="/hail.log")
    vds = read_vds(hc, step1_output_vds)

    pprint(vds.variant_schema)

    # start with the cadd vds since it contains all possible SNPs and common indels
    if not args.exclude_gnomad_coverage:
        vds = add_gnomad_genome_coverage_to_vds(hc, vds, args.genome_version, root="va.gnomad_genome_coverage")

    write_vds(vds, step2_output_vds)

    hc.stop()

    #if not args.dont_delete_intermediate_vds_files:
    #    delete_gcloud_file(step1_output_vds, is_directory=True)

if args.start_with_step <= 3:

    logger.info("\n=============================== step 3 - read in vds and annotate it with reference datasets ===============================")

    hc = hail.HailContext(log="/hail.log")
    vds = read_vds(hc, step2_output_vds)

    pprint(vds.variant_schema)

    if not args.exclude_cadd:
        logger.info("\n==> add cadd")
        vds = add_cadd_to_vds(hc, vds, args.genome_version, root="va.cadd", subset=filter_interval)
        pprint(vds.variant_schema)

    if not args.exclude_eigen:
        logger.info("\n==> add eigen")
        vds = add_eigen_to_vds(hc, vds, args.genome_version, root="va.eigen", subset=filter_interval)
        pprint(vds.variant_schema)

    if not args.exclude_1kg:
        logger.info("\n==> add 1kg")
        vds = add_1kg_phase3_to_vds(hc, vds, args.genome_version, root="va.g1k", subset=filter_interval)
        pprint(vds.variant_schema)

    if not args.exclude_exac:
        logger.info("\n==> add exac")
        vds = add_exac_to_vds(hc, vds, args.genome_version, root="va.exac", subset=filter_interval)
        pprint(vds.variant_schema)

    write_vds(vds, step3_output_vds)

    hc.stop()

if args.start_with_step <= 4:

    logger.info("\n=============================== step 4 - read in vds and annotate it with additional reference datasets ===============================")

    hc = hail.HailContext(log="/hail.log")
    vds = read_vds(hc, step3_output_vds)

    if not args.exclude_gnomad:
        logger.info("\n==> add gnomad exomes")
        vds = add_gnomad_to_vds(hc, vds, args.genome_version, exomes_or_genomes="exomes", root="va.gnomad_exomes", subset=filter_interval)
        pprint(vds.variant_schema)

    if not args.exclude_gnomad:
        logger.info("\n==> add gnomad genomes")
        vds = add_gnomad_to_vds(hc, vds, args.genome_version, exomes_or_genomes="genomes", root="va.gnomad_genomes", subset=filter_interval)
        pprint(vds.variant_schema)

    if not args.exclude_dbnsfp:
        logger.info("\n==> add dbnsfp")
        vds = add_dbnsfp_to_vds(hc, vds, args.genome_version, root="va.dbnsfp", subset=filter_interval)

        if args.genome_version == "37":
            # dbNSFP is missing DANN scores for GRCh37, so add it from hail annotationdb.
            # Later when annotationdb is available GRCh38 use it for everything.
            vds = vds.annotate_variants_db('va.dann.score')\
                .annotate_variants_expr("va.dbnsfp.DANN_score = va.dann.score")\
                .annotate_variants_expr("va = drop(va, dann)")

        pprint(vds.variant_schema)

    if not args.exclude_topmed:
        logger.info("\n==> add topmed")
        vds = add_topmed_to_vds(hc, vds, args.genome_version, root="va.topmed", subset=filter_interval)
        pprint(vds.variant_schema)

    if not args.exclude_mpc:
        logger.info("\n==> add mpc")
        vds = add_mpc_to_vds(hc, vds, args.genome_version, root="va.mpc", subset=filter_interval)
        pprint(vds.variant_schema)

    if not args.exclude_primate_ai:
        logger.info("\n==> add primate_ai")
        vds = add_primate_ai_to_vds(hc, vds, args.genome_version, root="va.primate_ai", subset=filter_interval)
        pprint(vds.variant_schema)

    # DON'T add clinvar because it updates frequently
    #if not args.exclude_clinvar:
    #    logger.info("\n==> Add clinvar")
    #    vds = add_clinvar_to_vds(hc, vds, args.genome_version, root="va.clinvar", subset=filter_interval)

    # DON'T add hgmd because it's got a restrictive license, so only staff users can use it
    #if not args.exclude_hgmd:
    #    logger.info("\n==> Add hgmd")
    #    vds = add_hgmd_to_vds(hc, vds, args.genome_version, root="va.hgmd", subset=filter_interval)

    pprint(vds.variant_schema)

    write_vds(vds, output_vds)

    #if not args.dont_delete_intermediate_vds_files:
    #    delete_gcloud_file(step2_output_vds)


summary = vds.summarize()
pprint(summary)




"""
from hail_scripts.utils.elasticsearch_client import ElasticsearchClient

DISABLE_INDEX_AND_DOC_VALUES_FOR_FIELDS = ("sortedTranscriptConsequences", )

print("======== Export to elasticsearch ======")
es = ElasticsearchClient(
    host=args.host,
    port=args.port,
)

es.export_vds_to_elasticsearch(
    vds,
    index_name=args.index,
    index_type_name=args.index_type,
    block_size=args.block_size,
    num_shards=args.num_shards,
    delete_index_before_exporting=True,
    disable_doc_values_for_fields=DISABLE_INDEX_AND_DOC_VALUES_FOR_FIELDS,
    disable_index_for_fields=DISABLE_INDEX_AND_DOC_VALUES_FOR_FIELDS,
    verbose=True,
)
"""
