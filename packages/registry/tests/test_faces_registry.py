"""Face models in the registry (07 §1.4, §8.1; FACE-GATE-005, FACE-PRV-004)."""

import pytest
import yaml
from opagent_registry.loader import BUILTIN_DIR, load_registry

FACE = ("face_detector", "face_embedder", "face_quality")


@pytest.mark.contract
@pytest.mark.tc("FACE-GATE-005")
def test_gate_005_bundled_face_weights_are_redistributable_and_local():
    reg = load_registry()
    faces = [m for m in reg.models.values() if m.spec.kind in FACE]
    assert {m.spec.id for m in faces} >= {"yunet-2023mar", "auraface-v1", "sface-2021dec", "insightface-buffalo-l"}
    for m in faces:
        assert all(v.runtime != "openai-compatible" for v in m.spec.variants), m.spec.id
        if m.spec.defaults.get("bundled"):
            assert m.spec.license.redistributable, m.spec.id
    ins = reg.models["insightface-buffalo-l"].spec
    assert not ins.license.redistributable and ins.license.requires_acceptance and not ins.defaults.get("bundled")
    assert {m.spec.id for m in faces if m.spec.defaults.get("bundled")} == {"yunet-2023mar", "auraface-v1"}


@pytest.mark.contract
@pytest.mark.tc("FACE-GATE-005")
def test_gate_005_remote_runtime_refused_for_face_models(tmp_path):
    spec = yaml.safe_load((BUILTIN_DIR / "models/auraface-v1.yaml").read_text())
    spec["id"] = "my-remote-face"
    spec["variants"][0]["runtime"] = "openai-compatible"
    for f in spec["variants"][0]["files"]:  # a well-formed user model, so the runtime rule is what refuses it
        f.update(size=1000, sha256="a" * 64)
    (tmp_path / "my-remote-face.yaml").write_text(yaml.safe_dump(spec))
    reg = load_registry(user_dirs=[tmp_path])
    m = reg.models["my-remote-face"]
    assert m.status != "valid" and "FACE_REMOTE_RUNTIME" in str(m.errors)


@pytest.mark.contract
@pytest.mark.tc("FACE-PRV-004")
def test_prv_004_no_attribute_models_anywhere():
    text = "\n".join(p.read_text() for p in (BUILTIN_DIR / "models").glob("*.yaml")).lower()
    for word in ("genderage", "gender", "emotion", "ethnicity", "age_estimation"):
        assert word not in text
    reg = load_registry()
    assert not [c for m in reg.models.values() for c in m.spec.capabilities if c in ("age", "gender", "emotion")]
