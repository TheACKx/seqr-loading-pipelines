import pip

pip.main(['install', 'elasticsearch'])

import argparse
import elasticsearch
import logging
import os
from pprint import pprint
import time

logging.basicConfig(format='%(asctime)s %(levelname)-8s %(message)s')
logger = logging.getLogger()
logger.setLevel(logging.INFO)

p = argparse.ArgumentParser()
p.add_argument("-H", "--host", help="Elasticsearch node host or IP. To look this up, run: `kubectl describe nodes | grep Addresses`", required=True)
p.add_argument("-p", "--port", help="Elasticsearch port", default=9200, type=int)  # 9200
p.add_argument("-b", "--bucket", help="Google bucket name", default="seqr-database-backups")
p.add_argument("-d", "--base-path", help="Path within the bucket", default="elasticsearch/snapshots")
p.add_argument("-r", "--repo", help="Repository name", default="elasticsearch-prod")
p.add_argument("-i", "--index", help="Index name(s). One or more comma-separated index names to include in the snapshot", required=True)
p.add_argument("-w", "--wait-for-completion", action="store_true", help="Whether to wait until the snapshot is created before returning")

# parse args
args = p.parse_args()

es = elasticsearch.Elasticsearch(args.host, port=args.port)

existing_indices = es.indices.get(index="*").keys()
if args.index not in existing_indices:
    p.error("%s not found. Existing indices are: %s" % (args.index, existing_indices))

# see https://www.elastic.co/guide/en/elasticsearch/reference/current/modules-snapshots.html
snapshot_name = "snapshot_%s__%s" % (args.index.lower(), time.strftime("%Y-%m-%d_%H-%M-%S", time.localtime()))

# see https://www.elastic.co/guide/en/elasticsearch/plugins/current/repository-gcs-repository.html
print("==> Check if snapshot repo exists: %s" % args.repo)
repo_info = es.snapshot.get_repository(repository=args.repo)
pprint(repo_info)

print("==> Creating snapshot in gs://%s/%s for index %s" % (args.bucket, args.base_path, args.index))
pprint(
    es.snapshot.create(
        repository=args.repo,
        snapshot=snapshot_name,
        wait_for_completion=args.wait_for_completion,
        body={
            "indices": args.index
        })
)

print("==> Getting snapshot status for: " + snapshot_name)
pprint(
    es.snapshot.status(repository=args.repo)
)
