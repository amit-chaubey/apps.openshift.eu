import importlib.util
import pathlib
import unittest

SPEC = importlib.util.spec_from_file_location("sbom", pathlib.Path(__file__).parent / "sbom.py")
sbom = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sbom)

MANIFESTS = """
kind: Deployment
spec:
  template:
    spec:
      initContainers:
      - image: busybox
      containers:
      - image: docker.io/library/nextcloud:33
      - image: docker.io/library/nextcloud:33
---
kind: Cluster
spec:
  imageName: ghcr.io/cloudnative-pg/postgresql:17.6
---
kind: ConfigMap
data:
  image: ""
"""


class Images(unittest.TestCase):
    def test_every_image_once(self):
        self.assertEqual(sbom.images(MANIFESTS), [
            "busybox", "docker.io/library/nextcloud:33", "ghcr.io/cloudnative-pg/postgresql:17.6"])

    def test_cluster_registry_is_not_scanned(self):
        self.assertTrue(sbom.in_cluster("image-registry.openshift-image-registry.svc:5000/ns/app:1"))
        self.assertTrue(sbom.in_cluster("registry.ns.svc.cluster.local/app:1"))
        self.assertFalse(sbom.in_cluster("docker.io/library/nextcloud:33"))
        self.assertFalse(sbom.in_cluster("busybox"))


class Merge(unittest.TestCase):
    def test_components_deduplicated_and_images_listed(self):
        a = {"components": [{"bom-ref": "pkg:a", "name": "a"}, {"bom-ref": "pkg:b", "name": "b"}]}
        b = {"components": [{"bom-ref": "pkg:a", "name": "a"}]}
        doc = sbom.merge([a, b], "nextcloud", "0.6.0", ["x:1"], ["in.svc/y:1"])
        self.assertEqual([c["name"] for c in doc["components"]], ["a", "b"])
        self.assertEqual(doc["metadata"]["component"], {"type": "application", "name": "nextcloud", "version": "0.6.0"})
        self.assertEqual([p["name"] for p in doc["metadata"]["properties"]], ["image", "unscanned"])
        self.assertEqual(doc["bomFormat"], "CycloneDX")

    def test_file_entries_dropped(self):
        a = {"components": [{"bom-ref": "f1", "type": "file", "name": "/etc/passwd"},
                            {"bom-ref": "pkg:c", "type": "library", "name": "c"}]}
        self.assertEqual([c["name"] for c in sbom.merge([a], "x", "1", [])["components"]], ["c"])


if __name__ == "__main__":
    unittest.main()
