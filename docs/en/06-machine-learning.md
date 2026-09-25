# Predictive Radar and machine learning

The `ml/` folder contains **production inference**, not a promise that every possible prediction is useful. The current models were selected after chronological out-of-sample testing. Targets that did not remain stable were rejected rather than exposed in the product.

The retained Radar families are short-horizon trading activity, medium-horizon probability of a higher equity price, and risk that the next newly published fund NAV is lower. Exact future prices are deliberately not presented as validated predictions.

`MODEL_CARD.md` explains the assumptions and validation results. `model-manifest.json` describes model inputs and files. Reliability JSON files help separate a probability from the confidence the interface should place in it.

The ML engine runs after a successful database import and stores predictions in PostgreSQL. It is separate from PDF extraction so an ML failure cannot corrupt the source-data pipeline.
