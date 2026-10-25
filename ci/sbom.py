#!/usr/bin/env python3
"""SBOM of one chart release: every image the chart renders, scanned by syft and
merged into one CycloneDX document, as the GitLab merge request gate does.

usage: ci/sbom.py <chart dir> <output file>
       ci/sbom.py --images <chart dir>
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

# as Argo CD renders: without them bitnami subcharts render a fixed UID
API_VERSIONS = ("security.openshift.io/v1", "route.openshift.io/v1")
IMAGE_KEYS = ("image", "imageName")
SYFT_TIMEOUT = 600
# the only variables syft inherits
SYFT_PASS = ("PATH", "LANG", "HOME", "SSL_CERT_FILE", "SSL_CERT_DIR", "DOCKER_CONFIG")


def render(chart):
    # the namespace the gate and the test values use
    cmd = ["helm", "template", "sbom", str(chart), "--namespace", "test"]
    values = Path(chart) / "ci" / "test-values.yaml"
    if values.exists():
        cmd += ["-f", str(values)]
    for api in API_VERSIONS:
        cmd += ["--api-versions", api]
    return subprocess.run(cmd, capture_output=True, text=True, check=True).stdout


def images_in(node, found):
    if isinstance(node, dict):
        for key, value in node.items():
            if key in IMAGE_KEYS and isinstance(value, str) and value.strip():
                found.add(value.strip())
            else:
                images_in(value, found)
    elif isinstance(node, list):
        for item in node:
            images_in(item, found)
    return found


def images(manifests):
    found = set()
    for doc in yaml.safe_load_all(manifests):
        images_in(doc, found)
    return sorted(found)


def in_cluster(ref):
    """An image built in the cluster's own registry, which no outside scanner can pull."""
    host = ref.split("/", 1)[0] if "/" in ref else ""
    return host.split(":", 1)[0].endswith((".svc", ".cluster.local"))


def syft_env():
    env = {k: v for k, v in os.environ.items() if k in SYFT_PASS}
    env["SYFT_CHECK_FOR_APP_UPDATE"] = "false"
    return env


def scan(ref, out):
    cmd = ["syft", "scan", f"registry:{ref}", "-q", "-o", f"cyclonedx-json={out}"]
    result = subprocess.run(cmd, env=syft_env(), capture_output=True, text=True, timeout=SYFT_TIMEOUT)
    if result.returncode:
        raise SystemExit(f"syft failed on {ref}: {result.stderr.strip()[-300:]}")
    return json.loads(Path(out).read_text())


def merge(boms, name, version, refs, unscanned=()):
    components, seen = [], set()
    for document in boms:
        for component in document.get("components") or []:
            # one entry per file in the image: tens of megabytes, no package
            if component.get("type") == "file":
                continue
            key = component.get("bom-ref") or component.get("purl") or json.dumps(component, sort_keys=True)
            if key in seen:
                continue
            seen.add(key)
            components.append(component)
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "version": 1,
        "metadata": {
            "component": {"type": "application", "name": name, "version": version},
            "properties": [{"name": "image", "value": ref} for ref in refs]
            + [{"name": "unscanned", "value": f"{ref} (built in the cluster)"} for ref in unscanned],
        },
        "components": components,
    }


def chart_meta(chart):
    meta = yaml.safe_load((Path(chart) / "Chart.yaml").read_text())
    return meta["name"], str(meta["version"])


def main(argv):
    if len(argv) == 3 and argv[1] == "--images":
        print("\n".join(images(render(argv[2]))))
        return 0
    if len(argv) != 3:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    chart, out = argv[1], argv[2]
    name, version = chart_meta(chart)
    found = images(render(chart))
    refs = [ref for ref in found if not in_cluster(ref)]
    unscanned = [ref for ref in found if in_cluster(ref)]
    with tempfile.TemporaryDirectory() as tmp:
        boms = [scan(ref, Path(tmp) / f"{i}.json") for i, ref in enumerate(refs)]
    Path(out).write_text(json.dumps(merge(boms, name, version, refs, unscanned), indent=1) + "\n")
    print(f"{name} {version}: {len(refs)} images scanned, {len(unscanned)} built in the cluster")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
