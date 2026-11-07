"""Wait until a build of BUILD_CONFIG is Complete; fail when the latest one failed.

Argo CD runs this as a Sync hook between the BuildConfig and the StatefulSet, so the
pod moves to a new image tag only after that image exists.
"""
import json
import os
import ssl
import sys
import time
import urllib.request

SA = "/var/run/secrets/kubernetes.io/serviceaccount"
BC = os.environ["BUILD_CONFIG"]
NS = open(f"{SA}/namespace").read().strip()
URL = f"https://kubernetes.default.svc/apis/build.openshift.io/v1/namespaces/{NS}/builds"
CTX = ssl.create_default_context(cafile=f"{SA}/ca.crt")


def builds() -> list:
    token = open(f"{SA}/token").read().strip()
    req = urllib.request.Request(URL, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, context=CTX, timeout=30) as resp:
        items = json.load(resp)["items"]
    mine = [b for b in items if b.get("status", {}).get("config", {}).get("name") == BC]
    number = lambda b: int(b["metadata"].get("annotations", {}).get("openshift.io/build.number", 0))
    return sorted(mine, key=number)


last = None
while True:
    try:
        found = builds()
    except OSError as err:
        print(f"wait-build: {err}", flush=True)
        found = None
    if found is not None:
        phases = [b["status"].get("phase", "New") for b in found]
        if "Complete" in phases:
            print(f"wait-build: {BC}: Complete", flush=True)
            sys.exit(0)
        if phases and phases[-1] in ("Failed", "Error", "Cancelled"):
            sys.exit(f"wait-build: {found[-1]['metadata']['name']}: {phases[-1]}")
        state = phases[-1] if phases else "no build yet"
        if state != last:
            print(f"wait-build: {BC}: {state}", flush=True)
            last = state
    time.sleep(10)
