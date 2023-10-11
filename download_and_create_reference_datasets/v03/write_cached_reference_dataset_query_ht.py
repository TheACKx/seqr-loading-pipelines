#!/usr/bin/env python3
import argparse

from v03_pipeline.lib.misc.io import write
from v03_pipeline.lib.model import CachedReferenceDatasetQuery, ReferenceGenome
from v03_pipeline.lib.paths import valid_cached_reference_dataset_query_path


def run(
    reference_genome: ReferenceGenome,
    query: CachedReferenceDatasetQuery,
):
    ht = query.ht(reference_genome=ReferenceGenome)
    ht = query.query(ht, reference_genome=reference_genome)
    destination_path = valid_cached_reference_dataset_query_path(
        reference_genome,
        query,
    )
    print(f'Uploading ht to {destination_path}')
    write(ht, destination_path)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--reference-genome',
        type=ReferenceGenome,
        choices=list(ReferenceGenome),
        default=ReferenceGenome.GRCh38,
    )
    parser.add_argument(
        '--query',
        type=CachedReferenceDatasetQuery,
        choices=list(CachedReferenceDatasetQuery),
        required=True,
    )
    args, _ = parser.parse_known_args()
    run(args.reference_genome, args.query)
