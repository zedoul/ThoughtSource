import pytest
from cot import Collection

def test_unique_id() -> None:
    """Test that id is unique within a dataset"""
    collection = Collection("all")
    for name, dataset in collection:
        for split in dataset:
            pd_ = dataset[split].to_pandas()
            assert (len(pd_["id"]) == pd_["id"].nunique()), f"IDs are not unique in {name} {split}"

