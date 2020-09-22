import logging
import os
import random
import re
import textwrap
import time
import typing
from collections import OrderedDict
from concurrent.futures._base import as_completed

# from datetime import datetime
# import cProfile

import github3
import networkx as nx
import requests
import tqdm

# from conda_forge_tick.profiler import profiling

from conda_forge_tick.git_utils import (
    close_out_labels,
    is_github_api_limit_reached,
    refresh_pr,
    close_out_dirty_prs,
    clean_up_closed_prs,
)
from .make_graph import github_token, ghctx
from .utils import (
    setup_logger,
    load_graph,
    github_client,
    executor,
)

if typing.TYPE_CHECKING:
    from .cli import CLIArgs

logger = logging.getLogger("conda_forge_tick.update_prs")

NUM_GITHUB_THREADS = 1
KEEP_PR_FRACTION = 0.5


def _get_last_updated_prs():
    query = textwrap.dedent(
        """
        {
          user(login: "regro-cf-autotick-bot") {
            pullRequests(first: 100, states: [CLOSED, MERGED], orderBy: {field: UPDATED_AT, direction: DESC} ) {
              totalCount
              nodes {
                closedAt
                createdAt
                number
                title
                databaseId
                baseRepository {
                  name
                }
              }
              pageInfo {
                hasNextPage
                endCursor
              }
            }
          }
        }
    """,  # noqa
    )
    headers = {"Authorization": f"token {github_token}"}
    # Try several times because this times out
    for i in range(10):
        logger.info("graphQL request try %d", i + 1)
        resp = requests.post(
            "https://api.github.com/graphql", json={"query": query}, headers=headers,
        )
        if resp.status_code == 200 and "data" in resp.json():
            data = resp.json()
            pr_ids = []
            for node in data["data"]["user"]["pullRequests"]["nodes"]:
                pr_ids.append(node["databaseId"])
            return pr_ids
        time.sleep(10)
    return []


PR_JSON_REGEX = re.compile(r"^pr_json/([0-9]*).json$")


def _update_pr(update_function, dry_run, gx):
    failed_refresh = 0
    succeeded_refresh = 0
    gh = "" if dry_run else github_client()
    futures = {}
    node_ids = list(gx.nodes)
    # this makes sure that github rate limits are dispersed
    random.shuffle(node_ids)

    pr_info_ordered = OrderedDict()
    # if not dry_run:
    #     last_prs = _get_last_updated_prs()
    # else:
    #     last_prs = []
    last_prs = []
    # Setting them here first gives them the highest priority in the OrderedDict
    for pr_id in last_prs:
        pr_info_ordered[pr_id] = None

    with executor("thread", NUM_GITHUB_THREADS) as pool:
        for node_id in tqdm.tqdm(node_ids, desc="ordering PRs", leave=False):
            node = gx.nodes[node_id]["payload"]
            prs = node.get("PRed", [])
            for i, migration in enumerate(prs):

                if random.uniform(0, 1) >= KEEP_PR_FRACTION:
                    continue

                pr_json = migration.get("PR", None)

                # allow for false
                if pr_json:
                    if "__lazy_json__" in pr_json:
                        m = PR_JSON_REGEX.match(pr_json["__lazy_json__"])
                        if m:
                            pr_id = int(m.group(1))
                        else:
                            pr_id = object()
                    else:
                        pr_id = object()
                    pr_info_ordered[pr_id] = (pr_json, node_id, i)

        for pr_id, v in pr_info_ordered.items():
            if v:
                (pr_json, node_id, i) = v
                future = pool.submit(update_function, ghctx, pr_json, gh, dry_run)
                futures[future] = (node_id, i, pr_json)

        for f in as_completed(futures):
            name, i, pr_json = futures[f]
            try:
                res = f.result()
                if res:
                    succeeded_refresh += 1
                    pr_json.update(**res)
                    logger.info(f"Updated json for {name}: {res['id']}")
            except github3.GitHubError as e:
                logger.error(f"GITHUB ERROR ON FEEDSTOCK: {name}")
                failed_refresh += 1
                if is_github_api_limit_reached(e, gh):
                    break
            except github3.exceptions.ConnectionError:
                logger.error(f"GITHUB ERROR ON FEEDSTOCK: {name}")
                failed_refresh += 1
            except Exception:
                logger.critical(
                    "ERROR ON FEEDSTOCK: {}: {}".format(
                        name, gx.nodes[name]["payload"]["PRed"][i],
                    ),
                )
                raise

    return succeeded_refresh, failed_refresh


def update_graph_pr_status(gx: nx.DiGraph, dry_run: bool = False) -> nx.DiGraph:
    succeeded_refresh, failed_refresh = _update_pr(refresh_pr, dry_run, gx)

    logger.info(f"JSON Refresh failed for {failed_refresh} PRs")
    logger.info(f"JSON Refresh succeed for {succeeded_refresh} PRs")
    return gx


def close_labels(gx: nx.DiGraph, dry_run: bool = False) -> nx.DiGraph:
    succeeded_refresh, failed_refresh = _update_pr(close_out_labels, dry_run, gx)

    logger.info(f"bot re-run failed for {failed_refresh} PRs")
    logger.info(f"bot re-run succeed for {succeeded_refresh} PRs")
    return gx


def close_dirty_prs(gx: nx.DiGraph, dry_run: bool = False) -> nx.DiGraph:
    succeeded_refresh, failed_refresh = _update_pr(close_out_dirty_prs, dry_run, gx)

    logger.info(f"bot re-run failed for {failed_refresh} PRs")
    logger.info(f"bot re-run succeed for {succeeded_refresh} PRs")
    return gx


keep_keys = {"url", "ETag", "Last-Modified", "id", "labels", "state", "merged_at"}


def cut_down_closed_prs(gx: nx.DiGraph, dry_run: bool = False) -> nx.DiGraph:
    for name, node in gx.nodes("payload"):
        for pr in [v.get("PR") for v in node.get("PRed")]:
            if pr and pr["state"] == "closed":
                with pr as pr_json:
                    for k in list(pr_json):
                        if k not in keep_keys:
                            del pr_json[k]
    return gx


# @profiling
def main(args: "CLIArgs") -> None:
    # # get current time
    # now = datetime.now()
    # current_time = now.strftime("%d-%m-%Y") + "_" + now.strftime("%H_%M_%S")

    # # start profiler
    # prof = cProfile.Profile()
    # prof.enable()

    setup_logger(logger)

    if os.path.exists("graph.json"):
        gx = load_graph()
    else:
        gx = None
    # Utility flag for testing -- we don't need to always update GH
    no_github_fetch = os.environ.get("CONDA_FORGE_TICK_NO_GITHUB_REQUESTS")
    if not no_github_fetch:
        gx = close_labels(gx, args.dry_run)
        gx = update_graph_pr_status(gx, args.dry_run)
        # This function needs to run last since it edits the actual pr json!
        gx = close_dirty_prs(gx, args.dry_run)

    # # stop profiler
    # prof.disable()
    #
    # # output to data
    # os.makedirs("profiler/update_prs", exist_ok=True)
    # prof.dump_stats(f"profiler/update_prs/{current_time}.txt")


if __name__ == "__main__":
    pass
    # main()
