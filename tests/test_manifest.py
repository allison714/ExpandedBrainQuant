from ebq.manifest import check_manifest, infer_genotype, read_manifest, write_template


def test_roles_and_genotype(tmp_path):
    p = tmp_path / "m.csv"
    write_template(p, [
        {"dataset": "260831_KO6_GluA3", "path": str(tmp_path), "animal": "KO6", "channel_0": "GluA3", "channel_1": "pan"},
        {"dataset": "261009_Sh2_ACC_1", "path": str(tmp_path), "animal": "A1", "genotype": "wt", "sex": "f",
         "channel_0": "pan", "channel_1": "Shank2", "channel_2": "NR1", "channel_3": "Homer1", "channel_4": "mGluR5"},
    ])
    ds = read_manifest(p)
    assert ds[0].genotype == "KO" and ds[0].genotype_inferred
    assert ds[0].pan == 1 and ds[0].antibodies == {"GluA3": 0}
    assert ds[1].genotype == "WT" and ds[1].sex == "F"
    assert ds[1].antibodies == {"Shank2": 1, "NR1": 2, "Homer1": 3, "mGluR5": 4}
    problems = check_manifest(ds)
    assert any("inferred" in q for q in problems)


def test_infer_genotype_tokens():
    assert infer_genotype("260831_KO1b_Cx43_40x") == "KO"
    assert infer_genotype("260831_WT2_GluA1Rb") == "WT"
    assert infer_genotype("AC_Shank2_FusionStitcher") == "WT"
