"""Keep pytest from collecting the nested fixture project (it is only ever run by generate.sh)."""

collect_ignore = ["project"]
