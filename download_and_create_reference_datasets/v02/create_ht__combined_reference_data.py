#!/usr/bin/env python3

from kubernetes.shell_utils import simple_run as run

run((
    "python3 gcloud_dataproc/v02/run_script.py "
    "--cluster create-ht-combined-reference-data "
    "download_and_create_reference_datasets/v02/hail_scripts/write_combined_reference_data_ht.py"))
