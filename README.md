# cf-scripts
Conda-Forge dependency graph tracker and auto ticker

[regro-cf-autotick-bot's PRs](https://github.com/pulls?utf8=%E2%9C%93&q=is%3Aopen+is%3Apr+author%3Aregro-cf-autotick-bot+archived%3Afalse+) 

# Dashboard
[![CircleCI](https://circleci.com/gh/regro/circle_worker.svg?style=svg)](https://circleci.com/gh/regro/circle_worker)

## Plan
There are five sections:
1. find feedstocks which finds all the names of the current feedstocks.
1. make graph which makes the DAG of packages and their dependencies using `networkx` with each recipe represented by a node. Important data from the `meta.yaml` for each recipe stuffed into the node `attrs`.
1. check upstream finds versions for each recipe's upstream source.
1. auto tick takes each node which is out of date and creates a PR to bring the recipe up to date with the source.
1. status updates the status page with current migrations.

These scripts run on CircleCI every hour and write the output data (the list of all conda-forge packages and the dependency graph) to the [cf-graph repo](https://github.com/regro/cf-graph3). 

## Setup

Below are instructions for setting up a local installation for testing. They
assume that you have conda installed and conda-forge in your channel list.

```
conda create -y -n cf --file requirements/run --file requirements/test ipython
source activate cf
python setup.py install
coverage run run_tests.py
```

To access the graph: 
1. clone the [cf-graph repo](https://github.com/regro/cf-graph3)
1. change dirs into the graph dir
1. in an interactive session run ``import networkx as nx``
1. ``gx = gx = nx.read_gpickle('graph.pkl')``
1. gx contains the entire conda-forge graph, node attributes can be accessed via ``gx.node['<name_of_feedstock>']``
