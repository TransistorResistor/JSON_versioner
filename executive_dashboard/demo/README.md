# Demo based on `test_records`

Build a four-snapshot September demo history from the repository's 26 test records:

```powershell
python executive_dashboard/demo/build_demo.py
python -m streamlit run executive_dashboard/app.py -- --demo
```

The generated data deliberately includes:

- paired `parameterValue` and `parameterUomValue` edits;
- parameter comments;
- widespread parameter-description noise;
- changes across both system-group and system-type dimensions;
- a field added broadly enough to be classified as a structure-only change.

Parameter descriptions, sequence numbers, duplicate labels, and data-type metadata are excluded by the demo configuration. The executive detail layer additionally prefers `parameterUomValue` over separate value/unit fields and retains comments or subtitle fields. Generated snapshots and the SQLite history are local artifacts and are not committed.

For pagination, search, and scale testing, generate the larger month-long corpus:

```powershell
python executive_dashboard/demo/build_large_demo.py
python -m streamlit run executive_dashboard/app.py -- --large-demo
```

The large corpus contains 72 records and eight change cycles across September 2026, producing 576 record updates.

## 500-record monthly history

Build the synthetic corpus based on the 25 system records in `test_records` (the schema-only exemplar is excluded):

```powershell
python executive_dashboard/demo/build_synthetic_demo.py
python -m streamlit run executive_dashboard/app.py -- --synthetic-demo
```

Defaults are 500 records, six months from April through September 2026, and exactly 50 distinct modified records per month. A 31 March baseline is followed by five captures per month, each with ten modified and 490 unchanged records. Baseline additions are excluded from the 300 update events. Record population remains steady at 500.

Each month mixes 20 single-parameter updates, 10 parameter bundles, 10 short-description updates, five extended-description updates, and five metadata updates. Clones retain the source schema and system classifications, with unique synthetic IDs/names, five owners, unique media IDs, and relationship IDs/names remapped within each clone cohort. Synthetic values are clearly marked as demonstration data.

Output is stored in `generated_synthetic/`: the final `records/`, `history/history.sqlite3`, `demo_config.json`, a verified monthly `summary.json`, template `provenance.json`, and a per-event `change_plan.json`. Counts and database integrity are validated before the output is published. Original records and other histories are unchanged.

The generator accepts `--records`, `--changes-per-month`, `--months`, `--start-month YYYY-MM`, and `--seed`. The fixed seed makes the record selection and content reproducible. To rebuild existing output, use `--replace`; the old generated dataset is archived alongside it rather than deleted.
